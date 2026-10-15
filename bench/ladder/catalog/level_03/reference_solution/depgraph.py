import heapq

class DependencyError(Exception):
    pass

class MissingDependencyError(DependencyError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__("missing dependencies: " + "; ".join(f"{n} needs {', '.join(m)}" for n, m in sorted(missing.items())))

class CycleError(DependencyError):
    def __init__(self, cycle):
        self.cycle = cycle
        super().__init__("dependency cycle: " + " -> ".join(cycle + cycle[:1]))

def _norm(graph):
    """Validated copy: {node: set(deps)}."""
    if not isinstance(graph, dict): raise TypeError("graph must be a dict")
    out = {}
    for k, v in graph.items():
        if not isinstance(k, str): raise TypeError("node names must be str")
        if isinstance(v, (str, bytes)): raise TypeError("dependencies must not be a bare string")
        try: deps = list(v)
        except TypeError: raise TypeError("dependencies must be iterable") from None
        for d in deps:
            if not isinstance(d, str): raise TypeError("dependency names must be str")
        out[k] = set(deps)
    return out

def _missing(g):
    return {n: sorted(d for d in deps if d not in g) for n, deps in sorted(g.items()) if any(d not in g for d in deps)}

def _find_cycle(g):
    state, path = {}, []
    def visit(n):
        state[n] = 1; path.append(n)
        for d in sorted(g[n]):
            if d not in g: continue
            if state.get(d) == 1:
                cyc = path[path.index(d):]; i = cyc.index(min(cyc)); return cyc[i:] + cyc[:i]
            if d not in state:
                r = visit(d)
                if r: return r
        path.pop(); state[n] = 2; return None
    for n in sorted(g):
        if n not in state:
            r = visit(n)
            if r: return r
    return []

def find_cycle(graph): return _find_cycle(_norm(graph))

def _order(g):
    miss = _missing(g)
    if miss: raise MissingDependencyError(miss)
    indeg = {n: len(d) for n, d in g.items()}; users = {n: [] for n in g}
    for n, deps in g.items():
        for d in deps: users[d].append(n)
    heap = [n for n, k in indeg.items() if k == 0]; heapq.heapify(heap); out = []
    while heap:
        n = heapq.heappop(heap); out.append(n)
        for u in users[n]:
            indeg[u] -= 1
            if indeg[u] == 0: heapq.heappush(heap, u)
    if len(out) != len(g): raise CycleError(_find_cycle(g))
    return out

def resolve(graph): return _order(_norm(graph))

def groups(graph):
    g = _norm(graph); order = _order(g); level = {}
    for n in order: level[n] = 1 + max((level[d] for d in g[n]), default=-1)
    out = []
    for n in order:
        while len(out) <= level[n]: out.append([])
        out[level[n]].append(n)
    return [sorted(x) for x in out]

def _known(g, node):
    if node in g: return True
    return any(node in deps for deps in g.values())

def transitive_deps(graph, node):
    g = _norm(graph)
    if not _known(g, node): raise KeyError(node)
    seen, stack = set(), list(g.get(node, ()))
    while stack:
        n = stack.pop()
        if n in seen: continue
        seen.add(n); stack.extend(g.get(n, ()))
    seen.discard(node); return seen

def dependents(graph, node):
    g = _norm(graph)
    if not _known(g, node): raise KeyError(node)
    users = {}
    for n, deps in g.items():
        for d in deps: users.setdefault(d, set()).add(n)
    seen, stack = set(), list(users.get(node, ()))
    while stack:
        n = stack.pop()
        if n in seen: continue
        seen.add(n); stack.extend(users.get(n, ()))
    seen.discard(node); return sorted(seen)

def resolve_subset(graph, targets):
    g = _norm(graph); targets = list(targets)
    for t in targets:
        if t not in g: raise KeyError(t)
    keep, stack = set(), list(targets)
    while stack:
        n = stack.pop()
        if n in keep or n not in g: continue
        keep.add(n); stack.extend(g[n])
    return _order({n: set(d for d in g[n]) for n in keep})
