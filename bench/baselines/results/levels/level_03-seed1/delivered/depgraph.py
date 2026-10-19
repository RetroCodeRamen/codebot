"""
Dependency resolution engine for build systems.

Public API:
    resolve(graph) -> list[str]
    groups(graph) -> list[list[str]]
    transitive_deps(graph, node) -> set[str]
    dependents(graph, node) -> list[str]
    find_cycle(graph) -> list[str]

Errors:
    DependencyError (base)
    MissingDependencyError (subclass)
    CycleError (subclass)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class DependencyError(Exception):
    """Base class for dependency-related errors."""


class MissingDependencyError(DependencyError):
    """Raised when one or more dependencies are not present in the graph."""

    def __init__(self, missing: dict[str, list[str]]):
        self.missing = missing
        names = sorted(
            name
            for node, deps in missing.items()
            for name in deps
        )
        super().__init__(
            "Missing dependencies: "
            + ", ".join(f"{n!r} (needed by {owner!r})" for owner, deps in missing.items() for n in deps)
        )


class CycleError(DependencyError):
    """Raised when the graph contains a cycle."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(
            "Cycle detected: "
            + " -> ".join(cycle + [cycle[0]])
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_graph(graph: object) -> None:
    """Raise TypeError if *graph* is not a valid dependency graph."""
    if not isinstance(graph, dict):
        raise TypeError(
            f"graph must be a dict, got {type(graph).__name__}"
        )
    for node, deps in graph.items():
        if not isinstance(node, str):
            raise TypeError(
                f"graph keys must be str, got {type(node).__name__}"
            )
        if not isinstance(deps, (list, tuple, set, frozenset)):
            raise TypeError(
                f"graph values must be iterable of str, got {type(deps).__name__}"
            )
        for dep in deps:
            if not isinstance(dep, str):
                raise TypeError(
                    f"graph values must be iterable of str, got {type(dep).__name__}"
                )


def _normalize_deps(deps: object) -> set[str]:
    """Return a *set* of unique dependency names from an iterable."""
    return {d for d in deps}


def _validate_node(graph: dict, node: object) -> None:
    """Raise TypeError if *node* is not a str key in *graph*."""
    if not isinstance(node, str):
        raise TypeError(
            f"node must be str, got {type(node).__name__}"
        )
    if node not in graph:
        raise KeyError(node)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def resolve(graph: dict) -> list[str]:
    """Return all nodes in dependency order (topological sort)."""
    _validate_graph(graph)
    if not graph:
        return []

    sorted_keys = sorted(graph.keys())
    clean_graph: dict[str, set[str]] = {
        k: _normalize_deps(graph[k]) for k in sorted_keys
    }

    # Check missing dependencies.
    missing: dict[str, list[str]] = {}
    for node in sorted_keys:
        for dep in clean_graph[node]:
            if dep not in clean_graph:
                missing.setdefault(node, []).append(dep)
    if missing:
        for node in sorted(missing):
            missing[node] = sorted(missing[node])
        raise MissingDependencyError(missing)

    # Topological sort (Kahn's) with sorted tie-breaking.
    # in_degree[node] = number of dependencies of node that are in the graph.
    in_degree: dict[str, int] = {n: 0 for n in clean_graph}
    for node in clean_graph:
        for dep in clean_graph[node]:
            if dep in clean_graph:
                in_degree[node] += 1

    result: list[str] = []
    available: list[str] = sorted([n for n in clean_graph if in_degree[n] == 0])

    while available:
        node = available.pop(0)
        result.append(node)
        for other in sorted(clean_graph.keys()):
            if node in clean_graph[other]:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    # Insert in sorted position
                    import bisect
                    bisect.insort(available, other)

    return result


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def groups(graph: dict) -> list[list[str]]:
    """Return nodes grouped into parallel build levels."""
    _validate_graph(graph)
    if not graph:
        return []

    sorted_keys = sorted(graph.keys())
    clean_graph: dict[str, set[str]] = {
        k: _normalize_deps(graph[k]) for k in sorted_keys
    }

    # Check missing dependencies.
    missing: dict[str, list[str]] = {}
    for node in sorted_keys:
        for dep in clean_graph[node]:
            if dep not in clean_graph:
                missing.setdefault(node, []).append(dep)
    if missing:
        for node in sorted(missing):
            missing[node] = sorted(missing[node])
        raise MissingDependencyError(missing)

    # BFS level-by-level.
    placed: set[str] = set()
    result: list[list[str]] = []
    level: int = 0

    while placed != set(clean_graph.keys()):
        ready: list[str] = []
        for node in sorted(clean_graph.keys()):
            if node in placed:
                continue
            if all(dep in placed for dep in clean_graph[node]):
                ready.append(node)
        if not ready:
            # Should not happen after missing check, but safety.
            break
        ready.sort()
        result.append(ready)
        placed.update(ready)
        level += 1

    return result


