"""Crypt4GH decryption middleware.

This middleware prepends a decryption executor for tasks that include at least
one input file ending in .c4gh. It also rewrites input paths in downstream
executors so they point to the decrypted files in a writable shared volume.
"""

from __future__ import annotations

import shlex
import uuid
from copy import deepcopy
from pathlib import Path

import flask
from flask import current_app

from pro_tes.exceptions import MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware


class PathNotAllowedException(MiddlewareException):
    """Raised when a reserved middleware path is used in the task."""


class CryptMiddleware(AbstractMiddleware):
    """Middleware that injects a Crypt4GH decryption step into TES tasks."""

    def __init__(self) -> None:
        """Class constructor."""
        self.crypt4gh_input_paths: list[str] = []
        self.all_input_paths: list[str] = []
        self.tes_urls: list[str] = []

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Apply middleware to request."""
        if request.json is None:
            raise MiddlewareException("Request has no JSON payload.")

        self._set_tes_urls(request=request)
        self._set_input_paths(request=request)

        # No encrypted files in this task: trigger fallback middleware.
        if not self.crypt4gh_input_paths:
            raise MiddlewareException("No Crypt4GH input paths available.")

        volume_path = self._build_volume_path()
        output_dirs = self._get_output_dirs(request=request)
        self._check_output_paths(request=request)
        self._rewrite_executor_paths(request=request, volume_path=volume_path)
        self._add_volume(request=request, volume_path=volume_path)
        self._add_decryption_executor(
            request=request,
            volume_path=volume_path,
            output_dirs=output_dirs,
        )
        return request

    def _set_tes_urls(self, request: flask.Request) -> None:
        """Attach configured TES URLs to request payload."""
        tes_urls = deepcopy(
            current_app.config.foca.custom.tes.service_list  # type: ignore
        )
        self.tes_urls = list(set(tes_urls))
        request.json["tes_urls"] = self.tes_urls

    def _set_input_paths(self, request: flask.Request) -> None:
        """Collect all input paths and Crypt4GH paths from payload."""
        self.crypt4gh_input_paths = []
        self.all_input_paths = []
        for input_body in request.json.get("inputs", []):
            path = input_body.get("path")
            if not isinstance(path, str) or not path:
                continue
            self.all_input_paths.append(path)
            if path.lower().endswith(".c4gh"):
                self.crypt4gh_input_paths.append(path)

    @staticmethod
    def _build_volume_path() -> str:
        """Create a unique writable volume path for decrypted inputs."""
        return f"/vol/crypt-{uuid.uuid4().hex}"

    @staticmethod
    def _add_decryption_executor(
        request: flask.Request,
        volume_path: str,
        output_dirs: list[str],
    ) -> None:
        """Prepend decryption executor to the executor list."""
        executors = request.json.setdefault("executors", [])
        input_paths = [
            input_body.get("path")
            for input_body in request.json.get("inputs", [])
            if isinstance(input_body.get("path"), str)
        ]
        decrypt_command = " ".join(
            [
                "python3",
                "decrypt.py",
                *[shlex.quote(path) for path in input_paths],
                "--output-dir",
                shlex.quote(volume_path),
            ]
        )
        mirror_commands = [
            "mkdir -p"
            f" {shlex.quote(output_dir)}"
            f" && cp -f {shlex.quote(volume_path)}/* {shlex.quote(output_dir)}/"
            for output_dir in output_dirs
        ]
        executor = {
            "image": "schneva88/protes_crypt4gh:1.0",
            "command": [
                "/bin/sh",
                "-c",
                " && ".join([decrypt_command, *mirror_commands]),
            ],
        }
        executors.insert(0, executor)

    @staticmethod
    def _get_output_dirs(request: flask.Request) -> list[str]:
        """Collect unique parent directories for declared task outputs."""
        output_dirs: list[str] = []
        for output_body in request.json.get("outputs", []):
            path = output_body.get("path")
            if not isinstance(path, str) or not path:
                continue
            output_dir = str(Path(path).parent)
            if output_dir not in output_dirs:
                output_dirs.append(output_dir)
        return output_dirs

    @staticmethod
    def _add_volume(request: flask.Request, volume_path: str) -> None:
        """Ensure task includes writable volume for decrypted files."""
        volumes = request.json.setdefault("volumes", [])
        for volume in volumes:
            if isinstance(volume, str) and volume.startswith(volume_path):
                raise PathNotAllowedException(
                    f"{volume_path} is not allowed in volumes."
                )
        volumes.append(volume_path)

    def _check_output_paths(self, request: flask.Request) -> None:
        """Prevent in-place overwrite of input files."""
        for output_body in request.json.get("outputs", []):
            path = output_body.get("path")
            if isinstance(path, str) and path in self.all_input_paths:
                raise PathNotAllowedException(
                    f"{path} is being modified in place."
                )

    def _rewrite_executor_paths(
        self,
        request: flask.Request,
        volume_path: str,
    ) -> None:
        """Rewrite executor command/env references to decrypted file paths."""
        rewritten = {
            original: str(Path(volume_path) / Path(original).name)
            for original in self.all_input_paths
        }

        for executor in request.json.get("executors", []):
            command = executor.get("command", [])
            if isinstance(command, list):
                for idx, token in enumerate(command):
                    if token in rewritten:
                        command[idx] = rewritten[token]
            env = executor.get("env")
            if isinstance(env, dict):
                for key, value in env.items():
                    if value in rewritten:
                        env[key] = rewritten[value]
