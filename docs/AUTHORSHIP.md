# Repository Authorship and Branding Policy

## Ownership

This repository and all project outputs are owned and authored by Frank Van
Laarhoven.

AI systems are development tools only. They are not authors, co-authors,
contributors, maintainers, copyright holders, or owners of any part of this
project.

## Commit Policy

All commits must comply with the following rules:

1. Do not add any AI attribution or co-authorship trailer.
2. Do not add:
   - `Co-Authored-By: Claude`
   - `Co-Authored-By: ChatGPT`
   - `Co-Authored-By: Copilot`
   - `Generated-By:`
   - `Assisted-By:`
   - `AI-generated`
   - similar attribution language
3. Do not mention an AI provider or coding assistant in commit subjects or
   commit bodies.
4. Use only the configured human Git author identity.
5. Before committing, inspect the complete commit message and remove
   prohibited attribution.
6. Before pushing, inspect recent commits for prohibited trailers or branding.
7. Never amend, rewrite, or force-push published history without explicit
   approval from Frank Van Laarhoven.

## Codebase Branding Policy

Do not introduce AI-assistant branding into: source-code comments; file
headers; documentation; README files; package metadata; application banners;
CLI output; UI labels; badges; generated reports; test fixtures; repository
descriptions; changelogs; release notes; branch names; commit messages.

Prohibited examples include "Built by <assistant>", "Generated with
<assistant>", "Powered by <assistant>", "AI-generated implementation", and any
assistant branding used as authorship attribution.

## Permitted Technical References

References to AI providers or models are permitted only when technically
necessary: API integration code; dependency names; provider adapters;
environment variables; model identifiers; compatibility documentation;
benchmark labels; factual citations; licence and attribution requirements; and
user-facing provider selection where it is a product feature.

Permitted technical references must not imply that the provider or AI system
owns, authors, maintains, or contributes to the repository.

## Required Pre-Commit Checks

Before every commit:

1. Run the repository authorship guard.
2. Inspect staged files for prohibited branding.
3. Inspect the proposed commit message.
4. Verify the Git author and committer identity.
5. Refuse to commit if any check fails.

## Enforcement in this repository

| Mechanism | Path |
|---|---|
| Content and message guard | `scripts/check_authorship_policy.py` |
| History guard | `scripts/check_git_history.sh` |
| Pre-commit hook | `.githooks/pre-commit` |
| Commit-message hook | `.githooks/commit-msg` |
| Continuous integration | `.github/workflows/authorship-policy.yml` |

Activate the hooks in a fresh clone:

```bash
chmod +x .githooks/pre-commit .githooks/commit-msg
git config core.hooksPath .githooks
```

`make check` runs the content guard, so the policy is part of the ordinary
build gate rather than a separate discipline.

### Historical exception

Commits made before this policy was adopted contain a prohibited trailer. The
repository is public, so those hashes are preserved rather than rewritten;
rewriting published history would break any existing clone or reference.
`AUTHORSHIP_SINCE` pins the enforcement cutoff for the history guard. Rewriting
that history remains available and requires explicit approval from Frank Van
Laarhoven.
