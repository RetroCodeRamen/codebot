Specification (SPEC.md):

# Dependency resolution engine

Build `depgraph.py` in the root of the code directory: a dependency resolver for build systems. Python 3, standard library only, no network.
A **graph** is a `dict` that maps each node name (a `str`) to the dependencies of that node (an iterable of `str`): the node needs every one of its dependencies to come BEFORE it.
All results must be deterministic, so every tie below is broken by plain string order (`sorted`). The functions never modify the graph they are given.

## Requirements

1. Input validation. Every public function that takes a graph raises `TypeError` if the graph is not a `dict`, if a key is not a `str`, if a value is a `str` or `bytes` (a bare string is not a list of dependencies), is not iterable, or contains an element that is not a `str`. A dependency listed twice counts once. A node may depend on itself; that is a cycle (see 4). Dependencies, nodes and results never depend on the order of the input.

2. `resolve(graph)` returns a list of all nodes in dependency order: every node comes after all its dependencies. Among the nodes that are ready at a given moment (all their dependencies already placed) the one that sorts first is placed first, so `resolve({"app": ["lib", "log"], "lib": ["log"], "log": []})` is `["log", "lib", "app"]` and `resolve({"b": [], "a": []})` is `["a", "b"]`. Graphs with several disconnected parts, isolated nodes and the empty graph (`[]`) must work.

3. Missing dependencies: a dependency that is not itself a key of the graph is an error. `resolve` raises `MissingDependencyError` (a subclass of `DependencyError`, which is a subclass of `Exception`). Its attribute `missing` is a dict that maps every node with missing dependencies to the sorted list of its missing dependency names, listing ALL of them, not only the first (`resolve({"a": ["x", "b"], "b": ["y"]})` has `missing == {"a": ["x"], "b": ["y"]}`). `str(error)` mentions every missing name. Missing dependencies are reported before any cycle.

4. Cycles: `find_cycle(graph)` returns one cycle as a list of nodes such that each node depends on the next one and the last depends on the first, or `[]` if there is none. It is defined exactly like this so that the answer is unique: visit the nodes in sorted order; from each unvisited node follow its dependencies in sorted order (depth first, ignoring dependencies that are not keys of the graph); the first time a dependency is a node that is on the current path, the cycle is the part of the path from that node to the current node; return it rotated so that it starts with its smallest node. `find_cycle({"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]})` is `["a", "b", "c"]`; `find_cycle({"x": ["y"], "y": ["z"], "z": ["y"]})` is `["y", "z"]`; `find_cycle({"a": ["a"]})` is `["a"]`. `resolve` raises `CycleError` (a subclass of `DependencyError`) when the graph has a cycle and no node is missing; its attribute `cycle` equals `find_cycle(graph)` and `str(error)` shows the cycle with arrows, e.g. `a -> b -> c -> a`.

5. `groups(graph)` returns the nodes grouped into parallel build levels: level 0 holds the nodes without dependencies, level 1 the nodes whose dependencies are all in level 0, and so on; every group is a sorted list, and the result is a list of groups (`[]` for the empty graph). `groups({"app": ["lib", "log"], "lib": ["log"], "log": [], "docs": []})` is `[["docs", "log"], ["lib"], ["app"]]`. It raises the same errors as `resolve`.

6. `transitive_deps(graph, node)` returns the set of all direct and indirect dependencies of `node`, never including `node` itself. A name that is only mentioned as a dependency is known and has no dependencies; a name that appears nowhere raises `KeyError`. It must terminate on graphs that contain cycles and must not raise `MissingDependencyError` or `CycleError`.

7. `dependents(graph, node)` returns the sorted list of all nodes that depend on `node` directly or indirectly, never including `node` itself. The same "known name" rule and the same behaviour on cycles and missing dependencies as `transitive_deps` apply.

8. `resolve_subset(graph, targets)` returns the dependency order for the given target nodes and everything they need, and for nothing else: the same ordering rules as `resolve`, applied to the part of the graph reachable from `targets` (an iterable of node names). A target that is not a key of the graph raises `KeyError`. Missing dependencies and cycles are only reported when they are inside that reachable part; the rest of the graph may be broken. Empty `targets` gives `[]`.

The code directory contains README.md and these files:

