"""Enforce agent sees.read / sees.write on workspace paths. Harness-side, not prompt-side."""
import os, fnmatch
from .validate import ValidationError

def _norm(rel):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"): raise ValidationError([f"unsafe path {rel!r}"])
    return rel

def _match(rel, patterns):
    rel = _norm(rel)
    for p in patterns or []:
        p = p.replace("\\", "/")
        if p in ("*", "**"): return True
        if p.endswith("/**"):
            root = p[:-3].rstrip("/")
            if rel == root or rel.startswith(root + "/"): return True
            continue
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p): return True
        if rel == p: return True
    return False

def can_read(agent, rel):
    sees = (agent or {}).get("sees") or {}
    return _match(rel, sees.get("read") or [])

def can_write(agent, rel):
    sees = (agent or {}).get("sees") or {}
    return _match(rel, sees.get("write") or []) and can_read(agent, rel)

def resolve(workspace, rel):
    """Absolute path under workspace for a relative path; refuses escape."""
    rel = _norm(rel)
    root = os.path.realpath(workspace)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep): raise ValidationError([f"path escapes workspace: {rel!r}"])
    return path

def _parent(workspace, rel, create=False):
    """(directory fd, file name) for rel, reached from the workspace one component at a time without following a symbolic link. A check of the
    path as text followed by an open of the path can be made to disagree (a link put in place in between, or already there); a walk by
    descriptor cannot: whatever is opened is inside the workspace."""
    parts = _norm(rel).split("/"); fd = os.open(os.path.realpath(workspace), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            if part in ("", "."): continue
            try: nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create: raise
                os.mkdir(part, dir_fd=fd); nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
    except OSError as e:
        os.close(fd)
        if isinstance(e, FileNotFoundError): raise
        raise ValidationError([f"{rel}: a directory on the way is a symbolic link or not a directory"])
    return fd, parts[-1]

def read_text(workspace, agent, rel, limit=200_000):
    if not can_read(agent, rel): raise ValidationError([f"read denied: {rel}"])
    fd, name = _parent(workspace, rel)
    try:
        try: f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError as e:
            if isinstance(e, FileNotFoundError): raise
            raise ValidationError([f"{rel} is a symbolic link; a link is not read"])
        with os.fdopen(f, "r", encoding="utf-8", errors="replace") as fh: return fh.read(limit)
    finally: os.close(fd)

def write_text(workspace, agent, rel, text):
    """Atomic: the new text is written beside the file and renamed over it (a link of that name is replaced, never followed)."""
    if not can_write(agent, rel): raise ValidationError([f"write denied: {rel}"])
    fd, name = _parent(workspace, rel, create=True)
    try:
        tmp = f".{name}.{os.getpid()}.tmp"
        f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        try:
            with os.fdopen(f, "w", encoding="utf-8") as fh: fh.write(text); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try: os.unlink(tmp, dir_fd=fd)
            except OSError: pass
            raise
    finally: os.close(fd)
    return rel
