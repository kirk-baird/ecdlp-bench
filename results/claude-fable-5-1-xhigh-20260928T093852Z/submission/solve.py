"""ecdlp-bench submission.

Honest status: this file does NOT contain a polylogarithmic ECDLP algorithm.
No such algorithm was found (see ATTACK.md).  What it contains is

  1. checks for the classical special cases (Pohlig-Hellman when the order is
     composite, Smart's p-adic attack when the curve is anomalous), which the
     benchmark curves are generated to avoid, and
  2. a reasonably engineered generic solver: baby-step giant-step for tiny
     groups and a parallel Pollard rho with distinguished points, an r-adding
     walk, Montgomery simultaneous inversion and the negation map otherwise.

The generic solver is Theta(sqrt(n)) group operations and therefore clears
only the small rungs of the ladder within the time limits.
"""

import math
import os
import random
import sys
import time

try:
    import multiprocessing as mp
except ImportError:  # pragma: no cover
    mp = None


# ----------------------------------------------------------------------------
# Affine short-Weierstrass arithmetic (None = point at infinity)
# ----------------------------------------------------------------------------

def ec_add(P, Q, a, p):
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


def ec_neg(P, p):
    return None if P is None else (P[0], (-P[1]) % p)


def ec_mul(k, P, a, p):
    """Scalar multiplication using Jacobian coordinates internally."""
    if P is None or k == 0:
        return None
    if k < 0:
        return ec_mul(-k, ec_neg(P, p), a, p)
    # Jacobian (X, Y, Z): x = X/Z^2, y = Y/Z^3
    X1, Y1 = P
    RX = RY = RZ = None  # infinity
    for bit in bin(k)[2:]:
        if RZ is not None:
            # doubling
            if RY == 0:
                RX = RY = RZ = None
            else:
                YY = RY * RY % p
                S = 4 * RX * YY % p
                ZZ = RZ * RZ % p
                M = (3 * RX * RX + a * ZZ * ZZ) % p
                X3 = (M * M - 2 * S) % p
                Y3 = (M * (S - X3) - 8 * YY * YY) % p
                Z3 = 2 * RY * RZ % p
                RX, RY, RZ = X3, Y3, Z3
        if bit == "1":
            if RZ is None:
                RX, RY, RZ = X1, Y1, 1
            else:
                # mixed addition (X1,Y1,1) + (RX,RY,RZ)
                ZZ = RZ * RZ % p
                U2 = X1 * ZZ % p
                S2 = Y1 * ZZ * RZ % p
                H = (U2 - RX) % p
                r = (S2 - RY) % p
                if H == 0:
                    if r == 0:
                        # doubling of the affine point
                        YY = RY * RY % p
                        S = 4 * RX * YY % p
                        M = (3 * RX * RX + a * ZZ * ZZ) % p
                        X3 = (M * M - 2 * S) % p
                        Y3 = (M * (S - X3) - 8 * YY * YY) % p
                        Z3 = 2 * RY * RZ % p
                        RX, RY, RZ = X3, Y3, Z3
                    else:
                        RX = RY = RZ = None
                else:
                    HH = H * H % p
                    HHH = HH * H % p
                    V = RX * HH % p
                    X3 = (r * r - HHH - 2 * V) % p
                    Y3 = (r * (V - X3) - RY * HHH) % p
                    Z3 = RZ * H % p
                    RX, RY, RZ = X3, Y3, Z3
    if RZ is None:
        return None
    zi = pow(RZ, -1, p)
    zi2 = zi * zi % p
    return (RX * zi2 % p, RY * zi2 * zi % p)


# ----------------------------------------------------------------------------
# Baby-step giant-step (tiny groups)
# ----------------------------------------------------------------------------

