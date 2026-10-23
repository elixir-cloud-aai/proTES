"""Derive the version and the kind of a release from its tag.

Used by the release workflow (`.github/workflows/release.yaml`); prints
`GITHUB_OUTPUT` lines (`<name>=<value>`) to standard output, and errors as
GitHub annotations to standard error. See "Versioning and releases" in
`docs/development.md`.

Commands:
    `outputs <tag>`: the version (`version`), the chart version
        (`chart_version`), whether the release is stable (`stable`), and
        whether it is a GitHub pre-release (`prerelease`).
    `latest <tag>`: whether the image of the release is tagged `latest`
        (`latest`), given the tags on `main`, one per line, on standard input.

Only the stdlib is used, so the workflow runs it without installing the app.
"""

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import re
import sys

# Canonical release versions only (no leading zeros), as setuptools-scm would
# normalize them: `vX.Y.Z`, or `vX.Y.ZaN`, `vX.Y.ZbN` and `vX.Y.ZrcN` for
# alpha, beta and release candidate versions
TAG = re.compile(
    r"v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?P<pre>(?:a|b|rc)(?:0|[1-9][0-9]*))?",
    re.ASCII,
)


@dataclass(frozen=True)
class Release:
    """Version and kind of a release.

    Attributes:
        version: Version of the app (the tag without the `v`), e.g.,
            `1.2.3rc1`.
        chart_version: Version of the Helm chart, which needs SemVer: e.g.,
            `1.2.3rc1` becomes `1.2.3-rc1`.
        stable: Whether the release is not an alpha, beta or release
            candidate version (only stable releases can become `latest`).
        prerelease: Whether the release is a GitHub pre-release: alpha, beta
            and release candidate versions, and all releases before 1.0.0.
        number: Major, minor and patch number, to order stable releases.
    """

    version: str
    chart_version: str
    stable: bool
    prerelease: bool
    number: tuple[int, int, int]

    def outputs(self) -> list[str]:
        """Return the outputs of the release as `GITHUB_OUTPUT` lines."""
        return [
            f"version={self.version}",
            f"chart_version={self.chart_version}",
            f"stable={str(self.stable).lower()}",
            f"prerelease={str(self.prerelease).lower()}",
        ]


def parse_tag(tag: str) -> Release:
    """Return the release of a tag.

    Args:
        tag: Git tag, e.g., `v1.2.3` or `v1.2.3rc1`.

    Returns:
        The version and kind of the release.

    Raises:
        ValueError: If the tag is not a canonical release version.
    """
    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError(
            f"Tag '{tag}' is not a release version (e.g., v1.2.3 or v1.2.3rc1)"
        )
    number = (int(match["major"]), int(match["minor"]), int(match["patch"]))
    base = ".".join(map(str, number))
    pre = match["pre"]
    return Release(
        version=tag.removeprefix("v"),
        chart_version=f"{base}-{pre}" if pre else base,
        stable=pre is None,
        prerelease=pre is not None or number[0] == 0,
        number=number,
    )


def is_latest(tag: str, tags: Iterable[str]) -> bool:
    """Return whether the image of a release is tagged `latest`.

    Args:
        tag: Git tag of the release.
        tags: Git tags on `main`; tags that are no stable release versions are
            ignored.

    Returns:
        Whether the release is stable and the highest stable release among
        `tags`.

    Raises:
        ValueError: If `tag` is not a canonical release version.
    """
    release = parse_tag(tag)
    if not release.stable:
        return False
    numbers = set()
    for other in tags:
        try:
            other_release = parse_tag(other)
        except ValueError:
            continue
        if other_release.stable:
            numbers.add(other_release.number)
    return release.number in numbers and release.number == max(numbers)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the outputs of a command (see the module's docstring).

    Args:
        argv: Command-line arguments, without the program name; those of the
            process if `None`.

    Returns:
        The exit status: 0 on success, 1 if the tag is not a release version.
    """
    parser = argparse.ArgumentParser(
        description="Derive the version and the kind of a release from its tag."
    )
    parser.add_argument("command", choices=["outputs", "latest"])
    parser.add_argument("tag", help="release tag, e.g., v1.2.3 or v1.2.3rc1")
    args = parser.parse_args(argv)
    # Undecodable bytes are replaced, so that tags with them are ignored as
    # invalid tags (instead of failing with a `UnicodeDecodeError`)
    tags = (
        sys.stdin.buffer.read().decode("utf-8", "replace").splitlines()
        if args.command == "latest"
        else []
    )
    try:
        if args.command == "outputs":
            lines = parse_tag(args.tag).outputs()
        else:
            latest = is_latest(args.tag, tags)
            lines = [f"latest={str(latest).lower()}"]
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
