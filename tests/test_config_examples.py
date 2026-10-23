"""Tests of the config examples in the docs and the chart's values.

Examples of TES backends (`custom.tes`) must validate against the config
model, so that deployments copying them get a config the app accepts.
"""

from pathlib import Path
import re
import textwrap
from typing import Any

from foca.config.config_parser import deep_merge
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
# YAML code blocks of Markdown files
YAML_BLOCK = re.compile(r"^```yaml\n(.*?)^```", re.DOTALL | re.MULTILINE)


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


@pytest.mark.parametrize("custom", _examples())
def test_tes_examples_valid(custom: dict[str, Any]) -> None:
    """The examples, merged into the base config, validate."""
    base = yaml.safe_load((ROOT / "pro_tes/config.yaml").read_text())["custom"]
    config = CustomConfig.model_validate(deep_merge(base, custom))
    assert config.tes.service_list
