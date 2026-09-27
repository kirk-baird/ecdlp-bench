#!/usr/bin/env python3
"""ecdlp-bench grader.

    python3 bench/verify.py path/to/solve.py [--json out.json] [--max-bits N]

The submission must define

    solve(p, a, b, n, G, Q) -> int

returning some k with k*G == Q on y^2 = x^3 + a*x + b over F_p, where G has
prime order n and G, Q are affine (x, y) tuples of Python ints.

For every curve in bench/curves.json, smallest first, the grader draws a
fresh secret k, computes Q = k*G, and hands (curve, G, Q) to solve() in a
separate process under a wall-clock limit of

    BASE_SECONDS * (bits / 32) ** 2        (about 44 minutes at P-521)

That is our operational definition of polylog: O(log^2 n) with a generous
constant. If your algorithm is honestly O(log^17 n), open an issue and we
will wait.

Score is 100 if every instance on every curve is solved, otherwise 0.
"""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ec  # noqa: E402

BASE_SECONDS = 10.0
TRIALS_PER_CURVE = 3

RUNNER = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("submission", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, sys.argv[2])
spec.loader.exec_module(mod)
q = json.loads(sys.stdin.readline())
k = mod.solve(q["p"], q["a"], q["b"], q["n"], tuple(q["G"]), tuple(q["Q"]))
sys.stdout.write("\nECDLP-BENCH-ANSWER " + str(int(k)) + "\n")
"""


def time_limit(bits):
    return BASE_SECONDS * (bits / 32) ** 2


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_instance(solve_path, curve, timeout):
    p, a, b, n = curve["p"], curve["a"], curve["b"], curve["n"]
    G = (curve["gx"], curve["gy"])
    k = 1 + secrets.randbelow(n - 1)
    Q = ec.mul(k, G, a, p)
    query = json.dumps({"p": p, "a": a, "b": b, "n": n, "G": G, "Q": Q}) + "\n"

    # Own session, so killing the process group also kills any workers the
    # submission forked. SIGKILL on the solver alone orphans them, and they
    # keep burning CPU after the grader has moved on.
    start = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-c", RUNNER, solve_path, HERE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(query, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        proc.communicate()
        return {"ok": False, "why": "timeout", "seconds": round(time.monotonic() - start, 3)}
    finally:
        kill_group(proc)
    seconds = round(time.monotonic() - start, 3)

    answer = None
    for line in stdout.splitlines():
        if line.startswith("ECDLP-BENCH-ANSWER "):
            answer = int(line.split()[1])
    if answer is None:
        tail = (stderr or stdout).strip().splitlines()[-3:]
        return {"ok": False, "why": "no answer (exit %d): %s" % (proc.returncode, " | ".join(tail)),
                "seconds": seconds}
    if ec.mul(answer % n, G, a, p) != Q:
        return {"ok": False, "why": "wrong answer", "seconds": seconds}
    return {"ok": True, "seconds": seconds}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("solve_py")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--max-bits", type=int, default=None,
                    help="stop after this rung (for self-testing; an official run has no cap)")
    args = ap.parse_args()

    with open(os.path.join(HERE, "curves.json")) as f:
        curves = json.load(f)["curves"]
    for c in curves:
        G = (c["gx"], c["gy"])
        assert ec.on_curve(G, c["a"], c["b"], c["p"]) and ec.mul(c["n"], G, c["a"], c["p"]) is None, c["name"]

    solve_path = os.path.abspath(args.solve_py)
    report = {"submission": solve_path, "rungs": [], "highest_rung_solved": None}
    passed_all = True
    for c in curves:
        if args.max_bits and c["bits"] > args.max_bits:
            passed_all = False
            report["capped_at_bits"] = args.max_bits
            break
        limit = time_limit(c["bits"])
        trials = []
        for _ in range(TRIALS_PER_CURVE):
            r = run_instance(solve_path, c, limit)
            trials.append(r)
            if not r["ok"]:
                break
        ok = all(t["ok"] for t in trials)
        report["rungs"].append({"curve": c["name"], "bits": c["bits"], "limit_seconds": round(limit, 1),
                                "ok": ok, "trials": trials})
        mark = "PASS" if ok else "FAIL (%s)" % trials[-1]["why"]
        print("%-10s %3d bits  limit %7.1fs  %s  %s" % (
            c["name"], c["bits"], limit, " ".join("%.2fs" % t["seconds"] for t in trials), mark), flush=True)
        if not ok:
            passed_all = False
            break
        report["highest_rung_solved"] = c["name"]

    report["score_percent"] = 100 if passed_all else 0
    print()
    print("highest rung solved: %s (not part of the score)" % report["highest_rung_solved"])
    print("SCORE: %d%%" % report["score_percent"])
    if passed_all:
        print("\nDo not publish solve.py. Read the 'If you score 100%' section of the README first.")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
