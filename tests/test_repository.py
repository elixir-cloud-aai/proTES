"""Tests of the repository's files."""

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# E-mail addresses, with a domain name of valid (also internationalized) labels
EMAIL = re.compile(r"[\w.%+-]+@((?:[^\W_](?:(?:[^\W_]|-)*[^\W_])?\.)+[^\W\d_]{2,})")
# The service address
ADDRESSES = {"cloud-service@elixir-europe.org"}
# Domains reserved for examples
DOMAINS = re.compile(r"(?:.+\.)?example\.(?:com|net|org)", re.IGNORECASE)


def test_email_pattern() -> None:
    """Addresses are found also with internationalized domain names."""
    text = "a@x.example.org, b@bücher.example.org, c@-x.org, d@x-.org"
    assert [m[0] for m in EMAIL.finditer(text)] == [
        "a@x.example.org",
        "b@bücher.example.org",
    ]


def test_no_personal_email_addresses() -> None:
    """No file has e-mail addresses but the service and example addresses."""
    files = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 (git from PATH)
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.decode()
    found = []
    for name in filter(None, files.split("\0")):
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [
            f"{name}: {match[0]}"
            for match in EMAIL.finditer(text)
            if match[0].lower() not in ADDRESSES and not DOMAINS.fullmatch(match[1])
        ]
    assert found == []
