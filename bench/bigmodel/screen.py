#!/usr/bin/env python3
"""Feasibility screen for one loaded llama-server: representative codebot requests, one at a time, with memory, paging, SSD-read, GPU and
temperature sampled every 0.5 s. Cold start is measured by launch.sh; this measures prompt processing, time to first token and generation
separately (llama-server's own timings), and checks the output is usable (drafts reply format; JSON that matches codebot's schemas).

  screen.py LABEL PORT SERVER_PID [--ctx 4096] [--max-tokens 1500] [--only name,name] [--think off]
Appends one JSON line per request to bench/results/bigmodel/LABEL/screen.jsonl and saves each reply next to it.
"""
import json, os, re, subprocess, sys, threading, time, urllib.request

H = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(H, "lib"))
from codebot import drafts, thermal, worker, planner  # noqa: E402

P = os.path.join(H, "bench", "bigmodel", "prompts")
PROMPTS = [  # name, language, a real drafts-mode prompt (calc_go seed 2 rounds 1-2, level_03 seed 1 rounds 1 and 5 of the 2026-10-20 runs)
    ("calc_fresh", "Go", f"{P}/calc_fresh.md"),
    ("calc_repair", "Go", f"{P}/calc_repair.md"),
    ("l03_fresh", "Python", f"{P}/l03_fresh.md"),
    ("l03_repair", "Python", f"{P}/l03_repair.md"),
]
JSON_REQS = [
    ("json_action", worker.action_schema(),
     "You are working on a Go command-line calculator in the current directory. main.go does not exist yet. The task: create main.go so that "
     "`go build -o calc .` builds a program that prints the sum of its two arguments. Reply with your next action as one JSON object."),
    ("json_task", planner.task_schema(),
     "Plan ONE task for this requirement as a JSON object: `textutil.py` provides `slugify(text)`, which lowercases text, replaces every run of "
     "characters that are not letters or digits with a single '-', and strips '-' from both ends: slugify('Hello, World!') is 'hello-world'. "
     "Use assertions of type `call` that check examples."),
]


def valid(v, s):
    """A small JSON-schema check for the subset codebot's schemas use."""
    if "anyOf" in s: return any(valid(v, x) for x in s["anyOf"])
    if "enum" in s and v not in s["enum"]: return False
    t = s.get("type")
    if t == "object":
        if not isinstance(v, dict): return False
        props = s.get("properties", {})
        if any(k not in v for k in s.get("required", [])): return False
        if s.get("additionalProperties") is False and any(k not in props for k in v): return False
        return all(valid(v[k], props[k]) for k in v if k in props)
    if t == "array":
        if not isinstance(v, list): return False
        if len(v) < s.get("minItems", 0) or len(v) > s.get("maxItems", 10 ** 9): return False
        return all(valid(x, s["items"]) for x in v) if "items" in s else True
    if t == "string": return isinstance(v, str) and len(v) <= s.get("maxLength", 10 ** 9)
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if t == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean": return isinstance(v, bool)
    return True


class Monitor(threading.Thread):
    def __init__(self, pid):
        super().__init__(daemon=True); self.pid, self.stop, self.s = pid, False, []
    @staticmethod
    def _kv(path, keys):
        out = {}
        for ln in open(path):
            k = ln.split(":")[0].split()[0] if ":" in ln else ln.split()[0]
            if k in keys: out[k] = int(ln.split()[1])
        return out
    def sample(self):
        st = open(f"/proc/{self.pid}/stat").read().rsplit(")", 1)[1].split()
        rss = self._kv(f"/proc/{self.pid}/status", {"VmRSS"}).get("VmRSS", 0)
        mi = self._kv("/proc/meminfo", {"MemAvailable", "SwapFree", "Cached"})
        vm = self._kv("/proc/vmstat", {"pswpin", "pswpout", "pgmajfault"})
        ssd = next((int(l.split()[5]) for l in open("/proc/diskstats") if l.split()[2] == "nvme0n1"), 0)
        return {"t": time.time(), "rss_mb": rss // 1024, "majflt": int(st[9]), "avail_mb": mi["MemAvailable"] // 1024, "swapfree_mb": mi["SwapFree"] // 1024,
                "pswpin": vm["pswpin"], "pswpout": vm["pswpout"], "ssd_read_mb": ssd * 512 // 2 ** 20, "temp": thermal.read_temp()}
    def run(self):
        n = 0
        while not self.stop:
            x = self.sample()
            if n % 10 == 0:
                try: x["gpu_mb"] = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout.split()[0])
                except Exception: pass
            self.s.append(x); n += 1; time.sleep(0.5)
    def summary(self, a, b):
        return {"peak_rss_mb": max(x["rss_mb"] for x in self.s), "min_avail_mb": min(x["avail_mb"] for x in self.s), "majflt": b["majflt"] - a["majflt"],
                "swap_in_pages": b["pswpin"] - a["pswpin"], "swap_out_pages": b["pswpout"] - a["pswpout"], "ssd_read_mb": b["ssd_read_mb"] - a["ssd_read_mb"],
                "max_temp": max((x["temp"] or 0) for x in self.s), "peak_gpu_mb": max((x.get("gpu_mb") or 0) for x in self.s)}


