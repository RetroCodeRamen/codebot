"""Pluggable modes (F11). A mode is a folder under modes/<name>/ with mode.json + prompts + worker rules + a memory file + verifier
helpers. `extends` inherits from another mode (prompts are base + `prompts_add`). Adding a mode needs no code change."""
import json, os, re, shutil

LISTS_CONCAT = {"deny", "ro", "env", "path_prepend", "helpers", "build_deps", "requires"}
REQUIRED = ("name", "engine")

class ModeError(Exception):
    pass

def _dir(home, name): return os.path.join(home, "modes", name)

def _merge(base, own, key=None):
    if isinstance(base, dict) and isinstance(own, dict):
        out = dict(base)
        for k, v in own.items(): out[k] = _merge(base[k], v, k) if k in base else v
        return out
    if isinstance(base, list) and isinstance(own, list) and key in LISTS_CONCAT: return base + [x for x in own if x not in base]
    return own

def load(home, name, _seen=()):
    """Merged mode dict plus `chain`: mode dirs from root-most base to this mode (used to resolve files)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""): raise ModeError(f"bad mode name {name!r}")
    if name in _seen: raise ModeError(f"mode inheritance loop: {' -> '.join(_seen)} -> {name}")
    path = os.path.join(_dir(home, name), "mode.json")
    if not os.path.isfile(path):
        avail = ", ".join(list_modes(home)) or "none"; raise ModeError(f"unknown mode '{name}' (available: {avail})")
    try:
        with open(path) as f: own = json.load(f)
    except ValueError as e: raise ModeError(f"modes/{name}/mode.json is not valid JSON: {e}")
    if "name" not in own: raise ModeError(f"modes/{name}/mode.json lacks 'name'")
    if own["name"] != name: raise ModeError(f"modes/{name}/mode.json says name '{own['name']}'")
    base = load(home, own["extends"], _seen + (name,)) if own.get("extends") else None
    merged = _merge({k: v for k, v in base.items() if k not in ("chain",)}, own) if base else dict(own)
    if "engine" not in merged: raise ModeError(f"modes/{name}/mode.json lacks 'engine' (and does not inherit one)")
    if merged["engine"] not in ("native", "opencode"): raise ModeError(f"modes/{name}: engine must be native or opencode")
    merged["chain"] = (base["chain"] if base else []) + [_dir(home, name)]
    merged["name"] = name; merged["dir"] = _dir(home, name)
    return merged

def list_modes(home):
    d = os.path.join(home, "modes")
    return sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "mode.json"))) if os.path.isdir(d) else []

def describe(home):
    out = []
    for n in list_modes(home):
        try: m = load(home, n); out.append({"name": n, "description": m.get("description", ""), "engine": m["engine"], "extends": m.get("extends")})
        except ModeError as e: out.append({"name": n, "description": f"BROKEN: {e}", "engine": "?", "extends": None})
    return out

def _read(path):
    try:
        with open(path) as f: return f.read()
    except OSError: return None

def prompt_template(home, name, kind, quick=False):
    """Unrendered prompt: the nearest base that defines it, then every mode's `prompts_add` (root to leaf)."""
    m = load(home, name); key = "plan_quick" if (kind == "plan" and quick) else kind
    text = None
    for d in reversed(m["chain"]):                      # leaf first: the most specific full prompt wins
        try:
            with open(os.path.join(d, "mode.json")) as f: own = json.load(f)
        except OSError: continue
        rel = (own.get("prompts") or {}).get(key) or (own.get("prompts") or {}).get(kind if quick and key == "plan_quick" else key)
        if rel and (t := _read(os.path.join(d, rel))) is not None: text = t; break
    if text is None: raise ModeError(f"mode '{name}' has no '{key}' prompt")
    for d in m["chain"]:
        try:
            with open(os.path.join(d, "mode.json")) as f: own = json.load(f)
        except OSError: continue
        add = (own.get("prompts_add") or {}).get(kind)
        if add and (t := _read(os.path.join(d, add))): text += "\n" + t
    return text

def _chain_files(m, key):
    out = []
    for d in m["chain"]:
        try:
            with open(os.path.join(d, "mode.json")) as f: own = json.load(f)
        except OSError: continue
        rel = own.get(key)
        if rel and (t := _read(os.path.join(d, rel))): out.append(t.strip())
    return out

def worker_rules(home, name): return "\n".join(_chain_files(load(home, name), "worker_rules"))
def notes(home, name):
    m = load(home, name); return "\n\n".join(_chain_files(m, "memory_file"))[: int(m.get("memory_cap", 2500))]

def expand(s):
    """Placeholders for machine-specific paths: {NODE_PREFIX} {JAVA_HOME} {GHIDRA_INSTALL_DIR} {HOME}. Unknown/missing -> ''."""
    node = shutil.which("node"); prefix = os.path.dirname(os.path.dirname(os.path.realpath(node))) if node else ""
    vals = {"NODE_PREFIX": prefix, "JAVA_HOME": os.environ.get("JAVA_HOME", ""), "GHIDRA_INSTALL_DIR": os.environ.get("GHIDRA_INSTALL_DIR", ""), "HOME": os.path.expanduser("~")}
    out = re.sub(r"\{(\w+)\}", lambda mm: vals.get(mm.group(1), ""), s)
    return out if out and not out.startswith(("/bin", "/:")) and "{" not in out else ""

def sandbox_cfg(proj):
    """Extra read-only mounts, env passthrough, PATH prefix and memory the mode needs inside the sandbox."""
    try: m = load(proj.home, proj.mode)
    except ModeError: return {"ro": [], "env": [], "path_prepend": [], "memory": None}
    s = m.get("sandbox") or {}
    ro = [p for p in (expand(x) for x in s.get("ro", [])) if p and os.path.exists(p)]
    return {"ro": ro, "env": list(s.get("env", [])), "path_prepend": [p for p in (expand(x) for x in s.get("path_prepend", [])) if p], "memory": s.get("memory")}

_SEG = re.compile(r"\|\||&&|\||;|\n")
def command_violation(proj, command):
    """None if the mode allows this shell command, else why not. Allowlist on the first word of every segment."""
    try: m = load(proj.home, proj.mode)
    except ModeError: return None
    c = m.get("commands") or {}
    for rx in c.get("deny", []):
        if re.search(rx, command): return f"forbidden in {m['name']} mode"
    allow = c.get("allow")
    if allow is None: return None
    if "`" in command or "$(" in command: return f"command substitution is not allowed in {m['name']} mode"
    for seg in _SEG.split(command):
        toks = seg.strip().lstrip("!( {").split()
        while toks and re.fullmatch(r"[A-Za-z_]\w*=\S*", toks[0]): toks.pop(0)
        if not toks: continue
        word = os.path.basename(toks[0])
        if word not in allow: return f"'{word}' is not allowed in {m['name']} mode (allowed: {', '.join(allow[:14])}...)"
    return None

def copy_verifiers(proj):
    """Verifier helper scripts of the mode chain become part of the project's frozen checks/."""
    if not os.path.isdir(os.path.join(proj.home, "modes")): return []     # a bare home (tests, old installs): nothing to copy
    m = load(proj.home, proj.mode); dst = os.path.join(proj.state, "checks"); copied = []
    for d in m["chain"]:
        src = os.path.join(d, "verifiers")
        if not os.path.isdir(src): continue
        for f in sorted(os.listdir(src)):
            target = os.path.join(dst, f)
            if not os.path.exists(target):
                os.makedirs(dst, exist_ok=True); shutil.copy2(os.path.join(src, f), target); copied.append(f)
    return copied
