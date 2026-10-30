"""Tests for the resources of the Helm chart (`deployment/`).

Most resources are rendered by FOCA's library chart foca-common, which has
tests of its own; these tests check the app's settings, values and checks,
the app's own templates (Flower) and how the chart fits the image.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tarfile
from typing import Any

from foca.models.config import SecurityConfig
import pytest
import yaml

from tests.helm import (
    APP,
    BROKER,
    BROKER_SECRET,
    CHART,
    FLOWER,
    HELM,
    MONGODB,
    MONGODB_SECRET,
    WORKER,
    Manifest,
    render,
    render_notes,
    render_with,
)

ROOT = Path(__file__).resolve().parents[1]
# Default image tag
APP_VERSION = yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"]
NO_INGRESS = ("--set", "protes.ingress.enabled=false")
FLOWER_ON = (
    "--set",
    "flower.enabled=true",
    "--set",
    "flower.existingSecret=flower",
)
# A credential for TES instances from a Secret
EXTRA_ENV = {
    "name": "TES_PASSWORD",
    "valueFrom": {"secretKeyRef": {"name": "tes", "key": "password"}},
}
WITH_EXTRA_ENV = ("--set-json", f"protes.extraEnv={json.dumps([EXTRA_ENV])}")

# Rendered manifests, by kind and name
Docs = dict[tuple[str, str], Manifest]

pytestmark = pytest.mark.skipif(HELM is None, reason="helm not installed")


def _render(*args: str, **kwargs: Any) -> Docs:
    """Render the chart and index the documents by kind and name.

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `tests.helm.render()`.

    Returns:
        The rendered manifests, by kind and name.
    """
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in render("--set", "hostName=tes.example.org", *args, **kwargs)
    }


def _render_error(*args: str, **kwargs: Any) -> str:
    """Render the chart, expecting it to fail.

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `tests.helm.render()`.

    Returns:
        The error output of Helm.
    """
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render(*args, **kwargs)
    stderr: str = exc.value.stderr
    return stderr


def _pod(docs: Docs, kind: str, name: str) -> dict[str, Any]:
    """Get the pod spec of a workload.

    Args:
        docs: Rendered manifests.
        kind: Kind of the workload, e.g., `Deployment`.
        name: Name of the workload.

    Returns:
        The pod spec.
    """
    pod: dict[str, Any] = docs[(kind, name)]["spec"]["template"]["spec"]
    return pod


def _container(docs: Docs, name: str) -> dict[str, Any]:
    """Get the container of a Deployment.

    Args:
        docs: Rendered manifests.
        name: Name of the Deployment.

    Returns:
        The (only) container of the Deployment's pods.
    """
    (container,) = _pod(docs, "Deployment", name)["containers"]
    return container


def _env(container: dict[str, Any]) -> dict[str, Any]:
    """Get the environment variables of a container.

    Args:
        container: Container spec.

    Returns:
        The variables (without their names), by name.
    """
    return {var["name"]: var for var in container.get("env", [])}


def _secret_refs(container: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map environment variables to the Secret keys they are taken from.

    Args:
        container: Container spec.

    Returns:
        Name and key of the Secret, by environment variable.
    """
    return {
        name: (
            var["valueFrom"]["secretKeyRef"]["name"],
            var["valueFrom"]["secretKeyRef"]["key"],
        )
        for name, var in _env(container).items()
        if "secretKeyRef" in var.get("valueFrom", {})
    }


def _overlay(docs: Docs) -> dict[str, Any]:
    """Get the config overlay of the app.

    Args:
        docs: Rendered manifests.

    Returns:
        The config overlay.
    """
    overlay: dict[str, Any] = yaml.safe_load(
        docs[("ConfigMap", f"{APP}-config")]["data"]["overlay.yaml"]
    )
    return overlay


def _labels(docs: Docs, kind: str, name: str) -> dict[str, str]:
    """Get the pod labels of a workload.

    Args:
        docs: Rendered manifests.
        kind: Kind of the workload.
        name: Name of the workload.

    Returns:
        The labels of the workload's pods.
    """
    labels: dict[str, str] = docs[(kind, name)]["spec"]["template"]["metadata"][
        "labels"
    ]
    return labels


def _library_file(name: str) -> bytes:
    """Read a file of the library chart, as packaged into `charts/`.

    Args:
        name: Path of the file within the library chart.

    Returns:
        The file's contents.
    """
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    (dependency,) = chart["dependencies"]
    archive = CHART / "charts" / f"foca-common-{dependency['version']}.tgz"
    with tarfile.open(archive) as package:
        member = package.extractfile(f"foca-common/{name}")
        assert member is not None
        return member.read()


def test_resources_default() -> None:
    """By default, the app, the worker, MongoDB and the broker are deployed.

    Flower is not; neither is an HTTPRoute or a Route.
    """
    docs = _render()
    assert {(kind, name) for kind, name in docs if kind != "NetworkPolicy"} == {
        ("ConfigMap", f"{APP}-config"),
        ("Deployment", APP),
        ("Service", APP),
        ("Ingress", APP),
        ("Deployment", WORKER),
        ("ConfigMap", f"{MONGODB}-init-script"),
        ("Deployment", MONGODB),
        ("PersistentVolumeClaim", MONGODB),
        ("Service", MONGODB),
        ("ConfigMap", f"{BROKER}-config"),
        ("PersistentVolumeClaim", BROKER),
        ("Service", BROKER),
        ("StatefulSet", BROKER),
    }
    assert not any(name.startswith(FLOWER) for _, name in docs)


def test_image_defaults_to_app_version() -> None:
    """Without a tag, the app and the worker run the image of `appVersion`.

    That is `latest` (the latest commit on `dev`) for the chart in the
    repository.
    """
    docs = _render()
    image = f"elixircloud/protes:{APP_VERSION}"
    assert _container(docs, APP)["image"] == image
    assert _container(docs, WORKER)["image"] == image


