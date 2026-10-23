"""Tests of the build: the base image, the FOCA pin and the Python version."""

from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _foca_requirement() -> Requirement:
    """Get the `foca` requirement of the app (`pyproject.toml`)."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    (foca,) = [
        requirement
        for requirement in map(Requirement, pyproject["project"]["dependencies"])
        if requirement.name == "foca"
    ]
    return foca


def _base_image_tag() -> str:
    """Get the tag of the FOCA base image (`Dockerfile`), without Python."""
    match = re.search(
        r"^FROM elixircloud/foca:(?P<version>[^-@\s]+)",
        (ROOT / "Dockerfile").read_text(),
        flags=re.MULTILINE,
    )
    assert match, "No FOCA base image in the Dockerfile"
    return match["version"]


def test_base_image_foca() -> None:
    """The base image has the FOCA that the `foca` requirement pins.

    So the image build does not install another FOCA on top of it: a FOCA
    release that satisfies the requirement or, for a direct reference to a
    FOCA commit (temporary, see `scripts/check_release_pins.py`), FOCA's
    development image (tagged by date) of that commit, which the `Dockerfile`
    records; the image build checks the FOCA version of the image
    (`scripts/image_requirements.py`).
    """
    foca, tag = _foca_requirement(), _base_image_tag()
    if foca.url is None:
        assert foca.specifier.contains(tag), (
            f"Base image has FOCA {tag}, but {foca} is required"
        )
    else:
        commit = re.fullmatch(
            r"git\+https://github\.com/elixir-cloud-aai/foca@(?P<sha>[0-9a-f]{40})",
            foca.url,
        )
        assert commit, f"FOCA must be pinned to a full commit SHA, not {foca.url}"
        assert re.fullmatch(r"[0-9]{8}", tag), (
            "A FOCA commit needs FOCA's development image (tagged by date)"
        )
        dockerfile = (ROOT / "Dockerfile").read_text()
        assert f"## FOCA commit: {commit['sha']}\n" in dockerfile, (
            "The Dockerfile must record the FOCA commit of its base image"
        )


def test_python_version() -> None:
    """The app targets one Python version, that of the base image.

    That is, the Python version of the base image in the `Dockerfile` is the
    minimum of `requires-python`, the only one in the classifiers and the one
    of ruff and mypy, and all workflows and Read the Docs use it.
    """
    match = re.search(
        r"^FROM elixircloud/foca:[^-@\s]+-py(?P<version>3\.\d+)@",
        (ROOT / "Dockerfile").read_text(),
        flags=re.MULTILINE,
    )
    assert match, "No Python version in the base image tag of the Dockerfile"
    image = match["version"]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == f">={image}"
    assert pyproject["tool"]["mypy"]["python_version"] == image
    assert pyproject["tool"]["ruff"]["target-version"] == f"py{image.replace('.', '')}"
    prefix = "Programming Language :: Python :: 3."
    classifiers = [
        classifier.removeprefix("Programming Language :: Python :: ")
        for classifier in pyproject["project"]["classifiers"]
        if classifier.startswith(prefix)
    ]
    assert classifiers == [image]
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yaml"))
    assert workflows
    for workflow in workflows:
        for name, job in yaml.safe_load(workflow.read_text())["jobs"].items():
            for step in job.get("steps", []):
                if "actions/setup-python@" in step.get("uses", ""):
                    version = step["with"]["python-version"]
                    assert version == image, f"{workflow.name}: {name}"
    readthedocs = yaml.safe_load((ROOT / ".readthedocs.yaml").read_text())
    assert readthedocs["build"]["tools"]["python"] == image
