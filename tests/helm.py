"""Helpers for tests of the Helm chart."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import yaml

# A rendered manifest (Kubernetes object)
Manifest = dict[str, Any]

CHART = Path(__file__).parents[1] / "deployment"
HELM = shutil.which("helm")
# Release rendered by default, and the names of its resources
RELEASE = "protes"
APP = "protes"
MONGODB = "protes-mongodb"
WORKER = "protes-worker"
BROKER = "protes-broker"
FLOWER = "protes-flower"
# Names of the Secrets with the MongoDB credentials and the broker URL,
# required by default (authentication, worker and bundled broker enabled)
MONGODB_SECRET = "mongodb-credentials"  # noqa: S105 (a Secret's name, no credential)
BROKER_SECRET = "celery"  # noqa: S105 (a Secret's name, no credential)
# Trusted token issuer, required (the base config trusts none)
ISSUER = "https://login.example.org/oidc/"
# Claim with the groups of users, required for group mappings
GROUP_CLAIM = "groups"
# Group mapped to the role `admin`, required by default
ADMIN_GROUP = "test-admins"
# Audience of the app's tokens, required by default
AUDIENCE = "test-client"
# Templates added to a copy of the chart to render its notes into a ConfigMap,
# as `helm template` does not render notes (and `helm install --dry-run` needs
# a cluster)
NOTES_HELPER = '{{- define "notes-test" }}NOTES{{- end }}'
NOTES_CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: notes-test
data:
  notes: {{ include "notes-test" . | toJson }}
"""


def render(
    *args: str,
    mongodb_secret: str | None = MONGODB_SECRET,
    broker_secret: str | None = BROKER_SECRET,
    issuer: str | None = ISSUER,
    group_claim: str | None = GROUP_CLAIM,
    admin_group: str | None = ADMIN_GROUP,
    audience: str | None = AUDIENCE,
    release: str = RELEASE,
    chart: Path = CHART,
) -> list[Manifest]:
    """Render the chart with `helm template` arguments.

    Args:
        args: Arguments of `helm template`, after the defaults set by the
            other arguments, so they take precedence.
        mongodb_secret: Name of the Secret with the MongoDB credentials; not
            set if `None`.
        broker_secret: Name of the Secret with the broker URL; not set if
            `None`.
        issuer: Trusted token issuer; not set if `None`.
        group_claim: Claim with the groups of users; not set if `None`.
        admin_group: Group mapped to the role `admin`; not set if `None`.
        audience: Audience of the app's tokens; not set if `None`.
        release: Name of the release.
        chart: Chart directory or packaged chart.

    Returns:
        The rendered manifests.

    Raises:
        RuntimeError: The chart's dependencies are not built (see "Helm
            chart" in `docs/development.md`).
        subprocess.CalledProcessError: Helm failed, e.g., as a required value
            is not set.
    """
    assert HELM is not None
    if chart.is_dir() and not list((chart / "charts").glob("foca-common-*.tgz")):
        raise RuntimeError(
            "The library chart is missing: run `scripts/foca_common.sh` and "
            "`helm dependency build deployment`"
        )
    defaults: list[str] = []
    if mongodb_secret is not None:
        defaults += ["--set", f"mongodb.auth.existingSecret={mongodb_secret}"]
    if broker_secret is not None:
        defaults += ["--set", f"broker.existingSecret={broker_secret}"]
    if audience is not None:
        defaults += [
            "--set-json",
            f"config.security.auth.audience={json.dumps([audience])}",
        ]
    if issuer is not None:
        defaults += [
            "--set-json",
            f"config.security.auth.issuers={json.dumps([issuer])}",
        ]
    if group_claim is not None:
        defaults += [
            "--set-json",
            f"config.security.access_control.claim_groups={json.dumps(group_claim)}",
        ]
    if admin_group is not None:
        mapping = json.dumps({admin_group: "admin"})
        defaults += [
            "--set-json",
            f"config.security.access_control.group_mapping={mapping}",
        ]
    rendered = subprocess.run(  # noqa: S603 (argument list, no shell)
        [HELM, "template", release, str(chart), *defaults, *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def render_with(templates: dict[str, str], *args: str, **kwargs: Any) -> list[Manifest]:
    """Render a copy of the chart with additional templates.

    Args:
        templates: Contents of the additional templates, by file name.
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `render()`, except `chart`.

    Returns:
        The rendered manifests.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        chart = Path(tmp_dir) / "chart"
        shutil.copytree(CHART, chart)
        for name, template in templates.items():
            (chart / "templates" / name).write_text(template)
        return render(*args, chart=chart, **kwargs)


def render_notes(*args: str, **kwargs: Any) -> str:
    """Render the chart's notes (`templates/NOTES.txt`).

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `render()`, except `chart`.

    Returns:
        The rendered notes.
    """
    notes = (CHART / "templates" / "NOTES.txt").read_text()
    templates = {
        "_notes-test.tpl": NOTES_HELPER.replace("NOTES", notes),
        "notes-test.yaml": NOTES_CONFIGMAP,
    }
    docs = render_with(templates, *args, **kwargs)
    (configmap,) = [doc for doc in docs if doc["metadata"]["name"] == "notes-test"]
    notes_text: str = configmap["data"]["notes"]
    return notes_text
