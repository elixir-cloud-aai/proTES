"""Check that a release only pins released versions of its dependencies.

Used by the release workflow (`.github/workflows/release.yaml`) and by the
tests of release checkouts (`tests/test_check_release_pins.py`), so that
temporary pins to development versions (e.g., of FOCA, to test unreleased
changes) can't be released by accident. Fails, with errors as GitHub
annotations on standard error, if:

- a dependency of the app (`project.dependencies` or
  `project.optional-dependencies` of `pyproject.toml`) is a direct reference
  (`<name> @ <URL>`, e.g., a Git commit) or names a pre-release or
  development version (e.g., `>=0.17.0rc1`), or a local version (e.g.,
  `==0.17.0+gabc1234`, a build of a commit);
- the base image of the `Dockerfile` (`elixircloud/foca`) has no release
  tag (`<X.Y.Z>-py<X.Y>`, e.g., a dated development image);
- a dependency of the Helm chart (`deployment/Chart.yaml`) has another
  version than a release (`X.Y.Z`, e.g., a pre-release or a range), or is
  given in flow style (`{...}`, `[...]`), which the check can't read.

Usage: `python scripts/check_release_pins.py [<repository root>]`.

Only the stdlib is used, so the workflow runs it without installing the app.
"""

from collections.abc import Sequence
from pathlib import Path
import re
import sys
import tomllib

# A release version `X.Y.Z` (no pre-release or build metadata)
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
# The name of a requirement (PEP 508), with extras (`foca[x]`)
NAME = r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?"
# A requirement that is a direct reference, e.g., `foca @ git+https://...`
DIRECT_REFERENCE = re.compile(NAME + r"\s*@")
# A pre-release or development version (PEP 440) in a version specifier,
# e.g., `0.17.0rc1`, `1.0.dev0`
PRE_RELEASE = re.compile(
    r"[0-9][._-]?(a|b|c|rc|alpha|beta|pre|preview|dev)[0-9]*(?![a-z])",
    re.IGNORECASE,
)
# A local version label (PEP 440) in a version specifier, e.g.,
# `0.17.0+gabc1234`
LOCAL_VERSION = re.compile(r"[0-9]\+[A-Za-z0-9]")
# The base image, with its tag
BASE_IMAGE = re.compile(r"^FROM\s+elixircloud/foca:(?P<tag>[^@\s]+)", re.MULTILINE)
RELEASE_IMAGE_TAG = re.compile(SEMVER + r"-py3\.[0-9]+")
# A chart dependency's version (`version: <version>`, in a list item of
# `dependencies`), up to a comment; the chart is read line by line, without a
# YAML parser
CHART_VERSION = re.compile(
    r"^\s*(?:-\s+)?version:\s*[\"']?(?P<version>.*?)[\"']?\s*(?:#.*)?$"
)
RELEASE_CHART_VERSION = re.compile(SEMVER)


def _chart_dependency_versions(path: Path) -> list[str | None]:
    """Get the versions of the dependencies of a chart.

    The `version` keys of the `dependencies` section, i.e., of the lines
    after the line `dependencies:` that are indented or list items, up to the
    next top-level key.

    Args:
        path: The chart's `Chart.yaml`.

    Returns:
        The versions, in order; `None` for dependencies in flow style
        (`{...}`, `[...]`), which aren't read.
    """
    versions: list[str | None] = []
    in_dependencies = False
    for line in path.read_text().splitlines():
        if line and line[0] not in " \t-#":
            key, _, value = line.split("#", 1)[0].partition(":")
            in_dependencies = key.strip() == "dependencies"
            if in_dependencies and value.strip():
                versions.append(None)
        elif in_dependencies and re.match(r"\s*(-\s*)?[{\[]", line):
            versions.append(None)
        elif in_dependencies and (match := CHART_VERSION.match(line)):
            versions.append(match["version"])
    return versions


def find_dev_pins(root: Path) -> list[str]:
    """Find pins to development versions.

    Args:
        root: Root directory of the repository.

    Returns:
        A description of each pin to a development version.
    """
    pins = []
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    requirements = [
        *project.get("dependencies", []),
        *(
            requirement
            for extra in project.get("optional-dependencies", {}).values()
            for requirement in extra
        ),
    ]
    for requirement in requirements:
        # The version specifiers, without name, extras and markers
        specifier = re.sub(NAME, "", requirement).split(";")[0]
        if DIRECT_REFERENCE.match(requirement):
            pins.append(
                f"pyproject.toml: dependency '{requirement}' is a direct reference"
            )
        elif PRE_RELEASE.search(specifier):
            pins.append(
                f"pyproject.toml: dependency '{requirement}' names a pre-release"
            )
        elif LOCAL_VERSION.search(specifier):
            pins.append(
                f"pyproject.toml: dependency '{requirement}' names a local version"
            )
    match = BASE_IMAGE.search((root / "Dockerfile").read_text())
    if match is None:
        pins.append("Dockerfile: no elixircloud/foca base image")
    elif not RELEASE_IMAGE_TAG.fullmatch(match["tag"]):
        pins.append(
            f"Dockerfile: base image tag '{match['tag']}' is not a release tag "
            "(<X.Y.Z>-py<X.Y>)"
        )
    for version in _chart_dependency_versions(root / "deployment" / "Chart.yaml"):
        if version is None:
            pins.append(
                "deployment/Chart.yaml: dependencies in flow style can't be checked"
            )
        elif not RELEASE_CHART_VERSION.fullmatch(version):
            pins.append(
                f"deployment/Chart.yaml: dependency version '{version}' is not "
                "a release version"
            )
    return pins


def main(argv: Sequence[str] | None = None) -> int:
    """Check the pins of the repository.

    Args:
        argv: Command line arguments, without the program name: optionally,
            the root directory of the repository (default: the parent of this
            script's directory).

    Returns:
        The exit status: 0 if all pins are releases, 1 otherwise.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    pins = find_dev_pins(root)
    for pin in pins:
        print(
            f"::error::{pin}: pin the released version before releasing",
            file=sys.stderr,
        )
    return 1 if pins else 0


if __name__ == "__main__":
    sys.exit(main())
