"""Point the links of the chart's README to the docs and files of a release.

`deployment/README.md` links to the latest docs on Read the Docs (`DOCS`) and
to files on branch `dev` (`FILES`), which is right for the repository and the
docs (`docs/hooks.py`), but not for the README packaged with a released chart:
its users would read docs of newer versions. The release workflow
(`.github/workflows/release.yml`) runs this script on a copy of the chart
before packaging it: it rewrites these links to the release's docs and files
(`/en/v<version>/`, `blob/v<version>/`; Read the Docs builds version tags),
and fails with GitHub annotations on standard error if any link to a version
that moves (`UNVERSIONED`, e.g., the latest docs or branch `dev`) is left.
It also replaces the placeholders of the release version, `{{ version }}`
(`VARIABLE`, the tag without `v`, e.g., `1.2.3rc1`), and of the chart version,
`{{ chart_version }}` (`CHART_VARIABLE`, the SemVer form, e.g., `1.2.3-rc1`,
as in the names of the packaged charts), which the docs hooks
(`docs/hooks.py`) replace with the version of `CITATION.cff`. Other files
of the chart have no such links and are packaged unchanged. See "Versioning
and releases" in `docs/development.md`.

Usage: `python scripts/chart_readme.py <readme> <tag>`, with the release tag,
e.g., `v1.2.3`. Only the stdlib is used, so the workflow runs it without
installing the app.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import re
import sys

# Docs on Read the Docs and repository on GitHub (adapt them in apps made from
# this one)
DOCS_HOST = "protes.readthedocs.io"
REPOSITORY = "elixir-cloud-aai/proTES"
# Links that are rewritten, without the version
DOCS = f"https://{DOCS_HOST}/en/"
FILES = f"https://github.com/{REPOSITORY}/blob/"

# Placeholders of the release version and of the chart version (also used
# by `docs/hooks.py`)
VARIABLE = "{{ version }}"
CHART_VARIABLE = "{{ chart_version }}"
# Start of the pre-release part of a version (e.g., `rc1` of `1.2.3rc1`)
PRE_RELEASE = re.compile(r"(?<=[0-9])(?=(?:a|b|rc)[0-9]+$)", re.ASCII)

# Links to versions that move (also forms that aren't rewritten, `stable`,
# branch `main` and the latest release), which must not be left in the
# packaged README
_MOVING = r"(?![\w.-])"
UNVERSIONED = re.compile(
    rf"{re.escape(DOCS_HOST)}/(?:en/)?(?:latest|stable){_MOVING}"
    rf"|github\.com/{re.escape(REPOSITORY)}/"
    rf"(?:blob|tree|raw|edit|blame|commits)/(?:refs/heads/)?(?:dev|main|HEAD)"
    rf"{_MOVING}"
    rf"|github\.com/{re.escape(REPOSITORY)}/releases/latest{_MOVING}"
    rf"|raw\.githubusercontent\.com/{re.escape(REPOSITORY)}/"
    rf"(?:refs/heads/)?(?:dev|main|HEAD){_MOVING}",
    re.ASCII | re.IGNORECASE,
)
# Guard against tags that would change the paths of the links (e.g., with
# `/`); the release workflow checks the tag first (`release_version.py`)
SAFE_TAG = re.compile(r"v[0-9]+(?:\.[0-9]+)*[0-9a-z]*", re.ASCII)


def chart_version(version: str) -> str:
    """Return the chart version of a release version.

    Args:
        version: Release version, e.g., `1.2.3` or `1.2.3rc1`.

    Returns:
        The version in SemVer form, as the chart versions of releases (see
        `scripts/release_version.py`), e.g., `1.2.3` or `1.2.3-rc1`.
    """
    return PRE_RELEASE.sub("-", version, count=1)


def substitute(markdown: str, version: str) -> str:
    """Replace the placeholders of the release and chart versions.

    Args:
        markdown: Markdown, e.g., of the chart's README.
        version: Release version, e.g., `1.2.3rc1`.

    Returns:
        The Markdown with `version` in place of `VARIABLE` and its chart
        version in place of `CHART_VARIABLE`.
    """
    return markdown.replace(VARIABLE, version).replace(
        CHART_VARIABLE, chart_version(version)
    )


def version_links(markdown: str, tag: str) -> str:
    """Return Markdown with its links pointed to the docs and files of a tag.

    Args:
        markdown: Markdown, e.g., of the chart's README.
        tag: Git tag of the release, e.g., `v1.2.3`.

    Returns:
        The Markdown with links to the latest docs (`DOCS` + `latest/`) and to
        files on branch `dev` (`FILES` + `dev/`) pointed to `tag`, and the
        placeholders of the release and chart versions replaced (see
        `substitute`), also in code blocks; other links unchanged.
    """
    linked = markdown.replace(f"{DOCS}latest/", f"{DOCS}{tag}/").replace(
        f"{FILES}dev/", f"{FILES}{tag}/"
    )
    return substitute(linked, tag.removeprefix("v"))


def unversioned_links(markdown: str) -> list[tuple[int, str]]:
    """Return the lines with links to versions that move (e.g., `latest`).

    Args:
        markdown: Markdown, e.g., of the chart's README.

    Returns:
        Number (from 1) and text of each line with a link to a version that
        moves (`UNVERSIONED`).
    """
    return [
        (number, line)
        for number, line in enumerate(markdown.splitlines(), start=1)
        if UNVERSIONED.search(line)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Point the links of a README to a release (see the module's docstring).

    Args:
        argv: Command-line arguments, without the program name; those of the
            process if `None`.

    Returns:
        The exit status: 0 on success, 1 if the tag is not a version tag (the
        README is left unchanged) or links to versions that move are left.
    """
    parser = argparse.ArgumentParser(
        description="Point the links of the chart's README to a release."
    )
    parser.add_argument("readme", type=Path, help="README to rewrite in place")
    parser.add_argument("tag", help="release tag, e.g., v1.2.3")
    args = parser.parse_args(argv)
    if not SAFE_TAG.fullmatch(args.tag):
        print(f"::error::Tag '{args.tag}' is not a version tag", file=sys.stderr)
        return 1
    readme: Path = args.readme
    markdown = version_links(readme.read_text(encoding="utf-8"), args.tag)
    readme.write_text(markdown, encoding="utf-8")
    left = unversioned_links(markdown)
    for number, line in left:
        print(
            f"::error file={readme},line={number}::Link to a version that moves"
            f" (e.g., latest docs, branch dev) left in the packaged README:"
            f" {line.strip()}",
            file=sys.stderr,
        )
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
