from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from zoomdoc.agents.inspector import InspectorView
from zoomdoc.schemas import (
    CropAsset,
    FinalResult,
    InspectorZoom,
    PageAsset,
    StepRecord,
)


class AgentState(BaseModel):
    trace_id: str
    question: str
    pdf_path: str
    run_dir: str
    pages: dict[int, PageAsset] = Field(default_factory=dict)
    candidate_pages: list[int] = Field(default_factory=list)  # ViDoRAG Seeker.buffer_images
    inspector_pages: list[int] = Field(default_factory=list)  # ViDoRAG Inspector.buffer_images
    crops: dict[str, CropAsset] = Field(default_factory=dict)
    last_view: InspectorView | None = None
    feedback: str | None = None
    pending_zoom: InspectorZoom | None = None
    draft_answer: str | None = None
    synth_view: InspectorView | None = None
    step: int = 0
    zoom_count: int = 0
    route: Literal["seeker", "inspector", "zoom", "synthesizer", "finalize", "end"] | None = None
    result: FinalResult | None = None
    history: list[StepRecord] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
