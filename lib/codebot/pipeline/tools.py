"""What a pipeline agent can do besides talk, and what it remembers of doing it.

Tools (an agent's "tools" list; the harness does each one, inside the agent's `sees`, and the result is the input of the agent's next turn):
  read   a workspace file it may read, or an artifact (`artifacts/<name>`)
  list   the workspace files it may read
  check  run the spec's worked examples against the workspace (gates.examples: the same judgement the examples gate makes)
  run    a shell command in the workspace, in the sandbox, under the worker's command policy (sandbox.run)
Writing is not a tool: `sees.write` decides it.

Turns: every action an agent takes and what came of it is appended to pipeline/turns.jsonl. An agent's context is rebuilt for each call, so
without this it would not know it had already read a file or written one.
"""
import hashlib, json, os
from codebot import drafts, sandbox
from . import artifacts, gates, scope
from .validate import ValidationError, AGENT_TOOLS as TOOLS

DEFAULT = ("read", "list", "check")          # run is given explicitly
SKIP = {".git", "__pycache__", ".gocache", "node_modules"}
READ_CHARS, LIST_MAX, RESULT_CHARS, TURNS_CHARS = 12000, 80, 6000, 14000
MAX_TURNS = 16                                # per visit of a workflow node (an agent's limits.max_turns overrides it)

class ToolError(Exception):
    """Something the agent asked for cannot be done; the text goes back to the agent as the turn's result."""

def allowed(agent):
    t = agent.get("tools")
    return [x for x in (DEFAULT if t is None else t) if x in TOOLS]

def actions(agent, has_question=False):
    """The actions this agent may take now, in the order the prompt lists them."""
    sees = agent.get("sees") or {}; tools = allowed(agent); out = []
    if sees.get("read"): out += [t for t in ("list", "read") if t in tools]
    if sees.get("write"): out.append("write")
    out += [t for t in ("check", "run") if t in tools]
    if agent.get("can_talk_to") or any((l or {}).get("kind") in ("agent", "both") for l in agent.get("links") or []): out += ["ask", "escalate"]
    if has_question: out.append("answer")
    return out + ["findings", "finish", "fail"]

GUIDE = {
    "list": '{"action": "list"}: the files you may read.',
    "read": '{"action": "read", "path": "FILE"}: the text of one file (or of an artifact, "artifacts/NAME").',
    "write": '{"action": "write", "path": "FILE", "content": "..."}: replace one file with this whole text. One file per turn.',
    "check": '{"action": "check"}: run the worked examples of SPEC.md against the files as they are now, and see which fail.',
    "run": '{"action": "run", "command": "..."}: run one shell command in the code directory (no network) and see its output.',
    "ask": '{"action": "ask", "to": "MEMBER", "text": "..."}: ask another member a question and wait for the answer.',
    "escalate": '{"action": "escalate", "text": "..."}: ask your manager for help when you are stuck.',
    "answer": '{"action": "answer", "question_id": "ID", "text": "..."}: answer a question from your inbox.',
    "findings": '{"action": "findings", "findings": [{"where": "...", "what": "...", "serious": "low|medium|high"}]}: report what you found; this ends your part.',
    "finish": '{"action": "finish", "reason": "..."}: your part is done. The harness checks the result itself; saying it works proves nothing.',
    "fail": '{"action": "fail", "reason": "..."}: the task cannot be done at all. This stops the whole run.',
}

def guide(acts):
    return "Your actions (reply with exactly one JSON object, one action per turn):\n" + "\n".join("- " + GUIDE[a] for a in acts if a in GUIDE)

# ---------- the tools ----------
def listing(ws, agent, limit=LIST_MAX):
    rows = []
    for root, dirs, files in os.walk(ws):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(root, f), ws).replace(os.sep, "/")
            if f.startswith(".") or f.endswith(".tmp"): continue
            try:
                if not scope.can_read(agent, rel): continue
            except ValidationError: continue
            rows.append((rel, os.path.getsize(os.path.join(root, f))))
    mine = lambda rel: _owns(agent, rel)
    out = [f"{rel} ({n} bytes{', yours to write' if mine(rel) else ''})" for rel, n in rows[:limit]]
    if len(rows) > limit: out.append(f"... and {len(rows) - limit} more")
    return "\n".join(out) or "(no files yet)"

