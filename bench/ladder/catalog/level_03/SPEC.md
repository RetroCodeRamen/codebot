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
