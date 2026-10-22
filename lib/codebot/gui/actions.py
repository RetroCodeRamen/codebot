"""Long actions the GUI starts (preflight, doctor, an audit with the network probe, a benchmark validation, a model import): one detached
process each, outliving the GUI, with its output in logs/gui/<id>.log and its description in logs/gui/<id>.json. A reopened GUI finds them again
by those files; a process counts as running only while its pid is alive and was started at the recorded time (pid reuse after a reboot)."""
import json, os, secrets, subprocess, time
from .. import projstate as P

def _dir(home):
    d = os.path.join(home, "logs", "gui"); os.makedirs(d, exist_ok=True); return d

def start(home, label, argv, env=None):
    aid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    d = _dir(home); log = os.path.join(d, f"{aid}.log")
    with open(log, "wb") as out:
        p = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=home, start_new_session=True,
                             env={**os.environ, **(env or {})})
    meta = {"id": aid, "label": label, "argv": argv, "pid": p.pid, "started": time.time(), "proc_start": P.proc_start(p.pid)}
    with open(os.path.join(d, f"{aid}.json"), "w") as f: json.dump(meta, f)
    _watch(d, aid, p)
    return meta

def _watch(d, aid, p):
    import threading
    def wait():
        rc = p.wait()
        path = os.path.join(d, f"{aid}.json"); meta = P.read_json(path) or {}
        meta.update(exit=rc, ended=time.time())
        tmp = path + ".tmp"
        with open(tmp, "w") as f: json.dump(meta, f)
        os.replace(tmp, path)
    threading.Thread(target=wait, daemon=True).start()

def status(home, aid):
    if not all(c.isalnum() or c == "-" for c in aid): return None
    d = _dir(home); meta = P.read_json(os.path.join(d, f"{aid}.json"))
    if not meta: return None
    running = False
    if "exit" not in meta and meta.get("pid"):
        ps = P.proc_start(meta["pid"])
        running = ps is not None and (meta.get("proc_start") is None or abs(ps - meta["proc_start"]) < 2)
    meta["running"] = running
    if "exit" not in meta and not running: meta["exit"] = None; meta["lost"] = True        # ended while no GUI was watching: its log is all we have
    return meta

def output(home, aid, offset=0, limit=256 << 10):
    if not all(c.isalnum() or c == "-" for c in aid): return "", offset
    path = os.path.join(_dir(home), f"{aid}.log")
    try:
        with open(path, "rb") as f: f.seek(offset); data = f.read(limit)
    except OSError: return "", offset
    return data.decode(errors="replace"), offset + len(data)

def recent(home, n=20):
    d = _dir(home); out = []
    for fn in sorted((f for f in os.listdir(d) if f.endswith(".json")), reverse=True)[:n]:
        s = status(home, fn[:-5])
        if s: out.append(s)
    return out
