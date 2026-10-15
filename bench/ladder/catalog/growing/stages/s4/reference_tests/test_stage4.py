import http.client, json, os, select, signal, socket, subprocess, sys, tempfile, threading, time, unittest
import notes
from notes import NoteStore, make_server

WS = os.getcwd()

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g4-"); self.path = os.path.join(self.dir, "n.json"); self.store = NoteStore(self.path, clock=lambda: "2024-03-01T09:00:00"); self.server = make_server(self.store).start(); self.addCleanup(self.stop)
    def stop(self):
        try: self.server.stop()
        except Exception: pass
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def call(self, method, path, body=None, raw=None, port=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        c = http.client.HTTPConnection("127.0.0.1", port or self.server.port, timeout=60)
        try: c.request(method, path, body=data); r = c.getresponse(); text = r.read().decode(); h = {k.lower(): v for k, v in r.getheaders()}
        finally: c.close()
        return r.status, (json.loads(text) if text else None), h

class TestServer(Base):
    def test_r01_lifecycle(self): self.assertGreater(self.server.port, 0); s, b, h = self.call("GET", "/health"); self.assertEqual((s, b, h["content-type"]), (200, {"status": "ok"}, "application/json"))
    def test_r01_context_manager_and_second_server(self):
        with make_server(self.store) as other: self.assertNotEqual(other.port, self.server.port); self.assertEqual(self.call("GET", "/health", port=other.port)[0], 200)
        s = socket.socket(); s.settimeout(2)
        try: self.assertNotEqual(s.connect_ex(("127.0.0.1", other.port)), 0)
        finally: s.close()
    def test_r01_start_returns_the_server_and_stop_releases_the_port(self):
        srv = make_server(self.store); self.assertIs(srv.start(), srv); port = srv.port; srv.stop(); s = socket.socket(); s.settimeout(2)
        try: self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)
        finally: s.close()
    def test_r01_content_length_is_correct_and_connections_are_kept_alive(self):
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        try:
            for _ in range(3): c.request("GET", "/health"); r = c.getresponse(); body = r.read(); self.assertEqual(int(r.getheader("Content-Length")), len(body))
        finally: c.close()
    def test_r01_no_request_log(self):
        import contextlib, io; buf = io.StringIO()
        with contextlib.redirect_stderr(buf): self.call("GET", "/health"); self.call("GET", "/nothing")
        self.assertEqual(buf.getvalue(), "")

