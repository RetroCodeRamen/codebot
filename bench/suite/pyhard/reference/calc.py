def _tokenize(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            while j < len(s) and (s[j].isdigit() or s[j] == "."):
                j += 1
            txt = s[i:j]
            try:
                out.append(float(txt) if "." in txt else int(txt))
            except ValueError:
                raise ValueError(f"bad number {txt!r}")
            i = j
        elif c in "+-*/()":
            out.append(c)
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r}")
    return out


def evaluate(expr):
    toks, pos = _tokenize(expr), [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def eat():
        t = peek()
        pos[0] += 1
        return t

    def expr_():
        v = term()
        while peek() in ("+", "-"):
            op = eat()
            r = term()
            v = v + r if op == "+" else v - r
        return v

    def term():
        v = factor()
        while peek() in ("*", "/"):
            op = eat()
            r = factor()
            if op == "*":
                v = v * r
            else:
                if r == 0:
                    raise ValueError("division by zero")
                v = v / r
        return v

    def factor():
        t = peek()
        if t == "-":
            eat()
            return -factor()
        if t == "+":
            eat()
            return factor()
        if t == "(":
            eat()
            v = expr_()
            if eat() != ")":
                raise ValueError("missing )")
            return v
        if isinstance(t, (int, float)):
            eat()
            return t
        raise ValueError("unexpected end" if t is None else f"unexpected {t!r}")

    v = expr_()
    if peek() is not None:
        raise ValueError(f"unexpected {peek()!r}")
    return v
