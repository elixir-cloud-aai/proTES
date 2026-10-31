"""Tests of the versioning of the chart's README (`scripts/chart_readme.py`)."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = "https://protes.readthedocs.io/en"
REPO = "https://github.com/elixir-cloud-aai/protes"
# The repository's name before the rename to `protes` (GitHub ignores the case)
MIXED_CASE_REPO = "https://github.com/elixir-cloud-aai/proTES"

SCRIPT = ROOT / "scripts/chart_readme.py"

# `scripts/` is not a package, so the script is loaded from its file
_spec = importlib.util.spec_from_file_location("chart_readme", SCRIPT)
assert _spec and _spec.loader
chart_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chart_readme)


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        (
            f"See [Server]({DOCS}/latest/configuration/#server).",
            f"See [Server]({DOCS}/v1.2.3rc1/configuration/#server).",
        ),
        (f"[Docs]({DOCS}/latest/)", f"[Docs]({DOCS}/v1.2.3rc1/)"),
        (
            f"[config]({REPO}/blob/dev/pro_tes/config.yaml)",
            f"[config]({REPO}/blob/v1.2.3rc1/pro_tes/config.yaml)",
        ),
        # The repository's name in any case, which is kept
        (
            f"[config]({MIXED_CASE_REPO}/blob/dev/pro_tes/config.yaml)",
            f"[config]({MIXED_CASE_REPO}/blob/v1.2.3rc1/pro_tes/config.yaml)",
        ),
        # Only the repository's name is matched in any case
        (f"{REPO}/blob/DEV/a", f"{REPO}/blob/DEV/a"),
        # Several links per line, also in code blocks (the packaged README is
        # not rendered by the docs)
        (
            f"[a]({DOCS}/latest/a/) [b]({REPO}/blob/dev/b)\n```\n{DOCS}/latest/\n```",
            f"[a]({DOCS}/v1.2.3rc1/a/) [b]({REPO}/blob/v1.2.3rc1/b)\n"
            f"```\n{DOCS}/v1.2.3rc1/\n```",
        ),
        # The placeholders get the release version (without `v`) and the
        # chart version; other `{{ ... }}` (e.g., of Helm templates) are left
        (
            f"{REPO}/releases/download/v{{{{ version }}}}/"
            "protes-{{ chart_version }}.tgz {{ .Values.x }} {{version}}",
            f"{REPO}/releases/download/v1.2.3rc1/"
            "protes-1.2.3-rc1.tgz {{ .Values.x }} {{version}}",
        ),
        # Other links are left as they are
        (f"[Releases]({REPO}/releases)", f"[Releases]({REPO}/releases)"),
        (
            f"{REPO}/releases/download/vX.Y.Z/protes-X.Y.Z.tgz",
            f"{REPO}/releases/download/vX.Y.Z/protes-X.Y.Z.tgz",
        ),
        (
            "[Helm](https://helm.readthedocs.io/en/latest/)",
            "[Helm](https://helm.readthedocs.io/en/latest/)",
        ),
        (
            "[FOCA](https://github.com/elixir-cloud-aai/foca/blob/dev/README.md)",
            "[FOCA](https://github.com/elixir-cloud-aai/foca/blob/dev/README.md)",
        ),
        ("[Server](#server)", "[Server](#server)"),
    ],
)
def test_version_links(markdown: str, expected: str) -> None:
    """Links to the latest docs and to branch `dev` point to the release."""
    assert chart_readme.version_links(markdown, "v1.2.3rc1") == expected


@pytest.mark.parametrize(
    "markdown",
    [
        f"[a]({DOCS}/latest/)",
        f"{DOCS}/latest",
        "http://protes.readthedocs.io/en/latest/",
        "https://protes.readthedocs.io/latest/",
        "https://PROTES.readthedocs.io/en/latest/",
        f"[a]({REPO}/blob/dev/README.md)",
        f"{REPO}/tree/dev/deployment",
        f"{REPO}/raw/dev/deployment/values.yaml",
        f"{REPO}/blob/dev",
        f"{DOCS}/stable/",
        f"{REPO}/blob/main/README.md",
        f"{REPO}/blob/HEAD/README.md",
        f"{REPO}/edit/dev/README.md",
        f"{REPO}/commits/dev",
        f"{REPO}/blame/main/README.md",
        f"{REPO}/blob/refs/heads/dev/README.md",
        f"{REPO}/releases/latest",
        f"{REPO}/releases/latest/download/protes.tgz",
        f"{MIXED_CASE_REPO}/blob/dev/README.md",
        f"{MIXED_CASE_REPO}/releases/latest",
        "https://raw.githubusercontent.com/elixir-cloud-aai/proTES/dev/x",
        "https://raw.githubusercontent.com/elixir-cloud-aai/protes/dev/x",
        "https://raw.githubusercontent.com/elixir-cloud-aai/protes/refs/heads/dev/x",
    ],
)
def test_unversioned_links(markdown: str) -> None:
    """Remaining links to the latest docs or to branch `dev` are found."""
    assert chart_readme.unversioned_links(f"Text\n{markdown}\n") == [(2, markdown)]


@pytest.mark.parametrize(
    "markdown",
    [
        f"[a]({DOCS}/v1.2.3/)",
        f"{DOCS}/latest-news/",
        f"{REPO}/blob/v1.2.3/README.md",
        f"{REPO}/blob/develop/README.md",
        f"{REPO}/releases",
        f"{REPO}/releases/download/v1.2.3/protes-1.2.3.tgz",
        "https://helm.readthedocs.io/en/latest/",
        "https://github.com/elixir-cloud-aai/foca/blob/dev/README.md",
        "The `dev` image and the latest release",
    ],
)
def test_unversioned_links_none(markdown: str) -> None:
    """Links to versions and to other sites are fine."""
    assert chart_readme.unversioned_links(markdown) == []


def test_chart_readme() -> None:
    """All links of the chart's README to the docs and files are versioned."""
    markdown = (ROOT / "deployment/README.md").read_text(encoding="utf-8")
    versioned = chart_readme.version_links(markdown, "v1.2.3")
    assert chart_readme.unversioned_links(markdown)
    assert chart_readme.unversioned_links(versioned) == []
    assert f"{DOCS}/v1.2.3/" in versioned
    assert f"{REPO}/blob/v1.2.3/" in versioned


