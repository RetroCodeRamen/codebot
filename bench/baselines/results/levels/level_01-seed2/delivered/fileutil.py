def safe_filename(name):
    if not isinstance(name, str):
        raise TypeError
    if not name:
        return "unnamed"
    result = ""
    for ch in name:
        if ch.isalnum() or ch in ".-_":
            result += ch
        else:
            result += "_"
    while result.startswith("."):
        result = result[1:]
    if not result:
        return "unnamed"
    if len(result) > 255:
        result = result[:255]
    return result


def split_ext(name):
    if "/" in name:
        raise ValueError
    if "." not in name:
        return (name, "")
    last_dot = name.rfind(".")
    if last_dot == 0:
        return (name, "")
    return (name[:last_dot], name[last_dot:])
