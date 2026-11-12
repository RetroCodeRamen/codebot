"""Milestone 1: pipeline JSON load + validate. No model, no scheduler."""
import json, os, tempfile, unittest
from codebot.pipeline import load_project, validate_project, ValidationError
from codebot.pipeline.load import load_and_validate, load_doc
from codebot.pipeline.validate import validate_doc

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
T = os.path.join(REPO, "pipelines", "templates")

class TestTemplates(unittest.TestCase):
    def test_shipped_projects_validate(self):
        d = os.path.join(T, "projects")
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        self.assertGreaterEqual(len(names), 4)
        for name in names:
            path = os.path.join(d, name)
            bundle = load_project(path)
            self.assertIn("team", bundle, name)
            self.assertIn("workflow", bundle, name)
            self.assertTrue(bundle["agents"], name)

    def test_shipped_agents_validate(self):
        d = os.path.join(T, "agents")
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                validate_doc(load_doc(os.path.join(d, name)))

    def test_cli_module_ok(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "-m", "codebot.pipeline", "validate",
                            os.path.join(T, "projects", "basic-calculator.json")],
                           cwd=REPO, env={**os.environ, "PYTHONPATH": os.path.join(REPO, "lib")},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

class TestValidation(unittest.TestCase):
    def test_rejects_write_outside_read(self):
        doc = {
            "schema": "codebot.pipeline/v1", "kind": "agent", "id": "a", "name": "A", "goal": "g",
            "sees": {"read": ["src/**"], "write": ["secrets/**"]}, "memory": "none"
        }
        with self.assertRaises(ValidationError) as cm: validate_doc(doc)
        self.assertTrue(any("write path" in e for e in cm.exception.errors))

    def test_rejects_unbounded_loop(self):
        wf = {
            "schema": "codebot.pipeline/v1", "kind": "workflow", "id": "w", "name": "W",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "loop", "type": "loop"},
                {"id": "end", "type": "end"}
            ],
            "edges": [{"from": "start", "to": "loop"}, {"from": "loop", "to": "end"}]
        }
        with self.assertRaises(ValidationError) as cm: validate_doc(wf)
        self.assertTrue(any("max_iterations" in e for e in cm.exception.errors))

    def test_rejects_unreachable_node(self):
        wf = {
            "schema": "codebot.pipeline/v1", "kind": "workflow", "id": "w", "name": "W",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "a", "type": "run", "agent": "dev"},
                {"id": "orphan", "type": "run", "agent": "dev"},
                {"id": "end", "type": "end"}
            ],
            "edges": [{"from": "start", "to": "a"}, {"from": "a", "to": "end"}]
        }
        with self.assertRaises(ValidationError) as cm: validate_doc(wf)
        self.assertTrue(any("unreachable" in e for e in cm.exception.errors))

    def test_rejects_talk_to_non_member(self):
        team = {
            "schema": "codebot.pipeline/v1", "kind": "team", "id": "t", "name": "T",
            "members": [{"id": "a", "agent": "solo-developer", "can_talk_to": ["ghost"]}]
        }
        with self.assertRaises(ValidationError) as cm: validate_doc(team)
        self.assertTrue(any("can_talk_to" in e for e in cm.exception.errors))

    def test_rejects_path_escape(self):
        doc = {
            "schema": "codebot.pipeline/v1", "kind": "agent", "id": "a", "name": "A", "goal": "g",
            "sees": {"read": ["../etc/passwd"], "write": []}, "memory": "none"
        }
        with self.assertRaises(ValidationError): validate_doc(doc)

    def test_one_agent_project_roundtrip_bundle(self):
        agent = load_doc(os.path.join(T, "agents", "solo-developer.json"))
        team = load_doc(os.path.join(T, "teams", "solo.json"))
        wf = load_doc(os.path.join(T, "workflows", "solo-drafts.json"))
        bundle = validate_project({
            "project": {"schema": "codebot.pipeline/v1", "kind": "project", "id": "p", "name": "P"},
            "agents": {"solo-developer": agent}, "team": team, "workflow": wf
        })
        self.assertEqual(bundle["team"]["members"][0]["id"], "dev")

    def test_ref_escape_refused(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "projects"))
            bad = os.path.join(d, "projects", "p.json")
            with open(bad, "w") as f:
                json.dump({
                    "schema": "codebot.pipeline/v1", "kind": "project", "id": "p", "name": "P",
                    "agents": {"x": {"$ref": "../outside.json"}},
                    "team": {"schema": "codebot.pipeline/v1", "kind": "team", "id": "t", "name": "T",
                             "members": [{"id": "m", "agent": "x"}]},
                    "workflow": {"schema": "codebot.pipeline/v1", "kind": "workflow", "id": "w", "name": "W",
                                 "nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
                                 "edges": [{"from": "start", "to": "end"}]}
                }, f)
            with self.assertRaises(ValidationError): load_and_validate(bad)

if __name__ == "__main__":
    unittest.main()
