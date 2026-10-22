"""Machine and model-server readings for the GUI: cheap, cached, each with the time it was taken so the page can label stale or missing data.
A sampler thread keeps a short history of temperature, memory and swap traffic while the GUI runs (one sysfs read and two /proc reads every 5 s);
the GPU (nvidia-smi), Docker and systemd are asked at most every 15-60 s, and disk usage only when the Machine screen asks for it."""
import collections, json, os, shutil, subprocess, threading, time, urllib.request
from .. import thermal

def _now(): return time.time()

class Cache:
    def __init__(self): self.d, self.lock = {}, threading.Lock()
    def get(self, key, ttl, fn):
        with self.lock:
            hit = self.d.get(key)
            if hit and _now() - hit[0] < ttl: return hit[1]
        val = fn()
        with self.lock: self.d[key] = (_now(), val)
        return val

def meminfo():
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                k, v = ln.split(":", 1); out[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError): return None
    return {"total": out.get("MemTotal"), "available": out.get("MemAvailable"), "swap_total": out.get("SwapTotal"), "swap_free": out.get("SwapFree")}

def vmstat():
    out = {}
    try:
        with open("/proc/vmstat") as f:
            for ln in f:
                k, v = ln.split()
                if k in ("pswpin", "pswpout"): out[k] = int(v)
    except (OSError, ValueError): return None
    return out

def gpu():
    if not shutil.which("nvidia-smi"): return {"available": False, "why": "nvidia-smi not found"}
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=8)
        name, used, total, temp, util = [x.strip() for x in p.stdout.strip().split("\n")[0].split(",")]
        return {"available": True, "name": name, "mem_used": int(used) << 20, "mem_total": int(total) << 20, "temp": float(temp), "util": float(util), "at": _now()}
    except (OSError, ValueError, subprocess.SubprocessError, IndexError) as e: return {"available": False, "why": f"nvidia-smi failed: {type(e).__name__}"}

def _http_json(url, timeout=1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r: return json.loads(r.read().decode())

def ollama(port):
    base = f"http://127.0.0.1:{port}"
    try: ver = _http_json(base + "/api/version").get("version")
    except Exception as e: return {"up": False, "why": f"no answer on 127.0.0.1:{port} ({type(e).__name__})", "at": _now()}
    try: ps = _http_json(base + "/api/ps").get("models") or []
    except Exception: ps = None
    loaded = [{"name": m.get("name"), "size": m.get("size"), "vram": m.get("size_vram"), "expires": m.get("expires_at"), "ctx": m.get("context_length")} for m in ps or []]
    return {"up": True, "version": ver, "port": port, "loaded": loaded, "loaded_known": ps is not None, "at": _now()}

def ollama_models(port):
    try: tags = _http_json(f"http://127.0.0.1:{port}/api/tags", timeout=3).get("models") or []
    except Exception as e: return {"ok": False, "why": type(e).__name__, "models": []}
    return {"ok": True, "models": [{"name": m.get("name"), "size": m.get("size"), "modified": m.get("modified_at"), "digest": (m.get("digest") or "")[:12],
                                    "family": (m.get("details") or {}).get("family"), "params": (m.get("details") or {}).get("parameter_size"),
                                    "quant": (m.get("details") or {}).get("quantization_level")} for m in tags]}

def docker(image):
    if not shutil.which("docker"): return {"available": False, "why": "docker is not installed"}
    try:
        p = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture_output=True, text=True, timeout=10)
        return {"available": True, "image": image, "image_present": p.returncode == 0, "why": "" if p.returncode == 0 else (p.stderr.strip()[:200] or "image not found")}
    except (OSError, subprocess.SubprocessError) as e: return {"available": False, "why": f"docker did not answer ({type(e).__name__})"}

def watchdog():
    if not shutil.which("systemctl"): return {"known": False, "why": "systemctl not found"}
    def q(*a):
        try: return subprocess.run(["systemctl", "--user", *a, "codebot.timer"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError): return "unknown"
    return {"known": True, "enabled": q("is-enabled"), "active": q("is-active")}

def disk(home):
    out = {"at": _now()}
    try: u = shutil.disk_usage(home); out["fs"] = {"total": u.total, "used": u.used, "free": u.free}
    except OSError: out["fs"] = None
    sizes = {}
    for d in ("models", "projects", "workspaces", "bench/runs", "bench/results", "logs", "tools"):
        p = os.path.join(home, d)
        if not os.path.isdir(p): continue
        try: sizes[d] = int(subprocess.run(["du", "-sb", p], capture_output=True, text=True, timeout=120).stdout.split()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError): sizes[d] = None
    out["dirs"] = sizes; return out

class Sampler(threading.Thread):
    """Temperature, memory and swap traffic every `period` s, kept for `keep` samples (2 h at 5 s)."""
    def __init__(self, period=5.0, keep=1440):
        super().__init__(daemon=True); self.period, self.hist, self.lock = period, collections.deque(maxlen=keep), threading.Lock()
        self.last_vm, self.last_t = None, None
    def sample(self):
        t, temp, mi, vm = _now(), None, meminfo(), vmstat()
        try: temp = thermal.read_temp()
        except Exception: temp = None
        swap_in = swap_out = None
        if vm and self.last_vm and self.last_t:
            dt = max(t - self.last_t, 1e-3); swap_in = (vm["pswpin"] - self.last_vm["pswpin"]) * 4096 / dt; swap_out = (vm["pswpout"] - self.last_vm["pswpout"]) * 4096 / dt
        self.last_vm, self.last_t = vm, t
        row = {"t": t, "temp": temp, "mem_available": (mi or {}).get("available"), "swap_used": ((mi or {}).get("swap_total") or 0) - ((mi or {}).get("swap_free") or 0) if mi else None,
               "swap_in_bps": swap_in, "swap_out_bps": swap_out}
        with self.lock: self.hist.append(row)
        return row
    def run(self):
        while True:
            try: self.sample()
            except Exception: pass
            time.sleep(self.period)
    def latest(self):
        with self.lock: return self.hist[-1] if self.hist else None
    def history(self, seconds=3600):
        cut = _now() - seconds
        with self.lock: return [r for r in self.hist if r["t"] >= cut]
