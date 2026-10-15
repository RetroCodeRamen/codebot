from base import Base
from helpdesk.errors import NotFound, PermissionDenied, ValidationError

class TestUsers(Base):
    def test_create_assigns_increasing_ids(self): self.assertEqual([self.admin.id, self.agent.id, self.cust.id], [1, 2, 3])
    def test_names_are_unique_ignoring_case(self):
        with self.assertRaises(ValidationError): self.hd.users.create_user("ada", "agent")
    def test_invalid_role_and_email(self):
        with self.assertRaises(ValidationError): self.hd.users.create_user("X", "boss")
        with self.assertRaises(ValidationError): self.hd.users.create_user("X", "agent", "not-an-email")
    def test_empty_name(self):
        with self.assertRaises(ValidationError): self.hd.users.create_user("   ")
    def test_find_by_name(self): self.assertEqual(self.hd.users.find("BOB").id, self.agent.id)
    def test_find_unknown(self):
        with self.assertRaises(NotFound): self.hd.users.find("nobody")
    def test_list_by_role(self): self.assertEqual([u.name for u in self.hd.users.list(role="agent")], ["Bob"])
    def test_disable_needs_manage_users(self):
        with self.assertRaises(PermissionDenied): self.hd.users.disable(self.cust.id, self.agent.id)
        self.hd.users.disable(self.cust.id, self.admin.id); self.assertFalse(self.hd.users.get(self.cust.id).active)
    def test_cannot_disable_yourself(self):
        with self.assertRaises(ValidationError): self.hd.users.disable(self.admin.id, self.admin.id)
    def test_disabled_users_are_not_agents(self): self.hd.users.disable(self.agent.id, self.admin.id); self.assertEqual([u.name for u in self.hd.users.agents()], ["Ada"])
    def test_set_role(self): self.hd.users.set_role(self.cust.id, "agent", self.admin.id); self.assertEqual(self.hd.users.get(self.cust.id).role, "agent")
    def test_users_persist(self): self.assertEqual([u.name for u in self.open().users.list()], ["Ada", "Bob", "Cy"])
    def test_disabled_users_cannot_act(self):
        self.hd.users.disable(self.agent.id, self.admin.id)
        with self.assertRaises(PermissionDenied): self.hd.tickets.assign(self.ticket().id, self.admin.id, self.agent.id)
