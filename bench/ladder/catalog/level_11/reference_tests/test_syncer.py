import json, os, random, tempfile, threading, unittest
import syncer
from syncer import ConnectionLost, Device, Link, SyncReport, SyncServer, fold

def op(op_id="d-1", device=None, ts=1, base=0, type="set", key="k", value=1):
    if device is None: device = op_id.rsplit("-", 1)[0] if isinstance(op_id, str) and "-" in op_id else "d"          # by default the device is the prefix of the op id
    return {"op_id": op_id, "device": device, "ts": ts, "base": base, "type": type, "key": key, "value": value}

class Base(unittest.TestCase):
    def setUp(self): self.dir = tempfile.mkdtemp(prefix="sync-test-"); self.server = SyncServer()
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def device(self, name, server=None, state=True, link=None):
        link = link or Link(server or self.server); d = Device(name, link, os.path.join(self.dir, f"{name}.json") if state is True else None); d.link = link; return d
    def converge(self, *devices, rounds=3):
        for _ in range(rounds):
            for d in devices: self.assertTrue(d.sync().completed)

class TestOperations(Base):
    def test_r01_connection_lost_is_a_connection_error(self): self.assertTrue(issubclass(ConnectionLost, ConnectionError))
    def test_r01_valid_operations_are_accepted(self):
        ans = self.server.push("d", [op("d-1"), op("d-2", type="delete", value=None, key="z"), op("d-3", type="incr", key="c", value=5)]); self.assertEqual(ans["accepted"], ["d-1", "d-2", "d-3"]); self.assertEqual(ans["rejected"], [])
    def test_r01_malformed_operations_are_rejected(self):
        bad = [None, "x", {}, op(op_id=""), op(op_id=5), op(device=""), op(ts=0), op(ts=True), op(ts="1"), op(base=-1), op(type="upsert"), op(key=""), op(key="k" * 201), op(key=5), op(type="incr", value=0), op(type="incr", value=True), op(type="incr", value=1.5), op(type="incr", value="2"), op(type="delete", value=3),
               op(value={1, 2}), {k: v for k, v in op().items() if k != "ts"}, op(device="other")]
        for o in bad:
            ans = self.server.push("d", [o]); self.assertEqual(ans["accepted"], [], repr(o)); self.assertEqual(len(ans["rejected"]), 1, repr(o)); self.assertEqual(ans["rejected"][0]["reason"], "invalid operation", repr(o))
        self.assertEqual(self.server.latest_seq(), 0)
    def test_r01_the_key_length_limit_is_inclusive(self): self.assertEqual(self.server.push("d", [op(key="k" * 200)])["accepted"], ["d-1"])
    def test_r01_type_mismatch_between_register_and_counter(self):
        self.server.push("d", [op("d-1", key="a"), op("d-2", type="incr", key="b", value=1)])
        ans = self.server.push("d", [op("d-3", type="incr", key="a", value=1), op("d-4", type="set", key="b", value=1), op("d-5", type="delete", value=None, key="b")]); self.assertEqual(ans["accepted"], [])
        self.assertEqual([(r["op_id"], r["reason"]) for r in ans["rejected"]], [("d-3", "type mismatch"), ("d-4", "type mismatch"), ("d-5", "type mismatch")])
    def test_r01_the_first_operation_in_a_batch_decides_the_kind(self):
        ans = self.server.push("d", [op("d-1", key="x", type="incr", value=1), op("d-2", key="x", type="set", value=3)]); self.assertEqual((ans["accepted"], [r["reason"] for r in ans["rejected"]]), (["d-1"], ["type mismatch"]))
    def test_r01_a_deleted_register_stays_a_register(self):
        self.server.push("d", [op("d-1", type="delete", value=None, key="x")]); self.assertEqual(self.server.push("d", [op("d-2", type="incr", key="x", value=1)])["rejected"][0]["reason"], "type mismatch")
    def test_r01_values_can_be_any_json(self):
        vals = [None, True, 3, 2.5, "s", [1, [2]], {"a": {"b": None}}]; self.server.push("d", [op(f"d-{i}", key=f"k{i}", ts=i + 1, value=v) for i, v in enumerate(vals)]); snap = self.server.snapshot(); self.assertEqual([snap[f"k{i}"] for i in range(len(vals))], vals)

