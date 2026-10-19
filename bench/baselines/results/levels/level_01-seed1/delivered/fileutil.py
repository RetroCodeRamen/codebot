import os

def safe_filename(name):
    if not isinstance(name, str):
        raise TypeError
    result = []
    for ch in name:
        if ch.isascii() and (ch.isalpha() or ch.isdigit() or ch == '.' or ch == '_' or ch == '-'):
            result.append(ch)
        else:
            result.append('_')
    name = ''.join(result)
    while name.startswith('.'):
        name = name[1:]
    if len(name) > 255:
        name = name[:255]
    if not name:
        name = 'unnamed'
    return name


def split_ext(name):
    if not isinstance(name, str):
        raise TypeError
    if '/' in name:
        raise ValueError
    if '.' not in name:
        return (name, '')
    last_dot = name.rfind('.')
    if last_dot == 0:
        return (name, '')
    if last_dot == len(name) - 1:
        return (name, '')
    return (name[:last_dot], name[last_dot:])
