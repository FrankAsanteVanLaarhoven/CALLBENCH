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
# enforce the policy from a cutoff forward. Unset means every ref we author,
# which is the intended configuration here.
SINCE="${AUTHORSHIP_SINCE:-}"

# Which refs count as ours.
#
# NOT `--all`. This workflow runs on `pull_request`, and on that event
# `actions/checkout` fetches `refs/pull/N/merge` — a merge preview GitHub
# synthesises, authored by the web identity and committed by
# `GitHub <noreply@github.com>`. It is not in our history, cannot be pushed to,
# attributes no contributor on the forge, and disappears when the pull request
# closes.
#
# `--all` therefore fails the first pull request ever opened against this
# repository, for a commit nobody can fix. It has not fired only because every
# commit so far went straight to `main`. Found in a sibling repository the
# moment a pull request was raised there.
#
# Selected positively — branches, remote branches, tags — rather than by
# excluding a pattern. An exclusion list is where the real violation eventually
# hides: `--all` minus today's known synthetic namespace still admits
# tomorrow's. Adding a namespace here is a visible edit.
select_refs() {
  git for-each-ref --format='%(refname)' refs/heads refs/remotes/origin refs/tags
}

if [ -n "$SINCE" ]; then
  RANGE_ARGS=("$SINCE..HEAD")
  RANGE_LABEL="$SINCE..HEAD"
else
  # `while read` rather than `mapfile`: the latter is a bash 4 builtin and
  # macOS ships bash 3.2, so a hook calling this would fail on the machine most
  # commits are written on while passing in CI.
  RANGE_ARGS=()
  while IFS= read -r ref; do
    [ -n "$ref" ] && RANGE_ARGS+=("$ref")
  done < <(select_refs)
  RANGE_LABEL="${#RANGE_ARGS[@]} authored refs"

  # Anti-vacuity. A filter bug, a bare checkout or a renamed remote would leave
  # this empty, and `git log` with no revisions audits nothing and exits zero —
  # a clean report over an empty set.
  if [ "${#RANGE_ARGS[@]}" -eq 0 ]; then
    echo "No authored refs found (refs/heads, refs/remotes/origin, refs/tags)." >&2
    echo "Refusing to report a pass over an empty ref set." >&2
    exit 1
  fi

  # A shallow clone has refs and almost no history, so `git clone --depth 1`
  # audits one commit and reports "history check passed" — a stronger claim
  # than a truncated fetch can support. The workflow sets `fetch-depth: 0`, but
  # anyone running this locally or from a hook may not have.
  if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    echo "This is a shallow clone, so most commits are not present." >&2
    echo "Refusing to report a full-history pass over a truncated history." >&2
    echo "Fetch the whole history first (actions/checkout: fetch-depth: 0)." >&2
    exit 1
  fi
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
