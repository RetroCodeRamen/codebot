import os, tempfile, threading, time, unittest
import jobq
from jobq import JobQueue, Worker, Job, JobNotFound, Cancelled

class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s

class Base(unittest.TestCase):
    def setUp(self): self.clock = Clock(); self.dir = tempfile.mkdtemp(prefix="jobq-test-"); self.path = os.path.join(self.dir, "q.db")
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def q(self, path=None, **kw):
        kw.setdefault("clock", self.clock); q = JobQueue(path or ":memory:", **kw); self.addCleanup(q.close); return q
    def w(self, q, handlers, **kw): return Worker(q, handlers, **kw)

class TestQueue(Base):
    def test_r01_enqueue_returns_increasing_ids_and_stores_defaults(self):
        q = self.q(); a = q.enqueue("send", {"to": "x"}); b = q.enqueue("send"); self.assertEqual((a, b), (1, 2))
        j = q.get(a); self.assertIsInstance(j, Job); self.assertEqual((j.name, j.payload, j.state, j.priority, j.attempts, j.max_attempts, j.timeout, j.run_at, j.lease_expires, j.worker_id, j.cancel_requested, j.last_error, j.result, j.key, j.created, j.finished),
                                                                       ("send", {"to": "x"}, "queued", 0, 0, 3, None, 1000.0, None, None, False, None, None, None, 1000.0, None))
    def test_r01_payload_round_trips_through_json(self): q = self.q(); p = {"a": [1, 2.5, None, True, "ü"], "b": {"c": []}}; self.assertEqual(q.get(q.enqueue("x", p)).payload, p); self.assertIsNone(q.get(q.enqueue("y")).payload)
    def test_r01_options_are_stored(self):
        q = self.q(); j = q.get(q.enqueue("x", priority=7, max_attempts=5, timeout=2.5, delay=10, key="k")); self.assertEqual((j.priority, j.max_attempts, j.timeout, j.run_at, j.key), (7, 5, 2.5, 1010.0, "k"))
    def test_r01_name_is_stripped(self): q = self.q(); self.assertEqual(q.get(q.enqueue("  work  ")).name, "work")
    def test_r01_invalid_arguments(self):
        q = self.q()
        for kw in ({"name": ""}, {"name": "  "}, {"name": 5}, {"name": "x", "priority": 1.5}, {"name": "x", "priority": True}, {"name": "x", "max_attempts": 0}, {"name": "x", "max_attempts": True}, {"name": "x", "timeout": 0}, {"name": "x", "timeout": -1}, {"name": "x", "timeout": "5"},
                   {"name": "x", "delay": -1}, {"name": "x", "delay": "1"}, {"name": "x", "key": ""}, {"name": "x", "key": 5}, {"name": "x", "payload": object()}, {"name": "x", "payload": {1, 2}}):
            with self.assertRaises(ValueError, msg=str(kw)): q.enqueue(**kw)
        self.assertEqual(q.counts()["queued"], 0)
    def test_r01_a_key_makes_enqueue_idempotent(self):
        q = self.q(); a = q.enqueue("x", 1, key="same"); b = q.enqueue("x", 2, key="same"); self.assertEqual(a, b); self.assertEqual(q.get(a).payload, 1); self.assertEqual(q.counts()["queued"], 1); self.assertNotEqual(q.enqueue("x", key="other"), a)
    def test_r01_a_key_also_matches_finished_jobs(self):
        q = self.q(); a = q.enqueue("x", key="k"); self.w(q, {"x": lambda p, c: 1}).run_until_empty(); self.assertEqual(q.enqueue("x", key="k"), a); self.assertEqual(q.counts()["succeeded"], 1)
    def test_r01_get_unknown_id(self):
        q = self.q()
        with self.assertRaises(JobNotFound): q.get(99)
        self.assertTrue(issubclass(JobNotFound, KeyError))
    def test_r01_counts_has_every_state(self): self.assertEqual(self.q().counts(), {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0})
    def test_r01_counts_follow_the_jobs(self):
        q = self.q(); [q.enqueue("x") for _ in range(3)]; q.claim("w"); c = q.counts(); self.assertEqual((c["queued"], c["running"]), (2, 1))
    def test_r01_context_manager_and_close(self):
        with JobQueue(self.path) as q: q.enqueue("x")
        with self.assertRaises(Exception): q.counts()
    def test_r01_job_fields(self): self.assertEqual(list(Job.__dataclass_fields__), ["id", "name", "payload", "state", "priority", "attempts", "max_attempts", "timeout", "run_at", "lease_expires", "worker_id", "cancel_requested", "last_error", "result", "key", "created", "finished"])
    def test_r01_a_failed_enqueue_leaves_no_trace(self):
        q = self.q()
        with self.assertRaises(ValueError): q.enqueue("x", object())
        self.assertEqual(q.enqueue("ok"), 1)