def test_chart_readme_library_links() -> None:
    """Links of the chart's README to FOCA are those of its library chart.

    They point to the tag of the version of foca-common that the chart
    depends on (`chart-vX.Y.Z`), not to FOCA's latest docs or branches, which
    describe other versions (`version_links` doesn't rewrite them).
    """
    markdown = (ROOT / "deployment/README.md").read_text(encoding="utf-8")
    chart = yaml.safe_load((ROOT / "deployment/Chart.yaml").read_text())
    (library,) = chart["dependencies"]
    refs = re.findall(
        r"github\.com/elixir-cloud-aai/foca/(?:blob|tree|raw)/([^/\s)]+)", markdown
    )
    assert refs
    assert set(refs) == {f"chart-v{library['version']}"}
    assert "foca.readthedocs.io" not in markdown


def test_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The README is rewritten in place."""
    readme = tmp_path / "README.md"
    readme.write_text(f"[a]({DOCS}/latest/)\n[b]({REPO}/blob/dev/b)\n")
    assert chart_readme.main([str(readme), "v1.2.3"]) == 0
    assert readme.read_text() == (f"[a]({DOCS}/v1.2.3/)\n[b]({REPO}/blob/v1.2.3/b)\n")
    assert capsys.readouterr().err == ""


def test_main_unversioned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Links left unversioned fail with an error annotation per line."""
    readme = tmp_path / "README.md"
    readme.write_text(
        f"[a]({DOCS}/latest/)\n[b]({REPO}/tree/dev/b)\n"
        "[c](http://protes.readthedocs.io/en/latest/)\n"
    )
    assert chart_readme.main([str(readme), "v1.2.3"]) == 1
    # The other links are rewritten anyway
    assert readme.read_text().startswith(f"[a]({DOCS}/v1.2.3/)\n")
    message = (
        f"::error file={readme},line={{}}::Link to a version that moves (e.g.,"
        " latest docs, branch dev) left in the packaged README: {}\n"
    )
    assert capsys.readouterr().err == (
        message.format(2, f"[b]({REPO}/tree/dev/b)")
        + message.format(3, "[c](http://protes.readthedocs.io/en/latest/)")
    )


@pytest.mark.parametrize(
    "tag", ["1.2.3", "latest", "dev", "v1.2.3/../x", "v1..2", "v1.2.3/", ""]
)
def test_main_invalid_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tag: str
) -> None:
    """Tags that would change the paths of links are rejected."""
    readme = tmp_path / "README.md"
    readme.write_text(f"[a]({DOCS}/latest/)\n")
    assert chart_readme.main([str(readme), tag]) == 1
    assert readme.read_text() == f"[a]({DOCS}/latest/)\n"
    assert capsys.readouterr().err == f"::error::Tag '{tag}' is not a version tag\n"


@pytest.mark.parametrize(
    ("markdown", "tag", "returncode", "expected", "stderr"),
    [
        (f"[a]({DOCS}/latest/)\n", "v1.2.3rc1", 0, f"[a]({DOCS}/v1.2.3rc1/)\n", ""),
        (
            f"[a]({REPO}/tree/dev/a)\n",
            "v1.2.3",
            1,
            f"[a]({REPO}/tree/dev/a)\n",
            "::error file=README.md,line=1::Link to a version that moves (e.g.,"
            " latest docs, branch dev) left in the packaged README:"
            f" [a]({REPO}/tree/dev/a)\n",
        ),
        (
            f"[a]({DOCS}/latest/)\n",
            "1.2.3",
            1,
            f"[a]({DOCS}/latest/)\n",
            "::error::Tag '1.2.3' is not a version tag\n",
        ),
    ],
)
def test_script(
    tmp_path: Path,
    markdown: str,
    tag: str,
    returncode: int,
    expected: str,
    stderr: str,
) -> None:
    """The script runs as the release workflow calls it."""
    (tmp_path / "README.md").write_text(markdown)
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [sys.executable, "-I", str(SCRIPT), "README.md", tag],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        returncode,
        "",
        stderr,
    )
    assert (tmp_path / "README.md").read_text() == expected


def test_script_usage(tmp_path: Path) -> None:
    """Calls without a README and a tag fail with the usage."""
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [sys.executable, "-I", str(SCRIPT), "README.md"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr


@pytest.mark.parametrize(
    "tag", ["v1.2.3", "v0.10.0", "v1.2.3a1", "v1.2.3b2", "v1.2.3rc10"]
)
def test_chart_version(tag: str) -> None:
    """Chart versions are those of the release workflow (`release_version.py`).

    Args:
        tag: Release tag.
    """
    spec = importlib.util.spec_from_file_location(
        "release_version", ROOT / "scripts/release_version.py"
    )
    assert spec and spec.loader
    release_version = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_version)
    release = release_version.parse_tag(tag)
    assert chart_readme.chart_version(release.version) == release.chart_version
