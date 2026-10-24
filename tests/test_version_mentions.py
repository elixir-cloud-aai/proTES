"""Tests that the current release version is not hard-coded (drift guard).

The release version has one source (see "Versioning and releases" in
`docs/development.md`): the release tag and, on `dev`, the `version` of
`CITATION.cff`. The docs and the chart's README write it as `{{ version }}`
(replaced by `docs/hooks.py` and `scripts/chart_readme.py`); comments,
templates and tests use placeholders (e.g., `1.2.3`) and refer to sections by
anchor. So the current release version may only appear in the files and lines
of the allowlist below, which are about that release itself, not about the
latest one.
"""

import importlib.util
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

# `scripts/` is not a package, so the script is loaded from its file
_spec = importlib.util.spec_from_file_location("citation", ROOT / "scripts/citation.py")
assert _spec and _spec.loader
citation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(citation)
_spec = importlib.util.spec_from_file_location(
    "chart_readme", ROOT / "scripts/chart_readme.py"
)
assert _spec and _spec.loader
chart_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chart_readme)

# Files that may name the release version: its source, the release notes,
# and the tests of the release tooling, whose versions are test data (if one
# is the release version, it means no other)
ALLOWED_FILES = (
    "CITATION.cff",
    "tests/test_chart_readme.py",
    "tests/test_check_release_pins.py",
    "tests/test_citation.py",
    "tests/test_docs_hooks.py",
    "tests/test_release_notes.py",
    "tests/test_release_version.py",
    "tests/test_version_mentions.py",
)
ALLOWED_DIRS = ("docs/releases/",)
# Mentions of the version that refer to a release of the app (`{version}` is
# the escaped version, `{end}` the end of a version): the tag, the image and
# chart, the version in prose or in inline code. Other mentions of the same
# number, e.g., the version of the GA4GH specification or of a dependency,
# are not about the app's release
MENTIONS = (
    r"(?:^|[\s`(\"']|refs/tags/|/en/|releases/(?:tag|download)/|\.\.\."
    r"|(?i:proTES)/(?:blob|tree|compare|commits)/)v{version}{end}",
    r"(?i:pro[-_ ]?tes)(?:[:\- @]|==)v?{version}{end}",
    r"(?i:\b(?:release|version)s?)\s+v?{version}{end}",
    r"`v?{version}`",
    r"(?<![0-9.]){version}{end} (?:on|or later|and later)\b",
    r"(?i:\bsince)\s+v?{version}{end}",
    r"(?:\btag|appVersion|_VERSION)[\"']?\s*[:=]\s*[\"']?v?{version}{end}",
)
# Parts of lines that may name it (the rest of the line is still checked):
# links to the release notes (also in `mkdocs.yml`
# and the changelog) and to its assets, and migration headings, which are
# about that release itself
ALLOWED_LINES = (
    r"releases/{version}(?:\.md|/)",
    r"releases/download/v{version}/\S*",
    r"^#{{1,6}} (?:From versions before|To (?:app )?version) {version}$",
)


