"""Tests of the MkDocs hooks (`docs/hooks.py`)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/elixir-cloud-aai/protes"
# The repository's name before the rename to `protes` (GitHub ignores the case)
MIXED_CASE_REPO_URL = "https://github.com/elixir-cloud-aai/proTES"
DOCS_URL = "https://protes.readthedocs.io/en/latest/"

# `docs/` is not a package, so the hooks are loaded from their file
_spec = importlib.util.spec_from_file_location("docs_hooks", ROOT / "docs/hooks.py")
assert _spec and _spec.loader
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("#names-and-labels", "#names-and-labels"),
        ("https://helm.sh/docs/", "https://helm.sh/docs/"),
        ("mailto:admin@example.org", "mailto:admin@example.org"),
        (f"{DOCS_URL}configuration/#server", "configuration.md#server"),
        (f"{DOCS_URL}releases/1.2.3/", "releases/1.2.3.md"),
        (f"{DOCS_URL}operations", "operations.md"),
        (DOCS_URL, "index.md"),
        (f"{DOCS_URL}#contact", "index.md#contact"),
        # Only links to branch `dev` are pointed to the given reference
        (
            f"{REPO_URL}/blob/v1.2.3/pro_tes/config.yaml",
            f"{REPO_URL}/blob/v1.2.3/pro_tes/config.yaml",
        ),
        (f"{REPO_URL}/releases", f"{REPO_URL}/releases"),
        # Relative links are left as they are, so that the strict build fails
        ("values.yaml", "values.yaml"),
    ],
)
def test_adapt_link(target: str, expected: str) -> None:
    """Links are adapted to pages of the docs, or to files on GitHub."""
    assert hooks.adapt_link(target, f"{REPO_URL}/") == expected


@pytest.mark.parametrize(
    ("link_repo_url", "config_repo_url"),
    [
        (MIXED_CASE_REPO_URL, REPO_URL),
        (REPO_URL, MIXED_CASE_REPO_URL),
    ],
)
def test_adapt_link_case(link_repo_url: str, config_repo_url: str) -> None:
    """Links match the repository's URL in any case, and get the configured."""
    link = f"{link_repo_url}/blob/dev/deployment/values.yaml"
    assert (
        hooks.adapt_link(link, config_repo_url, "v1")
        == f"{config_repo_url}/blob/v1/deployment/values.yaml"
    )


@pytest.mark.parametrize(
    "target",
    [
        f"{REPO_URL}/blob/DEV/deployment/values.yaml",
        f"{REPO_URL}-other/blob/dev/deployment/values.yaml",
    ],
)
def test_adapt_link_other(target: str) -> None:
    """Links to other branches or repositories are left as they are."""
    assert hooks.adapt_link(target, REPO_URL, "v1") == target


@pytest.mark.parametrize("repo_url", [REPO_URL, f"{REPO_URL}/"])
def test_adapt_link_ref(repo_url: str) -> None:
    """Links to files on GitHub point to the given Git reference."""
    assert (
        hooks.adapt_link(
            f"{REPO_URL}/blob/dev/deployment/values.yaml", repo_url, "0123abc"
        )
        == f"{REPO_URL}/blob/0123abc/deployment/values.yaml"
    )


def test_adapt_links_skips_code_blocks() -> None:
    """Links in fenced code blocks are left as they are."""
    link = f"{DOCS_URL}configuration/"
    markdown = f"See [a]({link}).\n\n```bash\necho '[b]({link})'\n```\n"
    assert hooks.adapt_links(markdown, REPO_URL).splitlines() == [
        "See [a](configuration.md).",
        "",
        "```bash",
        f"echo '[b]({link})'",
        "```",
    ]


def _is_docs(url: str) -> bool:
    """Tell whether a URL points to the docs of this repository.

    Args:
        url: URL to check.

    Returns:
        Whether the URL's host is that of the docs on Read the Docs, also if
        spelled with percent-encodings, in capitals or with a trailing dot.
    """
    host = unquote(urlsplit(url).hostname or "").rstrip(".")
    return host == urlsplit(DOCS_URL).hostname


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (DOCS_URL, True),
        ("https://protes.readthedocs.io./en/0.2.0/", True),
        ("https://protes.readthedocs%2Eio/en/0.2.0/", True),
        ("https://PROTES.readthedocs.io/en/0.2.0/", True),
        ("https://foca.readthedocs.io/en/latest/", False),
        ("https://protes.readthedocs.io.example.com/", False),
        ("https://example.com/protes.readthedocs.io/", False),
        ("#protes.readthedocs.io", False),
    ],
)
def test_is_docs(url: str, expected: bool) -> None:
    """Only URLs on the host of the docs count as links to the docs.

    Args:
        url: URL to check.
        expected: Whether the URL is on the host of the docs.
    """
    assert _is_docs(url) is expected


def test_readme_links_are_absolute() -> None:
    """The chart's README has no relative links (they break in the package)."""
    readme = (ROOT / "deployment/README.md").read_text(encoding="utf-8")
    targets = hooks.LINK.findall(readme)
    assert f"{DOCS_URL}configuration/" in targets
    assert [
        target for target in targets if not target.startswith(("#", "https://"))
    ] == []
    # Links to the docs are to the version that the hook adapts
    assert [
        target
        for target in targets
        if _is_docs(target) and not target.startswith(DOCS_URL)
    ] == []


