"""Run a shell command with a hard timeout that kills the WHOLE process tree (the shell and everything it started), not just the shell."""
import os, signal, subprocess

def kill_group(proc):
    try: os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError): pass
    try: proc.communicate(timeout=10)
    except Exception: pass

def run_group(cmd, timeout, **kw):
    """Like subprocess.run(shell=True, capture_output=True, text=True, timeout=...) but the command gets its own session and a timeout kills it all.
    Returns (returncode, stdout, stderr, timed_out)."""
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        kill_group(proc); return 124, "", "", True
