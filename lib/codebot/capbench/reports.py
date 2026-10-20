"""Reports from the run store: report.json (everything), report.md (human summary), trials.csv / tasks.csv / calls.csv (analysis across trials).
A run that is still going is labelled as such; a partially completed project is reported as partial (with its numbers), never as failed."""
import csv, io, json, os, statistics
from . import results as RS

CEILING_THRESHOLD = 0.80          # a level "holds" when the median independent (reference) pass rate over its finished trials is at least this

def _mean(xs): xs = [x for x in xs if x is not None]; return round(statistics.mean(xs), 4) if xs else None
def _med(xs): xs = [x for x in xs if x is not None]; return round(statistics.median(xs), 4) if xs else None
def _pct(x): return "n/a" if x is None else f"{round(100 * x)}%"
def _min(s): return "n/a" if s is None else f"{s / 60:.1f}"

def trial_row(t):
    """One flat record per trial (also the CSV row)."""
    tt = t.get("totals") or {}; ref = t.get("reference") or {}; tim = t.get("timing") or {}; plan = t.get("plan") or {}; cl = (t.get("classification") or {}).get("counts") or {}
    tasks = t.get("tasks") or []; used = [x["timing"]["used_s"] for x in tasks]; run_s = tim.get("run_s")
    gap = None
    if tt.get("tasks_total") and ref.get("pass_rate") is not None: gap = round(tt["internal_success_rate"] - ref["pass_rate"], 4)
    return {"trial_id": t["trial_id"], "benchmark": t.get("benchmark"), "level": t.get("level"), "stage": t.get("stage"), "repeat": t.get("repeat"), "mode": t.get("mode"), "phase": t.get("phase"), "outcome": t.get("outcome"),
            "plan_ok": plan.get("ok"), "plan_s": plan.get("secs"), "spec_items": plan.get("spec_items"), "tasks_planned": plan.get("tasks_planned"),
            "tasks_total": tt.get("tasks_total"), "verified": tt.get("verified"), "timeouts": tt.get("timeouts"), "blocked": tt.get("blocked"), "skipped": tt.get("skipped"), "incomplete": tt.get("incomplete"),
            "internal_rate": tt.get("internal_success_rate"), "reference_passed": ref.get("passed"), "reference_total": ref.get("total"), "reference_rate": ref.get("pass_rate"),
            "requirements_met": ref.get("requirements_met"), "requirements_total": ref.get("requirements_total"), "internal_minus_reference": gap,
            "run_s": run_s, "task_time_s": tt.get("task_time_s"), "task_mean_s": round(statistics.mean(used), 1) if used else None, "task_max_s": round(max(used), 1) if used else None,
            "inference_s": tt.get("inference_s"), "model_calls": tt.get("model_calls"), "prompt_tokens": tt.get("prompt_tokens"), "gen_tokens": tt.get("gen_tokens"),
            "thermal_wait_s": tt.get("thermal_wait_s"), "retries": tt.get("retries"), "splits": tt.get("splits"), "ladder_steps": tt.get("ladder_steps"), "manual_interventions": tt.get("manual_interventions"),
            "verified_per_hour": round(tt["verified"] / (run_s / 3600), 2) if run_s and tt.get("verified") is not None else None,
            "failure_categories": ";".join(f"{k}={v}" for k, v in sorted(cl.items())), "error": t.get("error"),
            "examples": f"{(t.get('drafts') or {}).get('delivered_examples')}/{(t.get('drafts') or {}).get('examples_total')}" if t.get("drafts") else None,
            "drafts_rounds": (t.get("drafts") or {}).get("rounds"), "drafts": (t.get("drafts") or {}).get("drafts")}

def task_rows(t):
    out = []
    for x in t.get("tasks") or []:
        cl = ((t.get("classification") or {}).get("tasks") or {}).get(x["id"], [])
        out.append({"trial_id": t["trial_id"], "task": x["id"], "title": x["title"], "status": x["status"], "attempts": x["attempts"], "retries": x["retries"], "max_rung": x["max_rung"], "splits": len(x["split_into"]),
                    "ladder_steps": len(x["ladder"]), "used_s": x["timing"]["used_s"], "limit_s": x["timing"]["limit_s"], "remaining_s": x["timing"]["remaining_s"], "thermal_s": x["timing"]["thermal_s"],
                    "inference_s": x["model"]["inference_s"], "model_calls": x["model"]["calls"], "prompt_tokens": x["model"]["prompt_tokens"], "gen_tokens": x["model"]["gen_tokens"],
                    "internal_checks_passed": x["internal"]["checks_passed"], "internal_checks_failed": x["internal"]["checks_failed"], "category": ",".join(c["category"] for c in cl)})
    return out

