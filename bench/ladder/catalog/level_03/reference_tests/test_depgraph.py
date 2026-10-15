import random, unittest
import depgraph as D

APP = {"app": ["lib", "log"], "lib": ["log"], "log": []}

def rand_dag(rng, n):
    names = [f"n{i:02d}" for i in range(n)]; rng.shuffle(names); g = {x: [] for x in names}
    order = sorted(names)
    for i, x in enumerate(order):
        for y in order[:i]:
            if rng.random() < 0.25: g[x].append(y)
    return g

class TestValidation(unittest.TestCase):
    def test_r01_graph_must_be_a_dict(self):
        for bad in (None, [], [("a", [])], "a", 5, {("a",)}):
            for fn in (D.resolve, D.groups, D.find_cycle):
                with self.assertRaises(TypeError): fn(bad)
    def test_r01_keys_must_be_strings(self):
        with self.assertRaises(TypeError): D.resolve({1: []})
        with self.assertRaises(TypeError): D.resolve({None: []})
    def test_r01_a_bare_string_is_not_a_list_of_dependencies(self):
        for bad in ("b", b"b"):
            with self.assertRaises(TypeError): D.resolve({"a": bad, "b": []})
    def test_r01_values_must_be_iterable(self):
        for bad in (5, None, 1.5):
            with self.assertRaises(TypeError): D.resolve({"a": bad})
    def test_r01_dependency_elements_must_be_strings(self):
        with self.assertRaises(TypeError): D.resolve({"a": [1]})
        with self.assertRaises(TypeError): D.resolve({"a": ["b", None], "b": []})
    def test_r01_every_public_function_validates(self):
        for call in (lambda: D.transitive_deps([], "a"), lambda: D.dependents([], "a"), lambda: D.resolve_subset([], []), lambda: D.groups({"a": "b"}), lambda: D.find_cycle({"a": [3]})):
            with self.assertRaises(TypeError): call()
    def test_r01_tuples_sets_and_generators_are_fine(self):
        self.assertEqual(D.resolve({"a": ("b",), "b": {"c"}, "c": (x for x in [])}), ["c", "b", "a"])
    def test_r01_duplicate_dependencies_count_once(self): self.assertEqual(D.resolve({"a": ["b", "b", "b"], "b": []}), ["b", "a"]); self.assertEqual(D.groups({"a": ["b", "b"], "b": []}), [["b"], ["a"]])
    def test_r01_the_input_is_never_modified(self):
        g = {"a": ["c", "b"], "b": ["c"], "c": []}; before = {k: list(v) for k, v in g.items()}
        D.resolve(g); D.groups(g); D.find_cycle(g); D.transitive_deps(g, "a"); D.dependents(g, "c"); D.resolve_subset(g, ["a"]); self.assertEqual(g, before)
    def test_r01_input_order_does_not_matter(self):
        a = {"x": ["y", "z"], "y": ["z"], "z": []}; b = {"z": [], "y": ["z"], "x": ["z", "y"]}
        self.assertEqual(D.resolve(a), D.resolve(b)); self.assertEqual(D.groups(a), D.groups(b))

