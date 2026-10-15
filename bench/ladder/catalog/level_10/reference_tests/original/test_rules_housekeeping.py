import json
from base import Base
from helpdesk import housekeeping, permissions, rules
from helpdesk.errors import ValidationError

class TestRules(Base):
    def write(self, data): json.dump(data, open(self.path + ".rules.json", "w"))
    def test_no_file_means_no_rules(self): self.assertEqual(self.hd.rules.rules(), []); self.assertEqual(self.hd.rules.apply(self.ticket().id, self.admin.id), [])
    def test_matching_rules_change_priority_and_assignee(self):
        self.write([{"name": "billing", "when": {"category": "billing", "priority": "low"}, "then": {"priority": "normal", "assign": "Bob"}}]); t = self.ticket("Invoice", priority="low", category="billing")
        self.assertEqual(self.hd.rules.apply(t.id, self.admin.id), ["billing: priority low -> normal", "billing: assigned to Bob"]); t = self.hd.tickets.get(t.id); self.assertEqual((t.priority, t.assignee_id), ("normal", self.agent.id))
    def test_non_matching_rules_do_nothing(self): self.write([{"when": {"category": "billing"}, "then": {"priority": "urgent"}}]); self.assertEqual(self.hd.rules.apply(self.ticket().id, self.admin.id), [])
    def test_later_rules_see_earlier_changes(self):
        self.write([{"name": "a", "when": {"priority": "low"}, "then": {"priority": "high"}}, {"name": "b", "when": {"priority": "high"}, "then": {"priority": "urgent"}}]); t = self.ticket(priority="low")
        self.assertEqual(len(self.hd.rules.apply(t.id, self.admin.id)), 2); self.assertEqual(self.hd.tickets.get(t.id).priority, "urgent")
    def test_malformed_rules(self):
        for bad in ({"when": {}}, [{"when": {"color": "x"}, "then": {}}], [{"when": {}, "then": {"explode": 1}}], "text"):
            self.write(bad)
            with self.assertRaises(ValidationError): self.hd.rules.rules()
    def test_unknown_assignee(self):
        self.write([{"when": {}, "then": {"assign": "Nobody"}}])
        with self.assertRaises(ValidationError): self.hd.rules.apply(self.ticket().id, self.admin.id)
    def test_matches_helper(self): self.assertTrue(rules.matches({"when": {"status": "open"}}, self.ticket())); self.assertFalse(rules.matches({"when": {"status": "closed"}}, self.ticket()))

class TestHousekeeping(Base):
    def test_clean_data_has_no_problems(self): self.ticket(); self.assertEqual(housekeeping.check_integrity(self.hd.store), [])
    def test_dangling_references_are_found_and_repaired(self):
        t = self.ticket(); t.watchers.append(99); t.links.append({"kind": "related", "target": 77}); t.assignee_id = 55
        problems = housekeeping.check_integrity(self.hd.store); self.assertEqual(len(problems), 3); self.assertIn("unknown assignee 55", problems[0]); self.assertEqual(housekeeping.repair(self.hd.store), 2); self.assertEqual(len(housekeeping.check_integrity(self.hd.store)), 1)
    def test_counters_behind_are_found_and_advanced(self):
        self.ticket(); self.hd.store.next["ticket"] = 1; self.assertIn("the ticket id counter is behind the highest ticket id", housekeeping.check_integrity(self.hd.store)); housekeeping.repair(self.hd.store); self.assertEqual(housekeeping.check_integrity(self.hd.store), [])
    def test_misplaced_comment(self):
        t = self.ticket(); self.hd.tickets.add_comment(t.id, self.agent.id, "x"); t.comments[0].ticket_id = 42; self.assertTrue(any("belongs to ticket #42" in p for p in housekeeping.check_integrity(self.hd.store)))
    def test_permission_matrix_lists_every_role(self): m = permissions.matrix(); self.assertIn("customer", m); self.assertIn("manage_users", m); self.assertEqual(len(m.splitlines()), 1 + len({a for acts in permissions.ACTIONS.values() for a in acts}))