def mentions(text: str, version: str) -> list[tuple[int, str]]:
    """Return the lines that mention a release version, unless allowed.

    Args:
        text: Content of a file.
        version: Release version, e.g., `1.2.3`.

    Returns:
        Number (from 1) and text of each line that mentions the version, or
        its chart version (e.g., `1.2.3-rc1`), as a release of the app
        (`MENTIONS`; not as part of another version, e.g., `11.2.3`, `1.2.30`
        or `1.2.3rc1`), outside the parts that `ALLOWED_LINES` allow.
    """
    found, allowed = [], []
    for form in {version, chart_readme.chart_version(version)}:
        fields = {"version": re.escape(form), "end": r"(?!\.?[0-9]|-?(?:a|b|rc)[0-9])"}
        found += [re.compile(pattern.format(**fields)) for pattern in MENTIONS]
        allowed += [re.compile(pattern.format(**fields)) for pattern in ALLOWED_LINES]
    result = []
    for number, line in enumerate(text.splitlines(), start=1):
        checked = line
        for pattern in allowed:
            checked = pattern.sub(" ", checked)
        if any(pattern.search(checked) for pattern in found):
            result.append((number, line))
    return result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("docker pull protes:1.2.3", [(1, "docker pull protes:1.2.3")]),
        ("a\n--source-ref refs/tags/v1.2.3", [(2, "--source-ref refs/tags/v1.2.3")]),
        ("Release 1.2.3.", [(1, "Release 1.2.3.")]),
        ("since app version 1.2.3,", [(1, "since app version 1.2.3,")]),
        ("proTES 1.2.3", [(1, "proTES 1.2.3")]),
        ("protes-1.2.3.tgz", [(1, "protes-1.2.3.tgz")]),
        ("(e.g., `1.2.3`)", [(1, "(e.g., `1.2.3`)")]),
        ("from 1.2.3 on", [(1, "from 1.2.3 on")]),
        ("1.2.3 or later", [(1, "1.2.3 or later")]),
        ("/en/v1.2.3/", [(1, "/en/v1.2.3/")]),
        ("Tag v1.2.3", [(1, "Tag v1.2.3")]),
        ("  tag: 1.2.3", [(1, "  tag: 1.2.3")]),
        ('appVersion: "1.2.3"', [(1, 'appVersion: "1.2.3"')]),
        ("X_VERSION=1.2.3", [(1, "X_VERSION=1.2.3")]),
        ("Since 1.2.3, x", [(1, "Since 1.2.3, x")]),
        (
            "releases/download/v1.2.3/protes-1.2.3.tgz protes:1.2.3",
            [
                (
                    1,
                    "releases/download/v1.2.3/protes-1.2.3.tgz protes:1.2.3",
                )
            ],
        ),
        ("protes-1.2.3rc1.tgz", []),
        ("x/protes/tree/v1.2.3/a", [(1, "x/protes/tree/v1.2.3/a")]),
        ("pro_tes-1.2.3.dist-info", [(1, "pro_tes-1.2.3.dist-info")]),
        ("pro_tes==1.2.3 protes@1.2.3", [(1, "pro_tes==1.2.3 protes@1.2.3")]),
        ("x/compare/v1.2.2...v1.2.3", [(1, "x/compare/v1.2.2...v1.2.3")]),
        ("x/proTES/blob/v1.2.3/a", [(1, "x/proTES/blob/v1.2.3/a")]),
        # Other versions, and the number not as a release of the app
        ("v11.2.3 v1.2.30 v1.2.3.4 protes:11.2.3", []),
        ("foca/blob/chart-v1.2.3/docs x.org/spec/v1.2.3/schema", []),
        ("version: 1.2.3", []),
        ("openapi: 1.2.3", []),
        ("Until 1.2.3, all releases are pre-releases", []),
        ("{{ version }}", []),
        # Allowed lines
        ("- [1.2.3](releases/1.2.3.md)", []),
        ('      - "1.2.3": releases/1.2.3.md', []),
        ("See [notes](https://x.example.org/en/latest/releases/1.2.3/).", []),
        ("## From versions before 1.2.3", []),
        ("### To app version 1.2.3", []),
        ("x/releases/download/v1.2.3/protes-1.2.3.tgz", []),
        ("See the [notes of 1.2.3](releases/1.2.3.md)", []),
        # Only the allowed part of a line is skipped
        (
            "Release 1.2.3 (see [notes](releases/1.2.3.md))",
            [(1, "Release 1.2.3 (see [notes](releases/1.2.3.md))")],
        ),
    ],
)
def test_mentions(text: str, expected: list[tuple[int, str]]) -> None:
    """Lines naming the version are found, unless they are allowed.

    Args:
        text: Content of a file.
        expected: Expected lines.
    """
    assert mentions(text, "1.2.3") == expected


def test_mentions_chart_version() -> None:
    """Pre-releases are also found in their chart version (SemVer form)."""
    text = "protes-1.2.3-rc1.tgz\nTag v1.2.3rc1\nprotes:1.2.3rc10"
    assert mentions(text, "1.2.3rc1") == [
        (1, "protes-1.2.3-rc1.tgz"),
        (2, "Tag v1.2.3rc1"),
    ]


def _tracked_files() -> list[str]:
    """Return the files tracked by Git, relative to the repository root.

    Returns:
        The paths, as `git ls-files` lists them.
    """
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs a Git checkout")
    listed = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    return [name for name in listed.stdout.decode().split("\0") if name]


def test_release_version_not_hard_coded() -> None:
    """The current release version appears only where the allowlist allows."""
    version = citation.release_version(
        (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    )
    if version is None:
        pytest.skip("CITATION.cff has no version (no release yet)")
    found = []
    for name in _tracked_files():
        if name in ALLOWED_FILES or name.startswith(ALLOWED_DIRS):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [
            f"{name}:{number}: {line}" for number, line in mentions(text, version)
        ]
    assert found == [], (
        f"The release version {version} is hard-coded; use {{{{ version }}}} in"
        " the docs and the chart's README, placeholders (e.g., 1.2.3) elsewhere,"
        " or extend the allowlist of this test if the line is about that"
        " release itself:\n" + "\n".join(found)
    )
