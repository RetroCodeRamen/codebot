def is_valid_ipv4(text):
    if not isinstance(text, str):
        return False
    if not text:
        return False
    parts = text.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part:
            return False
        if part[0] == "0" and len(part) > 1:
            return False
        if not part.isdigit():
            return False
        if int(part) > 255:
            return False
    return True


def ipv4_to_int(text):
    if not is_valid_ipv4(text):
        raise ValueError
    parts = text.split(".")
    return (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])