class TestClaim(Base):
    def test_r02_claim_marks_the_job_running(self):
        q = self.q(lease_seconds=30); q.enqueue("x"); j = q.claim("w1"); self.assertEqual((j.state, j.attempts, j.worker_id, j.lease_expires), ("running", 1, "w1", 1030.0)); self.assertEqual(q.get(j.id).state, "running")
    def test_r02_nothing_to_claim(self): self.assertIsNone(self.q().claim("w"))
    def test_r02_priority_then_run_at_then_id(self):
        q = self.q(); a = q.enqueue("x", priority=1); self.clock.advance(5); b = q.enqueue("x", priority=5); c = q.enqueue("x", priority=5); d = q.enqueue("x", priority=1); self.clock.advance(5)
        self.assertEqual([q.claim("w").id for _ in range(4)], [b, c, a, d])
    def test_r02_earlier_run_at_wins_at_equal_priority(self):
        q = self.q(); late = q.enqueue("x", delay=5); early = q.enqueue("x", delay=1); self.clock.advance(10); self.assertEqual(q.claim("w").id, early); self.assertEqual(q.claim("w").id, late)
    def test_r02_delayed_jobs_wait(self):
        q = self.q(); q.enqueue("x", delay=10); self.assertIsNone(q.claim("w")); self.clock.advance(9.9); self.assertIsNone(q.claim("w")); self.clock.advance(0.1); self.assertIsNotNone(q.claim("w"))
    def test_r02_a_claimed_job_is_not_claimed_again(self): q = self.q(); q.enqueue("x"); self.assertIsNotNone(q.claim("w1")); self.assertIsNone(q.claim("w2"))
    def test_r02_negative_priorities_sort_last(self): q = self.q(); a = q.enqueue("x", priority=-5); b = q.enqueue("x", priority=0); self.assertEqual(q.claim("w").id, b); self.assertEqual(q.claim("w").id, a)
    def test_r02_a_high_priority_job_added_later_goes_first(self): q = self.q(); q.enqueue("x"); hi = q.enqueue("x", priority=9); self.assertEqual(q.claim("w").id, hi)
    def test_r02_each_claim_returns_a_different_job_under_concurrency(self):
        q = self.q(self.path); [q.enqueue("x") for _ in range(60)]; got, lock = [], threading.Lock()
        def work():
            qq = JobQueue(self.path, clock=self.clock)
            try:
                while True:
                    j = qq.claim("t")
                    if j is None: break
                    with lock: got.append(j.id)
            finally: qq.close()
        ts = [threading.Thread(target=work) for _ in range(6)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        self.assertEqual(sorted(got), list(range(1, 61)))
    def test_r02_the_claimed_job_carries_its_payload(self): q = self.q(); q.enqueue("x", {"k": [1]}); self.assertEqual(q.claim("w").payload, {"k": [1]})
    def test_r02_run_at_equal_to_now_is_runnable(self): q = self.q(); q.enqueue("x", delay=0); self.assertIsNotNone(q.claim("w"))

class TestWorker(Base):
    def test_r03_success_stores_the_result(self):
        q = self.q(); i = q.enqueue("double", 21); self.assertTrue(self.w(q, {"double": lambda p, c: p * 2}).run_once()); j = q.get(i); self.assertEqual((j.state, j.result, j.attempts, j.finished), ("succeeded", 42, 1, 1000.0))
    def test_r03_run_once_without_work(self): self.assertFalse(self.w(self.q(), {}).run_once())
    def test_r03_run_until_empty_counts_jobs(self): q = self.q(); [q.enqueue("n", i) for i in range(5)]; self.assertEqual(self.w(q, {"n": lambda p, c: p}).run_until_empty(), 5); self.assertEqual(q.counts()["succeeded"], 5)
    def test_r03_run_until_empty_respects_max_jobs(self): q = self.q(); [q.enqueue("n") for _ in range(5)]; self.assertEqual(self.w(q, {"n": lambda p, c: 1}).run_until_empty(max_jobs=2), 2); self.assertEqual(q.counts()["queued"], 3)
    def test_r03_run_until_empty_ignores_delayed_jobs(self): q = self.q(); q.enqueue("n"); q.enqueue("n", delay=60); self.assertEqual(self.w(q, {"n": lambda p, c: 1}).run_until_empty(), 1); self.assertEqual(q.counts()["queued"], 1)
    def test_r03_jobs_run_in_claim_order(self):
        q = self.q(); seen = []; q.enqueue("n", "low"); q.enqueue("n", "high", priority=3); self.w(q, {"n": lambda p, c: seen.append(p)}).run_until_empty(); self.assertEqual(seen, ["high", "low"])
    def test_r03_a_missing_handler_fails_at_once_without_retries(self):
        q = self.q(); i = q.enqueue("nope", max_attempts=5); self.w(q, {}).run_once(); j = q.get(i); self.assertEqual((j.state, j.attempts, j.last_error), ("failed", 1, "LookupError: no handler for nope"))
    def test_r03_the_context(self):
        q = self.q(); seen = {}; i = q.enqueue("n"); self.w(q, {"n": lambda p, c: seen.update(job=c.job_id, attempt=c.attempt, cancelled=c.cancelled)}).run_once(); self.assertEqual(seen, {"job": i, "attempt": 1, "cancelled": False})
    def test_r03_results_must_be_serializable(self):
        q = self.q(); i = q.enqueue("n", max_attempts=1); self.w(q, {"n": lambda p, c: object()}).run_once(); j = q.get(i); self.assertEqual(j.state, "failed"); self.assertTrue(j.last_error.startswith("TypeError"))
    def test_r03_none_result(self): q = self.q(); i = q.enqueue("n"); self.w(q, {"n": lambda p, c: None}).run_once(); self.assertEqual((q.get(i).state, q.get(i).result), ("succeeded", None))
    def test_r03_complete_and_fail_apply_only_to_the_claimed_attempt(self):
        q = self.q(); q.enqueue("n"); j = q.claim("w1"); self.assertTrue(q.complete(j, "ok")); self.assertFalse(q.complete(j, "again")); self.assertIsNone(q.fail(j, "late")); self.assertEqual(q.get(j.id).result, "ok")
    def test_r03_complete_by_the_wrong_worker_is_ignored(self):
        q = self.q(); q.enqueue("n"); j = q.claim("w1"); import dataclasses; other = dataclasses.replace(j, worker_id="w2"); self.assertFalse(q.complete(other, 1)); self.assertEqual(q.get(j.id).state, "running")
    def test_r03_each_worker_has_an_id(self): self.assertTrue(self.w(self.q(), {}).worker_id); self.assertEqual(self.w(self.q(), {}, worker_id="abc").worker_id, "abc")
    def test_r03_the_worker_id_is_recorded_on_the_job(self):
        q = self.q(); seen = {}; i = q.enqueue("n"); self.w(q, {"n": lambda p, c: seen.update(w=q.get(i).worker_id)}, worker_id="wk").run_once(); self.assertEqual(seen["w"], "wk")

class TestRetries(Base):
    def test_r04_a_failed_attempt_is_retried_with_exponential_backoff(self):
        q = self.q(retry_base=1.0); i = q.enqueue("n", max_attempts=4); w = self.w(q, {"n": lambda p, c: 1 / 0}); w.run_once()
        j = q.get(i); self.assertEqual((j.state, j.attempts, j.run_at, j.last_error), ("queued", 1, 1001.0, "ZeroDivisionError: division by zero"))
        self.assertFalse(w.run_once()); self.clock.advance(1); w.run_once(); self.assertEqual(q.get(i).run_at, 1000 + 1 + 2.0); self.clock.advance(2); w.run_once(); self.assertEqual(q.get(i).run_at, 1003 + 4.0)
    def test_r04_the_last_attempt_fails_for_good(self):
        q = self.q(retry_base=0.0); i = q.enqueue("n", max_attempts=3); w = self.w(q, {"n": lambda p, c: 1 / 0}); self.assertEqual(w.run_until_empty(), 3); j = q.get(i); self.assertEqual((j.state, j.attempts, j.finished), ("failed", 3, 1000.0)); self.assertIn("ZeroDivisionError", j.last_error)
    def test_r04_backoff_is_capped(self):
        q = self.q(retry_base=10.0, retry_cap=25.0); i = q.enqueue("n", max_attempts=6); w = self.w(q, {"n": lambda p, c: 1 / 0}); runs = []
        for _ in range(4): w.run_once(); j = q.get(i); runs.append(j.run_at - self.clock()); self.clock.advance(60)
        self.assertEqual(runs, [10.0, 20.0, 25.0, 25.0])
    def test_r04_max_attempts_one_never_retries(self): q = self.q(); i = q.enqueue("n", max_attempts=1); self.w(q, {"n": lambda p, c: 1 / 0}).run_once(); self.assertEqual(q.get(i).state, "failed")
    def test_r04_success_after_failures_resets_last_error(self):
        q = self.q(retry_base=0.0); calls = {"n": 0}; i = q.enqueue("n")
        def flaky(p, c):
            calls["n"] += 1
            if calls["n"] < 3: raise RuntimeError("not yet")
            return "done"
        self.w(q, {"n": flaky}).run_until_empty(); j = q.get(i); self.assertEqual((j.state, j.attempts, j.result, j.last_error), ("succeeded", 3, "done", None))
    def test_r04_error_text_format(self):
        q = self.q(); i = q.enqueue("n", max_attempts=1)
        def bad(p, c): raise KeyError("missing")
        self.w(q, {"n": bad}).run_once(); self.assertEqual(q.get(i).last_error, "KeyError: 'missing'")
    def test_r04_fail_without_retry(self): q = self.q(); q.enqueue("n", max_attempts=5); j = q.claim("w"); self.assertEqual(q.fail(j, "fatal", retry=False), "failed"); self.assertEqual(q.get(j.id).last_error, "fatal")
    def test_r04_fail_with_retry_returns_queued(self): q = self.q(); q.enqueue("n", max_attempts=2); j = q.claim("w"); self.assertEqual(q.fail(j, "x"), "queued"); self.assertEqual(q.get(j.id).worker_id, None)
    def test_r04_a_retrying_job_does_not_block_other_jobs(self):
        q = self.q(retry_base=100.0); a = q.enqueue("n", max_attempts=2); b = q.enqueue("ok"); w = self.w(q, {"n": lambda p, c: 1 / 0, "ok": lambda p, c: 1}); self.assertEqual(w.run_until_empty(), 2); self.assertEqual((q.get(a).state, q.get(b).state), ("queued", "succeeded"))
    def test_r04_the_retry_keeps_priority(self):
        q = self.q(retry_base=0.0); a = q.enqueue("n", priority=9, max_attempts=2); b = q.enqueue("ok", priority=1); w = self.w(q, {"n": lambda p, c: 1 / 0, "ok": lambda p, c: 1}); w.run_once(); self.assertEqual(q.claim("w").id, a)

class TestTimeout(Base):
    def real(self, **kw): return JobQueue(self.path, **kw)
    def test_r05_a_slow_handler_times_out_and_is_retried(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=0.3, max_attempts=2); w = self.w(q, {"slow": lambda p, c: time.sleep(3)}); t0 = time.time(); w.run_once()
        self.assertLess(time.time() - t0, 2.0); j = q.get(i); self.assertEqual((j.state, j.last_error), ("queued", "TimeoutError: job exceeded 0.3 seconds"))
    def test_r05_the_last_timeout_fails_the_job(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=0.2, max_attempts=1); self.w(q, {"slow": lambda p, c: time.sleep(2)}).run_once(); j = q.get(i); self.assertEqual((j.state, j.last_error), ("failed", "TimeoutError: job exceeded 0.2 seconds"))
    def test_r05_a_late_result_changes_nothing(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=0.2, max_attempts=1); self.w(q, {"slow": lambda p, c: (time.sleep(0.8), "late")[1]}).run_once(); time.sleep(1.2); j = q.get(i); self.assertEqual((j.state, j.result), ("failed", None))
    def test_r05_a_late_exception_changes_nothing(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=0.2, max_attempts=1)
        def slow(p, c): time.sleep(0.8); raise RuntimeError("late boom")
        self.w(q, {"slow": slow}).run_once(); time.sleep(1.2); self.assertEqual(q.get(i).last_error, "TimeoutError: job exceeded 0.2 seconds")
    def test_r05_a_late_result_of_an_old_attempt_does_not_finish_the_new_attempt(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=1.0, max_attempts=3); gate = threading.Event()
        def slow(p, c):
            if c.attempt == 1: time.sleep(1.5); return "old"             # finishes 0.5 s after its attempt was given up
            gate.wait(5); return "new"
        w = self.w(q, {"slow": slow}); w.run_once(); self.assertEqual(q.get(i).state, "queued")        # attempt 1 timed out after 1.0 s
        t = threading.Thread(target=w.run_once); t.start(); time.sleep(0.8)                              # attempt 2 runs; attempt 1 finishes meanwhile
        j = q.get(i); self.assertEqual((j.state, j.attempts, j.result), ("running", 2, None))
        gate.set(); t.join(10); j = q.get(i); self.assertEqual((j.state, j.result, j.attempts), ("succeeded", "new", 2))
    def test_r05_no_timeout_means_no_limit(self):
        q = self.q(); i = q.enqueue("n"); self.w(q, {"n": lambda p, c: (time.sleep(0.5), "ok")[1]}).run_once(); self.assertEqual(q.get(i).result, "ok")
    def test_r05_a_fast_handler_is_unaffected_by_its_timeout(self): q = self.q(); i = q.enqueue("n", timeout=5); self.w(q, {"n": lambda p, c: "fast"}).run_once(); self.assertEqual(q.get(i).state, "succeeded")
    def test_r05_the_worker_keeps_processing_after_a_timeout(self):
        q = self.q(retry_base=0.0); a = q.enqueue("slow", timeout=0.2, max_attempts=1); b = q.enqueue("ok"); self.w(q, {"slow": lambda p, c: time.sleep(2), "ok": lambda p, c: 1}).run_until_empty(); self.assertEqual((q.get(a).state, q.get(b).state), ("failed", "succeeded"))
    def test_r05_timeout_error_text_uses_a_float(self):
        q = self.q(retry_base=0.0); i = q.enqueue("slow", timeout=1, max_attempts=1); w = self.w(q, {"slow": lambda p, c: time.sleep(3)}); w.run_once(); self.assertEqual(q.get(i).last_error, "TimeoutError: job exceeded 1.0 seconds")

class TestCancel(Base):
    def test_r06_cancelling_a_queued_job(self):
        q = self.q(); i = q.enqueue("n"); self.assertEqual(q.cancel(i), "cancelled"); j = q.get(i); self.assertEqual((j.state, j.finished), ("cancelled", 1000.0)); self.assertFalse(self.w(q, {"n": lambda p, c: 1}).run_once())
    def test_r06_cancelling_a_finished_job(self): q = self.q(); i = q.enqueue("n"); self.w(q, {"n": lambda p, c: 1}).run_once(); self.assertIs(q.cancel(i), False); self.assertEqual(q.get(i).state, "succeeded")
    def test_r06_cancelling_twice(self): q = self.q(); i = q.enqueue("n"); q.cancel(i); self.assertIs(q.cancel(i), False)
    def test_r06_unknown_id(self):
        with self.assertRaises(JobNotFound): self.q().cancel(5)
    def test_r06_cancelling_a_running_job_only_requests_it(self):
        q = self.q(); q.enqueue("n"); j = q.claim("w"); self.assertEqual(q.cancel(j.id), "requested"); g = q.get(j.id); self.assertEqual((g.state, g.cancel_requested), ("running", True))
    def test_r06_a_cooperating_handler_stops_and_the_job_is_cancelled(self):
        q = self.q(self.path); started, i = threading.Event(), q.enqueue("n", max_attempts=3)
        def handler(p, ctx):
            started.set()
            for _ in range(200):
                ctx.check(); time.sleep(0.02)
            return "finished"
        w = self.w(q, {"n": handler}); t = threading.Thread(target=w.run_once); t.start(); self.assertTrue(started.wait(5)); self.assertEqual(q.cancel(i), "requested"); t.join(10)
        j = q.get(i); self.assertEqual((j.state, j.result), ("cancelled", None)); self.assertEqual(j.attempts, 1)
    def test_r06_ctx_cancelled_turns_true(self):
        q = self.q(self.path); seen, started, i = [], threading.Event(), q.enqueue("n")
        def handler(p, ctx): seen.append(ctx.cancelled); started.set(); time.sleep(0.5); seen.append(ctx.cancelled)
        w = self.w(q, {"n": handler}); t = threading.Thread(target=w.run_once); t.start(); started.wait(5); q.cancel(i); t.join(10); self.assertEqual(seen, [False, True])
    def test_r06_a_result_is_discarded_when_cancellation_was_requested(self):
        q = self.q(); i = q.enqueue("n"); j = q.claim("w"); q.cancel(i); self.assertTrue(q.complete(j, "result")); g = q.get(i); self.assertEqual((g.state, g.result), ("cancelled", None))
    def test_r06_a_failing_cancelled_job_is_not_retried(self): q = self.q(); i = q.enqueue("n", max_attempts=5); j = q.claim("w"); q.cancel(i); self.assertEqual(q.fail(j, "boom"), "cancelled"); self.assertEqual(q.get(i).state, "cancelled")
    def test_r06_cancelled_without_a_request_is_a_normal_failure(self):
        q = self.q(retry_base=0.0); i = q.enqueue("n", max_attempts=2)
        def handler(p, ctx): raise Cancelled("spurious")
        self.w(q, {"n": handler}).run_once(); self.assertEqual((q.get(i).state, q.get(i).last_error), ("queued", "Cancelled: spurious"))
    def test_r06_cancelled_jobs_are_not_counted_as_queued(self): q = self.q(); i = q.enqueue("n"); q.enqueue("n"); q.cancel(i); self.assertEqual((q.counts()["queued"], q.counts()["cancelled"]), (1, 1))
    def test_r06_cancelled_is_an_exception_class(self): self.assertTrue(issubclass(Cancelled, Exception))
    def test_r06_check_does_nothing_without_a_request(self): q = self.q(); seen = []; q.enqueue("n"); self.w(q, {"n": lambda p, c: (c.check(), seen.append(1))}).run_once(); self.assertEqual(seen, [1])

class TestRecovery(Base):
    def test_r07_a_lost_job_is_recovered_after_its_lease_expires(self):
        q = self.q(lease_seconds=30); i = q.enqueue("n"); q.claim("dead"); self.assertEqual(q.recover(), 0); self.clock.advance(31); self.assertEqual(q.recover(), 1)
        j = q.get(i); self.assertEqual((j.state, j.attempts, j.worker_id, j.lease_expires, j.last_error, j.run_at), ("queued", 1, None, None, "worker lost (lease expired)", self.clock()))
    def test_r07_the_lease_boundary(self): q = self.q(lease_seconds=30); q.enqueue("n"); q.claim("dead"); self.clock.advance(30); self.assertEqual(q.recover(), 0); self.clock.advance(0.01); self.assertEqual(q.recover(), 1)
    def test_r07_claim_recovers_lost_jobs_first(self):
        q = self.q(lease_seconds=30); i = q.enqueue("n"); q.claim("dead"); self.clock.advance(31); j = q.claim("alive"); self.assertEqual((j.id, j.attempts, j.worker_id), (i, 2, "alive"))
    def test_r07_the_lost_attempt_counts(self):
        q = self.q(lease_seconds=10); i = q.enqueue("n", max_attempts=2); q.claim("a"); self.clock.advance(11); q.claim("b"); self.clock.advance(11); self.assertEqual(q.recover(), 1); j = q.get(i); self.assertEqual((j.state, j.attempts, j.last_error), ("failed", 2, "worker lost (lease expired)")); self.assertEqual(j.finished, self.clock())
    def test_r07_a_late_result_from_the_lost_worker_is_ignored(self):
        q = self.q(lease_seconds=10); q.enqueue("n"); old = q.claim("a"); self.clock.advance(11); new = q.claim("b"); self.assertFalse(q.complete(old, "stale")); self.assertIsNone(q.fail(old, "stale")); self.assertEqual(q.get(old.id).state, "running"); self.assertTrue(q.complete(new, "fresh")); self.assertEqual(q.get(old.id).result, "fresh")
    def test_r07_heartbeat_extends_the_lease(self):
        q = self.q(lease_seconds=10); q.enqueue("n"); j = q.claim("w"); self.clock.advance(8); self.assertTrue(q.heartbeat(j)); self.assertEqual(q.get(j.id).lease_expires, self.clock() + 10); self.clock.advance(8); self.assertEqual(q.recover(), 0)
    def test_r07_heartbeat_of_a_stale_attempt_returns_false(self): q = self.q(lease_seconds=10); q.enqueue("n"); old = q.claim("a"); self.clock.advance(11); q.claim("b"); self.assertFalse(q.heartbeat(old))
    def test_r07_ctx_heartbeat(self):
        q = self.q(lease_seconds=10); i = q.enqueue("n"); out = []
        def handler(p, c): self.clock.advance(8); out.append(c.heartbeat()); out.append(q.get(i).lease_expires - self.clock())
        self.w(q, {"n": handler}).run_once(); self.assertEqual(out, [True, 10.0])
    def test_r07_the_worker_extends_the_lease_of_a_long_job(self):
        q = JobQueue(self.path, lease_seconds=0.6); i = q.enqueue("n", timeout=None); self.addCleanup(q.close); done = []
        def handler(p, c): time.sleep(2.0); return "ok"
        w = Worker(q, {"n": handler}); t = threading.Thread(target=w.run_once); t.start(); time.sleep(1.5)
        other = JobQueue(self.path, lease_seconds=0.6); self.addCleanup(other.close); self.assertEqual(other.recover(), 0); self.assertIsNone(other.claim("thief")); t.join(10); self.assertEqual(q.get(i).state, "succeeded")
    def test_r07_recovering_nothing(self): self.assertEqual(self.q().recover(), 0)
    def test_r07_finished_jobs_are_never_recovered(self): q = self.q(lease_seconds=1); q.enqueue("n"); j = q.claim("w"); q.complete(j, 1); self.clock.advance(100); self.assertEqual(q.recover(), 0); self.assertEqual(q.counts()["succeeded"], 1)
    def test_r07_recovered_jobs_run_again_in_priority_order(self):
        q = self.q(lease_seconds=5); a = q.enqueue("n", priority=1); q.claim("dead"); b = q.enqueue("n", priority=9); self.clock.advance(6); self.assertEqual(q.claim("w").id, b); self.assertEqual(q.claim("w").id, a)
    def test_r07_several_lost_jobs_are_all_recovered(self):
        q = self.q(lease_seconds=5); [q.enqueue("n") for _ in range(3)]; [q.claim("dead") for _ in range(3)]; self.clock.advance(6); self.assertEqual(q.recover(), 3); self.assertEqual(q.counts()["queued"], 3)

class TestConcurrency(Base):
    def test_r08_four_threads_run_fifty_jobs_exactly_once(self):
        q = self.q(self.path); counter, lock = {}, threading.Lock()
        def h(p, c):
            with lock: counter[p] = counter.get(p, 0) + 1
            time.sleep(0.005); return p
        [q.enqueue("n", i) for i in range(50)]; w = Worker(q, {"n": h}, poll_interval=0.01).start(4); end = time.time() + 60
        while q.counts()["succeeded"] < 50 and time.time() < end: time.sleep(0.05)
        w.stop(); self.assertEqual(q.counts()["succeeded"], 50); self.assertEqual(sorted(counter), list(range(50))); self.assertEqual(set(counter.values()), {1})
    def test_r08_two_queue_objects_on_one_file_share_the_work(self):
        q1, q2 = self.q(self.path), self.q(self.path); [q1.enqueue("n", i) for i in range(30)]; seen, lock = [], threading.Lock()
        def h(p, c):
            with lock: seen.append(p)
            time.sleep(0.003)
        w1, w2 = Worker(q1, {"n": h}, poll_interval=0.01).start(2), Worker(q2, {"n": h}, poll_interval=0.01).start(2); end = time.time() + 60
        while len(seen) < 30 and time.time() < end: time.sleep(0.05)
        time.sleep(0.2); w1.stop(); w2.stop(); self.assertEqual(sorted(seen), list(range(30)))
    def test_r08_workers_in_separate_processes(self):
        import subprocess, sys; q = self.q(self.path); [q.enqueue("n", i) for i in range(20)]
        code = "import sys; sys.path.insert(0, %r)\nfrom jobq import JobQueue, Worker\nq = JobQueue(%r)\nWorker(q, {'n': lambda p, c: p}).run_until_empty()\nq.close()\n" % (os.getcwd(), self.path)
        ps = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(3)]; [p.wait(60) for p in ps]
        self.assertEqual(q.counts()["succeeded"], 20); self.assertEqual(sorted(q.get(i).result for i in range(1, 21)), list(range(20)))
    def test_r08_a_retried_job_is_never_run_in_parallel_with_itself(self):
        q = self.q(self.path, retry_base=0.0); active, peak, lock = [0], [0], threading.Lock(); i = q.enqueue("n", max_attempts=30)
        def h(p, c):
            with lock: active[0] += 1; peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            with lock: active[0] -= 1
            raise RuntimeError("again")
        w = Worker(q, {"n": h}, poll_interval=0.005).start(4); end = time.time() + 30
        while q.get(i).state != "failed" and time.time() < end: time.sleep(0.05)
        w.stop(); self.assertEqual(q.get(i).state, "failed"); self.assertEqual(q.get(i).attempts, 30); self.assertEqual(peak[0], 1)
    def test_r08_start_returns_the_worker_and_stop_waits_for_the_running_job(self):
        q = self.q(self.path); i = q.enqueue("n"); started = threading.Event()
        def h(p, c): started.set(); time.sleep(0.6); return "done"
        w = Worker(q, {"n": h}, poll_interval=0.01); self.assertIs(w.start(1), w); self.assertTrue(started.wait(10)); w.stop(wait=True); self.assertEqual(q.get(i).state, "succeeded")
    def test_r08_an_idle_worker_picks_up_jobs_enqueued_later(self):
        q = self.q(self.path); w = Worker(q, {"n": lambda p, c: p}, poll_interval=0.01).start(2); time.sleep(0.2); i = q.enqueue("n", 7); end = time.time() + 20
        while q.get(i).state != "succeeded" and time.time() < end: time.sleep(0.02)
        w.stop(); self.assertEqual(q.get(i).result, 7)
    def test_r08_stop_without_start_is_harmless(self): self.w(self.q(), {}).stop()
    def test_r08_state_survives_reopening(self):
        q = JobQueue(self.path, clock=self.clock); a = q.enqueue("n", {"x": 1}, priority=3, key="k"); b = q.enqueue("n"); Worker(q, {"n": lambda p, c: "r"}).run_once(); q.close()
        q2 = self.q(self.path); self.assertEqual((q2.get(a).state, q2.get(a).result, q2.get(b).state), ("succeeded", "r", "queued")); self.assertEqual(q2.enqueue("n", key="k"), a); self.assertEqual(q2.enqueue("n"), 3)
    def test_r08_a_job_running_when_the_process_died_is_recovered_after_reopening(self):
        q = JobQueue(self.path, clock=self.clock, lease_seconds=20); i = q.enqueue("n"); q.claim("dead"); q.close()
        q2 = self.q(self.path, lease_seconds=20); self.assertEqual(q2.recover(), 0); self.clock.advance(21); j = q2.claim("alive"); self.assertEqual((j.id, j.attempts), (i, 2))
    def test_r08_graceful_stop_leaves_queued_jobs_queued(self):
        q = self.q(self.path); [q.enqueue("n") for _ in range(10)]; w = Worker(q, {"n": lambda p, c: time.sleep(0.2)}, poll_interval=0.01).start(1); time.sleep(0.5); w.stop(); c = q.counts(); self.assertEqual(c["running"], 0); self.assertEqual(c["queued"] + c["succeeded"], 10); self.assertGreater(c["queued"], 0)
