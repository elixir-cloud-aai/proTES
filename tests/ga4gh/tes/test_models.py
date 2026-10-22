"""Tests of the TES models, their conversion and the forwarding log."""

from datetime import datetime
from pathlib import Path

from connexion import FlaskApp
import tes
import yaml

from pro_tes.ga4gh.tes.models import (
    DbDocument,
    Metadata,
    TesExecutor,
    TesForward,
    TesTask,
    TesTaskLog,
)
from pro_tes.ga4gh.tes.task_runs import TaskRuns
from pro_tes.utils.models import TaskModelConverter

ADDITIONS = Path(__file__).parents[3] / "pro_tes" / "api" / "additions.openapi.yaml"


def test_executor_defaults() -> None:
    """Executors without command get none, tasks without executors none."""
    assert not TesExecutor(image="alpine").command
    assert not TesTask().executors
    assert not DbDocument().task_original.executors


def test_ignore_error_relayed(app: FlaskApp) -> None:
    """`ignore_error` of executors is relayed to TES backends (cf. #176)."""
    executor = {"image": "alpine", "command": ["true"], "ignore_error": True}
    with app.app.app_context():
        payload = TaskRuns()._sanitize_request(payload={"executors": [executor]})
    assert payload["executors"][0].ignore_error is True


def test_ignore_error_converted() -> None:
    """`ignore_error` of executors of backend tasks is kept."""
    task = tes.Task(
        executors=[tes.Executor(image="alpine", command=["true"], ignore_error=True)]
    )
    converted = TaskModelConverter(task=task).convert_task()
    assert converted.executors[0].ignore_error is True


def test_forwarded_to_not_recursive() -> None:
    """Forwards are a list of `tesForward` without references (cf. #173)."""
    schemas = yaml.safe_load(ADDITIONS.read_text(encoding="utf-8"))["components"][
        "schemas"
    ]
    forwarded_to = schemas["tesTaskLog"]["properties"]["metadata"]["properties"][
        "forwarded_to"
    ]
    assert forwarded_to["type"] == "array"
    assert forwarded_to["items"] == {"$ref": "#/components/schemas/tesForward"}
    assert "tesNextTes" not in schemas
    assert "$ref" not in str(schemas["tesForward"])
    assert schemas["tesForward"]["required"] == ["id", "url"]


def test_nested_forwards_flattened() -> None:
    """Nested forwards of earlier versions are read as a list."""
    metadata = Metadata.model_validate(
        {
            "forwarded_to": {
                "id": "task-1",
                "url": "https://first.example.org",
                "forwarded_to": {"id": "task-2", "url": "https://second.example.org"},
            }
        }
    )
    assert metadata.forwarded_to == [
        TesForward(id="task-1", url="https://first.example.org"),
        TesForward(id="task-2", url="https://second.example.org"),
    ]


def test_forward_logged(app: FlaskApp) -> None:
    """Forwards of a task are appended to its logs, with an RFC 3339 time."""
    earlier = TesForward(
        id="upstream", url="https://upstream.example.org", forward_time="t"
    )
    document = DbDocument(
        task=TesTask(
            logs=[
                TesTaskLog(
                    logs=[],
                    outputs=[],
                    metadata=Metadata(forwarded_to=[earlier]),
                ),
                TesTaskLog(logs=[], outputs=[], metadata=Metadata()),
            ]
        )
    )
    with app.app.app_context():
        document = TaskRuns()._update_task_metadata(
            db_document=document,
            tes_url="https://tes.example.org",
            remote_task_id="task-1",
        )
    assert document.task.logs is not None
    assert len(document.task.logs) == 2
    for log, previous in zip(document.task.logs, [[earlier], []], strict=True):
        assert log.metadata is not None
        assert log.metadata.forwarded_to is not None
        *before, forward = log.metadata.forwarded_to
        assert before == previous
        assert (forward.id, forward.url) == ("task-1", "https://tes.example.org")
        assert forward.forward_time is not None
        assert datetime.fromisoformat(forward.forward_time).tzinfo is not None
