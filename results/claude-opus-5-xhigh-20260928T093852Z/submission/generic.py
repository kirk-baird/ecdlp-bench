"""Generic-group ECDLP: BSGS and a parallel Pollard rho with distinguished points.

This is the fallback for curves on which no structural attack applies.  It is
Theta(sqrt(n)) -- exponential in log n -- and is here only so that the solver returns a
correct answer whenever it is given enough time.  It is NOT the polylog algorithm the task
asks for; see ATTACK.md.

Implementation notes:
  * van Oorschot-Wiener parallel rho: every worker walks the SAME deterministic iteration
    function (the branch points are derived from a seed broadcast by the parent), so
    collisions between different workers are useful.  Speedup is linear in the number of
    cores.
  * Distinguished points (low d bits of x zero) keep the parent's memory at O(sqrt(n)/2^d).
  * Walks are restarted after 20*2^d fruitless steps to escape short cycles.
"""

import os
import multiprocessing as mp
import random


# --------------------------------------------------------------------------- arithmetic

def add(P, R, a, p):
    if P is None:
        return R
    if R is None:
        return P
    x1, y1 = P
    x2, y2 = R
    if x1 == x2:
        if (y1 + y2) % p == 0:
            return None
        lam = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, p) % p
    x3 = (lam * lam - x1 - x2) % p
    return x3, (lam * (x1 - x3) - y1) % p


def mul(k, P, a, p):
    if k < 0:
        k, P = -k, (None if P is None else (P[0], (-P[1]) % p))
    R = None
    for bit in bin(k)[2:] if k > 0 else "":
        R = add(R, R, a, p)
        if bit == "1":
            R = add(R, P, a, p)
    return R


# --------------------------------------------------------------------------- BSGS

def bsgs(p, a, n, G, Q, m=None):
    """Baby-step giant-step.  O(sqrt(n)) time and memory; used only for tiny n."""
    if Q is None:
        return 0
    if m is None:
        m = 1
        while m * m < n:
            m <<= 1
    table = {}
    R = None
    for j in range(m):
        key = R[0] if R is not None else -1
        table.setdefault(key, []).append(j)
        R = add(R, G, a, p)
    # R = m*G ; giant steps Q - i*(m*G)
    S = (R[0], (-R[1]) % p) if R is not None else None   # -(m*G)
    T = Q
    for i in range(m + 1):
        key = T[0] if T is not None else -1
        if key in table:
            for j in table[key]:
                k = (i * m + j) % n
                if mul(k, G, a, p) == Q:
                    return k
                k = (i * m - j) % n
                if mul(k, G, a, p) == Q:
                    return k
        T = add(T, S, a, p)
    return None


# --------------------------------------------------------------------------- rho worker

NBRANCH = 32


def _make_branches(p, a, n, G, Q, seed):
    rnd = random.Random(seed)
    out = []
    for _ in range(NBRANCH):
        while True:
            u = rnd.randrange(1, n)
            v = rnd.randrange(1, n)
            S = add(mul(u, G, a, p), mul(v, Q, a, p), a, p)
            if S is not None:
                out.append((u, v, S[0], S[1]))
                break
    return out


POOL = 64


def _die_with_parent():
    """prctl(PR_SET_PDEATHSIG, SIGKILL) -- never outlive the solver process."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass


def _rho_worker(p, a, n, G, Q, seed, dbits, out_q, wid):
    _die_with_parent()
    branches = _make_branches(p, a, n, G, Q, seed)
    mask = (1 << dbits) - 1
    maxsteps = 20 << dbits
    rnd = random.Random((seed << 20) ^ (wid * 0x9E3779B1) ^ os.getpid())

    # Restarting a walk from scratch costs 2*log2(n) group operations, which at small
    # dbits swamps the walk itself.  Instead keep a pool of points with known
    # decomposition and advance a running accumulator by one pool element per restart:
    # one group operation, and -- crucially -- a fresh starting point every time.  (An
    # earlier version drew starts from a fixed finite set of pool sums; each worker then
    # ran out of distinct walks and spent the rest of its life recomputing them.)
    pool = []
    while len(pool) < POOL:
        u = rnd.randrange(1, n)
        v = rnd.randrange(1, n)
        P = add(mul(u, G, a, p), mul(v, Q, a, p), a, p)
        if P is not None:
            pool.append((u, v, P))

    au, av, A = pool[0]
    nb = NBRANCH - 1
    batch = []
    put = out_q.put
    while True:
        du, dv, D = pool[rnd.randrange(POOL)]
        W = add(A, D, a, p)
        if W is None:                        # accumulator hit infinity; re-seed it
            au, av, A = pool[rnd.randrange(POOL)]
            continue
        au = (au + du) % n
        av = (av + dv) % n
        A = W
        u, v = au, av
        x, y = W
        for _ in range(maxsteps):
            bu, bv, sx, sy = branches[x & nb]
            dx = sx - x
            if dx == 0:
                break                        # degenerate: abandon this walk
            lam = (sy - y) * pow(dx, -1, p) % p
            nx = (lam * lam - x - sx) % p
            y = (lam * (x - nx) - y) % p
            x = nx
            u += bu
            v += bv
            if not (x & mask):
                batch.append((x, y, u % n, v % n))
                if len(batch) >= 32:
                    put(batch)
                    batch = []
                break


def pollard_rho_parallel(p, a, n, G, Q, workers=None, dbits=None):
    """van Oorschot-Wiener parallel rho.  Returns k, or raises on failure."""
    nbits = n.bit_length()
    if workers is None:
        workers = max(1, (os.cpu_count() or 1))
    if dbits is None:
        # long enough that the restart cost is amortised, short enough that the parent
        # stores at most ~2^20 distinguished points
        dbits = max(10, nbits // 2 - 20)
    seed = random.randrange(1 << 62)
    ctx = mp.get_context("fork") if hasattr(mp, "get_context") else mp
    q = ctx.Queue(maxsize=4096)
    procs = []
    for wid in range(workers):
        pr = ctx.Process(target=_rho_worker, args=(p, a, n, G, Q, seed, dbits, q, wid),
                         daemon=True)
        pr.start()
        procs.append(pr)
    seen = {}
    try:
        while True:
            for (x, y, u, v) in q.get():
                prev = seen.get((x, y))
                if prev is None:
                    seen[(x, y)] = (u, v)
                    continue
                u0, v0 = prev
                dv = (v0 - v) % n
                if dv == 0:
                    continue
                k = (u - u0) * pow(dv, -1, n) % n
                if mul(k, G, a, p) == Q:
                    return k
    finally:
        for pr in procs:
            try:
                pr.terminate()
            except Exception:
                pass
