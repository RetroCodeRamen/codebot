"""User management."""
from . import config, permissions
from .errors import NotFound, ValidationError
from .models import User
from .utils import require_choice, require_text

class UserService:
    def __init__(self, store, clock=None):
        self.store, self.clock = store, clock

    def create_user(self, name, role="customer", email=""):
        name = require_text(name, "name", 60); require_choice(role, "role", config.ROLES)
        if any(u.name.lower() == name.lower() for u in self.store.users.values()): raise ValidationError(f"a user named '{name}' already exists")
        if email and ("@" not in email or " " in email): raise ValidationError(f"invalid email '{email}'")
        user = User(self.store.new_id("user"), name, role, email)
        self.store.users[user.id] = user; self.store.save(); return user

    def get(self, user_id):
        return self.store.user(user_id)

    def find(self, name):
        for u in self.store.users.values():
            if u.name.lower() == str(name).lower(): return u
        raise NotFound(f"no user named '{name}'")

    def list(self, role=None, active_only=False):
        users = sorted(self.store.users.values(), key=lambda u: u.id)
        return [u for u in users if (role is None or u.role == role) and (u.active or not active_only)]

    def disable(self, user_id, actor_id):
        actor = self.store.user(actor_id); permissions.require(actor, "manage_users"); user = self.store.user(user_id)
        if user.id == actor.id: raise ValidationError("you cannot disable yourself")
        user.active = False; self.store.save(); return user

    def set_role(self, user_id, role, actor_id):
        actor = self.store.user(actor_id); permissions.require(actor, "manage_users"); require_choice(role, "role", config.ROLES); user = self.store.user(user_id)
        user.role = role; self.store.save(); return user

    def agents(self):
        """Active users who can be assigned tickets."""
        return [u for u in self.list(active_only=True) if u.role in ("agent", "admin")]
