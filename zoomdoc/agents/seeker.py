from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from pydantic import TypeAdapter

from zoomdoc.agents.base import call_with_schema
from zoomdoc.llm.vlm_client import ImagePart, TextPart, VLMClient, VLMResponse
from zoomdoc.prompts import SEEKER_PROMPT_TEMPLATE
from zoomdoc.schemas import PageAsset, SeekerOutput, VLMUsage
from zoomdoc.vision.grid import ARRANGEMENT, build_page_map, concat_grid

if TYPE_CHECKING:
    from zoomdoc.config import Settings
    from zoomdoc.observability import TraceLogger


class Seeker:
    """Coarse visual page selector. Inspects multi-thumbnail composite grid."""

    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger) -> None:
        self.vlm = vlm
        self.settings = settings
        self.logger = logger
        self.adapter = TypeAdapter(SeekerOutput)

    def run(
        self,
        question: str,
        candidate: list[PageAsset],
        feedback: str | None,
        out_dir: str,
        step: int = 1,
        usage_sink: list[VLMUsage] | None = None,
        namespace: str = "main",
    ) -> tuple[SeekerOutput, list[int], VLMResponse, int]:
        n = len(candidate)
        assert 1 <= n <= 20, f"Seeker expects 1 <= candidate count <= 20, got {n}"

        # 1. Load candidate thumbnail images and build grid
        thumb_images = [Image.open(p.thumb_path).convert("RGB") for p in candidate]
        arrangement = ARRANGEMENT[n]
        grid_img, _ = concat_grid(thumb_images, arrangement, self.settings.grid_line_width)

        grids_dir = Path(out_dir) / "grids"
        grids_dir.mkdir(parents=True, exist_ok=True)
        grid_path = grids_dir / f"seeker_step{step}.png"
        grid_img.save(grid_path, format="PNG")

        # 2. Build prompt
        full_question = question
        if feedback:
            full_question = f"{question}\n\n## Additional Information\n{feedback}"

        page_map = build_page_map(n, arrangement)
        prompt_text = SEEKER_PROMPT_TEMPLATE.replace("{question}", full_question).replace(
            "{page_map}", page_map
        )

        parts = [
            ImagePart(path=str(grid_path.resolve())),
            TextPart(text=prompt_text),
        ]

        def validate(output: SeekerOutput) -> None:
            for idx in output.choice:
                if not (0 <= idx < n):
                    raise ValueError(f"Choice index {idx} out of range [0, {n-1}]")

        obj, resp, attempts = call_with_schema(
            self.vlm,
            parts,
            self.adapter,
            agent="seeker",
            max_attempts=self.settings.max_attempts,
            logger=self.logger,
            validate=validate,
            usage_sink=usage_sink,
            namespace=namespace,
        )

        # Preserve order of distinct selections
        seen: set[int] = set()
        unique_choices: list[int] = []
        for idx in obj.choice:
            if idx not in seen:
                seen.add(idx)
                unique_choices.append(idx)

        # Map 0-based choice indices to 1-based physical page numbers
        selected_pages = [candidate[idx].page for idx in unique_choices]
        return (obj, selected_pages, resp, attempts)
