"""Thermal guard: pause model calls while the CPU package is too hot, resume when it has cooled. The model is the heat source,
so waiting between requests is what actually lets the machine recover."""
import glob, os, re, subprocess, time
from .config import append_jsonl, now

def read_temp():
    """CPU package temperature in C, or None."""
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(z + "/type") as f: typ = f.read().strip()
            if typ in ("x86_pkg_temp", "coretemp", "TCPU", "cpu-thermal"):
                with open(z + "/temp") as f: return int(f.read().strip()) / 1000.0
        except (OSError, ValueError): continue
    try:
        out = subprocess.run(["sensors"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"Package id 0:\s*\+?([\d.]+)", out)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError): return None

def wait(proj, sleep=time.sleep, reader=read_temp):
    """Block while hot. Returns seconds waited (0 normally). Gives up after THERMAL_MAX_WAIT seconds so a broken sensor cannot hang a run."""
    if str(proj.get("THERMAL_GUARD", "0")) in ("0", "", "false", "no"): return 0
    hot, cool, cap = proj.get("THERMAL_MAX", 92, float), proj.get("THERMAL_RESUME", 84, float), proj.get("THERMAL_MAX_WAIT", 1800, int)
    t = reader()
    if t is None or t < hot: return 0
    waited = 0; append_jsonl(proj.p("thermal.jsonl"), {"ts": now(), "event": "pause", "temp": t, "task": os.environ.get("CODEBOT_TASK_ID")})
    while waited < cap:
        sleep(20); waited += 20; t = reader()
        if t is None or t <= cool: break
    append_jsonl(proj.p("thermal.jsonl"), {"ts": now(), "event": "resume", "temp": t, "waited": waited, "task": os.environ.get("CODEBOT_TASK_ID")})
    return waited
