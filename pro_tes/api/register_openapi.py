"""Functions for registering OpenAPI specs with a Connexion app instance."""

from json import load
import logging
import os
from shutil import copyfile
from typing import (List, Dict)

from connexion import App
from yaml import safe_dump

from pro_tes.config.config_parser import get_conf


# Get logger instance
logger = logging.getLogger(__name__)


def register_openapi(
    app: App,
    specs: List[Dict] = [],
    add_security_definitions: bool = True
) -> App:
    """Registers OpenAPI specs with Connexion app."""
    # Iterate over list of API specs
    for spec in specs:

        # Get _this_ directory
        path = os.path.join(
            os.path.abspath(
                os.path.dirname(
                    os.path.realpath(__file__)
                )
            ),
            get_conf(spec, 'path')
        )

        # Convert JSON to YAML
        if get_conf(spec, 'type') == 'json':
            path = __json_to_yaml(path)

        # Generate API endpoints from OpenAPI spec
        try:
            app.add_api(
                path,
                strict_validation=get_conf(spec, 'strict_validation'),
                validate_responses=get_conf(spec, 'validate_responses'),
                swagger_ui=get_conf(spec, 'swagger_ui'),
                swagger_json=get_conf(spec, 'swagger_json'),
            )

            logger.info("API endpoints specified in '{path}' added.".format(
                path=path,
            ))

        except (FileNotFoundError, PermissionError) as e:
            logger.critical(
                (
                    "API specification file not found or accessible at "
                    "'{path}'. Execution aborted. Original error message: "
                    "{type}: {msg}"
                ).format(
                    path=path,
                    type=type(e).__name__,
                    msg=e,
                )
            )
            raise SystemExit(1)

    return(app)


def __json_to_yaml(
    path: str,
    replace_extension: bool = True
) -> str:
    """Converts JSON to YAML file."""
    out_base = os.path.splitext(path)[0] if replace_extension else path
    out_file = '.'.join([out_base, 'yaml'])
    with open(path, 'r') as f_in, open(out_file, 'w') as f_out:
        safe_dump(load(f_in), f_out, default_flow_style=False)
    return out_file
