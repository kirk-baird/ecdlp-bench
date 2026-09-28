"""ecdlp-bench submission.

HONEST SUMMARY: I did not find a polylog-time algorithm for the ECDLP on prime-order
curves over prime fields, and I do not believe this submission scores above 0%.  See
ATTACK.md for what was tried, what was measured, and why each avenue fails.

What this file does is apply, in order, every attack that is actually known to be
polynomial-time when its precondition holds, and fall back to the best generic algorithm
when none does:

  1. trivial cases (Q = O, Q = +-G);
  2. Pohlig-Hellman over the true order of G, in case n is not prime after all;
  3. the Smart / Satoh-Araki / Semaev p-adic attack when the curve is anomalous (#E = p):
     genuinely polylog, but the benchmark curves all have trace != 1;
  4. no MOV / Frey-Ruck path: the measured embedding degree exceeds 2e6 on every benchmark
     curve, so the target field F_{p^k} cannot even be written down;
  5. otherwise a parallel Pollard rho with distinguished points -- Theta(sqrt(n)), i.e.
     exponential in log n.  It clears the ladder up to bench-48 and times out at bench-56,
     far short of secp256k1.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from generic import add, mul, bsgs, pollard_rho_parallel  # noqa: E402
from padic import smart_attack  # noqa: E402

# n below this is handled by deterministic BSGS in-process (memory ~ sqrt(n) entries).
BSGS_LIMIT = 1 << 28


def _factor(m):
    """Trial division plus Pollard rho; only ever exercised if n is not prime."""
    import random as _r
    fs = {}
    for q in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47):
        while m % q == 0:
            fs[q] = fs.get(q, 0) + 1
            m //= q
    stack = [m] if m > 1 else []
    while stack:
        cur = stack.pop()
        if cur == 1:
            continue
        if _is_prime(cur):
            fs[cur] = fs.get(cur, 0) + 1
            continue
        d = _rho_factor(cur, _r)
        stack.append(d)
        stack.append(cur // d)
    return fs


def _is_prime(m):
    if m < 2:
        return False
    for q in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if m % q == 0:
            return m == q
    d, s = m - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for base in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(base, d, m)
        if x in (1, m - 1):
            continue
        for _ in range(s - 1):
            x = x * x % m
            if x == m - 1:
                break
        else:
            return False
    return True


def _rho_factor(m, _r):
    if m % 2 == 0:
        return 2
    while True:
        c = _r.randrange(1, m)
        x = y = _r.randrange(0, m)
        d = 1
        while d == 1:
            x = (x * x + c) % m
            y = (y * y + c) % m
            y = (y * y + c) % m
            d = _gcd(abs(x - y), m)
        if d != m:
            return d


def _gcd(x, y):
    while y:
        x, y = y, x % y
    return x


def solve(p, a, b, n, G, Q):
    a %= p
    b %= p

    # ---- 1. trivial cases -------------------------------------------------
    if Q is None:
        return 0
    if Q == G:
        return 1
    if Q == (G[0], (-G[1]) % p):
        return n - 1

    # ---- 2. Pohlig-Hellman, in case the given order is not prime ----------
    if not _is_prime(n):
        fs = _factor(n)
        rems, mods = [], []
        for q, e in fs.items():
            qe = q ** e
            cof = n // qe
            Gq = mul(cof, G, a, p)
            Qq = mul(cof, Q, a, p)
            kq = 0
            Gi = mul(q ** (e - 1), Gq, a, p)
            for i in range(e):
                Ri = add(Qq, mul(-(kq) % n, Gq, a, p), a, p)
                Ri = mul(q ** (e - 1 - i), Ri, a, p)
                di = bsgs(p, a, q, Gi, Ri)
                if di is None:
                    kq = None
                    break
                kq += di * q ** i
            if kq is None:
                break
            rems.append(kq % qe)
            mods.append(qe)
        else:
            k, M = 0, 1
            for r, m in zip(rems, mods):
                k += M * ((r - k) * pow(M, -1, m) % m)
                M *= m
            if mul(k % n, G, a, p) == Q:
                return k % n

    # ---- 3. anomalous curve: the one known polylog attack -----------------
    if n == p:
        k = smart_attack(p, a, b, G, Q)
        if k is not None and mul(k, G, a, p) == Q:
            return k

    # ---- 4. no MOV / Frey-Ruck path ---------------------------------------
    # The transfer needs the embedding degree k = ord_n(p) to be tiny.  Measured k > 2e6 on
    # every benchmark curve, so F_{p^k} cannot even be written down; and even k = 2 would
    # leave index calculus in F_{p^k}, which is subexponential rather than polylog.

    # ---- 5. generic fallback: exponential, not polylog --------------------
    if n < BSGS_LIMIT:
        k = bsgs(p, a, n, G, Q)
        if k is not None:
            return k
    return pollard_rho_parallel(p, a, n, G, Q)
