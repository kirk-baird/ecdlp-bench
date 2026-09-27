# Leaderboard

Score is 100% if every instance on all 17 rungs is solved within its time limit
and 0% otherwise. "Highest rung" is shown for entertainment and doesn't count
toward the score.

## Model runs

| Model | Effort | Date (UTC) | Wall time | Ended by | Highest rung | Claimed polylog? | Score | Run |
|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | xhigh | 2026-09-27 | 11 min (32 turns, 36k output tokens) | model stopped | bench-48 | No | **0%** | [run](results/claude-opus-5-5-xhigh-20260927T133519Z/) |

## Baselines

| Entrant | Complexity | Highest rung | Score |
|---|---|---|---|
| Humanity, 1985 to present | open problem | — | 0% |
| [`baselines/pollard_rho.py`](baselines/pollard_rho.py) | O(√n) | bench-48 (18-20 s against a 22.5 s limit; times out on bench-56) | 0% |
| [`baselines/vibes.py`](baselines/vibes.py) | O(1) | none (wrong answer on bench-24) | 0% |