class TestRoutes(Base):
    def test_r02_create_and_get(self):
        s, b, h = self.call("POST", "/notes", {"title": "Hello", "body": "World", "tags": ["a"]}); self.assertEqual((s, h["location"], b["id"], b["title"], b["tags"]), (201, "/notes/1", 1, "Hello", ["a"])); s, b2, h = self.call("GET", "/notes/1"); self.assertEqual((s, b2), (200, b))
    def test_r02_create_with_only_a_title(self): s, b, h = self.call("POST", "/notes", {"title": "T"}); self.assertEqual((s, b["body"], b["tags"]), (201, "", []))
    def test_r02_list_envelope(self):
        for i in range(25): self.store.add(f"n{i}", tags=["even" if i % 2 == 0 else "odd"])
        s, b, h = self.call("GET", "/notes"); self.assertEqual((s, b["total"], b["limit"], b["offset"], len(b["items"]), b["items"][0]["id"]), (200, 25, 20, 0, 20, 1)); s, b, h = self.call("GET", "/notes?limit=5&offset=22"); self.assertEqual(([n["id"] for n in b["items"]], b["total"]), ([23, 24, 25], 25))
    def test_r02_tag_filters(self):
        self.store.add("A", tags=["x", "y"]); self.store.add("B", tags=["x"]); self.assertEqual(self.call("GET", "/notes?tag=x")[1]["total"], 2); self.assertEqual([n["id"] for n in self.call("GET", "/notes?tag=x&tag=y")[1]["items"]], [1]); self.assertEqual(self.call("GET", "/notes?tag=zzz")[1]["items"], [])
    def test_r02_search(self):
        self.store.add("Milk run", "buy milk", ["home"]); self.store.add("Other"); b = self.call("GET", "/search?q=milk")[1]; self.assertEqual((b["total"], b["items"][0]["id"]), (1, 1)); self.assertEqual(self.call("GET", "/search?q=milk&tag=work")[1]["total"], 0); self.assertEqual(self.call("GET", "/search")[1]["total"], 2); self.assertEqual(self.call("GET", "/search?q=")[1]["total"], 2); self.assertEqual(self.call("GET", "/search?q=milk+run")[1]["total"], 1)
    def test_r02_put_replaces_the_content(self):
        self.store.add("Old", "body", ["t"]); s, b, h = self.call("PUT", "/notes/1", {"title": "New"}); self.assertEqual((s, b["title"], b["body"], b["tags"]), (200, "New", "", [])); s, b, h = self.call("PUT", "/notes/1", {"title": "Newer", "body": "b", "tags": ["x"]}); self.assertEqual((b["body"], b["tags"]), ("b", ["x"]))
    def test_r02_put_needs_a_title(self): self.store.add("Old"); s, b, h = self.call("PUT", "/notes/1", {"body": "x"}); self.assertEqual((s, b), (422, {"error": "title must not be empty"})); self.assertEqual(self.store.get(1)["title"], "Old")
    def test_r02_delete(self): self.store.add("A"); s, b, h = self.call("DELETE", "/notes/1"); self.assertEqual((s, b), (204, None)); self.assertEqual(self.call("GET", "/notes/1")[0], 404); self.assertEqual(self.store.list(), [])
    def test_r02_trailing_slashes(self): self.store.add("A"); self.assertEqual(self.call("GET", "/notes/")[0], 200); self.assertEqual(self.call("GET", "/notes/1/")[0], 200); self.assertEqual(self.call("GET", "/health/")[0], 200)
    def test_r02_the_updated_stamp_follows_the_store_clock(self): self.store.add("A"); s, b, h = self.call("PUT", "/notes/1", {"title": "B"}); self.assertEqual(b["updated"], "2024-03-01T09:00:00")

class TestErrors(Base):
    def test_r03_unknown_path_and_note(self): self.assertEqual(self.call("GET", "/nothing")[:2], (404, {"error": "not found"})); self.assertEqual(self.call("GET", "/notes/9")[:2], (404, {"error": "no note with id 9"})); self.assertEqual(self.call("DELETE", "/notes/9")[0], 404); self.assertEqual(self.call("PUT", "/notes/9", {"title": "x"})[0], 404)
    def test_r03_wrong_method(self):
        s, b, h = self.call("DELETE", "/notes"); self.assertEqual((s, b, h["allow"]), (405, {"error": "method not allowed"}, "GET, POST")); s, b, h = self.call("POST", "/notes/1", {"title": "x"}); self.assertEqual((s, h["allow"]), (405, "DELETE, GET, PUT")); s, b, h = self.call("POST", "/health", {}); self.assertEqual((s, h["allow"]), (405, "GET")); self.assertEqual(self.call("PATCH", "/search", {})[0], 405)
    def test_r03_validation_errors_are_422_with_the_store_message(self):
        for body, msg in (({"title": ""}, "title must not be empty"), ({"title": "x" * 101}, "title must be one line of at most 100 characters"), ({"title": "ok", "tags": ["bad tag"]}, "invalid tag 'bad tag'"), ({"title": "ok", "body": 5}, "body must be a string"), ({}, "title must not be empty")):
            self.assertEqual(self.call("POST", "/notes", body)[:2], (422, {"error": msg}), body)
        self.assertEqual(self.store.list(), [])
    def test_r03_paging_errors(self):
        for q in ("limit=0", "limit=101", "limit=x", "limit=", "limit=-1", "limit=1.5"): self.assertEqual(self.call("GET", "/notes?" + q)[:2], (400, {"error": "invalid limit"}), q)
        for q in ("offset=-1", "offset=x", "offset=", "offset=1.5"): self.assertEqual(self.call("GET", "/notes?" + q)[:2], (400, {"error": "invalid offset"}), q)
        self.assertEqual(self.call("GET", "/notes?limit=0&offset=-1")[1], {"error": "invalid limit"}); self.assertEqual(self.call("GET", "/search?limit=0")[0], 400); self.assertEqual(self.call("GET", "/notes?limit=100")[0], 200)
    def test_r03_body_errors(self):
        self.assertEqual(self.call("POST", "/notes", raw=b"{nope")[:2], (400, {"error": "invalid JSON"})); self.assertEqual(self.call("POST", "/notes", raw=b"\xff")[1], {"error": "invalid JSON"}); self.assertEqual(self.call("POST", "/notes", raw=b"[1]")[:2], (400, {"error": "body must be a JSON object"})); self.assertEqual(self.call("POST", "/notes")[:2], (400, {"error": "body must be a JSON object"})); self.assertEqual(self.call("PUT", "/notes/1", raw=b"5")[0], 400)
    def test_r03_oversized_body(self):
        # only the announced size matters: the server must answer 413 without reading a megabyte (a client that really sends it could be reset mid-send)
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=60)
        try: c.putrequest("POST", "/notes"); c.putheader("Content-Length", "1200000"); c.endheaders(); r = c.getresponse(); self.assertEqual((r.status, json.loads(r.read().decode())), (413, {"error": "request too large"}))
        finally: c.close()
        self.assertEqual(self.call("GET", "/health")[0], 200)
    def test_r03_check_order(self):
        self.assertEqual(self.call("DELETE", "/notes", raw=b"{bad")[0], 405); self.assertEqual(self.call("POST", "/nothing", raw=b"{bad")[0], 404); self.assertEqual(self.call("GET", "/notes?limit=0")[0], 400)
    def test_r03_errors_are_json(self):
        for m, p in (("GET", "/nope"), ("DELETE", "/notes"), ("GET", "/notes/9"), ("GET", "/notes?limit=0")): s, b, h = self.call(m, p); self.assertEqual(h["content-type"], "application/json"); self.assertIn("error", b)

