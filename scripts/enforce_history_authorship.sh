#!/usr/bin/env bash
# Authoritative authorship enforcement over Git history.
#
# Local hooks can be bypassed: `--no-verify`, a fresh clone that never ran
# `git config core.hooksPath`, or a push from another machine. This check runs
# in CI over the whole history and is therefore the enforcement boundary that
# actually holds. It fails on either violation:
#
#   1. any commit whose author OR committer is not the approved identity;
#   2. any commit message carrying a prohibited attribution trailer.
#
# Both are checked for every commit on every ref, not just the tip.
set -euo pipefail

EXPECTED_NAME="${EXPECTED_NAME:-Frank Van Laarhoven}"
EXPECTED_EMAIL="${EXPECTED_EMAIL:-frankleroyvan@gmail.com}"
EXPECTED="${EXPECTED_NAME} <${EXPECTED_EMAIL}>"

TRAILER_PATTERN='Co-Authored-By:.*(Claude|ChatGPT|Copilot|Gemini)|Generated-By:|Assisted-By:'

# AUTHORSHIP_SINCE lets a repository that inherits history it cannot rewrite
# enforce the policy from a cutoff forward. Unset means the whole history on
# every ref, which is the intended configuration here.
SINCE="${AUTHORSHIP_SINCE:-}"
if [ -n "$SINCE" ]; then
  RANGE_ARGS=("$SINCE..HEAD")
  RANGE_LABEL="$SINCE..HEAD"
else
  RANGE_ARGS=(--all)
  RANGE_LABEL="all refs"
fi

status=0

# ---- 1. author and committer identity --------------------------------------

identity_offenders="$(
  git log "${RANGE_ARGS[@]}" --format='%H%x1f%an <%ae>%x1f%cn <%ce>' |
  while IFS=$'\x1f' read -r sha author committer; do
    if [ "$author" != "$EXPECTED" ] || [ "$committer" != "$EXPECTED" ]; then
      printf '%s\n  author:    %s\n  committer: %s\n' "$sha" "$author" "$committer"
    fi
  done
)"

if [ -n "$identity_offenders" ]; then
  echo "Commits with an unapproved author or committer identity ($RANGE_LABEL)." >&2
  echo "Expected: $EXPECTED" >&2
  echo "$identity_offenders" >&2
  status=1
fi

# ---- 2. attribution trailers -----------------------------------------------
#
# Deliberately NOT `git log ... | grep -q`. Under `set -o pipefail`, grep -q
# exits on the first match, git log takes SIGPIPE, and the pipeline reports
# 141 — so the `if` evaluates false and a violation passes silently. The bug
# only bites when the offending commit is near the *tip*, because a match late
# in the stream lets git log finish first. That is the worst possible failure
# mode for this check: it would pass on exactly the commit just pushed.
#
# Capturing the output makes grep consume the whole stream, so there is no
# SIGPIPE and no race.

trailer_hits="$(
  git log "${RANGE_ARGS[@]}" --format='%H%n%B%n---END---' |
    grep -Ein "$TRAILER_PATTERN" || true
)"

if [ -n "$trailer_hits" ]; then
  echo "Prohibited attribution trailer found in Git history ($RANGE_LABEL)." >&2
  echo "$trailer_hits" >&2
  status=1
fi

if [ "$status" -ne 0 ]; then
  exit "$status"
fi

commits="$(git log "${RANGE_ARGS[@]}" --format='%H' | wc -l | tr -d ' ')"
echo "Sole-authorship history check passed ($RANGE_LABEL, $commits commits)."
echo "  every author and committer: $EXPECTED"
echo "  prohibited attribution trailers: none"
