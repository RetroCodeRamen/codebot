"""Native worker (F7 + F8). One attempt at one task. The model emits ONE JSON action per turn, constrained by a JSON
schema; the harness validates every action before applying it and builds all context itself. The worker's own claim
of success is irrelevant: the engine runs the frozen check afterwards."""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile
from . import audit, memory, modes, ollama, packer, planops, sandbox, tokens
from .config import Project, now, append_jsonl

ACTIONS = ("read_file", "list_dir", "write_file", "edit_file", "run", "replan", "note_lesson", "finish")

def _obj(name, props, req):
    return {"type": "object", "properties": {"why": {"type": "string", "maxLength": 240}, "action": {"enum": [name]}, **props},
            "required": ["why", "action", *req], "additionalProperties": False}

def action_schema():
    s = {"type": "string"}
    return {"anyOf": [
        _obj("read_file", {"path": s, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
        _obj("list_dir", {"path": s}, []),
        _obj("write_file", {"path": s, "content": s}, ["path", "content"]),
        _obj("edit_file", {"path": s, "search": s, "replace": s}, ["path", "search", "replace"]),
        _obj("run", {"command": s}, ["command"]),
        _obj("replan", {"op": {"type": "object"}}, ["op"]),
        _obj("note_lesson", {"text": s}, ["text"]),
        _obj("finish", {"summary": s}, ["summary"])]}

def flat_schema():   # fallback if the server cannot compile anyOf
    s = {"type": "string"}
    return {"type": "object", "properties": {"why": s, "action": {"enum": list(ACTIONS)}, "path": s, "offset": {"type": "integer"},
            "limit": {"type": "integer"}, "content": s, "search": s, "replace": s, "command": s, "op": {"type": "object"},
            "text": s, "summary": s}, "required": ["why", "action"]}

SYSTEM = """You are codebot's worker. You complete ONE task in a code directory. Each turn reply with exactly ONE JSON action and nothing else.
Fields: why (one short sentence), action, and the action's arguments.
Actions: read_file{path,offset?,limit?} list_dir{path} write_file{path,content} edit_file{path,search,replace} run{command} replan{op} note_lesson{text} finish{summary}
Rules: the ACCEPTANCE CHECK in your context is frozen and decides success; run it yourself with `run` to see if you are done. Prefer edit_file for small changes (search text must match exactly once; copy it from read_file). Paths are relative to the code directory. No network. Never touch checks or git history. When your check passes, or you cannot make progress, call finish."""
SYSTEM_ALT = SYSTEM + "\nPrevious approaches failed. In `why`, name the single most likely cause of the failure, then take a DIFFERENT approach than before. Start by reading the failing code and running the check."

class ActionError(Exception):
    pass

# ---------- validation (F7) ----------
def safe_path(proj, p):
    if not isinstance(p, str) or not p.strip(): raise ActionError("path is required")
    root = os.path.realpath(proj.rundir)
    full = os.path.realpath(os.path.join(root, p))
    st = os.path.realpath(proj.state)
    if full == st or full.startswith(st + os.sep): raise ActionError("that is the harness state directory; it is not yours to read or change")
    if full != root and not full.startswith(root + os.sep): raise ActionError(f"path {p!r} is outside the code directory")
    rel = os.path.relpath(full, root)
    if rel == ".git" or rel.startswith(".git" + os.sep): raise ActionError("the .git directory is managed by the harness")
    return full, rel

def syntax_error(path, content):
    """None if fine (or unknown type), else a message. Checked BEFORE a write lands."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        try: compile(content, path, "exec")
        except SyntaxError as e: return f"Python syntax error line {e.lineno}: {e.msg}"
    elif ext == ".json":
        try: json.loads(content)
        except ValueError as e: return f"invalid JSON: {e}"
    elif ext in (".sh", ".bash", ".js", ".mjs", ".cjs"):
        tool = ["bash", "-n"] if ext in (".sh", ".bash") else (["node", "--check"] if shutil.which("node") else None)
        if tool:
            with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False) as f: f.write(content); tmp = f.name
            try:
                r = subprocess.run(tool + [tmp], capture_output=True, text=True, timeout=20)
                if r.returncode: return f"{tool[0]} syntax error: {(r.stderr or r.stdout).strip()[:300].replace(tmp, path)}"
            finally: os.unlink(tmp)
    return None

def _atomic_write(full, content):
    os.makedirs(os.path.dirname(full), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(full), prefix=".cb-")
    with os.fdopen(fd, "w") as f: f.write(content)
    try: os.chmod(tmp, os.stat(full).st_mode & 0o777)
    except OSError: pass
    os.replace(tmp, full)

def validate_shape(a):
    if not isinstance(a, dict): raise ActionError("reply must be a JSON object")
    name = a.get("action")
    if name not in ACTIONS: raise ActionError(f"unknown action {name!r}; use one of: {', '.join(ACTIONS)}")
    need = {"read_file": ["path"], "list_dir": [], "write_file": ["path", "content"], "edit_file": ["path", "search", "replace"],
            "run": ["command"], "replan": ["op"], "note_lesson": ["text"], "finish": ["summary"]}[name]
    missing = [k for k in need if k not in a]
    if missing: raise ActionError(f"action {name} is missing: {', '.join(missing)}")

def _sha_of(path):
    try:
        with open(path, "rb") as f: return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError: return None

def _write_audited(ctx, full, rel, content, how):
    before = _sha_of(full); _atomic_write(full, content)
    audit.log(ctx.proj, "file_write", path=rel, action=how, sha_before=before, sha_after=_sha_of(full), task=ctx.task["id"], attempt=ctx.attempt)

# ---------- execution ----------
class Ctx:
    def __init__(self, proj, task, attempt): self.proj, self.task, self.attempt, self.finished, self.summary = proj, task, attempt, False, ""

def do_action(ctx, a):
    proj, name = ctx.proj, a["action"]
    if name == "read_file":
        full, rel = safe_path(proj, a["path"])
        if not os.path.isfile(full): raise ActionError(f"{rel}: not a file")
        with open(full, errors="replace") as f: lines = f.read().splitlines()
        audit.log(proj, "file_read", path=rel, task=ctx.task["id"])
        off, lim = max(1, int(a.get("offset") or 1)), min(400, max(1, int(a.get("limit") or 200)))
        chunk = lines[off - 1: off - 1 + lim]
        text = "\n".join(f"{i}: {l}" for i, l in enumerate(chunk, off))[:9000]
        return f"{rel} ({len(lines)} lines)\n{text}" + (f"\n... more lines: use offset={off + lim}" if off - 1 + lim < len(lines) else "")
    if name == "list_dir":
        full, rel = safe_path(proj, a.get("path") or ".")
        if not os.path.isdir(full): raise ActionError(f"{rel}: not a directory")
        ents = sorted(e for e in os.listdir(full) if e != ".git")[:100]
        return "\n".join(f"{e}/" if os.path.isdir(os.path.join(full, e)) else f"{e} ({os.path.getsize(os.path.join(full, e))} B)" for e in ents) or "(empty)"
    if name == "write_file":
        full, rel = safe_path(proj, a["path"]); c = a["content"]
        if not isinstance(c, str) or len(c) > 300_000: raise ActionError("content must be a string under 300 KB")
        err = syntax_error(rel, c)
        if err: raise ActionError(f"NOT written: {err}. Fix it and send the whole file again.")
        _write_audited(ctx, full, rel, c, "write"); return f"wrote {rel} ({len(c)} chars)"
    if name == "edit_file":
        full, rel = safe_path(proj, a["path"])
        if not os.path.isfile(full): raise ActionError(f"{rel}: not a file (use write_file to create it)")
        with open(full, errors="replace") as f: cur = f.read()
        s, r = a["search"], a["replace"]
        if not s: raise ActionError("search must not be empty")
        n = cur.count(s)
        if n == 0:
            hint = " The text exists with different surrounding whitespace; copy it exactly from read_file." if s.strip() and s.strip() in cur else " Copy the text exactly from read_file."
            raise ActionError(f"NOT edited: search text not found in {rel}.{hint}")
        if n > 1: raise ActionError(f"NOT edited: search text appears {n} times in {rel}; include more surrounding lines so it matches once.")
        new = cur.replace(s, r, 1); err = syntax_error(rel, new)
        if err: raise ActionError(f"NOT edited: the result would be broken: {err}.")
        _write_audited(ctx, full, rel, new, "edit"); return f"edited {rel}"
    if name == "run":
        try: rc, out = sandbox.run(proj, a["command"], proj.get("RUN_TIMEOUT", 120, int), task=ctx.task["id"])
        except sandbox.Denied as e: raise ActionError(f"command refused: {e}")
        return f"exit {rc}\n{out}"
    if name == "replan":
        op = a["op"]
        if not isinstance(op, dict): raise ActionError("op must be an object like {\"op\":\"add_task\",...}")
        r = planops.apply(proj, op, "model")
        return json.dumps(r)[:600]
    if name == "note_lesson":
        lid = memory.add(proj.home, str(a["text"]), proj.name, ctx.task["id"], ctx.attempt)
        if lid is None: raise ActionError("lesson too short or already known")
        return f"lesson {lid} saved"
    if name == "finish":
        ctx.finished = True; ctx.summary = str(a.get("summary", ""))[:300]; return "finished"
    raise ActionError(f"unhandled action {name}")

# ---------- conversation ----------
SUMMARY_MARK = "## EARLIER STEPS (compacted)"

def est_tokens(proj, messages): return tokens.est_messages(proj, messages)

def compact(messages, keep=6):
    """Fold old steps into one-line notes in ONE extra user message placed AFTER the untouched [system, context] prefix. The prefix
    stays byte-identical for the whole attempt, so the model server's KV cache keeps being reused for it."""
    prefix, rest, old = messages[:2], messages[2:], []
    if rest and rest[0]["role"] == "user" and rest[0]["content"].startswith(SUMMARY_MARK):
        old = rest[0]["content"].splitlines()[1:]; rest = rest[1:]
    if len(rest) <= keep and not (keep == 0 and rest): return messages
    mid, tail = (rest[:-keep], rest[-keep:]) if keep else (rest, [])
    mid, tail = list(mid), list(tail)
    while tail and tail[0]["role"] != "assistant": mid.append(tail.pop(0))
    notes = []
    for i in range(0, len(mid) - 1, 2):
        try: a = json.loads(mid[i]["content"]); act = f"{a.get('action')} {a.get('path') or a.get('command') or ''}".strip()
        except ValueError: act = "(malformed reply)"
        notes.append(f"- {act[:80]} -> {mid[i + 1]['content'].splitlines()[0][:100] if mid[i + 1]['content'] else ''}")
    return [*prefix, {"role": "user", "content": SUMMARY_MARK + "\n" + "\n".join((old + notes)[-30:])}, *tail]

def fit(proj, messages, reserve):
    """Guarantee request + generation reserve fit in the model's window (NUM_CTX), using the calibrated token estimate."""
    limit = int(tokens.num_ctx(proj) * 0.94) - reserve
    truncated = False
    for keep in (6, 4, 2, 0):
        if est_tokens(proj, messages) <= limit: return messages, False
        messages = compact(messages, keep)
    over = est_tokens(proj, messages) - limit
    if over > 0:        # the packed context itself is too big: cut the end of it (the least stable part) as a last resort
        ctx = dict(messages[1]); cut = int(over * tokens.ratio(proj)) + 400
        ctx["content"] = ctx["content"][: max(500, len(ctx["content"]) - cut)] + "\n\n[context truncated to fit]\nReply with ONE JSON action."
        messages = [messages[0], ctx, *messages[2:]]; truncated = True
    return messages, truncated

def trace(proj, rec): append_jsonl(proj.p("trace.jsonl"), {"ts": now(), **rec})

def run_attempt(proj, task, attempt, last_error, variant="", model=None, temperature=None):
    max_turns = proj.get("WORKER_MAX_TURNS", 24, int); ctx_cap = proj.get("WORKER_CONTEXT_TOKENS", 22000, int)
    num_predict = proj.get("WORKER_MAX_TOKENS", 1800, int); n_ctx = tokens.num_ctx(proj)
    temp = temperature if temperature is not None else proj.get("WORKER_TEMPERATURE", 0.2, float)
    text, manifest = packer.pack(proj, task, last_error)
    with open(proj.p("packed.json"), "w") as f: json.dump(manifest, f)
    trace(proj, {"event": "attempt_start", "task": task["id"], "attempt": attempt, "variant": variant or "std", "model": model, "context": manifest, "num_ctx": n_ctx})
    rules = ""
    try: rules = modes.worker_rules(proj.home, proj.mode)
    except modes.ModeError: pass
    messages = [{"role": "system", "content": (SYSTEM_ALT if variant == "alt" else SYSTEM) + ("\n" + rules if rules else "")},
                {"role": "user", "content": text + "\n\nReply with ONE JSON action."}]
    ctx, schema, bad, recent, turns = Ctx(proj, task, attempt), action_schema(), 0, [], 0
    ended, stats = "turn limit", {"malformed": 0, "action_errors": 0, "max_est": 0, "truncated": 0}
    while turns < max_turns:
        turns += 1
        messages, cut = fit(proj, messages, num_predict); est = est_tokens(proj, messages)
        stats["max_est"] = max(stats["max_est"], est); stats["truncated"] += 1 if cut else 0
        try: resp = ollama.chat(proj, messages, schema=schema, model=model, temperature=temp, num_predict=num_predict)
        except ollama.ModelError as e:
            if "HTTP 400" in str(e) and schema is not flat_schema():       # server cannot compile anyOf: use the flat schema
                schema = flat_schema(); trace(proj, {"event": "schema_fallback", "task": task["id"], "attempt": attempt}); continue
            ended = f"model error: {e}"; break
        raw = resp["content"]; kind = "ok"; result = ""; a = None
        try:
            a = json.loads(raw); validate_shape(a)
        except (ValueError, ActionError) as e:
            if resp.get("done_reason") == "length":      # structurally valid so far, but it hit the token limit: a size problem, not a format problem
                bad += 1; kind = "truncated"; stats["truncated_replies"] = stats.get("truncated_replies", 0) + 1; a = None
                result = (f"Your reply was CUT OFF at the {num_predict}-token limit, so nothing was applied. Do less per step: write a file in smaller parts "
                          f"(a short write_file, then several edit_file calls) and keep every string short.")
            else:
                bad += 1; kind = "malformed"; stats["malformed"] += 1
                result = f"Your reply was rejected: {e}. Reply with exactly one JSON action object."
        else:
            key = json.dumps({k: v for k, v in a.items() if k != "why"}, sort_keys=True)
            recent = (recent + [key])[-5:]
            if recent.count(key) >= 5: ended = "stuck repeating the same action"
            else:
                try: result = do_action(ctx, a)
                except ActionError as e: kind = "action_error"; stats["action_errors"] += 1; result = f"ERROR: {e}"
                if recent.count(key) >= 3: result += "\nWARNING: you repeated this exact action 3 times. Do something different or call finish."
        trace(proj, {"event": "turn", "task": task["id"], "attempt": attempt, "n": turns, "kind": kind, "ok": kind == "ok",
                     "action": (a.get("action") if isinstance(a, dict) else None), "arg": (str(a.get("path") or a.get("command") or "")[:120] if isinstance(a, dict) else ""),
                     "result": result[:160], "ptok": resp["prompt_tokens"], "gtok": resp["gen_tokens"], "prompt_ms": resp["prompt_ms"], "gen_ms": resp["gen_ms"],
                     "load_ms": resp["load_ms"], "secs": resp["secs"], "est_prompt": est, "num_ctx": n_ctx})
        if ended == "stuck repeating the same action": break
        if kind in ("malformed", "truncated") and bad >= 3: ended = "too many malformed replies" if kind == "malformed" else "too many cut-off replies"; break
        messages.append({"role": "assistant", "content": raw}); messages.append({"role": "user", "content": "RESULT:\n" + result[:3000]})
        if ctx.finished: ended = "finished"; break
        if est_tokens(proj, messages) > ctx_cap: messages = compact(messages)
    trace(proj, {"event": "attempt_end", "task": task["id"], "attempt": attempt, "turns": turns, "ended": ended, "summary": ctx.summary, **stats})
    return {"turns": turns, "ended": ended, "summary": ctx.summary}

def main():
    proj = Project(os.environ["CODEBOT_PROJECT"], os.environ.get("CODEBOT_HOME"))
    with open(os.environ["CODEBOT_TASK_FILE"]) as f: task = json.load(f)
    try:
        with open(os.environ.get("CODEBOT_LAST_ERROR_FILE", "")) as f: err = f.read()
    except OSError: err = ""
    t = os.environ.get("CODEBOT_TEMPERATURE")
    out = run_attempt(proj, task, int(os.environ.get("CODEBOT_ATTEMPT", "1")), err, os.environ.get("CODEBOT_VARIANT", ""),
                      os.environ.get("CODEBOT_MODEL") or None, float(t) if t else None)
    print(json.dumps(out))

if __name__ == "__main__": main()
