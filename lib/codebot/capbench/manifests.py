"""The catalog: bench/ladder/catalog/<id>/manifest.json + SPEC.md + seed/ + reference_solution/ + reference_tests/ (+ optional reference_plan.json).
A manifest is machine-readable and versioned; `validate_manifest` is what the framework tests (and `codebot bench validate`) use to keep it honest."""
import json, os, re
from .. import planner

def ladder_dir(home): return os.path.join(home, "bench", "ladder")
def catalog_dir(home): return os.path.join(ladder_dir(home), "catalog")

class CatalogError(Exception):
    pass

def load(home, ident):
    """ident: 'level_07', '7', 7 or 'growing'."""
    s = str(ident)
    if s.isdigit(): s = f"level_{int(s):02d}"
    d = os.path.join(catalog_dir(home), s); mf = os.path.join(d, "manifest.json")
    if not os.path.isfile(mf): raise CatalogError(f"no benchmark '{ident}' in {catalog_dir(home)} (try: codebot bench list)")
    with open(mf) as f: m = json.load(f)
    m["_dir"] = d; m["_id"] = s
    return m

def levels(home):
    """Manifests of the 12 ladder levels in difficulty order."""
    out = []
    for n in sorted(os.listdir(catalog_dir(home))) if os.path.isdir(catalog_dir(home)) else []:
        if re.fullmatch(r"level_\d\d", n) and os.path.isfile(os.path.join(catalog_dir(home), n, "manifest.json")): out.append(load(home, n))
    return sorted(out, key=lambda m: m["level"])

def parse_levels(home, text):
    """'1', '1-4', '1,3,5-6' -> sorted list of level numbers that exist."""
    have = {m["level"] for m in levels(home)}; want = set()
    for part in str(text).split(","):
        part = part.strip()
        if not part: continue
        if "-" in part: a, b = part.split("-", 1); want.update(range(int(a), int(b) + 1))
        else: want.add(int(part))
    bad = sorted(want - have)
    if bad: raise CatalogError(f"unknown level(s) {bad}; the catalog has {sorted(have)}")
    return sorted(want)

def spec_path(m, stage=None):
    return os.path.join(m["_dir"], stage["spec"] if stage else m["spec"])

def spec_text(m, stage=None):
    with open(spec_path(m, stage)) as f: return f.read()

def seed_dir(m): return os.path.join(m["_dir"], m.get("seed", "seed"))
def solution_dir(m, stage=None): return os.path.join(m["_dir"], stage["solution"] if stage else m.get("reference_solution", "reference_solution"))
def tests_dir(m, stage=None): return os.path.join(m["_dir"], (stage or m).get("reference_tests", "reference_tests"))
def plan_path(m, stage=None):
    p = os.path.join(m["_dir"], stage.get("reference_plan", "") if stage else m.get("reference_plan", "reference_plan.json"))
    return p if os.path.isfile(p) else None

def requirements(m, stage=None): return (stage or m)["requirements"]

def validate_manifest(m, home=None):
    """List of problems (empty = fine): required fields, files exist, every requirement has a title, numbered spec items match, planner limits are sane."""
    p = []
    stages = m.get("stages")
    for k in ("id", "title", "version", "difficulty", "primary_challenge"):
        if not m.get(k): p.append(f"missing {k}")
    if not stages:
        for k in ("level", "spec", "requirements", "planner", "expected_tasks"):
            if k not in m: p.append(f"missing {k}")
        items = [(None, m)]
    else:
        items = [(s["id"], s) for s in stages]
        for s in stages:
            for k in ("id", "title", "spec", "requirements", "solution", "reference_tests", "planner", "expected_tasks"):
                if k not in s: p.append(f"stage {s.get('id')}: missing {k}")
    for sid, it in items:
        tag = f"{sid}: " if sid else ""
        sp = os.path.join(m["_dir"], it.get("spec", "SPEC.md"))
        if not os.path.isfile(sp): p.append(f"{tag}spec file {it.get('spec')} does not exist"); continue
        with open(sp) as f: spec = f.read()
        n = planner.spec_items(spec)
        ids = [r.get("id") for r in it.get("requirements", [])]
        if len(ids) != len(set(ids)): p.append(f"{tag}duplicate requirement ids")
        for r in it.get("requirements", []):
            if not re.fullmatch(r"R\d+", r.get("id", "")) or not r.get("title"): p.append(f"{tag}requirement {r} needs an id like R3 and a title")
        if n != len(it.get("requirements", [])): p.append(f"{tag}the spec has {n} numbered items but the manifest lists {len(it.get('requirements', []))} requirements")
        pl = it.get("planner", {})
        if not (1 <= pl.get("max_tasks", 0) <= 40): p.append(f"{tag}planner.max_tasks must be 1..40")
        if pl.get("max_tasks", 0) < n: p.append(f"{tag}planner.max_tasks ({pl.get('max_tasks')}) is below the number of spec items ({n}): the completeness check could never be satisfied")
        if not os.path.isdir(tests_dir(m, it if sid else None)): p.append(f"{tag}no reference tests directory")
    if not stages and not os.path.isdir(seed_dir(m)): p.append("no seed directory")
    return p

def manifest_items(m):
    """Number of numbered requirements in a (non-staged) manifest's spec."""
    return planner.spec_items(spec_text(m))
