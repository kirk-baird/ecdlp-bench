# ECDLP attempt

I did **not** find a polylogarithmic-time algorithm for the prime-order,
prime-field curves in this benchmark. `solve.py` is an honest generic baseline:
baby-step giant-step (BSGS) for groups of at most 40 bits, and a parallel
Pollard-rho walk with distinguished points above that. It is not expected to
pass the full ladder or earn a nonzero official score.

## Algorithm and correctness

For a small group, let `m = floor(sqrt(n)) + 1`. Store `jG` for
`1 <= j < m`, indexed by its x coordinate and retaining the parity of y.
Then examine `Q - imG` for successive `i`. Every discrete log has the form
`k = im + j (mod n)` with `0 <= j < m`, so one of those giant steps matches a
baby step. Points with the same x coordinate are equal or negatives; the
stored y parity resolves the sign, yielding `im + j` or `im - j`. A point at
infinity yields `im`. The grader's final multiplication independently checks
the returned value.

For larger groups, each rho worker maintains the invariant
`P = alpha*G + beta*Q`. All workers use the same 32-way deterministic
partition of the x coordinate and the same precomputed increments
`delta_alpha*G + delta_beta*Q`. A worker reports points whose x coordinate has
a fixed number of zero low bits. When two reports have the same affine point,

    alpha_1 + beta_1*k = alpha_2 + beta_2*k (mod n),

so, if `beta_2 - beta_1` is nonzero modulo prime `n`, then

    k = (alpha_1 - alpha_2) / (beta_2 - beta_1) (mod n).

The solver verifies a candidate by multiplying `G` before returning it. A
collision with zero denominator gives no information and the search continues.
As with usual Pollard rho, completion time is random and there is no strict
per-instance time guarantee.

## Complexity and observed limit

BSGS takes `Theta(sqrt(n))` group operations and `Theta(sqrt(n))` stored
entries. Under the random-walk heuristic, rho takes `Theta(sqrt(n))` total
group operations, plus an expected `O(2^d)` walk tail after a collision when
the distinguished-point density is `2^-d`. With `W` workers, ideal wall time
is approximately `Theta(sqrt(n)/W)` group operations. The distinguished-point
table has expected size `O(sqrt(n)/2^d)`; this implementation caps `d` at 13.
Field arithmetic adds polylogarithmic factors to the bit complexity, but the
square-root dependence on `n` remains. For 521-bit `n`, it is utterly beyond
the benchmark's time limit.

The local verifier passed three fresh instances on every rung through 48 bits.
The 56-bit rung timed out at its 30.6-second limit in the sampled run. Rho
timings vary with the random walk, so even the 48-bit result is not a
guarantee. The official all-or-nothing score is therefore expected to be 0%.

## Coordinate-based avenues considered

The group orders are prime, so Pohlig-Hellman has no smaller factors to use.
The supplied trace is not 1, excluding the anomalous-curve p-adic logarithm
attack, and the embedding degrees are above 100, excluding a small-degree
MOV/Frey-Rueck transfer. A special endomorphism on one standard curve could
improve a rho constant, not turn its square-root cost into polylogarithmic
cost. Summation-polynomial relations do use the field coordinates, but no
polylogarithmic algorithm for these prime-field curves follows from them;
finding and solving enough relations remains the obstacle. I found no new
coordinate relation that overcomes it.