# Chart versions are SemVer, app versions PEP 440; the release workflow maps
# them
@pytest.mark.parametrize(
    ("chart_version", "app_version"),
    [("1.2.3", "1.2.3"), ("1.2.3-rc1", "1.2.3rc1")],
)
def test_image_of_packaged_chart(
    tmp_path: Path, chart_version: str, app_version: str
) -> None:
    """Packaged charts deploy the image of their `appVersion`.

    The release workflow sets it to the version of the release.
    """
    assert HELM is not None
    subprocess.run(  # noqa: S603 (argument list, no shell)
        [
            HELM,
            "package",
            str(CHART),
            "--version",
            chart_version,
            "--app-version",
            app_version,
            "-d",
            str(tmp_path),
        ],
        capture_output=True,
        check=True,
    )
    package = tmp_path / f"protes-{chart_version}.tgz"
    docs = {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in render("--set", "hostName=x", chart=package)
    }
    assert _container(docs, APP)["image"] == f"elixircloud/protes:{app_version}"
    # Packaged charts include the library chart, so they install offline
    with tarfile.open(package) as archive:
        member = archive.extractfile("protes/charts/foca-common/Chart.yaml")
        assert member is not None
        assert yaml.safe_load(member.read())["version"] == "0.2.0"


def test_image_set() -> None:
    """The image and its tag can be set, for the app and the worker."""
    docs = _render("--set", "protes.image=registry.example.org/protes:1.0")
    assert _container(docs, APP)["image"] == "registry.example.org/protes:1.0"
    assert _container(docs, WORKER)["image"] == "registry.example.org/protes:1.0"
    docs = _render("--set-string", "protes.tag=20261023")
    assert _container(docs, WORKER)["image"] == "elixircloud/protes:20261023"


def test_library_dependency() -> None:
    """The chart's templates are those of a released FOCA library chart.

    The library chart foca-common is pinned to a version of its OCI
    repository, locked in `Chart.lock`, and its archive to the digest in
    `charts.sha256`, which CI checks; the schema is that of this version.
    """
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    (dependency,) = chart["dependencies"]
    assert dependency["name"] == "foca-common"
    assert dependency["repository"] == "oci://ghcr.io/elixir-cloud-aai/charts"
    assert re.fullmatch(r"\d+\.\d+\.\d+", dependency["version"])
    lock = yaml.safe_load((CHART / "Chart.lock").read_text())
    assert lock["dependencies"] == [dependency]
    archive = f"charts/foca-common-{dependency['version']}.tgz"
    digest, name = (CHART / "charts.sha256").read_text().split()
    assert name == archive
    assert hashlib.sha256((CHART / archive).read_bytes()).hexdigest() == digest
    library = yaml.safe_load(_library_file("Chart.yaml"))
    assert (library["name"], library["version"]) == (
        "foca-common",
        dependency["version"],
    )
    schema = json.loads((CHART / "values.schema.json").read_text())
    assert f"foca-common {dependency['version']} " in schema["$comment"]


def test_schema_of_library() -> None:
    """The values schema is that of the library chart, with the app's values.

    That is the library's `app-values.schema.json`, with the app's key
    (`protes`, the library's app values plus `extraEnv`), Flower
    (`flower`), the public URL (`publicUrl`) and the TES backends
    (`tes`).
    """
    library = json.loads(_library_file("app-values.schema.json"))
    schema = json.loads((CHART / "values.schema.json").read_text())
    assert schema["$defs"] == library["$defs"]
    properties = dict(schema["properties"])
    app = properties.pop("protes")
    assert app["allOf"][0] == {"$ref": "#/$defs/app"}
    assert set(app["allOf"][1]["properties"]) == {"extraEnv"}
    assert set(properties.pop("flower")["properties"]) == set(
        yaml.safe_load((CHART / "values.yaml").read_text())["flower"]
    )
    assert set(properties.pop("tes")["properties"]) == {"backends"}
    assert properties.pop("publicUrl")["type"] == "string"
    assert properties == library["properties"]
    assert {key: schema[key] for key in ("$schema", "type")} == {
        key: library[key] for key in ("$schema", "type")
    }


def test_values_of_library() -> None:
    """The values have all keys of the library's values (of the petstore).

    The library chart has no values of its own; the app chart must have all
    of them with their defaults (cf. "Values" in FOCA's chart docs).
    """
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    schema = json.loads(_library_file("app-values.schema.json"))
    for key in schema["properties"]:
        assert key in values, key
    app = schema["$defs"]["app"]["properties"]
    assert set(app) <= set(values["protes"])


def test_config_overlay() -> None:
    """The overlay connects to the release's MongoDB, and has the config."""
    overlay = _overlay(
        _render("--set", "config.custom.storeLogs.execution_trace=false")
    )
    assert overlay["db"] == {"host": MONGODB}
    assert overlay["custom"] == {"storeLogs": {"execution_trace": False}}
    assert overlay["security"]["auth"]["issuers"] == ["https://login.example.org/oidc/"]


def test_worker() -> None:
    """The worker runs the app's Celery worker, with the app's settings.

    It has the app's image, config overlay, MongoDB credentials, broker URL
    and security contexts.
    """
    docs = _render()
    app = _container(docs, APP)
    worker = _container(docs, WORKER)
    module = worker["command"][worker["command"].index("--app") + 1]
    assert worker["command"][:2] == ["celery", "--app"]
    assert importlib.util.find_spec(module) is not None
    assert "worker" in worker["command"]
    assert worker["image"] == app["image"]
    assert worker["securityContext"] == app["securityContext"]
    refs = _secret_refs(worker)
    assert refs == {
        "MONGO_USERNAME": (MONGODB_SECRET, "username"),
        "MONGO_PASSWORD": (MONGODB_SECRET, "password"),
        "CELERY_BROKER_URL": (BROKER_SECRET, "broker-url"),
        "CELERY_RESULT_BACKEND": (BROKER_SECRET, "result-backend"),
    }
    assert _secret_refs(app) == refs
    assert _env(worker)["FOCA_CONFIG_OVERLAY"] == _env(app)["FOCA_CONFIG_OVERLAY"]


def test_worker_disabled() -> None:
    """The worker can be disabled, e.g., for one run elsewhere."""
    docs = _render("--set", "worker.enabled=false")
    assert ("Deployment", WORKER) not in docs
    assert "CELERY_BROKER_URL" in _env(_container(docs, APP))


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"mongodb_secret": None}, "mongodb.auth.existingSecret must be set"),
        ({"broker_secret": None}, "broker.existingSecret must be set"),
    ],
)
def test_secrets_required(kwargs: dict[str, Any], error: str) -> None:
    """Rendering fails without the Secrets of MongoDB and the broker.

    The chart creates no Secrets, so no credentials have defaults.
    """
    assert error in _render_error(**kwargs)


