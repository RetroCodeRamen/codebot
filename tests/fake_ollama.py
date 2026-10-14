"""A scripted stand-in for Ollama's /api/chat so the worker can be tested in milliseconds without a model.
script: list of replies (str, dict -> json-encoded) consumed in order, or a callable(messages, body) -> reply."""
import http.server, json, os, threading

class FakeOllama:
    def __init__(self, script=None, reject_schema=False, cache=False, ps=None, tags=None):
        self.script, self.requests, self.reject_schema, self.cache, self.last_text = script or [], [], reject_schema, cache, ""
        self.ps = ps if ps is not None else {"models": []}; self.tags = tags if tags is not None else {"models": [{"name": "codebot-model:latest"}]}
        outer = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                body = {"/api/ps": outer.ps, "/api/tags": outer.tags}.get(self.path, {"version": "fake"})
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                outer.requests.append(body)
                if outer.reject_schema and isinstance(body.get("format"), dict) and "anyOf" in body["format"]:
                    self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"cannot compile schema"}'); return
                s = outer.script
                reply = s(body["messages"], body) if callable(s) else (s.pop(0) if s else {"why": "done", "action": "finish", "summary": "script exhausted"})
                reason = "stop"
                if isinstance(reply, tuple): reply, reason = reply                      # (text, "length") simulates a reply cut off by the token limit
                content = reply if isinstance(reply, str) else json.dumps(reply)
                total, evaluated, dur = 100, 100, 2_000_000_000
                if outer.cache:      # real semantics: prompt_eval_count = TOTAL prompt tokens; the duration reflects only what was really evaluated
                    text = "".join(m["content"] for m in body["messages"]); total = max(5, int(len(text) / 3.0)) + 25
                    if outer.cache == "hybrid": k = len(outer.last_text) if text.startswith(outer.last_text) else 0      # recurrent layers: only an append-only extension can reuse
                    else: k = len(os.path.commonprefix([text, outer.last_text]))
                    outer.last_text = text; evaluated = max(5, int((len(text) - k) / 3.0)) + 25; dur = evaluated * 40_000_000     # 25 tok/s
                out = {"message": {"role": "assistant", "content": content}, "prompt_eval_count": total, "eval_count": 20, "prompt_eval_duration": dur, "eval_duration": 1_000_000_000, "load_duration": 0, "total_duration": 3_000_000_000, "done": True, "done_reason": reason}
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(out).encode())
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def stop(self): self.srv.shutdown(); self.srv.server_close()

def A(action, why="because", **kw): return {"why": why, "action": action, **kw}
