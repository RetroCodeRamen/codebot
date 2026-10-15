"""Plain data classes. They know how to turn themselves into dicts and back; they contain no business rules."""
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class User:
    id: int
    name: str
    role: str
    email: str = ""
    active: bool = True

    def to_dict(self):
        return {"id": self.id, "name": self.name, "role": self.role, "email": self.email, "active": self.active}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["name"], d["role"], d.get("email", ""), bool(d.get("active", True)))

@dataclass
class Comment:
    id: int
    ticket_id: int
    author_id: int
    body: str
    ts: str
    internal: bool = False

    def to_dict(self):
        return {"id": self.id, "ticket_id": self.ticket_id, "author_id": self.author_id, "body": self.body, "ts": self.ts, "internal": self.internal}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["ticket_id"], d["author_id"], d["body"], d["ts"], bool(d.get("internal", False)))

@dataclass
class Ticket:
    id: int
    title: str
    description: str
    requester_id: int
    assignee_id: Optional[int] = None
    status: str = "open"
    priority: str = "normal"
    category: str = "general"
    created: str = ""
    updated: str = ""
    sla_due: Optional[str] = None
    closed_at: Optional[str] = None
    resolution: Optional[str] = None
    comments: List[Comment] = field(default_factory=list)
    watchers: List[int] = field(default_factory=list)
    attachments: List[dict] = field(default_factory=list)
    links: List[dict] = field(default_factory=list)

    def to_dict(self):
        return {"id": self.id, "title": self.title, "description": self.description, "requester_id": self.requester_id, "assignee_id": self.assignee_id,
                "status": self.status, "priority": self.priority, "category": self.category, "created": self.created, "updated": self.updated,
                "sla_due": self.sla_due, "closed_at": self.closed_at, "resolution": self.resolution, "comments": [c.to_dict() for c in self.comments], "watchers": list(self.watchers), "attachments": [dict(a) for a in self.attachments], "links": [dict(l) for l in self.links]}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["title"], d["description"], d["requester_id"], d.get("assignee_id"), d.get("status", "open"), d.get("priority", "normal"), d.get("category", "general"),
                   d.get("created", ""), d.get("updated", ""), d.get("sla_due"), d.get("closed_at"), d.get("resolution"), [Comment.from_dict(c) for c in d.get("comments", [])], list(d.get("watchers", [])), [dict(a) for a in d.get("attachments", [])], [dict(l) for l in d.get("links", [])])

    @property
    def is_open(self):
        return self.status != "closed"
