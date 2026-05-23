"""Shared git short-SHA helper.

Used by every navi-* service's create_app() for the `version` field in
admin-info, and by navi-admin's fleet.recon_git_sha to read recon's deployed
SHA from its clone path. One implementation, one place to fix when behavior
needs changing.
"""
import subprocess


def git_short_sha(repo_path: str | None = None) -> str:
    """Return ``git rev-parse --short HEAD`` for the given repo path, or for the
    current working directory if path is None. Returns 'unknown' on any failure
    (no git, no repo, permission denied, detached HEAD, etc.) — never raises.

    repo_path: explicit repo to query (uses ``git -C <path>``); None = current
               cwd (the systemd unit's WorkingDirectory in prod).
    """
    cmd = ['git']
    if repo_path is not None:
        cmd.extend(['-C', repo_path])
    cmd.extend(['rev-parse', '--short', 'HEAD'])
    try:
        sha = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=3,
        ).strip()
        return sha or 'unknown'
    except Exception:
        return 'unknown'
