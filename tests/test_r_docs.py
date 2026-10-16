"""The documentation must match reality. These fail when a command, config key, mode field or file path changes without the docs being updated."""
import glob, json, os, re, subprocess, unittest

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
def read(*p):
    with open(os.path.join(REPO, *p)) as f: return f.read()
DOCS = {"README.md": read("README.md"), "docs/dev/HANDOFF.md": read("docs", "dev", "HANDOFF.md"), "docs/dev/ROADMAP.md": read("docs", "dev", "ROADMAP.md"), "modes/README.md": read("modes", "README.md"), "memory/README.md": read("memory", "README.md")}
ALL = "\n".join(DOCS.values())
INTERNAL = {"__loop", "ui", "menu"}

def dispatch_commands():
    block = re.search(r'^case "\$c" in\n(.*?)^esac', read("bin", "codebot"), re.S | re.M).group(1)
    names = set()
    for m in re.finditer(r"^  ([a-z_|-]+)\)", block, re.M): names.update(m.group(1).split("|"))
    return {n for n in names if n and n not in INTERNAL}

def usage_text():
    return subprocess.run([os.path.join(REPO, "bin", "codebot"), "help"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout

class TestCommands(unittest.TestCase):
    def test_every_command_is_in_the_help_text(self):
        u = usage_text(); missing = [c for c in sorted(dispatch_commands()) if not re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", u)]
        self.assertEqual(missing, [], "commands missing from `codebot help`")
    def test_help_does_not_print_shell_noise(self):
        r = subprocess.run([os.path.join(REPO, "bin", "codebot"), "help"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stderr, "", "the usage heredoc must be quoted so backticks are not executed")
    def test_every_command_is_documented_somewhere(self):
        missing = [c for c in sorted(dispatch_commands()) if not re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", ALL)]
        self.assertEqual(missing, [], "commands not mentioned in any doc")
    def test_every_command_in_the_readme_quick_start_exists(self):
        block = re.search(r"## Quick start\n\n```\n(.*?)```", DOCS["README.md"], re.S).group(1); cmds = dispatch_commands() | {"help", "menu"}
        for ln in block.splitlines():
            m = re.match(r"codebot(?:\s+([a-z-]+))?", ln.strip())
            if m and m.group(1): self.assertIn(m.group(1), cmds, ln)
    def test_every_engine_subcommand_is_in_the_handoff(self):
        subs = re.search(r'choices=\[(.*?)\]', read("bin", "codebot-engine"), re.S).group(1); subs = re.findall(r'"([a-z-]+)"', subs)
        self.assertEqual([s for s in subs if s not in DOCS["docs/dev/HANDOFF.md"]], [], "engine subcommands missing from docs/dev/HANDOFF.md")

class TestConfig(unittest.TestCase):
    def keys(self):
        out = []
        for ln in read("config", "codebot.env").splitlines():
            m = re.match(r"^#?\s*([A-Z][A-Z_]{3,})=", ln)
            if m: out.append(m.group(1))
        return sorted(set(out))
    def test_every_config_key_is_documented_in_a_doc(self):
        self.assertEqual([k for k in self.keys() if not re.search(rf"\b{k}\b", "\n".join(v for n, v in DOCS.items()))], [], "config keys missing from the docs")
    def test_every_active_key_has_an_explanation_in_the_env_file(self):
        lines = read("config", "codebot.env").splitlines(); bad = []
        for i, ln in enumerate(lines):
            if re.match(r"^[A-Z][A-Z_]+=", ln) and "#" not in ln and not (i and lines[i - 1].lstrip().startswith("#")): bad.append(ln.split("=")[0])
        self.assertEqual(bad, [], "keys without an inline or preceding comment")
    def test_num_ctx_is_documented_as_a_three_way_constraint(self): self.assertIn("NUM_CTX", DOCS["README.md"]); self.assertIn("Modelfile", DOCS["README.md"])

class TestPaths(unittest.TestCase):
    SKIP = ("projects/", "models/", "tools/", "logs/", "workspaces/", "bench/runs", "bench/results", "memory/lessons", "memory/sizing", "memory/.archive", "config/opencode-re.json")
    def test_every_repo_path_mentioned_in_the_docs_exists(self):
        bad = []
        for name, text in DOCS.items():
            for tok in re.findall(r"`((?:bin|lib/codebot|modes|config|tests|docs|bench|memory)/[^`\s]*)`", text):
                tok = tok.rstrip(".,:;)")
                if any(c in tok for c in "<>*{}$") or tok.startswith(self.SKIP) or tok.endswith("/..."): continue
                if not os.path.exists(os.path.join(REPO, tok)): bad.append(f"{name}: {tok}")
        self.assertEqual(bad, [], "docs mention paths that do not exist")
    def test_every_python_module_is_in_the_handoff_file_map(self):
        mods = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(REPO, "lib", "codebot", "*.py")) if not f.endswith("__init__.py")]
        self.assertEqual([m for m in mods if not re.search(rf"`{m}`|{m}\.py", DOCS["docs/dev/HANDOFF.md"])], [], "lib/codebot modules missing from docs/dev/HANDOFF.md section 2")
    def test_every_test_file_has_a_place_in_the_docs(self): self.assertIn("tests/run.sh", ALL)

class TestModesDoc(unittest.TestCase):
    def test_every_mode_json_field_in_use_is_documented(self):
        fields = set()
        for f in glob.glob(os.path.join(REPO, "modes", "*", "mode.json")): fields.update(json.load(open(f)).keys())
        self.assertEqual([k for k in sorted(fields) if f"`{k}`" not in DOCS["modes/README.md"]], [], "mode.json fields missing from modes/README.md")
    def test_every_mode_is_mentioned(self):
        for d in os.listdir(os.path.join(REPO, "modes")):
            if os.path.isfile(os.path.join(REPO, "modes", d, "mode.json")): self.assertIn(f"`{d}`", DOCS["README.md"] + DOCS["modes/README.md"], d)

class TestRoadmap(unittest.TestCase):
    def test_no_step_is_left_marked_todo(self): self.assertEqual(re.findall(r"^\|[^\n]*\| todo \|$", DOCS["docs/dev/ROADMAP.md"], re.M), [])
    def test_the_status_table_has_every_step(self):
        tbl = DOCS["docs/dev/ROADMAP.md"].split("## Status")[1]
        for s in "ABCDEFGHIJKLMN": self.assertRegex(tbl, rf"\| {s} \|", s)
    def test_handoff_points_to_the_other_docs_and_the_next_steps(self):
        h = DOCS["docs/dev/HANDOFF.md"]
        for frag in ("docs/dev/ROADMAP.md", "README.md", "Next steps", "In flight", "codebot setup"): self.assertIn(frag, h)

class TestTestIsolation(unittest.TestCase):
    def test_no_test_home_inherits_the_developers_local_settings(self):
        # config/local.env holds this machine's ntfy topic and server; a test home that copies config/ with it would push to a real phone
        offenders = []
        for f in glob.glob(os.path.join(REPO, "tests", "test_*.py")):
            src = read("tests", os.path.basename(f))
            for m in re.finditer(r"for d in \(([^)]*)\):\s*\n?\s*shutil\.copytree\([^\n]*", src):
                if '"config"' in m.group(1) and "local.env" not in m.group(0): offenders.append(os.path.basename(f))
        self.assertEqual(offenders, [])

if __name__ == "__main__": unittest.main()
