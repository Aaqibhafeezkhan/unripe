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

# the markdown report goes to a file first: it feeds the job summary and the pull request comment
report="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/unripe-report.md"
rm -f "$report"
args+=(--summary "$report")
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
status=0
uv run --no-project --python "$(trim "${INPUT_PYTHON:-3.13}")" python -m unripe "${args[@]}" || status=$?

if [ -f "$report" ] && [ "$(trim "${INPUT_SUMMARY:-true}")" = "true" ] && [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$report" >> "$GITHUB_STEP_SUMMARY"
fi

# one comment per pull request, edited in place on later runs. it is only
# created when there is something to report, and updated when it clears up.
# commenting is a courtesy: if it fails, the check's result stands as it is
if [ "$(trim "${INPUT_COMMENT:-false}")" = "true" ] && [ -n "${PR_NUMBER:-}" ] && [ -f "$report" ]; then
  marker="<!-- unripe -->"
  body="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/unripe-comment.md"
  { echo "$marker"; cat "$report"; } > "$body"
  comments="repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments"
  existing="$(gh api "$comments" --paginate --jq ".[] | select(.body | startswith(\"$marker\")) | .id" 2> /dev/null | head -n 1 || true)"
  case "$existing" in
    "" | *[!0-9]*) existing="" ;;
  esac
  if [ -n "$existing" ]; then
    gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" -F "body=@$body" > /dev/null || echo "::warning::unripe could not update its pull request comment (does the job have pull-requests: write?)"
  elif [ "$status" -ne 0 ]; then
    gh api "$comments" -F "body=@$body" > /dev/null || echo "::warning::unripe could not comment on the pull request (does the job have pull-requests: write?)"
  fi
fi

exit "$status"
