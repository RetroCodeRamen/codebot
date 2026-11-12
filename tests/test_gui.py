"""The GUI's guards: only this page on this machine may change state, settings files stay safe for bash to source, file views stay inside
their version, secrets never reach the page, demo mode changes nothing, and one model job at a time (jobs.py)."""
import contextlib, http.client, io, json, os, subprocess, sys, threading
import helpers
from codebot import jobs
from codebot.gui import api, server, settings as S

class ServerCase(helpers.Sandboxed):
    def setUp(self):
        super().setUp()
        self.srv = server.Server(0, api.Ctx(self.home)); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def req(self, method, path, body=None, **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **{k.replace("_", "-"): v for k, v in headers.items()}}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None: h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h); r = c.getresponse(); out = r.read(); c.close()
        return r.status, out
    def page(self): return {"X_Codebot": "1", "Origin": f"http://127.0.0.1:{self.port}"}

    def test_a_post_from_the_page_is_accepted(self):
        st, out = self.req("POST", "/api/inbox/ack", {"key": "x"}, **self.page())
        self.assertEqual(st, 200, out)

    def test_a_post_without_the_header_or_from_another_origin_is_refused(self):
        self.assertEqual(self.req("POST", "/api/inbox/ack", {"key": "x"}, Origin=f"http://127.0.0.1:{self.port}")[0], 403)
        self.assertEqual(self.req("POST", "/api/inbox/ack", {"key": "x"}, X_Codebot="1", Origin="http://evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/inbox/ack", {"key": "x"}, X_Codebot="1")[0], 403)

    def test_a_form_post_is_refused(self):
        st, _ = self.req("POST", "/api/inbox/ack", {"key": "x"}, **self.page(), Content_Type="application/x-www-form-urlencoded")
        self.assertEqual(st, 415)

    def test_another_host_name_is_refused(self):                      # DNS rebinding: a page on evil.example resolving to 127.0.0.1
        self.assertEqual(self.req("GET", "/api/status", Host=f"evil.example:{self.port}")[0], 421)
        self.assertEqual(self.req("POST", "/api/inbox/ack", {"key": "x"}, **self.page(), Host=f"evil.example:{self.port}")[0], 421)

    def test_static_files_stay_inside_the_static_folder(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/../api.py")[0], 404)
        self.assertEqual(self.req("GET", "/%2e%2e/api.py")[0], 404)

    def test_pages_carry_the_content_security_policy(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"}); r = c.getresponse(); r.read(); c.close()
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))

    def test_a_bad_number_in_a_query_is_not_a_server_error(self):
        st, _ = self.req("GET", f"/api/project/{self.name}/log?offset=abc")
        self.assertEqual(st, 200)

class SettingsCase(helpers.Sandboxed):
    def local(self):
        with open(os.path.join(self.home, "config", "local.env")) as f: return f.read()

    def test_only_codebot_settings_can_be_written(self):
        for key in ("PATH", "BASH_ENV", "LD_PRELOAD", "IFS", "SOMETHING_NEW"):
            with self.assertRaises(S.SettingError, msg=key): S.write(self.home, "machine", key, "/tmp/x")
        self.assertFalse(os.path.exists(os.path.join(self.home, "config", "local.env")))
        self.assertEqual(S.write(self.home, "machine", "DRAFTS_MINUTES", "30"), "30")
        self.assertIn('DRAFTS_MINUTES="30"', self.local())

    def test_a_value_bash_would_expand_is_refused(self):
        for v in ('$(touch /tmp/x)', '`id`', 'a"b', 'a\\b', "a\nb"):
            with self.assertRaises(S.SettingError, msg=v): S.write(self.home, "machine", "CODEBOT_MODEL", v)

    def test_types_and_ranges_are_checked(self):
        with self.assertRaises(S.SettingError): S.write(self.home, "machine", "DRAFTS_PATIENCE", "99")
        with self.assertRaises(S.SettingError): S.write(self.home, "machine", "SANDBOX", "off")
        with self.assertRaises(S.SettingError): S.write(self.home, "machine", "WORKER_CMD", "true")       # commands are edited by hand
        with self.assertRaises(S.SettingError): S.write(self.home, "project", "WORKDIR", "/tmp", self.name)

    def test_a_rewrite_keeps_comments_and_the_file_mode(self):
        p = os.path.join(self.home, "config", "local.env")
        with open(p, "w") as f: f.write('# mine\nDRAFTS_MINUTES=10   # short\n')
        os.chmod(p, 0o600)
        S.write(self.home, "machine", "DRAFTS_MINUTES", "20")
        self.assertEqual(self.local(), '# mine\nDRAFTS_MINUTES="20"   # short\n')
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        S.write(self.home, "machine", "DRAFTS_MINUTES", None)
        self.assertNotIn("DRAFTS_MINUTES", self.local())

    def test_a_written_file_sources_cleanly_in_bash(self):
        S.write(self.home, "machine", "CODEBOT_MODEL", "coder30b-iq3"); S.write(self.home, "machine", "SANDBOX_RO", "/a b,/c")
        r = subprocess.run(["bash", "-c", 'set -u; source "$1"; printf "%s|%s" "$CODEBOT_MODEL" "$SANDBOX_RO"', "_", os.path.join(self.home, "config", "local.env")],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "coder30b-iq3|/a b,/c"), r.stderr)

    def test_secrets_are_never_shown(self):
        with open(os.path.join(self.home, "config", "local.env"), "w") as f: f.write('NTFY_TOPIC="secret-topic-123"\n')
        view = json.dumps(S.view(self.home))
        self.assertNotIn("secret-topic-123", view); self.assertIn("set (hidden)", view)

