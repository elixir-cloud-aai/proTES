"""Test plugin `smoke.tag` of the smoke test: tags each new task.

The smoke test (`tests/smoke/plugins.sh`) installs it into an image derived
from the proTES image, following the recipe of `docs/plugin-images.md`,
enables it, and checks that tasks get the tag `TAG` with the value of the
plugin's admin setting `value`.
"""

from foca.plugins import Context, InboundRequest, Plugin
from pydantic import BaseModel, ConfigDict

# Tag that the plugin sets (not under `gateway.`, which proTES reserves)
TAG = "smoke.plugin"


class TagConfig(BaseModel):
    """Admin settings (`plugins.available."smoke.tag".config`).

    Attributes:
        value: Value of the tag.
    """

    model_config = ConfigDict(extra="forbid")

    value: str = "ran"


class Tag(Plugin[TagConfig, BaseModel]):
    """Tag each new task.

    Attributes:
        value: Value of the tag (`TagConfig.value`).
    """

    Config = TagConfig
    apis = frozenset({"tes"})
    description = "Sets the tag `smoke.plugin` of each new task."

    # Set by `setup()`, from the admin settings
    value: str

    def setup(self, config: TagConfig) -> None:
        """Keep the value of the tag.

        Args:
            config: Validated admin settings.
        """
        self.value = config.value

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Add the tag to the task.

        Args:
            request: Inbound request; its payload is the task.
            ctx: Context of the call.
        """
        task = request.payload
        task["tags"] = {**(task.get("tags") or {}), TAG: self.value}
