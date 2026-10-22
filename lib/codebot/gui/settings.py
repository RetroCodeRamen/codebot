"""codebot's settings as layers, for the GUI: the shipped defaults (config/codebot.env, tracked by git, never written here), this machine
(config/local.env) and one project (projects/<p>/project.env); the project wins, then the machine, then the shipped file, then the code's own
default. bin/codebot `source`s these files with bash, so a value is only ever written as KEY="value" and never contains a character bash would
expand ($, `, ", \\, newline). Commands (WORKER_CMD, SPLIT_CMD) and secrets' values are never returned."""
import os, re, time
from ..config import read_env

RE_LINE = re.compile(r"^(\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
UNSAFE = re.compile(r"[\"`$\\\n\r]")

# key: (group, type, rule, applies, scopes, note)   type: int | float | bool | choice | text | secret | command | path
#   applies: "next round" (the engine reads settings before every round/step), "next run" (the bash loop reads it when it starts),
#            "next start" (the model server or the menu reads it when they start)
_A = ("machine", "project"); _M = ("machine",)
META = {
    "CODEBOT_MODEL": ("model", "text", None, "next round", _A, "the model codebot's own engine uses (opencode paths keep codebot-model)"),
    "NUM_CTX": ("model", "int", (2048, 262144), "next round", _A, "must fit the model and its memory; the preflight checks it"),
    "KEEP_ALIVE": ("model", "text", r"^\d+[smh]$", "next round", _A, "how long the model stays loaded between requests"),
    "OLLAMA_PORT": ("model", "int", (1024, 65535), "next run", _M, "the private model server's port"),
    "THINK": ("model", "choice", ("off", "on"), "next run", _A, "legacy opencode loop only"),
    "DRAFTS_MINUTES": ("drafts", "float", (1, 1440), "next round", _A, "the whole drafts run's budget (not the benchmark's per-task minutes)"),
    "DRAFTS_PATIENCE": ("drafts", "int", (1, 10), "next round", _A, ""),
    "DRAFTS_MAX_ROUNDS": ("drafts", "int", (1, 1000), "next round", _A, ""),
    "DRAFTS_NUM_PREDICT": ("drafts", "int", (500, 32000), "next round", _A, ""),
    "DRAFTS_TEMPERATURE": ("drafts", "float", (0, 2), "next round", _A, ""),
    "DRAFTS_SEED": ("drafts", "int", (0, 1000000), "next round", _A, "rounds use seed*1000+round; change it before starting"),
    "WORKER": ("tasks", "choice", ("native",), "next round", _A, ""), "CONTEXT_BUDGET": ("tasks", "int", (1000, 200000), "next round", _A, ""),
    "WORKER_MAX_TURNS": ("tasks", "int", (1, 500), "next round", _A, ""), "WORKER_CONTEXT_TOKENS": ("tasks", "int", (1000, 262144), "next round", _A, ""),
    "WORKER_TEMPERATURE": ("tasks", "float", (0, 2), "next round", _A, ""), "WORKER_MAX_TOKENS": ("tasks", "int", (100, 32000), "next round", _A, ""),
    "RUN_TIMEOUT": ("tasks", "int", (5, 7200), "next round", _A, ""), "LADDER_TRIES": ("tasks", "text", r"^\d+,\d+,\d+,\d+,\d+$", "next round", _A, ""),
    "SPLIT_MAX_DEPTH": ("tasks", "int", (0, 5), "next round", _A, ""), "REDERIVE_MAX": ("tasks", "int", (0, 10), "next round", _A, ""),
    "ALT_TEMPERATURE": ("tasks", "float", (0, 2), "next round", _A, ""), "ESCALATE_MODEL": ("tasks", "text", None, "next round", _A, ""),
    "TASK_MAX_ATTEMPTS": ("tasks", "int", (1, 100), "next round", _A, ""), "STUCK_SAME_FAILURE": ("tasks", "int", (1, 50), "next round", _A, ""),
    "STUCK_NO_PROGRESS": ("tasks", "int", (1, 50), "next round", _A, ""), "PINGPONG_WINDOW": ("tasks", "int", (2, 50), "next round", _A, ""),
    "PROJECT_MAX_MINUTES": ("tasks", "int", (0, 100000), "next round", _A, "0 = none"), "CHECK_BASELINE_GUARD": ("tasks", "bool", None, "next round", _A, ""),
    "REGRESSION_GATE": ("tasks", "bool", None, "next round", _A, ""), "PLAN_PER_ITEM": ("tasks", "bool", None, "next round", _A, ""),
    "PLAN_STYLE": ("tasks", "choice", ("grill", "quick"), "next run", _A, ""), "CHECK_TIMEOUT": ("tasks", "int", (10, 7200), "next round", _A, ""),
    "ALLOW_UNCHECKED": ("tasks", "bool", None, "next round", _A, ""),
    "PLANNER_MODEL": ("roles", "text", None, "next round", _A, ""), "SPLITTER_MODEL": ("roles", "text", None, "next round", _A, ""),
    "PLANNER_THINK": ("roles", "choice", ("off", "on", "low", "medium", "high"), "next round", _A, ""),
    "SPLITTER_THINK": ("roles", "choice", ("off", "on", "low", "medium", "high"), "next round", _A, ""),
    "WORKER_THINK": ("roles", "choice", ("off", "on", "low", "medium", "high"), "next round", _A, ""), "THINK_BUDGET": ("roles", "int", (0, 32000), "next round", _A, ""),
    "ITER_TIMEOUT": ("legacy loop", "int", (60, 86400), "next run", _A, ""), "MAX_ITERATIONS": ("legacy loop", "int", (1, 100000), "next run", _A, ""),
    "STALL_LIMIT": ("legacy loop", "int", (1, 100), "next run", _A, ""), "LESSONS_MAX": ("legacy loop", "int", (100, 1000000), "next run", _A, ""),
    "SANDBOX": ("sandbox", "choice", ("docker", "auto", "none"), "next round", _A, "none runs model-written code unsandboxed"),
    "SANDBOX_IMAGE": ("sandbox", "text", r"^[\w./:-]+$", "next round", _A, "must already be present (never pulled)"),
    "SANDBOX_MEMORY": ("sandbox", "text", r"^\d+[kmgKMG]?$", "next round", _A, ""), "SANDBOX_PIDS": ("sandbox", "int", (16, 100000), "next round", _A, ""),
    "SANDBOX_RO": ("sandbox", "path", None, "next round", _A, "extra read-only mounts, comma separated"), "SANDBOX_PASS_ENV": ("sandbox", "text", r"^[\w,]*$", "next round", _A, ""),
    "THERMAL_GUARD": ("heat", "bool", None, "next round", _A, ""), "THERMAL_MAX": ("heat", "int", (50, 105), "next round", _A, "pause model calls at or above (C)"),
    "THERMAL_RESUME": ("heat", "int", (40, 100), "next round", _A, "resume at or below (C)"), "THERMAL_MAX_WAIT": ("heat", "int", (0, 86400), "next round", _A, ""),
    "NTFY_ENABLED": ("notifications", "bool", None, "next run", _M, "1 = push notifications leave this machine"),
    "NTFY_SERVER": ("notifications", "text", r"^https?://[\w.:-]+/?$", "next run", _M, ""),
    "NTFY_TOPIC": ("notifications", "secret", r"^[\w-]{1,64}$", "next run", _M, "works like a password"), "NTFY_TOKEN": ("notifications", "secret", r"^[\w.-]{1,200}$", "next run", _M, ""),
    "PHONE_CONTROL": ("notifications", "bool", None, "next run", _M, "1 = anyone with the topic can pause, resume, stop and answer"),
    "NOTIFY_EACH_TASK": ("notifications", "bool", None, "next run", _A, ""), "IDLE_POLL": ("notifications", "int", (5, 3600), "next run", _A, ""),
    "WORKER_CMD": ("expert", "command", None, "next round", (), "a command: edit the file by hand"), "SPLIT_CMD": ("expert", "command", None, "next round", (), "a command: edit the file by hand"),
    "SPLITTER": ("expert", "choice", ("model", "mechanical"), "next round", _A, ""),
}
# keys codebot itself writes into project.env: shown, never edited here
STRUCTURAL = {"WORKDIR", "RUNDIR", "WORKFLOW", "MODE", "MEMORY_DIR", "TARGETS", "BENCH", "CAPBENCH", "SANDBOX_PATH", "SANDBOX_SETENV"}
CODE_DEFAULTS = {"DRAFTS_MINUTES": "60", "DRAFTS_PATIENCE": "1", "DRAFTS_MAX_ROUNDS": "60", "DRAFTS_NUM_PREDICT": "6000", "DRAFTS_TEMPERATURE": "0.2",
                 "DRAFTS_SEED": "1", "NUM_CTX": "32768", "SANDBOX": "none", "THERMAL_GUARD": "0", "NTFY_ENABLED": "1", "PHONE_CONTROL": "1"}

class SettingError(ValueError): pass

# bin/codebot `source`s these files, so a variable bash or the loader acts on must never be written, whatever the shipped file holds
NEVER = re.compile(r"^(PATH|IFS|ENV|BASH_ENV|BASH_\w+|SHELLOPTS|BASHOPTS|PS4|PROMPT_COMMAND|CDPATH|GLOBIGNORE|HOME|SHELL|LD_\w+|PYTHON\w*|CODEBOT_HOME|OLLAMA_BIN|OPENCODE_BIN)$")

def known(home):
    """The keys the GUI may write: the ones described here and the ones config/codebot.env sets or lists (commented out)."""
    return (set(META) | set(read_env(files(home)["shipped"])) | set(shipped_comments(home))) - {k for k in META if META[k][1] == "command"}

def files(home, project=None):
    f = {"shipped": os.path.join(home, "config", "codebot.env"), "machine": os.path.join(home, "config", "local.env")}
    if project: f["project"] = os.path.join(home, "projects", project, "project.env")
    return f

def shipped_comments(home):
    """{key: comment} from config/codebot.env, including the commented-out optional keys (#KEY=value  # what it does)."""
    out = {}
    try:
        with open(files(home)["shipped"]) as f: lines = f.read().splitlines()
    except OSError: return out
    for ln in lines:
        m = re.match(r"^#?([A-Z_][A-Z0-9_]*)=\S*(?:\s+#\s*(.*))?$", ln.strip())
        if m and m.group(2): out.setdefault(m.group(1), m.group(2).strip())
    return out

def mask(key, value):
    if value in (None, ""): return value
    t = (META.get(key) or ("", "text"))[1]
    if t == "secret": return "set (hidden)"
    if t == "command": return "set (a command, not shown)"
    return value

def view(home, project=None):
    """[{key, group, type, rule, applies, scopes, note, effective, source, layers: {shipped, machine, project}}] for every known or set key."""
    fs = files(home, project); layers = {k: read_env(v) for k, v in fs.items()}; notes = shipped_comments(home)
    keys = sorted(set(META) | set(layers["shipped"]) | set(layers["machine"]) | (set(layers.get("project", {})) - STRUCTURAL))
    out = []
    for k in keys:
        g, t, rule, applies, scopes, note = META.get(k, ("other", "text", None, "next run", _A, ""))
        lv = {s: layers[s].get(k) for s in layers}
        src = next((s for s in ("project", "machine", "shipped") if lv.get(s) not in (None,)), None)
        eff = lv[src] if src else CODE_DEFAULTS.get(k)
        out.append({"key": k, "group": g, "type": t, "rule": list(rule) if isinstance(rule, tuple) else rule, "applies": applies, "scopes": list(scopes),
                    "note": note or notes.get(k, ""), "effective": mask(k, eff), "source": src or ("code default" if k in CODE_DEFAULTS else "unset"),
                    "layers": {s: mask(k, v) for s, v in lv.items()}})
    if project:
        out += [{"key": k, "group": "project (written by codebot)", "type": "structural", "rule": None, "applies": "", "scopes": [], "note": "",
                 "effective": layers["project"][k], "source": "project", "layers": {"project": layers["project"][k]}} for k in sorted(STRUCTURAL & set(layers["project"]))]
    return out

def validate(key, value):
    g, t, rule, applies, scopes, note = META.get(key, ("other", "text", None, "next run", _A, ""))
    v = str(value).strip()
    if UNSAFE.search(v): raise SettingError(f"{key}: quotes, $, backticks, backslashes and line breaks are not allowed (bash reads this file)")
    if t in ("command", "structural"): raise SettingError(f"{key} is not edited from the GUI")
    if t == "int":
        try: n = int(v)
        except ValueError: raise SettingError(f"{key} must be a whole number")
        if rule and not (rule[0] <= n <= rule[1]): raise SettingError(f"{key} must be between {rule[0]} and {rule[1]}")
    elif t == "float":
        try: x = float(v)
        except ValueError: raise SettingError(f"{key} must be a number")
        if rule and not (rule[0] <= x <= rule[1]): raise SettingError(f"{key} must be between {rule[0]} and {rule[1]}")
        v = f"{x:g}"
    elif t == "bool":
        if v not in ("0", "1"): raise SettingError(f"{key} must be 0 or 1")
    elif t == "choice":
        if rule and v not in rule: raise SettingError(f"{key} must be one of {', '.join(rule)}")
    elif rule and v and not re.match(rule, v): raise SettingError(f"{key} has an invalid format")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key): raise SettingError("invalid key")
    return v