class TestServer(Base):
    def test_r02_seq_numbers_follow_acceptance_order(self):
        self.server.push("d", [op("d-1"), op("d-2", key="b")]); self.server.push("e", [op("e-1", device="e", key="c")]); got = self.server.pull(0)["ops"]; self.assertEqual([(o["op_id"], o["seq"]) for o in got], [("d-1", 1), ("d-2", 2), ("e-1", 3)])
    def test_r02_push_answer_shape(self):
        ans = self.server.push("d", [op("d-1")]); self.assertEqual(ans, {"accepted": ["d-1"], "duplicates": [], "rejected": [], "latest_seq": 1})
    def test_r02_duplicates_change_nothing(self):
        self.server.push("d", [op("d-1", value=1)]); ans = self.server.push("d", [op("d-1", value=999), op("d-2", key="b")]); self.assertEqual((ans["accepted"], ans["duplicates"], ans["latest_seq"]), (["d-2"], ["d-1"], 2)); self.assertEqual(self.server.snapshot()["k"], 1)
    def test_r02_duplicates_inside_one_call(self): ans = self.server.push("d", [op("d-1"), op("d-1")]); self.assertEqual((ans["accepted"], ans["duplicates"]), (["d-1"], ["d-1"])); self.assertEqual(self.server.latest_seq(), 1)
    def test_r02_a_bad_operation_does_not_stop_the_good_ones(self):
        ans = self.server.push("d", [op("d-1"), op("d-2", ts=0), op("d-3", key="c")]); self.assertEqual((ans["accepted"], len(ans["rejected"])), (["d-1", "d-3"], 1)); self.assertEqual(ans["rejected"][0]["op_id"], "d-2")
    def test_r02_pull_pages(self):
        self.server.push("d", [op(f"d-{i}", key=f"k{i}", ts=i) for i in range(1, 8)])
        p = self.server.pull(0, 3); self.assertEqual(([o["seq"] for o in p["ops"]], p["more"], p["latest_seq"]), ([1, 2, 3], True, 7)); p = self.server.pull(3, 3); self.assertEqual(([o["seq"] for o in p["ops"]], p["more"]), ([4, 5, 6], True))
        p = self.server.pull(6, 3); self.assertEqual(([o["seq"] for o in p["ops"]], p["more"]), ([7], False)); p = self.server.pull(7); self.assertEqual((p["ops"], p["more"]), ([], False))
    def test_r02_pull_exactly_at_the_page_boundary(self): self.server.push("d", [op(f"d-{i}", key=f"k{i}", ts=i) for i in range(1, 4)]); self.assertFalse(self.server.pull(0, 3)["more"]); self.assertTrue(self.server.pull(0, 2)["more"])
    def test_r02_pull_default_limit_is_100(self):
        self.server.push("d", [op(f"d-{i}", key=f"k{i}", ts=i) for i in range(1, 151)]); p = self.server.pull(0); self.assertEqual((len(p["ops"]), p["more"]), (100, True))
    def test_r02_pulled_operations_are_copies(self): self.server.push("d", [op("d-1")]); o = self.server.pull(0)["ops"][0]; o["value"] = 99; self.assertEqual(self.server.pull(0)["ops"][0]["value"], 1)
    def test_r02_empty_server(self): self.assertEqual((self.server.latest_seq(), self.server.snapshot(), self.server.pull(0)), (0, {}, {"ops": [], "latest_seq": 0, "more": False}))
    def test_r02_stored_operations_keep_all_their_fields(self):
        self.server.push("d", [op("d-1", ts=7, base=3, value="v")]); o = self.server.pull(0)["ops"][0]; self.assertEqual(o, {"op_id": "d-1", "device": "d", "ts": 7, "base": 3, "type": "set", "key": "k", "value": "v", "seq": 1})
    def test_r02_concurrent_pushes_are_safe(self):
        def work(n): self.server.push(f"t{n}", [op(f"t{n}-{i}", device=f"t{n}", key=f"k{n}-{i}", ts=i + 1) for i in range(1, 21)])
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        seqs = [o["seq"] for o in self.server.pull(0, 1000)["ops"]]; self.assertEqual(seqs, list(range(1, 121)))

