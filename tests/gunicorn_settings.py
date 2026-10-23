"""Helpers for tests of the Gunicorn settings of the image."""

import json
import os
from pathlib import Path
import re
import sys

from gunicorn.app.wsgiapp import WSGIApplication

DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"


def dockerfile_instruction(name: str) -> str:
    """Get the arguments of the last instruction of the Dockerfile by name.

    Continued lines are joined.

    Args:
        name: Name of the instruction (e.g., `CMD`).

    Returns:
        Arguments of the instruction.
    """
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    (*_, args) = re.findall(rf"^{name}\s+(.+)$", text, flags=re.MULTILINE)
    return args


def image_command() -> list[str]:
    """Get the command of the image (`CMD`, in exec form).

    Returns:
        Command and arguments.
    """
    command: list[str] = json.loads(dockerfile_instruction("CMD"))
    return command


def load_settings(cmd_args: str | None = None) -> WSGIApplication:
    """Load Gunicorn's settings as the image's command does.

    Optionally with `GUNICORN_CMD_ARGS`; restores the arguments, environment
    and current directory afterwards (Gunicorn changes to its `chdir`
    setting).

    Args:
        cmd_args: Value of `GUNICORN_CMD_ARGS`; unset if `None`.

    Returns:
        Gunicorn application with its settings (`cfg`), without loading the
        app.
    """
    argv, cwd = sys.argv, os.getcwd()
    env = os.environ.pop("GUNICORN_CMD_ARGS", None)
    sys.argv = image_command()
    if cmd_args is not None:
        os.environ["GUNICORN_CMD_ARGS"] = cmd_args
    try:
        return WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]")
    finally:
        sys.argv = argv
        os.chdir(cwd)
        os.environ.pop("GUNICORN_CMD_ARGS", None)
        if env is not None:
            os.environ["GUNICORN_CMD_ARGS"] = env