def bsgs(p, a, n, G, Q, lo=0, hi=None):
    """Find k in [lo, hi) with k*G == Q, or None."""
    if hi is None:
        hi = n
    width = hi - lo
    if width <= 0:
        return None
    m = math.isqrt(width) + 1
    # baby steps: j*G for j in [0, m)
    table = {}
    R = None
    for j in range(m):
        key = R if R is None else R[0]
        # store both signs via x only, resolve sign below
        if key not in table:
            table[key] = (j, None if R is None else R[1])
        R = ec_add(R, G, a, p)
    # giant steps: Q - lo*G - i*m*G
    mG = ec_mul(m, G, a, p)
    neg_mG = ec_neg(mG, p)
    T = ec_add(Q, ec_neg(ec_mul(lo, G, a, p), p), a, p)
    for i in range(m + 1):
        key = T if T is None else T[0]
        if key in table:
            j, y = table[key]
            if T is None or T[1] == y:
                k = lo + i * m + j
            else:
                k = lo + i * m - j
            k %= n
            if ec_mul(k, G, a, p) == Q:
                return k
        T = ec_add(T, neg_mG, a, p)
    return None


# ----------------------------------------------------------------------------
# Smart's attack for anomalous curves (#E = p).  Not applicable to the ladder,
# included for completeness of the "special cases" handling.
# ----------------------------------------------------------------------------

def _smart_attack(p, a, b, G, Q):
    p2 = p * p

    def lift(P):
        x, y = P
        # lift x, solve for y mod p^2 by Hensel: y^2 = x^3 + a x + b (mod p^2)
        rhs = (x * x * x + a * x + b) % p2
        # y0^2 == rhs mod p, Newton step: y1 = y0 - (y0^2 - rhs)/(2 y0)
        y1 = (y - (y * y - rhs) * pow(2 * y, -1, p2)) % p2
        return (x, y1)

    def add_mod(P, R):
        # affine addition mod p^2; denominators must be units
        if P is None:
            return R
        if R is None:
            return P
        x1, y1 = P
        x2, y2 = R
        if (x1 - x2) % p == 0:
            if (y1 + y2) % p == 0:
                return None
            lam = (3 * x1 * x1 + a) * pow(2 * y1, -1, p2) % p2
        else:
            lam = (y2 - y1) * pow(x2 - x1, -1, p2) % p2
        x3 = (lam * lam - x1 - x2) % p2
        return x3, (lam * (x1 - x3) - y1) % p2

    def mul_mod(k, P):
        R = None
        for bit in bin(k)[2:]:
            R = add_mod(R, R)
            if bit == "1":
                R = add_mod(R, P)
        return R

    def p_adic_log(P):
        # (p-1)P ... we want pP which lands in E_1; compute (p) * P and read
        # -x/y which is p * (something).  Use t = -x/y mod p^2, divide by p.
        # A cleaner route: compute R = p*P in E(Z/p^2), then t = -x/y in pZ/p^2.
        R = mul_mod(p, P)
        if R is None:
            return None
        x, y = R
        t = (-x * pow(y, -1, p2)) % p2
        return t // p  # element of Z/p

    try:
        Gl, Ql = lift(G), lift(Q)
        tg, tq = p_adic_log(Gl), p_adic_log(Ql)
        if tg is None or tq is None or tg % p == 0:
            return None
        return tq * pow(tg, -1, p) % p
    except (ValueError, ZeroDivisionError):
        return None


# ----------------------------------------------------------------------------
# Parallel Pollard rho with distinguished points
# ----------------------------------------------------------------------------

R_WALK = 1024        # number of random multipliers in the r-adding walk
BATCH = 128          # walks per worker, sharing one inversion per step
CYCLE_W = 48         # checkpoint spacing for fruitless-cycle detection
DP_SHIFT = 10        # DP test uses x >> DP_SHIFT, the walk index uses x & (r-1)


def _canon(x, y, al, be, p, n):
    """Negation-map canonical representative (y <= p/2)."""
    if y > p - y:
        return x, p - y, (-al) % n, (-be) % n
    return x, y, al, be


