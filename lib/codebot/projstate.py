"""How a project's state reads, for every interface (the GUI, `codebot status --json`, the terminal menu). Read-only and tolerant: a missing,
half-written or unreadable file is reported as unknown, never raised. The run semantics are bin/codebot's: a loop is alive only when loop.pid
names a live process whose command line is a codebot `__loop` (pid reuse after a reboot), PAUSE asks the loop to wait (now: the step is killed;
--soft: after the current step), .active means "should be running" (the watchdog restarts it), and drafts/state.json `finished` is final."""
import json, os, re, time
from .config import read_env

STATES = ("running", "cooling", "pause_requested", "paused", "stopped", "finished", "crashed", "not_started", "shaping")
LOOP_MARK = "__loop"

# ---------- tolerant readers ----------
def read_text(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f: return f.read(limit) if limit else f.read()
    except OSError: return None

def read_json(path):
    t = read_text(path)
    if t is None: return None
    try: return json.loads(t)
    except ValueError: return None                      # half-written: the writer replaces it atomically, so the next read is whole

def read_jsonl(path, offset=0, limit_bytes=8 << 20):
    """(rows, next offset) from byte `offset`: complete lines only (a line still being written is left for the next call), bad lines skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(offset); data = f.read(limit_bytes)
    except OSError: return [], offset
    end = data.rfind(b"\n")
    if end < 0: return [], offset
    rows = []
    for line in data[:end].split(b"\n"):
        try: rows.append(json.loads(line))
        except ValueError: continue
    return rows, offset + end + 1

def tail_jsonl(path, nbytes=8192):
    """The complete records in the last `nbytes` of a JSONL file (a record cut by the window start is skipped)."""
    try: size = os.path.getsize(path)
    except OSError: return []
    return read_jsonl(path, max(0, size - nbytes))[0]

def mtime(path):
    try: return os.path.getmtime(path)
    except OSError: return None

# ---------- processes ----------
def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f: return f.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError: return None

def boot_time():
    t = read_text("/proc/stat") or ""
    m = re.search(r"^btime (\d+)", t, re.M)
    return int(m.group(1)) if m else None

def proc_start(pid):
    """When a process started (unix time), from /proc/<pid>/stat, or None."""
    st = read_text(f"/proc/{pid}/stat")
    if not st: return None
    try: ticks = int(st.rsplit(")", 1)[1].split()[19])
    except (IndexError, ValueError): return None
    bt = boot_time()
    return bt + ticks / os.sysconf("SC_CLK_TCK") if bt else None

def pid_of(path):
    t = read_text(path, 64)
    try: return int(t.strip()) if t else None
    except ValueError: return None

def loop_alive(state_dir):
    """(pid, alive): alive only if loop.pid names a running codebot loop (the same test as bin/codebot's alive())."""
    pid = pid_of(os.path.join(state_dir, "loop.pid"))
    if not pid: return None, False
    cl = _cmdline(pid)
    return pid, bool(cl and LOOP_MARK in cl)

def step_alive(state_dir):
    """(pid, started) of the step or iteration the loop is running now (iter.pid), or (None, None)."""
    pid = pid_of(os.path.join(state_dir, "iter.pid"))
    if not pid or _cmdline(pid) is None: return None, None
    return pid, proc_start(pid)

# ---------- one project ----------
def project_env(state_dir):
    return read_env(os.path.join(state_dir, "project.env"))

def workflow(env):
    w = env.get("WORKFLOW") or ""
    if w in ("drafts", "shape"): return w
    return "tasks"

def shape_info(state_dir):
    """The design conversation, if this project has one: phase, whether a model call is in progress, the plan's title."""
    st = read_json(os.path.join(state_dir, "shape.json"))
    if not isinstance(st, dict): return None
    return {"phase": st.get("phase") or "describe", "busy": bool(st.get("busy")), "title": ((st.get("outline") or {}).get("title") or ""),
            "notice": st.get("notice") or ""}

def thermal(state_dir):
    """The last thermal-guard event of the project: {"cooling": bool, "temp", "since"} (cooling = paused for heat and not resumed yet)."""
    rows = tail_jsonl(os.path.join(state_dir, "thermal.jsonl"), 4096)
    last = rows[-1] if rows else None
    if not last: return {"cooling": False}
    return {"cooling": last.get("event") == "pause", "temp": last.get("temp"), "since": last.get("ts"), "event": last.get("event")}

def drafts_info(state_dir, env):
    st = read_json(os.path.join(state_dir, "drafts", "state.json"))
    if not st: return {"started": False}
    b, settings = st.get("best") or None, st.get("settings") or {}
    try: budget = float(env.get("DRAFTS_MINUTES")) if env.get("DRAFTS_MINUTES") else None
    except ValueError: budget = None
    return {"started": True, "round": st.get("round", 0), "draft": st.get("draft", 0), "fresh_next": st.get("fresh"),
            "best": {"round": b.get("round"), "draft": b.get("draft"), "examples": b.get("examples"), "declared": b.get("declared", 0),
                     "commit": b.get("commit"), "files": b.get("files")} if b else None,
            "examples_total": st.get("examples_total", 0), "declared_total": st.get("declared_total", 0),
            "used_minutes": round((st.get("used_s") or 0) / 60, 1), "budget_minutes": settings.get("minutes"), "budget_configured": budget,
            "finished": st.get("finished"), "language": st.get("language"), "names": st.get("names") or [], "uncovered": st.get("uncovered") or [],
            "model_errors": st.get("model_errors", 0), "settings": settings, "updated": mtime(os.path.join(state_dir, "drafts", "state.json"))}

def tasks_info(state_dir):
    t = read_json(os.path.join(state_dir, "tasks.json"))
    if not t: return {"frozen": False, "plan": os.path.exists(os.path.join(state_dir, "PLAN.md"))}
    counts = {}
    for x in t.get("tasks", []): counts[x.get("status", "?")] = counts.get(x.get("status", "?"), 0) + 1
    blocked = [{"id": x.get("id"), "title": x.get("title"), "question": x.get("blocked_question")} for x in t.get("tasks", []) if x.get("status") == "blocked"]
    return {"frozen": True, "counts": counts, "total": len(t.get("tasks", [])), "blocked": blocked}

def state(home, name):
    """Everything an interface needs to show one project; never raises for a project folder that exists."""
    S = os.path.join(home, "projects", name)
    env = project_env(S); wf = workflow(env)
    pid, alive = loop_alive(S); spid, sstart = step_alive(S) if alive else (None, None)
    pause = os.path.exists(os.path.join(S, "PAUSE")); active = os.path.exists(os.path.join(S, ".active"))
    th = thermal(S); status_txt = (read_text(os.path.join(S, "STATUS"), 400) or "").strip()
    if wf == "drafts": info = drafts_info(S, env)
    elif wf == "shape": info = shape_info(S) or {"phase": "describe", "busy": False, "title": "", "notice": ""}
    else: info = tasks_info(S)
    finished = bool(info.get("finished")) if wf == "drafts" else False
    if wf == "shape": s = "shaping"
    elif alive:
        if pause: s = "pause_requested" if spid else "paused"
        elif spid and th.get("cooling"): s = "cooling"
        else: s = "running"
    elif finished: s = "finished"
    elif active: s = "crashed"                          # should be running (.active) but no loop: the watchdog restarts it within 5 min
    elif wf == "drafts" and not info.get("started"): s = "not_started"
    elif wf != "drafts" and not os.path.exists(os.path.join(S, "started.at")): s = "not_started"
    else: s = "stopped"
    blocked = (wf != "drafts" and bool(info.get("blocked"))) or status_txt.startswith("BLOCKED")
    return {"name": name, "workflow": wf, "mode": env.get("MODE", "code"), "workdir": env.get("WORKDIR", ""), "rundir": env.get("RUNDIR") or env.get("WORKDIR", ""),
            "benchmark": name.startswith("cap-"), "state": s, "loop_pid": pid if alive else None, "stale_pid": bool(pid) and not alive,
            "step": {"pid": spid, "started": sstart} if spid else None, "pause_flag": pause, "active_flag": active, "thermal": th,
            "status": status_txt, "blocked": blocked, "started_at": (read_text(os.path.join(S, "started.at"), 32) or "").strip() or None,
            {"drafts": "drafts", "shape": "shape"}.get(wf, "tasks"): info, "updated": max([m for m in (mtime(os.path.join(S, f)) for f in
                ("loop.log", "audit.jsonl", "drafts/state.json", "drafts/rounds.jsonl", "shape.json", "tasks.json", "STATUS")) if m] or [0]) or None}

def projects(home, include_bench=False):
    out = []
    root = os.path.join(home, "projects")
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not os.path.isfile(os.path.join(root, n, "project.env")): continue
        if n.startswith("cap-") and not include_bench: continue
        out.append(state(home, n))
    return out

# ---------- drafts details ----------
def rounds(home, name):
    rows, _ = read_jsonl(os.path.join(home, "projects", name, "drafts", "rounds.jsonl"))
    return rows

def round_dir(home, name, n):
    return os.path.join(home, "projects", name, "drafts", "rounds", f"{int(n):02d}")

FAILING = re.compile(r"^failing assertions: ([\d, ]+)$", re.M)

def example_results(home, name, feedback):
    """[(index, example, passed, message)] for the spec's runnable examples, from checks.json and a version's feedback ("failing assertions:
    7, 8, 9" plus one FAIL line per failure, in order). passed is None when the feedback cannot say (the version did not build)."""
    checks = (read_json(os.path.join(home, "projects", name, "drafts", "checks", "checks.json")) or {}).get("asserts") or []
    fb = feedback or ""
    if not checks: return []
    first = fb.split("\n", 1)[0]
    if fb.startswith(("The code does not compile", "These files are missing", "Your last reply did not contain")) or (first.startswith("`") and first.endswith("failed:")):
        return [(i, a, None, "not run: this version did not build") for i, a in enumerate(checks, 1)]
    m = FAILING.search(fb)
    failing = [int(x) for x in m.group(1).replace(" ", "").split(",") if x] if m else []
    msgs = [l[6:] for l in fb.splitlines() if l.startswith("FAIL: ")]
    by = dict(zip(failing, msgs)) if len(msgs) == len(failing) else {}
    return [(i, a, i not in failing, by.get(i, "failed" if i in failing else "")) for i, a in enumerate(checks, 1)]

def phase(home, name, st=None):
    """What a running drafts round is doing now: cooling (thermal guard), generating (the model call), judging (build and examples), or None."""
    st = st or state(home, name)
    if st["state"] not in ("running", "cooling", "pause_requested") or st["workflow"] != "drafts": return None
    if st["thermal"].get("cooling"): return {"phase": "cooling", "since": st["thermal"].get("since"), "temp": st["thermal"].get("temp")}
    nxt = (st["drafts"].get("round") or 0) + 1
    rd = round_dir(home, name, nxt)
    if os.path.exists(os.path.join(rd, "reply.md")):
        return {"phase": "judging", "round": nxt, "since": mtime(os.path.join(rd, "reply.md"))}
    return {"phase": "generating", "round": nxt, "since": (st.get("step") or {}).get("started")}
