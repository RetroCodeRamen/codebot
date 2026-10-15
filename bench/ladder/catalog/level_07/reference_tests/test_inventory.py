import os, sqlite3, tempfile, threading, unittest
import inventory as I
from inventory import Inventory, Product, Movement, Reservation, InventoryError, DuplicateSku, UnknownSku, UnknownOrder, InsufficientStock, SchemaError

V1 = ["CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents >= 0), stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0))",
      "CREATE TABLE movements(id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES products(sku), delta INTEGER NOT NULL, reason TEXT NOT NULL, ref TEXT, ts TEXT NOT NULL)"]
V2 = ["CREATE TABLE reservations(id INTEGER PRIMARY KEY AUTOINCREMENT, order_ref TEXT NOT NULL, sku TEXT NOT NULL REFERENCES products(sku), qty INTEGER NOT NULL CHECK(qty > 0), "
      "state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active', 'released', 'fulfilled')), ts TEXT NOT NULL)"]

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="inv-test-"); self.path = os.path.join(self.dir, "inv.db"); self.tick = 0
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def clock(self): self.tick += 1; return f"T{self.tick:03d}"
    def inv(self, path=None):
        i = Inventory(path or self.path, clock=self.clock); self.addCleanup(i.close); return i
    def mem(self): i = Inventory(clock=self.clock); self.addCleanup(i.close); return i
    def raw(self):
        c = sqlite3.connect(self.path); self.addCleanup(c.close); return c
    def stocked(self):
        i = self.mem(); i.add_product("A", "Apple", 100, 3); i.add_product("B", "Banana", 50); i.receive("A", 10); i.receive("B", 4); return i

