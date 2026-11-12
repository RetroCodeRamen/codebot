"""The approved specification, out of the agents' reach.

A result is judged by the spec's worked examples (and by verify.sh for a tests gate). An agent that may write those files can make wrong code
pass by changing what it is judged by: a probe on 2026-11-11 wrote `add` to return 0, changed the spec's example to expect 0, and the examples
gate passed. So the spec is frozen the moment implementation or code-checking starts (the first run node whose action is not spec work, the
first examples or tests gate, the first `check`) or when a spec-examples gate passes: a copy goes to pipeline/spec/ in the state directory,
which no agent can reach, and from then on

  - the gates and `check` read that copy, never the workspace's;
  - a write to SPEC.md or verify.sh is refused;
  - the workspace's SPEC.md and verify.sh are put back whenever they differ (a `run` command can still touch them).

A verify.sh that did not exist when the spec was frozen is never run: a check written during the build is the builder's own.
"""
import hashlib, json, os, time

PROTECTED = ("SPEC.md", "verify.sh")

def _dir(proj): return proj.p("pipeline", "spec")

def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f: return f.read()
    except OSError: return None

def info(proj):
    try:
        with open(os.path.join(_dir(proj), "frozen.json"), encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError): return None

def draft(proj, ws):
    """The spec as it is being written: the workspace's, else the project's own copy."""
    return _read(os.path.join(ws, "SPEC.md")) or _read(proj.p("SPEC.md")) or ""

def text(proj, ws):
    """The spec a result is judged by: the frozen copy once there is one."""
    cur = info(proj)
    return (_read(os.path.join(_dir(proj), cur["file"])) or "") if cur else draft(proj, ws)

def freeze(proj, ws):
    """Freeze the spec as it is now (once; later calls return the first freeze). None when there is no spec to freeze."""
    cur = info(proj)
    if cur: return cur
    spec = draft(proj, ws)
    if not spec.strip(): return None
    d = _dir(proj); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "v1.md"), "w", encoding="utf-8") as f: f.write(spec)
    verify = _read(os.path.join(ws, "verify.sh"))
    if verify is not None:
        with open(os.path.join(d, "verify.sh"), "w", encoding="utf-8") as f: f.write(verify)
    cur = {"version": 1, "file": "v1.md", "sha256": hashlib.sha256(spec.encode()).hexdigest(), "verify": verify is not None,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    tmp = os.path.join(d, "frozen.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f: json.dump(cur, f, indent=1)
    os.replace(tmp, os.path.join(d, "frozen.json"))
    restore(proj, ws)                      # the workspace gets SPEC.md even when the spec came from the project's own copy
    return cur

def verify_script(proj):
    """The frozen verify.sh's text, or None."""
    cur = info(proj)
    return _read(os.path.join(_dir(proj), "verify.sh")) if cur and cur.get("verify") else None

def protected(proj, rel):
    return info(proj) is not None and os.path.normpath((rel or "").replace("\\", "/")) in PROTECTED

def restore(proj, ws):
    """Put the frozen files back where the workspace's differ. Returns the names that had been changed."""
    cur = info(proj); changed = []
    if not cur or not os.path.isdir(ws): return changed
    for name, want in (("SPEC.md", _read(os.path.join(_dir(proj), cur["file"]))), ("verify.sh", verify_script(proj))):
        if want is None: continue
        have = _read(os.path.join(ws, name))
        if have == want: continue
        with open(os.path.join(ws, name), "w", encoding="utf-8") as f: f.write(want)
        if have is not None: changed.append(name)
    return changed