def _escape_cycle(x, y, al, be, p, a, n, Rx, Ry, Rc, Rd, rmask):
    """The walk at (x, y) is inside a short fruitless cycle.  Walk the cycle
    once, find the point with the smallest x (a canonical choice shared by
    every walk that landed in this cycle), and leave it by doubling."""
    best = (x, y, al, be)
    cx, cy, cal, cbe = x, y, al, be
    for _ in range(CYCLE_W + 2):
        j = cx & rmask
        P = ec_add((cx, cy), (Rx[j], Ry[j]), a, p)
        if P is None:
            break
        cx, cy, cal, cbe = _canon(P[0], P[1], (cal + Rc[j]) % n, (cbe + Rd[j]) % n, p, n)
        if cx == x:
            break
        if cx < best[0]:
            best = (cx, cy, cal, cbe)
    bx, by, bal, bbe = best
    D = ec_add((bx, by), (bx, by), a, p)
    if D is None:
        return None
    return _canon(D[0], D[1], (2 * bal) % n, (2 * bbe) % n, p, n)


def _walk_worker(wid, p, a, n, G, Q, R_pts, R_coef, dp_mask, out_q, seed):
    """One worker: BATCH simultaneous r-adding walks; reports distinguished
    points (x, y, alpha, beta) with alpha*G + beta*Q == (x, y)."""
    rng = random.Random(seed)
    Rx = [P[0] for P in R_pts]
    Ry = [P[1] for P in R_pts]
    Rc = [c for c, _ in R_coef]
    Rd = [d for _, d in R_coef]
    B = BATCH
    rmask = R_WALK - 1
    dp_shift = DP_SHIFT

    def fresh():
        while True:
            al = rng.randrange(1, n)
            be = rng.randrange(1, n)
            P = ec_add(ec_mul(al, G, a, p), ec_mul(be, Q, a, p), a, p)
            if P is not None:
                return _canon(P[0], P[1], al, be, p, n)

    xs = [0] * B
    ys = [0] * B
    als = [0] * B
    bes = [0] * B
    ck = [0] * B          # checkpoint x for cycle detection
    for i in range(B):
        xs[i], ys[i], als[i], bes[i] = fresh()
        ck[i] = xs[i]

    dx = [0] * B
    prod = [0] * B
    idx = [0] * B
    found = []
    last_flush = time.monotonic()
    step = 0
    while True:
        step += 1
        take_ck = (step % CYCLE_W == 0)
        # choose multiplier per walk and form denominators
        for i in range(B):
            j = xs[i] & rmask
            idx[i] = j
            d = (Rx[j] - xs[i]) % p
            if d == 0:
                # degenerate (X == +-R_j): restart this walk
                xs[i], ys[i], als[i], bes[i] = fresh()
                ck[i] = xs[i]
                j = xs[i] & rmask
                idx[i] = j
                d = (Rx[j] - xs[i]) % p
                if d == 0:
                    d = 1  # essentially impossible twice; keep going
            dx[i] = d
        # Montgomery batch inversion
        acc = 1
        for i in range(B):
            acc = acc * dx[i] % p
            prod[i] = acc
        inv = pow(acc, -1, p)
        for i in range(B - 1, -1, -1):
            if i:
                inv_i = inv * prod[i - 1] % p
                inv = inv * dx[i] % p
            else:
                inv_i = inv
            j = idx[i]
            x1 = xs[i]
            y1 = ys[i]
            lam = (Ry[j] - y1) * inv_i % p
            x3 = (lam * lam - x1 - Rx[j]) % p
            y3 = (lam * (x1 - x3) - y1) % p
            al = als[i] + Rc[j]
            be = bes[i] + Rd[j]
            if al >= n:
                al -= n
            if be >= n:
                be -= n
            # negation map: canonical representative has y <= p - y
            if y3 > p - y3:
                y3 = p - y3
                al = n - al
                be = n - be
                if al == n:
                    al = 0
                if be == n:
                    be = 0
            if (x3 >> dp_shift) & dp_mask == 0:
                found.append((x3, y3, al, be))
                x3, y3, al, be = fresh()
                ck[i] = x3
            elif x3 == ck[i]:
                # fruitless cycle: leave it deterministically
                esc = _escape_cycle(x3, y3, al, be, p, a, n, Rx, Ry, Rc, Rd, rmask)
                if esc is None:
                    esc = fresh()
                x3, y3, al, be = esc
                ck[i] = x3
            elif take_ck:
                ck[i] = x3
            xs[i] = x3
            ys[i] = y3
            als[i] = al
            bes[i] = be
        if found:
            now = time.monotonic()
            if len(found) >= 64 or now - last_flush > 0.5:
                out_q.put(found)
                found = []
                last_flush = now


