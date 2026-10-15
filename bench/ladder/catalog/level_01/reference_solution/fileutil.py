import re

def safe_filename(name):
    s = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")[:255]
    return s or "unnamed"

def split_ext(name):
    if "/" in name: raise ValueError("name must not contain /")
    i = name.rfind(".")
    if i <= 0 or i == len(name) - 1: return (name, "")
    return (name[:i], name[i:])
