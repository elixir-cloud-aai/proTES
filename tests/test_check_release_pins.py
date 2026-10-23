"""Tests of the release pin check (`scripts/check_release_pins.py`)."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

from packaging.version import Version
import pytest
import setuptools_scm

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_pins.py"

# `scripts/` is not a package, so the script is loaded from its file
_spec = importlib.util.spec_from_file_location("check_release_pins", SCRIPT)
assert _spec and _spec.loader
check_release_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_release_pins)

PYPROJECT = """[project]
name = "app"
dependencies = [
{dependencies}
]
"""
DOCKERFILE = """## A comment
FROM elixircloud/foca:{tag}@sha256:{digest}
USER 1000
"""
CHART = """apiVersion: v2
name: app
version: 0.0.0
appVersion: "dev"
dependencies:
  # A comment
  - name: foca-common
    version: {version}
    repository: oci://ghcr.io/elixir-cloud-aai/charts
"""
DIGEST = "0" * 64


def _write(
    root: Path,
    dependencies: str = '"foca>=0.17.0,<0.18",',
    tag: str = "0.17.0-py3.13",
    chart_version: str = "0.2.0",
) -> Path:
    """Write the files that the script checks.

    Args:
        root: Directory to write them to.
        dependencies: Lines of `project.dependencies` in `pyproject.toml`.
        tag: Tag of the base image in the `Dockerfile`.
        chart_version: Version of the chart's dependency.

    Returns:
        The directory.
    """
    (root / "deployment").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.format(dependencies=dependencies))
    (root / "Dockerfile").write_text(DOCKERFILE.format(tag=tag, digest=DIGEST))
    (root / "deployment" / "Chart.yaml").write_text(CHART.format(version=chart_version))
    return root


def test_release_pins(tmp_path: Path) -> None:
    """Pins of released versions are accepted."""
    root = _write(tmp_path, dependencies='"connexion>=2.15.1,<3",\n"foca[x]>=0.17.0",')
    assert check_release_pins.find_dev_pins(root) == []


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        (
            {
                "dependencies": (
                    '"foca @ git+https://github.com/elixir-cloud-aai/foca@'
                    + "a" * 40
                    + '",'
                )
            },
            "pyproject.toml: dependency 'foca @ git+https://github.com/"
            f"elixir-cloud-aai/foca@{'a' * 40}' is a direct reference",
        ),
        (
            {"dependencies": '"foca[x] @ https://example.org/foca.tar.gz",'},
            "pyproject.toml: dependency 'foca[x] @ https://example.org/foca.tar.gz'"
            " is a direct reference",
        ),
        (
            {"dependencies": '"foca@https://example.org/foca.tar.gz",'},
            "pyproject.toml: dependency 'foca@https://example.org/foca.tar.gz'"
            " is a direct reference",
        ),
        (
            {"dependencies": '"Foca [x]@https://example.org/foca.tar.gz",'},
            "pyproject.toml: dependency 'Foca [x]@https://example.org/foca.tar.gz'"
            " is a direct reference",
        ),
        (
            {"dependencies": '"foca>=0.17.0rc1,<0.18",'},
            "pyproject.toml: dependency 'foca>=0.17.0rc1,<0.18' names a pre-release",
        ),
        (
            {"dependencies": "\"connexion==3.0.dev0; python_version < '3.14'\","},
            "pyproject.toml: dependency 'connexion==3.0.dev0; python_version < "
            "'3.14'' names a pre-release",
        ),
        (
            {"dependencies": '"foca==0.17.0+gabc1234",'},
            "pyproject.toml: dependency 'foca==0.17.0+gabc1234' names a local version",
        ),
        (
            {"dependencies": "\"foca===0.17.0+local.1; python_version >= '3'\","},
            "pyproject.toml: dependency 'foca===0.17.0+local.1; python_version >= "
            "'3'' names a local version",
        ),
        (
            {"tag": "20261022-py3.13"},
            "Dockerfile: base image tag '20261022-py3.13' is not a release tag "
            "(<X.Y.Z>-py<X.Y>)",
        ),
        (
            {"tag": "0.17.0"},
            "Dockerfile: base image tag '0.17.0' is not a release tag "
            "(<X.Y.Z>-py<X.Y>)",
        ),
        (
            {"chart_version": "0.2.0-rc1"},
            "deployment/Chart.yaml: dependency version '0.2.0-rc1' is not a "
            "release version",
        ),
        (
            {"chart_version": '"~0.2.0"'},
            "deployment/Chart.yaml: dependency version '~0.2.0' is not a "
            "release version",
        ),
        (
            {"chart_version": "0.2.0 || 0.2.1-rc1  # a comment"},
            "deployment/Chart.yaml: dependency version '0.2.0 || 0.2.1-rc1' is "
            "not a release version",
        ),
    ],
    ids=[
        "git",
        "url",
        "no-spaces",
        "extras-spaces",
        "pre-release",
        "dev-marker",
        "local",
        "local-marker",
        "dated-image",
        "no-python",
        "chart-rc",
        "chart-range",
        "chart-alternatives",
    ],
)
def test_dev_pins(tmp_path: Path, kwargs: dict[str, str], error: str) -> None:
    """Pins of development versions are found."""
    assert check_release_pins.find_dev_pins(_write(tmp_path, **kwargs)) == [error]


@pytest.mark.parametrize(
    "requirement",
    ["foca>=0.17.0", "rcpackage>=1.0", "dev-tools[a]>=2.0.post1", "abc==1.0.0b1"],
    ids=["release", "name-rc", "post-release", "beta"],
)
def test_requirement_versions(tmp_path: Path, requirement: str) -> None:
    """Only versions are checked for pre-releases, not names or extras."""
    pins = check_release_pins.find_dev_pins(
        _write(tmp_path, dependencies=f'"{requirement}",')
    )
    assert pins == (
        [f"pyproject.toml: dependency '{requirement}' names a pre-release"]
        if requirement == "abc==1.0.0b1"
        else []
    )


def test_optional_dependencies(tmp_path: Path) -> None:
    """Optional dependencies are checked, too."""
    root = _write(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text()
        + '[project.optional-dependencies]\ndev = ["x @ https://example.org/x.zip"]\n'
    )
    assert check_release_pins.find_dev_pins(root) == [
        "pyproject.toml: dependency 'x @ https://example.org/x.zip' is a direct "
        "reference"
    ]


@pytest.mark.parametrize(
    "dependencies",
    [
        "dependencies: [{name: a, version: 1.0.0-rc1}]\n",
        "dependencies:\n  - {name: a, version: 1.0.0-rc1}\n",
    ],
    ids=["flow-list", "flow-item"],
)
def test_chart_dependencies_flow(tmp_path: Path, dependencies: str) -> None:
    """Dependencies in flow style are reported, as they can't be checked."""
    root = _write(tmp_path)
    (root / "deployment" / "Chart.yaml").write_text(f"name: app\n{dependencies}")
    assert check_release_pins.find_dev_pins(root) == [
        "deployment/Chart.yaml: dependencies in flow style can't be checked"
    ]


