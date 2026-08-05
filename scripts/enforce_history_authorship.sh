#!/usr/bin/env bash
set -euo pipefail

pattern='Co-Authored-By:.*(Claude|ChatGPT|Copilot|Gemini)|Generated-By:|Assisted-By:'

# --since lets CI enforce the policy from a cutoff forward on a repository whose
# earlier history is already published and deliberately preserved.
SINCE="${AUTHORSHIP_SINCE:-}"
if [ -n "$SINCE" ]; then
  RANGE="$SINCE..HEAD"
else
  RANGE="HEAD"
fi

if git log "$RANGE" --format='%H%n%B%n---END---' | grep -Eiq "$pattern"; then
  echo "Prohibited AI attribution found in Git history ($RANGE)." >&2
  git log "$RANGE" --format='%H %s%n%b' | grep -Ein -B2 -A3 "$pattern" >&2 || true
  exit 1
fi

echo "Git history authorship policy passed ($RANGE)."
