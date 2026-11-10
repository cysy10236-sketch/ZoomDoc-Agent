from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from zoomdoc.agents.base import call_with_schema
from zoomdoc.errors import ZoomDocError
from zoomdoc.index.tree import format_tree_outline
from zoomdoc.llm.vlm_client import TextPart, VLMClient, VLMResponse
from zoomdoc.prompts import NAVIGATOR_PROMPT_TEMPLATE
from zoomdoc.schemas import DocTreeNode, NavigatorOutput, VLMUsage

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.observability import TraceLogger


class Navigator:
    """Tree outline navigation agent (pure text, zero image payload)."""

    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger) -> None:
        self.vlm = vlm
        self.settings = settings
        self.logger = logger
        self.adapter = TypeAdapter(NavigatorOutput)

    def run(
        self,
        question: str,
        frontier_nodes: list[DocTreeNode],
        visited_node_ids: set[str],
        feedback: str | None,
        can_expand: bool,
        usage_sink: list[VLMUsage] | None = None,
        namespace: str = "navigator",
    ) -> tuple[NavigatorOutput, VLMResponse, int]:
        """Execute one step of tree outline navigation.

        Preconditions:
            frontier_nodes must not be empty.
        Validation:
            1. Every node_id in output.node_ids must exist in frontier_nodes.
            2. If action == 'expand', can_expand must be True and at least one chosen node must have children.
        """
        if not frontier_nodes:
            raise ZoomDocError("Navigator.run called with empty frontier_nodes")

        valid_node_ids = {n.node_id for n in frontier_nodes}
        node_has_children = {n.node_id: bool(n.children) for n in frontier_nodes}

        outline_block = format_tree_outline(frontier_nodes)
        feedback_str = f"## Additional Feedback\n{feedback}\n\n" if feedback else ""

        prompt_text = (
            NAVIGATOR_PROMPT_TEMPLATE.replace("{question}", question)
            .replace("{feedback}", feedback_str)
            .replace("{outline_block}", outline_block)
        )

        parts = [TextPart(text=prompt_text)]

        def validate(output: NavigatorOutput) -> None:
            for nid in output.node_ids:
                if nid not in valid_node_ids:
                    raise ValueError(
                        f"Node ID '{nid}' is not among current available nodes {sorted(list(valid_node_ids))}"
                    )
            if output.action == "expand":
                if not can_expand:
                    raise ValueError("Cannot 'expand' further; you must choose action='select' now.")
                if not any(node_has_children.get(nid, False) for nid in output.node_ids):
                    raise ValueError(
                        f"Selected node(s) {output.node_ids} have no children and cannot be expanded. Use 'select' instead."
                    )

        obj, resp, attempts = call_with_schema(
            self.vlm,
            parts,
            self.adapter,
            agent="navigator",
            max_attempts=self.settings.max_attempts,
            logger=self.logger,
            validate=validate,
            usage_sink=usage_sink,
            namespace=namespace,
        )

        return (obj, resp, attempts)