class TestResolve(unittest.TestCase):
    def test_r02_example_from_the_spec(self): self.assertEqual(D.resolve(APP), ["log", "lib", "app"])
    def test_r02_ties_are_broken_by_string_order(self): self.assertEqual(D.resolve({"b": [], "a": []}), ["a", "b"]); self.assertEqual(D.resolve({"c": [], "a": [], "b": []}), ["a", "b", "c"])
    def test_r02_empty_graph(self): self.assertEqual(D.resolve({}), [])
    def test_r02_single_node(self): self.assertEqual(D.resolve({"only": []}), ["only"])
    def test_r02_a_ready_node_beats_a_waiting_one_even_if_it_sorts_later(self): self.assertEqual(D.resolve({"a": ["z"], "z": [], "m": []}), ["m", "z", "a"])
    def test_r02_the_smallest_ready_node_goes_first_at_every_step(self): self.assertEqual(D.resolve({"d": ["a"], "a": [], "b": ["a"], "c": []}), ["a", "b", "c", "d"])
    def test_r02_disconnected_components(self): self.assertEqual(D.resolve({"a": ["b"], "b": [], "x": ["y"], "y": [], "solo": []}), ["b", "a", "solo", "y", "x"])
    def test_r02_diamond(self): self.assertEqual(D.resolve({"top": ["l", "r"], "l": ["base"], "r": ["base"], "base": []}), ["base", "l", "r", "top"])
    def test_r02_long_chain(self):
        g = {f"n{i:03d}": ([f"n{i - 1:03d}"] if i else []) for i in range(300)}; self.assertEqual(D.resolve(g), sorted(g))
    def test_r02_a_deep_chain_does_not_overflow_the_stack(self):
        g = {f"n{i:05d}": ([f"n{i - 1:05d}"] if i else []) for i in range(5000)}; self.assertEqual(len(D.resolve(g)), 5000)
    def test_r02_returns_a_new_list_of_all_nodes(self): r = D.resolve(APP); self.assertIsInstance(r, list); self.assertEqual(sorted(r), sorted(APP))
    def test_r02_property_every_node_comes_after_its_dependencies_and_the_tie_break_holds(self):
        rng = random.Random(7)
        for _ in range(60):
            g = rand_dag(rng, rng.randint(1, 14)); order = D.resolve(g); pos = {n: i for i, n in enumerate(order)}
            self.assertEqual(sorted(order), sorted(g))
            for n, deps in g.items():
                for d in deps: self.assertLess(pos[d], pos[n])
            placed = set()
            for n in order:           # at each step the chosen node is the smallest ready one
                ready = [m for m in g if m not in placed and all(d in placed for d in g[m])]
                self.assertEqual(n, min(ready)); placed.add(n)

