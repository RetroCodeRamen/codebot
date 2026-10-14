"""Command execution for the worker and for acceptance checks. Policy (denylist) + a Docker sandbox: --network none, read-only
root, only the project dir writable, capabilities dropped, memory/pid limits. Uses a base image that is already local
(ubuntu:24.04 by default, same distro as the host) with the host's /usr mounted read-only over it, so nothing is created in Docker."""
import os, re, shutil, subprocess, uuid
from . import audit

DENY = [
    (r"\bsudo\b|\bsu\s+-?\w*\b|\bdoas\b", "privilege escalation is not available"),
    (r"\b(curl|wget|ssh|scp|sftp|nc|ncat|netcat|telnet|ftp|rsync)\b", "this environment is offline: network tools are disabled"),
    (r"\bgit\s+(push|pull|fetch|clone|remote|config|reset|checkout|switch|clean|stash|rebase|commit|tag|branch|merge|worktree|gc|update-ref)\b",
     "git history is managed by the harness (read-only git commands are fine: status, diff, log, show, ls-files, grep)"),
    (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME)(\s|$|/\*)", "refusing a destructive delete outside the project"),
    (r"\b(mkfs|fdisk|parted|mount|umount|shutdown|reboot|poweroff)\b|\bdd\s+if=", "system administration commands are disabled"),
    (r"\b(pip3?|npm|pnpm|yarn|cargo|gem|apt|apt-get|brew)\s+(install|add|i|update|upgrade)\b", "installing packages needs the network, which is disabled"),
]

class Denied(Exception):
    pass

def check_command(proj, command):
    for rx, why in DENY:
        if re.search(rx, command): raise Denied(why)
    if proj.state and proj.state in command: raise Denied("the harness owns its state directory; the code directory is yours")
    if re.search(r"checks(\.lock|/\.frozen)|tasks\.json", command): raise Denied("those files are locked by the harness")

_MODE = {}
def mode(proj):
    """{"mode": "docker"|"none", "image":..., "reason":...}. SANDBOX=docker|none|auto (code default none; shipped config says auto)."""
    want = proj.get("SANDBOX", "none"); image = proj.get("SANDBOX_IMAGE", "ubuntu:24.04")
    key = (want, image)
    if key in _MODE: return _MODE[key]
    if want == "none": res = {"mode": "none", "image": None, "reason": "SANDBOX=none"}
    elif not shutil.which("docker"): res = {"mode": "none", "image": None, "reason": "docker not installed"}
    else:
        r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
        if r.returncode: res = {"mode": "none", "image": None, "reason": f"image {image} is not available locally and the sandbox never pulls (no network)"}
        else: res = {"mode": "docker", "image": image, "reason": "ok"}
    if want == "docker" and res["mode"] != "docker": raise RuntimeError(f"SANDBOX=docker but unavailable: {res['reason']}")
    _MODE[key] = res; return res

def _docker_argv(proj, name, cwd, env, rw, ro, image):
    mem, pids = proj.get("SANDBOX_MEMORY", "2g"), proj.get("SANDBOX_PIDS", "512")
    a = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
         "--pids-limit", pids, "--memory", mem, "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,exec,size=512m",
         "-v", "/usr:/usr:ro", "-v", "/etc/passwd:/etc/passwd:ro", "-v", "/etc/group:/etc/group:ro", "-w", cwd]
    for p in rw: a += ["-v", f"{p}:{p}"]
    for p in [*ro, *[x for x in proj.get("SANDBOX_RO", "").split(",") if x.strip()]]:
        if os.path.exists(p): a += ["-v", f"{p}:{p}:ro"]
    for k, v in env.items(): a += ["-e", f"{k}={v}"]
    return a + [image]

BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "TERM": "dumb", "PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "GIT_TERMINAL_PROMPT": "0"}

def execute(proj, argv, *, timeout, env=None, rw=None, ro=None, cwd=None):
    """Run argv (a list) sandboxed when available. Returns (rc, combined_output, sandbox_label)."""
    m = mode(proj); cwd = cwd or proj.rundir; env = {**BASE_ENV, **(env or {})}
    for k in proj.get("SANDBOX_PASS_ENV", "JAVA_HOME,GHIDRA_INSTALL_DIR,VIRTUAL_ENV").split(","):
        if k and os.environ.get(k): env.setdefault(k, os.environ[k])
    if m["mode"] == "docker":
        name = "cb-" + uuid.uuid4().hex[:10]; label = "docker:net=none:ro-root"
        full = _docker_argv(proj, name, cwd, env, rw or [proj.rundir], ro or [], m["image"]) + list(argv)
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            return r.returncode, (r.stdout or "") + (r.stderr or ""), label
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True)
            raw = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return 124, raw + f"\n[timed out after {timeout}s and was killed]", label
    full_env = {**env, "HOME": proj.rundir}
    try:
        r = subprocess.run(list(argv), cwd=cwd, env=full_env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), "none"
    except subprocess.TimeoutExpired as e:
        raw = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, raw + f"\n[timed out after {timeout}s and was killed]", "none"

def run(proj, command, timeout=120, max_out=3000, task=None):
    """Run a model-issued shell command in the code dir (policy-checked, sandboxed, audited). Returns (exit_code, output_tail)."""
    try: check_command(proj, command)
    except Denied as e:
        audit.log(proj, "command_denied", command=command[:500], reason=str(e), task=task); raise
    import time; t0 = time.time()
    rc, out, label = execute(proj, ["bash", "-c", command], timeout=timeout)
    audit.log(proj, "command", command=command[:500], rc=rc, secs=round(time.time() - t0, 1), sandbox=label, task=task, out_sha=__import__("hashlib").sha1(out.encode()).hexdigest()[:10])
    if len(out) > max_out: out = f"[...{len(out) - max_out} chars cut...]\n" + out[-max_out:]
    return rc, out

def probe(proj):
    """Prove the sandbox has no network: try to reach the internet from inside it."""
    code = ("import socket,sys\nfor h in ('1.1.1.1','8.8.8.8'):\n  try:\n    s=socket.create_connection((h,53),3); print('REACHED',h); sys.exit(0)\n  except OSError as e: pass\n"
            "try: socket.gethostbyname('example.com'); print('DNS-RESOLVED')\nexcept OSError: pass\nprint('BLOCKED')")
    m = mode(proj)
    if m["mode"] != "docker": return {"blocked": None, "detail": f"no sandbox active ({m['reason']})", "sandbox": "none"}
    rc, out, label = execute(proj, ["python3", "-c", code], timeout=30)
    return {"blocked": out.strip().endswith("BLOCKED") and "REACHED" not in out and "DNS-RESOLVED" not in out, "detail": out.strip()[-80:], "sandbox": label}
