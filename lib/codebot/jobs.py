"""One model job at a time, for every interface. A project loop, a benchmark run, a preflight, a model evaluation, a thermal sweep and the
planning chat each own the model while they run; a second one is refused with the owner's name. The owner holds an exclusive flock on
logs/model-job.lock for its whole life (the kernel drops it when the process dies, so a crash or a reboot never leaves a stale owner, unlike
a pid file) and describes itself in logs/model-job.json. A benchmark's trial loops run inside their benchmark's ownership. The per-step
logs/model.lock (one model call at a time) is a different, older lock and stays.

bin/codebot holds the lock on file descriptor 9 (`exec 9>>...; flock -w 1 9`) and writes the description with `describe-write`; Python
owners use hold(). Probes (owner()) take the lock for microseconds, so acquirers wait up to a second before giving up."""
import fcntl, json, os, sys, time

LOCK, INFO = "model-job.lock", "model-job.json"
KINDS = ("project", "benchmark", "preflight", "eval", "thermal", "planning", "tidy")

class Busy(Exception):
    def __init__(self, owner):
        self.owner = owner; super().__init__(f"another model job is running: {describe(owner)}")

def paths(home):
    d = os.path.join(home, "logs"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, LOCK), os.path.join(d, INFO)

def _read_info(path):
    try:
        with open(path) as f: return json.load(f)
    except (OSError, ValueError): return None

def write_info(home, kind, name, pid, extra=None):
    _, info = paths(home); tmp = f"{info}.{pid}.tmp"
    with open(tmp, "w") as f: json.dump({"kind": kind, "name": name, "pid": pid, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), **(extra or {})}, f)
    os.replace(tmp, info)

def owner(home):
    """The job that owns the model now ({kind, name, pid, started}), or None. kind "unknown" when the lock is held but its description is missing."""
    lock, info = paths(home)
    try: fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError: return None
    try:
        try: fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            d = _read_info(info) or {}
            return {"kind": d.get("kind", "unknown"), "name": d.get("name", ""), "pid": d.get("pid"), "started": d.get("started")}
        fcntl.flock(fd, fcntl.LOCK_UN); return None
    finally: os.close(fd)

def describe(o):
    if not o: return "nothing"
    what = {"project": "project", "benchmark": "benchmark run", "preflight": "preflight check", "eval": "model evaluation", "thermal": "thermal sweep",
            "planning": "planning chat", "tidy": "memory tidy"}.get(o.get("kind"), "a job")
    since = f", since {o['started']}" if o.get("started") else ""
    return f"{what} {o.get('name') or ''}".strip() + (f" (pid {o['pid']}{since})" if o.get("pid") else "")

def nested(o, project):
    """True when `project` runs inside the owner's job: a benchmark's own trial projects (cap-<run id>-...)."""
    return bool(o) and o.get("kind") == "benchmark" and bool(o.get("name")) and project.startswith(f"cap-{o['name'].lower()}-")

def unlocked(home):
    """Model jobs running without the lock: project loops and benchmark runners started by a codebot older than the lock. Found from their
    pid files with the same tests bin/codebot and the benchmark use, so they still count as the one model job."""
    from . import projstate as P
    out = []
    root = os.path.join(home, "bench", "results", "ladder")
    for rid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        pid = P.pid_of(os.path.join(root, rid, "runner.pid")); cl = P._cmdline(pid) if pid else None
        if cl and ("capbench" in cl or "codebot-bench" in cl): out.append({"kind": "benchmark", "name": rid, "pid": pid, "started": None, "locked": False})
    pr = os.path.join(home, "projects")
    for n in sorted(os.listdir(pr)) if os.path.isdir(pr) else []:
        pid, alive = P.loop_alive(os.path.join(pr, n))
        if alive and not any(nested(o, n) for o in out): out.append({"kind": "project", "name": n, "pid": pid, "started": None, "locked": False})
    return out

def current(home):
    """The model job running now: the lock's owner, else a job running without the lock, else None."""
    o = owner(home)
    if o: return o
    u = unlocked(home)
    return u[0] if u else None

class Hold:
    """The model for this process's lifetime (or until release()); raises Busy with the owner when another job has it."""
    def __init__(self, home, kind, name, wait=1.0):
        self.home, (self.lock, self.info) = home, paths(home)
        self.fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o644)   # not inherited by child processes (PEP 446)
        end = time.time() + wait
        while True:
            try: fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.time() >= end:
                    o = owner(home); os.close(self.fd); self.fd = None; raise Busy(o or {"kind": "unknown"})
                time.sleep(0.05)
        write_info(home, kind, name, os.getpid())
    def release(self):
        if self.fd is None: return
        d = _read_info(self.info) or {}
        if d.get("pid") == os.getpid():
            try: os.remove(self.info)
            except OSError: pass
        os.close(self.fd); self.fd = None
    def __enter__(self): return self
    def __exit__(self, *a): self.release()

def main(argv):
    """codebot-engine job owner|describe-write|check <home> ...: what bin/codebot needs (it holds the lock itself on fd 9)."""
    cmd, home = argv[0], argv[1]
    if cmd == "owner":
        o = owner(home); print(json.dumps(o)); return 0
    if cmd == "describe-write":                       # describe-write <home> <kind> <name> <pid>: the caller already holds the lock
        write_info(home, argv[2], argv[3], int(argv[4])); return 0
    if cmd == "check":                                 # check <home> <project>: exit 0 if this project may start now, else 75 with the owner
        o = owner(home)
        if o: busy = not nested(o, argv[2])
        else:
            o = next((u for u in unlocked(home) if u["name"] != argv[2] and not nested(u, argv[2])), None); busy = bool(o)
        if not busy: return 0
        print(describe(o)); return 75
    print("usage: jobs owner|describe-write|check <home> ...", file=sys.stderr); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
