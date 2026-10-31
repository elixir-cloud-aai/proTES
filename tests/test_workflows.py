"""Tests for the GitHub Actions workflows (names, gate, release checks)."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from packaging.version import Version
import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
GATE = "passed"
# The last release signed by the release workflow before its file was renamed
# from `release.yaml` to `release.yml`
LAST_RELEASE_YAML = Version("0.3.0")
# A chart file name as the release workflow packages it
CHART = "protes-1.2.3.tgz"


def _load(path: Path) -> dict[str, Any]:
    """Load a workflow file.

    Args:
        path: The path of the workflow file.

    Returns:
        The workflow.
    """
    workflow: dict[str, Any] = yaml.safe_load(path.read_text())
    return workflow


def test_workflow_files() -> None:
    """The workflows are `ci.yml`, `release.yml` and `scan.yml`."""
    names = {path.name: _load(path)["name"] for path in WORKFLOWS.iterdir()}
    assert names == {"ci.yml": "CI", "release.yml": "Release", "scan.yml": "Scan"}


def test_job_names_unique() -> None:
    """Job names are unique across all workflows (they name the checks)."""
    names = [
        job["name"]
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job in _load(path)["jobs"].values()
    ]
    assert len(names) == len(set(names))


def test_gate_needs_all_jobs() -> None:
    """The gate of `ci.yml` (the required check) needs every other job."""
    jobs = _load(WORKFLOWS / "ci.yml")["jobs"]
    gate = jobs[GATE]
    assert gate["name"] == "CI passed"
    assert gate["if"] == "always()"
    assert sorted(gate["needs"]) == sorted(set(jobs) - {GATE})


def test_verification_signer_workflow() -> None:
    """The verification commands name the workflow that signed the release.

    They are for the version in `CITATION.cff` (`{{ version }}`): releases up
    to 0.3.0 were signed by `release.yaml`, later ones by `release.yml`.
    """
    match = re.search(
        r"^version:\s*[\"']?([^\"'\s]+)",
        (ROOT / "CITATION.cff").read_text(),
        flags=re.MULTILINE,
    )
    assert match
    expected = (
        "release.yaml" if Version(match[1]) <= LAST_RELEASE_YAML else "release.yml"
    )
    signers = re.findall(
        r"proTES/\.github/workflows/(release\.ya?ml)",
        (ROOT / "docs" / "operations.md").read_text(),
    )
    assert signers
    assert set(signers) == {expected}


def test_publish_condition() -> None:
    """`push` and `publish` run exactly where `PUBLISH` of `ci.yml` is "true".

    The gate allows them to be skipped only where `PUBLISH` is "false", and
    `scan` builds the image locally there, so the three must agree.
    """
    workflow = _load(WORKFLOWS / "ci.yml")
    publish = workflow["env"]["PUBLISH"]
    assert publish.startswith("${{ github.event_name == 'push' && ")
    jobs = workflow["jobs"]
    assert jobs["push"]["if"] == publish
    assert jobs["publish"]["if"] == publish


def test_upstream_owner() -> None:
    """`ci.yml` marks upstream runs by the owner `elixir-cloud-aai` only.

    A typo in, or an inverted, owner check would quietly change upstream runs
    (e.g., release readiness findings to warnings), so every check compares
    with `==`.
    """
    text = (WORKFLOWS / "ci.yml").read_text()
    checks = re.findall(r"github\.repository_owner\s*(\S+)\s*'([^']*)'", text)
    assert checks
    assert len(checks) == text.count("github.repository_owner")
    assert set(checks) == {("==", "elixir-cloud-aai")}


def test_gate_uses_shared_action() -> None:
    """The gate of `ci.yml` uses the shared `gate` action.

    It passes the results of all needed jobs and allows `push` and
    `publish` to be skipped only where `PUBLISH` is "false", and needs no
    permissions.
    """
    gate = _load(WORKFLOWS / "ci.yml")["jobs"][GATE]
    assert gate["permissions"] == {}
    (step,) = gate["steps"]
    assert step["uses"].startswith("elixir-cloud-aai/.github/actions/gate@")
    assert step["with"]["needs"] == "${{ toJSON(needs) }}"
    assert step["with"]["allow-skipped"] == (
        "${{ env.PUBLISH == 'false' && 'push publish' || '' }}"
    )


def test_shared_actions_pinned() -> None:
    """The shared actions of `elixir-cloud-aai/.github` are pinned to a commit.

    All pins name the same full commit SHA (bumped together), and no
    workflow uses a local copy of an action. The YAML drops
    comments, so the test below checks the release comments (`# vX.Y.Z`) in
    the files' text.
    """
    pinned = re.compile(r"elixir-cloud-aai/\.github/actions/[a-z-]+@([0-9a-f]{40})")
    shas: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in _load(path)["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                assert not uses.startswith("./"), (path.name, uses)
                if uses.startswith("elixir-cloud-aai/.github/"):
                    match = pinned.fullmatch(uses)
                    assert match, (path.name, uses)
                    shas.add(match.group(1))
    assert len(shas) == 1


def test_shared_actions_release_comments() -> None:
    """Every pin of a shared action names its release in a comment.

    Dependabot bumps a pin `@<sha> # vX.Y.Z` to new releases only if the
    pinned commit carries a version tag, and updates the comment; all pins
    name the same release. Whether the commit is the release's commit isn't
    checked here (no network in the tests).
    """
    pin = re.compile(r"uses: elixir-cloud-aai/\.github/actions/[a-z-]+@\S+(.*)$")
    comment = re.compile(r" # (v\d+\.\d+\.\d+)")
    versions: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := pin.search(line):
                version = comment.fullmatch(match.group(1))
                assert version, (path.name, line.strip())
                versions.add(version.group(1))
    assert len(versions) == 1, versions


def test_release_chart_checked() -> None:
    """The release workflow checks the chart before attesting and signing it.

    `publish` downloads the chart artifact, which any job of the run could
    replace, so it checks that `dist/` holds exactly the file that `prepare`
    packaged (its name and SHA-256, as outputs of `prepare`) before anything,
    the image included, is attested or signed. Only that file is attested and
    signed. The wiring and order of the steps are checked here, the script
    itself by `test_release_chart_check_runs`.
    """
    release = _load(WORKFLOWS / "release.yml")["jobs"]
    prepare = release["prepare"]
    assert prepare["outputs"]["chart_file"] == "${{ steps.chart.outputs.file }}"
    assert prepare["outputs"]["chart_sha256"] == "${{ steps.chart.outputs.sha256 }}"
    packaging = {step["name"]: step for step in prepare["steps"]}
    assert packaging["Package chart"]["id"] == "chart"
    # Re-runs of all jobs replace the chart, so its checksum is the new one
    assert packaging["Upload chart"]["with"]["overwrite"] is True
    steps = {step["name"]: step for step in release["publish"]["steps"]}
    assert steps["Download chart"]["with"] == {"name": "chart", "path": "dist/"}
    check = steps["Check chart"]
    # A failed check stops the job
    assert "continue-on-error" not in check
    assert "if" not in check
    assert check["env"]["FILE"] == "${{ needs.prepare.outputs.chart_file }}"
    assert check["env"]["SHA256"] == "${{ needs.prepare.outputs.chart_sha256 }}"
    chart = "dist/${{ needs.prepare.outputs.chart_file }}"
    assert steps["Attest chart provenance"]["with"]["subject-path"] == chart
    assert steps["Sign chart"]["env"]["CHART"] == chart
    sign = steps["Sign chart"]["run"]
    assert sign == 'cosign sign-blob --yes --bundle "${CHART}.sigstore.json" "${CHART}"'
    names = list(steps)
    assert names.index("Download chart") < names.index("Check chart")
    # A rejected chart leaves the image (pushed untagged by `push`) without
    # GitHub attestation, signature and tags too
    assert names.index("Check chart") < names.index("Attest build provenance")
    assert names.index("Check chart") < names.index("Sign image")
    assert names.index("Check chart") < names.index("Attest chart provenance")
    assert names.index("Check chart") < names.index("Sign chart")
    assert names.index("Sign chart") < names.index("Create release")


@pytest.mark.parametrize(
    ("extra", "rename", "tamper", "empty", "file", "sha256", "passes"),
    [
        pytest.param(None, None, False, False, CHART, None, True, id="good"),
        pytest.param(None, None, True, False, CHART, None, False, id="wrong-hash"),
        # Sorts before the chart
        pytest.param("other.tgz", None, False, False, CHART, None, False, id="extra"),
        # Sorts after the chart, so only the count rejects it
        pytest.param("zzz", None, False, False, CHART, None, False, id="extra-after"),
        pytest.param(".hidden", None, False, False, CHART, None, False, id="hidden"),
        pytest.param(
            None, "protes-9.9.9.tgz", False, False, CHART, None, False, id="renamed"
        ),
        pytest.param(None, None, False, True, CHART, None, False, id="empty"),
        pytest.param(None, None, False, False, "../x.tgz", None, False, id="bad-name"),
        pytest.param(
            None,
            "protes-1.tar",
            False,
            False,
            "protes-1.tar",
            None,
            False,
            id="not-tgz",
        ),
        pytest.param(None, None, False, False, CHART, "abc", False, id="bad-hash"),
        pytest.param(None, None, False, False, CHART, "", False, id="no-hash"),
        pytest.param(None, None, False, False, CHART, "A" * 64, False, id="upper-hash"),
    ],
)
def test_release_chart_check_runs(
    tmp_path: Path,
    extra: str | None,
    rename: str | None,
    tamper: bool,
    empty: bool,
    file: str,
    sha256: str | None,
    passes: bool,
) -> None:
    """The chart check of `publish` passes only for the chart of `prepare`.

    The step's script runs with the options of GitHub's default shell for
    steps without `shell:` (`bash -e`, no `pipefail`) in a directory whose
    `dist/` holds the downloaded artifact. It needs `bash`
    and GNU `sha256sum`, as on the GitHub runners.

    Args:
        tmp_path: The working directory of the step.
        extra: A file added to the artifact, if any.
        rename: The name the chart is renamed to in the artifact, if any.
        tamper: Whether the chart's content is changed after packaging.
        empty: Whether the artifact holds no file at all.
        file: The chart file name output by `prepare`.
        sha256: The checksum output by `prepare` (that of the chart if None).
        passes: Whether the check passes.
    """
    bash = shutil.which("bash")
    assert bash
    steps = _load(WORKFLOWS / "release.yml")["jobs"]["publish"]["steps"]
    script = {step["name"]: step for step in steps}["Check chart"]["run"]
    dist = tmp_path / "dist"
    dist.mkdir()
    chart = dist / CHART
    chart.write_bytes(b"chart")
    digest = hashlib.sha256(chart.read_bytes()).hexdigest()
    if tamper:
        chart.write_bytes(b"other chart")
    if rename:
        chart.rename(dist / rename)
    if extra:
        (dist / extra).write_bytes(b"extra")
    if empty:
        chart.unlink()
    result = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [bash, "--noprofile", "--norc", "-e", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "FILE": file,
            "SHA256": digest if sha256 is None else sha256,
        },
        check=False,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr
