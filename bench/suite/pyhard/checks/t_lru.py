import sys
sys.path.insert(0, ".")
try:
    from lru import LRUCache
except Exception as e:
    print(f"cannot import LRUCache from lru.py: {e!r}"); sys.exit(1)
f = []
try:
    c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("c", 3)
    if c.keys() != ["b", "c"]: f.append(f"after putting a,b,c into capacity 2, keys() is {c.keys()!r}, expected ['b', 'c']")
    if c.get("b") != 2 or c.keys() != ["c", "b"]: f.append(f"get('b') should return 2 and make it most recent: keys() is {c.keys()!r}, expected ['c', 'b']")
    c.put("d", 4)
    if c.keys() != ["b", "d"]: f.append(f"after put('d') the least recent ('c') must be evicted: keys() is {c.keys()!r}, expected ['b', 'd']")
    c.put("b", 20)
    if c.get("b") != 20 or c.keys() != ["d", "b"] or len(c) != 2: f.append(f"updating an existing key must refresh it: keys() {c.keys()!r} len {len(c)}")
    if ("d" in c) is not True or c.keys() != ["d", "b"]: f.append("`in` must work and must NOT refresh recency")
    if c.get("zzz") is not None or c.get("zzz", 7) != 7: f.append("get() of a missing key must return the default (None, or the given default)")
    z = LRUCache(0); z.put("x", 1)
    if len(z) != 0 or z.get("x") is not None: f.append("capacity 0 must store nothing")
except Exception as e: f.append(f"raised {e!r}")
print("\n".join(f)); sys.exit(1 if f else 0)
