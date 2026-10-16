"""Run store: bench/results/ladder/<run_id>/{state.json, trials/<trial_id>/{trial.json, evidence/}, report.*} plus bench/results/ladder/history.jsonl.
Everything is written atomically under a file lock and after EVERY phase transition, so a killed runner resumes exactly where it was. The worker never
gets near these files (they live outside its code dir and outside the Docker mounts)."""
import fcntl, json, os, platform, shutil, subprocess, tempfile, time
from contextlib import contextmanager
from . import VERSION

def read_text(path):
    with open(path) as f: return f.read()

def root(home): return os.path.join(home, "bench", "results", "ladder")
def now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def new_run_id(name=None):
    base = time.strftime("%Y%m%d-%H%M%S")
    return f"{base}-{name}" if name else base

def _atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp.")
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)

class Store:
    def __init__(self, home, run_id):
        self.home, self.run_id = home, run_id; self.dir = os.path.join(root(home), run_id)
        self.state_path = os.path.join(self.dir, "state.json")
    def exists(self): return os.path.isfile(self.state_path)
    @contextmanager
    def locked(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, ".lock"), "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lf, fcntl.LOCK_UN)
    def load(self):
        with open(self.state_path) as f: return json.load(f)
    def save(self, st):
        st["updated"] = now_iso()
        with self.locked(): _atomic(self.state_path, st)
    def update(self, fn):
        """Read-modify-write under the lock; `fn(state)` mutates in place."""
        with self.locked():
            st = self.load(); fn(st); st["updated"] = now_iso(); _atomic(self.state_path, st); return st
    # per-trial detail
    def trial_dir(self, tid): return os.path.join(self.dir, "trials", tid)
    def trial_path(self, tid): return os.path.join(self.trial_dir(tid), "trial.json")
    def load_trial(self, tid):
        try:
            with open(self.trial_path(tid)) as f: return json.load(f)
        except (OSError, ValueError): return {}
    def save_trial(self, tid, data):
        with self.locked(): _atomic(self.trial_path(tid), data)
    def evidence_dir(self, tid): return os.path.join(self.trial_dir(tid), "evidence")
    def all_trials(self):
        st = self.load(); out = []
        for t in st["trials"]:
            d = self.load_trial(t["trial_id"]); out.append({**t, **d})
        return out
    # control files
    def cancel_path(self): return os.path.join(self.dir, "CANCEL")
    def cancel_requested(self): return os.path.exists(self.cancel_path())
    def request_cancel(self): open(self.cancel_path(), "w").close()
    def clear_cancel(self):
        try: os.remove(self.cancel_path())
        except OSError: pass
    def pid_path(self): return os.path.join(self.dir, "runner.pid")
    def active_path(self): return os.path.join(self.dir, ".active")
    def runner_alive(self):
        try:
            pid = int(read_text(self.pid_path()).strip()); os.kill(pid, 0)
            cmd = read_text(f"/proc/{pid}/cmdline").replace("\0", " "); return "capbench" in cmd or "codebot-bench" in cmd
        except (OSError, ValueError): return False

def list_runs(home):
    """Run ids, newest first."""
    d = root(home)
    return sorted((n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "state.json"))), reverse=True) if os.path.isdir(d) else []

def latest(home):
    r = list_runs(home); return r[0] if r else None

def resolve(home, ident=None):
    """Run id from user input: exact id, unique prefix/suffix match, or the latest run."""
    runs = list_runs(home)
    if not runs: raise SystemExit("no benchmark runs yet (codebot bench run ...)")
    if not ident: return runs[0]
    hit = [r for r in runs if r == ident] or [r for r in runs if r.startswith(ident) or ident in r]
    if len(hit) != 1: raise SystemExit(f"run '{ident}' is " + ("ambiguous: " + ", ".join(hit[:5]) if hit else "unknown (codebot bench status lists runs)"))
    return hit[0]

# ---------- history ----------
def history_path(home): return os.path.join(root(home), "history.jsonl")

def append_history(home, entry):
    os.makedirs(root(home), exist_ok=True)
    with open(history_path(home) + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(history_path(home), "a") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally: fcntl.flock(lf, fcntl.LOCK_UN)

def history(home):
    out = []
    try:
        with open(history_path(home)) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: pass
    except OSError: pass
    return out

# ---------- environment ----------
def _run(*a, cwd=None, t=10):
    try: return subprocess.run(list(a), capture_output=True, text=True, timeout=t, cwd=cwd).stdout.strip()
    except (OSError, subprocess.SubprocessError): return ""

def environment(home, proj_like=None, model=None):
    """What a result must be compared against later: code version, model and inference settings, hardware, software."""
    from ..config import read_env, home_env
    cfg = home_env(home); dirty = bool(_run("git", "status", "--porcelain", "--untracked-files=no", cwd=home))
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f: cpu = next((l.split(":", 1)[1].strip() for l in f if l.startswith("model name")), "")
    except OSError: pass
    mem = ""
    try:
        with open("/proc/meminfo") as f: mem = next((l.split(":", 1)[1].strip() for l in f if l.startswith("MemTotal")), "")
    except OSError: pass
    gpu = _run("nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader") or None
    return {"benchmark_framework_version": VERSION, "codebot_commit": _run("git", "rev-parse", "HEAD", cwd=home) or None, "codebot_dirty": dirty,
            "model": model or cfg.get("CODEBOT_MODEL", "codebot-model"), "ollama_port": cfg.get("OLLAMA_PORT", "11500"),
            "inference": {"num_ctx": cfg.get("NUM_CTX"), "worker_temperature": cfg.get("WORKER_TEMPERATURE", "0.2"), "planner_temperature": 0.2, "worker_max_tokens": cfg.get("WORKER_MAX_TOKENS", "1800"),
                          "worker_max_turns": cfg.get("WORKER_MAX_TURNS", "24"), "thermal_guard": cfg.get("THERMAL_GUARD"), "sandbox": "docker"},
            "ladder_tries": cfg.get("LADDER_TRIES"), "python": platform.python_version(), "platform": platform.platform(), "cpu": cpu, "memory": mem, "gpu": gpu}
