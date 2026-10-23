"""Tests of the checkout of the library chart (`scripts/foca_common.sh`).

TEMPORARY: removed with the script, once foca-common 0.2.0 is released
(#246). The tests use local Git repositories only, no network.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foca_common.sh"
GIT = shutil.which("git")
CHART = """apiVersion: v2
name: app
dependencies:
  # FOCA commit: {commit}
  - name: foca-common
    version: 0.2.0
    repository: file://../.foca-common/charts/foca-common
"""

pytestmark = pytest.mark.skipif(GIT is None, reason="git not installed")


def _git(directory: Path, *args: str) -> str:
    """Run Git in a directory.

    Args:
        directory: Working directory.
        args: Arguments of Git.

    Returns:
        The standard output, stripped.
    """
    assert GIT is not None
    return subprocess.run(  # noqa: S603 (argument list, no shell)
        [
            GIT,
            "-C",
            str(directory),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path, commit: str | None = None) -> Path:
    """Create a repository with the script and a chart pinning a commit.

    Args:
        root: Directory of the repository.
        commit: The pinned FOCA commit; that of a local checkout of a fake
            library chart in `.foca-common/` if `None`.

    Returns:
        The directory.
    """
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts")
    (root / "deployment").mkdir()
    if commit is None:
        checkout = root / ".foca-common"
        (checkout / "charts" / "foca-common").mkdir(parents=True)
        (checkout / "charts" / "foca-common" / "Chart.yaml").write_text("name: x\n")
        _git(checkout, "init", "--quiet")
        _git(checkout, "add", ".")
        _git(checkout, "commit", "--quiet", "-m", "chart")
        commit = _git(checkout, "rev-parse", "HEAD")
    (root / "deployment" / "Chart.yaml").write_text(CHART.format(commit=commit))
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the script of a repository.

    Args:
        root: Directory of the repository.

    Returns:
        The completed process.
    """
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 (argument list, no shell)
        [bash, str(root / "scripts" / "foca_common.sh")],
        capture_output=True,
        check=False,
        text=True,
    )


def test_pinned_commit() -> None:
    """The chart pins one full FOCA commit, which the script reads."""
    text = (ROOT / "deployment" / "Chart.yaml").read_text()
    lines = [line for line in text.splitlines() if "# FOCA commit:" in line]
    assert len(lines) == 1
    assert len(lines[0].split(": ")[1]) == 40


def test_checkout_exists(tmp_path: Path) -> None:
    """An existing checkout of the pinned commit is kept."""
    root = _repository(tmp_path)
    result = _run(root)
    assert result.returncode == 0, result.stderr
    assert "foca-common of FOCA commit" in result.stdout


@pytest.mark.parametrize("commit", ["", "abc123", "0" * 39])
def test_no_commit(tmp_path: Path, commit: str) -> None:
    """The script fails unless the chart pins a full commit SHA."""
    result = _run(_repository(tmp_path, commit=commit))
    assert result.returncode == 1
    assert "has no (single) line '# FOCA commit: <full SHA>'" in result.stderr


def test_other_commit(tmp_path: Path) -> None:
    """The script fails if the checkout has another commit."""
    root = _repository(tmp_path)
    (root / "deployment" / "Chart.yaml").write_text(CHART.format(commit="a" * 40))
    result = _run(root)
    assert result.returncode == 1
    assert f"not {'a' * 40}: remove it" in result.stderr


def test_local_changes(tmp_path: Path) -> None:
    """The script fails if the checkout has local changes."""
    root = _repository(tmp_path)
    (root / ".foca-common" / "charts" / "foca-common" / "Chart.yaml").write_text("")
    result = _run(root)
    assert result.returncode == 1
    assert "has local changes" in result.stderr
