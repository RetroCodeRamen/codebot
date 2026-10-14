"""Thermal sweep: how much sustained speed does each configuration give for how much heat? Each config runs a fixed, worker-like workload
(a cold ~800-token prompt + a short generated reply, repeated) for several minutes, starting from a cooled-down machine, and the STEADY STATE
(the last minutes, after boost clocks have settled) is compared with the first minute. Nothing here changes a setting permanently."""
import glob, json, os, random, re, shutil, statistics, subprocess, threading, time
from . import ollama, thermal, worker
from .config import now

def cpu_freq_mhz():
    try:
        f = [int(open(p).read()) for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")]
        return round(sum(f) / len(f) / 1000) if f else None
    except (OSError, ValueError): return None

def gpu_available():
    try: return subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError): return False

def power_profile(run=subprocess.run):
    try: return run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError): return None

def set_power_profile(name, run=subprocess.run):
    try: return run(["powerprofilesctl", "set", name], capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError): return False

def configs(gpu=None, have_profiles=None):
    gpu = gpu_available() if gpu is None else gpu; have_profiles = bool(shutil.which("powerprofilesctl")) if have_profiles is None else have_profiles
    out = [{"name": "4 threads (current default)", "options": {"num_thread": 4}}, {"name": "3 threads", "options": {"num_thread": 3}}, {"name": "2 threads", "options": {"num_thread": 2}}]
    out.append({"name": "4 threads + power-saver profile", "options": {"num_thread": 4}, "profile": "power-saver"} if have_profiles else {"name": "power-saver profile", "skip": "powerprofilesctl is not available"})
    out.append({"name": "GPU offload (num_gpu=99) + 4 threads", "options": {"num_thread": 4, "num_gpu": 99}} if gpu else
               {"name": "GPU offload (3050 Ti)", "skip": "no NVIDIA driver loaded (nvidia-smi fails); install nvidia-driver-580-open and reboot to test this"})
    return out

def _prompt():
    """Cold every time (salted) and long enough to be dominated by real prompt processing, like a worker's first turn."""
    filler = " ".join(f"item{i}: value {random.randrange(10**6)} and some words about parsing and files" for i in range(45))
    return [{"role": "system", "content": "You are a worker. Reply with one JSON action."},
            {"role": "user", "content": f"[{random.randrange(10**12)}] {filler}\n\nReply with ONE JSON action: list the directory '.'."}]

def cooldown(target, reader=thermal.read_temp, sleep=time.sleep, cap=900, step=15):
    waited = 0
    while waited < cap:
        t = reader()
        if t is None or t <= target: return waited, t
        sleep(step); waited += step
    return waited, reader()

def run_config(proj, cfg, minutes=4.0, cool_to=72, abort_at=97, sample_every=5, reader=thermal.read_temp, sleep=time.sleep, clock=time.time, chat=None, freq=cpu_freq_mhz,
               set_profile=set_power_profile, get_profile=power_profile, log=print):
    chat = chat or ollama.chat
    if cfg.get("skip"): return {"name": cfg["name"], "skipped": cfg["skip"]}
    waited, start_t = cooldown(cool_to, reader, sleep); prev = get_profile() if cfg.get("profile") else None
    samples, reqs, stop, aborted = [], [], threading.Event(), None
    def sampler():
        while not stop.is_set():
            samples.append({"t": clock(), "temp": reader(), "mhz": freq()}); stop.wait(sample_every)
    try:
        if cfg.get("profile"): set_profile(cfg["profile"])
        th = threading.Thread(target=sampler, daemon=True); th.start(); t0 = clock()
        while clock() - t0 < minutes * 60:
            tt = reader()
            if tt is not None and tt >= abort_at: aborted = f"aborted at {tt} C (limit {abort_at})"; break
            started = clock() - t0
            r = chat(proj, _prompt(), schema=worker.action_schema(), temperature=0.1, num_predict=70, timeout=900, options=cfg["options"])
            reqs.append({"t": started, "prompt_tps": r["prompt_tokens"] / max(0.001, r["prompt_ms"] / 1000), "gen_tps": r["gen_tokens"] / max(0.001, r["gen_ms"] / 1000),
                         "secs": r["secs"], "ptok": r["prompt_tokens"], "gtok": r["gen_tokens"]})
        stop.set(); th.join(timeout=5)
    finally:
        stop.set()
        if cfg.get("profile") and prev: set_profile(prev)               # ALWAYS put the power profile back
    return summarize(cfg, reqs, samples, t0, waited, start_t, aborted, prev)