class TestSchema(Base):
    def test_r01_new_database_is_at_the_latest_version(self):
        i = self.inv(); self.assertEqual(Inventory.SCHEMA_VERSION, 3); self.assertEqual(i.schema_version(), 3); self.assertEqual(self.raw().execute("PRAGMA user_version").fetchone()[0], 3)
    def test_r01_tables_and_columns(self):
        self.inv(); c = self.raw(); tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}; self.assertTrue({"products", "movements", "reservations"} <= tables)
        self.assertEqual([r[1] for r in c.execute("PRAGMA table_info(products)")], ["sku", "name", "price_cents", "stock", "reorder_level"])
        self.assertTrue(any(r[0] for r in c.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND tbl_name = 'movements' AND sql LIKE '%sku%'")))
    def test_r01_memory_database_works(self): self.assertEqual(self.mem().schema_version(), 3)
    def test_r01_foreign_keys_are_enabled(self):
        i = self.mem()
        with self.assertRaises(sqlite3.IntegrityError): i.db.execute("INSERT INTO movements(sku, delta, reason, ts) VALUES ('nope', 1, 'x', 't')")
    def build_v1(self):
        c = sqlite3.connect(self.path)
        for s in V1: c.execute(s)
        c.execute("INSERT INTO products VALUES ('A', 'Apple', 100, 7)"); c.execute("INSERT INTO products VALUES ('B', 'Banana', 50, 0)")
        c.execute("INSERT INTO movements(sku, delta, reason, ref, ts) VALUES ('A', 7, 'receive', NULL, 'old')"); c.execute("PRAGMA user_version = 1"); c.commit(); c.close()
    def test_r01_a_version_1_database_is_upgraded_in_place_keeping_its_data(self):
        self.build_v1(); i = self.inv(); self.assertEqual(i.schema_version(), 3)
        self.assertEqual(i.get_product("A"), Product("A", "Apple", 100, 7, 0)); self.assertEqual([m.ts for m in i.history("A")], ["old"]); self.assertEqual(i.get_product("B").stock, 0)
        i.reserve("o1", "A", 2); self.assertEqual(i.available("A"), 5)
    def test_r01_a_version_2_database_is_upgraded(self):
        self.build_v1(); c = self.raw()
        for s in V2: c.execute(s)
        c.execute("INSERT INTO reservations(order_ref, sku, qty, state, ts) VALUES ('o1', 'A', 3, 'active', 'old')"); c.execute("PRAGMA user_version = 2"); c.commit()
        i = self.inv(); self.assertEqual(i.schema_version(), 3); self.assertEqual(i.available("A"), 4); self.assertEqual(i.reservations()[0].order_ref, "o1"); self.assertEqual(i.get_product("A").reorder_level, 0)
    def test_r01_upgraded_columns_have_the_right_default_and_constraint(self):
        self.build_v1(); self.inv(); c = self.raw(); self.assertEqual(c.execute("SELECT reorder_level FROM products WHERE sku = 'A'").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError): c.execute("UPDATE products SET reorder_level = -1")
    def test_r01_a_newer_database_is_refused_and_untouched(self):
        c = self.raw(); c.execute("CREATE TABLE products(x)"); c.execute("PRAGMA user_version = 9"); c.commit(); before = open(self.path, "rb").read()
        with self.assertRaises(SchemaError): Inventory(self.path)
        self.assertEqual(open(self.path, "rb").read(), before)
    def test_r01_reopening_does_not_migrate_again_or_lose_data(self):
        i = self.inv(); i.add_product("A", "Apple", 1); i.close(); j = self.inv(); self.assertEqual(j.schema_version(), 3); self.assertEqual(j.get_product("A").name, "Apple")
    def test_r01_a_failing_step_leaves_the_previous_version(self):
        self.build_v1(); c = self.raw(); c.execute("CREATE TABLE reservations(conflict TEXT)"); c.commit()          # step 2 will fail: the table already exists
        with self.assertRaises(sqlite3.Error): Inventory(self.path)
        self.assertEqual(self.raw().execute("PRAGMA user_version").fetchone()[0], 1)
    def test_r01_context_manager_closes_the_connection(self):
        with Inventory(self.path) as i: i.add_product("A", "a", 1)
        with self.assertRaises(sqlite3.ProgrammingError): i.get_product("A")
    def test_r01_default_clock_format(self):
        i = Inventory(); i.add_product("A", "a", 1); i.receive("A", 1); import re; self.assertRegex(i.history("A")[0].ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"); i.close()
    def test_r01_error_classes(self):
        for c in (DuplicateSku, UnknownSku, UnknownOrder, InsufficientStock, SchemaError): self.assertTrue(issubclass(c, InventoryError))
        self.assertTrue(issubclass(InventoryError, Exception))

class TestProducts(Base):
    def test_r02_add_and_get(self):
        i = self.mem(); p = i.add_product("A-1", "Apple", 120, 5); self.assertEqual(p, Product("A-1", "Apple", 120, 0, 5)); self.assertEqual(i.get_product("A-1"), p)
    def test_r02_values_are_stripped(self): i = self.mem(); self.assertEqual(i.add_product("  A  ", "  Apple ", 1), Product("A", "Apple", 1, 0, 0))
    def test_r02_duplicate_sku(self):
        i = self.mem(); i.add_product("A", "a", 1)
        with self.assertRaises(DuplicateSku): i.add_product("A", "other", 2)
        self.assertEqual(i.get_product("A").name, "a")
    def test_r02_duplicate_after_stripping(self):
        i = self.mem(); i.add_product("A", "a", 1)
        with self.assertRaises(DuplicateSku): i.add_product(" A ", "b", 1)
    def test_r02_invalid_values_raise_value_error(self):
        i = self.mem()
        for args in (("", "n", 1), ("  ", "n", 1), ("s", "", 1), (5, "n", 1), ("s", 5, 1), ("s", "n", -1), ("s", "n", 1.5), ("s", "n", True), ("s", "n", "9"), ("s", "n", 1, -1), ("s", "n", 1, True), ("s", "n", 1, 2.0)):
            with self.assertRaises(ValueError): i.add_product(*args)
        self.assertEqual(i.list_products(), [])
    def test_r02_zero_price_is_allowed(self): self.assertEqual(self.mem().add_product("F", "Free", 0).price_cents, 0)
    def test_r02_unknown_sku(self):
        i = self.mem()
        with self.assertRaises(UnknownSku): i.get_product("nope")
    def test_r02_list_is_sorted_by_sku(self): i = self.mem(); [i.add_product(s, s.lower(), 1) for s in ("C", "A", "B")]; self.assertEqual([p.sku for p in i.list_products()], ["A", "B", "C"])
    def test_r02_empty_list(self): self.assertEqual(self.mem().list_products(), [])
    def test_r02_dataclass_fields(self):
        self.assertEqual(list(Product.__dataclass_fields__), ["sku", "name", "price_cents", "stock", "reorder_level"]); self.assertEqual(list(Movement.__dataclass_fields__), ["id", "sku", "delta", "reason", "ref", "ts"])
        self.assertEqual(list(Reservation.__dataclass_fields__), ["id", "order_ref", "sku", "qty", "state", "ts"])
    def test_r02_a_product_starts_with_zero_stock_and_no_movements(self): i = self.mem(); i.add_product("A", "a", 1); self.assertEqual(i.stock("A"), 0); self.assertEqual(i.history("A"), [])

class TestMovements(Base):
    def test_r03_receive_returns_the_new_stock_and_records_a_movement(self):
        i = self.mem(); i.add_product("A", "a", 1); self.assertEqual(i.receive("A", 5, ref="PO-1"), 5); self.assertEqual(i.receive("A", 2), 7)
        h = i.history("A"); self.assertEqual([(m.delta, m.reason, m.ref) for m in h], [(5, "receive", "PO-1"), (2, "receive", None)]); self.assertTrue(all(isinstance(m, Movement) and m.sku == "A" for m in h))
    def test_r03_timestamps_come_from_the_clock(self): i = self.mem(); i.add_product("A", "a", 1); self.tick = 100; i.receive("A", 1); self.assertEqual(i.history("A")[0].ts, "T101")
    def test_r03_history_order_and_ids(self): i = self.stocked(); i.adjust("A", -2, "damaged"); h = i.history("A"); self.assertEqual([m.delta for m in h], [10, -2]); self.assertLess(h[0].id, h[1].id)
    def test_r03_adjust_up_and_down(self): i = self.stocked(); self.assertEqual(i.adjust("A", -4, "lost"), 6); self.assertEqual(i.adjust("A", 1, "found"), 7); self.assertEqual([m.reason for m in i.history("A")][-2:], ["lost", "found"])
    def test_r03_stock_never_negative(self):
        i = self.stocked()
        with self.assertRaises(InsufficientStock): i.adjust("B", -5, "oops")
        self.assertEqual(i.stock("B"), 4); self.assertEqual(len(i.history("B")), 1)
    def test_r03_removing_everything_is_allowed(self): i = self.stocked(); self.assertEqual(i.adjust("B", -4, "all gone"), 0)
    def test_r03_cannot_remove_reserved_stock(self):
        i = self.stocked(); i.reserve("o1", "A", 8)
        with self.assertRaises(InsufficientStock): i.adjust("A", -3, "x")
        self.assertEqual(i.adjust("A", -2, "ok"), 8); self.assertEqual(i.available("A"), 0)
    def test_r03_adding_stock_is_allowed_while_reserved(self): i = self.stocked(); i.reserve("o1", "A", 10); self.assertEqual(i.adjust("A", 5, "found"), 15); self.assertEqual(i.available("A"), 5)
    def test_r03_invalid_arguments(self):
        i = self.stocked()
        for call in (lambda: i.receive("A", 0), lambda: i.receive("A", -1), lambda: i.receive("A", 1.5), lambda: i.receive("A", True), lambda: i.adjust("A", 0, "x"), lambda: i.adjust("A", 1, ""), lambda: i.adjust("A", 1, "  "), lambda: i.adjust("A", "1", "x"), lambda: i.adjust("A", 1.0, "x")):
            with self.assertRaises(ValueError): call()
        self.assertEqual(i.stock("A"), 10); self.assertEqual(len(i.history("A")), 1)
    def test_r03_unknown_sku_everywhere(self):
        i = self.mem()
        for call in (lambda: i.receive("x", 1), lambda: i.adjust("x", 1, "r"), lambda: i.stock("x"), lambda: i.history("x"), lambda: i.available("x"), lambda: i.reserve("o", "x", 1)):
            with self.assertRaises(UnknownSku): call()
    def test_r03_the_reason_is_stripped(self): i = self.stocked(); i.adjust("A", 1, "  recount "); self.assertEqual(i.history("A")[-1].reason, "recount")
    def test_r03_exactly_one_movement_per_stock_change(self): i = self.stocked(); n = len(i.history("A")); i.reserve("o1", "A", 2); i.release("o1"); self.assertEqual(len(i.history("A")), n); i.receive("A", 1); self.assertEqual(len(i.history("A")), n + 1)
    def test_r03_stock_is_the_sum_of_the_movements(self):
        i = self.stocked(); i.adjust("A", -3, "x"); i.receive("A", 6); self.assertEqual(i.stock("A"), sum(m.delta for m in i.history("A")))

class TestReservations(Base):
    def test_r04_reserve_creates_an_active_reservation_without_touching_stock(self):
        i = self.stocked(); r = i.reserve("o1", "A", 4); self.assertEqual((r.order_ref, r.sku, r.qty, r.state), ("o1", "A", 4, "active")); self.assertIsInstance(r, Reservation)
        self.assertEqual((i.stock("A"), i.available("A"), len(i.history("A"))), (10, 6, 1))
    def test_r04_cannot_reserve_more_than_available(self):
        i = self.stocked(); i.reserve("o1", "A", 8)
        with self.assertRaises(InsufficientStock): i.reserve("o2", "A", 3)
        self.assertEqual(i.available("A"), 2); self.assertEqual(len(i.reservations()), 1)
    def test_r04_reserving_exactly_what_is_available(self): i = self.stocked(); i.reserve("o1", "B", 4); self.assertEqual(i.available("B"), 0)
    def test_r04_invalid_arguments(self):
        i = self.stocked()
        for args in (("o", "A", 0), ("o", "A", -1), ("o", "A", 1.5), ("", "A", 1), ("  ", "A", 1), (None, "A", 1)):
            with self.assertRaises(ValueError): i.reserve(*args)
    def test_r04_release(self):
        i = self.stocked(); i.reserve("o1", "A", 4); i.reserve("o1", "B", 2); self.assertEqual(i.release("o1"), 2); self.assertEqual(i.available("A"), 10); self.assertEqual([r.state for r in i.reservations("o1")], ["released", "released"]); self.assertEqual(i.stock("A"), 10)
    def test_r04_release_unknown_or_already_closed_order(self):
        i = self.stocked()
        with self.assertRaises(UnknownOrder): i.release("nope")
        i.reserve("o1", "A", 1); i.release("o1")
        with self.assertRaises(UnknownOrder): i.release("o1")
        with self.assertRaises(UnknownOrder): i.fulfill("o1")
    def test_r04_fulfill_removes_stock_with_sale_movements(self):
        i = self.stocked(); i.reserve("o1", "A", 4); i.reserve("o1", "B", 1); moves = i.fulfill("o1")
        self.assertEqual([(m.sku, m.delta, m.reason, m.ref) for m in moves], [("A", -4, "sale", "o1"), ("B", -1, "sale", "o1")]); self.assertTrue(all(isinstance(m, Movement) for m in moves))
        self.assertEqual((i.stock("A"), i.stock("B"), i.available("A")), (6, 3, 6)); self.assertEqual({r.state for r in i.reservations("o1")}, {"fulfilled"}); self.assertEqual(i.history("A")[-1], moves[0])
    def test_r04_fulfilled_orders_cannot_be_fulfilled_again(self):
        i = self.stocked(); i.reserve("o1", "A", 1); i.fulfill("o1")
        with self.assertRaises(UnknownOrder): i.fulfill("o1")
        self.assertEqual(i.stock("A"), 9)
    def test_r04_available_ignores_closed_reservations(self): i = self.stocked(); i.reserve("o1", "A", 3); i.reserve("o2", "A", 2); i.release("o1"); i.fulfill("o2"); self.assertEqual((i.stock("A"), i.available("A")), (8, 8))
    def test_r04_two_reservations_of_the_same_order_and_sku_add_up(self): i = self.stocked(); i.reserve("o1", "A", 2); i.reserve("o1", "A", 3); self.assertEqual(i.available("A"), 5); self.assertEqual(i.release("o1"), 2)
    def test_r04_filtering_reservations(self):
        i = self.stocked(); i.reserve("o1", "A", 1); i.reserve("o2", "A", 1); i.reserve("o3", "B", 1); i.release("o2")
        self.assertEqual([r.order_ref for r in i.reservations()], ["o1", "o2", "o3"]); self.assertEqual([r.order_ref for r in i.reservations(order_ref="o2")], ["o2"]); self.assertEqual([r.order_ref for r in i.reservations(state="active")], ["o1", "o3"]); self.assertEqual(i.reservations(order_ref="o2", state="active"), [])
    def test_r04_a_released_quantity_can_be_reserved_again(self): i = self.stocked(); i.reserve("o1", "B", 4); i.release("o1"); i.reserve("o2", "B", 4); self.assertEqual(i.available("B"), 0)
    def test_r04_reservation_timestamps_use_the_clock(self): i = self.stocked(); self.tick = 50; r = i.reserve("o1", "A", 1); self.assertEqual(r.ts, "T051")

class TestTransactions(Base):
    def snapshot(self, i): return (i.list_products(), [m for p in i.list_products() for m in i.history(p.sku)], i.reservations())
    def test_r05_batch_applies_all_operations_in_order(self):
        i = self.mem(); res = i.batch([("add_product", "A", "Apple", 100), ("receive", "A", 5), ("reserve", "o1", "A", 2), ("adjust", "A", -1, "lost")])
        self.assertEqual(len(res), 4); self.assertEqual(res[0].sku, "A"); self.assertEqual(res[1], 5); self.assertEqual(res[2].qty, 2); self.assertEqual(res[3], 4); self.assertEqual((i.stock("A"), i.available("A")), (4, 2))
    def test_r05_batch_with_optional_arguments(self):
        i = self.mem(); i.batch([("add_product", "A", "Apple", 100, 7), ("receive", "A", 5, "PO-9")]); self.assertEqual(i.get_product("A").reorder_level, 7); self.assertEqual(i.history("A")[0].ref, "PO-9")
    def test_r05_a_failing_batch_changes_nothing(self):
        i = self.stocked(); before = self.snapshot(i)
        with self.assertRaises(InsufficientStock): i.batch([("receive", "A", 5), ("add_product", "C", "Cherry", 10), ("reserve", "o1", "B", 99)])
        self.assertEqual(self.snapshot(i), before)
    def test_r05_batch_failure_in_the_first_operation(self):
        i = self.stocked(); before = self.snapshot(i)
        with self.assertRaises(UnknownSku): i.batch([("receive", "ZZZ", 1), ("receive", "A", 1)])
        self.assertEqual(self.snapshot(i), before)
    def test_r05_unknown_operation_name(self):
        i = self.stocked(); before = self.snapshot(i)
        with self.assertRaises(ValueError): i.batch([("receive", "A", 1), ("explode", "A")])
        self.assertEqual(self.snapshot(i), before)
    def test_r05_later_operations_see_earlier_ones(self): i = self.mem(); i.add_product("A", "a", 1); i.batch([("receive", "A", 3), ("reserve", "o1", "A", 3), ("fulfill", "o1")]); self.assertEqual(i.stock("A"), 0)
    def test_r05_empty_batch(self): self.assertEqual(self.mem().batch([]), [])
    def test_r05_a_failed_single_operation_leaves_the_database_unchanged(self):
        i = self.stocked(); before = self.snapshot(i)
        for call in (lambda: i.adjust("B", -9, "x"), lambda: i.reserve("o", "A", 99), lambda: i.add_product("A", "dup", 1), lambda: i.fulfill("none"), lambda: i.receive("A", 0)):
            with self.assertRaises(Exception): call()
        self.assertEqual(self.snapshot(i), before)
    def test_r05_fulfill_is_atomic_across_skus(self):
        i = self.stocked(); i.reserve("o1", "A", 4); i.reserve("o1", "B", 4); i.db.execute("UPDATE products SET stock = 1 WHERE sku = 'B'"); before = self.snapshot(i)         # B no longer covers its reservation
        with self.assertRaises(InsufficientStock): i.fulfill("o1")
        self.assertEqual(self.snapshot(i), before); self.assertEqual({r.state for r in i.reservations("o1")}, {"active"})
    def test_r05_batch_rollback_also_rolls_back_ids_visible_state_on_a_file_database(self):
        i = self.inv(); i.add_product("A", "a", 1); i.receive("A", 3)
        with self.assertRaises(InsufficientStock): i.batch([("receive", "A", 5), ("adjust", "A", -99, "x")])
        j = self.inv(); self.assertEqual(j.stock("A"), 3); self.assertEqual(len(j.history("A")), 1)
    def test_r05_the_connection_is_usable_after_a_failed_operation(self):
        i = self.stocked()
        with self.assertRaises(InsufficientStock): i.adjust("B", -9, "x")
        self.assertEqual(i.receive("B", 1), 5)

class TestConsistency(Base):
    def test_r06_a_consistent_database_has_no_problems(self): i = self.stocked(); i.reserve("o1", "A", 2); i.fulfill("o1"); self.assertEqual(i.verify(), [])
    def test_r06_stock_mismatch_is_reported_and_repaired(self):
        i = self.stocked(); i.db.execute("UPDATE products SET stock = 99 WHERE sku = 'A'"); self.assertEqual(i.verify(), ["stock mismatch for A: cached 99, movements 10"]); self.assertEqual(i.repair(), 1); self.assertEqual(i.stock("A"), 10); self.assertEqual(i.verify(), []); self.assertEqual(i.repair(), 0)
    def test_r06_products_without_movements(self): i = self.mem(); i.add_product("A", "a", 1); self.assertEqual(i.verify(), []); i.db.execute("UPDATE products SET stock = 3"); self.assertEqual(i.verify(), ["stock mismatch for A: cached 3, movements 0"]); self.assertEqual(i.repair(), 1); self.assertEqual(i.stock("A"), 0)
    def test_r06_over_reservation_is_reported(self):
        i = self.stocked(); i.reserve("o1", "A", 8); i.db.execute("UPDATE products SET stock = 5 WHERE sku = 'A'"); out = i.verify(); self.assertEqual(out, ["stock mismatch for A: cached 5, movements 10", "over-reserved A: reserved 8, stock 5"])
    def test_r06_report_order_is_by_sku_with_mismatches_first(self):
        i = self.stocked(); i.reserve("o1", "B", 4); i.db.execute("UPDATE products SET stock = 1 WHERE sku IN ('A', 'B')")
        self.assertEqual(i.verify(), ["stock mismatch for A: cached 1, movements 10", "stock mismatch for B: cached 1, movements 4", "over-reserved B: reserved 4, stock 1"])
    def test_r06_repair_fixes_only_stock(self):
        i = self.stocked(); i.reserve("o1", "A", 2); i.db.execute("UPDATE products SET stock = 0"); self.assertEqual(i.repair(), 2); self.assertEqual((i.stock("A"), i.stock("B")), (10, 4)); self.assertEqual(len(i.reservations()), 1); self.assertEqual(len(i.history("A")), 1)
    def test_r06_released_and_fulfilled_reservations_are_not_over_reservations(self): i = self.stocked(); i.reserve("o1", "A", 10); i.fulfill("o1"); i.reserve("o2", "B", 4); i.release("o2"); self.assertEqual(i.verify(), [])
    def test_r06_verify_after_many_operations_stays_clean(self):
        i = self.stocked()
        for n in range(20): i.receive("A", 2); i.reserve(f"o{n}", "A", 1); (i.fulfill if n % 2 else i.release)(f"o{n}")
        self.assertEqual(i.verify(), [])

class TestReports(Base):
    def test_r07_low_stock_uses_available_quantity(self):
        i = self.mem(); i.add_product("A", "a", 1, 5); i.add_product("B", "b", 1, 5); i.add_product("C", "c", 1); i.receive("A", 6); i.receive("B", 20); i.receive("C", 0 + 1)
        self.assertEqual(i.low_stock(), []); i.reserve("o1", "A", 1); self.assertEqual(i.low_stock(), ["A"]); i.reserve("o2", "B", 15); self.assertEqual(i.low_stock(), ["A", "B"])
    def test_r07_reorder_level_zero_is_never_low(self): i = self.mem(); i.add_product("A", "a", 1); self.assertEqual(i.low_stock(), [])
    def test_r07_at_the_level_counts_as_low(self): i = self.mem(); i.add_product("A", "a", 1, 3); i.receive("A", 3); self.assertEqual(i.low_stock(), ["A"]); i.receive("A", 1); self.assertEqual(i.low_stock(), [])
    def test_r07_low_stock_is_sorted(self): i = self.mem(); [i.add_product(s, s, 1, 1) for s in ("Z", "M", "A")]; self.assertEqual(i.low_stock(), ["A", "M", "Z"])
    def test_r07_a_product_with_no_stock_and_a_level_is_low(self): i = self.mem(); i.add_product("A", "a", 1, 2); self.assertEqual(i.low_stock(), ["A"])
    def test_r07_stock_value(self): i = self.stocked(); self.assertEqual(i.stock_value(), 10 * 100 + 4 * 50); i.adjust("A", -10, "x"); self.assertEqual(i.stock_value(), 200)
    def test_r07_stock_value_of_an_empty_inventory(self): self.assertEqual(self.mem().stock_value(), 0)
    def test_r07_stock_value_ignores_reservations(self): i = self.stocked(); i.reserve("o1", "A", 5); self.assertEqual(i.stock_value(), 1200)
    def test_r07_stock_value_after_sales(self): i = self.stocked(); i.reserve("o1", "A", 5); i.fulfill("o1"); self.assertEqual(i.stock_value(), 5 * 100 + 4 * 50)

class TestPersistenceAndConcurrency(Base):
    def test_r08_data_survives_closing_and_reopening(self):
        i = self.inv(); i.add_product("A", "a", 5, 2); i.receive("A", 4); i.reserve("o1", "A", 1); i.close(); j = self.inv(); self.assertEqual(j.get_product("A"), Product("A", "a", 5, 4, 2)); self.assertEqual(j.available("A"), 3); self.assertEqual(len(j.history("A")), 1)
    def test_r08_a_second_object_sees_committed_changes_at_once(self):
        a, b = self.inv(), self.inv(); a.add_product("A", "a", 1); self.assertEqual(b.get_product("A").name, "a"); b.receive("A", 3); self.assertEqual(a.stock("A"), 3)
    def test_r08_a_second_object_does_not_see_a_rolled_back_batch(self):
        a, b = self.inv(), self.inv(); a.add_product("A", "a", 1)
        with self.assertRaises(InsufficientStock): a.batch([("receive", "A", 5), ("reserve", "o", "A", 50)])
        self.assertEqual(b.stock("A"), 0)
    def test_r08_no_oversell_with_threads_each_with_its_own_connection(self):
        i = self.inv(); i.add_product("A", "a", 1); i.receive("A", 5); results, lock = [], threading.Lock()
        def work(n):
            inv = Inventory(self.path, clock=self.clock)
            try: inv.reserve(f"o{n}", "A", 1); r = "ok"
            except InsufficientStock: r = "no"
            finally: inv.close()
            with lock: results.append(r)
        ts = [threading.Thread(target=work, args=(n,)) for n in range(12)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        self.assertEqual((results.count("ok"), results.count("no")), (5, 7)); self.assertEqual(i.available("A"), 0); self.assertEqual(i.verify(), [])
    def test_r08_no_oversell_with_one_shared_object(self):
        i = self.inv(); i.add_product("A", "a", 1); i.receive("A", 5); results, lock = [], threading.Lock()
        def work(n):
            try: i.reserve(f"o{n}", "A", 1); r = "ok"
            except InsufficientStock: r = "no"
            with lock: results.append(r)
        ts = [threading.Thread(target=work, args=(n,)) for n in range(12)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        self.assertEqual((results.count("ok"), results.count("no")), (5, 7))
    def test_r08_concurrent_receives_lose_no_updates(self):
        i = self.inv(); i.add_product("A", "a", 1)
        def work():
            inv = Inventory(self.path)
            try:
                for _ in range(10): inv.receive("A", 1)
            finally: inv.close()
        ts = [threading.Thread(target=work) for _ in range(6)]; [t.start() for t in ts]; [t.join(120) for t in ts]
        self.assertEqual(i.stock("A"), 60); self.assertEqual(len(i.history("A")), 60); self.assertEqual(i.verify(), [])
    def test_r08_concurrent_fulfillments_never_make_stock_negative(self):
        i = self.inv(); i.add_product("A", "a", 1); i.receive("A", 6)
        for n in range(6): i.reserve(f"o{n}", "A", 1)
        errors = []
        def work(n):
            inv = Inventory(self.path)
            try: inv.fulfill(f"o{n}")
            except Exception as e: errors.append(e)
            finally: inv.close()
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]; [t.start() for t in ts]; [t.join(60) for t in ts]
        self.assertEqual(errors, []); self.assertEqual(i.stock("A"), 0); self.assertEqual(i.verify(), [])
    def test_r08_the_busy_timeout_is_at_least_thirty_seconds(self):
        i = self.inv(); self.assertGreaterEqual(i.db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
