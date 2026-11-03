import random
import unittest

from kv_cache.allocators import ContiguousCache, OutOfMemory, PagedCache


class AllocatorTests(unittest.TestCase):
    def test_analytical_memory_and_roundtrip(self):
        for factory, allocated in [(ContiguousCache, 128), (PagedCache, 96)]:
            with self.subTest(allocator=factory.__name__):
                cache = factory(16, 8)
                cache.reserve("a", 8)
                cache.reserve("b", 8)
                for name, length in [("a", 3), ("b", 5)]:
                    for i in range(length):
                        cache.append(name, bytes([i + 1]) * 8)
                    for i in range(length):
                        self.assertEqual(cache.read(name, i), bytes([i + 1]) * 8)
                self.assertEqual(cache.snapshot()["allocated_bytes"], allocated)
                self.assertEqual(cache.snapshot()["live_bytes"], 64)
                cache.check_invariants()

    def test_external_fragmentation_and_coalescing(self):
        cache = ContiguousCache(12, 1)
        for name in "abc":
            cache.reserve(name, 4)
        cache.free("a")
        cache.free("c")
        before = cache.snapshot()
        with self.assertRaises(OutOfMemory):
            cache.reserve("d", 6)
        self.assertEqual(before["free_bytes"], 8)
        self.assertEqual(before["largest_free_span_bytes"], 4)
        self.assertNotIn("d", cache.sequences)
        cache.free("b")
        cache.reserve("d", 12)
        cache.check_invariants()

    def test_page_boundary_failure_and_reuse(self):
        cache = PagedCache(8, 1, 4)
        cache.reserve("a", 8)
        cache.reserve("b", 4)
        for _ in range(4):
            cache.append("a", b"a")
            cache.append("b", b"b")
        with self.assertRaises(OutOfMemory):
            cache.append("a", b"x")
        self.assertEqual(cache.sequences["a"].length, 4)
        cache.check_invariants()
        cache.free("b")
        cache.append("a", b"x")
        self.assertEqual(cache.read("a", 4), b"x")
        self.assertEqual([cache.read("a", i) for i in range(4)], [b"a"] * 4)
        cache.check_invariants()

    def test_noncontiguous_page_translation(self):
        cache = PagedCache(12, 1, 4)
        for name in "abc":
            cache.reserve(name, 8)
            cache.append(name, name.encode())
        cache.free("a")
        for _ in range(4):
            cache.append("c", b"z")
        self.assertEqual(cache.sequences["c"].slots, [2, 0])
        self.assertEqual([cache.read("c", i) for i in range(5)], [b"c"] + [b"z"] * 4)
        self.assertEqual(cache.read("b", 0), b"b")
        cache.check_invariants()

    def test_validation_preserves_state(self):
        for factory in (ContiguousCache, PagedCache):
            cache = factory(8, 1)
            cache.reserve("a", 1)
            for name, limit in [("a", 1), ("b", 0), ("", 2)]:
                with self.assertRaises(ValueError):
                    cache.reserve(name, limit)
            with self.assertRaises(ValueError):
                cache.append("a", b"wrong width")
            cache.append("a", b"a")
            with self.assertRaises(ValueError):
                cache.append("a", b"a")
            with self.assertRaises(IndexError):
                cache.read("a", -1)
            with self.assertRaises(KeyError):
                cache.free("missing")
            cache.check_invariants()

    def test_seeded_operations_against_byte_oracle(self):
        for factory in (ContiguousCache, PagedCache):
            cache = factory(64, 4)
            rng = random.Random(14)
            oracle = {}
            for step in range(1000):
                operation = rng.choice(("reserve", "append", "free"))
                if operation == "reserve":
                    name, limit = str(step), rng.randint(1, 16)
                    try:
                        cache.reserve(name, limit)
                    except OutOfMemory:
                        pass
                    else:
                        oracle[name] = (limit, [])
                elif oracle:
                    name = rng.choice(sorted(oracle))
                    limit, values = oracle[name]
                    if operation == "free":
                        cache.free(name)
                        del oracle[name]
                    elif len(values) < limit:
                        value = rng.getrandbits(32).to_bytes(4, "little")
                        try:
                            cache.append(name, value)
                        except OutOfMemory:
                            pass
                        else:
                            values.append(value)
                cache.check_invariants()
                self.assertEqual(set(cache.sequences), set(oracle))
                for name, (_, values) in oracle.items():
                    self.assertEqual(cache.sequences[name].length, len(values))
                    self.assertEqual([cache.read(name, i) for i in range(len(values))], values)
            for name in list(oracle):
                cache.free(name)
            self.assertEqual(cache.snapshot()["free_bytes"], 256)
            cache.check_invariants()


if __name__ == "__main__":
    unittest.main()
