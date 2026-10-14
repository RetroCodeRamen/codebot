"""Paths and config. Reads the same KEY="value" env files the bash side uses."""
import os, re, json, time

def read_env(path):
    out = {}
    try:
        with open(path) as f: lines = f.read().splitlines()
    except OSError: return out
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln: continue
        k, v = ln.split("=", 1)
        k = k.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k): continue
        v = v.strip()
        if v[:1] in "\"'" and v[:1] and v.find(v[0], 1) > 0:
            v = v[1:v.find(v[0], 1)]
        else:
            v = re.split(r"\s+#", v, 1)[0].strip()
        out[k] = v
    return out

def home_dir():
    return os.environ.get("CODEBOT_HOME") or os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", ".."))

class Project:
    def __init__(self, name, home=None):
        self.home = home or home_dir()
        self.name = name
        self.state = os.path.join(self.home, "projects", name)
        if not os.path.isdir(self.state): raise SystemExit(f"codebot-engine: unknown project '{name}'")
        self.cfg = read_env(os.path.join(self.home, "config", "codebot.env"))
        penv = read_env(os.path.join(self.state, "project.env"))
        self.cfg.update(penv)
        self.workdir = penv.get("WORKDIR", "")
        self.rundir = penv.get("RUNDIR") or self.workdir
        self.mode = penv.get("MODE", "code")
    def p(self, *a): return os.path.join(self.state, *a)
    def get(self, key, default=None, cast=str):
        v = os.environ.get(key, self.cfg.get(key))
        if v is None or v == "": return default
        try: return cast(v)
        except (TypeError, ValueError): return default

def now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def append_jsonl(path, obj):
    with open(path, "a") as f: f.write(json.dumps(obj, ensure_ascii=False) + "\n")
