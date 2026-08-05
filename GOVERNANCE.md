# Repository Ownership and Authorship

Frank Van Laarhoven is the sole author, contributor, committer, maintainer,
and owner represented by this repository.

Development tools and automated systems must not be attributed as authors,
co-authors, contributors, maintainers, or owners.

No commit may contain automated co-authorship, generation, assistance, or
equivalent attribution trailers.

Provider-specific development-assistant configuration must remain local and
must not be committed to the repository.

## Scope

This policy governs commit metadata, commit messages, and repository content:
source comments, file headers, documentation, package metadata, banners, CLI
output, generated reports, test fixtures, repository descriptions, changelogs,
release notes, and branch names.

## Permitted technical references

Names of external providers, services, models and libraries are permitted
where they are technically necessary and identify an *integration* rather than
authorship:

- provider adapters and API integration code;
- dependency names and package metadata;
- environment variables and model identifiers;
- pricing tables and compatibility documentation;
- benchmark labels and factual citations;
- licence and attribution requirements;
- user-facing provider selection, where that is a product feature.

Such references must never imply that a provider or automated system owns,
authors, maintains, or contributes to this repository.

## Enforcement

The policy is enforced deterministically rather than by convention.

| Control | Path |
|---|---|
| Content, message and identity guard | `scripts/enforce_sole_authorship.py` |
| History guard | `scripts/enforce_history_authorship.sh` |
| Pre-commit hook | `.githooks/pre-commit` |
| Commit-message hook | `.githooks/commit-msg` |
| Continuous integration | `.github/workflows/sole-authorship.yml` |

Activate the hooks in a fresh clone:

```bash
chmod +x .githooks/pre-commit .githooks/commit-msg
git config core.hooksPath .githooks
```

`make authorship` runs the content guard and is part of `make check`, so the
policy is a build gate rather than a separate discipline.

Expected identity: `Frank Van Laarhoven <frankleroyvan@gmail.com>`. Both hooks
verify it, so an identity drift fails at commit time.

### The authoritative boundary is CI, not the hooks

Local hooks are a convenience and can be bypassed — `--no-verify`, a clone that
never ran `git config core.hooksPath`, or a push from another machine. The
history check in CI is the control that actually holds. It fails when **any**
commit on **any** ref:

- has an author or committer other than the approved identity; or
- carries a prohibited attribution trailer.

Both conditions are checked over full history on every push and pull request,
and both are reported in a single run rather than stopping at the first
finding.

The guard is itself regression-tested (`tests/regression/test_authorship_enforcement.py`):
each violation class is exercised against a throwaway repository, because an
enforcement boundary that cannot fail enforces nothing. One such test exists
for a specific defect — a `grep -q` in a pipeline under `set -o pipefail` made
the trailer check pass silently when the offending commit was near the tip,
which is precisely the commit that matters.

## History

The full commit history satisfies this policy, verified without a cutoff:

```bash
bash scripts/enforce_history_authorship.sh
```

Earlier commits carried attribution trailers and an inconsistent author
identity. They were rewritten once, with explicit approval, before the
repository acquired downstream consumers. The rewrite was deliberately scoped
to commit messages and identity: a blanket text replacement across file
contents would have corrupted the permitted technical references above, on
which the benchmark depends to function.

`AUTHORSHIP_SINCE` remains supported in the history guard for any repository
that inherits history it cannot rewrite. It is unset here.
