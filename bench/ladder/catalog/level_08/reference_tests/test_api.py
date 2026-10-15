import http.client, json, os, socket, tempfile, threading, unittest
import inv_api

TOKENS = {"adm-token": "admin", "read-token": "reader"}

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="api-test-"); self.db = os.path.join(self.dir, "api.db"); self.server = inv_api.make_server(self.db, TOKENS).start(); self.addCleanup(self.stop)
    def stop(self):
        try: self.server.stop()
        except Exception: pass
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def call(self, method, path, body=None, token="adm-token", raw=None, headers=None, port=None):
        h = dict(headers or {})
        if token: h["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if data is not None: h.setdefault("Content-Type", "application/json")
        c = http.client.HTTPConnection("127.0.0.1", port or self.server.port, timeout=30)
        try:
            c.request(method, path, body=data, headers=h); r = c.getresponse(); text = r.read().decode(); hdrs = {k.lower(): v for k, v in r.getheaders()}
        finally: c.close()
        return r.status, (json.loads(text) if text else None), hdrs
    def product(self, sku="A1", name="Apple", price=100, **kw): return self.call("POST", "/products", {"sku": sku, "name": name, "price_cents": price, **kw})
    def stock(self, sku, delta, reason="test"): return self.call("POST", f"/products/{sku}/stock", {"delta": delta, "reason": reason})

class TestLifecycle(Base):
    def test_r01_health_needs_no_authentication(self):
        s, b, h = self.call("GET", "/health", token=None); self.assertEqual((s, b), (200, {"status": "ok"}))
    def test_r01_the_real_port_is_reported_for_port_zero(self): self.assertGreater(self.server.port, 0); self.assertNotEqual(self.server.port, 0)
    def test_r01_two_servers_get_different_ports(self):
        other = inv_api.make_server(os.path.join(self.dir, "o.db"), TOKENS).start(); self.addCleanup(other.stop); self.assertNotEqual(other.port, self.server.port)
    def test_r01_stop_releases_the_port(self):
        port = self.server.port; self.server.stop(); s = socket.socket(); s.settimeout(2)
        try: self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)
        finally: s.close()
    def test_r01_context_manager(self):
        with inv_api.make_server(os.path.join(self.dir, "c.db"), TOKENS) as srv:
            s, b, h = self.call("GET", "/health", token=None, port=srv.port); self.assertEqual(s, 200)
        s = socket.socket(); s.settimeout(2)
        try: self.assertNotEqual(s.connect_ex(("127.0.0.1", srv.port)), 0)
        finally: s.close()
    def test_r01_invalid_roles_are_rejected(self):
        for tokens in ({"t": "root"}, {"t": ""}, {"": "admin"}):
            with self.assertRaises(ValueError): inv_api.make_server(os.path.join(self.dir, "x.db"), tokens)
    def test_r01_start_returns_the_server(self): srv = inv_api.make_server(os.path.join(self.dir, "y.db"), TOKENS); self.assertIs(srv.start(), srv); self.addCleanup(srv.stop)
    def test_r01_the_database_file_is_created(self): self.assertTrue(os.path.exists(self.db))
    def test_r01_responses_are_json_with_the_right_content_length(self):
        c = http.client.HTTPConnection("127.0.0.1", self.server.port); c.request("GET", "/health"); r = c.getresponse(); body = r.read()
        self.assertEqual(r.getheader("Content-Type"), "application/json"); self.assertEqual(int(r.getheader("Content-Length")), len(body)); c.close()

