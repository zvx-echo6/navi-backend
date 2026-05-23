"""Hermetic tests for shared.git_sha.git_short_sha."""
from shared.git_sha import git_short_sha


def test_git_short_sha_returns_unknown_on_bad_path(tmp_path):
    # tmp_path is an empty dir, not a git repo → graceful 'unknown', no raise.
    assert git_short_sha(str(tmp_path)) == 'unknown'


def test_git_short_sha_in_real_repo():
    # The suite runs from inside the navi-backend repo, so the cwd lookup works.
    sha = git_short_sha()
    assert sha != 'unknown'
    assert len(sha) >= 7   # short SHA, sanity bound
