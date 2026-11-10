from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class NormBBox(BaseModel):
    """项目内唯一的规范坐标：(x1, y1, x2, y2)，相对于某张图像，取值范围 [0, 1]。"""

    model_config = ConfigDict(frozen=True)

    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> "NormBBox":
        if not (self.x1 < self.x2 and self.y1 < self.y2):
            raise ValueError(f"degenerate bbox {self}")
        return self

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


class PageAsset(BaseModel):
    page: int = Field(ge=1)  # 1-based 物理页码（与 PageIndex physical_index 一致）
    source_path: str
    view_path: str
    thumb_path: str
    source_size: tuple[int, int]  # (W, H) 像素
    view_size: tuple[int, int]
    thumb_size: tuple[int, int]


class CropAsset(BaseModel):
    crop_id: str  # f"c{序号}"
    page: int = Field(ge=1)
    page_box: NormBBox  # 实际裁剪区域（已 pad 和 clamp），相对整页
    requested_box: NormBBox  # VLM 请求的区域，相对整页
    path: str
    size: tuple[int, int]
    target: str  # VLM 声称要读取的内容
    pixel_box: tuple[int, int, int, int]  # 在 source 图上的 (X1, Y1, X2, Y2)


# ---------- VLM 输出模式（字段名沿用 ViDoRAG agent_prompt.py） ----------
class SeekerOutput(BaseModel):
    reason: str
    summary: str
    choice: list[int]


class InspectorAnswer(BaseModel):
    action: Literal["answer"]
    reason: str
    answer: str
    reference: list[int] = Field(min_length=1)  # ViDoRAG vidorag_agents.py:116 要求非空


class InspectorAnswerM2(InspectorAnswer):
    confidence: float = Field(ge=0.0, le=1.0)


class InspectorSeek(BaseModel):
    action: Literal["seek"]
    reason: str
    information: str
    choice: list[int]  # 允许为空（ViDoRAG 示例 1）


class InspectorZoom(BaseModel):  # [Design] 新增分支
    action: Literal["zoom"]
    reason: str
    image: int = Field(ge=0)
    bbox_2d: tuple[float, float, float, float]
    target: str


InspectorOutput = Annotated[
    Union[InspectorAnswer, InspectorSeek, InspectorZoom],
    Field(discriminator="action"),
]

InspectorOutputM2 = Annotated[
    Union[InspectorAnswerM2, InspectorSeek, InspectorZoom],
    Field(discriminator="action"),
]


class SynthesizerOutput(BaseModel):
    reason: str
    answer: str
    reference: list[int] = Field(default_factory=list)


class SynthesizerTextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    answer: str
    reference_pages: list[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices("reference_pages", "reference"),
    )


# ---------- 计量与坐标契约（供主图与子图共享，避免循环导入） ----------
class UsageTotals(BaseModel):
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    missing_usage_calls: int = 0
    latency_ms: float = 0.0


class CitedRegion(BaseModel):
    page: int = Field(ge=1)
    page_box: NormBBox | None = None  # None 表示整页引用
    target: str | None = None


# ---------- 运行记录 ----------
class VLMUsage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float
    model: str


class StepRecord(BaseModel):
    step: int
    node: Literal[
        "seeker",
        "inspector",
        "zoom",
        "synthesizer",
        "critic",
        "supervisor",
        "ingest",
        "pack_report",
    ]
    input_images: list[str]  # 路径
    output: dict  # 校验后对象的 model_dump()
    usage: VLMUsage | None
    attempts: int


class FinalResult(BaseModel):
    status: Literal["answered", "synthesized", "no_answer", "budget_exhausted"]
    answer: str | None
    reason: str | None
    reference_pages: list[int]
    crops: list[CropAsset]
    steps: int
    total_prompt_tokens: int
    total_completion_tokens: int
    trace_id: str


class FinalResultM2(BaseModel):
    status: Literal["answered", "synthesized", "no_answer", "budget_exhausted"]
    answer: str | None
    reason: str | None
    reference_pages: list[int]
    cited_regions: list[CitedRegion]
    inspect_rounds: int
    seek_rounds: int
    usage: UsageTotals
    trace_id: str
