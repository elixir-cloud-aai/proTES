"""Tests of the repository's files."""

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Values specific to an identity provider or deployment, which the
# repository must not contain (examples use fictitious values)
IDP_SPECIFIC = re.compile(
    # Names not preceded or followed by letters (e.g., also `perun_groups`)
    r"lifescience|(?<![a-z])ls[ -]login(?![a-z])|eduperson|(?<![a-z])perun(?![a-z])"
    r"|(?<![a-z])ecp(?![a-z])|urn:geant",
    re.IGNORECASE,
)
# E-mail addresses, with a domain name of valid (also internationalized) labels
EMAIL = re.compile(r"[\w.%+-]+@((?:[^\W_](?:(?:[^\W_]|-)*[^\W_])?\.)+[^\W\d_]{2,})")
# The service address
ADDRESSES = {"cloud-service@elixir-europe.org"}
# Domains reserved for examples
DOMAINS = re.compile(r"(?:.+\.)?example\.(?:com|net|org)", re.IGNORECASE)


def _text_lines(path: Path) -> list[str]:
    """Get the lines of a file, or none if it is not text (UTF-8).

    Args:
        path: The file.

    Returns:
        Its lines.
    """
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []


def test_text_lines(tmp_path: Path) -> None:
    """Files that aren't UTF-8 text (e.g., images) have no lines."""
    (tmp_path / "text.md").write_text("a\nb\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff")
    assert _text_lines(tmp_path / "text.md") == ["a", "b"]
    assert _text_lines(tmp_path / "image.png") == []


def _repository_lines() -> list[tuple[str, int, str]]:
    """Get the lines of the repository's text files.

    Returns:
        The file name, line number and line of each line.
    """
    files = (
        subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607 (git from PATH)
            capture_output=True,
            check=True,
            cwd=ROOT,
        )
        .stdout.decode()
        .split("\0")
    )
    return [
        (name, number, line)
        for name in files
        if name and (ROOT / name).is_file()
        for number, line in enumerate(_text_lines(ROOT / name), start=1)
    ]


def test_idp_specific_pattern() -> None:
    """Names of identity providers are found, but not within other words."""
    text = "LS Login, ls-login, perun_groups, eduPersonEntitlement, ECP, urn:geant"
    assert [m[0] for m in IDP_SPECIFIC.finditer(text)] == [
        "LS Login",
        "ls-login",
        "perun",
        "eduPerson",
        "ECP",
        "urn:geant",
    ]
    assert IDP_SPECIFIC.search("superuni, recipe, aspecial") is None


def test_repository_generic() -> None:
    """No file of the repository has identity provider specific values.

    Guides for specific identity providers or deployments are kept
    elsewhere.
    """
    found = [
        f"{name}:{number}: {line.strip()}"
        for name, number, line in _repository_lines()
        # This file names the values to find
        if name != "tests/test_repository.py" and IDP_SPECIFIC.search(line)
    ]
    assert found == []


def test_email_pattern() -> None:
    """Addresses are found also with internationalized domain names."""
    text = "a@x.example.org, b@bücher.example.org, c@-x.org, d@x-.org"
    assert [m[0] for m in EMAIL.finditer(text)] == [
        "a@x.example.org",
        "b@bücher.example.org",
    ]


def test_no_personal_email_addresses() -> None:
    """No file has e-mail addresses but the service and example addresses."""
    found = [
        f"{name}:{number}: {match[0]}"
        for name, number, line in _repository_lines()
        for match in EMAIL.finditer(line)
        if match[0].lower() not in ADDRESSES and not DOMAINS.fullmatch(match[1])
    ]
    assert found == []