def level_summary(rows):
    """Aggregate finished trials per benchmark (level or growing stage)."""
    groups = {}
    for r in rows: groups.setdefault(r["benchmark"] if r["benchmark"] != "growing" else f"growing:s{r['stage']}", []).append(r)
    out = []
    for k, rs in groups.items():
        fin = [r for r in rs if r["phase"] == "finished"]
        out.append({"benchmark": k, "level": rs[0]["level"], "stage": rs[0]["stage"], "trials": len(rs), "finished": len(fin), "complete": sum(1 for r in fin if r["outcome"] == "COMPLETE"),
                    "internal_rate_mean": _mean([r["internal_rate"] for r in fin]), "reference_rate_mean": _mean([r["reference_rate"] for r in fin]), "reference_rate_median": _med([r["reference_rate"] for r in fin]),
                    "reference_rate_min": min([r["reference_rate"] for r in fin if r["reference_rate"] is not None], default=None), "reference_rate_max": max([r["reference_rate"] for r in fin if r["reference_rate"] is not None], default=None),
                    "verified_tasks": sum(r["verified"] or 0 for r in fin), "tasks": sum(r["tasks_total"] or 0 for r in fin), "timeouts": sum(r["timeouts"] or 0 for r in fin),
                    "retries": sum(r["retries"] or 0 for r in fin), "splits": sum(r["splits"] or 0 for r in fin), "run_s_mean": _mean([r["run_s"] for r in fin]), "plan_s_mean": _mean([r["plan_s"] for r in fin]),
                    "verified_per_hour_mean": _mean([r["verified_per_hour"] for r in fin])})
    return sorted(out, key=lambda g: (g["level"] or 99, g["stage"] or 0))

def ceiling(groups, threshold=CEILING_THRESHOLD):
    """Observed capability ceiling: the highest level L such that EVERY level up to L finished at least one trial and held (median reference rate >= threshold).
    `first_below` is the first level that did not hold; `untested` lists levels with no finished trial (a ceiling is only a lower bound while they exist)."""
    lv = [g for g in groups if g["level"] and g["benchmark"] != "growing"]; held, first_below = 0, None
    for g in lv:
        if not g["finished"]: break
        if (g["reference_rate_median"] or 0) >= threshold: held = g["level"]
        else: first_below = g["level"]; break
    return {"threshold": threshold, "highest_level_held": held, "first_level_below": first_below, "untested": [g["level"] for g in lv if not g["finished"]],
            "definition": f"highest level L such that every level up to L has a finished trial and a median independent pass rate >= {int(threshold * 100)}%"}

def build(store):
    st = store.load(); trials = store.all_trials(); rows = [trial_row(t) for t in trials]; groups = level_summary(rows)
    fin = [t for t in trials if t.get("phase") == "finished"]; cats = {}
    for t in fin:
        for k, v in ((t.get("classification") or {}).get("counts") or {}).items(): cats[k] = cats.get(k, 0) + v
    cur = next((t for t in trials if t.get("phase") not in ("finished", "created")), None)
    return {"run_id": st["run_id"], "status": st["status"], "running": st["status"] in ("running", "created"), "created": st["created"], "updated": st["updated"], "suite": st["suite"], "mode": st["mode"], "config": st["config"], "environment": st["environment"],
            "progress": {"trials_total": len(trials), "trials_finished": len(fin), "current": ({"trial": cur["trial_id"], "phase": cur["phase"], "progress": cur.get("progress")} if cur and st["status"] == "running" else None)},
            "levels": groups, "ceiling": ceiling(groups), "failure_categories": cats, "trials": rows, "tasks": [r for t in trials for r in task_rows(t)]}

def _table(headers, rows):
    L = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows: L.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return L

