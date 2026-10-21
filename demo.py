# Edit this file and save: Blender rebuilds the scene.
# Nothing here names a shape. Inputs with no value (n, r, a, ...) get a sample size;
# pin one with a line starting with  #viz  followed by  n=8


def f(x):
    return x * x


def g(a, b, c):
    return a * b * c


def pairs(n):
    count = 0
    for i in range(n):
        for j in range(i):
            count += 1
    return count


def running_total(n):
    total = 0
    for i in range(n):
        total += i
    return total


closed = n * (n - 1) // 2


table = [[0] * n for _ in range(n)]


def hits(r):
    k = 0
    for x in range(2 * r + 1):
        for y in range(2 * r + 1):
            if (x - r) ** 2 + (y - r) ** 2 <= r * r:
                k += 1
    return k


def triples(n):
    c = 0
    for i in range(n):
        for j in range(i):
            for k in range(j):
                c += 1
    return c


def expand(x):
    return (x + 1) * (x + 1)
