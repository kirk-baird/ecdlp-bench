"""ECDLP solver for ecdlp-bench.

This is NOT a polylogarithmic algorithm.  No classical polylog algorithm is
known for prime-order curves over prime fields, and I did not find one (see
ATTACK.md).  What is implemented here is the best generic method:

  * n < 2**10  : exhaustive search
  * n < 2**34  : baby-step giant-step
  * otherwise  : Pollard rho (r-adding walk, distinguished points), run in
                 parallel on every available core via fork()

All of these need Theta(sqrt(n)) group operations, so only the smallest rungs
of the ladder can finish inside the grader's time limits.
"""

import math
import os
import queue
import random
import secrets

R_STEPS = 32              # jumps in the r-adding walk
J_BITS = 5                # log2(R_STEPS); low bits of x pick the jump
J_MASK = R_STEPS - 1
BRUTE_MAX_N = 1 << 10
BSGS_MAX_N = 1 << 34


# ---------------------------------------------------------------------------
# Affine short-Weierstrass arithmetic (same formulas as bench/ec.py).
# ---------------------------------------------------------------------------

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


def _neg(P, p):
    return None if P is None else (P[0], -P[1] % p)


def _mul(k, P, a, p):
    R = None
    for bit in bin(k)[2:] if k > 0 else "":
        R = _add(R, R, a, p)
        if bit == "1":
            R = _add(R, P, a, p)
    return R


# ---------------------------------------------------------------------------
# Small groups.
# ---------------------------------------------------------------------------

def _brute(p, a, n, G, Q):
    R = None
    for k in range(n):
        if R == Q:
            return k
        R = _add(R, G, a, p)
    raise ValueError("Q is not in <G>")


def _bsgs(p, a, n, G, Q):
    m = math.isqrt(n) + 1
    baby = {}
    R = G
    for j in range(1, m + 1):
        # R == j*G, never infinity because j <= m < n.
        baby.setdefault(R[0], j)
        R = _add(R, G, a, p)
    stride = _neg(_mul(m, G, a, p), p)
    S = Q
    for i in range(m + 1):
        # S == Q - i*m*G
        if S is None:
            return i * m % n
        j = baby.get(S[0])
        if j is not None:
            # Equal x-coordinates means S == +-j*G.
            for k in ((i * m + j) % n, (i * m - j) % n):
                if _mul(k, G, a, p) == Q:
                    return k
        S = _add(S, stride, a, p)
    raise ValueError("Q is not in <G>")


# ---------------------------------------------------------------------------
# Pollard rho with distinguished points.
# ---------------------------------------------------------------------------

def _make_steps(p, a, n, G, Q, rng):
    """R_STEPS random jumps M_j = c_j*G + d_j*Q, as (x, y, c, d)."""
    steps = []
    while len(steps) < R_STEPS:
        c = rng.randrange(1, n)
        d = rng.randrange(1, n)
        M = _add(_mul(c, G, a, p), _mul(d, Q, a, p), a, p)
        if M is not None:
            steps.append((M[0], M[1], c, d))
    return steps


def _walk(p, a, n, G, Q, steps, dp_bits, seed, emit):
    """Run trails from random starts until emit(x, y, c, d) returns True.

    Each trail walks X -> X + M_{x mod R_STEPS} while tracking X = c*G + d*Q
    and ends at the first distinguished point (dp_bits bits of x above the
    jump index are zero), which is passed to emit.
    """
    rng = random.Random(seed)
    sx = [s[0] for s in steps]
    sy = [s[1] for s in steps]
    sc = [s[2] for s in steps]
    sd = [s[3] for s in steps]
    dp_mask = ((1 << dp_bits) - 1) << J_BITS
    max_len = 32 << dp_bits   # give up on a trail stuck in a DP-free cycle
    while True:
        c = rng.randrange(n)
        d = rng.randrange(1, n)
        P = _add(_mul(c, G, a, p), _mul(d, Q, a, p), a, p)
        if P is None:
            continue
        x, y = P
        for _ in range(max_len):
            if not x & dp_mask:
                if emit(x, y, c % n, d % n):
                    return
                break
            j = x & J_MASK
            mx = sx[j]
            if x == mx:
                # Doubling or P == -M_j: rare, use the general formula.
                P = _add((x, y), (mx, sy[j]), a, p)
                if P is None:
                    break
                x, y = P
            else:
                lam = (sy[j] - y) * pow(mx - x, -1, p) % p
                x3 = (lam * lam - x - mx) % p
                y = (lam * (x - x3) - y) % p
                x = x3
            c += sc[j]
            d += sd[j]


