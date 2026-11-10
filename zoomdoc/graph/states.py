from __future__ import annotations

import operator
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zoomdoc.agents.inspector import InspectorView
from zoomdoc.schemas import (
    CitedRegion,
    CropAsset,
    DocTreeNode,
    DocumentTree,
    FinalResultM2,
    InspectorOutputM2,
    InspectorZoom,
    PageAsset,
    StepRecord,
    UsageTotals,
    VLMUsage,
)


_IMAGE_PATH_PATTERN = re.compile(
    r"(?:[/\\]|[a-zA-Z]:[/\\]|crops[/\\]|pages[/\\])[\w\-\./\\]*\.(?:png|jpe?g|webp|bmp)",
    re.IGNORECASE,
)


def _assert_no_image_payload(data: Any, path: str = "") -> None:
    if isinstance(data, str):
        lower = data.lower()
        if "data:image" in lower:
            raise ValueError(f"image payload leaked in field '{path}': found 'data:image'")
        if "base64," in lower:
            raise ValueError(f"image payload leaked in field '{path}': found 'base64,'")
        matched = _IMAGE_PATH_PATTERN.search(data)
        if matched:
            raise ValueError(f"image payload leaked in field '{path}': found image path '{matched.group(0)}'")
    elif isinstance(data, dict):
        for k, v in data.items():
            _assert_no_image_payload(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _assert_no_image_payload(item, f"{path}[{i}]")


def merge_usage(a: UsageTotals, b: UsageTotals) -> UsageTotals:
    """Reducer: 逐字段相加。必须是纯函数，不修改入参。"""
    return UsageTotals(
        calls=a.calls + b.calls,
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        missing_usage_calls=a.missing_usage_calls + b.missing_usage_calls,
        latency_ms=round(a.latency_ms + b.latency_ms, 2),
    )


def usage_from_sink(sink: list[VLMUsage]) -> UsageTotals:
    """calls=len(sink)；token 为 None 的条目 missing_usage_calls += 1，token 计 0；latency 求和。"""
    calls = len(sink)
    prompt_tokens = 0
    completion_tokens = 0
    missing = 0
    latency = 0.0
    for u in sink:
        if u.prompt_tokens is None or u.completion_tokens is None:
            missing += 1
        prompt_tokens += u.prompt_tokens or 0
        completion_tokens += u.completion_tokens or 0
        latency += u.latency_ms

    return UsageTotals(
        calls=calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        missing_usage_calls=missing,
        latency_ms=round(latency, 2),
    )


class EvidenceReport(BaseModel):
    """子图向主图返回的唯一结构化证据契约。extra='forbid'，严格杜绝图像信息泄漏。"""

    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    status: Literal["answered", "need_more_pages", "budget_exhausted"]
    answer: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str
    seek_hint: str | None = None
    reference_pages: list[int]
    cited_regions: list[CitedRegion] = Field(default_factory=list)
    zoom_count: int
    sub_steps: int
    usage: UsageTotals

    @model_validator(mode="after")
    def _check_leak(self) -> "EvidenceReport":
        _assert_no_image_payload(self.model_dump())
        return self


class CriticVerdict(BaseModel):
    """Critic 确定性规则评审判决。"""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reason_code: Literal[
        "ok",
        "low_confidence",
        "no_answer",
        "need_more_pages",
        "budget_exhausted",
        "invalid_reference",
    ]
    detail: str


class NavigatorReport(BaseModel):
    """Navigator 子图向主图返回的唯一路由报告契约。extra='forbid'，严禁包含图像数据。"""

    model_config = ConfigDict(extra="forbid")

    nav_round: int = Field(ge=1)
    status: Literal["located", "exhausted", "budget_exhausted"]
    selected_node_ids: list[str]
    candidate_pages: list[int]  # 1-based 升序去重页码列表，长度严格 <= max_pages (20)
    reason: str  # 路由选择理由
    sub_steps: int
    usage: UsageTotals

    @model_validator(mode="after")
    def _check_leak_and_bounds(self) -> "NavigatorReport":
        _assert_no_image_payload(self.model_dump())
        if len(self.candidate_pages) > 20:
            raise ValueError(
                f"NavigatorReport candidate_pages count {len(self.candidate_pages)} exceeds hard limit 20"
            )
        return self


class SupervisorState(BaseModel):
    """主图全局状态：全文本与页资产路径，绝不包含任何高分辨率裁剪图或 base64。"""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    question: str
    pdf_path: str
    run_dir: str
    total_pages: int = 0
    doc_tree: DocumentTree | None = None
    pages: dict[int, PageAsset] = Field(default_factory=dict)
    candidate_pages: list[int] = Field(default_factory=list)
    selected_pages: list[int] = Field(default_factory=list)
    feedback: str | None = None
    evidence: Annotated[list[EvidenceReport], operator.add] = Field(default_factory=list)
    nav_reports: Annotated[list[NavigatorReport], operator.add] = Field(default_factory=list)
    visited_node_ids: list[str] = Field(default_factory=list)
    verdict: CriticVerdict | None = None
    seek_rounds: int = 0
    inspect_rounds: int = 0
    nav_rounds: int = 0
    step: int = 0
    next: (
        Literal[
            "tree_builder",
            "navigator",
            "seeker",
            "inspector",
            "critic",
            "synthesizer",
            "finalize",
        ]
        | None
    ) = None
    final_answer: str | None = None
    result: FinalResultM2 | None = None
    usage: Annotated[UsageTotals, merge_usage] = Field(default_factory=UsageTotals)
    history: Annotated[list[StepRecord], operator.add] = Field(default_factory=list)


class NavigatorSubState(BaseModel):
    """Navigator 子图私有状态：自顶向下或全局检索文档树，生命周期仅限单次 invoke。"""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    question: str
    nav_round: int = Field(ge=1)
    doc_tree: DocumentTree
    visited_node_ids: list[str] = Field(default_factory=list)
    feedback: str | None = None
    current_frontier: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)
    last_reason: str = ""
    sub_step: int = 0
    max_nav_steps: int = 4
    max_candidate_pages: int = 20
    usage: UsageTotals = Field(default_factory=UsageTotals)
    report: NavigatorReport | None = None


class InspectorSubState(BaseModel):
    """子图私有局部状态：生命周期仅限于一次 invoke，执行完毕后就地丢弃。"""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    question: str
    round: int
    sub_dir: str
    pages: dict[int, PageAsset]
    feedback: str | None = None
    crops: dict[str, CropAsset] = Field(default_factory=dict)
    view: InspectorView | None = None
    pending_zoom: InspectorZoom | None = None
    last_output: InspectorOutputM2 | None = None
    sub_step: int = 0
    zoom_count: int = 0
    max_zooms: int
    max_sub_steps: int
    usage: UsageTotals = Field(default_factory=UsageTotals)
    report: EvidenceReport | None = None
