"""ECDLP solver for ecdlp-bench.

HONEST STATUS: this is NOT a polylogarithmic algorithm.  No polylog (or even
subexponential) ECDLP algorithm for prime-field curves is known, and I did not
find one.  See ATTACK.md.

What this file does implement, correctly, is a generic square-root solver:

  * n < 2**34 : baby-step giant-step, single process, O(sqrt n) time/memory.
  * otherwise : parallel Pollard rho (r-adding walk, distinguished points,
                many walks per worker with Montgomery batch inversion),
                O(sqrt n) group operations, O(1) memory per walk.

Both are exponential in log n, so the larger rungs of the ladder (roughly
64 bits and up on a typical 24-core machine) time out.
"""

import multiprocessing as mp
import os
import random


# ---------------------------------------------------------------- arithmetic

def _add(P, Q, a, p):
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


def _mul(k, P, a, p):
    R = None
    for bit in bin(k)[2:] if k > 0 else "":
        R = _add(R, R, a, p)
        if bit == "1":
            R = _add(R, P, a, p)
    return R


def _neg(P, p):
    return None if P is None else (P[0], -P[1] % p)


# ---------------------------------------------------------------- BSGS

def _bsgs(p, a, n, G, Q):
    m = int(n ** 0.5) + 1
    table = {}
    R = None
    for j in range(m):
        key = R
        if key not in table:
            table[key] = j
        R = _add(R, G, a, p)
    step = _neg(_mul(m, G, a, p), p)
    gamma = Q
    for i in range(m + 1):
        j = table.get(gamma)
        if j is not None:
            return (i * m + j) % n
        gamma = _add(gamma, step, a, p)
    raise ValueError("no solution found (Q not in <G>?)")


# ---------------------------------------------------------------- Pollard rho

R_TABLE = 64  # number of precomputed jump points
RESTARTS = 128  # per-worker restart offsets


def _rho_worker(args):
    """Run W parallel r-adding walks; push distinguished points to the queue."""
    (p, a, n, G, Q, jumps, dp_bits, W, seed, out_q, stop) = args
    rnd = random.Random(seed)
    mask = (1 << dp_bits) - 1
    rmask = R_TABLE - 1
    jx = [J[0] for J in jumps[0]]
    jy = [J[1] for J in jumps[0]]
    jc = jumps[1]
    jd = jumps[2]

    def fresh():
        while True:
            c = rnd.randrange(n)
            d = rnd.randrange(1, n)
            P = _add(_mul(c, G, a, p), _mul(d, Q, a, p), a, p)
            if P is not None:
                return P[0], P[1], c, d

    xs = [0] * W
    ys = [0] * W
    cs = [0] * W
    ds = [0] * W
    for i in range(W):
        xs[i], ys[i], cs[i], ds[i] = fresh()
    # after a distinguished point a walk is restarted cheaply by adding a
    # random point from this table (two merged walks then separate w.h.p.)
    restart = [fresh() for _ in range(RESTARTS)]

    def reseed(x, y, c, d):
        rx, ry, rc, rd = restart[rnd.randrange(RESTARTS)]
        P = _add((x, y), (rx, ry), a, p)
        if P is None:
            return fresh()
        return P[0], P[1], c + rc, d + rd

    pref = [0] * W
    it = 0
    batch = []
    while True:
        it += 1
        if (it & 255) == 0 and stop.is_set():
            return
        # Montgomery batch inversion of (x_jump - x_i)
        acc = 1
        for i in range(W):
            dx = (jx[xs[i] & rmask] - xs[i]) % p
            if dx == 0:  # hit a jump point or its negative: restart walk
                xs[i], ys[i], cs[i], ds[i] = fresh()
                dx = (jx[xs[i] & rmask] - xs[i]) % p
                if dx == 0:
                    dx = 1  # astronomically unlikely; walk result is discarded below anyway
            pref[i] = acc
            acc = acc * dx % p
        inv = pow(acc, -1, p)
        for i in range(W - 1, -1, -1):
            x = xs[i]
            y = ys[i]
            j = x & rmask
            xj = jx[j]
            dx = (xj - x) % p
            di = inv * pref[i] % p
            inv = inv * dx % p
            lam = (jy[j] - y) * di % p
            x3 = (lam * lam - x - xj) % p
            ys[i] = (lam * (x - x3) - y) % p
            xs[i] = x3
            cs[i] += jc[j]
            ds[i] += jd[j]
            if (x3 & mask) == 0:
                c = cs[i] % n
                d = ds[i] % n
                batch.append((x3, ys[i], c, d))
                xs[i], ys[i], cs[i], ds[i] = reseed(x3, ys[i], c, d)
        if len(batch) >= 64 or (batch and (it & 63) == 0):
            out_q.put(batch)
            batch = []


def _check(k, p, a, n, G, Q):
    return _mul(k % n, G, a, p) == Q


def _rho(p, a, n, G, Q):
    bits = n.bit_length()
    # leave one core for the collector process
    ncpu = max(1, (os.cpu_count() or 2) - 1)
    # expected total steps T ~ 1.25 sqrt(n); aim for ~2**14 distinguished
    # points in total (cheap for the collector) and keep the tail overhead
    # (walks * 2**dp_bits) below T/8
    logT = bits / 2 + 0.3
    dp_bits = max(0, int(logT) - 14)
    W = max(16, min(128, int(2 ** (logT - 3 - dp_bits)) // ncpu))

    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    jumps_P, jc, jd = [], [], []
    while len(jumps_P) < R_TABLE:
        c = rnd.randrange(n)
        d = rnd.randrange(n)
        P = _add(_mul(c, G, a, p), _mul(d, Q, a, p), a, p)
        if P is None:
            continue
        jumps_P.append(P)
        jc.append(c)
        jd.append(d)
    jumps = (jumps_P, jc, jd)

    ctx = mp.get_context("fork")
    out_q = ctx.Queue()
    stop = ctx.Event()
    procs = []
    for w in range(ncpu):
        seed = rnd.getrandbits(64)
        pr = ctx.Process(target=_rho_worker,
                         args=((p, a, n, G, Q, jumps, dp_bits, W, seed, out_q, stop),),
                         daemon=True)
        pr.start()
        procs.append(pr)

    seen = {}
    try:
        while True:
            batch = out_q.get()
            for (x, y, c, d) in batch:
                prev = seen.get(x)
                if prev is None:
                    seen[x] = (y, c, d)
                    continue
                y2, c2, d2 = prev
                # c + d k = +-(c2 + d2 k)
                if y == y2:
                    num, den = (c2 - c) % n, (d - d2) % n
                else:
                    num, den = (-(c + c2)) % n, (d + d2) % n
                if den == 0:
                    continue
                k = num * pow(den, -1, n) % n
                if _check(k, p, a, n, G, Q):
                    return k
    finally:
        stop.set()
        for pr in procs:
            pr.terminate()
        for pr in procs:
            pr.join(timeout=1)


# ---------------------------------------------------------------- entry point

def solve(p, a, b, n, G, Q) -> int:
    G = (G[0] % p, G[1] % p)
    Q = (Q[0] % p, Q[1] % p)
    if Q == G:
        return 1
    if Q == _neg(G, p):
        return n - 1
    if n < (1 << 34):
        return _bsgs(p, a, n, G, Q)
    return _rho(p, a, n, G, Q)
