"""Tests of the custom config, and its examples in the docs and the chart.

Examples of TES backends (`custom.tes`) must validate against the config
model, so that deployments copying them get a config the app accepts; the
model rejects unknown keys.
"""

from pathlib import Path
import re
import textwrap
from typing import Any

from foca.config.config_parser import deep_merge
import pydantic
import pytest
import yaml

from pro_tes.config_models import CustomConfig

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
# silently miss them)
EXPECTED_SOURCES = {
    "deployment/README.md",
    "deployment/values.yaml",
    "docs/access-control.md",
    "docs/configuration.md",
}


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


def _examples() -> list[Any]:
    """Collect the examples with TES backends (`service_list`).

    Returns:
        Parameters of the examples (custom config), with the source as ID.
    """
    examples: list[Any] = []
    for path in MARKDOWN:
        blocks = YAML_BLOCK.findall(path.read_text(encoding="utf-8"))
        for index, block in enumerate(blocks):
            if "service_list:" not in block:
                continue
            custom = _custom(yaml.safe_load(block))
            assert custom is not None, f"{path.name}, block {index}: no custom"
            examples.append(
                pytest.param(custom, id=f"{path.relative_to(ROOT)}[{index}]")
            )
    # The commented example of `config` in the chart's values
    values = (ROOT / "deployment/values.yaml").read_text(encoding="utf-8")
    match = re.search(r"^config: \{\}\n((?:#.*\n)+)", values, re.MULTILINE)
    assert match is not None, "no example of `config` in values.yaml"
    commented = textwrap.dedent(re.sub(r"^#", "", match.group(1), flags=re.M))
    examples.append(
        pytest.param(_custom(yaml.safe_load(commented)), id="deployment/values.yaml")
    )
    return examples


EXAMPLES = _examples()


def test_examples_collected() -> None:
    """Each source with examples of TES backends contributes some."""
    sources = {str(param.id).split("[")[0] for param in EXAMPLES}
    assert sources >= EXPECTED_SOURCES


@pytest.mark.parametrize("custom", EXAMPLES)
def test_tes_examples_valid(custom: dict[str, Any]) -> None:
    """The examples, merged into the base config, validate."""
    config = CustomConfig.model_validate(deep_merge(base_custom(), custom))
    backends = custom["tes"]["service_list"]
    assert [backend.url for backend in config.tes.service_list] == [
        backend["url"] for backend in backends
    ]


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
        {"tes": {"servce_list": [{"url": "https://tes.example.org"}]}},
        {"tes": {"service_list": [{"url": "https://tes.example.org", "ath": "none"}]}},
        {"tes": {"discovery": {"registry": "https://registry.example.org"}}},
        {"controllers": {"post_task": {"task_id": {"lenght": 8}}}},
        {"access_control": {"require_admins": False}},
        {"storeLogs": {"execution_traces": False}},
    ],
)
def test_unknown_keys_rejected(overlay: dict[str, Any]) -> None:
    """Unknown (e.g., misspelled or removed) keys are rejected, at any level."""
    with pytest.raises(pydantic.ValidationError, match="Extra inputs"):
        CustomConfig.model_validate(deep_merge(base_custom(), overlay))
