from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Union

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
    VLMUsage,
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

    @staticmethod
    def build_view_items(
        pages: list[PageAsset],
        crops: list[CropAsset],
        max_inspector_images: int,
    ) -> InspectorView:
        """Construct image sequence up to max_inspector_images.

        Order: pages (ascending page number) followed by crops (creation order).
        Overflow policy:
        - Reserve at least 1 slot for full pages when pages exist, keeping up to
          (max_inspector_images - 1) most recent crops (dropping earliest crops first).
        - Fill remaining slots with parent pages of kept crops first, then earliest
          remaining pages in ascending page order (dropping largest unreferenced pages).
        """
        max_limit = max(1, max_inspector_images)
        sorted_pages = sorted(pages, key=lambda p: p.page)

        if not sorted_pages:
            kept_crops = crops[-max_limit:]
            return InspectorView(items=[("crop", c.crop_id) for c in kept_crops])

        max_crop_slots = max(0, max_limit - 1)
        kept_crops = crops[-max_crop_slots:] if max_crop_slots > 0 else []
        page_slots = max_limit - len(kept_crops)

        if len(sorted_pages) <= page_slots:
            kept_pages = sorted_pages
        else:
            crop_parent_nums = {c.page for c in kept_crops}
            priority_pages = [p for p in sorted_pages if p.page in crop_parent_nums]
            other_pages = [p for p in sorted_pages if p.page not in crop_parent_nums]
            chosen = (priority_pages + other_pages)[:page_slots]
            kept_pages = sorted(chosen, key=lambda p: p.page)

        page_items: list[ViewItem] = [("page", p.page) for p in kept_pages]
        crop_items: list[ViewItem] = [("crop", c.crop_id) for c in kept_crops]
        return InspectorView(items=page_items + crop_items)

    def build_view(self, pages: list[PageAsset], crops: list[CropAsset]) -> InspectorView:
        return self.build_view_items(pages, crops, self.settings.max_inspector_images)

    def run(
        self,
        question: str,
        view: InspectorView,
        pages: dict[int, PageAsset],
        crops: dict[str, CropAsset],
        zoom_left: int,
        usage_sink: list[VLMUsage] | None = None,
        prompt_template: str | None = None,
        adapter: TypeAdapter | None = None,
        namespace: str = "main",
        feedback: str | None = None,
    ) -> tuple[Any, VLMResponse, int]:
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

        template = prompt_template or INSPECTOR_PROMPT_TEMPLATE
        feedback_str = f"## Additional Feedback\n{feedback}\n\n" if feedback else ""

        prompt_text = (
            template.replace("{question}", question)
            .replace("{zoom_left}", str(zoom_left))
            .replace("{max_index}", str(n_items - 1))
            .replace("{bbox_instruction}", bbox_instruction(self.settings.bbox_format))
            .replace("{feedback}", feedback_str)
        )
        parts.append(TextPart(text=prompt_text))

        def validate(output: Any) -> None:
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

        target_adapter = adapter or self.adapter

        obj, resp, attempts = call_with_schema(
            self.vlm,
            parts,
            target_adapter,
            agent="inspector",
            max_attempts=self.settings.max_attempts,
            logger=self.logger,
            validate=validate,
            usage_sink=usage_sink,
            namespace=namespace,
        )

        return (obj, resp, attempts)