def test_no_base_image(tmp_path: Path) -> None:
    """A `Dockerfile` without the FOCA base image is reported."""
    root = _write(tmp_path)
    (root / "Dockerfile").write_text("FROM python:3.13\n")
    assert check_release_pins.find_dev_pins(root) == [
        "Dockerfile: no elixircloud/foca base image"
    ]


def test_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`main()` reports pins as GitHub errors, and fails if there are any."""
    released = tmp_path / "released"
    released.mkdir()
    assert check_release_pins.main([str(_write(released))]) == 0
    assert capsys.readouterr().err == ""
    dev = tmp_path / "dev"
    dev.mkdir()
    assert check_release_pins.main([str(_write(dev, tag="20261022-py3.13"))]) == 1
    assert capsys.readouterr().err == (
        "::error::Dockerfile: base image tag '20261022-py3.13' is not a release "
        "tag (<X.Y.Z>-py<X.Y>): pin the released version before releasing\n"
    )


def test_main_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without arguments, `main()` checks this repository."""
    roots: list[Path] = []

    def find_dev_pins(root: Path) -> list[str]:
        roots.append(root)
        return []

    monkeypatch.setattr(check_release_pins, "find_dev_pins", find_dev_pins)
    assert check_release_pins.main([]) == 0
    assert roots == [ROOT]


def test_chart_dependencies_indentless(tmp_path: Path) -> None:
    """Dependencies listed without indentation are checked, too."""
    root = _write(tmp_path)
    (root / "deployment" / "Chart.yaml").write_text(
        "name: app\ndependencies:\n- name: a\n  version: 1.0.0-rc1\nversion: 0.0.0\n"
    )
    assert check_release_pins.find_dev_pins(root) == [
        "deployment/Chart.yaml: dependency version '1.0.0-rc1' is not a release version"
    ]


def test_chart_dependencies(tmp_path: Path) -> None:
    """All dependencies of the chart are checked, and only their versions."""
    root = _write(tmp_path)
    (root / "deployment" / "Chart.yaml").write_text(
        """apiVersion: v2
name: app
version: 0.0.0
dependencies:  # A comment
  - name: foca-common
    version: 0.2.0
    repository: oci://ghcr.io/elixir-cloud-aai/charts
  # A comment
  - version: "1.0.0-rc1"
    name: other

maintainers:
  - name: someone
    version: not-a-dependency
annotations:
  version: dev
"""
    )
    assert check_release_pins.find_dev_pins(root) == [
        "deployment/Chart.yaml: dependency version '1.0.0-rc1' is not a release version"
    ]


def test_script(tmp_path: Path) -> None:
    """The script runs as the workflows call it."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    _write(tmp_path, chart_version="0.2.0-rc1")
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [sys.executable, "-I", str(tmp_path / "scripts" / "check_release_pins.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert (result.returncode, result.stdout) == (1, "")
    assert "dependency version '0.2.0-rc1'" in result.stderr


def test_release_checkout() -> None:
    """Checkouts of releases pin released versions only.

    The version of the checkout (from Git tags) is a release, or a
    pre-release, unless it is a development version or has a local part
    (commits after the tag, or changes). Tests of a release checkout fail
    while it pins development versions, as does the workflow for release
    tags (`release-pins.yaml`); other checkouts may pin them.
    """
    try:
        checkout = Version(setuptools_scm.get_version(root=str(ROOT)))
    except LookupError:
        pytest.skip("No Git metadata")
    if checkout.is_devrelease or checkout.local is not None:
        pytest.skip(f"Not a release: {checkout}")
    assert check_release_pins.find_dev_pins(ROOT) == []
