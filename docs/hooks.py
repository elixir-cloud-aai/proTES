"""MkDocs hooks: include the README of the Helm chart in the deployment page.

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

import os
from pathlib import Path
import re
from typing import Protocol

INCLUDE = '--8<-- "deployment/README.md"'
README = Path("deployment/README.md")
# Base URL of the docs that the README links to
DOCS_URL = "https://protes.readthedocs.io/en/latest/"

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
        GitHub at branch `dev`, their URL at `ref`; other targets unchanged.
    """
    if target.startswith(DOCS_URL):
        page, hash_, anchor = target.removeprefix(DOCS_URL).partition("#")
        return f"{page.rstrip('/') or 'index'}.md{hash_}{anchor}"
    repo_url = repo_url.rstrip("/")
    blob = f"{repo_url}/blob/dev/"
    if target.startswith(blob):
        return f"{repo_url}/blob/{ref}/{target.removeprefix(blob)}"
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


def on_page_markdown(markdown: str, page: object, config: Config, files: object) -> str:
    """Include the chart's README in pages that ask for it.

    Args:
        markdown: Markdown of the page.
        page: The page (`mkdocs.structure.pages.Page`); unused.
        config: Settings of MkDocs.
        files: Files of the site (`mkdocs.structure.files.Files`); unused.

    Returns:
        The Markdown, with the README in place of the line `INCLUDE`.
    """
    if INCLUDE not in markdown.splitlines():
        return markdown
    readme = Path(config.config_file_path).parent / README
    # Set by Read the Docs to the commit of the build
    ref = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH") or "dev"
    content = adapt_links(readme.read_text(encoding="utf-8"), config.repo_url, ref)
    return "\n".join(
        content if line == INCLUDE else line for line in markdown.splitlines()
    )
