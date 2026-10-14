"""The thermal sweep tool: logic tested with injected sensors/clock/model so no real heat is needed."""
import unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import thermalbench as TB, ollama

class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s

class TestConfigs(unittest.TestCase):
    def test_gpu_config_is_skipped_without_a_driver_and_runs_with_one(self):
        no = TB.configs(gpu=False, have_profiles=True); self.assertIn("no NVIDIA driver", no[-1]["skip"]); self.assertIn("nvidia-driver", no[-1]["skip"])
        yes = TB.configs(gpu=True, have_profiles=True); self.assertEqual(yes[-1]["options"], {"num_thread": 4, "num_gpu": 99}); self.assertNotIn("skip", yes[-1])
    def test_power_saver_is_skipped_when_unavailable(self):
        self.assertIn("not available", [c for c in TB.configs(gpu=False, have_profiles=False) if "power-saver" in c["name"]][0]["skip"])
    def test_the_thread_variants_are_present(self):
        self.assertEqual([c["options"]["num_thread"] for c in TB.configs(gpu=False, have_profiles=True)[:3]], [4, 3, 2])

class TestCooldown(unittest.TestCase):
    def test_waits_until_cool_then_reports(self):
        temps = iter([90, 85, 78, 70]); c = Clock(); w, t = TB.cooldown(72, reader=lambda: next(temps), sleep=c.sleep, step=15); self.assertEqual((w, t), (45, 70))
    def test_gives_up_after_the_cap(self):
        c = Clock(); w, t = TB.cooldown(72, reader=lambda: 90, sleep=c.sleep, cap=60, step=15); self.assertEqual(w, 60)
    def test_a_missing_sensor_does_not_block(self): self.assertEqual(TB.cooldown(72, reader=lambda: None, sleep=lambda s: None)[0], 0)

class TestRunConfig(Sandboxed):
    def run_cfg(self, cfg, temps=None, minutes=1.0, profile_calls=None, chat=None, **kw):
        c = Clock(); profile_calls = profile_calls if profile_calls is not None else []
        def fake_chat(proj, messages, **k):
            c.t += 12.0; return {"prompt_tokens": 800, "gen_tokens": 40, "prompt_ms": 8000, "gen_ms": 4000, "secs": 12.0}
        reader = temps if callable(temps) else (lambda: 80.0)
        return TB.run_config(self.proj(), cfg, minutes=minutes, cool_to=999, reader=reader, sleep=c.sleep, clock=c.now, chat=chat or fake_chat, freq=lambda: 3000, sample_every=1,
                             set_profile=lambda n: profile_calls.append(("set", n)) or True, get_profile=lambda: "balanced", **kw)
    def test_summary_numbers(self):
        r = self.run_cfg({"name": "x", "options": {"num_thread": 4}}, minutes=2.0)
        self.assertEqual(r["requests"], 10); self.assertEqual(r["steady"]["gen_tps"], 10.0); self.assertEqual(r["steady"]["prompt_tps"], 100.0); self.assertEqual(r["steady"]["requests_per_min"], 5.0)
    def test_options_reach_the_model_server_per_request(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="s")); self.set_env(OLLAMA_PORT=fake.port); seen = []
        real = ollama.chat
        def spy(proj, messages, **k): seen.append(k.get("options")); return real(proj, messages, **k)
        c = Clock(); TB.run_config(self.proj(), {"name": "x", "options": {"num_thread": 3, "num_gpu": 7}}, minutes=0.05, cool_to=999, reader=lambda: 70, sleep=c.sleep, clock=lambda: __import__("time").time(), chat=spy, freq=lambda: 1)
        fake.stop(); self.assertEqual(seen[0], {"num_thread": 3, "num_gpu": 7}); self.assertEqual(fake.requests[0]["options"]["num_thread"], 3); self.assertEqual(fake.requests[0]["options"]["num_gpu"], 7)
    def test_power_profile_is_set_and_always_restored(self):
        calls = []; self.run_cfg({"name": "ps", "options": {}, "profile": "power-saver"}, profile_calls=calls); self.assertEqual(calls, [("set", "power-saver"), ("set", "balanced")])
    def test_power_profile_is_restored_even_if_the_run_crashes(self):
        calls = []
        def boom(proj, messages, **k): raise RuntimeError("model died")
        with self.assertRaises(RuntimeError): self.run_cfg({"name": "ps", "options": {}, "profile": "power-saver"}, profile_calls=calls, chat=boom)
        self.assertEqual(calls[-1], ("set", "balanced"))
    def test_aborts_at_the_critical_temperature(self):
        r = self.run_cfg({"name": "hot", "options": {}}, temps=lambda: 98.0, minutes=2.0); self.assertIn("aborted", r["aborted"]); self.assertEqual(r["requests"], 0)
    def test_skipped_configs_are_reported_not_run(self): self.assertEqual(TB.run_config(self.proj(), {"name": "g", "skip": "no driver"}), {"name": "g", "skipped": "no driver"})

class TestRecommendation(unittest.TestCase):
    def res(self, name, rpm, temp): return {"name": name, "steady": {"requests_per_min": rpm, "temp": temp, "temp_max": temp + 2, "gen_tps": 5, "prompt_tps": 30, "mhz": 3000}, "first_minute": {"requests_per_min": rpm, "gen_tps": 5, "prompt_tps": 30, "temp": temp, "mhz": 3000}, "aborted": None}
    def test_prefers_the_fastest_config_that_stays_cool(self):
        rs = [self.res("4t", 5.0, 92), self.res("3t", 4.8, 86), self.res("2t", 3.9, 80)]; self.assertEqual(TB.recommend(rs)["name"], "3t")
    def test_if_everything_is_hot_it_takes_the_coolest_that_keeps_90_percent_of_the_speed(self):
        rs = [self.res("4t", 5.0, 93), self.res("3t", 4.6, 90), self.res("2t", 3.0, 89)]; self.assertEqual(TB.recommend(rs)["name"], "3t")
    def test_skipped_and_empty(self): self.assertIsNone(TB.recommend([{"name": "g", "skipped": "x"}]))
    def test_render_has_every_config_and_a_recommendation(self):
        md = TB.render([self.res("4t", 5.0, 90), {"name": "gpu", "skipped": "no driver"}], 4.0); self.assertIn("| 4t |", md); self.assertIn("skipped: no driver", md); self.assertIn("Recommendation", md)

if __name__ == "__main__": unittest.main()