def _rewrite(path, key, value):
    """Set (value) or remove (None) KEY in an env file, keeping every other line and comment; atomic; the file's mode is kept (0600 stays)."""
    try:
        with open(path) as f: lines = f.read().split("\n")
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError: lines, mode = [], 0o600
    out, done = [], False
    for ln in lines:
        m = RE_LINE.match(ln)
        if m and m.group(2) == key and not ln.lstrip().startswith("#"):
            if value is None or done: continue                       # remove, or a later duplicate of a line already replaced
            rest = m.group(3); c = re.search(r"\s+#.*$", rest) if not rest.startswith(('"', "'")) else re.search(r"(?<=[\"'])\s+#.*$", rest)
            out.append(f'{m.group(1)}{key}="{value}"' + (c.group(0) if c else "")); done = True
        else: out.append(ln)
    if value is not None and not done:
        while out and out[-1] == "": out.pop()
        out.append(f'{key}="{value}"'); out.append("")
    tmp = f"{path}.gui.{os.getpid()}.tmp"
    with open(tmp, "w") as f: f.write("\n".join(out))
    os.chmod(tmp, mode); os.replace(tmp, path)

def write(home, scope, key, value, project=None):
    if scope not in ("machine", "project"): raise SettingError("settings are written for this machine or for one project")
    if scope == "project" and not project: raise SettingError("which project?")
    if key in STRUCTURAL: raise SettingError(f"{key} is written by codebot itself")
    if NEVER.match(key) or key not in known(home): raise SettingError(f"{key} is not a codebot setting")
    meta = META.get(key)
    if meta and scope not in meta[4]: raise SettingError(f"{key} can only be set for {' or '.join(meta[4]) or 'nobody here'}")
    v = None if value is None else validate(key, value)
    path = files(home, project)[scope]
    if scope == "project" and not os.path.isdir(os.path.dirname(path)): raise SettingError("unknown project")
    _rewrite(path, key, v); return v