def _owns(agent, rel):
    try: return scope.can_write(agent, rel)
    except ValidationError: return False

def read(ws, rd, agent, path):
    path = (path or "").strip()
    if not path: raise ToolError("read needs a path")
    if path.startswith("artifacts/"):
        name = path[len("artifacts/"):]
        if not scope.can_read(agent, path): raise ToolError(f"you may not read {path}")
        hits = [a for a in artifacts.list_artifacts(rd) if name in (a["id"], a["name"])]
        if not hits: raise ToolError(f"there is no artifact {name!r}")
        text = artifacts.read(rd, hits[-1])
    else:
        try: text = scope.read_text(ws, agent, path, limit=READ_CHARS + 1)
        except ValidationError as e: raise ToolError("; ".join(e.errors))
        except (OSError, UnicodeError): raise ToolError(f"there is no file {path!r} (use list to see the files)")
    if len(text) > READ_CHARS: text = text[:READ_CHARS] + f"\n... (cut after {READ_CHARS} characters)"
    return f"{path}:\n```\n{text}\n```" if text else f"{path} is empty"

def run(proj, ws, agent, member, command):
    command = (command or "").strip()
    if not command: raise ToolError("run needs a command")
    timeout = int((agent.get("limits") or {}).get("run_seconds") or proj.get("PIPELINE_RUN_TIMEOUT", 120, int))
    try: rc, out = sandbox.run(drafts._at(proj, ws), command, timeout=timeout, task=member)
    except sandbox.Denied as e: raise ToolError(f"that command is not allowed: {e}")
    return f"exit status {rc}\n{out.strip()}".strip()

def check(proj, ws, agent=None):
    """A worker that writes only some of the spec's files is told about the examples for those files (gates.examples only=...)."""
    only = None
    if agent is not None:
        names = drafts.spec_files(gates._spec(proj, ws)); own = [n for n in names if _owns(agent, n)]
        if own and len(own) < len(names): only = own
    when, detail = gates.examples(proj, ws, only=only)
    if when == "pass": return "The examples pass: " + detail + ". Nothing here is left to fix: finish."
    return "The examples do not pass yet: " + detail

# ---------- nothing has changed ----------
def _sha(text): return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]

def fingerprint(ws, agent, action):
    """What an action would look at or write, as a hash: the file's text for read, the new text for write, every readable file for list and check."""
    a = action.get("action")
    try:
        if a == "write": return _sha(action.get("content") or action.get("text") or "")
        if a == "read" and not (action.get("path") or "").startswith("artifacts/"):
            return _sha(scope.read_text(ws, agent, action.get("path") or "", limit=2_000_000))
        if a in ("list", "check"):
            h = hashlib.sha256()
            for root, dirs, files in os.walk(ws):
                dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
                for f in sorted(files):
                    if f.startswith(".") or f.endswith(".tmp"): continue
                    with open(os.path.join(root, f), "rb") as fh: h.update(os.path.join(root, f).encode() + b"\0" + fh.read() + b"\0")
            return h.hexdigest()[:16]
    except (ValidationError, OSError, UnicodeError): pass
    return None

def unchanged(rd, ws, agent, member, visit, action, sha):
    """Why this action would show the agent nothing new, or None. The 30B model's first real run (2026-11-11) wrote both files correctly on its
    first write, then spent 49 of 52 calls reading the same file and running the same passing check in turn until its turns ran out."""
    a = action.get("action"); prev = turns(rd, member, visit)
    if a == "write":
        try: now = scope.read_text(ws, agent, action.get("path") or "", limit=2_000_000)
        except (ValidationError, OSError, UnicodeError): return None
        return f"{action.get('path')} already has exactly this text, so there is nothing to write; if your part is done, finish" if _sha(now) == sha else None
    if sha is None or a not in ("read", "list", "check"): return None
    for n, r in reversed(list(enumerate(prev, 1))):
        if r.get("action") != a or r.get("sha") != sha or (r.get("result") or "").startswith("error:"): continue
        if a == "read" and r.get("path") != action.get("path"): continue
        if a == "read": return f"you read {action.get('path')} on turn {n} and it has not changed since: its text is above. Do something else, or finish"
        if a == "list": return f"no file has changed since you listed them on turn {n}. Do something else, or finish"
        return f"no file has changed since your check on turn {n}, so the result is the same: {(r.get('result') or '').splitlines()[0][:300]}"
    return None

