"""Tests of the custom config, and its examples in the docs and the chart.

Examples of TES backends (`custom.tes`, and `tes.backends` of the chart) must
validate against the config model, so that deployments copying them get a
config the app accepts; the model rejects unknown keys.
"""

import json
from pathlib import Path
import re
import textwrap
from typing import Any

from foca.config.config_parser import deep_merge
from foca.plugins.config import PluginsConfig
import pydantic
import pytest
import yaml

from pro_tes.config_models import CustomConfig
from tests.helm import HELM, render

ROOT = Path(__file__).resolve().parents[1]
# Markdown files with config examples
MARKDOWN = [
    ROOT / "README.md",
    ROOT / "deployment/README.md",
    *sorted((ROOT / "docs").glob("*.md")),
]
# YAML code blocks of Markdown files (`yaml` or `yml`, with or without
# further info, e.g., a title)
YAML_BLOCK = re.compile(r"^```ya?ml\b[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
# Sources that must contribute examples (collection by heuristics must not
# silently miss them): of the app config, and of the chart's values
EXPECTED_SOURCES = {
    "docs/access-control.md",
    "docs/configuration.md",
    "docs/upgrading.md",
}
EXPECTED_CHART_SOURCES = {"deployment/README.md", "deployment/values.yaml"}
# Marker of examples of backend lists
BACKEND_ENTRY = "type: ga4gh-"


def _custom(example: dict[str, Any]) -> dict[str, Any] | None:
    """Return the custom config of an example, if it has one.

    Args:
        example: A config overlay (`custom` at the top) or the values of the
            chart (`config.custom`).

    Returns:
        The custom config of the example, or `None` if it has none.
    """
    if "config" in example:
        example = example["config"] or {}
    return example.get("custom")


def _examples() -> tuple[list[Any], list[Any]]:
    """Collect the examples with TES backends (`backends`).

    Returns:
        Parameters of the examples of the app config (custom config) and of
        the chart's values (`tes.backends`), with the source as ID.
    """
    examples: list[Any] = []
    chart_examples: list[Any] = []
    for path in MARKDOWN:
        blocks = YAML_BLOCK.findall(path.read_text(encoding="utf-8"))
        for index, block in enumerate(blocks):
            if BACKEND_ENTRY not in block:
                continue
            example = yaml.safe_load(block)
            param_id = f"{path.relative_to(ROOT)}[{index}]"
            if "tes" in example:
                chart_examples.append(pytest.param(example, id=param_id))
                continue
            custom = _custom(example)
            assert custom is not None, f"{param_id}: no custom"
            examples.append(pytest.param(custom, id=param_id))
    # The commented example of `tes.backends` in the chart's values
    values = (ROOT / "deployment/values.yaml").read_text(encoding="utf-8")
    match = re.search(r"^  backends: \[\]\n((?:  #.*\n)+)", values, re.MULTILINE)
    assert match is not None, "no example of `tes.backends` in values.yaml"
    commented = textwrap.dedent(re.sub(r"^  #", "", match.group(1), flags=re.M))
    chart_examples.append(
        pytest.param(
            {"tes": {"backends": yaml.safe_load(commented)}},
            id="deployment/values.yaml",
        )
    )
    return examples, chart_examples


EXAMPLES, CHART_EXAMPLES = _examples()


def test_examples_collected() -> None:
    """Each source with examples of TES backends contributes some."""
    for params, expected in (
        (EXAMPLES, EXPECTED_SOURCES),
        (CHART_EXAMPLES, EXPECTED_CHART_SOURCES),
    ):
        sources = {str(param.id).split("[")[0] for param in params}
        assert sources >= expected


@pytest.mark.parametrize("custom", EXAMPLES)
def test_tes_examples_valid(custom: dict[str, Any]) -> None:
    """The examples, merged into the base config, validate."""
    config = CustomConfig.model_validate(deep_merge(base_custom(), custom))
    assert len(config.tes.backends.root) == len(custom["tes"]["backends"])


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("values", CHART_EXAMPLES)
def test_chart_examples_valid(values: dict[str, Any], tmp_path: Path) -> None:
    """The examples of the chart's values render a valid config.

    The rendered custom config, merged into the base config, validates; the
    identity provider of proTES is set unless the example sets the config
    (as the example of `config` in values.yaml does).
    """
    idp = {"client_credentials": {"issuer": "https://login.example.org/oidc/"}}
    path = tmp_path / "values.yaml"
    path.write_text(
        json.dumps(
            {
                "hostName": "tes.example.org",
                "config": {"custom": {"tes": idp}},
                **values,
            }
        )
    )
    (configmap,) = [
        doc
        for doc in render("-f", str(path))
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "protes-config"
    ]
    overlay = yaml.safe_load(configmap["data"]["overlay.yaml"])
    config = CustomConfig.model_validate(deep_merge(base_custom(), overlay["custom"]))
    assert len(config.tes.backends.root) == len(values["tes"]["backends"])


def base_custom() -> dict[str, Any]:
    """Get the custom config of the base config (`pro_tes/config.yaml`).

    Returns:
        The custom config.
    """
    base_config = (ROOT / "pro_tes/config.yaml").read_text(encoding="utf-8")
    custom: dict[str, Any] = yaml.safe_load(base_config)["custom"]
    return custom


def test_base_config_valid() -> None:
    """The custom config of the base config validates, without unknown keys."""
    CustomConfig.model_validate(base_custom())


@pytest.mark.parametrize(
    "overlay",
    [
        {"celery": {"monitor": {"timeout": 0.1}}},
        {"controllers": {"celery": {"message_maxsize": 1}}},
        {"tes": {"backens": []}},
        {
            "tes": {
                "backends": [
                    {
                        "type": "ga4gh-tes",
                        "url": "https://tes.example.org",
                        "ath": "none",
                    }
                ]
            }
        },
        {
            "tes": {
                "backends": [
                    {
                        "type": "ga4gh-service-registry",
                        "url": "https://registry.example.org",
                        "refresh": 60,
                    }
                ]
            }
        },
        {"controllers": {"post_task": {"task_id": {"lenght": 8}}}},
        {"storeLogs": {"execution_traces": False}},
    ],
)
def test_unknown_keys_rejected(overlay: dict[str, Any]) -> None:
    """Unknown (e.g., misspelled or removed) keys are rejected, at any level."""
    with pytest.raises(pydantic.ValidationError, match="Extra inputs"):
        CustomConfig.model_validate(deep_merge(base_custom(), overlay))


def test_access_control_removed() -> None:
    """`access_control` is refused, naming FOCA's `require_admin`."""
    overlay = {"access_control": {"require_admin": False}}
    with pytest.raises(
        pydantic.ValidationError, match=r"security\.access_control\.require_admin"
    ):
        CustomConfig.model_validate(deep_merge(base_custom(), overlay))


def _plugin_examples() -> list[Any]:
    """Collect the examples of the section `plugins` of the app config.

    Returns:
        Parameters of the examples (the section), with the source as ID: of
        the docs (blocks with `plugins` at the top, not those of the
        chart's values) and of the config overlays of Docker Compose.
    """
    examples: list[Any] = []
    for path in MARKDOWN:
        blocks = YAML_BLOCK.findall(path.read_text(encoding="utf-8"))
        for index, block in enumerate(blocks):
            example = yaml.safe_load(block)
            if not isinstance(example, dict) or "plugins" not in example:
                continue
            if path.name == "README.md" and path.parent.name == "deployment":
                continue
            param_id = f"{path.relative_to(ROOT)}[{index}]"
            examples.append(pytest.param(example["plugins"], id=param_id))
    for name in ("config.dev.yaml", "tests/smoke/config.yaml"):
        overlay = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        examples.append(pytest.param(overlay["plugins"], id=name))
    return examples


PLUGIN_EXAMPLES = _plugin_examples()


def test_plugin_examples_collected() -> None:
    """The docs and the Compose overlays have examples of plugins."""
    sources = {str(param.id).split("[")[0] for param in PLUGIN_EXAMPLES}
    assert sources >= {
        "docs/plugins.md",
        "config.dev.yaml",
        "tests/smoke/config.yaml",
    }


@pytest.mark.parametrize("plugins", PLUGIN_EXAMPLES)
def test_plugin_examples_valid(plugins: dict[str, Any]) -> None:
    """The examples of plugins validate with FOCA's model."""
    PluginsConfig.model_validate(plugins)