def test_no_secrets_rendered() -> None:
    """The chart renders no Secrets, also with Flower."""
    docs = _render(*FLOWER_ON, *WITH_EXTRA_ENV)
    assert not [name for kind, name in docs if kind == "Secret"]


def test_extra_env() -> None:
    """`protes.extraEnv` is added to the app's and the worker's containers.

    Only to theirs: not to MongoDB's, the broker's or Flower's.
    """
    docs = _render(*FLOWER_ON, *WITH_EXTRA_ENV)
    default = _render(*FLOWER_ON)
    for name in (APP, WORKER):
        env = _container(docs, name)["env"]
        assert env == [*_container(default, name)["env"], EXTRA_ENV]
    for name in (MONGODB, FLOWER):
        assert EXTRA_ENV["name"] not in _env(_container(docs, name))
    (broker,) = _pod(docs, "StatefulSet", BROKER)["containers"]
    assert EXTRA_ENV["name"] not in _env(broker)
    # The Deployments are otherwise unchanged
    for name in (APP, WORKER):
        docs[("Deployment", name)]["spec"]["template"]["spec"]["containers"][0][
            "env"
        ].pop()
        assert docs[("Deployment", name)] == default[("Deployment", name)]


def test_reserved_env() -> None:
    """The variables reserved for the chart are those it sets.

    In the app's and the worker's containers, so that `protes.extraEnv`
    can't set any of them again; and `MONGO_DBNAME`, which would move the
    app to a database that the chart does not provision.
    """
    template = (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: reserved-test\n"
        'data:\n  names: {{ include "protes.reservedEnv" . | quote }}\n'
    )
    docs = render_with({"reserved-test.yaml": template}, "--set", "hostName=x")
    (configmap,) = [doc for doc in docs if doc["metadata"]["name"] == "reserved-test"]
    reserved = set(json.loads(configmap["data"]["names"]))
    indexed = {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs}
    names = {
        name
        for container in (APP, WORKER)
        for name in _env(_container(indexed, container))
    }
    assert names | {"MONGO_DBNAME"} == reserved


def test_extra_env_worker_disabled() -> None:
    """`protes.extraEnv` doesn't render a worker that is disabled."""
    docs = _render(*WITH_EXTRA_ENV, "--set", "worker.enabled=false")
    assert ("Deployment", WORKER) not in docs
    assert EXTRA_ENV["name"] in _env(_container(docs, APP))


@pytest.mark.parametrize(
    ("env", "error"),
    [
        (
            [{"name": "CELERY_BROKER_URL", "value": "x"}],
            "protes.extraEnv must not set CELERY_BROKER_URL",
        ),
        (
            [{"name": "MONGO_PASSWORD", "value": "x"}],
            "protes.extraEnv must not set MONGO_PASSWORD",
        ),
        (
            [{"name": "MONGO_DBNAME", "value": "x"}],
            "protes.extraEnv must not set MONGO_DBNAME",
        ),
        (
            [{"name": "A", "value": "1"}, {"name": "A", "value": "2"}],
            "protes.extraEnv sets A more than once",
        ),
        ([{"value": "x"}], "extraEnv"),
        ({"name": "A"}, "extraEnv"),
    ],
)
def test_extra_env_invalid(env: Any, error: str) -> None:
    """Rendering fails for variables set by the chart, or set twice."""
    assert error in _render_error("--set-json", f"protes.extraEnv={json.dumps(env)}")


def test_flower_disabled() -> None:
    """Flower is disabled by default; enabling it requires credentials."""
    error = _render_error("--set", "flower.enabled=true")
    assert "flower.existingSecret must be set" in error
    error = _render_error(*FLOWER_ON, broker_secret=None)
    assert "broker.existingSecret must be set" in error


def test_flower() -> None:
    """Flower connects to the broker, with basic authentication.

    Its credentials and the broker URL come from Secrets; it runs as a
    non-root user with a read-only root file system and is not exposed.
    """
    docs = _render(*FLOWER_ON)
    container = _container(docs, FLOWER)
    assert _secret_refs(container) == {
        "CELERY_BROKER_URL": (BROKER_SECRET, "broker-url"),
        "FLOWER_BASIC_AUTH": ("flower", "basic-auth"),
    }
    assert set(_env(container)) == {"CELERY_BROKER_URL", "FLOWER_BASIC_AUTH"}
    values = yaml.safe_load((CHART / "values.yaml").read_text())["flower"]
    assert container["image"] == values["image"]
    assert re.fullmatch(r".+:\d+\.\d+\.\d+", container["image"])
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    pod = _pod(docs, "Deployment", FLOWER)
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert "--port=5555" in container["command"]
    (port,) = container["ports"]
    assert (port["name"], port["containerPort"]) == ("http", 5555)
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["tcpSocket"]["port"] == 5555
    service = docs[("Service", FLOWER)]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 5555, "protocol": "TCP", "targetPort": "http"}
    ]
    assert service["spec"]["selector"] == _labels(docs, "Deployment", FLOWER)
    assert not [
        doc
        for (kind, _), doc in docs.items()
        if kind == "Ingress" and doc["metadata"]["name"] != APP
    ]


def test_flower_external_broker() -> None:
    """Flower connects to an external broker, too (`broker.enabled: false`)."""
    docs = _render(*FLOWER_ON, "--set", "broker.enabled=false")
    assert ("StatefulSet", BROKER) not in docs
    container = _container(docs, FLOWER)
    assert _secret_refs(container)["CELERY_BROKER_URL"] == (
        BROKER_SECRET,
        "broker-url",
    )


def test_flower_openshift() -> None:
    """On OpenShift, Flower's user and group IDs are omitted."""
    docs = _render(*FLOWER_ON, "--api-versions", "security.openshift.io/v1")
    context = _pod(docs, "Deployment", FLOWER)["securityContext"]
    assert "runAsUser" not in context
    assert "runAsGroup" not in context


def test_flower_name_collision() -> None:
    """Rendering fails if the app and Flower would have the same name.

    That is if the name of the app has 63 characters and ends in `-flower`,
    which the name of Flower's resources keeps after truncating it.
    """
    name = f"{'x' * 56}-flower"
    error = _render_error(*FLOWER_ON, "--set", f"fullnameOverride={name}")
    assert "choose a fullnameOverride that does not end in -flower" in error