class TestResolution(Base):
    def test_r03_last_writer_wins_by_timestamp(self): self.server.push("a", [op("a-1", "a", 5, value="new"), op("a-2", "a", 3, value="old")]); self.assertEqual(self.server.snapshot(), {"k": "new"})
    def test_r03_equal_timestamps_are_broken_by_device_name(self): self.server.push("a", [op("a-1", "a", 4, value="from a")]); self.server.push("b", [op("b-1", "b", 4, value="from b")]); self.assertEqual(self.server.snapshot(), {"k": "from b"})
    def test_r03_device_names_compare_as_strings(self): self.server.push("a10", [op("a10-1", "a10", 4, value="x")]); self.server.push("a9", [op("a9-1", "a9", 4, value="y")]); self.assertEqual(self.server.snapshot(), {"k": "y"})
    def test_r03_arrival_order_does_not_matter(self):
        s2 = SyncServer(); a, b = op("a-1", "a", 5, value="A"), op("b-1", "b", 7, value="B"); self.server.push("a", [a]); self.server.push("b", [b]); s2.push("b", [b]); s2.push("a", [a]); self.assertEqual(self.server.snapshot(), s2.snapshot())
    def test_r03_a_later_delete_removes_the_key(self): self.server.push("a", [op("a-1", "a", 1, value=1), op("a-2", "a", 2, type="delete", value=None)]); self.assertEqual(self.server.snapshot(), {})
    def test_r03_a_later_set_beats_an_earlier_delete(self): self.server.push("a", [op("a-1", "a", 5, type="delete", value=None)]); self.server.push("b", [op("b-1", "b", 6, value="back")]); self.assertEqual(self.server.snapshot(), {"k": "back"})
    def test_r03_a_delete_that_arrives_first_still_loses_to_a_later_timestamp(self): self.server.push("b", [op("b-1", "b", 6, value="v")]); self.server.push("a", [op("a-1", "a", 5, type="delete", value=None)]); self.assertEqual(self.server.snapshot(), {"k": "v"})
    def test_r03_counters_sum_all_increments(self): self.server.push("a", [op("a-1", "a", 1, type="incr", key="c", value=5), op("a-2", "a", 2, type="incr", key="c", value=-2)]); self.server.push("b", [op("b-1", "b", 1, type="incr", key="c", value=10)]); self.assertEqual(self.server.snapshot(), {"c": 13})
    def test_r03_a_counter_at_zero_still_exists(self): self.server.push("a", [op("a-1", "a", 1, type="incr", key="c", value=3), op("a-2", "a", 2, type="incr", key="c", value=-3)]); self.assertEqual(self.server.snapshot(), {"c": 0})
    def test_r03_snapshot_is_sorted_by_key(self): self.server.push("a", [op("a-1", key="b"), op("a-2", key="a", ts=2), op("a-3", key="c", ts=3)]); self.assertEqual(list(self.server.snapshot()), ["a", "b", "c"])
    def test_r03_fold_ignores_duplicates_and_order(self):
        ops = [op("a-1", "a", 1, value=1), op("a-2", "a", 2, value=2), op("c-1", "a", 1, type="incr", key="n", value=4)]; self.assertEqual(fold(ops), {"k": 2, "n": 4}); self.assertEqual(fold(ops + ops + ops[::-1]), {"k": 2, "n": 4}); self.assertEqual(fold([]), {})
    def test_r03_fold_on_a_set_of_operations_equals_the_server_snapshot(self):
        ops = [op(f"a-{i}", "a" if i % 2 else "b", i, value=i, key=f"k{i % 3}") for i in range(1, 12)]; self.server.push("a", [o for o in ops if o["device"] == "a"]); self.server.push("b", [o for o in ops if o["device"] == "b"]); self.assertEqual(fold(ops), self.server.snapshot())
    def test_r03_values_are_returned_as_stored(self): self.server.push("a", [op("a-1", value={"n": [1, 2]})]); self.assertEqual(self.server.snapshot()["k"], {"n": [1, 2]})
    def test_r03_a_deleted_then_reset_counter_key_is_rejected_for_set(self):
        self.server.push("a", [op("a-1", type="incr", key="c", value=1)])
        self.assertEqual(self.server.push("a", [op("a-2", ts=9, key="c")])["rejected"][0]["reason"], "type mismatch"); self.assertEqual(self.server.snapshot(), {"c": 1})

class TestDeviceLocal(Base):
    def test_r04_op_ids_and_clock(self):
        d = self.device("phone"); self.assertEqual(d.set("a", 1), "phone-1"); self.assertEqual(d.delete("a"), "phone-2"); self.assertEqual(d.increment("c"), "phone-3"); p = d.pending(); self.assertEqual([(o["ts"], o["base"], o["type"]) for o in p], [(1, 0, "set"), (2, 0, "delete"), (3, 0, "incr")])
    def test_r04_read_your_writes_before_syncing(self):
        d = self.device("a"); d.set("x", 1); d.set("y", 2); d.delete("y"); d.increment("c", 3); d.increment("c", 4); self.assertEqual((d.get("x"), d.get("y"), d.get("y", "dflt"), d.get("c")), (1, None, "dflt", 7)); self.assertEqual(d.items(), {"c": 7, "x": 1})
    def test_r04_items_are_sorted(self): d = self.device("a"); d.set("b", 1); d.set("a", 2); self.assertEqual(list(d.items()), ["a", "b"])
    def test_r04_a_device_does_not_change_the_server_until_it_syncs(self): d = self.device("a"); d.set("x", 1); self.assertEqual(self.server.snapshot(), {}); self.assertEqual(d.link.calls, 0)
    def test_r04_invalid_input(self):
        d = self.device("a"); d.set("ok", 1); d.increment("cnt")
        for call in (lambda: d.set("", 1), lambda: d.set(5, 1), lambda: d.set("k" * 201, 1), lambda: d.set("x", {1, 2}), lambda: d.increment("n", 0), lambda: d.increment("n", True), lambda: d.increment("n", 1.5), lambda: d.increment("n", "1"), lambda: d.delete(""), lambda: d.set("cnt", 1), lambda: d.increment("ok"), lambda: d.delete("cnt")):
            with self.assertRaises(ValueError): call()
        self.assertEqual(len(d.pending()), 2); self.assertEqual(d.lamport, 2)
    def test_r04_invalid_device_ids(self):
        for bad in ("", "  ", "a/b", 5, None):
            with self.assertRaises(ValueError): Device(bad, Link(self.server))
    def test_r04_the_kind_check_uses_everything_the_device_knows(self):
        a, b = self.device("a"), self.device("b"); a.increment("cnt"); a.sync(); b.sync()
        with self.assertRaises(ValueError): b.set("cnt", 1)
    def test_r04_the_default_is_returned_for_deleted_and_unknown_keys(self): d = self.device("a"); d.set("x", 1); d.delete("x"); self.assertEqual((d.get("x", 0), d.get("never", "n")), (0, "n"))
    def test_r04_pending_returns_copies(self): d = self.device("a"); d.set("x", 1); d.pending()[0]["value"] = 99; self.assertEqual(d.get("x"), 1)
    def test_r04_a_counter_and_a_register_on_different_keys(self): d = self.device("a"); d.set("r", "v"); d.increment("c", 2); self.assertEqual(d.items(), {"c": 2, "r": "v"})
    def test_r04_local_state_is_in_memory_when_there_is_no_path(self):
        d = self.device("a", state=False); d.set("x", 1); self.assertEqual(os.listdir(self.dir), [])