def done_after_check(rd, member, visit):
    """True when the agent's check passed, it has written nothing since, and it then asked for something that was refused: its part is done and
    it is looking again. In the 2026-11-12 series every team run had agents stopped only after three such refusals (15 stops in six runs)."""
    passed = False; refused = False
    for r in turns(rd, member, visit):
        res = r.get("result") or ""
        if r.get("action") == "check" and res.startswith("The examples pass"): passed, refused = True, False
        elif r.get("action") == "write" and not res.startswith("error:"): passed = False
        elif res.startswith("error:"): refused = True
    return passed and refused

def refused_in_a_row(rd, member, visit, n=3):
    last = turns(rd, member, visit)[-n:]
    return len(last) == n and all((r.get("result") or "").startswith("error:") for r in last)

# ---------- turns ----------
def _path(rd): return os.path.join(rd, "turns.jsonl")

def summary(action):
    a = action.get("action")
    if a == "read": return f"read {action.get('path')}"
    if a == "write": return f"write {action.get('path') or action.get('artifact_name')} ({len(action.get('content') or action.get('text') or '')} characters)"
    if a == "run": return f"run `{(action.get('command') or '')[:200]}`"
    if a in ("ask", "escalate"): return f"{a} {action.get('to') or 'manager'}: {(action.get('text') or '')[:200]}"
    if a == "answer": return f"answer: {(action.get('text') or '')[:200]}"
    return a or "?"

def record(rd, member, visit, node, action, result, sha=None):
    os.makedirs(rd, exist_ok=True)
    row = {"member": member, "visit": visit, "node": node, "action": action.get("action"), "summary": summary(action), "path": action.get("path"), "sha": sha,
           "key": json.dumps({k: v for k, v in action.items() if k != "reason"}, sort_keys=True), "result": (result or "")[:RESULT_CHARS]}
    with open(_path(rd), "a", encoding="utf-8") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row

def turns(rd, member, visit=None):
    out = []
    try:
        with open(_path(rd), encoding="utf-8") as f:
            for ln in f:
                try: r = json.loads(ln)
                except ValueError: continue
                if r.get("member") == member and (visit is None or r.get("visit") == visit): out.append(r)
    except OSError: pass
    return out

def repeated(rd, member, visit, action):
    """True when this exact command was the agent's previous turn: its output is already in front of it."""
    prev = turns(rd, member, visit)
    key = json.dumps({k: v for k, v in action.items() if k != "reason"}, sort_keys=True)
    return bool(prev) and prev[-1].get("key") == key and action.get("action") == "run"

def history(rd, member, visit, memory="task"):
    """The agent's own turns as text for its next call: this visit's turns (the last three results whole, and the latest read of each file and the
    latest check, since asking for those again is refused while nothing has changed; older ones shortened); with project or persistent memory also
    one line for each turn of its earlier visits."""
    now = turns(rd, member, visit); parts = []
    if memory in ("project", "persistent"):
        old = [r for r in turns(rd, member) if r.get("visit") != visit]
        if old: parts.append("What you did earlier in this project:\n" + "\n".join(f"- {r['summary']}" for r in old[-20:]))
    if now:
        rows, budget, kept = [], TURNS_CHARS, set()
        for i, r in reversed(list(enumerate(now, 1))):
            res = r.get("result") or ""
            what = (r.get("action"), r.get("path")) if r.get("action") in ("read", "check", "list") and not res.startswith("error:") else None
            if len(now) - i >= 3 and what in kept | {None} and len(res) > 300: res = res[:300] + " ... (cut)"
            kept.add(what)
            if len(res) > budget: res = res[:max(budget, 0)] + " ... (cut)"
            budget -= len(res)
            rows.append(f"{i}. {r['summary']}" + (f"\n{res}" if res else ""))
        parts.append(f"Your turns on this task so far ({len(now)}, oldest first):\n" + "\n\n".join(reversed(rows)))
    return "\n\n".join(parts)
