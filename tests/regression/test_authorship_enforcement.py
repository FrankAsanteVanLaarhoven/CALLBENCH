"""The sole-authorship history guard.

Local hooks can be bypassed with `--no-verify`, by a clone that never set
`core.hooksPath`, or by a push from another machine. The history check in CI is
therefore the authoritative enforcement boundary, and a boundary that cannot
fail enforces nothing — so every violation class is exercised against a real
throwaway repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "enforce_history_authorship.sh"
IDENTITY = ("Frank Van Laarhoven", "frankleroyvan@gmail.com")


def _git(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    import os

    environment = {**os.environ, **env}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one clean commit by the approved identity."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", IDENTITY[0])
    _git(root, "config", "user.email", IDENTITY[1])
    (root / "file.txt").write_text("content\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def _run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GUARD)], cwd=repo, capture_output=True, text=True, check=False
    )


def test_a_clean_history_passes(repo: Path) -> None:
    result = _run_guard(repo)
    assert result.returncode == 0, result.stderr
    assert "every author and committer" in result.stdout


def test_an_unapproved_author_is_blocked(repo: Path) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "--author=Some Bot <bot@example.com>",
         "-m", "probe", "--no-verify")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "unapproved author or committer" in result.stderr
    assert "bot@example.com" in result.stderr


def test_an_unapproved_committer_is_blocked(repo: Path) -> None:
    """The author can be forged correctly while the committer is not."""
    _git(repo, "commit", "-q", "--allow-empty", "-m", "probe", "--no-verify",
         GIT_COMMITTER_NAME="Some Bot", GIT_COMMITTER_EMAIL="bot@example.com")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "unapproved author or committer" in result.stderr


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Co-Authored-By: Copilot <copilot@example.com>",
        "Generated-By: some-tool",
        "Assisted-By: some-tool",
    ],
)
def test_a_prohibited_trailer_is_blocked(repo: Path, trailer: str) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", f"probe\n\n{trailer}", "--no-verify")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "Prohibited attribution trailer" in result.stderr


def test_a_trailer_on_the_newest_commit_is_blocked(repo: Path) -> None:
    """The regression this test exists for.

    The check used `git log ... | grep -q`. Under `set -o pipefail`, grep -q
    exits on first match, git log takes SIGPIPE, and the pipeline reports 141 —
    so the guard evaluated false and the violation passed. It only bit when the
    offending commit was near the tip, because a match late in the stream lets
    git log finish first. That is the worst failure mode available: it passes on
    exactly the commit just pushed.
    """
    for index in range(12):
        body = "filler line\n" * 40
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"bulk {index}\n\n{body}")
    _git(repo, "commit", "-q", "--allow-empty",
         "-m", "newest\n\nCo-Authored-By: Claude <noreply@anthropic.com>", "--no-verify")

    result = _run_guard(repo)
    assert result.returncode == 1, "a trailer on the newest commit must not slip through"
    assert "Prohibited attribution trailer" in result.stderr


def test_both_violation_classes_are_reported_together(repo: Path) -> None:
    """One run should surface everything, not stop at the first finding."""
    _git(repo, "commit", "-q", "--allow-empty", "--author=Some Bot <bot@example.com>",
         "-m", "probe\n\nGenerated-By: some-tool", "--no-verify")
    result = _run_guard(repo)
    assert result.returncode == 1
    assert "unapproved author or committer" in result.stderr
    assert "Prohibited attribution trailer" in result.stderr


def test_this_repository_satisfies_the_policy() -> None:
    """The guard, run against the real repository history."""
    result = subprocess.run(
        ["bash", str(GUARD)], cwd=GUARD.parents[1], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# --- Which refs count as ours -----------------------------------------------
#
# The guard audited `--all`, which on a `pull_request` event includes
# `refs/pull/N/merge`: a merge preview GitHub synthesises, authored by the web
# identity and committed by `GitHub <noreply@github.com>`. That would have
# failed the first pull request ever opened against this repository, for a
# commit nobody can fix. It never fired only because every commit so far went
# straight to `main`.
#
# The exclusion has to stay exactly as narrow as it is. The first two tests
# below are a pair, and neither means anything alone: the same commit is
# ignored on a synthetic ref and caught on a real branch.


def _forge_commit(repo: Path) -> str:
    """An empty commit by an identity that is not ours, left unreferenced."""
    _git(
        repo,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Merge pull request #1",
        GIT_AUTHOR_NAME="Someone Else",
        GIT_AUTHOR_EMAIL="else@example.invalid",
        GIT_COMMITTER_NAME="A Forge",
        GIT_COMMITTER_EMAIL="noreply@forge.invalid",
    )
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    return sha


def test_a_synthetic_pull_merge_ref_is_not_audited(repo: Path) -> None:
    sha = _forge_commit(repo)
    _git(repo, "update-ref", "refs/remotes/pull/1/merge", sha)

    result = _run_guard(repo)

    assert result.returncode == 0, result.stderr


def test_the_same_commit_on_a_real_branch_is_still_caught(repo: Path) -> None:
    """The control. Without it the test above passes on a guard that audits
    nothing at all."""
    sha = _forge_commit(repo)
    _git(repo, "branch", "tainted", sha)

    result = _run_guard(repo)

    assert result.returncode == 1
    assert "unapproved author or committer" in result.stderr


def test_a_shallow_clone_is_refused(repo: Path, tmp_path: Path) -> None:
    """`git clone --depth 1` has refs and one commit, so the guard would report
    "history check passed" over a history it does not have. The workflow sets
    `fetch-depth: 0`, but anyone running this locally may not have."""
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-local", f"file://{repo}", str(shallow)],
        check=True,
        capture_output=True,
    )

    result = _run_guard(shallow)

    assert result.returncode == 1
    assert "shallow clone" in result.stderr


def test_a_full_clone_of_the_same_repository_passes(repo: Path, tmp_path: Path) -> None:
    """The control for the check above, which would otherwise pass on a guard
    that refuses every clone."""
    full = tmp_path / "full"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", f"file://{repo}", str(full)],
        check=True,
        capture_output=True,
    )

    result = _run_guard(full)

    assert result.returncode == 0, result.stderr


def test_a_repository_with_no_authored_refs_is_refused(tmp_path: Path) -> None:
    """`git log` over no revisions audits nothing and exits zero — a clean
    report over an empty set."""
    bare = tmp_path / "bare"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)

    result = _run_guard(bare)

    assert result.returncode == 1
    assert "empty ref set" in result.stderr


def test_the_guard_selects_refs_positively(repo: Path) -> None:
    """An exclusion list is where the real violation eventually hides: `--all`
    minus today's synthetic namespace still admits tomorrow's."""
    code = "\n".join(
        line
        for line in GUARD.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "refs/heads" in code
    assert "refs/remotes/origin" in code
    assert "refs/tags" in code
    assert "--all" not in code
    # `mapfile` is a bash 4 builtin and macOS ships 3.2, so a hook calling this
    # would fail on the machine most commits are written on.
    assert "mapfile" not in code
