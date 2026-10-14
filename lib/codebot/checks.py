"""Per-task acceptance checks: written first, hashed, frozen. The worker may not change them."""
import hashlib, json, os, re, shutil, subprocess, time
from . import audit, sandbox
from .config import now

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""): h.update(b)
    return h.hexdigest()

def _sha_text(s): return hashlib.sha256(s.encode()).hexdigest()

def jload(path):
    with open(path) as f: return json.load(f)

def jdump(path, obj, **kw):
    with open(path, "w") as f: json.dump(obj, f, **kw)

def checks_dir(proj): return proj.p("checks")
def frozen_dir(proj): return proj.p("checks", ".frozen")
def lock_path(proj): return proj.p("checks.lock")

def script_rel(tid): return f"{tid}.sh"

def write_script(proj, task):
    os.makedirs(checks_dir(proj), exist_ok=True)
    body = (f"#!/usr/bin/env bash\n# frozen acceptance check for {task['id']}: {task['title']}\n"
            f"cd \"$CODEBOT_RUNDIR\" || exit 99\n{task['check']}\n")
    with open(os.path.join(checks_dir(proj), script_rel(task["id"])), "w") as f: f.write(body)

def _tree(proj):
    """relative path -> sha for every file under checks/ except .frozen"""
    out, root = {}, checks_dir(proj)
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != ".frozen"]
        for f in fn:
            full = os.path.join(dp, f); out[os.path.relpath(full, root)] = _sha(full)
    return out

def freeze(proj, data, only_new=False):
    """Write check scripts for tasks with a check, then hash and copy everything to checks/.frozen.
    only_new: keep existing lock entries untouched (used when the harness adds tasks later)."""
    lock = load_lock(proj) if only_new else {"files": {}, "checks": {}}
    for t in data["tasks"]:
        if not t.get("check"): continue
        if only_new and t["id"] in lock["checks"]: continue
        write_script(proj, t); lock["checks"][t["id"]] = _sha_text(t["check"])
    tree = _tree(proj)
    for rel, sha in tree.items():
        if only_new and rel in lock["files"]: continue
        lock["files"][rel] = sha
        dst = os.path.join(frozen_dir(proj), rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(checks_dir(proj), rel), dst)
    texts = {t["id"]: t["check"] for t in data["tasks"] if t.get("check")}
    ft = os.path.join(frozen_dir(proj), "_check_text.json")
    old = jload(ft) if only_new and os.path.exists(ft) else {}
    old.update({k: v for k, v in texts.items() if k not in old})
    os.makedirs(frozen_dir(proj), exist_ok=True); jdump(ft, old, indent=1)
    lock["frozen_at"] = lock.get("frozen_at") or now(); lock["updated"] = now()
    jdump(lock_path(proj), lock, indent=1)
    return lock

def load_lock(proj):
    try:
        with open(lock_path(proj)) as f: return json.load(f)
    except (OSError, ValueError): return {"files": {}, "checks": {}}

def is_frozen(proj): return os.path.exists(lock_path(proj))

def verify(proj, data):
    """Return a list of tamper findings (strings). Empty = intact."""
    lock, bad = load_lock(proj), []
    tree = _tree(proj)
    for rel, sha in lock["files"].items():
        if rel not in tree: bad.append(f"missing:{rel}")
        elif tree[rel] != sha: bad.append(f"modified:{rel}")
    for rel in tree:
        if rel not in lock["files"]: bad.append(f"unexpected:{rel}")
    for t in data["tasks"]:
        if t["id"] in lock["checks"] and _sha_text(t.get("check") or "") != lock["checks"][t["id"]]:
            bad.append(f"task-check:{t['id']}")
    return bad

def restore(proj, data, findings):
    """Put frozen copies back; drop unexpected files; restore task check text from the frozen record."""
    ft = os.path.join(frozen_dir(proj), "_check_text.json")
    texts = jload(ft) if os.path.exists(ft) else {}
    for f in findings:
        kind, _, what = f.partition(":")
        if kind in ("missing", "modified"):
            dst = os.path.join(checks_dir(proj), what); os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(frozen_dir(proj), what), dst)
        elif kind == "unexpected":
            try: os.remove(os.path.join(checks_dir(proj), what))
            except OSError: pass
        elif kind == "task-check":
            for t in data["tasks"]:
                if t["id"] == what and what in texts: t["check"] = texts[what]

_NORM = [(re.compile(r"0x[0-9a-fA-F]+"), "0xN"), (re.compile(r"\b\d+(\.\d+)?\b"), "N"),
         (re.compile(r"/tmp/[^\s:'\"]+"), "/tmp/P"), (re.compile(r"\s+"), " ")]

def failure_sig(out):
    """Stable signature of a failure: last lines with numbers/addresses/temp paths normalised."""
    tail = "\n".join(out.strip().splitlines()[-12:])
    for rx, rep in _NORM: tail = rx.sub(rep, tail)
    return hashlib.sha1(tail.encode()).hexdigest()[:12]

def run_check(proj, task, timeout=600):
    """Run the frozen script for a task. Returns dict(rc, out, sig, secs)."""
    script = os.path.join(checks_dir(proj), script_rel(task["id"]))
    t0 = time.time()
    try:   # checks run in the same offline sandbox as the worker's commands: the frozen script is mounted read-only
        rc, out, label = sandbox.execute(proj, ["bash", script], timeout=timeout, rw=[proj.rundir], ro=[checks_dir(proj)],
                                         env={"CODEBOT_RUNDIR": proj.rundir, "CODEBOT_CHECKS": checks_dir(proj)})
    except OSError as e:
        rc, out, label = 127, f"[cannot run check: {e}]", "none"
    out = out[-6000:]
    audit.log(proj, "check_run", task=task["id"], rc=rc, secs=round(time.time() - t0, 1), sandbox=label)
    return {"rc": rc, "out": out, "sig": failure_sig(out) if rc else "", "secs": round(time.time() - t0, 1)}