class ApiCase(helpers.Sandboxed):
    def setUp(self):
        super().setUp(); self.ctx = api.Ctx(self.home)
        with open(os.path.join(self.home, "config", "local.env"), "w") as f: f.write('NTFY_TOPIC="secret-topic-123"\n')

    def test_names_and_paths_are_checked(self):
        for bad in ("../x", "", "-rf", "a/b"):
            with self.assertRaises(api.ApiError, msg=bad): self.ctx.proj_dir(bad)
        root = os.path.join(self.tmp, "root"); os.makedirs(root)
        for bad in ("../x", "/etc/passwd", "a/../../x", "x\0y"):
            with self.assertRaises(api.ApiError, msg=bad): api._safe_join(root, bad)
        os.symlink("/etc", os.path.join(root, "out"))
        with self.assertRaises(api.ApiError): api._safe_join(root, "out/passwd")

    def test_only_listed_harness_files_can_be_read(self):
        with open(os.path.join(self.state, "env.snap"), "w") as f: f.write("declare -x SECRET=1\n")
        with self.assertRaises(api.ApiError): api.file_view(self.ctx, {"v": "harness", "path": "env.snap"}, self.name)

    def test_the_topic_is_hidden_in_logs_and_harness_files(self):
        with open(os.path.join(self.state, "loop.log"), "w") as f: f.write("sent to https://ntfy.sh/secret-topic-123\n")
        self.assertNotIn("secret-topic-123", api.log(self.ctx, {}, self.name)["text"])
        self.assertNotIn("secret-topic-123", api.file_view(self.ctx, {"v": "harness", "path": "loop.log"}, self.name)["text"])
        with open(os.path.join(self.state, "project.env"), "a") as f: f.write('NTFY_TOKEN="tok-abc-999"\n')
        self.assertNotIn("tok-abc-999", api.file_view(self.ctx, {"v": "harness", "path": "project.env"}, self.name)["text"])

    def test_demo_mode_changes_nothing(self):
        ctx = api.Ctx(self.home, demo=True)
        for path, body in (("/api/settings", {"scope": "machine", "key": "DRAFTS_MINUTES", "value": "5"}), ("/api/models/delete", {"model": "x", "confirm": True}),
                           ("/api/memory", {"expertise": "x"}), ("/api/inbox/ack", {"key": "k"}), (f"/api/project/{self.name}/answer", {"text": "hi"})):
            with self.assertRaises(api.ApiError, msg=path) as cm: api.dispatch(ctx, "POST", path, {}, body)
            self.assertEqual(cm.exception.code, 409)
        with open(os.path.join(self.home, "config", "local.env")) as f: self.assertNotIn("DRAFTS_MINUTES", f.read())

    def test_projects_list_includes_title_blurb_and_attention(self):
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write("# Egg timer\n\nA small timer for boiling eggs.\n")
        rows = api.projects(self.ctx, {})["projects"]
        mine = next(p for p in rows if p["name"] == self.name)
        self.assertEqual(mine["title"], "Egg timer")
        self.assertIn("boiling eggs", mine["blurb"])
        self.assertIn("attention", mine)

    def test_delete_project_removes_state_keeps_workdir(self):
        code = self.code
        with open(os.path.join(code, "keep.txt"), "w") as f: f.write("safe\n")
        r = api.project_delete(self.ctx, {"confirm": True}, self.name)
        self.assertTrue(r["ok"])
        self.assertFalse(os.path.isdir(self.state))
        self.assertTrue(os.path.isfile(os.path.join(code, "keep.txt")))
        with self.assertRaises(api.ApiError): self.ctx.proj_dir(self.name)

    def test_delete_project_refuses_while_running(self):
        import codebot.projstate as P
        real = P.state
        def fake(home, name):
            s = real(home, name); s["state"] = "running"; return s
        P.state = fake
        try:
            with self.assertRaises(api.ApiError) as cm:
                api.project_delete(self.ctx, {"confirm": True}, self.name)
            self.assertEqual(cm.exception.code, 409)
        finally:
            P.state = real
        self.assertTrue(os.path.isdir(self.state))

