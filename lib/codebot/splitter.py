"""Model-driven task splitter (the SPLIT_CMD hook for sizing.auto_split). Prints {"subtasks":[...]} or nothing."""
import json, os, sys
from . import ollama, packer
from .config import Project

SCHEMA = {"type": "object", "properties": {"subtasks": {"type": "array", "minItems": 2, "maxItems": 4, "items": {
    "type": "object", "properties": {"title": {"type": "string"}, "check": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "check"], "additionalProperties": False}}}, "required": ["subtasks"], "additionalProperties": False}

def main():
    proj = Project(os.environ["CODEBOT_PROJECT"], os.environ.get("CODEBOT_HOME"))
    with open(os.environ["CODEBOT_TASK_FILE"]) as f: d = json.load(f)
    task, err = d["task"], d.get("error", "")
    rm, _ = packer.repo_map(proj.rundir, 2500)
    prompt = (f"A task keeps failing and must be split into 2-4 smaller steps done in order. Each step needs its own acceptance check: ONE shell "
              f"command run in the code directory that exits 0 only when that step is done and FAILS right now. Steps must be small (one file or one behaviour).\n\n"
              f"TASK: {task['title']}\nFINAL CHECK (kept as the last gate): {task.get('check')}\nLAST ERROR:\n{err[-800:]}\n\nREPO:\n{rm}\n\nReply with the JSON.")
    try:
        r = ollama.chat(proj, [{"role": "system", "content": "You decompose coding tasks. Reply with JSON only."}, {"role": "user", "content": prompt}],
                        schema=SCHEMA, temperature=0.2, num_predict=900, timeout=600, retries=2)
        subs = json.loads(r["content"])["subtasks"]
    except Exception: return
    subs = [s for s in subs if (s.get("check") or "").strip() and (s.get("title") or "").strip() and s["check"].strip() != (task.get("check") or "").strip()]
    if 2 <= len(subs) <= 4: print(json.dumps({"subtasks": [{"title": s["title"], "check": s["check"], "files": s.get("files") or [], "size": "S"} for s in subs]}))

if __name__ == "__main__": main()
