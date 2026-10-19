"""Dependency resolution engine for build systems.

Public API:
    resolve(graph) -> list[str]
    groups(graph) -> list[list[str]]
    transitive_deps(graph, node) -> set[str]
    dependents(graph, node) -> list[str]
    find_cycle(graph) -> list[str]
    resolve_subset(graph, targets) -> list[str]
"""


class DependencyError(Exception):
    """Base class for dependency-related errors."""
    pass


class MissingDependencyError(DependencyError):
    """Raised when a dependency is not a key in the graph."""

    def __init__(self, missing):
        # missing: dict mapping node -> sorted list of missing dep names
        self.missing = missing
        names = sorted(
            dep for deps in missing.values() for dep in deps
        )
        super().__init__(
            "Missing dependencies: " + ", ".join(names)
        )


class CycleError(DependencyError):
    """Raised when a cycle is detected and no dependencies are missing."""

    def __init__(self, cycle):
        # cycle: list of node names forming the cycle
        self.cycle = cycle
        arrow = " -> ".join(cycle + [cycle[0]])
        super().__init__(arrow)


def _validate(graph):
    """Validate that *graph* is a dict of str -> iterable of str.

    Raises TypeError with a descriptive message if any invariant is broken.
    """
    if not isinstance(graph, dict):
        raise TypeError(
            "graph must be a dict, got %s" % type(graph).__name__
        )
    for node, deps in graph.items():
        if not isinstance(node, str):
            raise TypeError(
                "node keys must be str, got %s" % type(node).__name__
            )
        if isinstance(deps, (str, bytes)):
            raise TypeError(
                "dependency values must be iterable of str, got %s" %
                type(deps).__name__
            )
        if not hasattr(deps, "__iter__"):
            raise TypeError(
                "dependency values must be iterable of str, got %s" %
                type(deps).__name__
            )
        for dep in deps:
            if not isinstance(dep, str):
                raise TypeError(
                    "dependency values must be str, got %s" %
                    type(dep).__name__
                )


def _normalize_deps(graph):
    """Return a new dict mapping each node to a *sorted* list of its deps.

    Duplicates are removed; order is deterministic.
    """
    out = {}
    for node in sorted(graph.keys()):
        deps = sorted(set(graph[node]))
        out[node] = deps
    return out


def resolve(graph):
    """Return all nodes in dependency order (topological sort)."""
    _validate(graph)
    norm = _normalize_deps(graph)
    missing = {}
    for node in sorted(norm.keys()):
        for dep in norm[node]:
            if dep not in norm:
                missing.setdefault(node, []).append(dep)
    if missing:
        raise MissingDependencyError(missing)

    placed = set()
    order = []
    while len(placed) < len(norm):
        ready = sorted(
            n for n in norm if n not in placed and
            all(d in placed for d in norm[n])
        )
        if not ready:
            # Cycle detected (no missing deps already checked)
            cycle = find_cycle(graph)
            raise CycleError(cycle)
        for n in ready:
            order.append(n)
            placed.add(n)
    return order


def groups(graph):
    """Return nodes grouped into parallel build levels."""
    _validate(graph)
    norm = _normalize_deps(graph)
    missing = {}
    for node in sorted(norm.keys()):
        for dep in norm[node]:
            if dep not in norm:
                missing.setdefault(node, []).append(dep)
    if missing:
        raise MissingDependencyError(missing)

    level = {}
    result = []
    while len(level) < len(norm):
        current = sorted(
            n for n in norm if n not in level and
            all(d in level for d in norm[n])
        )
        if not current:
            cycle = find_cycle(graph)
            raise CycleError(cycle)
        for n in current:
            level[n] = len(result)
        result.append(current)
    return result


def transitive_deps(graph, node):
    """Return the set of all transitive dependencies of *node*."""
    _validate(graph)
    norm = _normalize_deps(graph)
    if node not in norm:
        raise KeyError(node)

    visited = set()
    stack = list(norm[node])
    while stack:
        dep = stack.pop()
        if dep in visited:
            continue
        if dep not in norm:
            raise KeyError(dep)
        visited.add(dep)
        stack.extend(norm[dep])
    return visited


def dependents(graph, node):
    """Return sorted list of all nodes that depend on *node*."""
    _validate(graph)
    norm = _normalize_deps(graph)
    if node not in norm:
        raise KeyError(node)

    # Build reverse adjacency: for each node, which nodes depend on it
    reverse = {n: [] for n in norm}
    for n, deps in norm.items():
        for d in deps:
            if d in reverse:
                reverse[d].append(n)

    visited = set()
    stack = list(reverse[node])
    while stack:
        dep = stack.pop()
        if dep in visited:
            continue
        visited.add(dep)
        stack.extend(reverse[dep])

    return sorted(visited)


def find_cycle(graph):
    """Return one cycle as a list of nodes, or [] if none."""
    _validate(graph)
    norm = _normalize_deps(graph)

    # DFS: visit nodes in sorted order; from each unvisited node follow
    # its dependencies in sorted order. Ignore deps that are not keys.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in norm}
    parent = {}
    path = []
    cycle = []

    for start in sorted(norm.keys()):
        if color[start] != WHITE:
            continue
        stack = [(start, iter(sorted(norm.get(start, []))))]
        color[start] = GRAY
        path = [start]
        while stack:
            node, it = stack[-1]
            try:
                dep = next(it)
            except StopIteration:
                color[node] = BLACK
                path.pop()
                stack.pop()
                continue
            if dep not in norm:
                continue
            if color[dep] == GRAY:
                # Found a cycle: extract it
                idx = path.index(dep)
                cycle = path[idx:]
                cycle = sorted(cycle)
                return cycle
            if color[dep] == WHITE:
                color[dep] = GRAY
                path.append(dep)
                stack.append((dep, iter(sorted(norm.get(dep, [])))))
        color[start] = BLACK
    return []


def resolve_subset(graph, targets):
    """Return dependency order for *targets* and their transitive deps."""
    _validate(graph)
    targets = list(targets)
    if not targets:
        return []

    norm = _normalize_deps(graph)
    # Determine reachable nodes from targets
    reachable = set()
    for t in targets:
        if t not in norm:
            raise KeyError(t)
        reachable.add(t)
    # BFS/DFS to find all reachable nodes
    stack = list(targets)
    while stack:
        n = stack.pop()
        if n in reachable:
            continue
        reachable.add(n)
        for d in norm.get(n, []):
            if d in norm and d not in reachable:
                stack.append(d)

    # Restrict graph to reachable nodes
    sub = {n: norm[n] for n in reachable}

    # Check for missing deps within the subgraph
    missing = {}
    for node in sorted(sub.keys()):
        for dep in sub[node]:
            if dep not in sub:
                missing.setdefault(node, []).append(dep)
    if missing:
        raise MissingDependencyError(missing)

    # Check for cycles within the subgraph
    cycle = find_cycle(sub)
    if cycle:
        raise CycleError(cycle)

    # Topological sort on subgraph
    placed = set()
    order = []
    while len(placed) < len(sub):
        ready = sorted(
            n for n in sub if n not in placed and
            all(d in placed for d in sub[n])
        )
        if not ready:
            break
        for n in ready:
            order.append(n)
            placed.add(n)
    return order
