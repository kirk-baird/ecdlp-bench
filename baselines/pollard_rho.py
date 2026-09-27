"""Baseline: Pollard rho with an r-adding walk and distinguished points.

O(sqrt(n)) group operations, which is optimal for generic algorithms (Shoup 1997)
and exponential in log n, which is the problem.
"""

import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench"))
import ec  # noqa: E402

R = 32


def solve(p, a, b, n, G, Q):
    if Q is None:
        return 0
    dist_mask = (1 << max(0, n.bit_length() // 4 - 2)) - 1
    while True:
        steps = []
        for _ in range(R):
            u, v = secrets.randbelow(n), secrets.randbelow(n)
            steps.append((u, v, ec.add(ec.mul(u, G, a, p), ec.mul(v, Q, a, p), a, p)))
        seen = {}
        u, v = secrets.randbelow(n), secrets.randbelow(n)
        X = ec.add(ec.mul(u, G, a, p), ec.mul(v, Q, a, p), a, p)
        for _ in range(8 * int(n ** 0.5) + 1000):
            if X is None:
                break
            if X[0] & dist_mask == 0:
                if X in seen:
                    u2, v2 = seen[X]
                    if (v - v2) % n:
                        return (u2 - u) * pow(v - v2, -1, n) % n
                    break
                seen[X] = (u, v)
                u, v = secrets.randbelow(n), secrets.randbelow(n)
                X = ec.add(ec.mul(u, G, a, p), ec.mul(v, Q, a, p), a, p)
                continue
            du, dv, S = steps[X[0] % R]
            u, v, X = (u + du) % n, (v + dv) % n, ec.add(X, S, a, p)
