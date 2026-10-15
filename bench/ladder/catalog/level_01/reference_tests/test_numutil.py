import time, unittest
import numutil

class TestClamp(unittest.TestCase):
    def test_r03_examples_from_the_spec(self):
        self.assertEqual(numutil.clamp(5, 1, 3), 3); self.assertEqual(numutil.clamp(-1, 0, 10), 0); self.assertEqual(numutil.clamp(2.5, 0, 10), 2.5)
    def test_r03_value_inside_is_returned_itself(self):
        self.assertEqual(numutil.clamp(7, 0, 10), 7); self.assertEqual(numutil.clamp(0, 0, 10), 0); self.assertEqual(numutil.clamp(10, 0, 10), 10)
    def test_r03_floats_and_negative_ranges(self):
        self.assertEqual(numutil.clamp(-5.5, -3.0, -1.0), -3.0); self.assertEqual(numutil.clamp(0.5, -1, 1), 0.5)
    def test_r03_low_equal_high_is_allowed(self): self.assertEqual(numutil.clamp(9, 4, 4), 4)
    def test_r03_low_above_high_raises_value_error(self):
        with self.assertRaises(ValueError): numutil.clamp(1, 5, 2)
    def test_r03_returns_the_bound_object_when_clamped(self):
        r = numutil.clamp(100, 0, 2.5); self.assertEqual(r, 2.5); self.assertIsInstance(r, float)

class TestIsPrime(unittest.TestCase):
    def test_r04_examples_from_the_spec(self):
        for n in (2, 97): self.assertIs(numutil.is_prime(n), True)
        for n in (1, 0, -7, 561): self.assertIs(numutil.is_prime(n), False)
    def test_r04_small_primes_and_composites(self):
        primes = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47}
        for n in range(-3, 50): self.assertIs(numutil.is_prime(n), n in primes, n)
    def test_r04_squares_of_primes_are_not_prime(self):
        for p in (2, 3, 5, 7, 11, 13, 101): self.assertIs(numutil.is_prime(p * p), False)
    def test_r04_carmichael_numbers(self):
        for n in (561, 1105, 1729, 2465, 6601): self.assertIs(numutil.is_prime(n), False)
    def test_r04_larger_primes(self):
        for n in (7919, 104729, 1_000_003): self.assertIs(numutil.is_prime(n), True)
    def test_r04_a_large_prime_is_fast(self):
        t = time.time(); self.assertIs(numutil.is_prime(1_000_000_007), True); self.assertLess(time.time() - t, 1.0)
    def test_r04_returns_a_real_bool(self): self.assertIs(type(numutil.is_prime(7)), bool)
    def test_r04_non_int_raises_type_error(self):
        for bad in (True, False, 3.0, "7", None, [7]):
            with self.assertRaises(TypeError): numutil.is_prime(bad)
