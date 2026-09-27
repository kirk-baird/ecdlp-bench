"""Minimal short-Weierstrass arithmetic over F_p. Affine points, None is infinity.

This is what the grader uses to check answers. Submissions may import it.
"""


def add(P, Q, a, p):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2:
        if (y1 + y2) % p == 0:
            return None
        lam = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, p) % p
    x3 = (lam * lam - x1 - x2) % p
    return x3, (lam * (x1 - x3) - y1) % p


def neg(P, p):
    return None if P is None else (P[0], -P[1] % p)


def mul(k, P, a, p):
    R = None
    for bit in bin(k)[2:] if k > 0 else "":
        R = add(R, R, a, p)
        if bit == "1":
            R = add(R, P, a, p)
    return R


def on_curve(P, a, b, p):
    if P is None:
        return True
    x, y = P
    return (y * y - x * x * x - a * x - b) % p == 0
