#!/usr/bin/env bash
# One official ecdlp-bench run.
#
#   ./run.sh <model> <effort> [label]
#   ./run.sh claude-opus-5-5 xhigh
#
# Starts one headless Claude Code session with PROMPT.md and nothing else, in a
# fresh workspace outside this repo. Bash runs in Claude Code's sandbox (writes
# confined to the workspace, no network); file edits are auto-accepted; anything
# that would need a human to approve it is denied, since nobody is watching.
# The run ends when the usage window runs out, 5 hours pass, or the model stops.
# Then the pristine grader in this repo scores workspace/submission/solve.py.
#
# Env:
#   ECDLP_BENCH_WORKDIR   where workspaces go   (default ~/ecdlp-bench-runs)
#   ECDLP_BENCH_MEMORY    systemd MemoryMax for the session (default 24G)
#   ECDLP_BENCH_WALL      wall-clock cap                   (default 5h)
set -euo pipefail

MODEL=${1:?usage: ./run.sh <model> <effort> [label]}
EFFORT=${2:?usage: ./run.sh <model> <effort> [label]}
REPO=$(cd "$(dirname "$0")" && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LABEL=${3:-$MODEL-$EFFORT}-$STAMP
WORK=${ECDLP_BENCH_WORKDIR:-$HOME/ecdlp-bench-runs}/$LABEL
OUT=$REPO/results/$LABEL
WALL=${ECDLP_BENCH_WALL:-5h}

grader_hash() { cat "$REPO/bench/verify.py" "$REPO/bench/ec.py" "$REPO/bench/curves.json" | sha256sum | cut -d' ' -f1; }
HASH_BEFORE=$(grader_hash)

mkdir -p "$WORK/submission" "$WORK/.sage" "$OUT"
cp -r "$REPO/bench" "$WORK/bench"
cp "$REPO/PROMPT.md" "$WORK/PROMPT.md"
cd "$WORK"
git init -q && git add -A && git -c user.name=ecdlp-bench -c user.email=bench@localhost commit -qm "initial workspace"

SETTINGS=$(python3 - "$WORK" <<'PY'
import json, sys
work = sys.argv[1]
print(json.dumps({
    "sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {"allowWrite": [work, "/tmp"]},
        "network": {"deniedDomains": ["*"]},
    },
    "permissions": {"deny": ["WebFetch", "WebSearch"]},
}))
PY
)

echo "workspace: $WORK"
echo "results:   $OUT"
echo "model:     $MODEL ($EFFORT)"
START=$(date -u +%s)

LAUNCH=()
if command -v systemd-run >/dev/null; then
  LAUNCH=(systemd-run --user --scope --quiet -p MemoryMax="${ECDLP_BENCH_MEMORY:-24G}")
fi

set +e
DOT_SAGE="$WORK/.sage" "${LAUNCH[@]}" timeout "$WALL" claude -p "$(cat PROMPT.md)" \
  --model "$MODEL" --effort "$EFFORT" \
  --permission-mode acceptEdits --permission-prompts none \
  --allowedTools Bash --disallowedTools WebFetch WebSearch \
  --settings "$SETTINGS" \
  --output-format stream-json --verbose \
  > "$OUT/transcript.jsonl" 2> "$OUT/claude.stderr"
EXIT=$?
set -e
END=$(date -u +%s)

HASH_AFTER=$(grader_hash)
if [[ "$HASH_BEFORE" != "$HASH_AFTER" ]]; then
  echo "grader in $REPO/bench changed during the run; refusing to score" | tee "$OUT/TAMPERED"
  exit 1
fi

cp -r "$WORK/submission" "$OUT/submission" 2>/dev/null || true
if [[ -f "$WORK/submission/solve.py" ]]; then
  python3 "$REPO/bench/verify.py" "$WORK/submission/solve.py" --json "$OUT/verify.json" | tee "$OUT/verify.txt"
else
  echo "no submission/solve.py" | tee "$OUT/verify.txt"
  echo '{"score_percent": 0, "highest_rung_solved": null, "rungs": []}' > "$OUT/verify.json"
fi

python3 - "$OUT" "$MODEL" "$EFFORT" "$START" "$END" "$EXIT" "$(claude --version)" <<'PY'
import json, sys
out, model, effort, start, end, code, version = sys.argv[1:]
final = None
with open(f"{out}/transcript.jsonl") as f:
    for line in f:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            final = ev
v = json.load(open(f"{out}/verify.json"))
summary = {
    "model": model, "effort": effort, "claude_code": version,
    "started_utc": int(start), "wall_seconds": int(end) - int(start), "exit_code": int(code),
    "ended_by": "wall clock" if int(code) == 124 else (final or {}).get("subtype", "unknown"),
    "num_turns": (final or {}).get("num_turns"),
    "usage": (final or {}).get("usage"),
    "total_cost_usd_equivalent": (final or {}).get("total_cost_usd"),
    "highest_rung_solved": v.get("highest_rung_solved"),
    "score_percent": v["score_percent"],
}
json.dump(summary, open(f"{out}/summary.json", "w"), indent=2)
print(json.dumps(summary, indent=2))
PY

if grep -q '"score_percent": 100' "$OUT/verify.json"; then
  rm -rf "$OUT/submission"
  echo
  echo "Score 100%. The submission was removed from results/ so it can't be committed by accident."
  echo "It is still in $WORK. Read the README before doing anything else."
fi