def _policies(docs: Docs) -> dict[str, Manifest]:
    """Get the NetworkPolicies by name.

    Args:
        docs: Rendered manifests.

    Returns:
        The NetworkPolicies, by name.
    """
    return {name: doc for (kind, name), doc in docs.items() if kind == "NetworkPolicy"}


def _peers(policy: Manifest) -> list[dict[str, Any]]:
    """Get the peers of a NetworkPolicy that restricts ingress only.

    Args:
        policy: NetworkPolicy with at most one ingress rule.

    Returns:
        The peers allowed to connect.
    """
    spec = policy["spec"]
    assert spec["policyTypes"] == ["Ingress"]
    assert "egress" not in spec
    return [peer for rule in spec["ingress"] for peer in rule["from"]]


def test_network_policies_default() -> None:
    """MongoDB and the broker admit only the app's and the worker's pods."""
    docs = _render()
    policies = _policies(docs)
    assert set(policies) == {MONGODB, BROKER}
    app_and_worker = [
        {"podSelector": {"matchLabels": _labels(docs, "Deployment", APP)}},
        {"podSelector": {"matchLabels": _labels(docs, "Deployment", WORKER)}},
    ]
    assert _peers(policies[MONGODB]) == app_and_worker
    assert _peers(policies[BROKER]) == app_and_worker
    assert policies[BROKER]["spec"]["podSelector"] == {
        "matchLabels": _labels(docs, "StatefulSet", BROKER)
    }


def test_network_policies_flower() -> None:
    """With Flower, the broker admits its pods, and no one may connect to it.

    `kubectl port-forward` is not affected by NetworkPolicies.
    """
    docs = _render(*FLOWER_ON)
    policies = _policies(docs)
    flower_labels = _labels(docs, "Deployment", FLOWER)
    assert policies[FLOWER]["spec"]["podSelector"] == {"matchLabels": flower_labels}
    assert policies[FLOWER]["spec"]["ingress"] == []
    to_broker = policies[f"{BROKER}-from-flower"]
    assert to_broker["spec"]["podSelector"] == {
        "matchLabels": _labels(docs, "StatefulSet", BROKER)
    }
    assert _peers(to_broker) == [{"podSelector": {"matchLabels": flower_labels}}]
    assert to_broker["spec"]["ingress"][0]["ports"] == [
        {"port": 5672, "protocol": "TCP"}
    ]


def test_network_policies_flower_peers() -> None:
    """Flower's NetworkPolicy admits `ingressFrom`, and can be disabled."""
    peer = {"podSelector": {"matchLabels": {"app": "proxy"}}}
    docs = _render(
        *FLOWER_ON,
        "--set-json",
        f"flower.networkPolicy.ingressFrom={json.dumps([peer])}",
    )
    policy = _policies(docs)[FLOWER]
    assert _peers(policy) == [peer]
    assert policy["spec"]["ingress"][0]["ports"] == [{"port": 5555, "protocol": "TCP"}]
    docs = _render(*FLOWER_ON, "--set", "flower.networkPolicy.enabled=false")
    assert FLOWER not in _policies(docs)
    docs = _render(*FLOWER_ON, "--set", "broker.networkPolicy.enabled=false")
    assert set(_policies(docs)) == {MONGODB, FLOWER}
    docs = _render(*FLOWER_ON, "--set", "broker.enabled=false")
    assert f"{BROKER}-from-flower" not in _policies(docs)


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("--set", "applicationDomain=example.org"), "applicationDomain was removed"),
        (("--set", "clusterType=openshift"), "clusterType was removed"),
        (("--set", "storageAccessMode=ReadWriteMany"), "storageAccessMode was removed"),
        (("--set", "celeryWorker.image=x"), "celeryWorker was removed"),
        (("--set", "rabbitmq.image=x"), "rabbitmq was removed"),
        (("--set", "protes.appName=x"), "protes.appName was removed"),
        (("--set", "mongodb.appName=x"), "mongodb.appName was removed"),
        (("--set", "flower.appName=x"), "flower.appName was removed"),
        (("--set", "mongodb.secret.databaseUser=x"), "mongodb.secret was removed"),
        (("--set", "flower.basicAuth=a:b"), "flower.basicAuth was removed"),
    ],
)
def test_legacy_values(args: tuple[str, ...], error: str) -> None:
    """Rendering fails for values of the legacy chart, which would be ignored."""
    assert error in _render_error(*args)


def test_legacy_values_first() -> None:
    """Legacy values are reported instead of their consequences.

    E.g., `applicationDomain` instead of the missing `hostName`.
    """
    with pytest.raises(subprocess.CalledProcessError) as exc:
        render("--set", "applicationDomain=example.org")
    assert "applicationDomain was removed" in exc.value.stderr
    assert "hostName" in exc.value.stderr.split("removed", 1)[1]


@pytest.mark.parametrize(
    ("args", "kwargs", "error"),
    [
        ((), {"issuer": None}, "config.security.auth.issuers must list"),
        (
            ("--set-json", 'config.security.auth.issuers=[" https://x/ "]'),
            {"issuer": None},
            "config.security.auth.issuers must list",
        ),
        ((), {"audience": None}, "config.security.auth.audience must list"),
        (
            (),
            {"group_claim": None},
            "config.security.access_control.claim_groups must name the claim",
        ),
        (
            (),
            {"admin_group": None},
            "group_mapping must map a group to the role admin",
        ),
        (
            ("--set", "config.security.access_control.require_admin=maybe"),
            {"admin_group": None},
            "require_admin must be true or false, not maybe",
        ),
        (
            ("--set-json", "config.security.access_control.group_mapping=[]"),
            {"admin_group": None},
            "group_mapping must be a map",
        ),
    ],
)
def test_security_config_invalid(
    args: tuple[str, ...], kwargs: dict[str, Any], error: str
) -> None:
    """Rendering fails without the settings the app refuses to start without."""
    assert error in _render_error(*args, **kwargs)


