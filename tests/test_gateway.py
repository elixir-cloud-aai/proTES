"""Tests of the callbacks of proTES for FOCA's plugin engine."""

from collections import UserDict
from dataclasses import replace
from pathlib import Path
from typing import Any

from connexion import FlaskApp
from foca import get_custom_config
from foca.models.config import Config
from foca.plugins import GatewayError, Headers, OutboundRequest
from foca.plugins.api import SPEC_PATH
from foca.plugins.backends import ServiceEntry
from pydantic import ValidationError
import pytest

from pro_tes import gateway
from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import InlineContentTooLarge, exceptions

TASK: dict[str, Any] = {
    "executors": [{"image": "alpine", "command": ["true"]}],
    "creation_time": "2026-10-30T12:00:00+00:00",
    "logs": [{"logs": []}],
    "tags": {"gateway.pipeline": "fast", "project": "x"},
}


def test_settings() -> None:
    """ProTES is a gateway of the TES API, with its callbacks."""
    assert gateway.GATEWAY.api == "tes"
    assert gateway.GATEWAY.name == "proTES"
    assert gateway.GATEWAY.validate is gateway.validate_task
    assert gateway.GATEWAY.sanitize is gateway.sanitize_task
    assert gateway.GATEWAY.validate_outbound is gateway.check_outbound


def test_backend_entries(app: FlaskApp) -> None:
    """The backend list is that of `custom.tes.backends`."""
    with app.app.app_context():
        config = app.app.config.foca
        assert gateway.backend_entries(config) is config.custom.tes.backends


@pytest.mark.parametrize("payload", [None, [], "task", UserDict({"executors": []})])
def test_not_an_object(payload: Any) -> None:
    """Tasks that are no JSON objects are invalid."""
    with pytest.raises(TypeError, match="JSON objects"):
        gateway.validate_task(payload)
    with pytest.raises(TypeError, match="JSON objects"):
        gateway.tes_task(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"executors": "none"},
        {"executors": [{"image": "alpine", "command": ["true"], "unknown": 1}]},
        {"executors": [{"image": "alpine"}], "unknown": 1},
    ],
)
def test_invalid(payload: dict[str, Any]) -> None:
    """Tasks that the models of proTES or py-tes reject are invalid."""
    with pytest.raises((TypeError, ValueError, ValidationError)):
        gateway.validate_task(payload)


def test_relayed_task() -> None:
    """Relayed tasks have no fields that servers set, and no gateway tags."""
    task = gateway.relayed_task(TASK, prefix="gateway.")
    assert task.creation_time is None
    assert task.logs is None
    assert task.tags == {"project": "x"}
    assert task.executors[0].image == "alpine"
    assert TASK["tags"]["gateway.pipeline"] == "fast"
    assert gateway.relayed_task({"executors": []}, prefix="gateway.").tags is None


def test_relayed_task_credentials() -> None:
    """Relayed tasks have no credentials in URLs; the payload is kept."""
    payload: dict[str, Any] = {
        "executors": [{"image": "alpine", "command": ["true"]}],
        "inputs": [{"url": "s3://key:secret@bucket/in", "path": "/in"}],
    }
    task = gateway.relayed_task(payload, prefix="gateway.")
    assert task.inputs[0].url == "s3://bucket/in"
    assert payload["inputs"][0]["url"] == "s3://key:secret@bucket/in"


def test_check_outbound(app: FlaskApp) -> None:
    """Outbound requests need tasks that can be relayed, within the limits."""
    entry = ServiceEntry(type="ga4gh-tes", url="https://tes.example.org")
    outbound = OutboundRequest(
        backend=entry.backend(), method="POST", path=gateway.TASKS_PATH, payload=TASK
    )
    with app.app.app_context():
        gateway.check_outbound(outbound)
        for changed in (
            replace(outbound, method="GET"),
            replace(outbound, path=f"{gateway.TASKS_PATH}/other"),
        ):
            with pytest.raises(ValueError, match="Tasks are created with POST"):
                gateway.check_outbound(changed)
        for query in (5, [1], {"a": "b"}, {"a": [1]}):
            with pytest.raises(ValueError, match="Query parameters"):
                gateway.check_outbound(replace(outbound, query=query))  # type: ignore[arg-type]
        gateway.check_outbound(replace(outbound, query={"a": ["b"]}))
        with pytest.raises(ValueError, match="reserved for proTES: Host"):
            gateway.check_outbound(
                replace(outbound, headers=Headers({"Host": "other.example.org"}))
            )
        gateway.check_outbound(replace(outbound, headers=Headers({"X-A": "b"})))
        outbound.payload = {"executors": "none"}
        with pytest.raises((TypeError, ValueError)):
            gateway.check_outbound(outbound)
        outbound.payload = {
            "executors": [{"image": "alpine", "command": ["true"]}],
            "inputs": [{"content": "x" * 32, "path": "/content"}],
        }
        get_custom_config(CustomConfig).controllers.post_task.max_inline_content = 16
        with pytest.raises(InlineContentTooLarge):
            gateway.check_outbound(outbound)


def test_sanitize_task(app: FlaskApp) -> None:
    """Credentials in URLs are removed; inline contents are limited."""
    task = {
        "inputs": [
            {"url": "s3://key:secret@bucket/in", "path": "/in"},
            {"content": "x" * 32, "path": "/content"},
        ]
    }
    with app.app.app_context():
        assert gateway.sanitize_task(task) is task
        assert task["inputs"][0]["url"] == "s3://bucket/in"
        get_custom_config(CustomConfig).controllers.post_task.max_inline_content = 16
        with pytest.raises(InlineContentTooLarge):
            gateway.sanitize_task(task)
    assert gateway.sanitize_task(None) is None


def test_error_mapping() -> None:
    """Mapped errors of the plugin engine keep their status."""
    for exc, entry in exceptions.items():
        if issubclass(exc, GatewayError):
            assert entry["status"] == exc.code, exc.__name__


@pytest.mark.parametrize("path", ["tes.yaml", ["tes.yaml"]])
def test_add_plugin_listing(path: str | list[str]) -> None:
    """FOCA's plugin listing is added to the TES API once, not to other APIs."""
    config = Config.model_validate(
        {
            "api": {
                "specs": {
                    "other": {"path": "other.yaml"},
                    "tes": {"path": path},
                }
            }
        }
    )
    gateway.add_plugin_listing(config)
    gateway.add_plugin_listing(config)
    other, tes_spec = config.api.specs
    assert other.path == [Path("other.yaml").resolve()]
    assert tes_spec.path == [Path(SPEC_PATH), Path("tes.yaml").resolve()]


def test_add_plugin_listing_no_tes() -> None:
    """Without the TES API, the listing is not served."""
    config = Config.model_validate({"api": {"specs": {"other": {"path": "o.yaml"}}}})
    gateway.add_plugin_listing(config)
    (other,) = config.api.specs
    assert other.path == [Path("o.yaml").resolve()]
