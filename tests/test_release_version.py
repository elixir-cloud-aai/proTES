"""Tests of the release version logic (`scripts/release_version.py`)."""

import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_version.py"

# `scripts/` is not a package, so the script is loaded from its file
_spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
assert _spec and _spec.loader
release_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_version)


@pytest.mark.parametrize(
    ("tag", "version", "chart_version", "stable", "prerelease"),
    [
        ("v0.3.0", "0.3.0", "0.3.0", True, True),
        ("v0.3.0rc1", "0.3.0rc1", "0.3.0-rc1", False, True),
        ("v0.0.0", "0.0.0", "0.0.0", True, True),
        ("v1.0.0", "1.0.0", "1.0.0", True, False),
        ("v1.0.0a1", "1.0.0a1", "1.0.0-a1", False, True),
        ("v1.0.0b2", "1.0.0b2", "1.0.0-b2", False, True),
        ("v1.0.0rc0", "1.0.0rc0", "1.0.0-rc0", False, True),
        ("v2.10.3", "2.10.3", "2.10.3", True, False),
        ("v10.0.1rc12", "10.0.1rc12", "10.0.1-rc12", False, True),
    ],
)
def test_parse_tag(
    tag: str, version: str, chart_version: str, stable: bool, prerelease: bool
) -> None:
    """Release tags give the version, chart version and release kind."""
    release = release_version.parse_tag(tag)
    assert release.version == version
    assert release.chart_version == chart_version
    assert release.stable is stable
    assert release.prerelease is prerelease
    assert ".".join(map(str, release.number)) == chart_version.partition("-")[0]


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "v",
        "garbage",
        # No `v`
        "0.3.0",
        # Not three components
        "v1",
        "v1.2",
        "v1.2.3.4",
        # Leading zeros (not canonical)
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v1.2.3rc01",
        # Digits other than ASCII
        "v1.2.\u0663",
        # Other pre-release, post-release, development and local versions
        "v1.2.3rc",
        "v1.2.3c1",
        "v1.2.3-rc1",
        "v1.2.3.rc1",
        "v1.2.3alpha1",
        "v1.2.3.post1",
        "v1.2.3.dev1",
        "v1.2.3rc1.dev1",
        "v1.2.3+local",
        "v1.2.3rc1+local",
        # Not exactly the tag
        "V1.2.3",
        " v1.2.3",
        "v1.2.3 ",
        "v1.2.3\n",
        "refs/tags/v1.2.3",
    ],
)
def test_parse_tag_invalid(tag: str) -> None:
    """Tags that are no canonical release versions are rejected."""
    with pytest.raises(ValueError, match="not a release version"):
        release_version.parse_tag(tag)


def test_outputs() -> None:
    """The outputs are `GITHUB_OUTPUT` lines."""
    release = release_version.parse_tag("v1.0.0rc1")
    assert release.outputs() == [
        "version=1.0.0rc1",
        "chart_version=1.0.0-rc1",
        "stable=false",
        "prerelease=true",
    ]


@pytest.mark.parametrize(
    ("tag", "tags", "latest"),
    [
        # The only release
        ("v0.3.0", ["v0.3.0"], True),
        # Highest of several, compared by numbers, not as text
        ("v0.10.0", ["v0.2.0", "v0.10.0", "v0.9.9"], True),
        ("v1.0.0", ["v0.3.0", "v1.0.0", "v0.10.0"], True),
        # Lower than another stable release (e.g., a fix of an old minor)
        ("v0.9.1", ["v0.9.1", "v0.10.0"], False),
        ("v0.3.0", ["v0.3.0", "v0.3.1"], False),
        # Pre-releases are never `latest` and don't count as higher
        ("v1.0.0rc1", ["v0.3.0", "v1.0.0rc1"], False),
        ("v0.3.0", ["v0.3.0", "v1.0.0rc1", "v1.0.0a1", "v1.0.0b1"], True),
        # Other tags are ignored
        ("v0.3.0", ["v0.3.0", "v9.0.0+local", "v09.0.0", "v9.0", "v9.0.0.0"], True),
        ("v0.3.0", ["v0.3.0", "", "garbage"], True),
        # Not among the tags on `main`
        ("v0.3.0", ["v0.2.0"], False),
        ("v0.3.0", [], False),
    ],
)
def test_is_latest(tag: str, tags: list[str], latest: bool) -> None:
    """Only the highest stable release among the given tags is `latest`."""
    assert release_version.is_latest(tag, tags) is latest