def _render(markdown: str) -> str:
    """Run the hook on the Markdown of a page, with the repository's settings.

    Args:
        markdown: Markdown of the page.

    Returns:
        The Markdown returned by the hook.
    """
    config = SimpleNamespace(
        config_file_path=str(ROOT / "mkdocs.yml"), repo_url=REPO_URL
    )
    # the hooks are loaded from their file, so their types are unknown
    rendered: str = hooks.on_page_markdown(
        markdown, page=None, config=config, files=None
    )
    return rendered


def test_on_page_markdown_includes_readme(monkeypatch: pytest.MonkeyPatch) -> None:
    """The include line is replaced with the chart's README."""
    monkeypatch.delenv("READTHEDOCS_GIT_COMMIT_HASH", raising=False)
    markdown = _render(f"<!-- comment -->\n{hooks.INCLUDE}\n")
    assert markdown.startswith("<!-- comment -->\n# Helm chart\n")
    assert hooks.INCLUDE not in markdown
    assert f"]({DOCS_URL}" not in markdown
    assert "](configuration.md)" in markdown
    assert f"]({REPO_URL}/blob/dev/pro_tes/config.yaml)" in markdown


def test_on_page_markdown_read_the_docs_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Read the Docs, links to files point to the commit of the build."""
    monkeypatch.setenv("READTHEDOCS_GIT_COMMIT_HASH", "0123abc")
    markdown = _render(f"{hooks.INCLUDE}\n")
    assert f"]({REPO_URL}/blob/0123abc/pro_tes/config.yaml)" in markdown


def test_on_page_markdown_without_include() -> None:
    """Other pages are left as they are."""
    markdown = f"# Page\n\nSee `{hooks.INCLUDE}`.\n"
    assert _render(markdown) == markdown


def _version() -> str:
    """Return the version that the docs show for the current release.

    Returns:
        The `version` of `CITATION.cff`, or `UNRELEASED` before the first
        release.
    """
    version = hooks.citation.release_version(
        (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    )
    # The hooks are loaded from their file, so their types are unknown
    assert version is None or isinstance(version, str)
    return version or str(hooks.UNRELEASED)


def test_on_page_markdown_version() -> None:
    """The placeholder is the release version, also in code blocks."""
    version = _version()
    markdown = _render(
        "Version {{ version }}.\n\n```bash\ndocker pull x:{{ version }}\n```\n"
        "{{ json .SBOM }} {{version}}\n"
    )
    assert markdown == (
        f"Version {version}.\n\n```bash\ndocker pull x:{version}\n```\n"
        "{{ json .SBOM }} {{version}}\n"
    )


@pytest.mark.parametrize(
    ("citation", "expected"),
    [("cff-version: 1.2.0\n", "X.Y.Z"), ('version: "1.2.3rc1"\n', "1.2.3-rc1")],
)
def test_on_page_markdown_citation(
    tmp_path: Path, citation: str, expected: str
) -> None:
    """The version is that of `CITATION.cff`; without one, `UNRELEASED`.

    Args:
        tmp_path: Directory of the docs settings and the citation file.
        citation: Content of the citation file.
        expected: Expected chart version.
    """
    (tmp_path / "CITATION.cff").write_text(citation, encoding="utf-8")
    config = SimpleNamespace(
        config_file_path=str(tmp_path / "mkdocs.yml"), repo_url=REPO_URL
    )
    markdown = hooks.on_page_markdown(
        "{{ chart_version }}", page=None, config=config, files=None
    )
    assert markdown == expected


def test_on_page_markdown_readme_version() -> None:
    """The placeholder is also replaced in the included chart README."""
    readme = (ROOT / "deployment/README.md").read_text(encoding="utf-8")
    assert "{{ version }}" in readme
    markdown = _render(f"{hooks.INCLUDE}\n")
    assert "{{ version }}" not in markdown
    assert f"protes:{_version()}`" in markdown


@pytest.mark.parametrize(
    ("markdown", "version", "expected"),
    [
        ("a {{ version }} b {{ version }}", "1.2.3", "a 1.2.3 b 1.2.3"),
        ("{{ version }} {{ chart_version }}", "1.2.3rc1", "1.2.3rc1 1.2.3-rc1"),
        ("only {{ chart_version }}", "1.2.3b1", "only 1.2.3-b1"),
        ("no placeholder", None, "no placeholder"),
        # Before the first release, the placeholders become `UNRELEASED`
        ("{{ version }} {{ chart_version }}", None, "X.Y.Z X.Y.Z"),
    ],
)
def test_substitute_version(markdown: str, version: str | None, expected: str) -> None:
    """The placeholder is replaced; without it, no version is needed.

    Args:
        markdown: Markdown of a page.
        version: Release version.
        expected: Expected Markdown.
    """
    assert hooks.substitute_version(markdown, version) == expected
