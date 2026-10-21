#!/usr/bin/env bash
# The GitHub Action's one step. Inputs arrive as environment variables, never
# pasted into the script, so no input can run as shell code.
#
# unripe runs straight from the action's own checkout, so the code that runs is
# exactly the commit the workflow pinned, with nothing fetched from PyPI.
set -euo pipefail

args=(check)

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

if [ -n "$(trim "${INPUT_MIN_AGE:-}")" ]; then
  args+=(--min-age "$(trim "$INPUT_MIN_AGE")")
fi
if [ -n "$(trim "${INPUT_UNKNOWN:-}")" ]; then
  args+=(--unknown "$(trim "$INPUT_UNKNOWN")")
fi
if [ -n "$(trim "${INPUT_CONFIG:-}")" ]; then
  args+=(--config "$(trim "$INPUT_CONFIG")")
fi

since="$(trim "${INPUT_SINCE:-}")"
if [ -z "$since" ]; then
  since="${PR_BASE:-}"
fi
if [ -n "$since" ] && [ "$since" != "none" ]; then
  # actions/checkout fetches one commit by default; fetch the base too when it is missing
  if ! git rev-parse --verify --quiet "${since}^{commit}" > /dev/null; then
    echo "unripe: fetching $since to compare against"
    git fetch --no-tags --depth=1 origin "$since" || echo "unripe: could not fetch $since; use actions/checkout with fetch-depth: 0"
  fi
  args+=(--since "$since")
fi

while IFS= read -r pattern; do
  pattern="$(trim "$pattern")"
  if [ -n "$pattern" ]; then
    args+=(--exclude "$pattern")
  fi
done <<< "${INPUT_EXCLUDE:-}"

if [ "$(trim "${INPUT_SUMMARY:-true}")" = "true" ] && [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  args+=(--summary "$GITHUB_STEP_SUMMARY")
fi
if [ -n "$(trim "${INPUT_SARIF:-}")" ]; then
  args+=(--sarif "$(trim "$INPUT_SARIF")")
fi

read -r -a paths <<< "$(printf '%s' "${INPUT_PATHS:-}" | tr '\n' ' ')" || true
if [ "${#paths[@]}" -gt 0 ]; then
  args+=(--)
  args+=("${paths[@]}")
fi

export PYTHONPATH="$GITHUB_ACTION_PATH/src"
export UNRIPE_CACHE="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/unripe-cache"
exec uv run --no-project --python "$(trim "${INPUT_PYTHON:-3.13}")" python -m unripe "${args[@]}"
