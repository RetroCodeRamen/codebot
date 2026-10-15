import ast, importlib, os, subprocess, sys, tempfile, unittest

WS = os.getcwd()
PKG = os.path.join(WS, "notes")

def modules():
    return sorted(f for f in os.listdir(PKG) if f.endswith(".py")) if os.path.isdir(PKG) else []

def run(code, cwd=None, env=None):
    e = dict(os.environ, PYTHONPATH=WS); e.update(env or {}); p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=cwd or tempfile.mkdtemp(prefix="g8-"), env=e, timeout=120); return p

def imports_of(name):
    tree = ast.parse(open(os.path.join(PKG, name)).read()); found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level == 1 and n.module: found.add(n.module.split(".")[0])
            elif n.level == 1:
                for a in n.names: found.add(a.name)
            elif n.module and n.module.startswith("notes."): found.add(n.module.split(".")[1])
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("notes."): found.add(a.name.split(".")[1])
    return found

class TestPackage(unittest.TestCase):
    def test_r01_a_package_replaces_the_module(self): self.assertTrue(os.path.isfile(os.path.join(PKG, "__init__.py"))); self.assertFalse(os.path.exists(os.path.join(WS, "notes.py"))); self.assertTrue(os.path.isfile(os.path.join(PKG, "__main__.py")))
    def test_r01_the_public_names_are_still_importable(self):
        p = run("from notes import NoteStore, NotesError, ValidationError, NotFound, StoreError, AuthError, PermissionDenied, Worker, make_server, sync_remote, main; print('ok')"); self.assertEqual((p.returncode, p.stdout.strip()), (0, "ok"), p.stderr)
    def test_r01_python_dash_m_works(self):
        d = tempfile.mkdtemp(prefix="g8m-"); env = dict(os.environ, PYTHONPATH=WS, NOTES_FILE=os.path.join(d, "n.db")); p = subprocess.run([sys.executable, "-m", "notes", "add", "Hello"], capture_output=True, text=True, cwd=d, env=env, timeout=60); self.assertEqual((p.returncode, p.stdout), (0, "Added note 1\n"), p.stderr)
        p = subprocess.run([sys.executable, "-m", "notes", "list"], capture_output=True, text=True, cwd=d, env=env, timeout=60); self.assertEqual(p.stdout, "  1  Hello\n")
    def test_r02_the_required_modules_define_the_objects(self):
        p = run("import notes, notes.errors, notes.store, notes.http_api, notes.cli, notes.worker, notes.sync\n"
                "pairs = [(notes.NoteStore, notes.store), (notes.make_server, notes.http_api), (notes.main, notes.cli), (notes.Worker, notes.worker), (notes.sync_remote, notes.sync)] + [(getattr(notes, n), notes.errors) for n in ('NotesError', 'ValidationError', 'NotFound', 'StoreError', 'AuthError', 'PermissionDenied')]\n"
                "bad = [o.__name__ for o, m in pairs if o.__module__ != m.__name__]\nprint('bad:', bad)"); self.assertEqual(p.stdout.strip(), "bad: []", p.stderr)
    def test_r02_enough_modules(self): names = [m for m in modules() if m not in ("__init__.py", "__main__.py")]; self.assertGreaterEqual(len(names), 10, names)
    def test_r02_the_init_module_only_reexports(self):
        src = open(os.path.join(PKG, "__init__.py")).read(); tree = ast.parse(src); self.assertLessEqual(len(src.splitlines()), 25)
        for n in tree.body:
            ok = isinstance(n, (ast.Import, ast.ImportFrom)) or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)) or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in n.targets))
            self.assertTrue(ok, f"logic in __init__.py: {ast.dump(n)[:80]}")
    def test_r03_no_file_is_longer_than_250_lines(self):
        for m in modules():
            n = len(open(os.path.join(PKG, m)).read().splitlines()); self.assertLessEqual(n, 250, f"{m} has {n} lines")
    def test_r03_every_module_imports_on_its_own(self):
        for m in modules():
            if m == "__main__.py": continue
            mod = "notes" if m == "__init__.py" else "notes." + m[:-3]; p = run(f"import {mod}"); self.assertEqual(p.returncode, 0, f"{mod}: {p.stderr[-300:]}")
    def test_r03_importing_has_no_side_effects(self):
        d = tempfile.mkdtemp(prefix="g8s-"); p = run("import threading, os, sys\nbefore = (threading.active_count(), sorted(os.listdir('.')))\nimport notes\nprint(before == (threading.active_count(), sorted(os.listdir('.'))), repr(sys.stdout.tell() if False else ''))", cwd=d); self.assertEqual(p.stdout.split()[0], "True", p.stderr); self.assertEqual(p.stderr, "")
    def test_r04_layering(self):
        self.assertEqual({x for x in imports_of("errors.py") if x != "__future__"}, set())
        self.assertFalse(imports_of("store.py") & {"http_api", "cli", "worker"}, imports_of("store.py")); self.assertNotIn("cli", imports_of("http_api.py"))
    def test_r04_the_command_line_module_is_on_top(self):
        for m in modules():
            if m in ("cli.py", "__main__.py", "__init__.py"): continue
            self.assertNotIn("cli", imports_of(m), m)
    def test_r05_a_quick_end_to_end_run_through_the_package(self):
        code = ("import notes, tempfile, os, json, http.client\n"
                "d = tempfile.mkdtemp(); s = notes.NoteStore(os.path.join(d, 'n.db'), clock=lambda: '2024-03-01T09:00:00', device_id='D'); s.add('One', 'b', ['t']); s.create_user('alice', 'password1'); s.set_reminder(1, '2024-01-01T00:00:00')\n"
                "w = notes.Worker(s); r = w.run_once(); srv = notes.make_server(s).start(); c = http.client.HTTPConnection('127.0.0.1', srv.port); c.request('GET', '/notes'); body = json.loads(c.getresponse().read()); srv.stop()\n"
                "print(r['fired'], body['total'], s.snapshot()['device'], len(s.notifications()))\n")
        p = run(code); self.assertEqual(p.stdout.strip(), "[1] 1 D 1", p.stderr)
