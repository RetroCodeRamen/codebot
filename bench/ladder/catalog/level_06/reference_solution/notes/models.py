from dataclasses import dataclass, field, asdict

@dataclass
class Note:
    id: int
    title: str
    body: str = ""
    tags: list = field(default_factory=list)
    created: str = ""
    updated: str = ""
    pinned: bool = False

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(id=d["id"], title=d["title"], body=d.get("body", ""), tags=list(d.get("tags", [])), created=d.get("created", ""), updated=d.get("updated", ""), pinned=bool(d.get("pinned", False)))