class _Collider:
    """Stores distinguished points and turns a collision into k."""

    def __init__(self, p, a, n, G, Q):
        self.p, self.a, self.n, self.G, self.Q = p, a, n, G, Q
        self.table = {}

    def add(self, x, y, c, d):
        n = self.n
        old = self.table.get(x)
        if old is None:
            self.table[x] = (y, c, d)
            return None
        y2, c2, d2 = old
        if y == y2:
            # c*G + d*Q == c2*G + d2*Q  =>  c - c2 == k*(d2 - d)
            num, den = c - c2, d2 - d
        else:
            # c*G + d*Q == -(c2*G + d2*Q)  =>  c + c2 == -k*(d + d2)
            num, den = c + c2, -(d + d2)
        den %= n
        if den == 0:
            return None
        try:
            k = num * pow(den, -1, n) % n
        except ValueError:
            return None
        if _mul(k, self.G, self.a, self.p) == self.Q:
            return k
        return None


def _die_with_parent():
    """Ask Linux to SIGKILL this process when its parent dies (best effort)."""
    try:
        import ctypes
        import signal
        libc = ctypes.CDLL(None)
        libc.prctl(1, int(signal.SIGKILL), 0, 0, 0)   # PR_SET_PDEATHSIG
    except Exception:
        pass


def _worker(p, a, n, G, Q, steps, dp_bits, seed, out, parent_pid):
    _die_with_parent()

    def emit(x, y, c, d):
        if os.getppid() != parent_pid:
            os._exit(0)
        out.put((x, y, c, d))
        return False

    if os.getppid() != parent_pid:
        os._exit(0)
    _walk(p, a, n, G, Q, steps, dp_bits, seed, emit)


def _cpu_count():
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _rho_parallel(p, a, n, G, Q, steps, dp_bits, workers):
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    out = ctx.Queue()
    procs = []
    try:
        for _ in range(workers):
            proc = ctx.Process(target=_worker,
                               args=(p, a, n, G, Q, steps, dp_bits,
                                     secrets.randbits(128), out, os.getpid()))
            proc.daemon = True
            proc.start()
            procs.append(proc)
        coll = _Collider(p, a, n, G, Q)
        while True:
            try:
                x, y, c, d = out.get(timeout=1.0)
            except queue.Empty:
                if not any(proc.is_alive() for proc in procs):
                    raise RuntimeError("all rho workers died")
                continue
            k = coll.add(x, y, c, d)
            if k is not None:
                return k
    finally:
        # Workers inherit the grader's stdout pipe, so they must be gone
        # before we return.
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.join(1.0)
            if proc.is_alive():
                proc.kill()
                proc.join(1.0)


def _rho_serial(p, a, n, G, Q, steps, dp_bits):
    coll = _Collider(p, a, n, G, Q)
    found = []

    def emit(x, y, c, d):
        k = coll.add(x, y, c, d)
        if k is None:
            return False
        found.append(k)
        return True

    _walk(p, a, n, G, Q, steps, dp_bits, secrets.randbits(128), emit)
    return found[0]


def _rho(p, a, n, G, Q):
    rng = random.Random(secrets.randbits(128))
    steps = _make_steps(p, a, n, G, Q, rng)
    # Trails of ~2**dp_bits steps: long enough that the two scalar
    # multiplications per trail start are negligible, short enough that the
    # post-collision delay (about workers * 2**dp_bits steps) is too.
    dp_bits = min(20, n.bit_length() // 4 + 2)
    workers = _cpu_count()
    if workers > 1:
        try:
            return _rho_parallel(p, a, n, G, Q, steps, dp_bits, workers)
        except Exception:
            pass
    return _rho_serial(p, a, n, G, Q, steps, dp_bits)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def solve(p, a, b, n, G, Q) -> int:
    if Q is None:
        return 0
    G = (G[0] % p, G[1] % p)
    Q = (Q[0] % p, Q[1] % p)
    a %= p
    if n < BRUTE_MAX_N:
        return _brute(p, a, n, G, Q)
    if n < BSGS_MAX_N:
        return _bsgs(p, a, n, G, Q)
    return _rho(p, a, n, G, Q)


if __name__ == "__main__":
    # Self-test on the ladder curves: python3 submission/solve.py [max_bits]
    import json
    import sys
    import time

    max_bits = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "bench", "curves.json")) as f:
        curves = json.load(f)["curves"]
    for cv in curves:
        if cv["bits"] > max_bits:
            break
        p, a, b, n = cv["p"], cv["a"], cv["b"], cv["n"]
        G = (cv["gx"], cv["gy"])
        k = random.randrange(1, n)
        Q = _mul(k, G, a, p)
        t0 = time.monotonic()
        got = solve(p, a, b, n, G, Q)
        dt = time.monotonic() - t0
        ok = _mul(got % n, G, a, p) == Q
        print("%-10s %3d bits  %8.2fs  %s" % (cv["name"], cv["bits"], dt,
                                              "ok" if ok else "WRONG"), flush=True)
