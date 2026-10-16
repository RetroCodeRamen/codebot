"""Mutants of a level's reference solution: copies with ONE small deliberate bug each (a comparison flipped, a constant off by one, and/or swapped,
a `not` dropped, a raise removed, ...). A mutant is kept only if the hidden reference tests reject it, which proves it is a real behaviour change and
not an equivalent rewrite, and the hidden tests that reject it name the requirement it breaks. A plan's coverage is the share of these mutants its
(sound) assertions also reject: two trivial assertions catch few mutants, a thorough plan catches most.

Stored as bench/ladder/catalog/<level>/mutants.json with the hash of the reference solution, so a stale file is detected. Build with
`codebot eval mutants --levels 1-3`."""
import ast, copy, hashlib, json, os, shutil, tempfile
from ..capbench import manifests as M, reference as R

CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.FloorDiv: ast.Mult, ast.Mod: ast.FloorDiv, ast.LShift: ast.RShift, ast.RShift: ast.LShift, ast.BitOr: ast.BitAnd, ast.BitAnd: ast.BitOr}

def _docstrings(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str):
            ids.add(id(n.body[0].value))
    return ids

def sites(tree):
    """[(node index in ast.walk order, operator name, description)] of every mutation this module knows for the tree."""
    doc = _docstrings(tree); out = []
    for i, n in enumerate(ast.walk(tree)):
        line = getattr(n, "lineno", 0)
        if isinstance(n, ast.Compare):
            for k, op in enumerate(n.ops):
                if type(op) in CMP: out.append((i, f"cmp:{k}", f"line {line}: {type(op).__name__} -> {CMP[type(op)].__name__}"))
        elif isinstance(n, ast.BinOp) and type(n.op) in BIN: out.append((i, "bin", f"line {line}: {type(n.op).__name__} -> {BIN[type(n.op)].__name__}"))
        elif isinstance(n, ast.BoolOp): out.append((i, "bool", f"line {line}: {type(n.op).__name__} -> {'Or' if isinstance(n.op, ast.And) else 'And'}"))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not): out.append((i, "not", f"line {line}: drop `not`"))
        elif isinstance(n, ast.Constant) and id(n) not in doc:
            v = n.value
            if isinstance(v, int) and not isinstance(v, bool): out += [(i, "int+1", f"line {line}: {v} -> {v + 1}"), (i, "int-1", f"line {line}: {v} -> {v - 1}")]
            elif isinstance(v, bool): out.append((i, "boolconst", f"line {line}: {v} -> {not v}"))
            elif isinstance(v, str) and 0 < len(v) <= 4: out.append((i, "str", f"line {line}: {v!r} -> {'#'!r}"))
        elif isinstance(n, ast.Raise): out.append((i, "noraise", f"line {line}: raise removed"))
    return out

def apply(tree, index, op):
    """A mutated copy of tree (the node at walk index `index` changed by `op`)."""
    t = copy.deepcopy(tree); n = list(ast.walk(t))[index]
    if op.startswith("cmp:"): k = int(op[4:]); n.ops[k] = CMP[type(n.ops[k])]()
    elif op == "bin": n.op = BIN[type(n.op)]()
    elif op == "bool": n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
    elif op == "not":
        for p in ast.walk(t):
            for f, v in ast.iter_fields(p):
                if v is n: setattr(p, f, n.operand)
                elif isinstance(v, list): v[:] = [n.operand if x is n else x for x in v]
    elif op == "int+1": n.value += 1
    elif op == "int-1": n.value -= 1
    elif op == "boolconst": n.value = not n.value
    elif op == "str": n.value = "#"
    elif op == "noraise":
        for p in ast.walk(t):
            for f, v in ast.iter_fields(p):
                if isinstance(v, list) and any(x is n for x in v): v[:] = [ast.Pass() if x is n else x for x in v]
    return ast.fix_missing_locations(t)

def solution_hash(m):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(M.solution_dir(m))):
        for f in sorted(files):
            if f.endswith(".py"):
                with open(os.path.join(root, f), "rb") as fh: h.update(f.encode() + b"\0" + fh.read())
    return h.hexdigest()

def path(m): return os.path.join(m["_dir"], "mutants.json")

def build(m, log=print):
    """Generate every mutant of every .py file of the reference solution and keep those the hidden tests reject. Returns the stored dict."""
    sol = M.solution_dir(m); kept, tried = [], 0
    with tempfile.TemporaryDirectory(prefix="cb-mut-") as tmp:
        base = os.path.join(tmp, "base"); R.build_solution(m, base)
        for rel in sorted(f for f in os.listdir(sol) if f.endswith(".py")):
            with open(os.path.join(sol, rel)) as f: src = f.read()
            tree = ast.parse(src)
            for idx, op, desc in sites(tree):
                tried += 1
                try: text = ast.unparse(apply(tree, idx, op))
                except Exception: continue
                ws = os.path.join(tmp, f"m{tried}"); shutil.copytree(base, ws)
                with open(os.path.join(ws, rel), "w") as f: f.write(text + "\n")
                res = R.run_tests(M.tests_dir(m), ws, per_test_s=5, file_timeout_s=60)
                bad = sorted(t for t, r in res["tests"].items() if r["status"] != "pass")
                shutil.rmtree(ws, ignore_errors=True)
                if bad:
                    kept.append({"id": f"{rel}:{idx}:{op}", "file": rel, "op": op, "desc": desc, "source": text + "\n", "hidden_failures": len(bad),
                                 "requirements": sorted({R.req_of(t) for t in bad if R.req_of(t)})})
        log(f"{m['_id']}: {tried} mutants tried, {len(kept)} rejected by the hidden tests (kept)")
    out = {"level": m["_id"], "manifest_version": m.get("version"), "solution_sha256": solution_hash(m), "tried": tried, "mutants": kept}
    with open(path(m), "w") as f: json.dump(out, f, indent=1)
    return out

def load(m):
    """The stored mutants, or None if missing or built from a different reference solution."""
    try:
        with open(path(m)) as f: d = json.load(f)
    except (OSError, ValueError): return None
    return d if d.get("solution_sha256") == solution_hash(m) else None
