"""Tests of the requirements of the image build (`scripts/image_requirements.py`)."""

import importlib.metadata
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/image_requirements.py"

# `scripts/` is not a package, so the script is loaded from its file
_spec = importlib.util.spec_from_file_location("image_requirements", SCRIPT)
assert _spec and _spec.loader
image_requirements = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(image_requirements)

SHA = "db6998cae4e1f1fbbbaafd7a26795f3bbfa4511a"
PIN = f"foca @ git+https://github.com/elixir-cloud-aai/foca@{SHA}"


@pytest.mark.parametrize(
    "installed",
    [
        "0.16.1.dev7+gdb6998cae",
        "0.16.1.dev7+gdb6998c",
        "0.16.1.dev7+gdb6998cae.d20261022",
        f"0.16.1.dev7+g{SHA}",
    ],
)
def test_commit_pin(installed: str) -> None:
    """A pin of the base image's FOCA commit is left out."""
    dependencies = ["connexion>=2.15.1,<3", PIN, "gunicorn>=26"]
    assert image_requirements.image_requirements(dependencies, installed) == [
        "connexion>=2.15.1,<3",
        "gunicorn>=26",
    ]


def test_release() -> None:
    """Requirements of FOCA releases are kept."""
    dependencies = ["foca>=0.17.0,<0.18", "gunicorn>=26"]
    assert image_requirements.image_requirements(dependencies, "0.17.0") == (
        dependencies
    )


@pytest.mark.parametrize(
    "installed",
    ["0.17.0", "0.16.1.dev7+g0417848aa", "0.16.1.dev7+gdb699", "0.16.1+local"],
)
def test_other_commit(installed: str) -> None:
    """The build fails unless the base image has FOCA of the pinned commit."""
    with pytest.raises(
        ValueError, match=f"has FOCA {re.escape(installed)}, not of commit"
    ):
        image_requirements.image_requirements([PIN], installed)


@pytest.mark.parametrize(
    "dependency",
    [
        "foca @ git+https://github.com/elixir-cloud-aai/foca@dev",
        "foca @ git+https://github.com/example/foca@" + SHA,
        "foca[x] @ https://example.org/foca.tar.gz",
        "FOCA@https://example.org/foca.tar.gz",
    ],
)
def test_other_reference(dependency: str) -> None:
    """Direct references of FOCA other than to a commit are refused."""
    with pytest.raises(ValueError, match="full commit SHA"):
        image_requirements.image_requirements([dependency], "0.16.1.dev7+gdb6998cae")


def test_main(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()` prints the requirements, or the error."""
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.17.0")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["foca>=0.1", "gunicorn"]\n')
    assert image_requirements.main([str(pyproject)]) == 0
    assert capsys.readouterr().out == "foca>=0.1\ngunicorn\n"
    pyproject.write_text(f'[project]\ndependencies = ["{PIN}"]\n')
    assert image_requirements.main([str(pyproject)]) == 1
    assert capsys.readouterr().err == (
        f"error: The base image has FOCA 0.17.0, not of commit {SHA}\n"
    )


def test_script(tmp_path: Path) -> None:
    """The script runs as the image build calls it."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["gunicorn>=26"]\n')
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [sys.executable, "-I", str(SCRIPT), str(pyproject)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "gunicorn>=26\n",
        "",
    )
