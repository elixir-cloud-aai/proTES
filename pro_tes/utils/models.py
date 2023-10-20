"""Class to convert py-tes to proTES TES task model."""

from datetime import datetime
from typing import Optional

from tes.models import TaskLog, Task  # type: ignore

from pro_tes.ga4gh.tes.models import (
    TesExecutor,
    TesExecutorLog,
    TesFileType,
    TesInput,
    TesTaskLog,
    TesOutput,
    TesOutputFileLog,
    TesResources,
    TesState,
    TesTask,
)


class TaskModelConverter:
    """Convert py-tes to proTES TES task model.

    Convert :class:`tes.models.Task` to
    :class:`pro_tes.ga4gh.tes.models.TesTask`

    Args:
        task: Instance of :class:`tes.models.Task`

    Attributes:
        task: Instance of :class:`tes.models.Task`
    """

    def __init__(self, task: Task) -> None:
        """Construct object instance."""
        self.task: Task = task

    def convert_task(self) -> TesTask:
        """Convert py-tes to proTES TES task.

        Returns:
            Instance of :class:`pro_tes.ga4gh.tes.models.TesTask`
        """
        state = self.convert_state()
        inputs = self.convert_inputs()
        outputs = self.convert_outputs()
        resources = self.convert_resources()
        executors = self.convert_executors()
        logs = self.convert_logs()
        return TesTask(
            id=self.task.id,
            state=state,
            name=self.task.name,
            description=self.task.description,
            inputs=inputs,
            outputs=outputs,
            resources=resources,
            executors=executors,
            volumes=self.task.volumes,
            tags=self.task.tags,
            logs=logs,
            creation_time=TaskModelConverter.convert_time(
                timestamp=self.task.creation_time
            ),
        )

    def convert_state(self) -> TesState:
        """Convert py-tes to proTES TES task state.

        Returns:
            Instance of :class:`pro_tes.ga4gh.tes.models.TesState`
        """
        if self.task.state is None:
            state = TesState("UNKNOWN")
        else:
            state = TesState(self.task.state)
        return state

    def convert_inputs(self) -> Optional[list[TesInput]]:
        """Convert py-tes to proTES TES task inputs.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesInput`
        """
        if self.task.inputs is None:
            inputs = None
        else:
            inputs = [
                TesInput(
                    url=_input.url,
                    path=_input.path,
                    type=TesFileType[_input.type],
                    description=_input.description,
                    name=_input.name,
                    content=_input.content,
                )
                for _input in self.task.inputs
            ]
        return inputs

    def convert_outputs(self) -> Optional[list[TesOutput]]:
        """Convert py-tes to proTES TES task outputs.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesOutput`
        """
        if self.task.outputs is None:
            outputs = None
        else:
            outputs = [
                TesOutput(
                    url=_output.url,
                    path=_output.path,
                    type=TesFileType[_output.type],
                    name=_output.name,
                    description=_output.description,
                )
                for _output in self.task.outputs
            ]
        return outputs

    def convert_resources(self) -> Optional[TesResources]:
        """Convert py-tes to proTES TES task resources.

        Returns:
            Instance of :class:`pro_tes.ga4gh.tes.models.TesResources`
        """
        if self.task.resources is None:
            resources = None
        else:
            resources = TesResources(
                cpu_cores=self.task.resources.cpu_cores,
                ram_gb=self.task.resources.ram_gb,
                disk_gb=self.task.resources.disk_gb,
                preemptible=self.task.resources.preemptible,
                zones=self.task.resources.zones,
            )
        return resources

    def convert_executors(self) -> list[TesExecutor]:
        """Convert py-tes to proTES TES task executors.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesExecutor`
        """
        if self.task.executors is None:
            executors = []
        else:
            executors = [
                TesExecutor(
                    image=executor.image,
                    command=executor.command,
                    workdir=executor.workdir,
                    stdin=executor.stdin,
                    stdout=executor.stdout,
                    stderr=executor.stderr,
                    env=executor.env,
                )
                for executor in self.task.executors
            ]
        return executors

    def convert_logs(self) -> Optional[list[TesTaskLog]]:
        """Convert py-tes to proTES TES task logs.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesTaskLog`
        """
        if self.task.logs is None:
            logs = None
        else:
            logs = []
            for log in self.task.logs:
                executor_logs = self.convert_executor_logs(log=log)
                output_file_logs = self.convert_output_file_logs(log=log)
                logs.append(
                    TesTaskLog(
                        start_time=TaskModelConverter.convert_time(
                            timestamp=log.start_time,
                        ),
                        end_time=TaskModelConverter.convert_time(
                            timestamp=log.end_time,
                        ),
                        metadata=log.metadata,
                        logs=executor_logs,
                        outputs=output_file_logs,
                        system_logs=log.system_logs,
                    )
                )
        return logs

    @staticmethod
    def convert_executor_logs(log: TaskLog) -> list[TesExecutorLog]:
        """Convert py-tes to proTES TES task executor logs.

        Args:
            log: py-tes task log.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesExecutorLog`
        """
        if log.logs is None:
            executor_logs = []
        else:
            executor_logs = [
                TesExecutorLog(
                    start_time=TaskModelConverter.convert_time(
                        timestamp=_executor_log.start_time,
                    ),
                    end_time=TaskModelConverter.convert_time(
                        timestamp=_executor_log.end_time,
                    ),
                    stdout=_executor_log.stdout,
                    stderr=_executor_log.stderr,
                    exit_code=_executor_log.exit_code,
                )
                for _executor_log in log.logs
            ]
        return executor_logs

    @staticmethod
    def convert_output_file_logs(log: TaskLog) -> list[TesOutputFileLog]:
        """Convert py-tes to proTES TES task output file logs.

        Args:
            log: py-tes task log.

        Returns:
            List of :class:`pro_tes.ga4gh.tes.models.TesOutputFileLog`
        """
        if log.outputs is None:
            output_file_logs = []
        else:
            output_file_logs = [
                TesOutputFileLog(
                    url=_output_file_log.url,
                    path=_output_file_log.path,
                    size_bytes=_output_file_log.size_bytes,
                )
                for _output_file_log in log.outputs
            ]
        return output_file_logs

    @staticmethod
    def convert_time(
        timestamp: Optional[datetime],
        allow_none=True,
    ) -> Optional[str]:
        """Convert py-tes to proTES TES task time.

        Args:
            timestamp: Time to convert.
            allow_none: Whether to allow `None` for return value if `timestamp`
                is `None`.

        Returns:
            String representation of time in RFC3339 format, or `None`, if
                `allow_none` is `True` and `timestamp` is `None`.
        """
        if timestamp is None:
            if allow_none:
                return None
            return datetime.fromtimestamp(0).isoformat()
        return timestamp.isoformat()
