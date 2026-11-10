from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Union

from pydantic import BaseModel, TypeAdapter

from zoomdoc.agents.base import call_with_schema
from zoomdoc.llm.vlm_client import ImagePart, Part, TextPart, VLMClient, VLMResponse
from zoomdoc.prompts import (
    INSPECTOR_PROMPT_TEMPLATE,
    bbox_instruction,
    label_crop,
    label_full_page,
)
from zoomdoc.schemas import (
    CropAsset,
    InspectorAnswer,
    InspectorOutput,
    InspectorSeek,
    InspectorZoom,
    PageAsset,
)
from zoomdoc.vision.geometry import vlm_bbox_to_norm

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.observability import TraceLogger

ViewItem = Union[tuple[Literal["page"], int], tuple[Literal["crop"], str]]


class InspectorView(BaseModel):
    """Sequence mapping of images presented to Inspector: index -> source item."""

    items: list[ViewItem]


class Inspector:
    """Detailed visual inspector. Evaluates full-page views and zooms into regions."""

    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger) -> None:
        self.vlm = vlm
        self.settings = settings
        self.logger = logger
        self.adapter = TypeAdapter(InspectorOutput)

    def build_view(self, pages: list[PageAsset], crops: list[CropAsset]) -> InspectorView:
        """Construct image sequence up to max_inspector_images.

        Order: pages (ascending page number) followed by crops (creation order).
        Overflow policy: drop earliest crops first, then drop largest page numbers.
        """
        sorted_pages = sorted(pages, key=lambda p: p.page)
        page_items: list[ViewItem] = [("page", p.page) for p in sorted_pages]
        crop_items: list[ViewItem] = [("crop", c.crop_id) for c in crops]

        total_items = page_items + crop_items
        max_limit = self.settings.max_inspector_images

        while len(total_items) > max_limit:
            # First drop earliest crop if any crop exists
            crop_indices = [i for i, it in enumerate(total_items) if it[0] == "crop"]
            if crop_indices:
                total_items.pop(crop_indices[0])
            else:
                # Otherwise drop the largest page number (last page item)
                total_items.pop(-1)

        return InspectorView(items=total_items)

    def run(
        self,
        question: str,
        view: InspectorView,
        pages: dict[int, PageAsset],
        crops: dict[str, CropAsset],
        zoom_left: int,
    ) -> tuple[InspectorOutput, VLMResponse, int]:
        n_items = len(view.items)
        assert n_items > 0, "InspectorView cannot be empty"

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

        prompt_text = (
            INSPECTOR_PROMPT_TEMPLATE.replace("{question}", question)
            .replace("{zoom_left}", str(zoom_left))
            .replace("{max_index}", str(n_items - 1))
            .replace("{bbox_instruction}", bbox_instruction(self.settings.bbox_format))
        )
        parts.append(TextPart(text=prompt_text))

        def validate(output: InspectorOutput) -> None:
            if isinstance(output, InspectorAnswer):
                for ref_idx in output.reference:
                    if not (0 <= ref_idx < n_items):
                        raise ValueError(f"Reference image index {ref_idx} out of bounds [0, {n_items-1}]")
            elif isinstance(output, InspectorSeek):
                for ch_idx in output.choice:
                    if not (0 <= ch_idx < n_items):
                        raise ValueError(f"Seek choice index {ch_idx} out of bounds [0, {n_items-1}]")
            elif isinstance(output, InspectorZoom):
                if zoom_left <= 0:
                    raise ValueError("Zoom budget exhausted (zoom_left == 0). Choose 'answer' or 'seek'.")
                if not (0 <= output.image < n_items):
                    raise ValueError(f"Zoom image index {output.image} out of bounds [0, {n_items-1}]")
                # Validate bbox conversion
                vlm_bbox_to_norm(output.bbox_2d, self.settings.bbox_format)

        obj, resp, attempts = call_with_schema(
            self.vlm,
            parts,
            self.adapter,
            agent="inspector",
            max_attempts=self.settings.max_attempts,
            logger=self.logger,
            validate=validate,
        )

        return (obj, resp, attempts)