class TestSync(Base):
    def test_r05_push_then_pull(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.increment("n", 5); r = a.sync(); self.assertEqual((r.completed, r.pushed, r.pulled, r.duplicates, r.rejected, r.conflicts, r.error), (True, 2, 2, 0, [], 0, None)); r = b.sync(); self.assertEqual((r.pushed, r.pulled), (0, 2)); self.assertEqual(b.items(), {"n": 5, "x": 1})
    def test_r05_report_type(self): self.assertIsInstance(self.device("a").sync(), SyncReport); self.assertEqual(list(SyncReport.__dataclass_fields__), ["completed", "pushed", "pulled", "duplicates", "rejected", "conflicts", "error"])
    def test_r05_a_second_sync_transfers_nothing(self):
        a = self.device("a"); a.set("x", 1); a.sync(); calls = a.link.calls; r = a.sync(); self.assertEqual((r.pushed, r.pulled, r.completed), (0, 0, True)); self.assertEqual(a.link.calls, calls + 1)
    def test_r05_nothing_to_push_means_no_push_call(self): a = self.device("a"); a.sync(); self.assertEqual(a.link.log, ["pull"])
    def test_r05_incremental_pull_asks_only_for_what_is_new(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.sync(); b.sync(); a.set("y", 2); a.sync(); r = b.sync(); self.assertEqual((r.pulled, b.last_seq), (1, 2)); self.assertEqual(b.items(), {"x": 1, "y": 2})
    def test_r05_pull_since_is_the_last_seq(self):
        a = self.device("a"); a.set("x", 1); a.sync(); seen = []; orig = a.link.pull
        def spy(since, limit=100): seen.append((since, limit)); return orig(since, limit)
        a.link.pull = spy; a.sync(page=7); self.assertEqual(seen, [(1, 7)])
    def test_r05_batches_and_pages(self):
        a, b = self.device("a"), self.device("b")
        for i in range(25): a.set(f"k{i:02d}", i)
        r = a.sync(batch=10, page=8); self.assertEqual(a.link.log.count("push"), 3); self.assertEqual((r.pushed, r.pulled), (25, 25)); self.assertEqual(a.link.log.count("pull"), 4)
        r = b.sync(page=10); self.assertEqual((r.pulled, b.link.log.count("pull")), (25, 3)); self.assertEqual(b.items(), a.items())
    def test_r05_operations_are_pushed_oldest_first(self):
        a = self.device("a"); [a.set("k", i) for i in range(5)]; a.sync(batch=2); self.assertEqual([o["value"] for o in self.server.pull(0)["ops"]], [0, 1, 2, 3, 4])
    def test_r05_own_operations_received_are_not_counted_twice(self):
        a = self.device("a"); a.increment("n", 5); a.sync(); a.sync(); self.assertEqual(a.get("n"), 5); self.assertEqual(len(a.known), 1)
    def test_r05_the_lamport_clock_follows_what_is_received(self):
        a, b = self.device("a"), self.device("b")
        for _ in range(10): a.set("x", 1)
        a.sync(); b.sync(); self.assertEqual(b.lamport, 10); b.set("y", 1); self.assertEqual(b.pending()[0]["ts"], 11)
    def test_r05_base_is_the_last_seq_at_creation(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.sync(); b.sync(); b.set("y", 1); self.assertEqual(b.pending()[0]["base"], 1)
    def test_r05_a_later_write_wins_after_syncing(self):
        a, b = self.device("a"), self.device("b"); a.set("x", "from a"); a.sync(); b.sync(); b.set("x", "from b"); b.sync(); a.sync(); self.assertEqual((a.get("x"), b.get("x"), self.server.snapshot()["x"]), ("from b", "from b", "from b"))
    def test_r05_rejected_operations_are_dropped_and_reported(self):
        a = self.device("a"); a.increment("n"); a.sync(); b = self.device("b"); b.sync(); b.outbox.append(op("b-1", "b", 5, key="n", value=1)); r = b.sync(); self.assertEqual([x["reason"] for x in r.rejected], ["type mismatch"]); self.assertEqual(b.pending(), []); self.assertTrue(r.completed)
    def test_r05_ordering_of_push_before_pull(self): a = self.device("a"); a.set("x", 1); a.sync(); self.assertEqual(a.link.log[:2], ["push", "pull"])
    def test_r05_after_restart_without_state_own_ids_do_not_repeat(self):
        a = self.device("a", state=False); a.set("x", 1); a.set("x", 2); a.sync(); a2 = Device("a", Link(self.server)); a2.sync(); self.assertEqual(a2.set("x", 3), "a-3"); self.assertEqual(a2.pending()[0]["ts"], 3)
    def test_r05_two_devices_with_equal_clocks_and_different_names(self):
        a, b = self.device("a"), self.device("b"); a.set("x", "a"); b.set("x", "b"); self.converge(a, b); self.assertEqual((a.get("x"), b.get("x")), ("b", "b"))

class TestInterruptions(Base):
    def test_r06_link_counts_calls_and_validates_where(self):
        l = Link(self.server); l.push("d", []); l.pull(0); self.assertEqual(l.calls, 2)
        with self.assertRaises(ValueError): l.fail_next(1, "somewhere")
    def test_r06_a_lost_request_raises_and_the_server_never_sees_it(self):
        l = Link(self.server); l.fail_next(1, "request")
        with self.assertRaises(ConnectionLost): l.push("d", [op("d-1")])
        self.assertEqual(self.server.latest_seq(), 0); l.push("d", [op("d-1")]); self.assertEqual(self.server.latest_seq(), 1)
    def test_r06_a_lost_response_raises_but_the_server_has_handled_the_call(self):
        l = Link(self.server); l.fail_next(1, "response")
        with self.assertRaises(ConnectionLost): l.push("d", [op("d-1")])
        self.assertEqual(self.server.latest_seq(), 1)
    def test_r06_fail_next_counts_down(self): l = Link(self.server); l.fail_next(2); [self.assertRaises(ConnectionLost, l.pull, 0) for _ in range(2)]; l.pull(0)
    def test_r06_sync_never_raises_on_connection_errors(self):
        a = self.device("a"); a.set("x", 1); a.link.fail_next(1, "request"); r = a.sync(); self.assertEqual((r.completed, r.pushed, bool(r.error)), (False, 0, True)); self.assertEqual(len(a.pending()), 1)
    def test_r06_a_lost_ack_does_not_duplicate_or_lose_anything(self):
        a = self.device("a"); a.increment("n", 5); a.link.fail_next(1, "response"); r = a.sync(); self.assertFalse(r.completed); self.assertEqual(len(a.pending()), 1); self.assertEqual(self.server.snapshot(), {"n": 5})
        r = a.sync(); self.assertEqual((r.completed, r.pushed, r.duplicates), (True, 0, 1)); self.assertEqual((a.pending(), a.get("n"), self.server.snapshot()), ([], 5, {"n": 5})); self.assertEqual(self.server.latest_seq(), 1)
    def test_r06_a_lost_request_is_simply_retried(self): a = self.device("a"); a.increment("n", 5); a.link.fail_next(1, "request"); a.sync(); r = a.sync(); self.assertEqual((r.completed, r.pushed, r.duplicates), (True, 1, 0)); self.assertEqual(self.server.snapshot(), {"n": 5})
    def test_r06_interrupted_between_push_chunks_resumes(self):
        a = self.device("a")
        for i in range(10): a.set(f"k{i}", i)
        orig = a.link.push; n = {"c": 0}
        def flaky(did, ops):
            n["c"] += 1
            if n["c"] == 2: raise ConnectionLost("cut")
            return orig(did, ops)
        a.link.push = flaky; r = a.sync(batch=4); self.assertEqual((r.completed, r.pushed, len(a.pending())), (False, 4, 6)); a.link.push = orig; r = a.sync(batch=4); self.assertEqual((r.completed, r.pushed), (True, 6)); self.assertEqual(len(self.server.snapshot()), 10)
    def test_r06_interrupted_during_the_pull_resumes_from_the_last_applied_page(self):
        a, b = self.device("a"), self.device("b")
        for i in range(10): a.set(f"k{i}", i)
        a.sync(); orig = b.link.pull; n = {"c": 0}
        def flaky(since, limit=100):
            n["c"] += 1
            if n["c"] == 3: raise ConnectionLost("cut")
            return orig(since, limit)
        b.link.pull = flaky; r = b.sync(page=3); self.assertEqual((r.completed, r.pulled, b.last_seq, len(b.items())), (False, 6, 6, 6)); b.link.pull = orig; r = b.sync(page=3); self.assertEqual((r.completed, r.pulled, b.last_seq), (True, 4, 10)); self.assertEqual(b.items(), a.items())
    def test_r06_a_lost_pull_response_just_repeats_the_page(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.increment("n", 4); a.sync(); b.link.fail_next(1, "response"); r = b.sync(); self.assertFalse(r.completed); self.assertEqual(b.last_seq, 0); r = b.sync(); self.assertEqual((r.completed, r.pulled), (True, 2)); self.assertEqual(b.items(), {"n": 4, "x": 1})
    def test_r06_the_device_stays_usable_after_a_failure(self): a = self.device("a"); a.set("x", 1); a.link.fail_next(1); a.sync(); a.set("y", 2); self.assertEqual(a.items(), {"x": 1, "y": 2}); self.converge(a); self.assertEqual(self.server.snapshot(), {"x": 1, "y": 2})
    def test_r06_failures_at_every_possible_call_still_converge(self):
        for fail_at in range(1, 8):
            for where in ("request", "response"):
                server = SyncServer(); a = Device("a", Link(server)); b = Device("b", Link(server))
                for i in range(6): a.set(f"k{i}", i); a.increment("n")
                b.set("k0", "b"); calls = {"n": 0}
                def arm(dev):
                    orig = dev.link._call
                    def wrapped(name, *args):
                        calls["n"] += 1
                        if calls["n"] == fail_at: dev.link.fail_next(1, where)
                        return orig(name, *args)
                    dev.link._call = wrapped
                arm(a); arm(b)
                for _ in range(6): a.sync(batch=3, page=3); b.sync(batch=3, page=3)
                self.assertEqual((a.items(), b.items()), (server.snapshot(), server.snapshot()), (fail_at, where)); self.assertEqual(server.snapshot()["n"], 6, (fail_at, where))
    def test_r06_a_counter_is_never_incremented_twice(self):
        a = self.device("a"); a.increment("n", 7)
        for _ in range(4): a.link.fail_next(1, "response"); a.sync()
        a.sync(); self.assertEqual((self.server.snapshot()["n"], a.get("n")), (7, 7))

class TestConflicts(Base):
    def concurrent(self, va="from a", vb="from b"):
        a, b = self.device("a"), self.device("b"); a.set("x", va); b.set("x", vb); a.sync(); b.sync(); a.sync(); return a, b
    def test_r07_concurrent_writes_are_recorded_on_both_devices(self):
        a, b = self.concurrent(); self.assertEqual(len(a.conflicts), 1); self.assertEqual(len(b.conflicts), 1)
        ca, cb = a.conflicts[0], b.conflicts[0]; self.assertEqual((ca["key"], ca["local_op"], ca["remote_op"], ca["winner_op"], ca["value"]), ("x", "a-1", "b-1", "b-1", "from b")); self.assertEqual((cb["local_op"], cb["remote_op"], cb["winner_op"], cb["value"]), ("b-1", "a-1", "b-1", "from b"))
        self.assertEqual(sorted(ca), ["key", "local_op", "remote_op", "value", "winner_op"])
    def test_r07_the_report_counts_conflicts(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); b.set("x", 2); a.sync(); r = b.sync(); self.assertEqual(r.conflicts, 1); self.assertEqual(b.sync().conflicts, 0)
    def test_r07_a_winning_delete_has_value_none(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); b.set("pad", 1); b.set("pad", 2); b.delete("x"); self.converge(a, b); self.assertEqual(b.conflicts[0]["value"], None); self.assertNotIn("x", a.items())
    def test_r07_writes_that_saw_each_other_are_no_conflict(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.sync(); b.sync(); b.set("x", 2); b.sync(); a.sync(); self.assertEqual((a.conflicts, b.conflicts), ([], []))
    def test_r07_different_keys_do_not_conflict(self): a, b = self.device("a"), self.device("b"); a.set("x", 1); b.set("y", 1); self.converge(a, b); self.assertEqual((a.conflicts, b.conflicts), ([], []))
    def test_r07_counters_never_conflict(self): a, b = self.device("a"), self.device("b"); a.increment("n"); b.increment("n"); self.converge(a, b); self.assertEqual((a.conflicts, b.conflicts, a.get("n")), ([], [], 2))
    def test_r07_a_device_does_not_conflict_with_itself(self): a = self.device("a"); a.set("x", 1); a.set("x", 2); a.sync(); a.sync(); self.assertEqual(a.conflicts, [])
    def test_r07_the_lower_timestamp_loses_either_way(self):
        a, b = self.device("a"), self.device("b")
        for i in range(5): a.set("pad", i)
        a.set("x", "late clock"); b.set("x", "early clock"); self.converge(a, b); self.assertEqual(a.get("x"), "late clock"); self.assertEqual(b.conflicts[0]["winner_op"], "a-6")
    def test_r07_three_devices_record_pairwise_conflicts(self):
        a, b, c = self.device("a"), self.device("b"), self.device("c"); a.set("x", 1); b.set("x", 2); c.set("x", 3); self.converge(a, b, c); self.assertEqual((len(a.conflicts), len(b.conflicts), len(c.conflicts)), (2, 2, 2)); self.assertEqual({d.get("x") for d in (a, b, c)}, {3})
    def test_r07_clear_conflicts(self): a, b = self.concurrent(); self.assertEqual(b.clear_conflicts(), 1); self.assertEqual(b.conflicts, []); self.assertEqual(b.clear_conflicts(), 0)
    def test_r07_a_pending_local_write_conflicts_with_a_remote_one_on_pull(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.sync(); b.set("x", 2); b.link.fail_next(1, "request"); b.sync(); b.link.fail_next(0); b.sync(); self.assertEqual(len(b.conflicts), 1)
    def test_r07_conflicts_survive_a_restart(self):
        a, b = self.concurrent(); b2 = Device("b", Link(self.server), os.path.join(self.dir, "b.json")); self.assertEqual(len(b2.conflicts), 1)

class TestPersistenceAndConvergence(Base):
    def test_r08_device_state_survives_restart(self):
        a = self.device("a"); a.set("x", 1); a.increment("n", 2); a.sync(); a.set("y", 3); a2 = Device("a", Link(self.server), os.path.join(self.dir, "a.json"))
        self.assertEqual((a2.items(), a2.last_seq, a2.counter, a2.lamport), (a.items(), a.last_seq, a.counter, a.lamport)); self.assertEqual(a2.pending(), a.pending()); self.assertEqual(a2.set("z", 1), "a-4")
    def test_r08_the_state_file_is_json_and_atomic(self):
        a = self.device("a"); a.set("x", 1); d = json.load(open(os.path.join(self.dir, "a.json"))); self.assertEqual(d["device_id"], "a"); self.assertEqual(d["outbox"][0]["op_id"], "a-1"); self.assertEqual(sorted(os.listdir(self.dir)), ["a.json"])
    def test_r08_a_state_file_of_another_device(self):
        self.device("a").set("x", 1)
        with self.assertRaises(ValueError): Device("b", Link(self.server), os.path.join(self.dir, "a.json"))
    def test_r08_a_crash_after_the_server_accepted_but_before_the_ack_was_stored(self):
        a = self.device("a"); a.increment("n", 5); a.link.fail_next(1, "response"); a.sync(); a2 = Device("a", Link(self.server), os.path.join(self.dir, "a.json")); self.assertEqual(len(a2.pending()), 1)
        r = a2.sync(); self.assertEqual((r.completed, r.duplicates), (True, 1)); self.assertEqual((self.server.snapshot(), a2.get("n")), ({"n": 5}, 5))
    def test_r08_a_crash_between_local_changes_loses_nothing(self):
        a = self.device("a"); a.set("x", 1); a.set("y", 2); del a; a2 = Device("a", Link(self.server), os.path.join(self.dir, "a.json")); self.assertEqual(a2.items(), {"x": 1, "y": 2}); a2.sync(); self.assertEqual(self.server.snapshot(), {"x": 1, "y": 2})
    def test_r08_a_restarted_device_continues_pulling_where_it_stopped(self):
        a, b = self.device("a"), self.device("b"); a.set("x", 1); a.sync(); b.sync(); a.set("y", 2); a.sync(); b2 = Device("b", Link(self.server), os.path.join(self.dir, "b.json")); r = b2.sync(); self.assertEqual((r.pulled, b2.items()), (1, {"x": 1, "y": 2}))
    def test_r08_server_persistence_and_restart(self):
        path = os.path.join(self.dir, "server.log"); s = SyncServer(path); s.push("a", [op("a-1", "a", 1, value=1), op("a-2", "a", 2, key="n", type="incr", value=3)]); s.push("a", [op("a-3", "a", 3, key="m", value="v")])
        s2 = SyncServer(path); self.assertEqual((s2.latest_seq(), s2.snapshot()), (3, {"k": 1, "m": "v", "n": 3})); ans = s2.push("a", [op("a-1", "a", 1, value=99), op("a-4", "a", 4, key="n", type="set", value=1), op("a-5", "a", 5, key="z")]); self.assertEqual((ans["duplicates"], [r["reason"] for r in ans["rejected"]], ans["accepted"]), (["a-1"], ["type mismatch"], ["a-5"]))
        self.assertEqual([o["seq"] for o in s2.pull(0)["ops"]], [1, 2, 3, 4])
    def test_r08_the_server_file_has_one_json_object_per_line(self):
        path = os.path.join(self.dir, "server.log"); s = SyncServer(path); s.push("a", [op("a-1"), op("a-2", key="b", ts=2)]); lines = open(path).read().splitlines(); self.assertEqual(len(lines), 2); self.assertEqual([json.loads(l)["op_id"] for l in lines], ["a-1", "a-2"])
    def test_r08_rejected_and_duplicate_operations_are_not_written(self):
        path = os.path.join(self.dir, "server.log"); s = SyncServer(path); s.push("a", [op("a-1")]); s.push("a", [op("a-1"), op("a-2", ts=0)]); self.assertEqual(len(open(path).read().splitlines()), 1)
    def test_r08_a_torn_last_line_is_ignored(self):
        path = os.path.join(self.dir, "server.log"); s = SyncServer(path); s.push("a", [op("a-1"), op("a-2", key="b", ts=2)]); open(path, "a").write('{"op_id": "a-3", "device": "a", "ts"'); s2 = SyncServer(path); self.assertEqual(s2.latest_seq(), 2)
        self.assertEqual(s2.push("a", [op("a-3", key="c", ts=3)])["accepted"], ["a-3"]); self.assertEqual(s2.latest_seq(), 3)
    def test_r08_devices_work_against_a_restarted_server(self):
        path = os.path.join(self.dir, "server.log"); s = SyncServer(path); a = Device("a", Link(s)); a.set("x", 1); a.sync(); s2 = SyncServer(path); b = Device("b", Link(s2)); b.sync(); self.assertEqual(b.items(), {"x": 1}); a.link = Link(s2); a.set("y", 2); a.sync(); self.assertEqual(s2.snapshot(), {"x": 1, "y": 2})
    def test_r08_many_devices_converge_regardless_of_sync_order(self):
        devs = [self.device(n) for n in "abcd"]
        for i, d in enumerate(devs): d.set("shared", f"v{i}"); d.set(f"own-{d.device_id}", i); d.increment("total", i + 1)
        for d in reversed(devs): d.sync()
        for d in devs: d.sync()
        snap = self.server.snapshot(); self.assertEqual([d.items() for d in devs], [snap] * 4); self.assertEqual(snap["total"], 10); self.assertEqual(snap["shared"], "v3")
    def test_r08_randomized_offline_work_with_failures_converges(self):
        for seed in range(12):
            rng = random.Random(seed); server = SyncServer(); links = [Link(server) for _ in range(4)]; devs = [Device(f"dev{i}", links[i], os.path.join(self.dir, f"r{seed}-{i}.json")) for i in range(4)]; expected_counter = 0
            for step in range(120):
                d = rng.choice(devs); r = rng.random()
                if r < 0.35: d.set(rng.choice("abcde"), rng.randint(0, 99))
                elif r < 0.45: d.delete(rng.choice("abcde"))
                elif r < 0.65: n = rng.randint(1, 5); d.increment("count", n); expected_counter += n
                else:
                    if rng.random() < 0.4: d.link.fail_next(rng.randint(1, 2), rng.choice(["request", "response"]))
                    d.sync(batch=rng.randint(1, 5), page=rng.randint(1, 5))
                    if rng.random() < 0.15: devs[devs.index(d)] = d = Device(d.device_id, d.link, d.state_path)       # a restart
            for link in links: link.fail_next(0)
            for _ in range(3):
                for d in devs: self.assertTrue(d.sync(batch=3, page=3).completed)
            snap = server.snapshot(); self.assertEqual(snap.get("count", 0), expected_counter, seed); self.assertEqual([d.items() for d in devs], [snap] * 4, seed); self.assertEqual(snap, fold(server.pull(0, 10000)["ops"]), seed)
            self.assertEqual(server.latest_seq(), len({o["op_id"] for o in server.pull(0, 10000)["ops"]}), seed)
    def test_r08_the_result_is_independent_of_the_order_operations_reach_the_server(self):
        rng = random.Random(5); ops = []
        for i in range(60): dev = rng.choice("abc"); t = rng.choice(["set", "delete", "incr"]); key = rng.choice(["k1", "k2"]) if t != "incr" else "c"; ops.append(op(f"{dev}-{i}", dev, rng.randint(1, 20), 0, t, key, None if t == "delete" else (rng.randint(1, 9) if t == "incr" else i)))
        results = []
        for _ in range(5):
            rng.shuffle(ops); s = SyncServer()
            for o in ops: s.push(o["device"], [o])
            results.append(s.snapshot())
        self.assertTrue(all(r == results[0] for r in results))
