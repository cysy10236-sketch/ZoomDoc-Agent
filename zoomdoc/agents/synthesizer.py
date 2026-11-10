from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from zoomdoc.agents.base import call_with_schema
from zoomdoc.agents.inspector import InspectorView
from zoomdoc.llm.vlm_client import ImagePart, Part, TextPart, VLMClient, VLMResponse
from zoomdoc.prompts import (
    SYNTHESIZER_PROMPT_TEMPLATE,
    label_crop,
    label_full_page,
)
from zoomdoc.schemas import CropAsset, PageAsset, SynthesizerOutput

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.observability import TraceLogger


class Synthesizer:
    """Final answer synthesizer using shortlisted images and draft information."""

    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger) -> None:
        self.vlm = vlm
        self.settings = settings
        self.logger = logger
        self.adapter = TypeAdapter(SynthesizerOutput)

    def run(
        self,
        question: str,
        draft: str | None,
        view: InspectorView,
        pages: dict[int, PageAsset],
        crops: dict[str, CropAsset],
    ) -> tuple[SynthesizerOutput, VLMResponse, int]:
        n_items = len(view.items)
        assert n_items > 0, "Synthesizer view cannot be empty"

        parts: list[Part] = []
        for idx, item in enumerate(view.items):
            kind, identifier = item
            if kind == "page":
                page_asset = pages[int(identifier)]
                parts.append(TextPart(text=label_full_page(idx, page_asset.page)))
                parts.append(ImagePart(path=page_asset.view_path))
            elif kind == "crop":
                crop_asset = crops[str(identifier)]
                parts.append(TextPart(text=label_crop(idx, crop_asset)))
                parts.append(ImagePart(path=crop_asset.path))

        related_section = f"\n## Related Information\n{draft}\n" if draft else "\n"
        prompt_text = (
            SYNTHESIZER_PROMPT_TEMPLATE.replace("{question}", question)
            .replace("{related_information_section}", related_section)
            .replace("{max_index}", str(n_items - 1))
        )
        parts.append(TextPart(text=prompt_text))

        def validate(output: SynthesizerOutput) -> None:
            for ref_idx in output.reference:
                if not (0 <= ref_idx < n_items):
                    raise ValueError(f"Synthesizer reference index {ref_idx} out of bounds [0, {n_items-1}]")

        obj, resp, attempts = call_with_schema(
            self.vlm,
            parts,
            self.adapter,
            agent="synthesizer",
            max_attempts=self.settings.max_attempts,
            logger=self.logger,
            validate=validate,
        )

        return (obj, resp, attempts)
