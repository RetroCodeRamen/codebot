"""Failure classification. Heuristic, evidence-carrying, and re-derivable: every verdict lists the facts it rests on, the raw records are saved with the
trial, and `codebot bench report --reclassify` recomputes everything with the current rules. A timeout is NEVER explained as a reasoning failure: it is
reported as TIMEOUT with the measurements (time used, thermal waiting, turns, attempts) so a reader can judge."""
import json, os, re, shutil, subprocess, sys, tempfile
from .. import checkspec as K
from . import reference as R

CATEGORIES = ("PLANNING_OMISSION", "PLANNING_UNSOUND", "IMPLEMENTATION_ERROR", "VERIFICATION_GAP", "RECOVERY_LOOP", "CONTEXT_FAILURE", "TIMEOUT", "BLOCKED_AMBIGUITY", "INFRASTRUCTURE_ERROR")
EXTRA = ("SKIPPED_DEPENDENCY",)          # not in the requested list: a task that never ran because a prerequisite did not finish (the cause is its prerequisite)
RULES_VERSION = 2                 # 2 (2026-10-19): a VERIFICATION_GAP verdict for drafts mode (every spec example passed, the reference tests did not)

def plan_audit(proj, seed_tree, solution_tree):
    """Run every frozen assertion spec against the trusted solution (a sound spec HOLDS: rc 0) and against the starting workspace (a real test FAILS: rc 1).
    Returns {"unsound": [ids], "vacuous": [ids], "tasks": {id: {"seed": rc, "solution": rc, "why": [...]}}}."""
    cd = proj.p("checks"); out = {"unsound": [], "vacuous": [], "tasks": {}}
    if not os.path.isdir(cd): return out
    runner = os.path.join(cd, K.RUNNER)
    if not os.path.isfile(runner): return out
    for fn in sorted(os.listdir(cd)):
        if not fn.endswith(".spec.json"): continue
        tid = fn[:-len(".spec.json")]; row = {}
        for label, tree in (("seed", seed_tree), ("solution", solution_tree)):
            try: r = subprocess.run([sys.executable, runner, os.path.join(cd, fn)], cwd=tree, capture_output=True, text=True, timeout=300); row[label] = r.returncode
            except subprocess.TimeoutExpired: row[label] = 124; continue
            if label == "solution" and r.returncode: row["why"] = [l[6:160] for l in r.stdout.splitlines() if l.startswith("FAIL: ")][:3]
        out["tasks"][tid] = row
        if row.get("solution") not in (0, None): out["unsound"].append(tid)
        if row.get("seed") == 0: out["vacuous"].append(tid)
    return out

def _v(category, confidence, **evidence): return {"category": category, "confidence": confidence, "evidence": evidence}

def classify_task(t, audit=None, infra_task_ids=()):
    """Verdicts for one root task that did not end VERIFIED (a list: usually one)."""
    st = t["status"]
    if st == "VERIFIED": return []
    fam = set(t["family"])
    if st == "TIMEOUT":
        tm = t["timing"]
        return [_v("TIMEOUT", "certain", used_s=tm["used_s"], limit_s=tm["limit_s"], thermal_wait_s=tm["thermal_s"], attempts=t["attempts"], model_calls=t["model"]["calls"],
                   max_rung=t["max_rung"], inference_s=t["model"]["inference_s"], note="wall-clock budget exhausted; no claim is made about why (slow model, thermal waiting, a hard task, or a loop)")]
    if st == "SKIPPED": return [_v("SKIPPED_DEPENDENCY", "certain", reason=t.get("skip_reason"))]
    if st == "INCOMPLETE": return [_v("INFRASTRUCTURE_ERROR", "low", note="the run ended (cancelled or crashed) before this task reached a final state")]
    out = []
    unsound = [i for i in (audit or {}).get("unsound", []) if i.split(".")[0] in {x.split(".")[0] for x in fam}]
    if unsound:
        out.append(_v("PLANNING_UNSOUND", "high", specs=unsound, why=[(audit["tasks"].get(i) or {}).get("why") for i in unsound][:2], note="the reference solution fails the task's own acceptance assertions, so it could not be satisfied by a correct implementation"))
    if any(i in infra_task_ids for i in fam) or any(r.startswith("model error") for r in t["ended_reasons"]):
        out.append(_v("INFRASTRUCTURE_ERROR", "medium", ended=t["ended_reasons"]))
    if t["truncated_contexts"] or any("cut-off" in r or "context" in r for r in t["ended_reasons"]) or t["model"]["truncated_replies"] >= 3:
        out.append(_v("CONTEXT_FAILURE", "medium", truncated_contexts=t["truncated_contexts"], truncated_replies=t["model"]["truncated_replies"], ended=t["ended_reasons"]))
    sigs = [a["sig"] for a in t["attempt_rows"] if a.get("sig") and not a.get("passed")]
    if t["max_rung"] >= 3 and len(sigs) >= 4 and len(set(sigs)) <= 2:
        out.append(_v("RECOVERY_LOOP", "medium", max_rung=t["max_rung"], attempts=t["attempts"], distinct_failures=len(set(sigs)), ladder=[f"{e['from_name']}->{e['to_name']}" for e in t["ladder"]]))
    if st == "BLOCKED" and (t.get("blocked_question") or "").lower().find("ambigu") >= 0:
        out.append(_v("BLOCKED_AMBIGUITY", "low", question=t["blocked_question"][:200]))
    if not out: out.append(_v("IMPLEMENTATION_ERROR", "medium", attempts=t["attempts"], max_rung=t["max_rung"], last_failure=t["internal"]["last_failure_tail"][-200:]))
    return out

