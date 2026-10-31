"""MkDocs hooks: the release version, and the chart's README in the docs.

Pages write the current release version as `{{ version }}` (`VARIABLE` of
`scripts/chart_readme.py`), e.g., in commands such as
`docker pull elixircloud/protes:{{ version }}`, and the chart version
(its SemVer form, as in the names of packaged charts) as
`{{ chart_version }}`, so that they are not hard-coded in many places (see
"Versioning and releases" in `docs/development.md`): the hook replaces them
with the `version` of `CITATION.cff`, the latest release (or the upcoming
one, once its citation metadata is set; before the first release, it has
none, and the docs show `X.Y.Z` (`UNRELEASED`)), also in code blocks and in the
included chart README. Only these exact forms are replaced; other
`{{ ... }}`, e.g., of Helm templates, are left as they are. Snippets
(`--8<--` sections of `README.md`) are included after the hook ran, so they
can't use them.

The chart's `deployment/README.md` is kept next to the chart and is also
packaged with it, where relative links to other files of the repository don't
resolve, so its links are absolute: to pages of the docs (`DOCS_URL`, the
latest version on Read the Docs) and to files of the repository on GitHub (at
branch `dev`). This hook replaces the line `--8<-- "deployment/README.md"` in
pages with the README, with its links adapted to the docs: links to pages of
the docs become links to these pages, which MkDocs then checks, and links to
files on GitHub point to the commit that Read the Docs builds, so that the
files match the version of the docs. Only inline links (`[text](target)`)
outside fenced code blocks are adapted; use no other link forms in the README,
nor relative links (the strict build fails on them, as their targets aren't
found), and no nested fences (every line starting with ``` or ~~~ opens or
closes a block).
"""

import importlib.util
import os
from pathlib import Path
import re
from types import ModuleType
from typing import Protocol

INCLUDE = '--8<-- "deployment/README.md"'
README = Path("deployment/README.md")
# Base URL of the docs that the README links to
DOCS_URL = "https://protes.readthedocs.io/en/latest/"

CITATION = Path("CITATION.cff")
# Version that the docs show before the first release (`CITATION.cff` has no
# `version` until then)
UNRELEASED = "X.Y.Z"


def _load(name: str) -> ModuleType:
    """Load a script of `scripts/` (not a package) from its file.

    Args:
        name: Name of the script, without `.py`.

    Returns:
        The script, as a module.

    Raises:
        ImportError: If the script can't be loaded.
    """
    path = Path(__file__).resolve().parents[1] / f"scripts/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} can't be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


citation = _load("citation")
chart_readme = _load("chart_readme")
# Placeholders of the release and chart versions in pages
PLACEHOLDERS = (chart_readme.VARIABLE, chart_readme.CHART_VARIABLE)

# Targets of inline links (`[text](target)`)
LINK = re.compile(r"\]\((?P<target>[^)\s]+)\)")
# Opening and closing lines of fenced code blocks
FENCE = re.compile(r"^\s*(```|~~~)")


class Config(Protocol):
    """Settings of MkDocs that the hooks use (of `MkDocsConfig`).

    A protocol, so that the hooks are type-checked without MkDocs, which only
    the `docs` extra installs.

    Attributes:
        config_file_path: Path of `mkdocs.yml`.
        repo_url: URL of the repository on GitHub (set in `mkdocs.yml`).
    """

    config_file_path: str
    repo_url: str


def adapt_link(target: str, repo_url: str, ref: str = "dev") -> str:
    """Return the target of a link of the chart's README for the docs.

    Args:
        target: Link target in `deployment/README.md`.
        repo_url: URL of the repository on GitHub.
        ref: Git reference (branch, tag or commit) of files on GitHub.

    Returns:
        For pages of the docs (`DOCS_URL<page>/#<anchor>`), the page relative
        to the docs (`<page>.md#<anchor>`); for files of the repository on
        GitHub at branch `dev` (with the repository's name in any case), their
        URL at `ref`; other targets unchanged.
    """
    if target.startswith(DOCS_URL):
        page, hash_, anchor = target.removeprefix(DOCS_URL).partition("#")
        return f"{page.rstrip('/') or 'index'}.md{hash_}{anchor}"
    repo_url = repo_url.rstrip("/")
    blob = f"{repo_url}/blob/dev/"
    # GitHub ignores the case of the repository's name (e.g., `proTES` and
    # `protes`), so links match with it in any case
    if target[: len(blob)].lower() == blob.lower():
        return f"{repo_url}/blob/{ref}/{target[len(blob) :]}"
    return target


def adapt_links(markdown: str, repo_url: str, ref: str = "dev") -> str:
    """Adapt the inline links outside fenced code blocks (see `adapt_link`)."""
    lines = []
    in_fence = False
    for line in markdown.splitlines():
        adapted = line
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            adapted = LINK.sub(
                lambda match: f"]({adapt_link(match['target'], repo_url, ref)})",
                line,
            )
        lines.append(adapted)
    return "\n".join(lines)


def uses_placeholders(markdown: str) -> bool:
    """Return whether Markdown uses a placeholder of the versions.

    Args:
        markdown: Markdown of a page.

    Returns:
        Whether it contains one of `PLACEHOLDERS`.
    """
    return any(placeholder in markdown for placeholder in PLACEHOLDERS)


def substitute_version(markdown: str, version: str | None) -> str:
    """Replace the placeholders of the release and chart versions.

    Args:
        markdown: Markdown of a page.
        version: The release version, e.g., `1.2.3`; `None` if there is none
            (before the first release).

    Returns:
        The Markdown, with `version` (or `UNRELEASED`, if it is `None`) and
        its chart version in place of the placeholders (`PLACEHOLDERS`, see
        `scripts/chart_readme.py`).
    """
    if not uses_placeholders(markdown):
        return markdown
    substituted: str = chart_readme.substitute(markdown, version or UNRELEASED)
    return substituted


def on_page_markdown(markdown: str, page: object, config: Config, files: object) -> str:
    """Include the chart's README, and replace the release version.

    Args:
        markdown: Markdown of the page.
        page: The page (`mkdocs.structure.pages.Page`); unused.
        config: Settings of MkDocs.
        files: Files of the site (`mkdocs.structure.files.Files`); unused.

    Returns:
        The Markdown, with the README in place of the line `INCLUDE`, and the
        release and chart versions in place of their placeholders.

    Raises:
        ValueError: If the version in `CITATION.cff` is invalid.
    """
    root = Path(config.config_file_path).parent
    if INCLUDE in markdown.splitlines():
        # Set by Read the Docs to the commit of the build
        ref = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH") or "dev"
        content = adapt_links(
            (root / README).read_text(encoding="utf-8"), config.repo_url, ref
        )
        markdown = "\n".join(
            content if line == INCLUDE else line for line in markdown.splitlines()
        )
    # Pages without placeholders need no version (`CITATION.cff` isn't read)
    if not uses_placeholders(markdown):
        return markdown
    version = citation.release_version((root / CITATION).read_text(encoding="utf-8"))
    return substitute_version(markdown, version)