@pytest.mark.parametrize(
    ("tag", "tags", "previous"),
    [
        # The first release
        ("v0.3.0", [], None),
        ("v0.3.0", ["v0.3.0"], None),
        ("v0.3.0rc1", ["v0.3.0rc1"], None),
        # Highest below, compared by numbers, not as text
        ("v0.10.0", ["v0.2.0", "v0.9.9", "v0.10.0"], "v0.9.9"),
        ("v1.0.0", ["v0.3.0", "v0.10.0", "v0.9.0"], "v0.10.0"),
        # Higher releases (e.g., for a fix of an old minor) are ignored
        ("v0.9.1", ["v0.9.0", "v0.9.1", "v0.10.0"], "v0.9.0"),
        # Stable releases start after the previous stable release
        ("v1.0.0", ["v0.3.0", "v1.0.0a1", "v1.0.0b1", "v1.0.0rc2"], "v0.3.0"),
        # Without a stable release before, the highest pre-release (the
        # generated notes can't start before the first tag)
        ("v1.0.0", ["v1.0.0rc1"], "v1.0.0rc1"),
        ("v0.3.0", ["v0.3.0a1", "v0.3.0rc1", "v0.3.0b2"], "v0.3.0rc1"),
        ("v1.0.0", ["v0.9.0", "v1.0.0rc1"], "v0.9.0"),
        # Pre-releases start after the previous release of any kind, ordered
        # alpha < beta < release candidate < stable, then by number
        ("v1.0.0rc2", ["v0.3.0", "v1.0.0rc1", "v1.0.0rc10"], "v1.0.0rc1"),
        ("v1.0.0rc1", ["v0.3.0", "v1.0.0a2", "v1.0.0b1"], "v1.0.0b1"),
        ("v1.0.0b1", ["v1.0.0a10", "v1.0.0a9", "v1.0.0rc1"], "v1.0.0a10"),
        ("v1.0.0a1", ["v0.3.0", "v0.3.1rc1", "v1.0.0b1"], "v0.3.1rc1"),
        ("v0.3.1rc1", ["v0.3.0", "v0.3.0rc1"], "v0.3.0"),
        # Other tags are ignored
        ("v0.3.0", ["v0.2.0", "v0.2.9+local", "v0.2.10.0", "garbage", ""], "v0.2.0"),
    ],
)
def test_previous_tag(tag: str, tags: list[str], previous: str | None) -> None:
    """The previous release is the highest of the right kind below the tag."""
    assert release_version.previous_tag(tag, tags) == previous


def test_order() -> None:
    """Releases are ordered by number, then pre-release kind and number."""
    tags = ["v1.0.0", "v1.0.0rc1", "v0.9.0", "v1.0.0a2", "v1.0.0b1", "v1.0.0a10"]
    assert sorted(tags, key=lambda tag: release_version.parse_tag(tag).order) == [
        "v0.9.0",
        "v1.0.0a2",
        "v1.0.0a10",
        "v1.0.0b1",
        "v1.0.0rc1",
        "v1.0.0",
    ]