### depgraph.py
```python
"""Dependency resolution engine for build systems.

Public API:
    resolve(graph)
    find_cycle(graph)
    groups(graph)
    transitive_deps(graph, node)
    dependents(graph, node)
    resolve_subset(graph, targets)
"""


class DependencyError(Exception):
    """Base class for dependency-related errors."""
    pass


class MissingDependencyError(DependencyError):
    """Raised when a dependency is not a key in the graph."""
    def __init__(self, missing):
        # missing: dict mapping node -> sorted list of missing dep names
        super().__init__(self._format(missing))
        self.missing = missing

    @staticmethod
    def _format(missing):
        parts = []
        for node in sorted(missing):
            for dep in missing[node]:
                parts.append(f"{node} -> {dep}")
        return ", ".join(parts)


class CycleError(DependencyError):
    """Raised when a cycle is detected in the graph."""
    def __init__(self, cycle):
        # cycle: list of nodes forming the cycle
        super().__init__(self._format(cycle))
        self.cycle = cycle

    @staticmethod
    def _format(cycle):
        return " -> ".join(cycle + [cycle[0]])


def _validate(graph):
    """Validate that graph is a dict of str -> iterable of str.
    Raises TypeError on any violation.
    """
    if not isinstance(graph, dict):
        raise TypeError("graph must be a dict")
    for node, deps in graph.items():
        if not isinstance(node, str):
            raise TypeError("node keys must be str")
        if not isinstance(deps, (list, tuple, set, frozenset)):
            # Must be iterable but not a bare string
            if isinstance(deps, str):
                raise TypeError("dependency values must be iterable, not str")
            if not hasattr(deps, '__iter__'):
                raise TypeError("dependency values must be iterable")
            # Check elements
            for dep in deps:
                if not isinstance(dep, str):
                    raise TypeError("dependency elements must be str")


def _normalize_deps(graph):
    """Return a new graph with sorted dependency lists and deduplicated entries.
    Does not modify the original graph.
    """
    result = {}
    for node in sorted(graph):
        deps = set(graph[node])
        deps.discard(node)  # self-dependency is a cycle, remove for resolution
        result[node] = sorted(deps)
    return result


def resolve(graph):
    """Return nodes in dependency order.
    Raises MissingDependencyError if any dep is missing.
    Raises CycleError if a cycle exists and no deps are missing.
    """
    _validate(graph)
    if not graph:
        return []

    # Check for missing dependencies first
    missing = {}
    for node in sorted(graph):
        for dep in sorted(graph[node]):
            if dep not in graph:
                if node not in missing:
                    missing[node] = []
                if dep not in missing[node]:
                    missing[node].append(dep)
    if missing:
        missing = {n: sorted(deps) for n, deps in missing.items()}
        raise MissingDependencyError(missing)

    # Check for cycles
    cycle = find_cycle(graph)
    if cycle:
        raise CycleError(cycle)

    # Topological sort with deterministic tie-breaking
    sorted_graph = _normalize_deps(graph)
    placed = set()
    order = []
    for node in sorted(sorted_graph):
        if node in placed:
            continue
        # BFS/DFS to find all reachable nodes from this node
        stack = [node]
        visited_in_this_pass = set()
        while stack:
            current = stack.pop()
            if current in visited_in_this_pass:
                continue
            visited_in_this_pass.add(current)
            for dep in sorted(sorted_graph[current]):
                if dep not in placed:
                    stack.append(dep)
        # Now we need to place nodes in correct order
        # Use Kahn's algorithm for proper topological sort
        in_degree = {node: 0 for node in sorted_graph}
        for node in sorted_graph:
            for dep in sorted_graph[node]:
                if dep in in_degree:
                    in_degree[dep] = in_degree.get(dep, 0) + 1
        # Actually, let's use a proper topological sort
        in_degree = {node: 0 for node in sorted_graph}
        for node in sorted_graph:
            for dep in sorted_graph[node]:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Kahn's algorithm
        queue = sorted([n for n in in_degree if in_degree[n] == 0])
        while queue:
            node = queue.pop(0)
            order.append(node)
            placed.add(node)
            # Find nodes that depend on this node
            for n in sorted(sorted_graph):
                if n in placed:
                    continue
                if node in sorted_graph.get(n, []):
                    in_degree[n] -= 1
                    if in_degree[n] == 0:
                        queue.append(n)
            queue.sort()

    return order


def find_cycle(graph):
    """Return one cycle as a list of nodes, or [] if no cycle.
    Uses DFS visiting nodes in sorted order, following deps in sorted order.
    """
    _validate(graph)
    if not graph:
        return []

    # DFS to find a cycle
    visited = set()
    path = []
    path_set = set()

    def dfs(node):
        if node in path_set:
            # Found a cycle
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            # Rotate to start with smallest
            min_idx = cycle.index(min(cycle))
            return cycle[min_idx:] + cycle[:min_idx]
        if node in visited:
            return None

        visited.add(node)
        path.append(node)
        path_set.add(node)

        for dep in sorted(graph.get(node, [])):
            if dep in graph:  # Only follow deps that are keys
                result = dfs(dep)
                if result is not None:
                    return result

        path.pop()
        path_set.remove(node)
        return None

    for node in sorted(graph):
        visited = set()
        path = []
        path_set = set()
        result = dfs(node)
        if result is not None:
            return result

    return []


def groups(graph):
    """Return nodes grouped into parallel build levels.
    Level 0: nodes with no dependencies.
    Level 1: nodes whose deps are all in level 0.
    """
    _validate(graph)
    if not graph:
        return []

    # Check for missing dependencies
    missing = {}
    for node in sorted(graph):
        for dep in sorted(graph[node]):
            if dep not in graph:
                if node not in missing:
                    missing[node] = []
                if dep not in missing[node]:
                    missing[node].append(dep)
    if missing:
        missing = {n: sorted(deps) for n, deps in missing.items()}
        raise MissingDependencyError(missing)

    # Check for cycles
    cycle = find_cycle(graph)
    if cycle:
        raise CycleError(cycle)

    # BFS level by level
    remaining = set(graph.keys())
    levels = []
    while remaining:
        current_level = sorted([n for n in remaining if not any(d in remaining for d in graph[n])])
        if not current_level:
            # This shouldn't happen if no cycles and no missing deps
            break
        levels.append(current_level)
        remaining -= set(current_level)

    return levels


def transitive_deps(graph, node):
    """Return the set of all direct and indirect dependencies of node.
    Never includes node itself.
    """
    _validate(graph)
    if node not in graph:
        raise KeyError(node)

    visited = set()
    stack = sorted(graph[node])
    while stack:
        dep = stack.pop()
        if dep in visited:
            continue
        if dep not in graph:
            raise KeyError(dep)
        visited.add(dep)
        for d in sorted(graph[dep]):
            if d not in visited:
                stack.append(d)

    return visited


def dependents(graph, node):
    """Return sorted list of all nodes that depend on node directly or indirectly.
    Never includes node itself.
    """
    _validate(graph)
    if node not in graph:
        raise KeyError(node)

    # Build reverse graph
    reverse = {}
    for n in graph:
        for d in graph[n]:
            if d not in reverse:
                reverse[d] = []
            reverse[d].append(n)

    visited = set()
    stack = sorted(reverse.get(node, []))
    while stack:
        dep = stack.pop()
        if dep in visited:
            continue
        if dep == node:
            continue
        visited.add(dep)
        for d in sorted(reverse.get(dep, [])):
            if d not in visited:
                stack.append(d)

    return sorted(visited)


def resolve_subset(graph, targets):
    """Return dependency order for target nodes and everything they need.
    Only considers the reachable part of the graph.
    """
    _validate(graph)
    if not targets:
        return []

    targets = sorted(set(targets))
    for t in targets:
        if t not in graph:
            raise KeyError(t)

    # Find reachable nodes
    reachable = set()
    stack = list(targets)
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        for dep in graph.get(node, []):
            if dep in graph and dep not in reachable:
                stack.append(dep)

    # Build subgraph
    subgraph = {n: graph[n] for n in reachable}

    # Check for missing dependencies within reachable set
    missing = {}
    for node in sorted(subgraph):
        for dep in sorted(subgraph[node]):
            if dep not in subgraph:
                if node not in missing:
                    missing[node] = []
                if dep not in missing[node]:
                    missing[node].append(dep)
    if missing:
        missing = {n: sorted(deps) for n, deps in missing.items()}
        raise MissingDependencyError(missing)

    # Check for cycles within reachable set
    cycle = find_cycle(subgraph)
    if cycle:
        raise CycleError(cycle)

    # Topological sort
    in_degree = {node: 0 for node in subgraph}
    for node in subgraph:
        for dep in subgraph[node]:
            if dep in in_degree:
                in_degree[dep] += 1

    queue = sorted([n for n in in_degree if in_degree[n] == 0])
    order = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for n in sorted(subgraph):
            if n in set(order):
                continue
            if node in subgraph.get(n, []):
                in_degree[n] -= 1
                if in_degree[n] == 0:
                    queue.append(n)
        queue.sort()

    return order
```

All files compile, but 4 of 6 examples from the specification fail:
FAIL: resolve({'app': ['lib', 'log'], 'lib': ['log'], ...) returned ['app', 'app', 'app'], expected ['log', 'lib', 'app']
FAIL: find_cycle({'a': ['b'], 'b': ['c'], 'c': ['a'], 'd'...) returned ['a', 'b', 'c', 'a'], expected ['a', 'b', 'c']
FAIL: find_cycle({'x': ['y'], 'y': ['z'], 'z': ['y']}) returned ['y', 'z', 'y'], expected ['y', 'z']
FAIL: find_cycle({'a': ['a']}) returned ['a', 'a'], expected ['a']
failing assertions: 1, 3, 4, 5
4 of 6 assertions failed

Fix the code so that it meets the whole specification. Reply with the complete corrected contents of every file.