class TestAuth(Base):
    def test_r02_missing_token(self):
        s, b, h = self.call("GET", "/products", token=None); self.assertEqual((s, b), (401, {"error": "unauthorized"})); self.assertEqual(h["www-authenticate"], "Bearer")
    def test_r02_wrong_token_and_malformed_header(self):
        self.assertEqual(self.call("GET", "/products", token="nope")[0], 401)
        for hdr in ("Basic abc", "Bearer", "adm-token", "bearer adm-token"): self.assertEqual(self.call("GET", "/products", token=None, headers={"Authorization": hdr})[0], 401, hdr)
    def test_r02_reader_can_read(self):
        self.product(); self.assertEqual(self.call("GET", "/products", token="read-token")[0], 200); self.assertEqual(self.call("GET", "/products/A1", token="read-token")[0], 200); self.assertEqual(self.call("GET", "/products/A1/movements", token="read-token")[0], 200)
    def test_r02_reader_cannot_write(self):
        self.product()
        for m, p, b in (("POST", "/products", {"sku": "Z", "name": "z", "price_cents": 1}), ("PATCH", "/products/A1", {"name": "x"}), ("DELETE", "/products/A1", None), ("POST", "/products/A1/stock", {"delta": 1, "reason": "r"})):
            s, body, h = self.call(m, p, b, token="read-token"); self.assertEqual((s, body), (403, {"error": "forbidden"}), (m, p))
        self.assertEqual(self.call("GET", "/products/A1")[1]["name"], "Apple")
    def test_r02_admin_can_do_everything(self): self.assertEqual(self.product()[0], 201); self.assertEqual(self.call("PATCH", "/products/A1", {"name": "B"})[0], 200)
    def test_r02_unknown_path_is_404_even_without_a_token(self): self.assertEqual(self.call("GET", "/nothing", token=None), (404, {"error": "not found"}, self.call("GET", "/nothing", token=None)[2]))
    def test_r02_authentication_comes_before_method_checks(self): self.assertEqual(self.call("DELETE", "/products", token=None)[0], 401)
    def test_r02_method_not_allowed_has_a_sorted_allow_header(self):
        s, b, h = self.call("DELETE", "/products"); self.assertEqual((s, b), (405, {"error": "method not allowed"})); self.assertEqual(h["allow"], "GET, POST")
        s, b, h = self.call("PUT", "/products/x"); self.assertEqual(s, 405); self.assertEqual(h["allow"], "DELETE, GET, PATCH")
        s, b, h = self.call("GET", "/products/x/stock"); self.assertEqual(s, 405); self.assertEqual(h["allow"], "POST")
        s, b, h = self.call("POST", "/health", token=None); self.assertEqual(s, 405); self.assertEqual(h["allow"], "GET")
    def test_r02_method_check_comes_before_the_role_check(self): self.assertEqual(self.call("DELETE", "/products", token="read-token")[0], 405)
    def test_r02_role_check_comes_before_body_validation(self): self.assertEqual(self.call("POST", "/products", raw=b"{nope", token="read-token")[0], 403)
    def test_r02_each_token_maps_to_its_own_role(self):
        srv = inv_api.make_server(os.path.join(self.dir, "t.db"), {"only-reader": "reader"}).start(); self.addCleanup(srv.stop)
        self.assertEqual(self.call("POST", "/products", {"sku": "a", "name": "a", "price_cents": 1}, token="only-reader", port=srv.port)[0], 403); self.assertEqual(self.call("GET", "/products", token="adm-token", port=srv.port)[0], 401)
    def test_r02_the_error_body_is_json(self): s, b, h = self.call("GET", "/products", token=None); self.assertEqual(h["content-type"], "application/json")
    def test_r02_trailing_slash_and_query_do_not_change_the_route(self): self.assertEqual(self.call("GET", "/products/?limit=5")[0], 200)