@pytest.mark.parametrize(
    ("tags", "published", "output"),
    [
        ("v0.2.0\nv0.3.0\nv0.2.1rc1\n", "v0.2.0\nv0.2.1rc1\n", "previous_tag=v0.2.0\n"),
        # No published releases (the first release)
        ("v0.2.0\nv0.3.0\n", "", "previous_tag=\n"),
        ("", "", "previous_tag=\n"),
        # The release's own tag among the published ones (a rerun)
        ("v0.2.0\nv0.3.0\n", "v0.3.0\nv0.2.0\n", "previous_tag=v0.2.0\n"),
        ("v0.3.0\n", "v0.3.0\n", "previous_tag=\n"),
        # Tags without a published release (e.g., whose release failed) and
        # published releases outside the release's history are ignored
        ("v0.2.0\nv0.2.1\nv0.3.0\n", "v0.2.0\nv0.2.2\n", "previous_tag=v0.2.0\n"),
        ("v0.2.1\nv0.3.0\n", "v0.2.2\n", "previous_tag=\n"),
        # Only whole lines match
        ("v0.2.0\nv0.2.1\n", "v0.2.10\nv0.2.0 \n", "previous_tag=\n"),
        # Undecodable bytes don't match
        ("v0.2.0\nv0.2.1\n", b"v0.2.0\xff\nv0.2.1\n", "previous_tag=v0.2.1\n"),
        # Lines end at line feeds only: other line breaks stay in the tag
        ("v0.2.0\nv0.2.1\u2028x\n", "v0.2.0\nv0.2.1\n", "previous_tag=v0.2.0\n"),
        ("v0.2.0\nv0.2.1\n", "v0.2.0\nv0.2.1\u2028x\n", "previous_tag=v0.2.0\n"),
        ("v0.2.0\nv0.2.1\r\n", "v0.2.0\nv0.2.1\n", "previous_tag=v0.2.0\n"),
    ],
)
def test_main_previous(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    tags: str,
    published: str | bytes,
    output: str,
) -> None:
    """`previous` counts the tags in the history that have a published release.

    The tags in the release's history come from standard input, those of the
    published releases from the file given with `--published`.
    """
    published_file = tmp_path / "published"
    published_file.write_bytes(
        published.encode() if isinstance(published, str) else published
    )
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(tags.encode())))
    assert (
        release_version.main(["previous", "v0.3.0", "--published", str(published_file)])
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == output
    assert captured.err == ""


def test_main_previous_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A missing file of published tags fails with an error annotation."""
    missing = tmp_path / "missing"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"v0.2.0\n")))
    assert (
        release_version.main(["previous", "v0.3.0", "--published", str(missing)]) == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("::error::Cannot read published tags: ")
    assert str(missing) in captured.err


@pytest.mark.parametrize(
    "args",
    [
        ["previous", "v0.3.0"],
        ["outputs", "v0.3.0", "--published", "published"],
        ["latest", "v0.3.0", "--published", "published"],
    ],
)
def test_main_published_usage(
    capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    """`--published` is required for `previous` and rejected otherwise."""
    with pytest.raises(SystemExit) as exc_info:
        release_version.main(args)
    assert exc_info.value.code == 2
    assert "--published is required for, and only for" in capsys.readouterr().err


def test_main_outputs(capsys: pytest.CaptureFixture[str]) -> None:
    """`outputs` prints the outputs of a tag."""
    assert release_version.main(["outputs", "v0.3.0"]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "version=0.3.0\nchart_version=0.3.0\nstable=true\nprerelease=true\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    ("tags", "output"),
    [
        ("v0.2.0\nv0.3.0\nv1.0.0rc1\n", "latest=true\n"),
        ("v0.3.0\nv0.3.1\n", "latest=false\n"),
        ("", "latest=false\n"),
        # Tags that aren't UTF-8 are ignored
        (b"v0.3.0\nv\xff9.0.0\nv9.0.0\xff\n", "latest=true\n"),
        # Lines end at line feeds only: other line breaks stay in the tag
        ("v0.3.0\nv9.0.0\u2028x\nv9.0.0\x85\nv9.0.0\r\n", "latest=true\n"),
    ],
)
def test_main_latest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tags: str | bytes,
    output: str,
) -> None:
    """`latest` reads the tags on `main` from standard input."""
    data = tags.encode() if isinstance(tags, str) else tags
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data)))
    assert release_version.main(["latest", "v0.3.0"]) == 0
    captured = capsys.readouterr()
    assert captured.out == output
    assert captured.err == ""


@pytest.mark.parametrize(
    "args",
    [
        ["outputs", "0.3.0"],
        ["latest", "v0.3.0+local"],
        ["previous", "v0.3", "--published", os.devnull],
    ],
)
def test_main_invalid_tag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    """Invalid tags fail with an error annotation and no outputs."""
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"v0.3.0\n")))
    assert release_version.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"::error::Tag '{args[1]}' is not a release version"
        " (e.g., v1.2.3 or v1.2.3rc1)\n"
    )


def test_main_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """Calls without a command or tag fail with the usage."""
    with pytest.raises(SystemExit) as exc_info:
        release_version.main(["outputs"])
    assert exc_info.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "stdin", "returncode", "stdout", "stderr"),
    [
        (
            ["outputs", "v1.2.3rc1"],
            "",
            0,
            "version=1.2.3rc1\nchart_version=1.2.3-rc1\nstable=false\n"
            "prerelease=true\n",
            "",
        ),
        (["latest", "v1.2.3"], "v1.2.2\nv1.2.3\n", 0, "latest=true\n", ""),
        (
            ["previous", "v1.2.3", "--published", os.devnull],
            "v1.2.2\nv1.2.3\n",
            0,
            "previous_tag=\n",
            "",
        ),
        (
            ["previous", "v1.2.3", "--published", "published"],
            "v1.2.1\nv1.2.2\nv1.2.3\n",
            0,
            "previous_tag=v1.2.1\n",
            "",
        ),
        (
            ["outputs", "1.2.3"],
            "",
            1,
            "",
            "::error::Tag '1.2.3' is not a release version (e.g., v1.2.3 or"
            " v1.2.3rc1)\n",
        ),
    ],
)
def test_script(
    tmp_path: Path,
    args: list[str],
    stdin: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    """The script runs as the release workflow calls it."""
    (tmp_path / "published").write_text("v1.2.1\nv1.2.3\n")
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [sys.executable, "-I", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        returncode,
        stdout,
        stderr,
    )


def test_script_reads_all_input(tmp_path: Path) -> None:
    """`previous` reads all of standard input, also without published releases.

    The release workflow pipes `git tag` into the script under
    `set -o pipefail`: a reader that exits before reading all of it (as
    `grep -Fx -f` with an empty file of patterns does) kills the writer with
    `SIGPIPE` and fails the step on the first release.
    """
    published = tmp_path / "published"
    published.write_text("")
    args = [sys.executable, "-I", str(SCRIPT), "previous", "v1.0.0"]
    args += ["--published", str(published)]
    with subprocess.Popen(  # noqa: S603 (fixed arguments, no shell)
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        assert process.stdin is not None
        # More than a pipe's buffer, so that writing fails with a broken pipe
        # unless the script reads all of it
        for number in range(100_000):
            process.stdin.write(f"v0.0.{number}\n".encode())
        stdout, stderr = process.communicate(timeout=60)
    assert (process.returncode, stdout, stderr) == (0, b"previous_tag=\n", b"")
