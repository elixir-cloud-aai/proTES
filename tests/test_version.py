"""Tests for the package version, derived from Git tags."""

from importlib.metadata import version
from pathlib import Path
import shutil
import subprocess
import tomllib

from packaging.version import Version
import pytest
import setuptools_scm

ROOT = Path(__file__).parents[1]
GIT = shutil.which("git")
# Git options for commits and tags in a temporary repository: an identity,
# without signing
GIT_CONFIG = [
    *("-c", "user.name=test"),
    *("-c", "user.email=test@example.org"),
    *("-c", "commit.gpgsign=false"),
    *("-c", "tag.gpgsign=false"),
]


def test_installed_version() -> None:
    """The installed package has a valid PEP 440 version."""
    installed = version("pro-tes")
    assert str(Version(installed)) == installed


def test_fallback_version() -> None:
    """Builds without Git metadata get a development version."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    fallback = Version(config["tool"]["setuptools_scm"]["fallback_version"])
    assert fallback.is_devrelease


def test_pretend_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The version passed to Docker builds is used without Git metadata."""
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION", "1.2.3rc1")
    assert setuptools_scm.get_version(root=str(tmp_path)) == "1.2.3rc1"


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_version_from_tag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The version of a tagged commit is the tag without the `v`."""
    assert GIT is not None
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)

    def git(*args: str) -> None:
        """Run Git in the temporary repository."""
        subprocess.run(  # noqa: S603 (fixed arguments, no shell)
            [GIT, "-C", str(tmp_path), *GIT_CONFIG, *args],
            capture_output=True,
            check=True,
        )

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "release")
    git("tag", "-a", "v1.2.3", "-m", "v1.2.3")
    assert setuptools_scm.get_version(root=str(tmp_path)) == "1.2.3"
