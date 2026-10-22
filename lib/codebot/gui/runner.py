"""Run a project's program from the window. The page shows Run; starting it opens a terminal there.
One program per project. The command is chosen here; the page can only start, stop, and type."""
import os, re, select, signal, subprocess, sys, threading, time
from ..config import read_env

GUI = re.compile(r"(?m)^(?:import|from)\s+(tkinter|customtkinter|PyQt[456]|PySide[26]|pygame|kivy|wx|gi)\b"
                 r"|require\(['\"]electron['\"]\)|github\.com/fyne-io/fyne|gioui\.org")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
SKIP = {".git", "__pycache__", "node_modules", ".gocache"}
KEEP = 150000                                                          # characters of output kept per program
_lock = threading.Lock()
_runs = {}

def _files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP and not d.startswith("."))
        for f in sorted(fn):
            if f.startswith("."): continue
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return out

def _text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f: return f.read(8000)
    except OSError: return ""

def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK): return p
    return None

def command(workdir):
    """[argv, label, opens its own window] for a program in this folder, or None when there is nothing to run."""
    if not workdir or not os.path.isdir(workdir): return None
    files = _files(workdir)
    window = any(GUI.search(_text(os.path.join(workdir, f))) for f in files if f.endswith((".py", ".js", ".mjs", ".go")))
    py = [f for f in files if f.endswith(".py")]
    if "main.py" in py: found = [[sys.executable, "main.py"], "python3 main.py"]
    elif len(py) == 1: found = [[sys.executable, py[0]], f"python3 {py[0]}"]
    elif "main.go" in files and _which("go"): found = [["go", "run", "."], "go run ."]
    elif "main.js" in files and _which("node"): found = [["node", "main.js"], "node main.js"]
    elif "main.rb" in files and _which("ruby"): found = [["ruby", "main.rb"], "ruby main.rb"]
    elif "main.sh" in files: found = [["bash", "main.sh"], "bash main.sh"]
    else: return None
    return [found[0], found[1], window]

def describe(home, name):
    env = read_env(os.path.join(home, "projects", name, "project.env"))
    wd = env.get("RUNDIR") or env.get("WORKDIR") or ""
    found = command(wd)
    if not found: return {"show": False}
    with _lock: running = (home, name) in _runs and _runs[(home, name)].running
    return {"show": True, "label": found[1], "window": found[2], "running": running}

class _Run:
    def __init__(self, argv, cwd):
        self.argv, self.cwd = argv, cwd
        self.text, self.running, self.stopped = "", True, False
        self.base = 0                                                  # characters dropped from the front of text (positions stay absolute)
        master, slave = os.openpty()
        try:
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, start_new_session=True, close_fds=True,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24"})
        except Exception:
            os.close(master); os.close(slave); raise
        os.close(slave)
        self.master = master
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try: ready, _, _ = select.select([self.master], [], [], 0.2)
            except (OSError, ValueError): break
            if ready:
                try: data = os.read(self.master, 4096)
                except OSError: break
                if not data: break
                chunk = ANSI.sub("", data.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
                with _lock:
                    self.text += chunk
                    if len(self.text) > KEEP: drop = len(self.text) - KEEP; self.text = self.text[drop:]; self.base += drop
            elif self.proc.poll() is not None: break
        code = self.proc.poll()
        if code is None:
            try: code = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired: code = None
        with _lock:
            self.running = False
            if self.stopped: self.text += "\n— stopped\n"
            elif code == 0: self.text += "\n— ended\n"
            elif code is not None: self.text += f"\n— ended with status {code}\n"
        try: os.close(self.master)
        except OSError: pass

    def write(self, text):
        if not self.running: raise RuntimeError("The program is not running.")
        data = text.encode("utf-8")[:4000]
        if not data.endswith(b"\n"): data += b"\n"
        try: os.write(self.master, data)
        except OSError as e: raise RuntimeError("The program is not running.") from e

    def stop(self):
        self.stopped = True
        if self.proc.poll() is not None: return
        try: os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError): return
        for _ in range(20):
            if self.proc.poll() is not None: return
            time.sleep(0.05)
        try: os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass

def workdir(home, name):
    env = read_env(os.path.join(home, "projects", name, "project.env"))
    wd = env.get("RUNDIR") or env.get("WORKDIR") or ""
    if not wd or not os.path.isdir(wd): raise FileNotFoundError("no code folder")
    return os.path.realpath(wd)

def start(home, name):
    wd = workdir(home, name)
    found = command(wd)
    if not found: raise RuntimeError("There is nothing to run yet.")
    key = (home, name)
    with _lock:
        old = _runs.get(key)
    if old: old.stop()
    run = _Run(found[0], wd)
    with _lock: _runs[key] = run
    return {"ok": True, "label": found[1]}

def send(home, name, text):
    with _lock: run = _runs.get((home, name))
    if not run: raise RuntimeError("Press Run first.")
    run.write(str(text or ""))
    return {"ok": True}

def stop(home, name):
    with _lock: run = _runs.get((home, name))
    if run: run.stop()
    return {"ok": True}

def output(home, name, since):
    with _lock:
        run = _runs.get((home, name))
        if not run: return {"output": "", "next": 0, "running": False}
        text, running, base = run.text, run.running, run.base
    since = max(0, int(since or 0))
    if since > base + len(text): since = 0                             # a position from an earlier run of the program
    return {"output": text[max(0, since - base):], "next": base + len(text), "running": running}