@pytest.mark.parametrize(
    "issuers",
    [
        [{"namespace": "x"}],
        [{"issuer": ""}],
        [{"issuer": " "}],
        [{"issuer": 1}],
        [{"issuer": None}],
        [{"issuer": " https://x.org/"}],
        ["https://x.org/", {"issuer": "https://y.org/ "}],
        [[]],
    ],
    ids=[
        "without-issuer",
        "empty",
        "blank",
        "number",
        "null",
        "padded",
        "second-padded",
        "list",
    ],
)
def test_issuers_invalid(issuers: list[Any]) -> None:
    """Rendering fails on issuers that are neither URLs nor maps with one.

    The app compares issuers with the `iss` claim as written, so surrounding
    whitespace is refused, too; checked before the audience.
    """
    error = _render_error(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        issuer=None,
        audience=None,
    )
    assert "config.security.auth.issuers must list" in error


def test_issuer_settings() -> None:
    """Issuers with settings of their own are passed to the app.

    E.g., to trust a second issuer, keeping the subjects of the first one
    bare (`namespace: ""`, see "Upgrading" in the docs); their audiences and
    group claims replace the global ones, which are then not required. The
    app's config model (FOCA's, the contract of the chart's checks) accepts
    the rendered settings.
    """
    issuers = [
        {
            "issuer": "https://login.example.org/oidc/",
            "namespace": "",
            "audience": ["portal"],
            "claim_groups": "memberships",
        },
        {"issuer": "https://other.example.org/", "audience": ["portal"]},
    ]
    docs = _render(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        issuer=None,
        audience=None,
        group_claim=None,
    )
    security = _overlay(docs)["security"]
    assert security["auth"]["issuers"] == issuers
    assert "audience" not in security["auth"]
    config = SecurityConfig.model_validate(security)
    assert config.auth.get_namespaces() == {
        "https://login.example.org/oidc/": None,
        "https://other.example.org/": "https://other.example.org/",
    }


def test_issuers_mixed() -> None:
    """Issuers may be URLs and maps, with the global audience and claim."""
    issuers = ["https://login.example.org/oidc/", {"issuer": "https://x.org/"}]
    docs = _render(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        issuer=None,
    )
    assert _overlay(docs)["security"]["auth"]["issuers"] == issuers


@pytest.mark.parametrize(
    "issuers",
    [
        [{"issuer": "https://x.org/", "audience": ["a"]}, "https://y.org/"],
        [{"issuer": "https://x.org/"}],
        [{"issuer": "https://x.org/", "audience": None}],
        [
            {"issuer": "https://x.org/", "audience": ["a"]},
            {"issuer": "https://y.org/"},
        ],
    ],
    ids=["url", "unset", "null", "last-unset"],
)
def test_issuer_audience_required(issuers: list[Any]) -> None:
    """Without a global audience, each issuer needs an audience of its own.

    It replaces the global audience for the issuer's tokens, as in the app.
    """
    error = _render_error(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        issuer=None,
        audience=None,
    )
    assert "config.security.auth.audience must list" in error


@pytest.mark.parametrize(
    ("claims", "valid"),
    [
        (["", ""], False),
        (["", None], True),
        ([None, None], True),
        (["", "null"], True),
    ],
    ids=["all-replaced", "one-replaced", "global", "null"],
)
def test_issuer_group_claim_replaced(claims: list[Any], valid: bool) -> None:
    """Group claims of issuers replace the global one, as in the app.

    Unless unset (`None` in `claims`) or null (`"null"`); group mappings
    need a valid group claim of at least one issuer.
    """
    issuers: list[dict[str, Any]] = []
    for index, claim in enumerate(claims):
        issuer: dict[str, Any] = {"issuer": f"https://{index}.example.org/"}
        if claim == "null":
            issuer["claim_groups"] = None
        elif claim is not None:
            issuer["claim_groups"] = claim
        issuers.append(issuer)
    args = ("--set-json", f"config.security.auth.issuers={json.dumps(issuers)}")
    if valid:
        assert ("Deployment", APP) in _render(*args, issuer=None)
        return
    error = _render_error(*args, issuer=None)
    assert "config.security.access_control.claim_groups must name the claim" in error


@pytest.mark.parametrize(
    ("setting", "error"),
    [
        ({"audience": []}, "audience of issuer https://x.org/ must list"),
        ({"audience": [" "]}, "audience of issuer https://x.org/ must list"),
        ({"audience": ["a", 1]}, "audience of issuer https://x.org/ must list"),
        ({"audience": "a"}, "audience of issuer https://x.org/ must list"),
        ({"claim_groups": " g"}, "claim_groups of issuer https://x.org/ must be"),
        ({"claim_groups": 1}, "claim_groups of issuer https://x.org/ must be"),
        ({"claim_group": "g"}, "claim_group of issuer map[claim_group:g"),
        ({"iss": "https://y.org/"}, "iss of issuer map[iss:https://y.org/"),
    ],
    ids=[
        "empty",
        "blank",
        "number",
        "string",
        "padded",
        "claim-number",
        "typo",
        "issuer-typo",
    ],
)
def test_issuer_settings_invalid(setting: dict[str, Any], error: str) -> None:
    """Rendering fails on settings of issuers that the app refuses.

    Also next to a valid global audience and group claim, which the
    issuer's settings would replace.
    """
    issuers = [{"issuer": "https://x.org/", **setting}]
    if "iss" in setting:
        issuers = [setting]
    assert error in _render_error(
        "--set-json", f"config.security.auth.issuers={json.dumps(issuers)}"
    )


def test_audience_invalid_with_issuer_audiences() -> None:
    """Rendering fails on a global audience the app refuses, if not required.

    The library checks it only if it is required (`requireAudience`).
    """
    issuers = [{"issuer": "https://x.org/", "audience": ["a"]}]
    error = _render_error(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        "--set-json",
        'config.security.auth.audience="a"',
        audience=None,
    )
    assert "config.security.auth.audience must list" in error


