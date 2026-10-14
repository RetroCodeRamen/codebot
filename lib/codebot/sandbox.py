"""Command execution for the worker. H: policy + limits. J adds bubblewrap (no network, read-only system) around this."""
import os, re, subprocess

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

def run(proj, command, timeout=120, max_out=3000):
    """Run a shell command in the code dir. Returns (exit_code, output_tail)."""
    check_command(proj, command)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": proj.rundir, "LANG": "C.UTF-8", "TERM": "dumb",
           "PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "GIT_TERMINAL_PROMPT": "0"}
    for k in ("JAVA_HOME", "GHIDRA_INSTALL_DIR", "VIRTUAL_ENV"):
        if os.environ.get(k): env[k] = os.environ[k]
    try:
        r = subprocess.run(["bash", "-c", command], cwd=proj.rundir, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired as e:
        raw = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out, rc = raw + f"\n[timed out after {timeout}s and was killed]", 124
    if len(out) > max_out: out = f"[...{len(out) - max_out} chars cut...]\n" + out[-max_out:]
    return rc, out