def summarize(cfg, reqs, samples, t0, waited, start_t, aborted=None, prev_profile=None):
    dur = max(1.0, (reqs[-1]["t"] + reqs[-1]["secs"]) if reqs else 1.0)
    split = min(60.0, dur / 3); first = [r for r in reqs if r["t"] < split]; steady = [r for r in reqs if r["t"] >= dur / 2]   # steady state = the second half of the run
    sam = lambda lo, hi, k: [s[k] for s in samples if s[k] is not None and lo <= s["t"] - t0 < hi]
    mean = lambda xs: round(statistics.mean(xs), 1) if xs else None
    rpm = lambda rs, span: round(len(rs) / (span / 60), 2) if rs and span else None
    return {"name": cfg["name"], "requests": len(reqs), "aborted": aborted, "cooldown_s": waited, "start_temp": start_t, "restored_profile": prev_profile,
            "first_minute": {"requests_per_min": rpm(first, split), "prompt_tps": mean([r["prompt_tps"] for r in first]), "gen_tps": mean([r["gen_tps"] for r in first]), "temp": mean(sam(0, split, "temp")), "mhz": mean(sam(0, split, "mhz"))},
            "steady": {"requests_per_min": rpm(steady, dur / 2), "prompt_tps": mean([r["prompt_tps"] for r in steady]), "gen_tps": mean([r["gen_tps"] for r in steady]),
                       "temp": mean(sam(dur / 2, 1e9, "temp")), "temp_max": max(sam(dur / 2, 1e9, "temp") or [None], key=lambda x: -1 if x is None else x), "mhz": mean(sam(dur / 2, 1e9, "mhz"))}}

def recommend(results, hot=88.0):
    """Highest steady-state throughput among configs whose steady temperature stays under `hot`; if none do, the coolest one that keeps >= 90% of the best rate."""
    ok = [r for r in results if not r.get("skipped") and r["steady"]["requests_per_min"]]
    if not ok: return None
    best = max(ok, key=lambda r: r["steady"]["requests_per_min"]); cool = [r for r in ok if (r["steady"]["temp"] or 999) < hot]
    if cool: return max(cool, key=lambda r: r["steady"]["requests_per_min"])
    near = [r for r in ok if r["steady"]["requests_per_min"] >= 0.9 * best["steady"]["requests_per_min"]]
    return min(near, key=lambda r: r["steady"]["temp"] or 999)

def render(results, minutes):
    L = [f"### Thermal sweep ({minutes} min per config, cooldown to a common start, steady state = second half)", "",
         "| config | req/min first min | req/min steady | gen tok/s steady | cold prompt tok/s steady | temp steady (max) | CPU MHz steady |", "|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("skipped"): L.append(f"| {r['name']} | skipped: {r['skipped']} | | | | | |"); continue
        f, s = r["first_minute"], r["steady"]
        L.append(f"| {r['name']}{' (ABORTED: ' + r['aborted'] + ')' if r['aborted'] else ''} | {f['requests_per_min']} | {s['requests_per_min']} | {s['gen_tps']} | {s['prompt_tps']} | {s['temp']} ({s['temp_max']}) | {s['mhz']} |")
    rec = recommend(results)
    if rec: L += ["", f"Recommendation: **{rec['name']}**: {rec['steady']['requests_per_min']} req/min at {rec['steady']['temp']} C steady."]
    return "\n".join(L)