def markdown(rep, store=None):
    st = rep["status"]; p = rep["progress"]
    banner = {"running": f"**STILL RUNNING** ({p['trials_finished']} of {p['trials_total']} trials finished; numbers below are partial)", "created": "**NOT STARTED**",
              "cancelled": f"**CANCELLED** (resumable: {p['trials_finished']} of {p['trials_total']} trials finished)", "finished": f"**FINISHED** ({p['trials_finished']} of {p['trials_total']} trials)"}.get(st, st)
    L = [f"# Capability ladder run `{rep['run_id']}`", "", banner, ""]
    c, e = rep["config"], rep["environment"]
    drafts = rep["mode"] == "drafts"                   # no tasks: each trial is one run of whole-program drafts judged by the spec's examples
    budget = f"time per run **{c['task_minutes']:g} min**" if drafts else f"per-task budget **{c['task_minutes']:g} min** (no project-wide limit)"
    L += [f"- mode `{rep['mode']}`, suite `{rep['suite']}`, {budget}, model `{c['model']}`, seed {c['seed']}, driver {c['driver']}",
          f"- codebot `{(e.get('codebot_commit') or '?')[:10]}`{' (uncommitted changes)' if e.get('codebot_dirty') else ''}, framework {e.get('benchmark_framework_version')}, {e.get('cpu') or '?'}, {e.get('gpu') or 'no GPU info'}"]
    if c.get("roles"): L += ["- role settings: " + ", ".join(f"`{k}={v}`" for k, v in sorted(c["roles"].items()))]
    L += [""]
    if p.get("current"): L += [f"- now: `{p['current']['trial']}` in phase `{p['current']['phase']}`", ""]
    lvl_rows = [[g["benchmark"], f"{g['finished']}/{g['trials']}", g["complete"], f"{g['verified_tasks']}/{g['tasks']}", _pct(g["internal_rate_mean"]),
                 f"{_pct(g['reference_rate_median'])} ({_pct(g['reference_rate_min'])}-{_pct(g['reference_rate_max'])})", g["timeouts"], g["retries"], g["splits"], _min(g["run_s_mean"]), g["verified_per_hour_mean"]] for g in rep["levels"]]
    if drafts:
        lvl_rows = [[g["benchmark"], f"{g['finished']}/{g['trials']}", g["complete"], f"{_pct(g['reference_rate_median'])} ({_pct(g['reference_rate_min'])}-{_pct(g['reference_rate_max'])})", _min(g["run_s_mean"])] for g in rep["levels"]]
        L += ["## Per level", ""] + _table(["level", "trials done", "every spec example passes", "independent (median / min-max)", "run min"], lvl_rows) + [""]
    else: L += ["## Per level", ""] + _table(["level", "trials done", "complete", "tasks verified", "internal", "independent (median / min-max)", "timeouts", "retries", "splits", "run min", "verified/h"], lvl_rows) + [""]
    ce = rep["ceiling"]
    L += ["## Observed capability ceiling", "", f"- {ce['definition']}",
          f"- highest level held: **{ce['highest_level_held'] or 'none'}**; first level below the threshold: {ce['first_level_below'] or 'none seen'}" + (f"; levels not yet finished: {ce['untested']}" if ce["untested"] else ""), ""]
    tr_rows = [[r["trial_id"], r["outcome"] or r["phase"], r["tasks_total"], r["verified"], r["timeouts"], _pct(r["internal_rate"]),
                f"{r['reference_passed']}/{r['reference_total']} ({_pct(r['reference_rate'])})" if r["reference_total"] else "n/a",
                _pct(r["internal_minus_reference"]) if r["internal_minus_reference"] is not None else "n/a", _min(r["plan_s"]), _min(r["run_s"]), r["retries"], r["splits"], r["failure_categories"]] for r in rep["trials"]]
    if drafts:
        tr_rows = [[r["trial_id"], r["outcome"] or r["phase"], r["examples"], r["drafts_rounds"], r["drafts"], f"{r['reference_passed']}/{r['reference_total']} ({_pct(r['reference_rate'])})" if r["reference_total"] else "n/a",
                    _min(r["run_s"]), r["failure_categories"]] for r in rep["trials"]]
        L += ["## Trials", ""] + _table(["trial", "outcome", "spec examples", "rounds", "drafts", "independent", "run min", "categories"], tr_rows) + [""]
    else: L += ["## Trials", ""] + _table(["trial", "outcome", "tasks", "verified", "timeouts", "internal", "independent", "gap", "plan min", "run min", "retries", "splits", "categories"], tr_rows) + [""]
    if rep["failure_categories"]: L += ["## Failure categories (all finished trials)", ""] + [f"- `{k}`: {v}" for k, v in sorted(rep["failure_categories"].items(), key=lambda kv: -kv[1])] + ["", "_Heuristic classifications with their evidence are in report.json / trial.json and can be recomputed (`codebot bench report --reclassify`). A TIMEOUT is never attributed to reasoning._", ""]
    tk_rows = [[r["trial_id"], f"{r['task']} {r['title'][:46]}", r["status"], r["attempts"], r["max_rung"], f"{r['used_s'] / 60:.1f} / {(r['limit_s'] or 0) / 60:.0f}", f"{r['thermal_s'] / 60:.1f}", r["model_calls"], r["category"]] for r in rep["tasks"]]
    if not drafts: L += ["## Tasks", ""] + _table(["trial", "task", "status", "attempts", "rung", "min used / limit", "thermal min", "calls", "category"], tk_rows)
    return "\n".join(L) + "\n"

