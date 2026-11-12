"""The GUI's JSON API. Reads go through the shared modules (projstate, jobs, drafts, settings); every action runs the same `codebot`
command a user would type (argument arrays, never a shell), so the GUI, the terminal menu and the CLI cannot disagree. Names and paths are
checked here, not trusted from the page."""
import difflib, glob, json, os, re, shutil, subprocess, threading, time
from .. import projstate as P, jobs
from ..config import read_env, home_env
from . import settings as S, sysinfo, actions

ALIVE_STATES = ("running", "cooling", "paused", "pause_requested")

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
MODEL = re.compile(r"^[\w./:@-]{1,200}$")
RUN = re.compile(r"^[\w.-]{1,160}$")
MAX_FILE = 1 << 20

class ApiError(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.code = code

class Ctx:
    def __init__(self, home, demo=False):
        self.home, self.demo = home, demo
        self.cli = os.path.join(home, "bin", "codebot")
        self.cache = sysinfo.Cache(); self.sampler = sysinfo.Sampler(); self.sampler.start()
        self.disk, self.disk_busy = None, False

    # ---------- helpers ----------
    def cfg(self): return home_env(self.home)
    def port(self):
        try: return int(self.cfg().get("OLLAMA_PORT", 11500))
        except ValueError: return 11500
    def proj_dir(self, name):
        if not NAME.match(name or ""): raise ApiError("invalid project name")
        d = os.path.join(self.home, "projects", name)
        if not os.path.isfile(os.path.join(d, "project.env")): raise ApiError("no such project", 404)
        return d
    def run_cli(self, *args, timeout=120):
        try: p = subprocess.run([self.cli, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=self.home)
        except subprocess.TimeoutExpired: raise ApiError(f"`codebot {args[0]}` did not finish within {timeout} s", 504)
        return {"ok": p.returncode == 0, "code": p.returncode, "out": _redact(self, (p.stdout + p.stderr).strip())}

def _int(q, key, default):
    """A whole-number query parameter; anything else is the default (never a server error)."""
    try: return int(q.get(key) if q.get(key) not in (None, "") else default)
    except (TypeError, ValueError): return default

def _redact(ctx, text):
    """Never let a notification topic or token reach the page through command output."""
    c = ctx.cfg()
    for k in ("NTFY_TOPIC", "NTFY_TOKEN"):
        v = c.get(k)
        if v and len(v) >= 4: text = text.replace(v, "<hidden>")
    return text

# ---------- global status ----------
def status(ctx, q):
    c = ctx.cfg()
    oll = ctx.cache.get("ollama", 3, lambda: sysinfo.ollama(ctx.port()))
    g = ctx.cache.get("gpu", 15, sysinfo.gpu)
    owner = jobs.current(ctx.home)
    if owner and owner.get("kind") == "project" and owner.get("name"):
        try: owner["state"] = P.state(ctx.home, owner["name"])["state"]
        except Exception: pass
    latest = ctx.sampler.latest(); mi = sysinfo.meminfo()
    return {"time": time.time(), "demo": ctx.demo, "ollama": oll, "job": owner, "job_text": jobs.describe(owner) if owner else None,
            "machine": {"temp": latest and latest.get("temp"), "temp_at": latest and latest.get("t"), "mem": mi, "swap_in_bps": latest and latest.get("swap_in_bps"),
                        "swap_out_bps": latest and latest.get("swap_out_bps"), "gpu": g},
            "thermal": {"guard": c.get("THERMAL_GUARD", "0") not in ("0", "", "false", "no"), "max": c.get("THERMAL_MAX", "92"), "resume": c.get("THERMAL_RESUME", "84")},
            "notifications": {"enabled": c.get("NTFY_ENABLED", "1") != "0", "phone_control": c.get("PHONE_CONTROL", "1") != "0"},
            "default_model": c.get("CODEBOT_MODEL", "codebot-model"), "inbox": len(inbox_items(ctx))}

# ---------- projects ----------
def project_attention(st, acks=None):
    """Items that need a person on this project (questions, blocked, finished, crashed)."""
    acks = acks if acks is not None else {}
    n, out = st["name"], []
    if st["state"] == "crashed":
        out.append({"key": f"crash:{n}:{st.get('updated')}", "kind": "crashed", "text": "Interrupted — the loop should be running but is not"})
    sh = st.get("shape") or {}
    if st.get("workflow") == "shape" and sh.get("phase") == "questions" and not sh.get("busy"):
        out.append({"key": f"q:{n}", "kind": "question", "text": "Questions for you before the plan is ready"})
    if st.get("blocked"):
        out.append({"key": f"blocked:{n}", "kind": "question", "text": "A question is waiting"})
    fin = (st.get("drafts") or {}).get("finished")
    if fin:
        key = f"fin:{n}:{fin.get('at')}"
        if key not in acks:
            notice = sh.get("notice") if sh.get("phase") == "done" and sh.get("notice") else None
            text = notice or f"Finished ({fin.get('stop')}) after {fin.get('minutes')} min"
            out.append({"key": key, "kind": "finished", "text": text, "stop": fin.get("stop")})
    return out

def projects(ctx, q):
    acks = _acks(ctx)
    rows = P.projects(ctx.home, include_bench=q.get("bench") == "1")
    for st in rows: st["attention"] = project_attention(st, acks)
    return {"projects": rows}

def _versions(ctx, name, st):
    """The versions a drafts project has: spec, best, latest round with code, each round, snapshot, baseline, delivered."""
    d = os.path.join(ctx.proj_dir(name), "drafts"); rows = P.rounds(ctx.home, name)
    with_code = [r["round"] for r in rows if os.path.isdir(os.path.join(P.round_dir(ctx.home, name, r["round"]), "code"))]
    best = (st.get("drafts") or {}).get("best") or {}
    return {"best_round": best.get("round"), "latest_round": with_code[-1] if with_code else None, "rounds": [r["round"] for r in rows],
            "delivered_commit": best.get("commit"), "has_snapshot": os.path.isdir(os.path.join(d, "ws")), "has_baseline": os.path.isdir(os.path.join(d, "baseline"))}

def project(ctx, q, name):
    d = ctx.proj_dir(name); st = P.state(ctx.home, name)
    st["attention"] = project_attention(st, _acks(ctx))
    out = {"state": st, "spec": P.read_text(os.path.join(d, "SPEC.md"), 200000), "phase": P.phase(ctx.home, name, st),
           "title": st.get("title") or name, "blurb": st.get("blurb") or "", "attention": st["attention"]}
    if st["workflow"] == "drafts":
        out["versions"] = _versions(ctx, name, st)
        ds = P.read_json(os.path.join(d, "drafts", "state.json")) or {}
        b = ds.get("best") or {}
        out["best_feedback"] = b.get("feedback")
        out["best_examples"] = [{"n": i, "example": a, "passed": ok, "message": m} for i, a, ok, m in P.example_results(ctx.home, name, b.get("feedback"))] if b else []
        out["checks"] = (P.read_json(os.path.join(d, "drafts", "checks", "checks.json")) or {}).get("asserts") or []
        out["summary"] = P.read_json(os.path.join(d, "drafts", "summary.json"))
        out["budget_pending"] = _budget_pending(st)
    else:
        out["tasks"] = P.read_json(os.path.join(d, "tasks.json")); out["plan"] = P.read_text(os.path.join(d, "PLAN.md"), 200000)
        out["answers"] = P.read_text(os.path.join(d, "ANSWERS.md"), 100000); out["progress"] = (P.read_text(os.path.join(d, "PROGRESS.md")) or "")[-20000:]
    return out

def _budget_pending(st):
    dr = st.get("drafts") or {}
    if dr.get("finished") or dr.get("budget_configured") is None or dr.get("budget_minutes") is None: return None
    return dr["budget_configured"] if abs(dr["budget_configured"] - dr["budget_minutes"]) > 1e-6 else None

def rounds(ctx, q, name):
    ctx.proj_dir(name); st = P.state(ctx.home, name)
    return {"rounds": P.rounds(ctx.home, name), "phase": P.phase(ctx.home, name, st), "state": st["state"], "updated": st.get("updated")}

def _feedback_after(ctx, name, n):
    """The feedback the next round received about round n (a repair prompt ends with it), or None."""
    nxt = os.path.join(P.round_dir(ctx.home, name, n + 1), "prompt.md"); t = P.read_text(nxt)
    if not t or not t.startswith("Specification (SPEC.md):"): return None
    end = t.rfind("\n\nFix the code so that it meets the whole specification.")
    if end < 0: return None
    body = t[:end]; k = body.rfind("```\n\n")
    return body[k + 5:].strip() if k >= 0 else None

def round_detail(ctx, q, name, n):
    ctx.proj_dir(name)
    try: n = int(n)
    except ValueError: raise ApiError("bad round")
    rd = P.round_dir(ctx.home, name, n)
    if not os.path.isdir(rd): raise ApiError("no such round", 404)
    row = next((r for r in P.rounds(ctx.home, name) if r.get("round") == n), None)
    ds = P.read_json(os.path.join(ctx.proj_dir(name), "drafts", "state.json")) or {}
    b = ds.get("best") or {}
    fb = b.get("feedback") if b.get("round") == n else _feedback_after(ctx, name, n)
    return {"round": n, "row": row, "prompt": _clip(P.read_text(os.path.join(rd, "prompt.md"))), "reply": _clip(P.read_text(os.path.join(rd, "reply.md"))),
            "files": _tree(os.path.join(rd, "code")), "feedback": fb, "feedback_source": "best version (exact)" if b.get("round") == n else ("the next round's prompt" if fb else None),
            "examples": [{"n": i, "example": a, "passed": ok, "message": m} for i, a, ok, m in P.example_results(ctx.home, name, fb)] if fb is not None else []}

def _clip(t, n=400000): return t if t is None or len(t) <= n else t[:n] + f"\n... ({len(t) - n} more characters not shown)"

def _tree(root, limit=800):
    out = []
    if not os.path.isdir(root): return out
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "__pycache__")
        for f in sorted(fn):
            p = os.path.join(dp, f); rel = os.path.relpath(p, root)
            try: out.append({"path": rel, "size": os.path.getsize(p)})
            except OSError: continue
            if len(out) >= limit: return out
    return out

HARNESS = ["SPEC.md", "project.env", "drafts/state.json", "drafts/summary.json", "drafts/rounds.jsonl", "drafts/checks/checks.json", "loop.log",
           "audit.jsonl", "thermal.jsonl", "tasks.json", "PLAN.md", "PROGRESS.md", "ANSWERS.md", "STATUS"]

def files(ctx, q, name):
    d = ctx.proj_dir(name); st = P.state(ctx.home, name); out = {"workflow": st["workflow"], "groups": []}
    if st["workflow"] == "drafts":
        v = _versions(ctx, name, st)
        if v["best_round"]: out["groups"].append({"version": "best", "label": f"best version (round {v['best_round']})", "files": _tree(os.path.join(P.round_dir(ctx.home, name, v["best_round"]), "code"))})
        if v["latest_round"]: out["groups"].append({"version": "latest", "label": f"latest round with code (round {v['latest_round']})", "files": _tree(os.path.join(P.round_dir(ctx.home, name, v["latest_round"]), "code"))})
        if v["has_baseline"]: out["groups"].append({"version": "baseline", "label": "baseline (the code folder when the run began)", "files": _tree(os.path.join(d, "drafts", "baseline"))})
        if v["has_snapshot"]: out["groups"].append({"version": "snapshot", "label": "working snapshot (the last version judged)", "files": _tree(os.path.join(d, "drafts", "ws"))})
        out["versions"] = v
    reports = sorted(os.path.basename(p) for p in glob.glob(os.path.join(d, "report-*.md")))
    out["harness"] = [{"path": h, "size": os.path.getsize(os.path.join(d, h))} for h in HARNESS + reports if os.path.isfile(os.path.join(d, h))]
    return out

def _root(ctx, name, version):
    d = ctx.proj_dir(name); st = P.state(ctx.home, name)
    if version == "harness": return d
    if st["workflow"] != "drafts": raise ApiError("this project has no drafts versions")
    v = _versions(ctx, name, st)
    if version == "best" and v["best_round"]: return os.path.join(P.round_dir(ctx.home, name, v["best_round"]), "code")
    if version == "latest" and v["latest_round"]: return os.path.join(P.round_dir(ctx.home, name, v["latest_round"]), "code")
    if version.startswith("round:"):
        try: return os.path.join(P.round_dir(ctx.home, name, int(version[6:])), "code")
        except ValueError: pass
    if version == "baseline": return os.path.join(d, "drafts", "baseline")
    if version == "snapshot": return os.path.join(d, "drafts", "ws")
    raise ApiError(f"no version {version!r}")

def _safe_join(root, rel):
    if not rel or rel.startswith("/") or "\0" in rel: raise ApiError("bad path")
    p = os.path.realpath(os.path.join(root, rel)); r = os.path.realpath(root)
    if p != r and not p.startswith(r + os.sep): raise ApiError("path outside the version")
    return p

def _read_version_file(ctx, name, version, rel):
    if version == "delivered": return _git_show(ctx, name, rel)
    if version == "harness":                                           # logs and reports can quote anything the loop printed
        r = _read_plain(ctx, name, version, rel)
        if "text" in r: r["text"] = _redact(ctx, r["text"])
        return r
    return _read_plain(ctx, name, version, rel)

def _read_plain(ctx, name, version, rel):
    if version == "harness" and rel not in HARNESS and not re.fullmatch(r"report-[\w.-]+\.md", rel): raise ApiError("not a harness file")
    p = _safe_join(_root(ctx, name, version), rel)
    try: size = os.path.getsize(p)
    except OSError: raise ApiError("no such file", 404)
    with open(p, "rb") as f: data = f.read(MAX_FILE)
    if b"\0" in data[:8000]: return {"binary": True, "size": size}
    text = data.decode(errors="replace")
    if rel == "project.env": text = "\n".join(_mask_env_line(l) for l in text.split("\n"))
    return {"text": text, "size": size, "truncated": size > MAX_FILE}

def _mask_env_line(l):
    m = S.RE_LINE.match(l)
    return f"{m.group(2)}=\"<hidden>\"" if m and (S.META.get(m.group(2)) or ("", ""))[1] in ("secret", "command") else l

def file_view(ctx, q, name):
    return _read_version_file(ctx, name, q.get("v", "best"), q.get("path", ""))

def _git(ctx, name, *args, timeout=30):
    env = read_env(os.path.join(ctx.proj_dir(name), "project.env")); wd = env.get("WORKDIR")
    if not wd or not os.path.isdir(wd): raise ApiError("the project's code folder is missing")
    p = subprocess.run(["git", "-C", wd, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def _git_show(ctx, name, rel):
    ctx.proj_dir(name); st = P.state(ctx.home, name); c = ((st.get("drafts") or {}).get("best") or {}).get("commit")
    if not c or not re.fullmatch(r"[0-9a-f]{7,40}", c): raise ApiError("nothing delivered as a commit (the code folder is not a git repository, or nothing has built yet)", 404)
    if rel.startswith("-") or ".." in rel.split("/"): raise ApiError("bad path")
    rc, out, err = _git(ctx, name, "show", f"{c}:{rel}")
    if rc: raise ApiError(f"git show failed: {err.strip()[:200]}", 404)
    return {"text": out[:MAX_FILE], "size": len(out), "truncated": len(out) > MAX_FILE, "commit": c}

def compare(ctx, q, name):
    a, b, rel = q.get("a", "latest"), q.get("b", "best"), q.get("path")
    def texts(v):
        if rel:
            try: return {rel: _read_version_file(ctx, name, v, rel).get("text") or ""}
            except ApiError as e:
                if e.code == 404: return {rel: None}
                raise
        root = _root(ctx, name, v); return {f["path"]: (_read_version_file(ctx, name, v, f["path"]).get("text") or "") for f in _tree(root) if f["size"] < MAX_FILE}
    ta, tb = texts(a), texts(b); out = []
    for path in sorted(set(ta) | set(tb)):
        x, y = tb.get(path), ta.get(path)                              # from b (older/reference) to a
        if x == y: continue
        out.append({"path": path, "diff": "".join(difflib.unified_diff((x or "").splitlines(True), (y or "").splitlines(True), f"{b}/{path}", f"{a}/{path}", n=3)),
                    "status": "added" if x is None else "removed" if y is None else "changed"})
    return {"from": b, "to": a, "files": out}

def delivered(ctx, q, name):
    d = ctx.proj_dir(name); st = P.state(ctx.home, name); env = read_env(os.path.join(d, "project.env"))
    summary = ctx.run_cli("diff", name, timeout=60)
    full = None
    rc, out, err = _git(ctx, name, "diff", f"HEAD...codebot/{name}") if os.path.isdir(env.get("WORKDIR", "") or "/nonexistent") else (1, "", "")
    if rc == 0: full = out[:MAX_FILE]
    return {"summary": summary["out"], "diff": full, "branch": f"codebot/{name}", "workdir": env.get("WORKDIR"), "rundir": env.get("RUNDIR") or env.get("WORKDIR"),
            "commit": ((st.get("drafts") or {}).get("best") or {}).get("commit")}

def report(ctx, q, name):
    ctx.proj_dir(name); r = ctx.run_cli("report", name, timeout=60); return {"text": r["out"], "ok": r["ok"]}

def audit(ctx, q, name):
    ctx.proj_dir(name); r = ctx.run_cli("audit", name, timeout=120); return {"text": r["out"], "warnings": not r["ok"]}

def log(ctx, q, name):
    path = os.path.join(ctx.proj_dir(name), "loop.log")
    try: size = os.path.getsize(path)
    except OSError: return {"text": "", "offset": 0, "size": 0}
    off = _int(q, "offset", -1)
    if off < 0 or off > size: off = max(0, size - (64 << 10))
    with open(path, "rb") as f: f.seek(off); data = f.read(512 << 10)
    return {"text": _redact(ctx, data.decode(errors="replace")), "offset": off + len(data), "start": off, "size": size}

# ---------- inbox ----------
def _acks(ctx): return P.read_json(os.path.join(ctx.home, "logs", "gui", "inbox-ack.json")) or {}

def inbox_items(ctx):
    """Legacy aggregate (status count + ack); UI uses per-project attention. Benchmark notices stay here for Tools → Benchmarks."""
    acks, out = _acks(ctx), []
    for st in P.projects(ctx.home):
        for a in project_attention(st, acks):
            out.append({**a, "project": st["name"]})
    for r in bench_runs(ctx, {}).get("runs", []):
        if r.get("status") in ("finished", "failed", "cancelled"):
            key = f"bench:{r['run_id']}:{r.get('status')}"
            if key not in acks: out.append({"key": key, "kind": "benchmark", "run": r["run_id"], "text": f"Benchmark run {r.get('status')}"})
    return out

def project_delete(ctx, body, name):
    """Remove projects/<name> only. Never touches the user's WORKDIR. Refuses while a loop is alive."""
    if not body.get("confirm"): raise ApiError("confirm required")
    d = ctx.proj_dir(name); st = P.state(ctx.home, name)
    if st["state"] in ALIVE_STATES: raise ApiError("Stop the project before deleting it", 409)
    workdir = st.get("workdir") or ""
    title = st.get("title") or name
    try: shutil.rmtree(d)
    except OSError as e: raise ApiError(f"could not delete: {e.strerror or e}")
    acks = _acks(ctx); changed = False
    for k in list(acks):
        if f":{name}:" in k or k.endswith(f":{name}") or k.startswith(f"q:{name}") or k.startswith(f"blocked:{name}") or k.startswith(f"crash:{name}") or k.startswith(f"fin:{name}:"):
            del acks[k]; changed = True
    if changed:
        ad = os.path.join(ctx.home, "logs", "gui"); os.makedirs(ad, exist_ok=True)
        tmp = os.path.join(ad, "inbox-ack.json.tmp")
        with open(tmp, "w") as f: json.dump(acks, f)
        os.replace(tmp, os.path.join(ad, "inbox-ack.json"))
    return {"ok": True, "name": name, "title": title, "workdir_kept": workdir}

def inbox(ctx, q): return {"items": inbox_items(ctx)}

def inbox_ack(ctx, body):
    key = str(body.get("key", ""))[:300]; acks = _acks(ctx); acks[key] = time.time()
    d = os.path.join(ctx.home, "logs", "gui"); os.makedirs(d, exist_ok=True); tmp = os.path.join(d, "inbox-ack.json.tmp")
    with open(tmp, "w") as f: json.dump(acks, f)
    os.replace(tmp, os.path.join(d, "inbox-ack.json")); return {"ok": True}

# ---------- spec coverage ----------
def check_spec(ctx, body):
    from .. import drafts
    text = body.get("text")
    if text is None and body.get("path"):
        p = os.path.expanduser(str(body["path"]))
        try:
            with open(p, encoding="utf-8", errors="replace") as f: text = f.read(400000)
        except OSError as e: raise ApiError(f"cannot read the spec: {e.strerror}")
    if not isinstance(text, str) or not text.strip(): raise ApiError("the spec is empty")
    cov = drafts.coverage(text); build, ex = drafts.examples(text)
    code = os.path.expanduser(str(body.get("code_dir") or "")) or None
    why = drafts.fits(None, text, code) if code and os.path.isdir(code) else None
    return {"coverage": cov, "examples": ex, "build": build, "fits": why is None, "why": why, "text_chars": len(text)}

# ---------- actions on projects ----------
def act(ctx, name, body, what):
    ctx.proj_dir(name)
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    if what == "stop" and body.get("confirm") is not True: raise ApiError("stopping needs confirmation", 428)
    args = {"start": ("start", name), "pause": ("pause", name), "pause-soft": ("pause", name, "--soft"), "resume": ("resume", name), "stop": ("stop", name)}[what]
    r = ctx.run_cli(*args, timeout=90)
    return {**r, "state": P.state(ctx.home, name)}

def answer(ctx, name, body):
    ctx.proj_dir(name); text = str(body.get("text", "")).strip()
    if not text or len(text) > 4000: raise ApiError("an answer is 1 to 4000 characters")
    return ctx.run_cli("answer", name, text, timeout=60)

def budget(ctx, name, body):
    ctx.proj_dir(name); st = P.state(ctx.home, name)
    if st["workflow"] != "drafts": raise ApiError("only a drafts run has a time budget")
    if (st.get("drafts") or {}).get("finished"): raise ApiError("this run has finished; a new run needs a new project")
    v = S.write(ctx.home, "project", "DRAFTS_MINUTES", body.get("minutes"), name)
    return {"ok": True, "minutes": v, "applies": "at the start of the next round (the engine reads it before every round)"}

def _spec_file(ctx, name, text):
    d = os.path.join(ctx.home, "logs", "gui", "specs"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.md")
    with open(p, "w") as f: f.write(text)
    return p

def new_project(ctx, body):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    name = str(body.get("name", "")).strip()
    if not NAME.match(name): raise ApiError("a project name is letters, digits, '.', '_' and '-', starting with a letter or digit")
    if os.path.exists(os.path.join(ctx.home, "projects", name)): raise ApiError(f"project {name} already exists")
    code = os.path.realpath(os.path.expanduser(str(body.get("code_dir", "")).strip()))
    if not body.get("code_dir"): raise ApiError("choose a code folder")
    if code == os.path.realpath(ctx.home) or code.startswith(os.path.realpath(ctx.home) + os.sep): raise ApiError("the code folder must be outside codebot's own folder")
    spec = body.get("spec_path")
    if body.get("spec_text"): spec = _spec_file(ctx, name, str(body["spec_text"]))
    if not spec or not os.path.isfile(os.path.expanduser(str(spec))): raise ApiError("choose a spec file or write one")
    overrides = body.get("overrides") or {}
    for k, v in overrides.items():
        if k not in ("DRAFTS_MINUTES", "DRAFTS_SEED", "DRAFTS_PATIENCE", "DRAFTS_MAX_ROUNDS", "DRAFTS_TEMPERATURE", "CODEBOT_MODEL"): raise ApiError(f"{k} cannot be set here")
        S.validate(k, v)
    r = ctx.run_cli("new", name, os.path.expanduser(str(spec)), code, timeout=120)
    if not r["ok"]: return r
    for k, v in overrides.items():
        if str(v).strip() != "": S.write(ctx.home, "project", k, v, name)
    if body.get("start"): r["start"] = ctx.run_cli("start", name, timeout=90)
    r["project"] = name; return r

def run_again(ctx, name, body):
    d = ctx.proj_dir(name); env = read_env(os.path.join(d, "project.env"))
    spec = os.path.join(d, "drafts", "SPEC.md") if os.path.isfile(os.path.join(d, "drafts", "SPEC.md")) else os.path.join(d, "SPEC.md")
    ov = {k: env[k] for k in ("DRAFTS_MINUTES", "DRAFTS_PATIENCE", "DRAFTS_MAX_ROUNDS", "DRAFTS_TEMPERATURE", "CODEBOT_MODEL") if env.get(k)}
    if body.get("seed") not in (None, ""): ov["DRAFTS_SEED"] = body["seed"]
    return new_project(ctx, {"name": body.get("name"), "spec_path": spec, "code_dir": env.get("WORKDIR"), "overrides": ov, "start": body.get("start")})

# ---------- benchmarks ----------
def bench_runs(ctx, q):
    out = []
    for sp in sorted(glob.glob(os.path.join(ctx.home, "bench", "results", "ladder", "*", "state.json")), reverse=True)[:200]:
        s = P.read_json(sp)
        if not s: continue
        rid = s.get("run_id") or os.path.basename(os.path.dirname(sp)); tr = s.get("trials") or []
        out.append({"run_id": rid, "status": s.get("status"), "created": s.get("created"), "mode": s.get("mode"), "suite": s.get("suite"), "levels": s.get("levels"),
                    "trials": len(tr), "config": {k: (s.get("config") or {}).get(k) for k in ("task_minutes", "model", "seed", "roles")},
                    "active": os.path.exists(os.path.join(os.path.dirname(sp), ".active")), "runner_alive": _runner_alive(ctx, rid)})
    return {"runs": out}

def _runner_alive(ctx, rid):
    """The benchmark's own test (runner.pid names a live benchmark runner): a run whose status says running without one has stalled."""
    from ..capbench import results as RS
    try: return RS.Store(ctx.home, rid).runner_alive()
    except Exception: return False

def bench_run(ctx, q, rid):
    if not RUN.match(rid): raise ApiError("bad run id")
    d = os.path.join(ctx.home, "bench", "results", "ladder", rid)
    s = P.read_json(os.path.join(d, "state.json"))
    if not s: raise ApiError("no such run", 404)
    trials = []
    for tj in sorted(glob.glob(os.path.join(d, "trials", "*", "trial.json"))):
        t = P.read_json(tj) or {}; ref = t.get("reference") or {}
        trials.append({"id": t.get("trial_id"), "outcome": t.get("outcome"), "phase": t.get("phase"), "benchmark": t.get("benchmark") or t.get("level"),
                       "passed": ref.get("passed"), "total": ref.get("total"), "by_requirement": ref.get("by_requirement"), "drafts": t.get("drafts"),
                       "timing": t.get("timing"), "started": t.get("started"), "finished": t.get("finished"), "progress": t.get("progress")})
    return {"state": s, "trials": trials, "report": P.read_text(os.path.join(d, "report.md"), 200000), "active": os.path.exists(os.path.join(d, ".active")),
            "runner_alive": _runner_alive(ctx, rid)}

def bench_levels(ctx, q):
    from ..capbench import manifests as M
    out = []
    for d in sorted(glob.glob(os.path.join(M.catalog_dir(ctx.home), "*", "manifest.json"))):
        try: m = M.load(ctx.home, os.path.basename(os.path.dirname(d)))
        except Exception: continue
        out.append({"id": m["_id"], "title": m.get("title"), "level": m.get("level"), "requirements": len(m.get("requirements") or []) if m.get("requirements") else None})
    return {"levels": out}

def bench_start(ctx, body):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    args = ["bench", "run"]; t = body.get("target", "")
    if re.fullmatch(r"level_(\d\d)", t): args += ["--level", str(int(t[6:]))]
    elif t in ("ladder", "growing"): args += ["--suite", t]
    elif re.fullmatch(r"[a-z][a-z0-9_]{1,40}", t): args += ["--bench", t]
    else: raise ApiError("choose a level, a catalog entry, the ladder or the growing project")
    for k, flag, pat in (("repeats", "--repeats", r"\d{1,2}"), ("task_minutes", "--task-minutes", r"\d{1,4}(\.\d+)?"), ("mode", "--mode", r"full|reference-plan|minimal|drafts"),
                         ("seed", "--seed", r"\d{1,7}"), ("model", "--model", MODEL.pattern), ("num_ctx", "--num-ctx", r"\d{4,6}"), ("name", "--name", r"[\w-]{1,40}")):
        v = str(body.get(k, "") or "").strip()
        if v:
            if not re.fullmatch(pat, v): raise ApiError(f"invalid {k}")
            args += [flag, v]
    if body.get("no_notify"): args.append("--no-notify")
    return ctx.run_cli(*args, timeout=120)

def bench_ctl(ctx, rid, body, what):
    if not RUN.match(rid): raise ApiError("bad run id")
    if what == "cancel" and body.get("confirm") is not True: raise ApiError("cancelling needs confirmation", 428)
    return ctx.run_cli("bench", what, rid, timeout=120)

# ---------- models ----------
def models(ctx, q):
    port = ctx.port(); c = ctx.cfg()
    lst = ctx.cache.get("tags", 20, lambda: sysinfo.ollama_models(port))
    if lst.get("ok"):
        running = _running_models(ctx)
        lst = {**lst, "models": [{**m, "protected": _delete_refusal(ctx, m["name"], c, running)} for m in lst["models"]]}
    return {"server": ctx.cache.get("ollama", 3, lambda: sysinfo.ollama(port)), "list": lst,
            "default": c.get("CODEBOT_MODEL", "codebot-model"), "num_ctx": c.get("NUM_CTX", "32768"), "keep_alive": c.get("KEEP_ALIVE", ""),
            "job": jobs.current(ctx.home), "actions": [a for a in actions.recent(ctx.home, 40) if a.get("label", "").startswith(("preflight", "import"))][:5]}

def _running_models(ctx):
    """{model base name: [projects running with it]} for projects whose loop is alive or paused."""
    out = {}
    for s in P.projects(ctx.home, include_bench=True):
        if s["state"] in ("running", "paused", "pause_requested", "cooling"):
            m = (read_env(os.path.join(ctx.home, "projects", s["name"], "project.env")).get("CODEBOT_MODEL") or "").split(":")[0]
            if m: out.setdefault(m, []).append(s["name"])
    return out

def _delete_refusal(ctx, name, c, running):
    """Why this model may not be deleted from the GUI, or None."""
    base = name.split(":")[0]
    if c.get("CODEBOT_MODEL", "codebot-model").split(":")[0] == base: return "this is codebot's default model; choose another default first"
    if base in ("codebot-model", "ornith"): return "codebot-model and ornith are what opencode and the shipped defaults use"
    if running.get(base): return f"in use by {', '.join(running[base])}"
    return None

def _ollama_bin(ctx): return os.path.join(ctx.home, "tools", "ollama", "bin", "ollama")

def _ollama_env(ctx): return {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{ctx.port()}", "OLLAMA_MODELS": os.path.join(ctx.home, "models")}

def model_unload(ctx, body):
    m = str(body.get("model", ""))
    if not MODEL.match(m): raise ApiError("bad model name")
    if jobs.current(ctx.home) and body.get("confirm") is not True: raise ApiError("a model job is running: unloading would force it to reload", 428)
    p = subprocess.run([_ollama_bin(ctx), "stop", m], capture_output=True, text=True, timeout=60, env=_ollama_env(ctx))
    ctx.cache.d.pop("ollama", None)
    return {"ok": p.returncode == 0, "out": (p.stdout + p.stderr).strip()}

def model_delete(ctx, body):
    m = str(body.get("model", ""))
    if not MODEL.match(m): raise ApiError("bad model name")
    if body.get("confirm") is not True: raise ApiError("deleting a model needs confirmation", 428)
    why = _delete_refusal(ctx, m, ctx.cfg(), _running_models(ctx))
    if why: raise ApiError(why)
    p = subprocess.run([_ollama_bin(ctx), "rm", m], capture_output=True, text=True, timeout=120, env=_ollama_env(ctx))
    ctx.cache.d.pop("tags", None)
    return {"ok": p.returncode == 0, "out": re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", p.stdout + p.stderr).strip()}

def model_default(ctx, body):
    m = str(body.get("model", ""))
    if not MODEL.match(m): raise ApiError("bad model name")
    if jobs.current(ctx.home) and body.get("confirm") is not True: raise ApiError("a model job is running; its next round would use the new model", 428)
    v = S.write(ctx.home, "machine", "CODEBOT_MODEL", m)
    if body.get("num_ctx"): S.write(ctx.home, "machine", "NUM_CTX", body["num_ctx"])
    return {"ok": True, "model": v}

def model_import(ctx, body):
    name, mf = str(body.get("name", "")), os.path.expanduser(str(body.get("modelfile", "")))
    if not MODEL.match(name): raise ApiError("bad model name")
    if not os.path.isfile(mf): raise ApiError("no such Modelfile")
    a = actions.start(ctx.home, f"import {name}", [_ollama_bin(ctx), "create", name, "-f", mf], env={"OLLAMA_HOST": f"127.0.0.1:{ctx.port()}", "OLLAMA_MODELS": os.path.join(ctx.home, "models")})
    return {"ok": True, "action": a}

def preflight(ctx, body):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    o = jobs.current(ctx.home)
    if o: raise ApiError(f"another model job is running: {jobs.describe(o)}", 409)
    return {"ok": True, "action": actions.start(ctx.home, "preflight", [ctx.cli, "bench", "--preflight"])}

# ---------- machine ----------
def machine(ctx, q):
    c = ctx.cfg()
    if q.get("refresh_disk") == "1" and not ctx.disk_busy:
        def run():
            ctx.disk_busy = True
            try: ctx.disk = sysinfo.disk(ctx.home)
            finally: ctx.disk_busy = False
        threading.Thread(target=run, daemon=True).start()
    return {"history": ctx.sampler.history(_int(q, "seconds", 3600)), "latest": ctx.sampler.latest(), "mem": sysinfo.meminfo(),
            "gpu": ctx.cache.get("gpu", 15, sysinfo.gpu), "disk": ctx.disk, "disk_busy": ctx.disk_busy,
            "sandbox": {"mode": c.get("SANDBOX", "none"), "memory": c.get("SANDBOX_MEMORY", "2g"), **ctx.cache.get("docker", 60, lambda: sysinfo.docker(c.get("SANDBOX_IMAGE", "ubuntu:24.04")))},
            "watchdog": ctx.cache.get("watchdog", 60, sysinfo.watchdog),
            "thermal": {"guard": c.get("THERMAL_GUARD", "0"), "max": c.get("THERMAL_MAX", "92"), "resume": c.get("THERMAL_RESUME", "84"), "max_wait": c.get("THERMAL_MAX_WAIT", "1800")},
            "actions": [a for a in actions.recent(ctx.home, 40) if a.get("label") in ("doctor",)][:3]}

def doctor(ctx, body): return {"ok": True, "action": actions.start(ctx.home, "doctor", [ctx.cli, "doctor"])}

def action_view(ctx, q, aid):
    s = actions.status(ctx.home, aid)
    if not s: raise ApiError("no such action", 404)
    text, off = actions.output(ctx.home, aid, max(0, _int(q, "offset", 0)))
    return {"action": s, "text": _redact(ctx, text), "offset": off}

def actions_list(ctx, q): return {"actions": actions.recent(ctx.home, 30)}

# ---------- settings, notifications, memory ----------
def settings_view(ctx, q):
    p = q.get("project") or None
    if p: ctx.proj_dir(p)
    return {"settings": S.view(ctx.home, p), "project": p}

def settings_write(ctx, body):
    key, scope, project = str(body.get("key", "")), str(body.get("scope", "")), body.get("project") or None
    if project: ctx.proj_dir(project)
    if key == "PHONE_CONTROL" and str(body.get("value")) == "1" and body.get("confirm") is not True:
        raise ApiError("phone control lets anyone who knows the topic pause, resume, stop and answer; confirm to turn it on", 428)
    if key == "NTFY_ENABLED" and str(body.get("value")) == "1" and body.get("confirm") is not True:
        raise ApiError("notifications send messages off this machine; confirm to turn them on", 428)
    try: v = S.write(ctx.home, scope, key, None if body.get("reset") else body.get("value"), project)
    except S.SettingError as e: raise ApiError(str(e))
    return {"ok": True, "value": S.mask(key, v), "settings": S.view(ctx.home, project)}

def notifications(ctx, q):
    eg = P.tail_jsonl(os.path.join(ctx.home, "logs", "egress.jsonl"), 16384)[-30:]
    return {"settings": [s for s in S.view(ctx.home) if s["group"] == "notifications"], "egress": eg}

def notify_test(ctx, body):
    if body.get("confirm") is not True: raise ApiError("a test sends one message off this machine; confirm", 428)
    return ctx.run_cli("notify-test", timeout=30)

def memory(ctx, q):
    d = os.path.join(ctx.home, "memory")
    st = P.read_json(os.path.join(d, "lessons.status.json")) or {}
    return {"expertise": P.read_text(os.path.join(d, "expertise.md")), "lessons": P.read_text(os.path.join(d, "lessons.md"), 200000),
            "status": {k: v for k, v in st.items() if k != "lessons"}, "lesson_stats": list((st.get("lessons") or {}).items())[:200]}

def memory_write(ctx, body):
    text = body.get("expertise")
    if not isinstance(text, str) or len(text) > 100000: raise ApiError("expertise is text up to 100 kB")
    p = os.path.join(ctx.home, "memory", "expertise.md"); tmp = p + ".gui.tmp"
    with open(tmp, "w") as f: f.write(text)
    os.replace(tmp, p); return {"ok": True}

def tidy(ctx, body): return {"ok": True, "action": actions.start(ctx.home, "tidy", [ctx.cli, "tidy"])}
def audit_probe(ctx, name, body): ctx.proj_dir(name); return {"ok": True, "action": actions.start(ctx.home, f"audit probe {name}", [ctx.cli, "audit", name, "--probe"])}
def bench_validate(ctx, body):
    lv = str(body.get("levels", "") or "")
    if lv and not re.fullmatch(r"[\d,\-]+|growing", lv): raise ApiError("levels like 1-3 or growing")
    return {"ok": True, "action": actions.start(ctx.home, "bench validate", [ctx.cli, "bench", "validate", *(["--levels", lv] if lv else [])])}

# ---------- modular pipeline (team graph) ----------
def pipeline_view(ctx, q, name):
    ctx.proj_dir(name)
    from ..config import Project
    from ..pipeline import runtime as R, mailbox, artifacts
    from ..pipeline.validate import ValidationError
    proj = Project(name, ctx.home)
    out = {"name": name, "workflow": read_env(os.path.join(ctx.proj_dir(name), "project.env")).get("WORKFLOW")}
    try:
        bundle, path = R.load_bundle(proj)
        out["bundle"] = {"project": bundle.get("project"), "team": bundle.get("team"), "workflow": bundle.get("workflow"),
                         "ui": bundle.get("ui"),
                         "agents": {k: {"id": v.get("id"), "name": v.get("name"), "role": v.get("role"), "goal": v.get("goal"),
                                        "memory": v.get("memory"), "sees": v.get("sees")} for k, v in (bundle.get("agents") or {}).items()}}
        out["path"] = path
    except ValidationError as e:
        out["error"] = "; ".join(e.errors)
    run = R.load_run(proj)
    out["run"] = run
    if run:
        out["messages"] = list(mailbox.iter_messages(R.pipeline_dir(proj)))[-40:]
        out["artifacts"] = artifacts.list_artifacts(R.pipeline_dir(proj))[-40:]
    return out

def pipeline_json(ctx, q, name):
    ctx.proj_dir(name)
    path = os.path.join(ctx.proj_dir(name), "pipeline", "project.json")
    if not os.path.isfile(path): raise ApiError("no pipeline/project.json", 404)
    with open(path, encoding="utf-8") as f: return {"path": path, "json": f.read()}

def pipeline_validate(ctx, body, name=None):
    from ..pipeline.validate import validate_project, ValidationError
    import json as _json
    raw = body.get("json")
    if raw is None and name:
        path = os.path.join(ctx.proj_dir(name), "pipeline", "project.json")
        with open(path, encoding="utf-8") as f: raw = f.read()
    try: doc = _json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as e: raise ApiError(f"bad JSON: {e}")
    try:
        validate_project(doc)
        return {"ok": True}
    except ValidationError as e: return {"ok": False, "errors": e.errors}

def pipeline_save(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    import json as _json
    from ..pipeline.validate import validate_project, ValidationError
    raw = body.get("json")
    try: doc = _json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as e: raise ApiError(f"bad JSON: {e}")
    try: validate_project(doc)
    except ValidationError as e: raise ApiError("; ".join(e.errors))
    d = os.path.join(ctx.proj_dir(name), "pipeline"); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "project.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: _json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return {"ok": True, "path": path}

def pipeline_design(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    return {"ok": True, "action": actions.start(ctx.home, f"pipeline design {name}", [ctx.cli, "pipeline", "design", name, "--home", ctx.home])}

def pipeline_step(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    return {"ok": True, "action": actions.start(ctx.home, f"pipeline step {name}", [ctx.cli, "pipeline", "step", name, "--home", ctx.home])}

def pipeline_run(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    # background via normal start so pause/stop/watchdog work
    return {"ok": True, "action": actions.start(ctx.home, f"pipeline start {name}", [ctx.cli, "start", name])}

def pipeline_cancel(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    from ..config import Project
    from ..pipeline import schedule
    return schedule.cancel(Project(name, ctx.home))

def pipeline_init(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    from ..config import Project
    from ..pipeline import design
    from ..pipeline.validate import ValidationError
    tmpl = body.get("template") or "solo"
    force = bool(body.get("force"))
    try:
        path, meta = design.init_project(Project(name, ctx.home), tmpl, force=force)
    except ValidationError as e:
        raise ApiError("; ".join(e.errors))
    return {"ok": True, "path": path, "meta": meta}

def pipeline_approve(ctx, body, name):
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    from ..config import Project
    from ..pipeline import schedule, runtime as R
    ev = schedule.approve(Project(name, ctx.home))
    return {"ok": True, "status": ev.get("status"), "event": ev.get("event"), "run": ev.get("run") or R.load_run(Project(name, ctx.home))}

def pipeline_templates(ctx, q, name=None):
    from ..pipeline import design
    design.ensure_templates(ctx.home)
    return {"templates": [{"id": k, "path": v} for k, v in design.TEMPLATES.items()]}

# ---------- a design, then a build ----------
def _shape(ctx):
    from .. import shape
    return shape

def _shape_call(ctx, fn):
    from .. import shape
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    try: return fn()
    except shape.ShapeError as e: raise ApiError(str(e), e.code)

def shape_begin(ctx, body):
    sh = _shape(ctx)
    return _shape_call(ctx, lambda: sh.begin(ctx.home, body.get("idea") or "", body.get("name") or None, body.get("security", 1)))

def shape_view(ctx, q, name):
    sh = _shape(ctx)
    try: view = sh.view(ctx.home, name)
    except sh.ShapeError as e: raise ApiError(str(e), e.code)
    from . import runner
    view["terminal"] = runner.describe(ctx.home, name)
    st = P.state(ctx.home, name)
    view["attention"] = project_attention(st, _acks(ctx))
    view["title"] = view.get("title") or st.get("title") or ((view.get("outline") or {}).get("title")) or name
    return view

def program_run(ctx, q, name):
    from . import runner
    ctx.proj_dir(name)
    try: since = int(q.get("since") or 0)
    except ValueError: since = 0
    info = runner.describe(ctx.home, name)
    info.update(runner.output(ctx.home, name, since))
    return info

def program_run_do(ctx, body, name):
    from . import runner
    if ctx.demo: raise ApiError("demo mode: actions are disabled", 409)
    ctx.proj_dir(name)
    action = body.get("action") or "start"
    try:
        if action == "start": return runner.start(ctx.home, name)
        if action == "input": return runner.send(ctx.home, name, body.get("text") or "")
        if action == "stop": return runner.stop(ctx.home, name)
    except FileNotFoundError as e: raise ApiError(str(e), 404)
    except RuntimeError as e: raise ApiError(str(e))
    raise ApiError("action is start, input, or stop")

def shape_answer(ctx, body, name):
    sh = _shape(ctx); return _shape_call(ctx, lambda: sh.answer(ctx.home, name, body.get("answers") or {}))

def shape_clarify(ctx, body, name):
    sh = _shape(ctx); return _shape_call(ctx, lambda: sh.clarify(ctx.home, name, body.get("text") or ""))

def shape_retry(ctx, body, name):
    sh = _shape(ctx); return _shape_call(ctx, lambda: sh.retry(ctx.home, name))

def shape_security(ctx, body, name):
    sh = _shape(ctx); return _shape_call(ctx, lambda: sh.set_security(ctx.home, name, body.get("security")))

def shape_build(ctx, body, name):
    sh = _shape(ctx)
    return _shape_call(ctx, lambda: sh.build(ctx.home, name, body.get("code_dir") or "", body.get("security"), body.get("overrides") or {}))

# ---------- routing ----------
GET = [
    (r"/api/status", status), (r"/api/projects", projects), (r"/api/project/([^/]+)", project), (r"/api/project/([^/]+)/rounds", rounds),
    (r"/api/project/([^/]+)/round/(\d+)", round_detail), (r"/api/project/([^/]+)/files", files), (r"/api/project/([^/]+)/file", file_view),
    (r"/api/project/([^/]+)/compare", compare), (r"/api/project/([^/]+)/delivered", delivered), (r"/api/project/([^/]+)/report", report),
    (r"/api/project/([^/]+)/audit", audit), (r"/api/project/([^/]+)/log", log),     (r"/api/project/([^/]+)/shape", shape_view), (r"/api/project/([^/]+)/run", program_run),
    (r"/api/project/([^/]+)/pipeline", pipeline_view), (r"/api/project/([^/]+)/pipeline/json", pipeline_json),
    (r"/api/inbox", inbox), (r"/api/bench/runs", bench_runs),
    (r"/api/bench/levels", bench_levels), (r"/api/bench/run/([^/]+)", bench_run), (r"/api/models", models), (r"/api/machine", machine),
    (r"/api/actions", actions_list), (r"/api/action/([^/]+)", action_view), (r"/api/settings", settings_view), (r"/api/notifications", notifications),
    (r"/api/memory", memory),
]
POST = [
    (r"/api/project/([^/]+)/(start|pause|pause-soft|resume|stop)", lambda c, b, n, w: act(c, n, b, w)), (r"/api/project/([^/]+)/answer", lambda c, b, n: answer(c, n, b)),
    (r"/api/project/([^/]+)/budget", lambda c, b, n: budget(c, n, b)), (r"/api/project/([^/]+)/run-again", lambda c, b, n: run_again(c, n, b)),
    (r"/api/project/([^/]+)/audit-probe", lambda c, b, n: audit_probe(c, n, b)), (r"/api/projects/new", new_project), (r"/api/shape/begin", shape_begin), (r"/api/check-spec", check_spec),
    (r"/api/project/([^/]+)/shape/answer", shape_answer), (r"/api/project/([^/]+)/shape/clarify", shape_clarify),
    (r"/api/project/([^/]+)/shape/retry", shape_retry), (r"/api/project/([^/]+)/shape/security", shape_security),
    (r"/api/project/([^/]+)/shape/build", shape_build), (r"/api/project/([^/]+)/run", program_run_do),
    (r"/api/project/([^/]+)/pipeline/validate", lambda c, b, n: pipeline_validate(c, b, n)),
    (r"/api/project/([^/]+)/pipeline/save", pipeline_save),
    (r"/api/project/([^/]+)/pipeline/design", pipeline_design),
    (r"/api/project/([^/]+)/pipeline/step", pipeline_step),
    (r"/api/project/([^/]+)/pipeline/run", pipeline_run),
    (r"/api/project/([^/]+)/pipeline/cancel", pipeline_cancel),
    (r"/api/project/([^/]+)/pipeline/init", pipeline_init),
    (r"/api/project/([^/]+)/pipeline/approve", pipeline_approve),
    (r"/api/pipeline/templates", pipeline_templates),
    (r"/api/project/([^/]+)/delete", project_delete),
    (r"/api/inbox/ack", inbox_ack), (r"/api/bench/start", bench_start), (r"/api/bench/validate", bench_validate),
    (r"/api/bench/run/([^/]+)/(resume|cancel)", lambda c, b, r, w: bench_ctl(c, r, b, w)), (r"/api/models/unload", model_unload), (r"/api/models/delete", model_delete),
    (r"/api/models/default", model_default), (r"/api/models/import", model_import), (r"/api/models/preflight", preflight), (r"/api/machine/doctor", doctor),
    (r"/api/settings", settings_write), (r"/api/notifications/test", notify_test), (r"/api/memory", memory_write), (r"/api/memory/tidy", tidy),
]

DEMO_POST = {"/api/check-spec"}                                         # the only POST that changes nothing

def dispatch(ctx, method, path, query, body):
    if ctx.demo and method != "GET" and path not in DEMO_POST: raise ApiError("demo mode: actions are disabled", 409)
    for pat, fn in (GET if method == "GET" else POST):
        m = re.fullmatch(pat, path)
        if m:
            args = [ctx, query if method == "GET" else body, *m.groups()]
            return fn(*args)
    raise ApiError("not found", 404)
