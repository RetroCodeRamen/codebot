"""Invocation journal: what each model turn decided and how far the harness got in carrying it out.

pipeline/invocations.jsonl is append-only, one event per line:
  prepared   a member is about to be asked (no decision yet)
  decided    the action the reply (or the harness, for a forced end of a step) settled on; the whole action is recorded
  applied    the action's effects are done and its turn is recorded
  committed  run.json holds the state that follows from it
  abandoned  a prepared invocation whose reply never arrived (the process died during the model call)

run.json is the authority on what is committed: a member's `done_inv` is the last invocation whose effects its saved state includes. After a
crash, schedule.step looks for the member's last invocation: `prepared` only means the inference was lost and is asked again under a new id;
`decided` or `applied` without being that member's `done_inv` means the action is known and is carried out again WITHOUT a model call. That is
safe because every effect is keyed by the invocation id: a message, an artifact or a turn that already carries the id is not made twice, a file
write is the same bytes, and a shell command that may have run is never run again (its turn says so).
"""
import json, os, time, uuid

def _path(rd): return os.path.join(rd, "invocations.jsonl")

def new_id(): return "inv-" + uuid.uuid4().hex[:12]

def append_line(path, text):
    """Append one line durably. If a crash left the file ending in half a line, that fragment is closed off first, so it spoils only itself."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab+") as f:
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n": f.write(b"\n")
        f.write(text.encode("utf-8") + b"\n"); f.flush(); os.fsync(f.fileno())

def event(rd, inv, kind, **kw):
    row = {"inv": inv, "event": kind, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}
    append_line(_path(rd), json.dumps(row, ensure_ascii=False))
    return row

def events(rd):
    try:
        with open(_path(rd), encoding="utf-8") as f:
            for ln in f:
                try: yield json.loads(ln)
                except ValueError: continue            # a line cut off by a crash
    except OSError: return

def last(rd, member):
    """The member's most recent invocation: {inv, state, action, node, visit} or None."""
    cur = None
    for e in events(rd):
        if e["event"] == "prepared" and e.get("member") == member: cur = {"inv": e["inv"], "state": "prepared", "action": None, "node": e.get("node"), "visit": e.get("visit")}
        elif cur and e["inv"] == cur["inv"]:
            cur["state"] = e["event"]
            if e["event"] == "decided": cur["action"] = e.get("action"); cur["forced"] = e.get("forced")
    return cur

def unfinished(rd, member, done_inv):
    """The invocation to carry out again without the model, or None. Marks a lost inference abandoned."""
    cur = last(rd, member)
    if not cur or cur["inv"] == done_inv or cur["state"] in ("committed", "abandoned"): return None
    if cur["state"] == "prepared": event(rd, cur["inv"], "abandoned", why="the process stopped during the model call"); return None
    return cur
