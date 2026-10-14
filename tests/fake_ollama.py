"""A scripted stand-in for Ollama's /api/chat so the worker can be tested in milliseconds without a model.
script: list of replies (str, dict -> json-encoded) consumed in order, or a callable(messages, body) -> reply."""
import http.server, json, threading

class FakeOllama:
    def __init__(self, script=None, reject_schema=False):
        self.script, self.requests, self.reject_schema = script or [], [], reject_schema
        outer = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"version":"fake"}')
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                outer.requests.append(body)
                if outer.reject_schema and isinstance(body.get("format"), dict) and "anyOf" in body["format"]:
                    self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"cannot compile schema"}'); return
                s = outer.script
                reply = s(body["messages"], body) if callable(s) else (s.pop(0) if s else {"why": "done", "action": "finish", "summary": "script exhausted"})
                content = reply if isinstance(reply, str) else json.dumps(reply)
                out = {"message": {"role": "assistant", "content": content}, "prompt_eval_count": 100, "eval_count": 20, "done": True}
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(out).encode())
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def stop(self): self.srv.shutdown(); self.srv.server_close()

def A(action, why="because", **kw): return {"why": why, "action": action, **kw}