class TestProducts(Base):
    def test_r03_create_and_get(self):
        s, b, h = self.product("A1", "  Apple  ", 120, reorder_level=5); self.assertEqual(s, 201); self.assertEqual(b, {"sku": "A1", "name": "Apple", "price_cents": 120, "reorder_level": 5, "stock": 0}); self.assertEqual(h["location"], "/products/A1")
        self.assertEqual(self.call("GET", "/products/A1")[1], b)
    def test_r03_defaults(self): self.assertEqual(self.product()[1]["reorder_level"], 0)
    def test_r03_duplicate_sku(self):
        self.product(); s, b, h = self.product(name="other"); self.assertEqual((s, b), (409, {"error": "sku already exists"})); self.assertEqual(self.call("GET", "/products/A1")[1]["name"], "Apple")
    def test_r03_unknown_sku_is_404(self): self.assertEqual(self.call("GET", "/products/nope")[:2], (404, {"error": "not found"}))
    def test_r03_validation_lists_every_invalid_field(self):
        s, b, h = self.call("POST", "/products", {"sku": "bad sku", "name": "  ", "price_cents": -1, "reorder_level": "x", "color": "red"})
        self.assertEqual((s, b["error"]), (422, "validation failed")); self.assertEqual(set(b["fields"]), {"sku", "name", "price_cents", "reorder_level", "color"}); self.assertEqual(b["fields"]["color"], "unknown field")
    def test_r03_missing_required_fields(self): s, b, h = self.call("POST", "/products", {}); self.assertEqual(s, 422); self.assertEqual(set(b["fields"]), {"sku", "name", "price_cents"})
    def test_r03_integers_must_be_real_integers(self):
        for v in (True, 1.5, "10", None, [1]):
            s, b, h = self.call("POST", "/products", {"sku": "A", "name": "a", "price_cents": v}); self.assertEqual(s, 422, repr(v)); self.assertIn("price_cents", b["fields"])
    def test_r03_sku_rules(self):
        for sku in ("", "a b", "x" * 33, "ä", "a/b", 5, None): self.assertEqual(self.call("POST", "/products", {"sku": sku, "name": "a", "price_cents": 1})[0], 422, repr(sku))
        for sku in ("x" * 32, "A.b_c-9", "0"): self.assertEqual(self.call("POST", "/products", {"sku": sku, "name": "a", "price_cents": 1})[0], 201, sku)
    def test_r03_name_rules(self):
        self.assertEqual(self.call("POST", "/products", {"sku": "a", "name": "x" * 101, "price_cents": 1})[0], 422); self.assertEqual(self.call("POST", "/products", {"sku": "b", "name": "x" * 100, "price_cents": 1})[0], 201)
        self.assertEqual(self.call("POST", "/products", {"sku": "c", "name": 5, "price_cents": 1})[0], 422)
    def test_r03_zero_price_and_reorder_level_are_fine(self): self.assertEqual(self.product("F", "Free", 0, reorder_level=0)[0], 201)
    def test_r03_a_rejected_product_is_not_stored(self): self.call("POST", "/products", {"sku": "A", "name": "", "price_cents": 1}); self.assertEqual(self.call("GET", "/products")[1]["total"], 0)
    def test_r03_skus_are_case_sensitive(self): self.product("abc"); self.assertEqual(self.product("ABC")[0], 201)
    def test_r03_unicode_names(self): self.assertEqual(self.product("U", "Café ✓")[1]["name"], "Café ✓"); self.assertEqual(self.call("GET", "/products/U")[1]["name"], "Café ✓")