class _Found(Exception):
    def __init__(self, k):
        self.k = k


class _InlineSink:
    """Queue stand-in for the single-process fallback: checks collisions as
    the walker reports distinguished points and raises _Found on success."""

    def __init__(self, p, a, n, G, Q):
        self.p, self.a, self.n, self.G, self.Q = p, a, n, G, Q
        self.seen = {}

    def put(self, batch):
        for item in batch:
            k = _collide(self.seen, item, self.n, self.p)
            if k is not None and ec_mul(k, self.G, self.a, self.p) == self.Q:
                raise _Found(k)


def _setup_walk(p, a, n, G, Q, rng):
    while True:
        R_coef = [(rng.randrange(1, n), rng.randrange(1, n)) for _ in range(R_WALK)]
        R_pts = [ec_add(ec_mul(c, G, a, p), ec_mul(d, Q, a, p), a, p) for c, d in R_coef]
        if all(P is not None for P in R_pts):
            return R_coef, R_pts


def _dp_mask(bits, workers):
    # expected total steps ~ sqrt(pi n / 4) with the negation map; keep the
    # number of stored distinguished points around 2^14 .. 2^20 and the walk
    # length well below sqrt(n)/workers.
    dp_bits = max(0, min(bits // 2 - 14, 28))
    return (1 << dp_bits) - 1


def _rho_serial(p, a, n, G, Q):
    """Single-process rho used when multiprocessing is unavailable."""
    rng = random.SystemRandom()
    R_coef, R_pts = _setup_walk(p, a, n, G, Q, rng)
    sink = _InlineSink(p, a, n, G, Q)
    try:
        _walk_worker(0, p, a, n, G, Q, R_pts, R_coef, _dp_mask(n.bit_length(), 1),
                     sink, rng.getrandbits(64))
    except _Found as f:
        return f.k


def _collide(seen, item, n, p):
    x, y, al, be = item
    prev = seen.get(x)
    if prev is None:
        seen[x] = (y, al, be)
        return None
    y0, al0, be0 = prev
    if y0 == y:
        # al*G + be*Q == al0*G + be0*Q
        if be == be0:
            return None
        return (al0 - al) * pow((be - be0) % n, -1, n) % n
    # y0 == -y: al*G + be*Q == -(al0*G + be0*Q)
    if (be + be0) % n == 0:
        return None
    return (-(al0 + al)) * pow((be + be0) % n, -1, n) % n


def _rho_parallel(p, a, n, G, Q, workers):
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        ctx = mp.get_context()
    rng = random.SystemRandom()
    R_coef, R_pts = _setup_walk(p, a, n, G, Q, rng)
    dp_mask = _dp_mask(n.bit_length(), workers)
    out_q = ctx.Queue()
    procs = []
    for wid in range(workers):
        seed = rng.getrandbits(64)
        pr = ctx.Process(target=_walk_worker,
                         args=(wid, p, a, n, G, Q, R_pts, R_coef, dp_mask, out_q, seed),
                         daemon=True)
        pr.start()
        procs.append(pr)
    seen = {}
    try:
        while True:
            batch = out_q.get()
            for item in batch:
                k = _collide(seen, item, n, p)
                if k is not None:
                    if ec_mul(k, G, a, p) == Q:
                        return k
                    # a false collision (should not happen); keep going
    finally:
        for pr in procs:
            try:
                pr.kill()
            except Exception:
                pass
        for pr in procs:
            try:
                pr.join(timeout=1.0)
            except Exception:
                pass


# ----------------------------------------------------------------------------
# Pohlig-Hellman (order composite) -- not applicable on the ladder
# ----------------------------------------------------------------------------

def _factor_trial(m, bound=1 << 20):
    fs = {}
    d = 2
    while d * d <= m and d <= bound:
        while m % d == 0:
            fs[d] = fs.get(d, 0) + 1
            m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        fs[m] = fs.get(m, 0) + 1
    return fs


def _crt(residues, moduli):
    x, M = 0, 1
    for r, m in zip(residues, moduli):
        t = (r - x) * pow(M % m, -1, m) % m
        x += M * t
        M *= m
    return x % M


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def _solve_prime(p, a, b, n, G, Q):
    """Discrete log in a group of prime order n."""
    if Q is None:
        return 0
    if Q == G:
        return 1
    if Q == ec_neg(G, p):
        return n - 1
    if n == p:
        k = _smart_attack(p, a, b, G, Q)
        if k is not None and ec_mul(k, G, a, p) == Q:
            return k
    if n < (1 << 34):
        k = bsgs(p, a, n, G, Q)
        if k is not None:
            return k
    workers = os.cpu_count() or 1
    if mp is None or workers <= 1:
        return _rho_serial(p, a, n, G, Q)
    try:
        return _rho_parallel(p, a, n, G, Q, workers)
    except (OSError, RuntimeError):
        # e.g. no fork()/semaphores available in an exotic sandbox
        return _rho_serial(p, a, n, G, Q)


def solve(p, a, b, n, G, Q):
    G = (G[0] % p, G[1] % p)
    Q = (Q[0] % p, Q[1] % p)
    # Pohlig-Hellman in case n is composite (the bench guarantees prime n).
    fs = _factor_trial(n)
    if len(fs) == 1 and list(fs.values())[0] == 1:
        return _solve_prime(p, a, b, n, G, Q) % n
    residues, moduli = [], []
    for q, e in fs.items():
        qe = q ** e
        h = n // qe
        Gq = ec_mul(h, G, a, p)
        Qq = ec_mul(h, Q, a, p)
        # lift digit by digit
        k = 0
        Gq1 = ec_mul(q ** (e - 1), Gq, a, p)  # order q
        for i in range(e):
            Ti = ec_mul(q ** (e - 1 - i), ec_add(Qq, ec_neg(ec_mul(k, Gq, a, p), p), a, p), a, p)
            d = 0 if Ti is None else _solve_prime(p, a, b, q, Gq1, Ti)
            k += d * q ** i
        residues.append(k % qe)
        moduli.append(qe)
    return _crt(residues, moduli) % n


if __name__ == "__main__":  # quick self-test
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "bench", "curves.json")) as f:
        curves = json.load(f)["curves"]
    max_bits = int(sys.argv[1]) if len(sys.argv) > 1 else 48
    for c in curves:
        if c["bits"] > max_bits:
            break
        p, a, b, n = c["p"], c["a"], c["b"], c["n"]
        G = (c["gx"], c["gy"])
        k = random.SystemRandom().randrange(1, n)
        Q = ec_mul(k, G, a, p)
        t0 = time.time()
        k2 = solve(p, a, b, n, G, Q)
        print(c["name"], "ok" if k2 % n == k else "WRONG", "%.2fs" % (time.time() - t0), flush=True)
