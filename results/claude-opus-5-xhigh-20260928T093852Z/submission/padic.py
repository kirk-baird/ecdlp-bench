"""Fixed-precision Q_p arithmetic and the Smart / Satoh-Araki / Semaev anomalous attack.

Pure standard library.  This is the one genuinely polylog-time ECDLP algorithm that is
publicly known; it applies exactly when #E(F_p) = p (trace 1).  The benchmark curves are
built to have trace != 1, so this path never fires on them, but a solver that did not try
it would be incomplete.

Elements of Q_p are carried as (unit, val) meaning unit * p**val with `unit` a p-adic unit
known modulo p**prec (or the exact zero, represented as (0, 0)).
"""


class Qp:
    """Q_p to a fixed relative precision."""

    __slots__ = ("p", "prec", "mod")

    def __init__(self, p, prec):
        self.p = p
        self.prec = prec
        self.mod = p ** prec

    def norm(self, u, v):
        """Normalise so that u is a unit (or the value is zero)."""
        p, mod = self.p, self.mod
        u %= mod
        if u == 0:
            return (0, 0)
        while u % p == 0:
            u //= p
            v += 1
            if u == 0:
                return (0, 0)
        return (u % mod, v)

    def from_int(self, k):
        return self.norm(k, 0)

    def add(self, A, B):
        ua, va = A
        ub, vb = B
        if ua == 0:
            return B
        if ub == 0:
            return A
        p = self.p
        v = va if va < vb else vb
        return self.norm(ua * p ** (va - v) + ub * p ** (vb - v), v)

    def neg(self, A):
        return (0, 0) if A[0] == 0 else ((-A[0]) % self.mod, A[1])

    def sub(self, A, B):
        return self.add(A, self.neg(B))

    def mul(self, A, B):
        if A[0] == 0 or B[0] == 0:
            return (0, 0)
        return (A[0] * B[0] % self.mod, A[1] + B[1])

    def inv(self, A):
        if A[0] == 0:
            raise ZeroDivisionError("0 is not invertible in Q_p")
        return (pow(A[0], -1, self.mod), -A[1])

    def div(self, A, B):
        return self.mul(A, self.inv(B))

    def val(self, A):
        return None if A[0] == 0 else A[1]

    def to_int(self, A):
        """The value as an integer, valid when val >= 0."""
        u, v = A
        if u == 0:
            return 0
        if v < 0:
            raise ValueError("negative valuation")
        return u * self.p ** v % self.mod


def _qp_add_points(K, P, Q, a):
    """Short-Weierstrass affine addition over Q_p.  None is the point at infinity."""
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    dx = K.sub(x2, x1)
    if dx[0] == 0:
        s = K.add(y1, y2)
        if s[0] == 0:
            return None
        num = K.add(K.mul(K.from_int(3), K.mul(x1, x1)), a)
        lam = K.div(num, K.mul(K.from_int(2), y1))
    else:
        lam = K.div(K.sub(y2, y1), dx)
    x3 = K.sub(K.sub(K.mul(lam, lam), x1), x2)
    y3 = K.sub(K.mul(lam, K.sub(x1, x3)), y1)
    return (x3, y3)


def _qp_mul(K, k, P, a):
    R = None
    for bit in bin(k)[2:]:
        R = _qp_add_points(K, R, R, a)
        if bit == "1":
            R = _qp_add_points(K, R, P, a)
    return R


def _sqrt_p_adic(K, c, y0):
    """Hensel-lift the square root of c in Z_p starting from y0 (y0^2 = c mod p)."""
    p, prec = K.p, K.prec
    # Work with integers: we need y with y^2 == c mod p^prec, y == y0 mod p.
    cval = K.to_int(c)
    y = y0 % p
    m = p
    while m < K.mod:
        m = min(m * m, K.mod)
        # Newton: y <- y - (y^2 - c)/(2y)
        y = (y - (y * y - cval) * pow(2 * y, -1, m)) % m
    return K.norm(y % K.mod, 0)


def smart_attack(p, a, b, G, Q, prec=8):
    """ECDLP on an anomalous curve (#E(F_p) = p).  Returns k with k*G = Q, or None.

    Lift E and the two points to Q_p, push p*P into the kernel of reduction, and read the
    formal-group parameter t = -x/y.  Because the curve is anomalous the first p-adic digit
    of t(p*Ptil)/p does not depend on the chosen lift, so

        psi(P) = (t(p*Ptil)/p) mod p

    is a well-defined group homomorphism E(F_p) -> (F_p, +), and k = psi(Q)/psi(G) mod p.
    """
    K = Qp(p, prec)
    A = K.from_int(a % p)
    B = K.from_int(b % p)

    def lift(P, shift):
        x = K.from_int((P[0] + shift * p) % (p ** prec))
        rhs = K.add(K.add(K.mul(K.mul(x, x), x), K.mul(A, x)), B)
        y = _sqrt_p_adic(K, rhs, P[1] % p)
        if K.to_int(y) % p != P[1] % p:
            y = K.neg(y)
        return (x, y)

    def psi(P, shift):
        R = _qp_mul(K, p, lift(P, shift), A)
        if R is None:
            return None
        t = K.div(K.neg(R[0]), R[1])
        if t[0] == 0:
            return 0
        if t[1] < 1:
            return None
        # t / p, reduced mod p
        return K.to_int((t[0], t[1] - 1)) % p

    pg = psi(G, 1)
    pq = psi(Q, 1)
    if not pg:
        return None
    return pq * pow(pg, -1, p) % p
