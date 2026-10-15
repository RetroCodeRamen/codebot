def is_valid_ipv4(text):
    if not isinstance(text, str): return False
    parts = text.split(".")
    if len(parts) != 4: return False
    for p in parts:
        if not p or not all(c in "0123456789" for c in p): return False
        if len(p) > 1 and p[0] == "0": return False
        if int(p) > 255: return False
    return True

def ipv4_to_int(text):
    if not is_valid_ipv4(text): raise ValueError(f"not a valid IPv4 address: {text!r}")
    n = 0
    for p in text.split("."): n = (n << 8) | int(p)
    return n
