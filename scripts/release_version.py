"""Derive the version and the kind of a release from its tag.

Used by the release workflow (`.github/workflows/release.yml`); prints
`GITHUB_OUTPUT` lines (`<name>=<value>`) to standard output, and errors as
GitHub annotations to standard error. See "Versioning and releases" in
`docs/development.md`.

Commands:
    `outputs <tag>`: the version (`version`), the chart version
        (`chart_version`), whether the release is stable (`stable`), and
        whether it is a GitHub pre-release (`prerelease`).
    `latest <tag>`: whether the image of the release is tagged `latest`
        (`latest`), given the tags on `main`, one per line, on standard input.
    `previous <tag> --published <file>`: the tag of the previous release
        (`previous_tag`, empty if there is none), after which GitHub's
        generated release notes start, given the tags in the history of the
        release commit, one per line, on standard input, and the tags of
        published releases, one per line, in `<file>` (only tags in both
        count). All of standard input is read, also when `<file>` is empty
        (the first release), so that the command writing it never fails
        with a broken pipe.

Only the stdlib is used, so the workflow runs it without installing the app.
"""

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import sys

# Canonical release versions only (no leading zeros), as setuptools-scm would
# normalize them: `vX.Y.Z`, or `vX.Y.ZaN`, `vX.Y.ZbN` and `vX.Y.ZrcN` for
# alpha, beta and release candidate versions
TAG = re.compile(
    r"v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:(?P<pre_kind>a|b|rc)(?P<pre_number>0|[1-9][0-9]*))?",
    re.ASCII,
)
# Kinds of pre-releases, in their order (stable releases come after them)
PRE_RELEASES = ("a", "b", "rc")


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
        order: Key to order all releases: the number, then the kind (alpha,
            beta and release candidate versions before the stable one) and
            the number of the pre-release.
    """

    version: str
    chart_version: str
    stable: bool
    prerelease: bool
    number: tuple[int, int, int]
    order: tuple[int, int, int, int, int]

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
    pre_kind = match["pre_kind"]
    pre = f"{pre_kind}{match['pre_number']}" if pre_kind else None
    kind = PRE_RELEASES.index(pre_kind) if pre_kind else len(PRE_RELEASES)
    return Release(
        version=tag.removeprefix("v"),
        chart_version=f"{base}-{pre}" if pre else base,
        stable=pre is None,
        prerelease=pre is not None or number[0] == 0,
        number=number,
        order=(*number, kind, int(match["pre_number"] or 0)),
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


def previous_tag(tag: str, tags: Iterable[str]) -> str | None:
    """Return the tag of the release before a release.

    Args:
        tag: Git tag of the release.
        tags: Git tags of the published releases in the history of the
            release commit; tags that are no release versions are ignored.

    Returns:
        For a stable release, the highest stable release below it, or, if
        there is none, the highest pre-release below it (as GitHub's
        generated notes can't start before the first tag); for an alpha,
        beta or release candidate version, the highest release of any kind
        below it; `None` if there is no release below it (the first
        release).

    Raises:
        ValueError: If `tag` is not a canonical release version.
    """
    release = parse_tag(tag)
    below = []
    for other in tags:
        try:
            other_release = parse_tag(other)
        except ValueError:
            continue
        if other_release.order < release.order:
            below.append((other_release.stable, other_release.order, other))
    if not below:
        return None
    if release.stable:
        # Stable ones first, if any
        return max(below)[2]
    return max(below, key=lambda candidate: candidate[1])[2]


def read_lines(data: bytes) -> list[str]:
    """Return the lines of a list of tags.

    Args:
        data: Tags, one per line, as UTF-8.

    Returns:
        The lines, split at line feeds only (as `git tag` and `gh` write
        them), so that other line breaks (e.g., U+2028) stay in a tag and
        make it invalid instead of yielding a valid-looking part of it;
        undecodable bytes are replaced, so that tags with them are ignored
        as invalid tags (instead of failing with a `UnicodeDecodeError`).
    """
    return data.decode("utf-8", "replace").split("\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Print the outputs of a command (see the module's docstring).

    Args:
        argv: Command-line arguments, without the program name; those of the
            process if `None`.

    Returns:
        The exit status: 0 on success, 1 if the tag is not a release version
        or the file of published tags can't be read.
    """
    parser = argparse.ArgumentParser(
        description="Derive the version and the kind of a release from its tag."
    )
    parser.add_argument("command", choices=["outputs", "latest", "previous"])
    parser.add_argument("tag", help="release tag, e.g., v1.2.3 or v1.2.3rc1")
    parser.add_argument(
        "--published",
        type=Path,
        metavar="FILE",
        help="tags of published releases, one per line (`previous` only)",
    )
    args = parser.parse_args(argv)
    if (args.command == "previous") != (args.published is not None):
        parser.error("--published is required for, and only for, `previous`")
    tags = (
        read_lines(sys.stdin.buffer.read())
        if args.command in {"latest", "previous"}
        else []
    )
    # `--published` is given exactly when the command is `previous` (above)
    if args.command == "previous":
        try:
            published = set(read_lines(args.published.read_bytes()))
        except OSError as error:
            print(f"::error::Cannot read published tags: {error}", file=sys.stderr)
            return 1
        tags = [tag for tag in tags if tag in published]
    try:
        if args.command == "outputs":
            lines = parse_tag(args.tag).outputs()
        elif args.command == "latest":
            latest = is_latest(args.tag, tags)
            lines = [f"latest={str(latest).lower()}"]
        else:
            lines = [f"previous_tag={previous_tag(args.tag, tags) or ''}"]
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