class TestPatchDelete(Base):
    def setUp(self): super().setUp(); self.product("A1", "Apple", 100, reorder_level=2)
    def test_r04_patch_some_fields(self): s, b, h = self.call("PATCH", "/products/A1", {"price_cents": 150}); self.assertEqual(s, 200); self.assertEqual((b["price_cents"], b["name"], b["reorder_level"]), (150, "Apple", 2)); self.assertEqual(self.call("GET", "/products/A1")[1]["price_cents"], 150)
    def test_r04_patch_all_fields_and_strip_the_name(self): s, b, h = self.call("PATCH", "/products/A1", {"name": " New ", "price_cents": 1, "reorder_level": 9}); self.assertEqual(b, {"sku": "A1", "name": "New", "price_cents": 1, "reorder_level": 9, "stock": 0})
    def test_r04_empty_patch(self): self.assertEqual(self.call("PATCH", "/products/A1", {})[:2], (400, {"error": "nothing to update"}))
    def test_r04_sku_and_stock_cannot_be_patched(self):
        for f in ("sku", "stock"): s, b, h = self.call("PATCH", "/products/A1", {f: 5}); self.assertEqual(s, 422); self.assertEqual(b["fields"][f], "unknown field")
    def test_r04_invalid_patch_changes_nothing(self):
        s, b, h = self.call("PATCH", "/products/A1", {"name": "ok", "price_cents": -5}); self.assertEqual(s, 422); self.assertEqual(self.call("GET", "/products/A1")[1]["name"], "Apple")
    def test_r04_patch_unknown_sku(self): self.assertEqual(self.call("PATCH", "/products/zz", {"name": "x"})[0], 404)
    def test_r04_delete_an_empty_product(self):
        s, b, h = self.call("DELETE", "/products/A1"); self.assertEqual((s, b), (204, None)); self.assertEqual(self.call("GET", "/products/A1")[0], 404)
    def test_r04_delete_with_stock_is_a_conflict(self):
        self.stock("A1", 3); s, b, h = self.call("DELETE", "/products/A1"); self.assertEqual((s, b), (409, {"error": "product still has stock"})); self.assertEqual(self.call("GET", "/products/A1")[0], 200)
    def test_r04_delete_after_the_stock_is_gone_removes_the_movements_too(self):
        self.stock("A1", 3); self.stock("A1", -3); self.assertEqual(self.call("DELETE", "/products/A1")[0], 204); self.product("A1", "Again"); self.assertEqual(self.call("GET", "/products/A1/movements")[1]["total"], 0)
    def test_r04_delete_unknown_sku(self): self.assertEqual(self.call("DELETE", "/products/zz")[0], 404)
    def test_r04_the_sku_can_be_reused_after_deletion(self): self.call("DELETE", "/products/A1"); self.assertEqual(self.product("A1", "Reborn")[0], 201)
    def test_r04_patch_validation_comes_before_existence(self): self.assertEqual(self.call("PATCH", "/products/zz", {"price_cents": -1})[0], 422)

class TestListing(Base):
    def setUp(self):
        super().setUp()
        for i in range(1, 26): self.product(f"P{i:02d}", f"Widget {i}" if i % 2 else f"Gadget {i}", i)
    def get(self, q=""): s, b, h = self.call("GET", "/products" + q); return s, b
    def test_r05_defaults(self):
        s, b = self.get(); self.assertEqual(s, 200); self.assertEqual((b["total"], b["limit"], b["offset"], len(b["items"])), (25, 20, 0, 20)); self.assertEqual(b["items"][0]["sku"], "P01"); self.assertEqual(b["items"][-1]["sku"], "P20")
    def test_r05_limit_and_offset(self): s, b = self.get("?limit=5&offset=10"); self.assertEqual([i["sku"] for i in b["items"]], ["P11", "P12", "P13", "P14", "P15"]); self.assertEqual((b["limit"], b["offset"], b["total"]), (5, 10, 25))
    def test_r05_the_last_page_is_short(self): s, b = self.get("?limit=10&offset=20"); self.assertEqual(len(b["items"]), 5)
    def test_r05_an_offset_beyond_the_end_gives_an_empty_page(self): s, b = self.get("?offset=100"); self.assertEqual((s, b["items"], b["total"]), (200, [], 25))
    def test_r05_limit_bounds(self):
        self.assertEqual(len(self.get("?limit=100")[1]["items"]), 25); self.assertEqual(len(self.get("?limit=1")[1]["items"]), 1)
        for bad in ("0", "101", "-1", "abc", "1.5", ""): s, b = self.get(f"?limit={bad}"); self.assertEqual((s, b), (400, {"error": "invalid limit"}), bad)
    def test_r05_offset_bounds(self):
        for bad in ("-1", "abc", "1.5", ""): s, b = self.get(f"?offset={bad}"); self.assertEqual((s, b), (400, {"error": "invalid offset"}), bad)
    def test_r05_limit_is_checked_before_offset(self): self.assertEqual(self.get("?limit=0&offset=-1")[1], {"error": "invalid limit"})
    def test_r05_search_by_name_case_insensitive(self): s, b = self.get("?q=GADGET"); self.assertEqual(b["total"], 12); self.assertTrue(all("Gadget" in i["name"] for i in b["items"]))
    def test_r05_search_by_sku(self): s, b = self.get("?q=p1"); self.assertEqual([i["sku"] for i in b["items"]], ["P10", "P11", "P12", "P13", "P14", "P15", "P16", "P17", "P18", "P19"])
    def test_r05_total_counts_matches_before_pagination(self): s, b = self.get("?q=widget&limit=3"); self.assertEqual((b["total"], len(b["items"])), (13, 3))
    def test_r05_search_and_pagination_combine(self): s, b = self.get("?q=widget&limit=5&offset=10"); self.assertEqual([i["sku"] for i in b["items"]], ["P21", "P23", "P25"])
    def test_r05_no_match(self): s, b = self.get("?q=zzz"); self.assertEqual((s, b["items"], b["total"]), (200, [], 0))
    def test_r05_items_are_full_products(self): s, b = self.get("?limit=1"); self.assertEqual(set(b["items"][0]), {"sku", "name", "price_cents", "reorder_level", "stock"})
    def test_r05_walking_all_pages_visits_every_product_once(self):
        seen = []
        for off in range(0, 30, 7): seen += [i["sku"] for i in self.get(f"?limit=7&offset={off}")[1]["items"]]
        self.assertEqual(seen, sorted(seen)); self.assertEqual(len(seen), 25); self.assertEqual(len(set(seen)), 25)
    def test_r05_empty_database(self):
        srv = inv_api.make_server(os.path.join(self.dir, "e.db"), TOKENS).start(); self.addCleanup(srv.stop); s, b, h = self.call("GET", "/products", port=srv.port); self.assertEqual(b, {"items": [], "total": 0, "limit": 20, "offset": 0})