@pytest.mark.parametrize(
    ("issuers", "required"),
    [
        (["https://x.org/"], "true"),
        ([{"issuer": "https://x.org/"}], "true"),
        ([{"issuer": "https://x.org/", "audience": None}], "true"),
        (
            [{"issuer": "https://x.org/", "audience": ["a"]}, "https://y.org/"],
            "true",
        ),
        ([{"issuer": "https://x.org/", "audience": ["a"]}], "false"),
        (
            [
                {"issuer": "https://x.org/", "audience": ["a"]},
                {"issuer": "https://y.org/", "audience": ["b"]},
            ],
            "false",
        ),
    ],
    ids=["url", "unset", "null", "one-url", "own", "all-own"],
)
def test_require_audience(issuers: list[Any], required: str) -> None:
    """The library requires a global audience unless each issuer has one."""
    template = 'required: {{ include "protes.requireAudience" . | quote }}'
    docs = render_with(
        {
            "require-audience-test.yaml": (
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
                "  name: require-audience-test\ndata:\n  " + template + "\n"
            )
        },
        "--set",
        "hostName=tes.example.org",
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
    )
    (configmap,) = [
        doc for doc in docs if doc["metadata"]["name"] == "require-audience-test"
    ]
    assert configmap["data"]["required"] == required


def test_issuer_audience_not_verified() -> None:
    """Issuers need no audience if audiences are not verified."""
    issuers = [{"issuer": "https://x.org/", "namespace": ""}]
    docs = _render(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        "--set",
        "config.security.auth.verify_audience=false",
        issuer=None,
        audience=None,
    )
    assert _overlay(docs)["security"]["auth"]["issuers"] == issuers


def test_issuer_group_claim() -> None:
    """A group claim of an issuer suffices for group mappings."""
    issuers = [{"issuer": "https://x.org/", "claim_groups": "memberships"}]
    docs = _render(
        "--set-json",
        f"config.security.auth.issuers={json.dumps(issuers)}",
        issuer=None,
        group_claim=None,
    )
    assert "claim_groups" not in _overlay(docs)["security"]["access_control"]


@pytest.mark.parametrize("value", ["false", '"no"', "0"])
def test_admins_not_required(value: str) -> None:
    """Admins are not required if `require_admin` is false, as the app parses it."""
    docs = _render(
        "--set-json",
        f"config.security.access_control.require_admin={value}",
        admin_group=None,
        group_claim=None,
    )
    assert ("Deployment", APP) in docs


@pytest.mark.parametrize(
    "args",
    [
        ("--set", "config.custom.access_control.require_admin=false"),
        ("--set-json", "config.custom.access_control={}"),
    ],
)
def test_custom_access_control_removed(args: tuple[str, ...]) -> None:
    """Rendering fails with `custom.access_control`, as the app refuses it.

    Naming FOCA's `security.access_control.require_admin`, where its only
    setting moved.
    """
    error = _render_error(*args, admin_group=None)
    assert "config.custom.access_control was removed" in error
    assert "config.security.access_control.require_admin" in error


@pytest.mark.parametrize(
    "args, url",
    [
        ((), "https://tes.example.org/"),
        (("--set", "publicUrl=https://example.org/tes/"), "https://example.org/tes/"),
        (
            (
                "--set",
                "publicUrl=https://example.org/tes/",
                "--set",
                "config.server.public_url=https://other.example.org/",
            ),
            "https://other.example.org/",
        ),
    ],
)
def test_public_url(args: tuple[str, ...], url: str) -> None:
    """The public URL of the app is `https://<hostName>/`.

    Unless `publicUrl` sets it, or `config` (which takes precedence).
    """
    assert _overlay(_render(*args))["server"]["public_url"] == url


def test_public_url_unknown() -> None:
    """Without `hostName` and `publicUrl`, the app has no public URL."""
    docs = _render("--set", "hostName=", *NO_INGRESS)
    assert "server" not in _overlay(docs)


@pytest.mark.parametrize(
    "url", ["tes.example.org", "ftp://tes.example.org/", "https://tes.example.org/?a"]
)
def test_public_url_invalid(url: str) -> None:
    """`publicUrl` must be an `http` or `https` URL without query or fragment."""
    assert "/publicUrl" in _render_error("--set", f"publicUrl={url}")


@pytest.mark.parametrize("section", ["server", "custom"])
def test_generated_section_null(section: str) -> None:
    """`config.server: null` and `config.custom: null` are like no such section.

    The overlay has the generated sections (FOCA would refuse `null`).
    """
    docs = _render("--set-json", f"config.{section}=null")
    assert _overlay(docs)["server"] == {"public_url": "https://tes.example.org/"}
    assert _overlay(docs)["custom"] == {}


# Entries of `tes.backends`, with the chart's keys, and as in the app config
CHART_BACKENDS = [
    {
        "type": "ga4gh-tes",
        "url": "https://tes.example.org",
        "auth": "basic",
        "basic": {"usernameEnv": "TES_USERNAME", "passwordEnv": "TES_PASSWORD"},
        "labels": {"regionName": "eu-north"},
        "location": {"latitude": 60.17, "longitude": 24.94},
    },
    {
        "type": "ga4gh-service-registry",
        "id": "registry",
        "url": "https://registry.example.org/ga4gh/registry/v1",
        "urlSchemes": ["https"],
        "refreshInterval": 60,
        "maxStale": 600,
        "timeout": 2.5,
        "versions": ["1.1"],
        "environments": ["prod"],
        "auth": "none",
        "allowPrivateAddresses": True,
        "registryAllowPrivateAddresses": True,
        "labels": {"source": "registry"},
    },
]
CONFIG_BACKENDS = [
    {
        "type": "ga4gh-tes",
        "url": "https://tes.example.org",
        "auth": "basic",
        "basic": {"username_env": "TES_USERNAME", "password_env": "TES_PASSWORD"},
        "labels": {"regionName": "eu-north"},
        "location": {"latitude": 60.17, "longitude": 24.94},
    },
    {
        "type": "ga4gh-service-registry",
        "id": "registry",
        "url": "https://registry.example.org/ga4gh/registry/v1",
        "url_schemes": ["https"],
        "refresh_interval": 60,
        "max_stale": 600,
        "timeout": 2.5,
        "versions": ["1.1"],
        "environments": ["prod"],
        "auth": "none",
        "allow_private_addresses": True,
        "registry_allow_private_addresses": True,
        "labels": {"source": "registry"},
    },
]
WITH_BACKENDS = ("--set-json", f"tes.backends={json.dumps(CHART_BACKENDS)}")


