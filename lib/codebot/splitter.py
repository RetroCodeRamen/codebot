"""Model-driven task splitter (the SPLIT_CMD hook for sizing.auto_split). Prints {"subtasks":[...], "fixtures":{...}} or nothing.
Like the planner, the model emits declarative ASSERTIONS (assertions.py), never a shell command: the harness writes each child's spec file (a locked
fixture) and builds its check command, so a split child can never get a check that cannot fail, cannot pass, or points at a path that does not exist."""
import json, os, sys
from . import assertions as A, checkspec as K, checks as C, ollama, packer, roles
from .config import Project, now, append_jsonl

SCHEMA = {"type": "object", "properties": {"subtasks": {"type": "array", "minItems": 2, "maxItems": 4, "items": {     # asked for 2-4; a split survives with 1 usable child
    "type": "object", "properties": {"title": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "asserts": K.assert_schema()},
    "required": ["title", "asserts"], "additionalProperties": False}}}, "required": ["subtasks"], "additionalProperties": False}

def build(proj, tid, subs, parent_files=None, parent_asserts=None, notes=None):
    """Model output -> (subtasks for planops.split_task, fixtures). Assertions are repaired with the parent's as context (the splitter froze 12 of 21
    impossible assertions on level 1, mostly the expected value copied into args); unusable assertions are dropped one by one, and a child with
    none left is dropped. notes (a list) collects what was repaired or dropped."""
    from .planner import salvage
    out, fx = [], {}; notes = notes if notes is not None else []
    groups, rep = K.repair_plan([K.prepare(s.get("asserts")) for s in subs], [parent_asserts or []]); notes += rep
    for s, asserts in zip(subs, groups):
        asserts, dropped = salvage(asserts); notes += [f"dropped an assertion: {e}" for e in dropped]
        if not (s.get("title") or "").strip() or A.validate(asserts): notes.append(f"dropped child {s.get('title', '')[:60]!r}: no usable assertion"); continue
        cid = f"{tid}.{len(out) + 1}"
        fx[K.spec_name(cid)] = json.dumps({"asserts": asserts}, indent=1)
        out.append({"title": s["title"].strip(), "check": K.command_for(cid), "files": s.get("files") or list(parent_files or []), "size": "S"})
    if out and not os.path.isfile(os.path.join(C.checks_dir(proj), K.RUNNER)):          # a project frozen before declarative checks existed
        with open(A.__file__) as f: fx[K.RUNNER] = f.read()
    return out, fx

def main():
    proj = Project(os.environ["CODEBOT_PROJECT"], os.environ.get("CODEBOT_HOME"))
    with open(os.environ["CODEBOT_TASK_FILE"]) as f: d = json.load(f)
    task, err = d["task"], d.get("error", "")
    rm, _ = packer.repo_map(proj.rundir, 2500)
    spec = K.read_spec(proj, task["id"]) if K.is_runner_check(task.get("check")) else None
    final = f"ASSERTIONS OF THE FINAL CHECK (kept as the last gate): {json.dumps(spec)[:1500]}" if spec else f"FINAL CHECK (kept as the last gate): {task.get('check')}"
    prompt = (f"A task keeps failing and must be split into 2-4 smaller steps done in order. Each step needs its own ASSERTIONS (data, evaluated by a trusted runner in the code directory) "
              f"that FAIL right now and hold once that step is done. Steps must be small (one function, one file or one behaviour); together they must lead to the final check passing. "
              f"Assertion types: call {{\"type\":\"call\",\"module\":\"m\",\"func\":\"f\",\"args\":[..],\"expect\":value or \"raises\":\"ValueError\"}}; "
              f"run {{\"type\":\"run\",\"argv\":[\"python3\",\"tool.py\",..],\"stdout\":\"..\"}} (also exit, stdout_contains, stdout_json); file_exists/file_contains/file_not_contains with path (+ text). "
              f"Use plain relative file names, never absolute paths.\n\nTASK: {task['title']}\n{final}\nLAST ERROR:\n{err[-800:]}\n\nREPO:\n{rm}\n\nReply with the JSON.")
    raw, why, notes = None, "", []
    try:
        r = ollama.chat(proj, [{"role": "system", "content": "You decompose coding tasks. Reply with JSON only."}, {"role": "user", "content": prompt}],
                        schema=SCHEMA, temperature=0.2, num_predict=1500, timeout=600, retries=2,
                        model=roles.model(proj, "splitter"), think=roles.think(proj, "splitter"), role="splitter")
        raw = r.get("content") or ""; subs = json.loads(raw)["subtasks"]
    except Exception as e: subs = None; why = f"no usable reply: {type(e).__name__}: {str(e)[:200]}"
    if raw is not None:                                    # kept for the replay corpus (tests/corpus/build_corpus.py)
        with open(proj.p(f"split.reply.{task['id']}.json"), "w") as f: f.write(raw)
    kids, fx = build(proj, task["id"], subs, task.get("files"), spec, notes) if subs else ([], {})
    if subs and not kids: why = "every proposed child was unusable"
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "splitter", "op": "split_proposal", "ok": bool(kids), "task": task["id"], "reason": why or f"{len(kids)} usable child(ren)", "notes": notes[:20]})
    if 1 <= len(kids) <= 4: print(json.dumps({"subtasks": kids, "fixtures": fx}))

if __name__ == "__main__": main()
