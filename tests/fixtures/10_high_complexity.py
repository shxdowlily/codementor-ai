"""Искусственно раздутая функция с высокой цикломатической сложностью."""
def classify(a, b, c, d, e):
    result = []
    if a > 0:
        if b > 0:
            if c > 0:
                result.append("a")
            else:
                result.append("b")
        elif d > 0:
            result.append("c")
        else:
            result.append("d")
    elif e > 0:
        for i in range(e):
            if i % 2 == 0:
                result.append(i)
            else:
                try:
                    result.append(1 / (i - e))
                except ZeroDivisionError:
                    result.append(0)
    while len(result) < 3:
        result.append(0)
    return result
