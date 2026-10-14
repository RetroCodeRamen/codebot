def wrap(text, width):
    if width < 1:
        raise ValueError("width must be >= 1")
    out = []
    for pi, para in enumerate(text.split("\n\n")):
        if pi:
            out.append("")
        line = ""
        for word in para.split():
            while len(word) > width:
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        if line:
            out.append(line)
    return out
