def clamp(value, low, high):
    if low > high: raise ValueError("low must not exceed high")
    if value < low: return low
    if value > high: return high
    return value

def is_prime(n):
    if isinstance(n, bool) or not isinstance(n, int): raise TypeError("n must be an int")
    if n < 2: return False
    if n < 4: return True
    if n % 2 == 0 or n % 3 == 0: return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0: return False
        i += 6
    return True