def classify_project(trial, requirements):
    """Project-level verdicts that need the plan, the audit and the reference results together."""
    out = []; plan = trial.get("plan") or {}; ref = trial.get("reference") or {}; tasks = trial.get("tasks") or []
    if plan and not plan.get("ok"):
        out.append(_v("PLANNING_UNSOUND", "high", where="project", mode=plan.get("mode"), rounds=plan.get("rounds"), note="no plan the baseline guard accepted was produced"))
    elif plan.get("mode") == "full" and plan.get("tasks_planned") is not None and plan.get("spec_items") and plan["tasks_planned"] < plan["spec_items"]:
        out.append(_v("PLANNING_OMISSION", "high", where="project", tasks_planned=plan["tasks_planned"], spec_items=plan["spec_items"]))
    a = trial.get("plan_audit") or {}
    if a.get("vacuous"): out.append(_v("PLANNING_UNSOUND", "medium", where="project", vacuous_specs=a["vacuous"], note="assertions that already hold on the starting workspace test nothing"))
    by = ref.get("by_requirement") or {}
    failing = [r for r in requirements if by.get(r["id"], {}).get("total") and by[r["id"]]["passed"] < by[r["id"]]["total"]]
    texts = " ".join([t["title"] for t in tasks] + [json.dumps(trial.get("plan_specs") or "")]).lower()
    for r in failing:
        kw = [k.lower() for k in r.get("keywords", [])]
        if kw and not any(k in texts for k in kw) and plan.get("mode") == "full":
            out.append(_v("PLANNING_OMISSION", "medium", requirement=r["id"], title=r["title"], note="no planned task or assertion mentions this requirement's keywords", keywords=kw))
    d = trial.get("drafts") or {}
    if plan.get("mode") == "drafts" and d.get("complete") and ref.get("pass_rate") is not None and ref["pass_rate"] < 1.0:
        out.append(_v("VERIFICATION_GAP", "high", internal_rate=1.0, reference_rate=ref["pass_rate"], failing_requirements=[r["id"] for r in failing], failing_tests=(ref.get("failing") or [])[:10],
                      uncovered_spec_items=d.get("uncovered"), note="every spec example passed but the independent reference tests did not (requirements without examples are never checked)"))
    if tasks and all(t["status"] == "VERIFIED" for t in tasks) and ref.get("pass_rate") is not None and ref["pass_rate"] < 1.0:
        out.append(_v("VERIFICATION_GAP", "high", internal_rate=1.0, reference_rate=ref["pass_rate"], failing_requirements=[r["id"] for r in failing], failing_tests=(ref.get("failing") or [])[:10],
                      note="every internal acceptance check passed but the independent reference tests did not"))
    elif tasks:
        for t in tasks:
            if t["status"] == "VERIFIED":
                reqs = [r for r in failing if any(k.lower() in (t["title"] + " ").lower() for k in r.get("keywords", []))]
                if reqs: out.append(_v("VERIFICATION_GAP", "medium", task=t["id"], requirements=[r["id"] for r in reqs], note="task verified internally, yet the reference tests of the requirement it names fail"))
    return out

def summarize(trial, requirements):
    """Fill trial['classification'] = {tasks: {id: [...]}, project: [...], counts: {category: n}}; counts are per verdict."""
    audit = trial.get("plan_audit") or {}; infra = set(trial.get("infra_tasks") or [])
    per = {t["id"]: classify_task(t, audit, infra) for t in trial.get("tasks") or []}
    per = {k: v for k, v in per.items() if v}; proj = classify_project(trial, requirements); counts = {}
    for vs in list(per.values()) + [proj]:
        for v in vs: counts[v["category"]] = counts.get(v["category"], 0) + 1
    return {"version": RULES_VERSION, "tasks": per, "project": proj, "counts": counts}
