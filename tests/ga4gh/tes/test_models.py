"""Tests of the TES models, their conversion and the forwarding log."""

from datetime import UTC, datetime
from pathlib import Path

from connexion import FlaskApp
import pytest
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
from pro_tes.gateway import relayed_task
from pro_tes.utils.models import TaskModelConverter

ADDITIONS = Path(__file__).parents[3] / "pro_tes" / "api" / "additions.openapi.yaml"


def test_executor_defaults() -> None:
    """Executors without command get none, tasks without executors none."""
    assert not TesExecutor(image="alpine").command
    assert not TesTask().executors
    assert not DbDocument().task_original.executors


def test_ignore_error_relayed() -> None:
    """`ignore_error` of executors is relayed to TES backends (cf. #176)."""
    executor = {"image": "alpine", "command": ["true"], "ignore_error": True}
    task = relayed_task({"executors": [executor]}, prefix="gateway.")
    assert task.executors[0].ignore_error is True


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


def test_task_converted() -> None:
    """Tasks of backends are converted with all their fields."""
    time = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    task = tes.Task(
        id="remote-1",
        state="COMPLETE",
        creation_time=time,
        inputs=[tes.Input(url="s3://bucket/in", path="/in", type="FILE")],
        outputs=[tes.Output(url="s3://bucket/out", path="/out", type="DIRECTORY")],
        resources=tes.Resources(cpu_cores=2, ram_gb=4.0, zones=["eu"]),
        executors=[tes.Executor(image="alpine", command=["true"])],
        logs=[
            tes.TaskLog(
                start_time=time,
                end_time=time,
                logs=[
                    tes.ExecutorLog(
                        start_time=time, end_time=time, stdout="out", exit_code=0
                    )
                ],
                outputs=[tes.OutputFileLog(url="s3://bucket/out", path="/out")],
            ),
            tes.TaskLog(),
        ],
    )
    converted = TaskModelConverter(task=task).convert_task()
    assert converted.creation_time == time.isoformat()
    assert converted.inputs is not None
    assert converted.inputs[0].url == "s3://bucket/in"
    assert converted.outputs is not None
    assert converted.outputs[0].type == "DIRECTORY"
    assert converted.resources is not None
    assert (converted.resources.cpu_cores, converted.resources.zones) == (2, ["eu"])
    assert converted.logs is not None
    log, empty = converted.logs
    assert (log.start_time, log.end_time) == (time.isoformat(), time.isoformat())
    (executor_log,) = log.logs
    assert (executor_log.start_time, executor_log.stdout) == (time.isoformat(), "out")
    assert (empty.logs, empty.outputs, empty.start_time) == ([], [], None)


def test_forward_logged_without_metadata(app: FlaskApp) -> None:
    """Forwards are logged in logs without metadata."""
    document = DbDocument(task=TesTask(logs=[TesTaskLog(logs=[], outputs=[])]))
    with app.app.app_context():
        document = TaskRuns()._update_task_metadata(
            db_document=document, tes_url="https://tes.example.org", remote_task_id="1"
        )
    assert document.task.logs is not None
    assert document.task.logs[0].metadata is not None
    assert document.task.logs[0].metadata.forwarded_to is not None
    assert document.task.logs[0].metadata.forwarded_to[0].id == "1"


def test_forward_logged_no_logs(app: FlaskApp) -> None:
    """Forwards of tasks without logs can't be logged."""
    with app.app.app_context(), pytest.raises(ValueError, match="has no logs"):
        TaskRuns()._update_task_metadata(
            db_document=DbDocument(),
            tes_url="https://tes.example.org",
            remote_task_id="1",
        )