class TestStock(Base):
    def setUp(self): super().setUp(); self.product("A1")
    def test_r06_change_stock(self):
        s, b, h = self.stock("A1", 5, "delivery"); self.assertEqual(s, 200); self.assertEqual((b["sku"], b["stock"]), ("A1", 5)); self.assertIsInstance(b["movement_id"], int)
        s, b, h = self.stock("A1", -2, "sold"); self.assertEqual(b["stock"], 3); self.assertEqual(self.call("GET", "/products/A1")[1]["stock"], 3)
    def test_r06_insufficient_stock(self):
        self.stock("A1", 2); s, b, h = self.stock("A1", -3); self.assertEqual((s, b), (409, {"error": "insufficient stock"})); self.assertEqual(self.call("GET", "/products/A1")[1]["stock"], 2); self.assertEqual(self.call("GET", "/products/A1/movements")[1]["total"], 1)
    def test_r06_removing_everything(self): self.stock("A1", 2); self.assertEqual(self.stock("A1", -2)[1]["stock"], 0)
    def test_r06_validation(self):
        for body, fields in (({"delta": 0, "reason": "x"}, {"delta"}), ({"delta": 1.5, "reason": "x"}, {"delta"}), ({"delta": True, "reason": "x"}, {"delta"}), ({"delta": 1}, {"reason"}), ({"delta": 1, "reason": "  "}, {"reason"}), ({}, {"delta", "reason"}), ({"delta": 1, "reason": "x", "extra": 1}, {"extra"})):
            s, b, h = self.call("POST", "/products/A1/stock", body); self.assertEqual(s, 422, body); self.assertEqual(set(b["fields"]), fields)
    def test_r06_unknown_sku(self): self.assertEqual(self.stock("zz", 1)[0], 404)
    def test_r06_validation_comes_before_existence(self): self.assertEqual(self.call("POST", "/products/zz/stock", {"delta": 0, "reason": "x"})[0], 422)
    def test_r06_movements_listing(self):
        self.stock("A1", 5, "first"); self.stock("A1", -1, "second"); s, b, h = self.call("GET", "/products/A1/movements"); self.assertEqual(s, 200); self.assertEqual(b["total"], 2); self.assertEqual([(m["delta"], m["reason"]) for m in b["items"]], [(5, "first"), (-1, "second")])
        self.assertLess(b["items"][0]["id"], b["items"][1]["id"]); import re; self.assertRegex(b["items"][0]["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"); self.assertEqual(set(b["items"][0]), {"id", "delta", "reason", "ts"})
    def test_r06_movements_pagination(self):
        for n in range(1, 8): self.stock("A1", 1, f"r{n}")
        s, b, h = self.call("GET", "/products/A1/movements?limit=3&offset=2"); self.assertEqual([m["reason"] for m in b["items"]], ["r3", "r4", "r5"]); self.assertEqual((b["total"], b["limit"], b["offset"]), (7, 3, 2))
        self.assertEqual(self.call("GET", "/products/A1/movements?limit=0")[:2], (400, {"error": "invalid limit"}))
    def test_r06_movements_of_an_unknown_product(self): self.assertEqual(self.call("GET", "/products/zz/movements")[0], 404)
    def test_r06_movement_ids_match_the_stock_response(self): s, b, h = self.stock("A1", 4); self.assertEqual(self.call("GET", "/products/A1/movements")[1]["items"][0]["id"], b["movement_id"])
    def test_r06_the_reason_is_stripped(self): self.stock("A1", 1, "  padded  "); self.assertEqual(self.call("GET", "/products/A1/movements")[1]["items"][0]["reason"], "padded")
    def test_r06_empty_movements(self): self.assertEqual(self.call("GET", "/products/A1/movements")[1], {"items": [], "total": 0, "limit": 20, "offset": 0})
    def test_r06_stock_is_per_product(self): self.product("B1", "B"); self.stock("A1", 5); self.assertEqual(self.call("GET", "/products/B1")[1]["stock"], 0)

class TestProtocol(Base):
    def test_r07_invalid_json(self):
        for raw in (b"{nope", b"", b"\xff\xfe"): self.assertEqual(self.call("POST", "/products", raw=raw)[:2], (400, {"error": "invalid JSON"}), raw)
        self.assertEqual(self.call("PATCH", "/products/x", raw=b"{bad")[:2], (400, {"error": "invalid JSON"}))
    def test_r07_a_missing_body_is_invalid_json(self): self.assertEqual(self.call("POST", "/products")[:2], (400, {"error": "invalid JSON"})); self.assertEqual(self.call("PATCH", "/products/x")[:2], (400, {"error": "invalid JSON"}))
    def test_r07_the_body_must_be_an_object(self):
        for raw in (b"[1]", b'"s"', b"5", b"null", b"true"): self.assertEqual(self.call("POST", "/products", raw=raw)[:2], (400, {"error": "body must be a JSON object"}), raw)
    def test_r07_too_large_a_body(self):
        s, b, h = self.call("POST", "/products", raw=b'{"sku": "' + b"x" * 1_100_000 + b'"}'); self.assertEqual((s, b), (413, {"error": "request too large"}))
    def test_r07_a_body_just_under_the_limit_is_read(self):
        pad = b"x" * (1_048_576 - 40); raw = b'{"sku": "A", "name": "a", "price_cents": 1, "z": "' + pad + b'"}'
        s, b, h = self.call("POST", "/products", raw=raw[:1_048_576] if len(raw) > 1_048_576 else raw); self.assertIn(s, (400, 422))
    def test_r07_the_server_survives_a_413(self): self.call("POST", "/products", raw=b"x" * 1_200_000); self.assertEqual(self.call("GET", "/health", token=None)[0], 200)
    def test_r07_method_and_path_errors(self):
        self.assertEqual(self.call("GET", "/")[0], 404); self.assertEqual(self.call("GET", "/products/a/b/c")[0], 404); self.assertEqual(self.call("DELETE", "/products")[0], 405); self.assertEqual(self.call("PUT", "/products/x", {})[0], 405)
    def test_r07_keep_alive_connections_serve_several_requests(self):
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        try:
            for _ in range(3): c.request("GET", "/products", headers={"Authorization": "Bearer adm-token"}); r = c.getresponse(); r.read(); self.assertEqual(r.status, 200)
        finally: c.close()
    def test_r07_no_request_log_lines_on_stderr(self):
        import contextlib, io; buf = io.StringIO()
        with contextlib.redirect_stderr(buf): self.call("GET", "/products"); self.call("GET", "/nope")
        self.assertEqual(buf.getvalue(), "")
    def test_r07_error_bodies_are_always_json_objects_with_an_error_key(self):
        for m, p, kw in (("GET", "/nope", {"token": None}), ("GET", "/products", {"token": None}), ("DELETE", "/products", {}), ("POST", "/products", {"raw": b"x"}), ("GET", "/products/zz", {})):
            s, b, h = self.call(m, p, **kw); self.assertEqual(h["content-type"], "application/json"); self.assertIn("error", b)

class TestPersistence(Base):
    def test_r08_data_survives_a_restart(self):
        self.product("A1", "Apple", 100, reorder_level=3); self.stock("A1", 7, "in"); self.stock("A1", -2, "out"); self.server.stop()
        srv = inv_api.make_server(self.db, TOKENS).start(); self.addCleanup(srv.stop)
        s, b, h = self.call("GET", "/products/A1", port=srv.port); self.assertEqual(b, {"sku": "A1", "name": "Apple", "price_cents": 100, "reorder_level": 3, "stock": 5})
        m = self.call("GET", "/products/A1/movements", port=srv.port)[1]; self.assertEqual([x["delta"] for x in m["items"]], [7, -2]); self.assertEqual(self.call("POST", "/products/A1/stock", {"delta": 1, "reason": "x"}, port=srv.port)[1]["movement_id"], m["items"][-1]["id"] + 1)
    def test_r08_a_restart_can_be_done_with_other_tokens(self):
        self.product(); self.server.stop(); srv = inv_api.make_server(self.db, {"new": "reader"}).start(); self.addCleanup(srv.stop)
        self.assertEqual(self.call("GET", "/products/A1", token="new", port=srv.port)[0], 200); self.assertEqual(self.call("GET", "/products/A1", token="adm-token", port=srv.port)[0], 401)
    def test_r08_concurrent_additions_lose_nothing(self):
        self.product(); results = []
        def work():
            for _ in range(2): results.append(self.stock("A1", 1)[0])
        ts = [threading.Thread(target=work) for _ in range(10)]; [t.start() for t in ts]; [t.join(120) for t in ts]
        self.assertEqual(results, [200] * 20); self.assertEqual(self.call("GET", "/products/A1")[1]["stock"], 20); self.assertEqual(self.call("GET", "/products/A1/movements?limit=100")[1]["total"], 20)
    def test_r08_no_overselling_under_concurrency(self):
        self.product(); self.stock("A1", 5); results, lock = [], threading.Lock()
        def work():
            s = self.stock("A1", -1)[0]
            with lock: results.append(s)
        ts = [threading.Thread(target=work) for _ in range(12)]; [t.start() for t in ts]; [t.join(120) for t in ts]
        self.assertEqual((results.count(200), results.count(409)), (5, 7)); self.assertEqual(self.call("GET", "/products/A1")[1]["stock"], 0); self.assertEqual(self.call("GET", "/products/A1/movements?limit=100")[1]["total"], 6)
    def test_r08_the_stock_equals_the_sum_of_the_movements(self):
        self.product()
        for d in (5, -2, 7, -4, 1): self.stock("A1", d)
        m = self.call("GET", "/products/A1/movements")[1]["items"]; self.assertEqual(sum(x["delta"] for x in m), self.call("GET", "/products/A1")[1]["stock"])
    def test_r08_concurrent_creation_of_the_same_sku_creates_it_once(self):
        results, lock = [], threading.Lock()
        def work():
            s = self.product("DUP")[0]
            with lock: results.append(s)
        ts = [threading.Thread(target=work) for _ in range(8)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        self.assertEqual((results.count(201), results.count(409)), (1, 7))
    def test_r08_parallel_reads_while_writing(self):
        self.product(); errors = []
        def reader():
            for _ in range(20):
                s = self.call("GET", "/products")[0]
                if s != 200: errors.append(s)
        def writer():
            for _ in range(10): self.stock("A1", 1)
        ts = [threading.Thread(target=reader) for _ in range(4)] + [threading.Thread(target=writer)]; [t.start() for t in ts]; [t.join(120) for t in ts]; self.assertEqual(errors, [])