class JobsCase(helpers.Sandboxed):
    def test_one_model_job_at_a_time(self):
        self.assertIsNone(jobs.owner(self.home))
        with jobs.Hold(self.home, "planning", "egg"):
            o = jobs.owner(self.home)
            self.assertEqual((o["kind"], o["name"], o["pid"]), ("planning", "egg", os.getpid()))
            with self.assertRaises(jobs.Busy) as cm: jobs.Hold(self.home, "preflight", "", wait=0.1)
            self.assertIn("planning chat egg", str(cm.exception))
        self.assertIsNone(jobs.owner(self.home))
        self.assertFalse(os.path.exists(jobs.paths(self.home)[1]))

    def test_the_lock_dies_with_its_process(self):
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib")
        p = subprocess.Popen([sys.executable, "-c", "import sys,time; from codebot import jobs; jobs.Hold(sys.argv[1], 'eval', 'm'); print('held', flush=True); time.sleep(30)", self.home],
                             stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": lib})
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            self.assertEqual(jobs.owner(self.home)["kind"], "eval")
            with contextlib.redirect_stdout(io.StringIO()) as out: self.assertEqual(jobs.main(["check", self.home, self.name]), 75)
            self.assertIn("model evaluation m", out.getvalue())
        finally: p.kill(); p.wait(); p.stdout.close()
        self.assertIsNone(jobs.owner(self.home))
        self.assertEqual(jobs.main(["check", self.home, self.name]), 0)

    def test_a_benchmark_owns_its_own_trials(self):
        o = {"kind": "benchmark", "name": "20261022-L1"}
        self.assertTrue(jobs.nested(o, "cap-20261022-l1-level_01-t1"))
        self.assertFalse(jobs.nested(o, "egg"))


class PipelineGuiCase(helpers.Sandboxed):
    """Pipeline JSON round-trip through the GUI API."""
    def setUp(self):
        super().setUp(); self.ctx = api.Ctx(self.home)
        import shutil
        src = os.path.join(os.path.dirname(__file__), "..", "pipelines", "templates")
        shutil.copytree(src, os.path.join(self.home, "pipelines", "templates"))
        from codebot.pipeline import design, load
        from codebot.config import Project
        with open(os.path.join(self.state, "project.env"), "a") as f:
            f.write(f'WORKDIR="{self.code}"\nRUNDIR="{self.code}"\nWORKFLOW="pipeline"\nPIPELINE="pipeline/project.json"\n')
        with open(os.path.join(self.code, "SPEC.md"), "w") as f: f.write("# T\n\n## Requirements\n\n1. `add(1,1)` is `2`. `m.py` provides `add(a,b)`.\n")
        bundle = load.load_project(os.path.join(self.home, "pipelines", "templates", "projects", "basic-calculator.json"))
        design.write_project_pipeline(Project(self.name, self.home), bundle)

    def test_pipeline_view_and_json(self):
        v = api.pipeline_view(self.ctx, {}, self.name)
        self.assertIn("bundle", v); self.assertEqual(v["bundle"]["team"]["id"], "solo")
        j = api.pipeline_json(self.ctx, {}, self.name)
        self.assertIn("solo-developer", j["json"])

    def test_pipeline_validate_and_save_ui_positions(self):
        j = json.loads(api.pipeline_json(self.ctx, {}, self.name)["json"])
        j["ui"] = {"team": {"positions": {"dev": {"x": 100, "y": 120}}}}
        r = api.pipeline_validate(self.ctx, {"json": j}, self.name)
        self.assertTrue(r["ok"], r)
        api.pipeline_save(self.ctx, {"json": j}, self.name)
        again = json.loads(api.pipeline_json(self.ctx, {}, self.name)["json"])
        self.assertEqual(again["ui"]["team"]["positions"]["dev"]["x"], 100)

    def test_demo_refuses_pipeline_mutations(self):
        ctx = api.Ctx(self.home, demo=True)
        with self.assertRaises(api.ApiError) as cm:
            api.pipeline_save(ctx, {"json": "{}"}, self.name)
        self.assertEqual(cm.exception.code, 409)
        with self.assertRaises(api.ApiError) as cm2:
            api.pipeline_cancel(ctx, {}, self.name)
        self.assertEqual(cm2.exception.code, 409)

    def test_pipeline_cancel_api(self):
        from codebot.config import Project
        from codebot.pipeline import schedule, runtime as R
        schedule.step(Project(self.name, self.home))
        r = api.pipeline_cancel(self.ctx, {}, self.name)
        self.assertEqual(r["status"], "CANCELLED")
        self.assertEqual(R.load_run(Project(self.name, self.home))["status"], "CANCELLED")

    def test_pipeline_init_and_templates(self):
        # wipe existing pipeline from setUp, re-init via API
        import shutil
        shutil.rmtree(os.path.join(self.state, "pipeline"))
        with open(os.path.join(self.state, "project.env"), "w") as f:
            f.write(f'WORKDIR="{self.code}"\nRUNDIR="{self.code}"\nWORKFLOW="drafts"\n')
        t = api.pipeline_templates(self.ctx, {})
        self.assertIn("solo", [x["id"] for x in t["templates"]])
        r = api.pipeline_init(self.ctx, {"template": "solo"}, self.name)
        self.assertTrue(r["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self.state, "pipeline", "project.json")))
        with open(os.path.join(self.state, "project.env")) as f:
            self.assertIn('WORKFLOW="pipeline"', f.read())