def chat(port, msgs, max_tokens, schema=None, think=None):
    body = {"messages": msgs, "temperature": 0.2, "seed": 1, "max_tokens": max_tokens, "stream": True, "timings_per_token": False}
    if schema is not None: body["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}
    if think is not None: body["chat_template_kwargs"] = {"enable_thinking": think}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time(); ttft = None; text, reasoning, timings, finish = [], [], None, None
    with urllib.request.urlopen(req, timeout=4 * 3600) as r:
        for raw in r:
            ln = raw.decode().strip()
            if not ln.startswith("data:"): continue
            data = ln[5:].strip()
            if data == "[DONE]": break
            ev = json.loads(data)
            if ev.get("timings"): timings = ev["timings"]
            for c in ev.get("choices", []):
                d = c.get("delta") or {}
                if (d.get("content") or d.get("reasoning_content")) and ttft is None: ttft = time.time() - t0
                if d.get("content"): text.append(d["content"])
                if d.get("reasoning_content"): reasoning.append(d["reasoning_content"])
                if c.get("finish_reason"): finish = c["finish_reason"]
    return {"wall_s": round(time.time() - t0, 1), "ttft_s": round(ttft, 1) if ttft else None, "text": "".join(text), "reasoning_chars": len("".join(reasoning)),
            "timings": timings, "finish": finish}


def main():
    a = sys.argv[1:]; label, port, pid = a[0], int(a[1]), int(a[2])
    opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    ctx, maxt = int(opt("--ctx", 4096)), int(opt("--max-tokens", 1500)); only = opt("--only", ""); think = opt("--think", "")
    think = {"off": False, "on": True}.get(think)
    d = os.path.join(H, "bench", "results", "bigmodel", label); os.makedirs(d, exist_ok=True)
    reqs = [(n, lang, open(p).read(), None) for n, lang, p in PROMPTS] + [(n, None, txt, sch) for n, sch, txt in JSON_REQS]
    for name, lang, user, schema in reqs:
        if only and name not in only.split(","): continue
        est = len(user) // 3 + 300
        if est + maxt > ctx: print(f"skip {name}: about {est} prompt tokens + {maxt} do not fit in {ctx}"); continue
        sysmsg = drafts.system_prompt(lang) if lang else "You are a careful software engineer. Reply with JSON only."
        mon = Monitor(pid); a0 = mon.sample(); mon.start()
        try: r = chat(port, [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], maxt, schema, think)
        except Exception as e: r = {"error": f"{type(e).__name__}: {e}"[:300]}
        mon.stop = True; mon.join(); b = mon.sample()
        row = {"label": label, "request": name, "ctx": ctx, "max_tokens": maxt, **{k: v for k, v in r.items() if k != "text"}, **mon.summary(a0, b)}
        t = r.get("timings") or {}
        if t:
            row.update(prompt_n=t.get("prompt_n"), prompt_ms=round(t.get("prompt_ms", 0)), prompt_tps=round(t.get("prompt_per_second", 0), 1),
                       gen_n=t.get("predicted_n"), gen_ms=round(t.get("predicted_ms", 0)), gen_tps=round(t.get("predicted_per_second", 0), 2))
        text = r.get("text", "")
        if schema is not None:
            try: v = json.loads(text); row.update(json_ok=True, schema_ok=valid(v, schema))
            except ValueError: row.update(json_ok=False, schema_ok=False)
        elif text:
            got = drafts.extract(text, [], lang)
            row.update(files=sorted(got), reply_chars=len(text))
        with open(os.path.join(d, f"reply-{name}.txt"), "w") as f: f.write(text)
        with open(os.path.join(d, "screen.jsonl"), "a") as f: f.write(json.dumps(row) + "\n")
        print(json.dumps({k: row.get(k) for k in ("request", "prompt_n", "prompt_tps", "ttft_s", "gen_n", "gen_tps", "wall_s", "finish", "json_ok", "schema_ok",
                                                    "files", "peak_rss_mb", "min_avail_mb", "majflt", "swap_out_pages", "ssd_read_mb", "peak_gpu_mb", "max_temp", "error")}), flush=True)


if __name__ == "__main__":
    main()