class TestMissing(unittest.TestCase):
    def test_r03_example_from_the_spec(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve({"a": ["x", "b"], "b": ["y"]})
        self.assertEqual(cm.exception.missing, {"a": ["x"], "b": ["y"]})
    def test_r03_class_hierarchy(self):
        self.assertTrue(issubclass(D.MissingDependencyError, D.DependencyError)); self.assertTrue(issubclass(D.DependencyError, Exception)); self.assertFalse(issubclass(D.DependencyError, ValueError))
    def test_r03_all_missing_names_are_listed_sorted(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve({"a": ["z", "y", "x", "b"], "b": []})
        self.assertEqual(cm.exception.missing, {"a": ["x", "y", "z"]})
    def test_r03_the_message_mentions_every_missing_name(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve({"a": ["ghost1"], "b": ["ghost2"]})
        self.assertIn("ghost1", str(cm.exception)); self.assertIn("ghost2", str(cm.exception))
    def test_r03_nodes_without_missing_dependencies_are_not_listed(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve({"ok": [], "bad": ["nope"]})
        self.assertEqual(list(cm.exception.missing), ["bad"])
    def test_r03_missing_is_reported_before_a_cycle(self):
        with self.assertRaises(D.MissingDependencyError): D.resolve({"a": ["b", "ghost"], "b": ["a"]})
    def test_r03_groups_and_resolve_subset_raise_it_too(self):
        with self.assertRaises(D.MissingDependencyError): D.groups({"a": ["ghost"]})
        with self.assertRaises(D.MissingDependencyError): D.resolve_subset({"a": ["ghost"]}, ["a"])
    def test_r03_a_self_contained_graph_has_no_error(self): D.resolve({"a": ["b"], "b": []})
    def test_r03_missing_is_a_dict_of_lists(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve({"a": ["q"]})
        self.assertIsInstance(cm.exception.missing, dict); self.assertIsInstance(cm.exception.missing["a"], list)

class TestCycles(unittest.TestCase):
    def test_r04_find_cycle_examples_from_the_spec(self):
        self.assertEqual(D.find_cycle({"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]}), ["a", "b", "c"])
        self.assertEqual(D.find_cycle({"x": ["y"], "y": ["z"], "z": ["y"]}), ["y", "z"]); self.assertEqual(D.find_cycle({"a": ["a"]}), ["a"])
    def test_r04_no_cycle_gives_an_empty_list(self): self.assertEqual(D.find_cycle(APP), []); self.assertEqual(D.find_cycle({}), [])
    def test_r04_the_cycle_starts_with_its_smallest_node(self): self.assertEqual(D.find_cycle({"c": ["b"], "b": ["a"], "a": ["c"]}), ["a", "c", "b"])
    def test_r04_two_node_cycle(self): self.assertEqual(D.find_cycle({"b": ["a"], "a": ["b"]}), ["a", "b"])
    def test_r04_dependencies_are_followed_in_sorted_order(self): self.assertEqual(D.find_cycle({"a": ["z", "b"], "b": ["a"], "z": ["a"]}), ["a", "b"])
    def test_r04_a_start_node_outside_the_cycle_is_not_part_of_it(self): self.assertEqual(D.find_cycle({"a": ["b"], "b": ["c"], "c": ["b"]}), ["b", "c"])
    def test_r04_missing_dependencies_are_ignored_by_find_cycle(self): self.assertEqual(D.find_cycle({"a": ["ghost", "b"], "b": ["a"]}), ["a", "b"])
    def test_r04_only_the_first_cycle_in_sorted_search_order_is_returned(self): self.assertEqual(D.find_cycle({"p": ["q"], "q": ["p"], "a": ["b"], "b": ["a"]}), ["a", "b"])
    def test_r04_a_cycle_reachable_only_through_a_long_tail(self):
        g = {"s": ["t1"], "t1": ["t2"], "t2": ["t3"], "t3": ["u"], "u": ["v"], "v": ["u"]}; self.assertEqual(D.find_cycle(g), ["u", "v"])
    def test_r04_resolve_raises_cycle_error_with_the_same_cycle(self):
        g = {"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]}
        with self.assertRaises(D.CycleError) as cm: D.resolve(g)
        self.assertEqual(cm.exception.cycle, D.find_cycle(g))
    def test_r04_the_message_shows_the_cycle_with_arrows(self):
        with self.assertRaises(D.CycleError) as cm: D.resolve({"a": ["b"], "b": ["c"], "c": ["a"]})
        self.assertIn("a -> b -> c -> a", str(cm.exception))
        with self.assertRaises(D.CycleError) as cm2: D.resolve({"x": ["x"]})
        self.assertIn("x -> x", str(cm2.exception))
    def test_r04_class_hierarchy(self): self.assertTrue(issubclass(D.CycleError, D.DependencyError))
    def test_r04_self_dependency_is_a_cycle(self):
        with self.assertRaises(D.CycleError) as cm: D.resolve({"a": ["a"], "b": []})
        self.assertEqual(cm.exception.cycle, ["a"])
    def test_r04_groups_and_resolve_subset_raise_cycle_error(self):
        with self.assertRaises(D.CycleError): D.groups({"a": ["b"], "b": ["a"]})
        with self.assertRaises(D.CycleError): D.resolve_subset({"a": ["b"], "b": ["a"]}, ["a"])
    def test_r04_property_the_returned_cycle_is_a_real_cycle_in_canonical_form(self):
        rng = random.Random(11)
        for _ in range(80):
            names = [f"n{i}" for i in range(rng.randint(2, 9))]; g = {n: [m for m in names if rng.random() < 0.22] for n in names}; cyc = D.find_cycle(g)
            if not cyc:
                try: D.resolve(g)
                except D.CycleError: self.fail("find_cycle said acyclic but resolve found a cycle")
                continue
            self.assertEqual(cyc[0], min(cyc)); self.assertEqual(len(set(cyc)), len(cyc))
            for i, n in enumerate(cyc): self.assertIn(cyc[(i + 1) % len(cyc)], g[n])
    def test_r04_acyclic_graphs_never_raise(self):
        rng = random.Random(3)
        for _ in range(30): D.resolve(rand_dag(rng, 12))

class TestGroups(unittest.TestCase):
    def test_r05_example_from_the_spec(self): self.assertEqual(D.groups({"app": ["lib", "log"], "lib": ["log"], "log": [], "docs": []}), [["docs", "log"], ["lib"], ["app"]])
    def test_r05_empty_graph(self): self.assertEqual(D.groups({}), [])
    def test_r05_a_node_sits_one_level_above_its_deepest_dependency(self): self.assertEqual(D.groups({"a": [], "b": ["a"], "c": ["a", "b"]}), [["a"], ["b"], ["c"]])
    def test_r05_every_group_is_sorted(self): self.assertEqual(D.groups({"z": [], "y": [], "x": [], "w": ["z"]}), [["x", "y", "z"], ["w"]])
    def test_r05_independent_chains_progress_in_parallel(self): self.assertEqual(D.groups({"a1": [], "a2": ["a1"], "b1": [], "b2": ["b1"]}), [["a1", "b1"], ["a2", "b2"]])
    def test_r05_a_single_level(self): self.assertEqual(D.groups({"a": [], "b": []}), [["a", "b"]])
    def test_r05_result_is_a_list_of_lists(self): r = D.groups(APP); self.assertIsInstance(r, list); self.assertTrue(all(isinstance(x, list) for x in r))
    def test_r05_property_levels_are_consistent_with_resolve(self):
        rng = random.Random(5)
        for _ in range(40):
            g = rand_dag(rng, rng.randint(1, 12)); gr = D.groups(g); lvl = {n: i for i, grp in enumerate(gr) for n in grp}
            self.assertEqual(sorted(lvl), sorted(g)); self.assertTrue(all(grp for grp in gr))
            for n, deps in g.items(): self.assertEqual(lvl[n], 1 + max((lvl[d] for d in deps), default=-1))
    def test_r05_errors_match_resolve(self):
        with self.assertRaises(D.CycleError): D.groups({"a": ["a"]})
        with self.assertRaises(D.MissingDependencyError): D.groups({"a": ["b"]})

class TestTransitive(unittest.TestCase):
    G = {"app": ["lib", "log"], "lib": ["log", "util"], "log": [], "util": [], "tool": ["util"]}
    def test_r06_all_direct_and_indirect_dependencies(self): self.assertEqual(D.transitive_deps(self.G, "app"), {"lib", "log", "util"})
    def test_r06_a_leaf_has_none(self): self.assertEqual(D.transitive_deps(self.G, "log"), set())
    def test_r06_returns_a_set(self): self.assertIsInstance(D.transitive_deps(self.G, "app"), set)
    def test_r06_never_includes_the_node_itself(self): self.assertNotIn("app", D.transitive_deps(self.G, "app")); self.assertEqual(D.transitive_deps({"a": ["a"]}, "a"), set())
    def test_r06_a_name_only_mentioned_as_a_dependency_is_known(self): self.assertEqual(D.transitive_deps({"a": ["ghost"]}, "ghost"), set()); self.assertEqual(D.transitive_deps({"a": ["ghost"]}, "a"), {"ghost"})
    def test_r06_an_unknown_name_raises_key_error(self):
        with self.assertRaises(KeyError): D.transitive_deps(self.G, "nope")
    def test_r06_terminates_on_cycles(self): self.assertEqual(D.transitive_deps({"a": ["b"], "b": ["c"], "c": ["a"]}, "a"), {"b", "c"})
    def test_r06_does_not_raise_dependency_errors(self): self.assertEqual(D.transitive_deps({"a": ["b", "ghost"], "b": ["a"]}, "a"), {"b", "ghost"})
    def test_r06_diamonds_are_counted_once(self): self.assertEqual(D.transitive_deps({"t": ["l", "r"], "l": ["b"], "r": ["b"], "b": []}, "t"), {"l", "r", "b"})
    def test_r06_cycle_members_see_each_other_but_not_themselves(self): self.assertEqual(D.transitive_deps({"a": ["b"], "b": ["a"]}, "b"), {"a"})

class TestDependents(unittest.TestCase):
    G = {"app": ["lib", "log"], "lib": ["log", "util"], "log": [], "util": [], "tool": ["util"]}
    def test_r07_everything_that_depends_on_a_node(self): self.assertEqual(D.dependents(self.G, "util"), ["app", "lib", "tool"]); self.assertEqual(D.dependents(self.G, "log"), ["app", "lib"])
    def test_r07_a_root_has_no_dependents(self): self.assertEqual(D.dependents(self.G, "app"), [])
    def test_r07_result_is_a_sorted_list(self): r = D.dependents(self.G, "util"); self.assertIsInstance(r, list); self.assertEqual(r, sorted(r))
    def test_r07_never_includes_the_node_itself(self): self.assertEqual(D.dependents({"a": ["a"]}, "a"), [])
    def test_r07_a_name_only_mentioned_as_a_dependency_is_known(self): self.assertEqual(D.dependents({"a": ["ghost"], "b": ["a"]}, "ghost"), ["a", "b"])
    def test_r07_an_unknown_name_raises_key_error(self):
        with self.assertRaises(KeyError): D.dependents(self.G, "nope")
    def test_r07_terminates_on_cycles(self): self.assertEqual(D.dependents({"a": ["b"], "b": ["c"], "c": ["a"]}, "a"), ["b", "c"])
    def test_r07_does_not_raise_dependency_errors(self): self.assertEqual(D.dependents({"a": ["ghost"], "b": ["a"]}, "a"), ["b"])
    def test_r07_is_the_reverse_of_transitive_deps(self):
        rng = random.Random(9)
        for _ in range(30):
            g = rand_dag(rng, 10)
            for n in g: self.assertEqual(D.dependents(g, n), sorted(m for m in g if n in D.transitive_deps(g, m)))

class TestSubset(unittest.TestCase):
    G = {"app": ["lib", "log"], "lib": ["log"], "log": [], "docs": ["app"], "tool": ["util"], "util": []}
    def test_r08_target_and_everything_it_needs(self): self.assertEqual(D.resolve_subset(self.G, ["lib"]), ["log", "lib"]); self.assertEqual(D.resolve_subset(self.G, ["app"]), ["log", "lib", "app"])
    def test_r08_several_targets(self): self.assertEqual(D.resolve_subset(self.G, ["tool", "lib"]), ["log", "lib", "util", "tool"])
    def test_r08_unrelated_nodes_are_left_out(self): self.assertNotIn("tool", D.resolve_subset(self.G, ["app"])); self.assertNotIn("docs", D.resolve_subset(self.G, ["app"]))
    def test_r08_empty_targets(self): self.assertEqual(D.resolve_subset(self.G, []), [])
    def test_r08_an_unknown_target_raises_key_error(self):
        with self.assertRaises(KeyError): D.resolve_subset(self.G, ["nope"])
        with self.assertRaises(KeyError): D.resolve_subset({"a": ["ghost"]}, ["ghost"])
    def test_r08_a_broken_part_outside_the_subset_is_ignored(self):
        g = {"ok": ["base"], "base": [], "broken": ["ghost"], "loop1": ["loop2"], "loop2": ["loop1"]}; self.assertEqual(D.resolve_subset(g, ["ok"]), ["base", "ok"])
    def test_r08_a_missing_dependency_inside_the_subset_is_reported(self):
        with self.assertRaises(D.MissingDependencyError) as cm: D.resolve_subset({"a": ["b"], "b": ["ghost"], "c": ["other"]}, ["a"])
        self.assertEqual(cm.exception.missing, {"b": ["ghost"]})
    def test_r08_a_cycle_inside_the_subset_is_reported(self):
        with self.assertRaises(D.CycleError) as cm: D.resolve_subset({"a": ["b"], "b": ["c"], "c": ["b"], "z": ["z"]}, ["a"])
        self.assertEqual(cm.exception.cycle, ["b", "c"])
    def test_r08_targets_may_be_any_iterable_and_duplicates_are_fine(self): self.assertEqual(D.resolve_subset(self.G, (t for t in ["lib", "lib"])), ["log", "lib"])
    def test_r08_order_follows_the_same_tie_break_as_resolve(self): self.assertEqual(D.resolve_subset({"a": ["z", "m"], "z": [], "m": []}, ["a"]), ["m", "z", "a"])
    def test_r08_subset_of_everything_equals_resolve(self):
        rng = random.Random(21)
        for _ in range(20): g = rand_dag(rng, 11); self.assertEqual(D.resolve_subset(g, list(g)), D.resolve(g))
    def test_r08_the_graph_is_not_modified(self): g = {"a": ["b"], "b": []}; D.resolve_subset(g, ["a"]); self.assertEqual(g, {"a": ["b"], "b": []})
