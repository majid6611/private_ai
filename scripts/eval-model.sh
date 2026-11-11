#!/bin/sh
# Evaluate the configured local model on exported retrieval cases (see orchestrator/evaluate.py).
# Usage: sh scripts/eval-model.sh [model-id]   (needs: make up-ai, models/work/cases.json)
set -eu
cd "$(dirname "$0")/.."
model="${1:-qwen2.5:1.5b}"
dc="docker compose -f compose.yaml -f compose.ai.yaml --profile ai"
$dc build orchestrator >/dev/null
$dc run --rm --no-deps -v "$PWD/models/work:/work" -e MODEL_ID="$model" -e UNEVALUATED_TEST_MODEL=1 \
    -e EVAL_LIMIT="${EVAL_LIMIT:-0}" orchestrator python evaluate.py /work/cases.json "/work/results-$(echo "$model" | tr ':/' '__').json" "/work/evaluation-$(echo "$model" | tr ':/' '__').json"