class TestConsistency(Base):
    def test_r04_the_cli_and_the_api_see_each_other(self):
        env = dict(os.environ, NOTES_FILE=self.path, PYTHONPATH=WS); subprocess.run([sys.executable, "-m", "notes", "add", "from cli", "--tag", "c"], env=env, cwd=self.dir, check=True, capture_output=True, timeout=60)
        self.assertEqual(self.call("GET", "/notes/1")[1]["title"], "from cli"); self.call("POST", "/notes", {"title": "from api"}); p = subprocess.run([sys.executable, "-m", "notes", "list"], env=env, cwd=self.dir, capture_output=True, text=True, timeout=60); self.assertEqual(p.stdout, "  1  from cli  #c\n  2  from api\n")
    def test_r04_another_store_object_sees_api_changes(self): self.call("POST", "/notes", {"title": "x"}); self.assertEqual(NoteStore(self.path).get(1)["title"], "x")
    def test_r04_parallel_creates_lose_nothing(self):
        ids, lock = [], threading.Lock()
        def work():
            for _ in range(5):
                s, b, h = self.call("POST", "/notes", {"title": "p"})
                with lock: ids.append((s, b["id"]))
        ts = [threading.Thread(target=work) for _ in range(4)]; [t.start() for t in ts]; [t.join(180) for t in ts]; self.assertEqual(sorted(i for _, i in ids), list(range(1, 21))); self.assertTrue(all(s == 201 for s, _ in ids)); self.assertEqual(len(self.store.list()), 20)

class TestServeCommand(unittest.TestCase):
    def test_r05_serve_prints_its_url_and_answers_requests(self):
        d = tempfile.mkdtemp(prefix="g4s-"); env = dict(os.environ, NOTES_FILE=os.path.join(d, "n.json"), PYTHONPATH=WS)
        p = subprocess.Popen([sys.executable, "-m", "notes", "serve", "--port", "0"], env=env, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([p.stdout], [], [], 30); self.assertTrue(ready, "no output from serve"); line = p.stdout.readline().strip(); self.assertRegex(line, r"^Serving on http://127\.0\.0\.1:\d+$"); port = int(line.rsplit(":", 1)[1])
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=30); c.request("POST", "/notes", body=json.dumps({"title": "served"}).encode()); r = c.getresponse(); self.assertEqual(r.status, 201); r.read(); c.request("GET", "/notes"); r = c.getresponse(); self.assertEqual(json.loads(r.read())["total"], 1); c.close()
        finally:
            p.send_signal(signal.SIGTERM); p.wait(30)
        self.assertEqual(NoteStore(os.path.join(d, "n.json")).get(1)["title"], "served")
