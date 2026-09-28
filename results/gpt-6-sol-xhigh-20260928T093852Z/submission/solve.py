"""Generic ECDLP baseline.  It is not a polylogarithmic-time algorithm.

Small groups use baby-step giant-step.  Larger groups use parallel Pollard rho
with distinguished points.  Both have square-root group-operation complexity.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import secrets

from ec import add, mul, neg


def _bsgs(p, a, n, G, Q):
    m = math.isqrt(n) + 1
    babies = {}
    P = G
    for j in range(1, m):
        # x determines a point up to sign; the parity bit selects the sign.
        babies[P[0]] = (j << 1) | (P[1] & 1)
        P = add(P, G, a, p)

    stride = neg(mul(m, G, a, p), p)
    P = Q
    for i in range((n + m - 1) // m + 1):
        if P is None:
            return (i * m) % n
        encoded = babies.get(P[0])
        if encoded is not None:
            j = encoded >> 1
            if (P[1] & 1) == (encoded & 1):
                return (i * m + j) % n
            return (i * m - j) % n
        P = add(P, stride, a, p)
    raise RuntimeError("baby-step giant-step search failed")


def _walk(p, a, n, G, Q, steps, dp_mask, out, stop):
    count = len(steps) - 1
    r = secrets.SystemRandom()
    start_a = r.randrange(n)
    start_b = r.randrange(n)
    P = add(mul(start_a, G, a, p), mul(start_b, Q, a, p), a, p)
    aa, bb = start_a, start_b
    while not stop.is_set():
        for _ in range(1024):
            if P is None:
                # P=0 supplies a direct relation unless the Q coefficient is 0.
                if bb:
                    out.put((None, None, aa, bb))
                aa, bb = r.randrange(n), r.randrange(n)
                P = add(mul(aa, G, a, p), mul(bb, Q, a, p), a, p)
                continue
            x, y = P
            if (x & dp_mask) == 0:
                out.put((x, y, aa, bb))
            da, db, S = steps[(x >> 16) & count]
            P = add(P, S, a, p)
            aa = (aa + da) % n
            bb = (bb + db) % n


def _rho(p, a, n, G, Q):
    workers = min(24, os.cpu_count() or 1)
    # The same random transition function must be shared by all workers.
    r = secrets.SystemRandom()
    steps = []
    for _ in range(32):
        da, db = r.randrange(n), r.randrange(n)
        S = add(mul(da, G, a, p), mul(db, Q, a, p), a, p)
        steps.append((da, db, S))
    # Keep the expected tail after a collision short, while limiting IPC.
    dp_bits = max(8, min(13, (n.bit_length() - 20) // 2))
    dp_mask = (1 << dp_bits) - 1

    ctx = mp.get_context("fork")
    out = ctx.Queue()
    stop = ctx.Event()
    processes = [
        ctx.Process(target=_walk, args=(p, a, n, G, Q, steps, dp_mask, out, stop))
        for _ in range(workers)
    ]
    for proc in processes:
        proc.start()

    seen = {}
    try:
        while True:
            x, y, aa, bb = out.get()
            if x is None:
                k = -aa * pow(bb, -1, n) % n
            else:
                key = (x, y)
                old = seen.setdefault(key, (aa, bb))
                if old == (aa, bb):
                    continue
                denominator = (bb - old[1]) % n
                if not denominator:
                    continue
                k = (old[0] - aa) * pow(denominator, -1, n) % n
            if mul(k, G, a, p) == Q:
                return k
    finally:
        stop.set()
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.join(timeout=0.2)


def solve(p, a, b, n, G, Q) -> int:
    if Q == G:
        return 1
    if Q is None:
        return 0
    if n.bit_length() <= 40:
        return _bsgs(p, a, n, G, Q)
    return _rho(p, a, n, G, Q)
