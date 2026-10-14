"""Minimal Ollama /api/chat client (stdlib only). Local only: the host is always 127.0.0.1:<port>."""
import json, time, urllib.error, urllib.request
from . import audit, thermal, tokens

class ModelError(Exception):
    pass

def base_url(proj):
    return f"http://127.0.0.1:{proj.get('OLLAMA_PORT', 11500, int)}"

def chat(proj, messages, schema=None, model=None, temperature=None, num_predict=None, timeout=900, retries=3):
    """One non-streaming chat call. `schema` constrains the output (structured outputs). Thinking is always off:
    the harness wants a single JSON action, not hidden reasoning. Returns dict(content, prompt_tokens, gen_tokens, secs)."""
    thermal.wait(proj)
    body = {"model": model or proj.get("CODEBOT_MODEL", "codebot-model"), "messages": messages, "stream": False, "think": False,
            "keep_alive": proj.get("KEEP_ALIVE", "30m"), "options": {"num_ctx": tokens.num_ctx(proj)}}   # explicit on every request: a different value would reload the model
    if schema is not None: body["format"] = schema
    if temperature is not None: body["options"]["temperature"] = float(temperature)
    if num_predict: body["options"]["num_predict"] = int(num_predict)
    data = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        t0 = time.time()
        req = urllib.request.Request(base_url(proj) + "/api/chat", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: out = json.loads(r.read().decode())
            msg = out.get("message") or {}
            ms = lambda k: round(out.get(k, 0) / 1e6)                     # Ollama reports nanoseconds
            st = {"prompt_tokens": out.get("prompt_eval_count", 0), "gen_tokens": out.get("eval_count", 0), "prompt_ms": ms("prompt_eval_duration"),
                  "gen_ms": ms("eval_duration"), "load_ms": ms("load_duration"), "total_ms": ms("total_duration")}
            audit.log(proj, "model_request", endpoint=base_url(proj).split("//")[1], model=body["model"], secs=round(time.time() - t0, 1), **st)
            return {"content": msg.get("content", ""), "secs": round(time.time() - t0, 1), "done_reason": out.get("done_reason", ""), **st}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last = ModelError(f"HTTP {e.code}: {detail}")
            if e.code == 400: break                         # a bad request will not get better by retrying
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = ModelError(f"cannot reach the local model server: {e}")
        time.sleep(min(2 ** attempt * 2, 20))               # the server may still be loading the model
    raise last