def test_tes_backends() -> None:
    """`tes.backends` sets the backends of the app config, with its keys.

    The keys of labels are kept; other settings of `config.custom.tes` are
    merged.
    """
    docs = _render(
        *WITH_BACKENDS,
        "--set",
        "config.custom.tes.client_credentials.issuer=https://idp.example.org/",
    )
    tes = _overlay(docs)["custom"]["tes"]
    assert tes["backends"] == CONFIG_BACKENDS
    assert tes["client_credentials"] == {"issuer": "https://idp.example.org/"}


def test_tes_backends_default() -> None:
    """Without `tes.backends`, the overlay sets no backends."""
    assert _overlay(_render())["custom"] == {}
    docs = _render("--set-json", f"config.custom.tes.backends={json.dumps([])}")
    assert _overlay(docs)["custom"] == {"tes": {"backends": []}}


@pytest.mark.parametrize(
    "entry, error",
    [
        ({"type": "ga4gh-tes"}, "missing property 'url'"),
        ({"type": "ga4gh-wes", "url": "https://wes.example.org"}, "/tes/backends/0"),
        (
            {"type": "ga4gh-tes", "url": "https://tes.example.org", "urlSchemes": []},
            "/tes/backends/0",
        ),
        (
            {
                "type": "ga4gh-tes",
                "url": "https://tes.example.org",
                "url_schemes": ["https"],
            },
            "/tes/backends/0",
        ),
        (
            {
                "type": "ga4gh-tes",
                "url": "https://tes.example.org",
                "basic": {"usernameEnv": "TES_USERNAME"},
            },
            "/tes/backends/0",
        ),
        (
            {
                "type": "ga4gh-service-registry",
                "url": "https://registry.example.org",
                "auth": "basic",
            },
            "/tes/backends/0",
        ),
    ],
)
def test_tes_backends_invalid(entry: dict[str, Any], error: str) -> None:
    """Entries of `tes.backends` must have the chart's keys and values."""
    assert error in _render_error("--set-json", f"tes.backends={json.dumps([entry])}")


@pytest.mark.parametrize("key", ["service_list", "discovery"])
def test_tes_backends_removed(key: str) -> None:
    """Rendering fails with the settings replaced by `tes.backends`."""
    error = _render_error("--set-json", f"config.custom.tes.{key}={json.dumps([])}")
    assert f"config.custom.tes.{key} was removed" in error
    assert "tes.backends" in error


def test_tes_backends_twice() -> None:
    """Rendering fails with backends in `tes.backends` and in `config`."""
    error = _render_error(
        *WITH_BACKENDS, "--set-json", f"config.custom.tes.backends={json.dumps([])}"
    )
    assert "either in tes.backends or in config.custom.tes.backends" in error


def test_registry_public_url_null() -> None:
    """`config.server.public_url: null` takes precedence over `hostName`.

    Also for registries in `config.custom.tes.backends`.
    """
    registry = [{"type": "ga4gh-service-registry", "url": "https://r.example.org"}]
    for args in (
        WITH_BACKENDS,
        ("--set-json", f"config.custom.tes.backends={json.dumps(registry)}"),
    ):
        error = _render_error(*args, "--set-json", "config.server.public_url=null")
        assert "hostName or publicUrl" in error


def test_custom_tes_null() -> None:
    """Rendering fails on `config.custom.tes: null`, which the app refuses."""
    error = _render_error(*WITH_BACKENDS, "--set-json", "config.custom.tes=null")
    assert "config.custom.tes must be a map" in error


def test_registry_without_public_url() -> None:
    """Registries need a public URL of the app, as the app requires on startup.

    Unless `config.server.public_url` sets it.
    """
    args = ("--set", "hostName=", *NO_INGRESS, *WITH_BACKENDS)
    assert "hostName or publicUrl" in _render_error(*args)
    docs = _render(*args, "--set", "config.server.public_url=https://x.example.org/")
    assert _overlay(docs)["server"] == {"public_url": "https://x.example.org/"}


@pytest.mark.parametrize(
    "args",
    [
        ("--set", "config.security.access_control.enabled=false"),
        ("--set", "config.security.access_control.enforce=off"),
        (
            "--set",
            "config.security.access_control.enabled=false",
            "--set",
            "config.security.access_control.enforce=false",
        ),
    ],
    ids=["disabled", "not-enforced", "both"],
)
def test_access_control_required(args: tuple[str, ...]) -> None:
    """Access control must be enabled and enforced, as the app requires.

    So that only the owners of tasks can access them.
    """
    error = _render_error(*args)
    assert "access_control.enabled and enforce must be true" in error


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (
            ("--set", "config.security.access_control.enabled=maybe"),
            "config.security.access_control.enabled must be true or false",
        ),
        (
            ("--set", "config.security.access_control.enforce=maybe"),
            "config.security.access_control.enforce must be true or false",
        ),
    ],
)
def test_access_control_invalid(args: tuple[str, ...], error: str) -> None:
    """The booleans of access control are parsed as the app parses them."""
    assert error in _render_error(*args)


@pytest.mark.parametrize("value", ["true", '"yes"', "1"])
def test_access_control_enabled(value: str) -> None:
    """Access control may be enabled and enforced explicitly, too."""
    docs = _render(
        "--set-json",
        f"config.security.access_control.enabled={value}",
        "--set-json",
        f"config.security.access_control.enforce={value}",
    )
    assert ("Deployment", APP) in docs


def test_upgrade() -> None:
    """Upgrades render without a cluster (no legacy resources found)."""
    assert ("Deployment", APP) in _render("--is-upgrade")


@pytest.mark.parametrize(
    ("kind", "owner", "labels", "fails"),
    [
        ("Deployment", "protes", {"app": "protes"}, True),
        ("PersistentVolumeClaim", "protes", {}, True),
        ("Deployment", "protes", {"app.kubernetes.io/instance": "protes"}, False),
        ("Deployment", "other", {"app": "protes"}, False),
        ("Deployment", "", {"app": "protes"}, False),
    ],
    ids=["legacy", "legacy-claim", "current", "other-release", "unmanaged"],
)
def test_upgrade_legacy(
    kind: str, owner: str, labels: dict[str, str], fails: bool
) -> None:
    """Upgrades fail if the release has resources of the legacy chart.

    The legacy chart is versions 2 and 3, whose volume with the MongoDB data
    the upgrade would delete; checked with a Deployment or claim as returned
    by `lookup`, which finds nothing in `helm template`.
    """
    resource = {
        "metadata": {
            "name": "protes",
            "annotations": {"meta.helm.sh/release-name": owner} if owner else {},
            "labels": labels,
        }
    }
    # A template string literal (JSON-quoted) with the resource as JSON
    literal = json.dumps(json.dumps(resource))
    template = (
        f'{{{{- include "protes.failIfLegacy" (dict "root" . "kind" "{kind}"'
        f' "resource" (fromJson {literal})) }}}}'
    )
    args = ({"legacy-test.yaml": template}, "--set", "hostName=x")
    if fails:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            render_with(*args)
        assert "legacy chart (version 2 or 3)" in exc.value.stderr
        assert f"({kind} protes)" in exc.value.stderr
    else:
        render_with(*args)