def _csv(rows):
    if not rows: return ""
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in rows]; return buf.getvalue()

def calls_rows(store):
    out = []
    for t in store.all_trials():
        for c in t.get("calls_sample") or []: out.append({"trial_id": t["trial_id"], **c})
    return out

def one_line(store):
    rep = build(store); rows = [r for r in rep["trials"] if r["phase"] == "finished"]
    ver = sum(r["verified"] or 0 for r in rows); tot = sum(r["tasks_total"] or 0 for r in rows); to = sum(r["timeouts"] or 0 for r in rows)
    ref = _mean([r["reference_rate"] for r in rows])
    if rep["mode"] == "drafts":
        return f"{len(rows)} trials finished: spec examples {', '.join(str(r['examples']) for r in rows)}, independent pass rate {_pct(ref)}. Ceiling: level {rep['ceiling']['highest_level_held'] or 'none'}. Report: bench/results/ladder/{rep['run_id']}/report.md"
    return f"{len(rows)} trials finished: {ver}/{tot} tasks verified internally, independent pass rate {_pct(ref)}, {to} task timeout(s). Ceiling: level {rep['ceiling']['highest_level_held'] or 'none'}. Report: bench/results/ladder/{rep['run_id']}/report.md"

def write_all(store):
    """Refresh report.json / report.md / trials.csv / tasks.csv / calls.csv in the run directory (safe to call while the run is going)."""
    rep = build(store); d = store.dir
    for name, text in (("report.json", json.dumps(rep, indent=1, ensure_ascii=False)), ("report.md", markdown(rep, store)), ("trials.csv", _csv(rep["trials"])), ("tasks.csv", _csv(rep["tasks"])), ("calls.csv", _csv(calls_rows(store)))):
        tmp = os.path.join(d, f".{name}.tmp")
        with open(tmp, "w") as f: f.write(text)
        os.replace(tmp, os.path.join(d, name))
    return rep

def compare(home, run_a, run_b):
    """Per level: independent pass rate and verified tasks of two runs side by side (for evaluating a codebot change against an earlier run)."""
    ra, rb = build(RS.Store(home, run_a)), build(RS.Store(home, run_b)); ga = {g["benchmark"]: g for g in ra["levels"]}; gb = {g["benchmark"]: g for g in rb["levels"]}
    keys = sorted(set(ga) | set(gb), key=lambda k: ((ga.get(k) or gb.get(k))["level"] or 99, (ga.get(k) or gb.get(k))["stage"] or 0)); f = lambda g, k: _pct(g[k]) if g and g["finished"] else "n/a"
    rows = [[k, f(ga.get(k), "reference_rate_median"), f(gb.get(k), "reference_rate_median"), f"{ga[k]['verified_tasks']}/{ga[k]['tasks']}" if k in ga and ga[k]["finished"] else "n/a",
             f"{gb[k]['verified_tasks']}/{gb[k]['tasks']}" if k in gb and gb[k]["finished"] else "n/a", ga[k]["timeouts"] if k in ga else "n/a", gb[k]["timeouts"] if k in gb else "n/a"] for k in keys]
    head = [f"Compare `{run_a}` (A: commit {(ra['environment'].get('codebot_commit') or '?')[:8]}, {ra['mode']}, {ra['config']['model']}) with `{run_b}` (B: commit {(rb['environment'].get('codebot_commit') or '?')[:8]}, {rb['mode']}, {rb['config']['model']})", ""]
    return "\n".join(head + _table(["level", "independent A", "independent B", "verified A", "verified B", "timeouts A", "timeouts B"], rows) + ["", f"ceiling A: {ra['ceiling']['highest_level_held'] or 'none'}, ceiling B: {rb['ceiling']['highest_level_held'] or 'none'}"])

def history_table(home, level=None, limit=40):
    rows = [h for h in RS.history(home) if level is None or h.get("level") == level][-limit:]
    return "\n".join(_table(["finished", "run", "trial", "mode", "model", "commit", "outcome", "verified", "independent"], [[(h.get("finished") or "")[:16], h["run_id"][-14:], h["trial_id"], h["mode"], h.get("model"), (h.get("commit") or "?")[:8], h.get("outcome"),
        f"{h.get('verified')}/{h.get('tasks_total')}", _pct(h.get("reference_rate"))] for h in rows]))
