"""
GitHub repository fetch via shallow clone.
Uses git clone --depth 1 for fast, minimal retrieval of public repos.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CLONE_TIMEOUT_SECONDS = 90


class GitHubClientError(Exception):
    """Raised when repo cannot be cloned."""

    pass


class RepoNotFoundError(GitHubClientError):
    """Repo does not exist or access is denied (private)."""

    pass


def _parse_github_url(url: str) -> tuple[str, str]:
    """Parse GitHub URL into (owner, repo). Handles trailing slash, .git suffix, and extra path segments."""
    url = url.strip().rstrip("/").strip()
    if url.endswith(".git"):
        url = url[:-4].rstrip("/")
    if not url or "github.com" not in url:
        raise GitHubClientError("Invalid GitHub URL. Use https://github.com/owner/repo")
    match = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$", url)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/?]+?)(?:\.git)?(?:/.*)?$", url)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    raise GitHubClientError("Invalid GitHub URL. Use https://github.com/owner/repo")


def clone_repo(github_url: str, timeout: int = CLONE_TIMEOUT_SECONDS) -> Path:
    """
    Shallow clone (depth=1) to a temp directory. Returns path to cloned repo.
    Caller must call cleanup(repo_root) in a finally block.
    """
    owner, repo = _parse_github_url(github_url)
    clone_url = f"https://github.com/{owner}/{repo}.git"
    tmpdir = tempfile.mkdtemp(prefix="repo_summary_")

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", clone_url, tmpdir],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise GitHubClientError("Clone timed out. Repository may be too large.")

    if result.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        err = (result.stderr or result.stdout or "").strip().lower()
        if "repository not found" in err or "could not read username" in err or "authentication failed" in err:
            raise RepoNotFoundError("Repository not found or access denied (private repo).")
        raise GitHubClientError(f"Git clone failed: {(result.stderr or result.stdout or '')[:500]}")

    return Path(tmpdir)


def cleanup(repo_path: Path) -> None:
    """Remove cloned repo directory."""
    shutil.rmtree(repo_path, ignore_errors=True)
