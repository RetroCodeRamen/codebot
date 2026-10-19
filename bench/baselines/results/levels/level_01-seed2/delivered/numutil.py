def clamp(value, low, high):
    if low > high:
        raise ValueError
    if value < low:
        return low
    if value > high:
        return high
    return value


def is_prime(n):
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True