# ---------------------------------------------------------------------------
# transitive_deps
# ---------------------------------------------------------------------------

def transitive_deps(graph: dict, node: object) -> set[str]:
    """Return the set of all direct and indirect dependencies of *node*."""
    _validate_node(graph, node)
    clean_graph: dict[str, set[str]] = {
        k: _normalize_deps(graph[k]) for k in sorted(graph.keys())
    }

    visited: set[str] = set()
    stack: list[str] = [node]

    while stack:
        current = stack.pop()
        for dep in clean_graph.get(current, set()):
            if dep not in visited:
                visited.add(dep)
                stack.append(dep)

    return visited


# ---------------------------------------------------------------------------
# dependents
# ---------------------------------------------------------------------------

def dependents(graph: dict, node: object) -> list[str]:
    """Return the sorted list of all nodes that depend on *node*."""
    _validate_node(graph, node)
    clean_graph: dict[str, set[str]] = {
        k: _normalize_deps(graph[k]) for k in sorted(graph.keys())
    }

    # Build reverse adjacency: for each node, who depends on it?
    reverse: dict[str, set[str]] = {k: set() for k in clean_graph}
    for other, deps in clean_graph.items():
        for dep in deps:
            if dep in reverse:
                reverse[dep].add(other)

    visited: set[str] = set()
    stack: list[str] = [node]

    while stack:
        current = stack.pop()
        for dependent in reverse.get(current, set()):
            if dependent not in visited:
                visited.add(dependent)
                stack.append(dependent)

    return sorted(visited)


# ---------------------------------------------------------------------------
# find_cycle
# ---------------------------------------------------------------------------

def find_cycle(graph: dict) -> list[str]:
    """Return one cycle as a list of nodes, or [] if there is none."""
    _validate_graph(graph)
    if not graph:
        return []

    sorted_keys = sorted(graph.keys())
    clean_graph: dict[str, set[str]] = {
        k: _normalize_deps(graph[k]) for k in sorted_keys
    }

    # DFS following sorted dependencies.
    # State: 0 = unvisited, 1 = in current path, 2 = done.
    state: dict[str, int] = {k: 0 for k in clean_graph}
    parent: dict[str, str | None] = {k: None for k in clean_graph}

    for start in sorted_keys:
        if state[start] != 0:
            continue
        path: list[str] = [start]
        path_set: set[str] = {start}
        stack: list[str] = [start]

        while stack:
            current = stack[-1]
            deps = sorted(clean_graph.get(current, set()))
            found_next = False
            for dep in deps:
                if dep not in clean_graph:
                    continue
                if dep not in path_set:
                    if state[dep] == 0:
                        path.append(dep)
                        path_set.add(dep)
                        stack.append(dep)
                        found_next = True
                        break
                elif dep in path_set:
                    # Cycle found
                    cycle_start = dep
                    cycle = path[path.index(dep):]
                    # Rotate so smallest is first
                    min_idx = cycle.index(min(cycle))
                    cycle = cycle[min_idx:] + cycle[:min_idx]
                    return cycle
            if not found_next:
                # Backtrack
                stack.pop()
                path.pop()
                path_set.discard(current)
                state[current] = 2

    return []
