"""Plain data classes. They know how to turn themselves into dicts and back; they contain no business rules."""
from dataclasses import dataclass, field
from typing import List, Optional

from . import config


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


def severity_from_priority(priority):
    """Old priority name -> severity name (ValueError for anything else)."""
    if priority not in config.PRIORITY_TO_SEVERITY:
        raise ValueError(f"invalid priority '{priority}'")
    return config.PRIORITY_TO_SEVERITY[priority]


@dataclass(init=False)
class Ticket:
    id: int
    title: str
    description: str
    requester_id: int
    assignee_id: Optional[int]
    status: str
    severity: str
    category: str
    created: str
    updated: str
    sla_due: Optional[str]
    closed_at: Optional[str]
    resolution: Optional[str]
    comments: List[Comment]
    watchers: List[int]
    attachments: List[dict]
    links: List[dict]
    labels: List[str]
    merged_into: Optional[int]

    def __init__(self, id, title, description, requester_id, assignee_id=None, status="open", priority=None, category="general", created="", updated="",
                 sla_due=None, closed_at=None, resolution=None, comments=None, watchers=None, attachments=None, links=None, *, severity=None, labels=None, merged_into=None):
        """`priority` (the old name of the scale) and `severity` (the new one) are two spellings of the same field; giving both requires that they agree."""
        by_priority = severity_from_priority(priority) if priority is not None else None
        if severity is not None and severity not in config.SEVERITIES:
            raise ValueError(f"invalid severity '{severity}'")
        if by_priority is not None and severity is not None and by_priority != severity:
            raise ValueError("priority and severity disagree")
        self.id, self.title, self.description, self.requester_id, self.assignee_id, self.status = id, title, description, requester_id, assignee_id, status
        self.severity = severity or by_priority or "medium"
        self.category, self.created, self.updated, self.sla_due, self.closed_at, self.resolution = category, created, updated, sla_due, closed_at, resolution
        self.comments = list(comments or [])
        self.watchers = list(watchers or [])
        self.attachments = [dict(a) for a in attachments or []]
        self.links = [dict(l) for l in links or []]
        self.labels = list(labels or [])
        self.merged_into = merged_into

    @property
    def priority(self):
        """The severity under its old name (medium -> normal, critical -> urgent). Assigning an old name changes the severity."""
        return config.SEVERITY_TO_PRIORITY[self.severity]

    @priority.setter
    def priority(self, value):
        self.severity = severity_from_priority(value)

    def to_dict(self):
        """The stored form (it has `severity`, not `priority`)."""
        return {"id": self.id, "title": self.title, "description": self.description, "requester_id": self.requester_id, "assignee_id": self.assignee_id,
                "status": self.status, "severity": self.severity, "category": self.category, "created": self.created, "updated": self.updated,
                "sla_due": self.sla_due, "closed_at": self.closed_at, "resolution": self.resolution, "comments": [c.to_dict() for c in self.comments],
                "watchers": list(self.watchers), "attachments": [dict(a) for a in self.attachments], "links": [dict(l) for l in self.links],
                "labels": list(self.labels), "merged_into": self.merged_into}

    @classmethod
    def from_dict(cls, d):
        severity = d.get("severity") or (severity_from_priority(d["priority"]) if d.get("priority") else None)
        return cls(d["id"], d["title"], d["description"], d["requester_id"], d.get("assignee_id"), d.get("status", "open"), None, d.get("category", "general"),
                   d.get("created", ""), d.get("updated", ""), d.get("sla_due"), d.get("closed_at"), d.get("resolution"), [Comment.from_dict(c) for c in d.get("comments", [])],
                   d.get("watchers", []), d.get("attachments", []), d.get("links", []), severity=severity, labels=d.get("labels", []), merged_into=d.get("merged_into"))

    @property
    def is_open(self):
        return self.status != "closed"