def test_probes() -> None:
    """The app is probed at its Swagger UI, which needs no token or database."""
    container = _container(_render(), APP)
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {
            "path": "/ga4gh/tes/v1/ui/",
            "port": 8080,
        }
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "ENV HEALTHCHECK_URL=http://localhost:8080/ga4gh/tes/v1/ui/" in dockerfile


def test_ingress_all_paths() -> None:
    """The Ingress routes all paths of `hostName` to the app's Service."""
    ingress = _render()[("Ingress", APP)]
    (rule,) = ingress["spec"]["rules"]
    assert rule["host"] == "tes.example.org"
    (path,) = rule["http"]["paths"]
    assert path["path"] == "/"
    assert path["backend"]["service"]["name"] == APP


def test_http_route() -> None:
    """The app can be exposed with a Gateway API HTTPRoute instead."""
    parent = {"name": "gateway", "namespace": "gateway-system", "sectionName": "https"}
    docs = _render(
        *NO_INGRESS,
        "--set",
        "protes.httpRoute.enabled=true",
        "--set-json",
        f"protes.httpRoute.parentRefs={json.dumps([parent])}",
    )
    route = docs[("HTTPRoute", APP)]
    assert route["spec"]["hostnames"] == ["tes.example.org"]
    assert route["spec"]["parentRefs"] == [parent]
    assert ("Ingress", APP) not in docs


def test_ingress_no_controller_defaults() -> None:
    """The Ingress has no annotations for a particular ingress controller.

    In particular, no size limit of request bodies for ingress-nginx, whose
    default (1 MiB) is the app's; annotations of the values are kept.
    """
    ingress = _render()[("Ingress", APP)]
    assert not ingress["metadata"].get("annotations")
    annotation = {"nginx.ingress.kubernetes.io/proxy-body-size": "8m"}
    ingress = _render(
        "--set-json", f"protes.ingress.annotations={json.dumps(annotation)}"
    )[("Ingress", APP)]
    assert ingress["metadata"]["annotations"] == annotation


def test_route() -> None:
    """On OpenShift without an Ingress, a Route is created unless disabled."""
    openshift = ("--api-versions", "route.openshift.io/v1")
    docs = _render(*NO_INGRESS, *openshift)
    assert ("Route", APP) in docs
    docs = _render(*NO_INGRESS, *openshift, "--set", "protes.route.enabled=false")
    assert ("Route", APP) not in docs


def test_network_policy_app_peers() -> None:
    """The app's NetworkPolicy has no default peers, and needs some."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["protes"]["networkPolicy"] == {"enabled": False, "ingressFrom": []}
    enabled = ("--set", "protes.networkPolicy.enabled=true")
    assert "networkpolicy-peers" in _render_error(*enabled)
    peer = {"podSelector": {"matchLabels": {"app": "proxy"}}}
    docs = _render(
        *enabled, "--set-json", f"protes.networkPolicy.ingressFrom={json.dumps([peer])}"
    )
    assert _peers(_policies(docs)[APP]) == [peer]


@pytest.mark.parametrize(
    ("args", "url", "warnings"),
    [
        ((), "https://tes.example.org", ["The Ingress has no TLS"]),
        (("--set", "protes.ingress.https.enabled=true"), "https://tes.example.org", []),
        (NO_INGRESS, "http://localhost:8080", ["Neither an Ingress"]),
        (
            ("--set", "worker.enabled=false"),
            "https://tes.example.org",
            ["The Ingress has no TLS", "The Celery worker is disabled"],
        ),
    ],
    ids=["ingress", "ingress-https", "none", "no-worker"],
)
def test_notes(args: tuple[str, ...], url: str, warnings: list[str]) -> None:
    """The notes show how to reach the app, with warnings where needed."""
    notes = render_notes("--set", "hostName=tes.example.org", "-n", "tes", *args)
    assert f'curl -X GET "{url}/ga4gh/tes/v1/service-info"' in notes
    for warning in warnings:
        assert warning in notes
    assert notes.count("WARNING") + notes.count("Neither an Ingress") == len(warnings)
    assert "Flower" not in notes


def test_notes_route_disabled() -> None:
    """The notes name a disabled Route, not missing Route support, as reason."""
    args = (
        "--set",
        "hostName=x",
        *NO_INGRESS,
        "--api-versions",
        "route.openshift.io/v1",
    )
    notes = render_notes(*args, "--set", "protes.route.enabled=false")
    assert "Neither an Ingress" in notes
    assert (
        "nor an OpenShift Route\n(protes.route.enabled is false) was created" in notes
    )
    assert "does not serve" not in notes
    notes = render_notes("--set", "hostName=x", *NO_INGRESS)
    assert "(the cluster does\nnot serve route.openshift.io/v1)" in notes
    assert "Neither an Ingress" not in render_notes(*args)


def test_notes_flower() -> None:
    """With Flower, the notes show how to reach it."""
    notes = render_notes("--set", "hostName=x", "-n", "tes", *FLOWER_ON)
    assert f"kubectl port-forward svc/{FLOWER} 5555:5555 -n tes" in notes


def test_ci_values() -> None:
    """The values of CI render, and set what each deployment must set."""
    assert HELM is not None
    for files in (["test-values.yaml"], ["test-values.yaml", "optional-values.yaml"]):
        args = [arg for name in files for arg in ("-f", str(CHART / "ci" / name))]
        docs = render(
            *args,
            mongodb_secret=None,
            broker_secret=None,
            issuer=None,
            group_claim=None,
            admin_group=None,
            audience=None,
        )
        assert any(doc["kind"] == "Deployment" for doc in docs)
