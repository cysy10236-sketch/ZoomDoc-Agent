# ZoomDoc-Agent：架构蓝图与施工计划

> 文档版本：v0.5（2026-11-10）。v0.5 变更：新增 §13 Milestone 4 架构契约与科学评测设计、§14 Milestone 4 施工指导手册（正式基准评测、对等滑窗网格基线 `run_sliding_grid_baseline`、H1 Token 节省率 Bootstrap CI 检验、渲染耗时占比剖析与 M5 Redis 触发裁决）。v0.4 变更：新增 §11 Milestone 3 架构契约与瓶颈审查、§12 Milestone 3 施工指导手册（PageIndex 式无向量层级树索引 + Navigator 导航子 Agent，解除 20 页硬上限，支撑 100+ 页长文档）。v0.3 变更：里程碑重排为 M2 子图隔离 → M2.5 真实 VLM 评测门 → M3 树索引 → M4 正式评测 → M5 Redis（条件触发）；新增 §10 M2.5 规格。v0.2 变更：新增 §7 Milestone 1 代码审查、§8 Milestone 2 架构契约、§9 Milestone 2 施工手册。§4–§5 保留为 M1 施工记录，不再修改。
> 读者：总架构师（审阅）、Gemini 3.8 Flash（施工方）
> 证据来源：`references/ViDoRAG`（浅克隆 HEAD）、`references/PageIndex`（浅克隆 HEAD）。所有源码引用格式为 `文件:行号`，行号基于本地克隆。
> 认知标注：`[Fact]` = 已读源码确认；`[Design]` = 本项目新设计，ViDoRAG 或 PageIndex 中没有；`[Hypothesis]` = 未经测试，附带证伪路径；`[UNVERIFIED]` = 外部事实，施工前必须核对。

---

## 0. LOD-0 结论

1. `[Fact]` **ViDoRAG 里没有视觉放大或裁剪能力。** `utils/image_preprosser.py:6` 的 `concat_images_with_bbox` 声明了 `bboxs` 参数，但函数体（第 23–90 行）从未读取它。仓库中搜索 `crop|bbox|zoom`，只命中这个函数签名、它的 docstring，以及 `vidorag_agents.py` 中 3 处调用（均未传 `bboxs`）。ViDoRAG 的"视觉"机制只有三部分：**多页网格拼图、文字版编号映射（page_map）、Seeker→Inspector→Synthesizer 迭代**。
2. 因此，Milestone 1 的 **BBox 定位 Prompt、坐标归一化与裁剪重采样公式都属于 `[Design]`**，不是从源码中提取的。可以从源码直接继承的有：三类 Agent 的 JSON 字段约定、Inspector 的三路分支语义、网格排布表与网格坐标几何、JSON 解析、重试上限。
3. `[Fact]` 从 PageIndex 可继承的有：`<physical_index_X>` 页码锚点、`structure: "x.x.x"` 层级编码、按父级 structure 前缀建树（`utils.py:907 list_to_tree`）、按 level 栈式建树（`page_index_md.py:192 build_tree_from_nodes`）、大节点递归拆分（`page_index_classic.py:1175`）、以页为单位的检索代价模型（`tree_optimize.py:1-48`）。这些在 Milestone 2 引入。
4. `[Fact]` **许可证风险**：`references/ViDoRAG` 根目录没有 LICENSE 文件（已用 `Get-ChildItem` 确认）；PageIndex 采用 MIT License（`references/PageIndex/LICENSE` 第 1 行）。本项目的 Prompt 只沿用 ViDoRAG 的 **JSON 模式与任务结构**，并重写措辞，不逐字复制其 few-shot 示例，并在代码注释中注明出处。

---

## 1. LOD-1 源码审计：可继承机制与已知缺陷

### 1.1 ViDoRAG 多 Agent 状态机（`vidorag_agents.py`）

| 组件 | 源码位置 | 输入 | 输出 JSON 字段 | 控制语义 |
|---|---|---|---|---|
| Seeker | `:12-63` | question + 候选页网格拼图 | `reason`, `summary`, `choice: List[int]` | 被选中的页**从候选池删除**（`:53-54`），防止重复选中 |
| Inspector | `:65-132` | question + 累积页（`buffer_images.extend`, `:84`） | 分支 A：`reason`, `answer`, `reference` / 分支 B：`reason`, `information`, `choice` | A 且 `len(ref)==len(buffer)` → 直接作答（`:118-119`）；A 且 ref 为子集 → 只带被引用页交给 Synthesizer（`:120-122`）；B → buffer 截取为 `choice` 对应的页，`information` 作为反馈交回 Seeker（`:123-127`） |
| Synthesizer | `:134-167` | question + Inspector 草稿答案 + 被引用页 | `reason`, `answer` | 终止节点 |
| 调度器 | `:169-194` | — | — | `while True` 循环：`answer` / `synthesizer` / `seeker` / `None` |

Seeker 带反馈重新运行时（`:28-30`），把 `## Additional Information\n{feedback}` 追加到问题后面，并在**剩余候选池**上再选一次。

### 1.2 网格拼图几何（`utils/image_preprosser.py:23-90`，`agent/map_dict.py:1-22`）

- 所有页先缩放到**出现次数最多的尺寸**（`:28-33`）。宽高比不同的页会被非等比拉伸。
- 排布 `(rows, columns)` 来自 `arrangement_map_dict`（n=1..20，例如 3→(2,2)，5→(3,2)，8→(2,4)）。
- 空格用白图填充（`:53-57`），分隔线为黑色、宽 `line_width=40`（`:65`）。
- 画布尺寸：`W_T = C·W + (C−1)·L`，`H_T = R·H + (R−1)·L`（`:63-64`）。
- 第 i 格的原点：`x0 = (i mod C)·(W+L)`，`y0 = ⌊i/C⌋·(H+L)`（`:68-73`）。
- 调用方传 `scale=1` 时走 `:86-88`，`int(W_T·1)`，**即不缩放**，按原分辨率拼图。视觉 Token 开销在这里产生，也是本项目缩略图粗筛要压缩的对象。

### 1.3 已知缺陷（Bad Case 档案，施工中禁止复现）

| ID | 源码位置 | 缺陷 | 本项目对策 |
|---|---|---|---|
| V1 | `vidorag_agents.py:153` | Synthesizer 的 `while True` 没有重试上限，解析一直失败时会死循环 | 所有 Agent 统一 `max_attempts=3`，超出后抛 `AgentOutputError` |
| V2 | `llms/llm.py:12` | `_encode_image` 用了 `io.BytesIO`，但文件只 `from io import BytesIO`，没有 `import io`，传入 PIL Image 时触发 NameError | 编码函数只接收 `PIL.Image`，带单元测试 |
| V3 | `llms/llm.py:91` + `vidorag_agents.py:35` | GPT 路径对输入调用 `Path(img)`，但调度器传入的是 PIL Image（拼图结果），类型不匹配 | VLM 客户端用 Pydantic 定义输入类型（`ImagePart`） |
| V4 | `vidorag_agents.py:56-60` 等 | 宽泛的 `except Exception` 加 `print`，所有错误都被吞掉后重试 | 只捕获 `json.JSONDecodeError` 和 `pydantic.ValidationError`，其余异常直接上抛 |
| V5 | `vidorag_agents.py:26` | 候选池为空时 `page_map[0]` 触发 KeyError | 图路由先判断候选池为空，不进入 Seeker |
| V6 | `map_dict.py:66` | `page_map_dict_normal[2]` 写成 "the second image number is 0" | 编号映射文本由函数生成，不硬编码 |
| V7 | `map_dict.py:42` | `page_map_dict[3]` 缺少 "The first row" | 同 V6 |
| V8 | `llms/llm.py:100` | 模型名硬编码为浮动别名 `"gpt-4o"` | 模型名必须由配置给出，且必须是带版本号的快照 |
| V9 | 全文件 | `print` 日志，没有 trace、Token 计数和延迟 | JSONL 结构化日志（§5.9） |

### 1.4 PageIndex 树索引思想（Milestone 2 使用）

| 机制 | 源码位置 | 要点 |
|---|---|---|
| 页码锚点 | `page_index_classic.py:655-657` | 文本中用 `<physical_index_X>` 标出第 X 页的起止，LLM 输出时原样保留标签，事后解析成整数（`:55 _parse_physical_index`），并校验范围（`:66`） |
| 层级编码 | `:651, :660-667` | `structure` 为 `"1"`, `"1.1"`, `"1.2"`；字段为 `structure`, `title`, `physical_index` |
| 建树 | `utils.py:907-940` | 把 `structure` 去掉最后一段得到父级 key，挂到父节点的 `nodes` 下 |
| 建树（Markdown） | `page_index_md.py:192-223` | 维护 `(node, level)` 栈，弹出所有 level ≥ 当前 level 的节点；`node_id` 为 4 位零填充 |
| 大节点拆分 | `page_index_classic.py:1175-1209` | 同时满足"无子节点"、"页跨度 > max_page_num_each_node"、"token ≥ max_token_num_each_node"时，对该节点的页段再跑一次无目录建树，再并发递归 |
| 代价模型 | `tree_optimize.py:4-24` | 路由代价 R(v)=1 页，折叠代价 S(v)=子树页数，`expand iff R(v)+max_i S(c_i) < S(v)` |
| Prompt 注入防护 | `page_index_classic.py:13-53` | 文档文本先做关键词脱敏，再包进 `<user_document>` 定界标签 |
| Agent 工具 | `agent_tools.py:1185-1186` | `get_document_structure`（先看树）→ `get_page_content`（按页读取） |

---

## 2. 系统架构

### 2.1 融合映射

| 来源 | 原机制 | ZoomDoc 对应 | 引入阶段 |
|---|---|---|---|
| PageIndex | 文本树 + 按页取内容 | 树节点挂接页缩略图键，Navigator 只读标题和摘要（纯文本）后选节点 | M2 |
| PageIndex | 以页为单位的代价模型 | 视觉 Token 预算：只有被选中节点下的页进入缩略图网格 | M2 |
| ViDoRAG | Seeker 拼图选页 | Seeker 只看**低分辨率缩略图**网格 | M1 |
| ViDoRAG | Inspector 三路分支 | 扩展为四路：`answer` / `seek` / `zoom` / 隐式 none | M1 |
| — `[Design]` | — | `zoom`：VLM 输出 bbox → 在高分辨率源图上裁剪 → 上采样 → 作为新图像证据回到 Inspector | M1 |
| — `[Design]` | — | LangGraph 主从子图，Supervisor 只接收结构化证据，图像不进入主上下文 | M2（v0.2 调整） |
| — `[Design]` | — | Redis：渲染图、缩略图、裁剪图的内容寻址缓存 + LangGraph checkpoint | M3（v0.2 调整） |

### 2.2 三级分辨率（`[Design]`）

| 层级 | 用途 | 默认长边 | 消费者 |
|---|---|---|---|
| `thumb` | 粗筛 | 448 px | Seeker（网格） |
| `view` | 整页检视 | 1024 px | Inspector（多图模式） |
| `source` | 裁剪源 | 按 200 DPI 渲染 | Zoom 工具（不直接送入 VLM） |

`[Hypothesis H1]`：对 Qwen2.5-VL 类模型，视觉 Token 数约为 `⌈H/28⌉·⌈W/28⌉` `[UNVERIFIED，需对照官方文档与 API 返回的 usage]`。缩略图长边从 1024 降到 448，单页视觉 Token 约降为原来的 (448/1024)² ≈ 19%。用户提出的"整体节省 60%"**没有实测依据**。证伪路径：在 M5 中，对同一组问题（N ≥ 50，temperature=0，固定 seed）记录本系统与 ViDoRAG 式全分辨率网格基线的 `prompt_tokens` 之和，计算比值并给出 95% bootstrap 置信区间；比值 > 0.4 则 H1 的 60% 目标不成立。

### 2.3 目标架构（M3 完成态）

```mermaid
flowchart TD
    U["User Question + PDF"] --> ING["Ingestion: render source/view/thumb, sha256 doc_id"]
    ING --> RC[("Redis: image cache + tree + checkpoint")]
    ING --> TREE["Tree Builder (PageIndex-style)"]
    TREE --> RC
    SUP["Supervisor Graph (text-only context)"] --> NAV["Navigator Sub-Agent: tree search over titles/summaries"]
    NAV -->|"EvidenceReport: page ids"| SUP
    SUP --> VIS["Visual Sub-Agent: Seeker -> Inspector <-> Zoom"]
    VIS -->|"EvidenceReport: answer draft, refs, crop keys"| SUP
    SUP --> SYN["Synthesizer"]
    SYN --> OUT["Final Answer + citations (page, bbox)"]
    VIS <--> RC
    NAV <--> RC
```

上下文隔离契约 `[Design]`：子 Agent 内部的消息和图像只存在于子图私有 state，返回给 Supervisor 的只有 `EvidenceReport`（Pydantic，纯文本 + 缓存键）。Supervisor 的上下文中没有 base64 图像。

### 2.4 帕累托权衡

| 决策 | 选项 A | 选项 B | 选择与条件 |
|---|---|---|---|
| Seeker 输入形式 | 网格单图（ViDoRAG 默认） | 多图分条 | M1 选 A：编号由 page_map 约束，单次请求；代价是单格分辨率低 |
| Inspector 输入形式 | 网格单图 | 多图分条 | M1 选 B：bbox 天然以单图为参考系，避免跨格框；代价是每张图有固定 Token 开销 |
| bbox 输出约定 | 归一化 0–1000 | 绝对像素 | 默认 0–1000（与发送分辨率无关）；Qwen2.5-VL 原生为绝对像素 `[UNVERIFIED]`，通过 `bbox_format` 适配器切换 |
| 状态存储 | 内存 | Redis | M1 内存（定义 Protocol 接口），M2 换成 Redis 实现，不改调用方 |
| 重试策略 | 原 prompt 重发（ViDoRAG） | 附上校验错误再发 | 选 B：temperature=0 时原样重发大概率得到相同输出 |

### 2.5 里程碑

| 阶段 | 交付物 | 验收断言 |
|---|---|---|
| **M1** | 最小主动视觉 ReAct 闭环（单 PDF，≤ 20 页） | §5.12 全部通过 |
| **M2**（v0.2） | LangGraph Supervisor + Inspector 子图上下文隔离 + Critic 回环 + Token 全量计量 | §9.6 全部通过，且 M1 的 24 个测试不回归 |
| **M2.5**（v0.3，评测门） | 真实 VLM 小规模消融：合成 PDF 变体 N ≥ 20 × 3 组配置 | §10.4 判决规则；未通过则停止进入 M3，回到设计评审 |
| M3（v0.3） | PageIndex 式树索引 + Navigator 子 Agent（第二个子图）；去掉 20 页上限 | 100 页 PDF 上，Seeker 单次输入页数 ≤ 20；报告 Seeker 召回率（参考页 ∈ 选中页） |
| M4（v0.3） | 正式评测：ViDoSeek（`ViDoRAG/README.md:14,37` 的数据集）+ 1 个外部集，对比全分辨率网格基线 | 报告准确率、Token、P95 延迟、失败样例集、n-gram 污染审计；判定 H1 |
| M5（v0.3） | Redis 内容寻址缓存 + LangGraph checkpoint | **条件触发**：M4 实测渲染耗时占单次运行 ≥ 20%，或单次运行 P95 ≥ 120 s 时实施；否则降级为可选 |
| M6（可选） | 混合检索（移植 `search_engine.py:21 gmm` 动态截断） | 与纯树导航做 A/B：相同 N、相同模型版本下的召回@K |

> v0.3 排期依据（`[Fact]`，N=2，仅作动机，不作统计结论）：`runs/4ef2be254cef/result.json` 未 zoom，答案 `ZD-7319-DX`（错误），理由自称 "legible"；`runs/57ad6979395a/result.json` 执行 1 次 zoom，答案 `ZD-7319-QX`（正确）。trace 中单次 VLM 调用 `latency_ms` 为 14030 与 17618，延迟由 VLM 主导，渲染耗时未测。`runs/smoke_report.md` 不存在，§5.12 的 H2 冒烟验证未执行。

---

## 3. 总体约束（适用于所有阶段）

1. Python 3.11，独立虚拟环境 `.venv`。
2. 依赖安装完成后执行 `pip freeze > requirements.lock` 并提交。**蓝图不提供版本号**（未经安装核实）。
3. 所有 VLM 调用：`temperature=0`，`seed` 来自配置。模型名必须由环境变量显式提供，禁止代码内写默认值或浮动别名。
4. 禁止 `except Exception` 吞掉错误（V4）。业务代码中禁止 `print`，统一使用 §5.9 的 logger（CLI 输出最终结果除外）。
5. 状态中只存图像**键/路径**，不存 base64 或 PIL 对象。
6. 不修改 `references/` 下任何文件。

---

## 4. Milestone 1 范围

**目标**：输入一个 ≤ 20 页的 PDF 和一个问题，系统完成 `渲染 → Seeker 缩略图粗筛 → Inspector 整页检视 → (Zoom 高清裁剪 ⇄ Inspector)* → 作答/合成` 闭环，并输出结构化 trace。

**不在范围内**：树索引、Redis、子图隔离、检索器、多文档、OCR。

**硬性边界**：
- 页数 > 20：抛 `ValueError("M1 supports <= 20 pages")`。
- 单次运行：`max_steps=8`（Seeker、Inspector、Zoom 每执行一次计 1 步）。
- 放大次数：`max_zooms=3`。
- Inspector 单次输入图像数 ≤ 6（view 页 + crop）。超出时保留最近的 crop，并按页码顺序保留 view 页。

---

## 5. Milestone 1 施工说明书（给 Gemini 3.8 Flash）

### 5.1 目录结构（严格按此创建）

```
ZoomDoc-Agent/
├── pyproject.toml
├── requirements.lock            # 安装后 pip freeze 生成
├── .env.example
├── PROGRESS.md                  # 施工进度清单（每完成一个文件就勾选）
├── zoomdoc/
│   ├── __init__.py
│   ├── config.py
│   ├── schemas.py
│   ├── prompts.py
│   ├── errors.py
│   ├── cache.py
│   ├── observability.py
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── render.py
│   │   ├── grid.py
│   │   ├── geometry.py
│   │   └── zoom.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── vlm_client.py
│   │   └── json_parser.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── seeker.py
│   │   ├── inspector.py
│   │   └── synthesizer.py
│   └── graph/
│       ├── __init__.py
│       ├── state.py
│       ├── nodes.py
│       └── builder.py
├── scripts/
│   ├── make_synthetic_pdf.py
│   └── run_m1.py
└── tests/
    ├── conftest.py
    ├── fake_vlm.py
    ├── test_geometry.py
    ├── test_grid.py
    ├── test_zoom.py
    ├── test_json_parser.py
    ├── test_page_map.py
    ├── test_agents.py
    └── test_graph_e2e.py
```

依赖（只写包名，不写版本）：`langgraph`, `pydantic`, `pydantic-settings`, `pymupdf`, `pillow`, `openai`, `python-dotenv`, `pytest`。

### 5.2 `zoomdoc/config.py`

```python
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BBoxFormat = Literal["xyxy_1000", "yxyx_1000", "xyxy_abs"]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZOOMDOC_", env_file=".env", extra="forbid")

    vlm_base_url: str                       # OpenAI 兼容端点
    vlm_api_key: str
    vlm_model: str                          # 必填，必须是带版本号的快照名
    vlm_seed: int = 42
    vlm_max_tokens: int = 1024
    vlm_timeout_s: float = 120.0
    bbox_format: BBoxFormat = "xyxy_1000"
    image_encoding: Literal["png", "jpeg"] = "png"
    jpeg_quality: int = Field(90, ge=50, le=100)

    source_dpi: int = Field(200, ge=72, le=400)
    view_long_side: int = Field(1024, ge=256)
    thumb_long_side: int = Field(448, ge=128)
    grid_line_width: int = 40               # ViDoRAG vidorag_agents.py:35

    zoom_pad_ratio: float = Field(0.10, ge=0.0, le=1.0)
    zoom_min_norm_side: float = Field(0.05, gt=0.0, le=1.0)
    zoom_target_long_side: int = 1024
    zoom_max_upscale: float = 4.0

    max_pages: int = 20
    max_steps: int = 8
    max_zooms: int = 3
    max_inspector_images: int = 6
    max_attempts: int = 3                   # ViDoRAG: times > 2 -> raise

    runs_dir: str = "runs"
```

提供 `def get_settings() -> Settings`（带 `functools.lru_cache`）。缺少必填项时由 pydantic 抛出 `ValidationError`（快速失败）。

### 5.3 `zoomdoc/errors.py`

```python
class ZoomDocError(Exception): ...
class AgentOutputError(ZoomDocError):      # 超过 max_attempts 后仍无法解析或校验
    def __init__(self, agent: str, attempts: int, last_raw: str, last_error: str): ...
class GeometryError(ZoomDocError): ...       # bbox 非法、落在分隔线上、跨格
class BudgetExceeded(ZoomDocError): ...
```

### 5.4 `zoomdoc/schemas.py`（完整 Pydantic 模型）

```python
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, model_validator, ConfigDict

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
    def width(self) -> float: return self.x2 - self.x1
    @property
    def height(self) -> float: return self.y2 - self.y1

class PageAsset(BaseModel):
    page: int = Field(ge=1)                 # 1-based 物理页码（与 PageIndex physical_index 一致）
    source_path: str
    view_path: str
    thumb_path: str
    source_size: tuple[int, int]            # (W, H) 像素
    view_size: tuple[int, int]
    thumb_size: tuple[int, int]

class CropAsset(BaseModel):
    crop_id: str                            # f"c{序号}"
    page: int = Field(ge=1)
    page_box: NormBBox                      # 实际裁剪区域（已 pad 和 clamp），相对整页
    requested_box: NormBBox                 # VLM 请求的区域，相对整页
    path: str
    size: tuple[int, int]
    target: str                             # VLM 声称要读取的内容
    pixel_box: tuple[int, int, int, int]    # 在 source 图上的 (X1, Y1, X2, Y2)

# ---------- VLM 输出模式（字段名沿用 ViDoRAG agent_prompt.py） ----------
class SeekerOutput(BaseModel):
    reason: str
    summary: str
    choice: list[int]

class InspectorAnswer(BaseModel):
    action: Literal["answer"]
    reason: str
    answer: str
    reference: list[int] = Field(min_length=1)    # ViDoRAG vidorag_agents.py:116 要求非空

class InspectorSeek(BaseModel):
    action: Literal["seek"]
    reason: str
    information: str
    choice: list[int]                              # 允许为空（ViDoRAG 示例 1）

class InspectorZoom(BaseModel):                    # [Design] 新增分支
    action: Literal["zoom"]
    reason: str
    image: int = Field(ge=0)
    bbox_2d: tuple[float, float, float, float]
    target: str

InspectorOutput = Annotated[
    Union[InspectorAnswer, InspectorSeek, InspectorZoom],
    Field(discriminator="action"),
]

class SynthesizerOutput(BaseModel):
    reason: str
    answer: str
    reference: list[int] = Field(default_factory=list)

# ---------- 运行记录 ----------
class VLMUsage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float
    model: str

class StepRecord(BaseModel):
    step: int
    node: Literal["seeker", "inspector", "zoom", "synthesizer"]
    input_images: list[str]                 # 路径
    output: dict                            # 校验后对象的 model_dump()
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
```

### 5.5 坐标与几何（`zoomdoc/vision/geometry.py`）`[Design]`

#### 5.5.1 符号定义

- 图像 I 的像素尺寸为 (W, H) ∈ ℕ²。
- 规范框 b = (x₁, y₁, x₂, y₂) ∈ [0,1]⁴，满足 x₁ < x₂、y₁ < y₂，参考系为 I。
- 归一化坐标与分辨率无关：对同一页，在 thumb、view、source 三个层级上，b 指向同一块物理区域。这一性质成立的前提是三级图像由同一页**等比**缩放得到（render.py 必须保证）。
- ε = 1e-6，用于抵消浮点误差。

#### 5.5.2 VLM 原始输出 → 规范框

| `bbox_format` | VLM 输出 `bbox_2d = [a, b, c, d]` | 换算 |
|---|---|---|
| `xyxy_1000`（默认） | `[x_min, y_min, x_max, y_max]`，整数 0–1000 | x₁=a/1000, y₁=b/1000, x₂=c/1000, y₂=d/1000 |
| `yxyx_1000` | `[y_min, x_min, y_max, x_max]`，0–1000 | x₁=b/1000, y₁=a/1000, x₂=d/1000, y₂=c/1000 |
| `xyxy_abs` | 相对于**发送给 VLM 的那张图**（尺寸 Wₛ×Hₛ）的绝对像素 | x₁=a/Wₛ, y₁=b/Hₛ, x₂=c/Wₛ, y₂=d/Hₛ |

换算后：先把各分量 clamp 到 [0,1]，若 x₁>x₂ 或 y₁>y₂ 则交换（VLM 偶尔会颠倒顺序），交换后仍退化（宽或高为 0）则抛 `GeometryError`。

#### 5.5.3 Crop 坐标 → 整页坐标（支持对 crop 再放大）

若 VLM 在一张 crop 图上给出框 b（参考系为该 crop），而这张 crop 的实际区域为 B = `page_box`：

```
X1 = B.x1 + b.x1 · (B.x2 − B.x1)
Y1 = B.y1 + b.y1 · (B.y2 − B.y1)
X2 = B.x1 + b.x2 · (B.x2 − B.x1)
Y2 = B.y1 + b.y2 · (B.y2 − B.y1)
```

#### 5.5.4 Pad → 最小尺寸 → Clamp

```
w = x2 − x1,  h = y2 − y1,  p = zoom_pad_ratio
x1' = x1 − p·w,  x2' = x2 + p·w,  y1' = y1 − p·h,  y2' = y2 + p·h
若 (x2' − x1') < m（m = zoom_min_norm_side）：以中心 cx 为轴扩展为 [cx − m/2, cx + m/2]
y 方向同理
平移进 [0,1]：若 x1' < 0 则整体右移 −x1'；若 x2' > 1 则整体左移 x2' − 1；最后再 clamp 到 [0,1]
```

#### 5.5.5 规范框 → 源图像素

```
X1 = floor(x1'·W + ε),  Y1 = floor(y1'·H + ε)
X2 = ceil (x2'·W − ε),  Y2 = ceil (y2'·H − ε)
断言 0 ≤ X1 < X2 ≤ W 且 0 ≤ Y1 < Y2 ≤ H
```

#### 5.5.6 上采样

```
L = zoom_target_long_side,  s_max = zoom_max_upscale
w_px = X2 − X1,  h_px = Y2 − Y1
s = min(s_max, L / max(w_px, h_px))
out = (round(w_px·s), round(h_px·s))，使用 Image.Resampling.LANCZOS
s < 1 时同样执行（降采样到长边 L）
```

#### 5.5.7 网格坐标 → 页坐标（M1 用于 Seeker 测试与 M2 预留，Inspector 不使用）

网格参数：n 页，排布 (R, C)，单格 (W, H)，分隔线宽 L，画布 W_T = C·W+(C−1)·L，H_T = R·H+(R−1)·L。给定网格图上的归一化点 (u, v)：

```
X = u·W_T,  Y = v·H_T
c = floor(X / (W+L)),  r = floor(Y / (H+L))
lx = X − c·(W+L),  ly = Y − r·(H+L)
若 lx > W 或 ly > H：点落在分隔线上 → GeometryError
i = r·C + c；若 i ≥ n：点落在填充格 → GeometryError
页内坐标 = (lx / W, ly / H)，页索引为 i
```

一个框的两个角点必须映射到同一个 i，否则视为跨格框，抛 `GeometryError`。注意：ViDoRAG 把每页非等比缩放到众数尺寸（`image_preprosser.py:33`），但页内归一化坐标在逐页的轴向缩放下保持不变，所以映射结果仍然对应原页。

#### 5.5.8 函数签名

```python
EPS: float = 1e-6

def vlm_bbox_to_norm(raw: tuple[float, float, float, float], fmt: BBoxFormat,
                     sent_size: tuple[int, int] | None = None) -> NormBBox: ...
def crop_box_to_page_box(local: NormBBox, crop_page_box: NormBBox) -> NormBBox: ...
def pad_and_clamp(box: NormBBox, pad_ratio: float, min_side: float) -> NormBBox: ...
def norm_to_pixel(box: NormBBox, size: tuple[int, int]) -> tuple[int, int, int, int]: ...
def upsample_size(pixel_box: tuple[int, int, int, int], target_long_side: int,
                  max_upscale: float) -> tuple[int, int]: ...
def grid_point_to_page(u: float, v: float, n: int, arrangement: tuple[int, int],
                       cell_size: tuple[int, int], line_width: int) -> tuple[int, float, float]: ...
def grid_box_to_page(box: NormBBox, n: int, arrangement: tuple[int, int],
                     cell_size: tuple[int, int], line_width: int) -> tuple[int, NormBBox]: ...
```

### 5.6 视觉模块

#### `vision/render.py`

```python
def sha256_file(path: str) -> str: ...
def render_pdf(pdf_path: str, out_dir: str, settings: Settings) -> list[PageAsset]:
    """PyMuPDF：以 source_dpi 渲染（matrix = fitz.Matrix(dpi/72, dpi/72)），存为 PNG。
    view/thumb 由 source 等比缩放得到：scale = long_side / max(W, H)，只缩小不放大。
    幂等：目标文件已存在且尺寸匹配时跳过。
    页数 > settings.max_pages 时抛 ValueError。"""
def resize_long_side(img: Image.Image, long_side: int) -> Image.Image: ...
```

输出目录：`{runs_dir}/assets/{doc_sha256[:16]}/p{page:03d}_{source|view|thumb}.png`。

#### `vision/grid.py`（移植 `image_preprosser.py:6-90`，修正 V6/V7）

```python
ARRANGEMENT: dict[int, tuple[int, int]]   # 按 ViDoRAG map_dict.py:1-22 逐项填写 n=1..20，表中 n=1→(1,1) … n=20→(4,5)

def concat_grid(images: list[Image.Image], arrangement: tuple[int, int],
                line_width: int) -> tuple[Image.Image, tuple[int, int]]:
    """返回 (网格图, 单格尺寸)。规则：众数尺寸 → 每页 resize 到该尺寸 → 白色填充空格 → 黑色分隔线。
    不做最终缩放（输入已经是缩略图）。"""

def build_page_map(n: int, arrangement: tuple[int, int]) -> str:
    """生成网格编号说明，语义与 ViDoRAG page_map_dict 一致：
    n == 1: "Your choice is empty as [] or this image as [0]!"
    n >= 2: 对每一行 r 输出 "The {ordinal(r)} row, the first one is number k, the second one is number k+1, ..."
            结尾为 " The image numbers are from 0-{n-1}!"
    ordinal 使用 first..fifth；第 r 行实际页数 = min(C, n − r·C)。"""
```

#### `vision/zoom.py`

```python
def zoom(page: PageAsset, page_box_requested: NormBBox, settings: Settings,
         out_dir: str, crop_id: str, target: str) -> CropAsset:
    """pad_and_clamp → norm_to_pixel(source_size) → 裁剪 source → upsample_size → resize → 保存 PNG。
    幂等：文件名使用 f"{crop_id}_p{page}_{X1}_{Y1}_{X2}_{Y2}.png"。"""
```

### 5.7 VLM 客户端（`zoomdoc/llm/`）

#### `vlm_client.py`

```python
class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    path: str                                # 只接受文件路径（修正 V2/V3）

Part = Annotated[Union[TextPart, ImagePart], Field(discriminator="type")]

class VLMResponse(BaseModel):
    text: str
    usage: VLMUsage

class VLMClient(Protocol):
    def generate(self, parts: list[Part], *, extra_messages: list[dict] | None = None) -> VLMResponse: ...

class OpenAICompatVLM:
    def __init__(self, settings: Settings): ...
    def generate(self, parts: list[Part], *, extra_messages: list[dict] | None = None) -> VLMResponse:
        """把 parts 按顺序转为 OpenAI content：
        text  -> {"type": "text", "text": ...}
        image -> {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        调用 chat.completions.create(model=settings.vlm_model, temperature=0,
             seed=settings.vlm_seed, max_tokens=settings.vlm_max_tokens, messages=[...])。
        extra_messages 用于重试时追加 assistant 原输出 + user 纠错信息。
        用 time.perf_counter 计时；usage 为空时记 None，不伪造数值。"""

def encode_image_file(path: str, fmt: Literal["png", "jpeg"], quality: int) -> str: ...
```

#### `json_parser.py`（移植 `parse_tool.py:19-27`）

```python
def extract_json(text: str) -> dict:
    """去掉 ``` 与 ```json；取第一个 '{' 到最后一个 '}' 之间的内容，用 json.loads 解析；
    找不到成对花括号时对全文 json.loads（与 ViDoRAG 一致，失败即抛 JSONDecodeError）。
    结果不是 dict 时抛 ValueError。"""
```

### 5.8 Prompt（`zoomdoc/prompts.py`）

占位符统一用 `str.replace`，不用 `str.format`（模板中有 JSON 花括号，ViDoRAG 也用 replace，见 `vidorag_agents.py:26`）。

#### 5.8.1 `SEEKER_PROMPT`（JSON 模式沿用 `agent_prompt.py:1-46`；措辞重写，加入缩略图说明）

```text
You are an assistant that locates evidence in document pages. The input is ONE image composed of several LOW-RESOLUTION page thumbnails arranged in a grid and separated by black lines. Small text may be unreadable; judge relevance from layout, headings, tables, figures and any legible words.

Images are numbered from zero, counting left to right, then top to bottom.

## Question
{question}

## Response Format
Respond with JSON only:
{
    "reason": "Evaluate the relevance of each thumbnail to the question step by step",
    "summary": "Information related to the question that is legible in the thumbnails",
    "choice": [<image numbers>]
}

## Example
Question: What is the departure time of the train from Hangzhou to Beijing?
{
    "reason": "Image 0 is a weather report. Image 1 looks like a ticket with a route line from Hangzhou to Beijing; the time field is too small to read. Image 2 is an airport timetable, not related.",
    "summary": "A train ticket for Hangzhou to Beijing exists; its time field needs closer reading.",
    "choice": [1]
}

## Rules
- Select every image that may be related, even weakly.
- The summary only contains question-related information.
- Do not mention image numbers in the summary.
- Return JSON only.

{page_map}
```

反馈轮（对应 `vidorag_agents.py:29`）：`{question}` 替换为 `question + "\n\n## Additional Information\n" + feedback`。

#### 5.8.2 `INSPECTOR_PROMPT`（分支 A/B 沿用 `agent_prompt.py:48-105`，分支 C 为 `[Design]`）

送给 Inspector 的消息是**多图分条**：每张图前插入一条文字标签（见 5.8.4），最后附上本 Prompt。

```text
You are an assistant that answers questions from document images. Each image is preceded by a label stating whether it is a FULL PAGE or a ZOOMED CROP of a page.

## Task
Choose exactly ONE action:
- "answer": the images are sufficient. Answer and list the images you used.
- "zoom": the needed information is on one of the images but too small or blurry to read reliably. Request a high-resolution crop of that region.
- "seek": the images are insufficient. State what information is missing and keep only the related images.

## Question
{question}

## Zoom budget
Remaining zoom operations: {zoom_left}. If it is 0, do not choose "zoom".

## Answer Format (JSON only, one of the following)
A. {
    "action": "answer",
    "reason": "Solve the question step by step",
    "answer": "Brief answer in several words",
    "reference": [<image numbers>]
}
B. {
    "action": "zoom",
    "reason": "Why the region must be enlarged",
    "image": <image number to zoom into>,
    "bbox_2d": [x_min, y_min, x_max, y_max],
    "target": "What you expect to read in the region"
}
   bbox_2d uses coordinates normalized to 0-1000 relative to THAT image: (0,0) is its top-left corner, (1000,1000) its bottom-right corner. Make the box tight around the region but include the full text line or table cell.
C. {
    "action": "seek",
    "reason": "Evaluate each image's relevance one by one, then reason step by step",
    "information": "The specific information still required",
    "choice": [<image numbers that are related>]
}

## Examples
{"action": "answer", "reason": "The process diagram ends with an output labeled purity 42%.", "answer": "42%", "reference": [0]}
{"action": "zoom", "reason": "Image 1 contains the fee table, but the digits in the last row are illegible at this resolution.", "image": 1, "bbox_2d": [120, 610, 880, 700], "target": "the total fee in the last table row"}
{"action": "seek", "reason": "The images describe the campaign goals but not its success metrics.", "information": "Success metrics of the campaign.", "choice": [0]}

Images are numbered from 0 to {max_index} in the order they are given.
Return JSON only.
```

`bbox_format != "xyxy_1000"` 时，由 `prompts.bbox_instruction(fmt)` 生成对应的坐标说明段落，替换上面的"bbox_2d uses..."一行（`yxyx_1000`：`[y_min, x_min, y_max, x_max]`；`xyxy_abs`：相对该图的绝对像素，并在标签中注明该图尺寸）。

#### 5.8.3 `SYNTHESIZER_PROMPT`（沿用 `agent_prompt.py:107-122`）

```text
You are an assistant that answers questions from document images. Answer using the images and the related information, and list the images you used.

## Question
{question}

## Related Information
{draft}

## Answer Format (JSON only)
{
    "reason": "Solve the question step by step",
    "answer": "Brief answer in several words",
    "reference": [<image numbers>]
}

Images are numbered from 0 to {max_index} in the order they are given.
```

`draft` 为空时删除 `## Related Information` 整段（对应 `vidorag_agents.py:144-145`）。

#### 5.8.4 图像标签

```python
def label_full_page(idx: int, page: int) -> str:
    return f"Image {idx}: FULL PAGE {page} (medium resolution)."
def label_crop(idx: int, crop: CropAsset) -> str:
    b = crop.page_box
    return (f"Image {idx}: ZOOMED CROP of page {crop.page}, region "
            f"[{round(b.x1*1000)}, {round(b.y1*1000)}, {round(b.x2*1000)}, {round(b.y2*1000)}] "
            f"(x_min, y_min, x_max, y_max in 0-1000 page coordinates). Requested to read: {crop.target}.")
```

### 5.9 可观测性（`zoomdoc/observability.py`）

```python
class TraceLogger:
    def __init__(self, runs_dir: str, trace_id: str): ...   # 写入 {runs_dir}/{trace_id}/trace.jsonl
    def span(self, node: str) -> "Span": ...                 # 上下文管理器，生成 span_id=uuid4().hex[:12]
    def event(self, *, span_id: str, node: str, status: Literal["ok", "retry", "error"],
              latency_ms: float, prompt_tokens: int | None, completion_tokens: int | None,
              attempt: int, payload: dict) -> None: ...
```

每行 JSON 的必需字段：`ts`（ISO8601 UTC）、`trace_id`、`span_id`、`node`、`status`、`latency_ms`、`prompt_tokens`、`completion_tokens`、`attempt`、`payload`。原始 VLM 文本放在 `payload.raw`（截断到 4000 字符，并记录 `payload.raw_len`）。

### 5.10 Agent 层（`zoomdoc/agents/`）

#### `base.py`：带校验的调用循环（修正 V1/V4）

```python
T = TypeVar("T")

def call_with_schema(vlm: VLMClient, parts: list[Part], adapter: TypeAdapter[T], *,
                     agent: str, max_attempts: int, logger: TraceLogger,
                     validate: Callable[[T], None] | None = None) -> tuple[T, VLMResponse, int]:
    """attempt = 1..max_attempts：
       resp = vlm.generate(parts, extra_messages=correction)
       obj  = adapter.validate_python(extract_json(resp.text))
       validate(obj)   # 业务校验，例如索引越界，失败时抛 ValueError
       成功则返回 (obj, resp, attempt)
    只捕获 (json.JSONDecodeError, pydantic.ValidationError, ValueError)：
       记录 status="retry"，correction = [
         {"role": "assistant", "content": resp.text},
         {"role": "user", "content": f"Your output was invalid: {err}. Return only valid JSON following the format."}]
    全部失败后抛 AgentOutputError。其他异常（网络、鉴权）直接上抛。"""
```

#### `seeker.py`

```python
class Seeker:
    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger): ...
    def run(self, question: str, candidate: list[PageAsset], feedback: str | None,
            out_dir: str) -> tuple[SeekerOutput, list[int], VLMResponse, int]:
        """前置条件：1 <= len(candidate) <= 20（违反即 AssertionError，图路由负责保证）。
        1. 用 candidate 的 thumb 拼网格：concat_grid(..., ARRANGEMENT[n], settings.grid_line_width)，
           网格图保存到 out_dir/seeker_{step}.png
        2. 组装 Prompt：question（含 feedback）+ build_page_map(n, arrangement)
        3. call_with_schema；validate：choice 中每个 i 满足 0 <= i < n，去重后保持原顺序
        4. 返回值中的 list[int] 是选中页的 1-based 物理页码"""
```

#### `inspector.py`

```python
class InspectorView(BaseModel):
    """Inspector 一次输入中的图像顺序表：index -> 来源。"""
    items: list[Union[tuple[Literal["page"], int], tuple[Literal["crop"], str]]]

class Inspector:
    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger): ...
    def build_view(self, pages: list[PageAsset], crops: list[CropAsset]) -> InspectorView:
        """先放 view 页（按页码升序），再放 crop（按 crop_id 创建顺序）；
        总数超过 max_inspector_images 时，先丢最早的 crop，再丢页码最大的页。"""
    def run(self, question: str, view: InspectorView, pages: dict[int, PageAsset],
            crops: dict[str, CropAsset], zoom_left: int) -> tuple[InspectorOutput, VLMResponse, int]:
        """validate：
           answer -> reference 中每个索引 < len(view.items)
           seek   -> choice 中每个索引 < len(view.items)
           zoom   -> image < len(view.items)；zoom_left > 0；bbox 能通过 vlm_bbox_to_norm 换算"""
```

#### `synthesizer.py`

```python
class Synthesizer:
    def run(self, question: str, draft: str | None, view: InspectorView,
            pages: dict[int, PageAsset], crops: dict[str, CropAsset]) -> tuple[SynthesizerOutput, VLMResponse, int]: ...
```

### 5.11 LangGraph 编排（`zoomdoc/graph/`）

#### `state.py`

```python
class AgentState(BaseModel):
    trace_id: str
    question: str
    pdf_path: str
    run_dir: str
    pages: dict[int, PageAsset] = {}
    candidate_pages: list[int] = []          # = ViDoRAG Seeker.buffer_images（候选池）
    inspector_pages: list[int] = []          # = ViDoRAG Inspector.buffer_images（累积页）
    crops: dict[str, CropAsset] = {}
    last_view: InspectorView | None = None
    feedback: str | None = None
    pending_zoom: InspectorZoom | None = None
    draft_answer: str | None = None
    synth_view: InspectorView | None = None
    step: int = 0
    zoom_count: int = 0
    route: Literal["seeker", "inspector", "zoom", "synthesizer", "end"] | None = None
    result: FinalResult | None = None
    history: list[StepRecord] = []
    prompt_tokens: int = 0
    completion_tokens: int = 0
```

所有字段采用覆盖语义（不定义 reducer）。节点返回**完整的新列表或字典**，不在原地修改。

#### `nodes.py`：节点与转移

| 节点 | 动作 | 写入字段 |
|---|---|---|
| `ingest` | `render_pdf`；`candidate_pages = 全部页码` | pages, candidate_pages |
| `seeker` | `Seeker.run(candidate, feedback)`；选中页从 candidate 中删除（`vidorag_agents.py:54`），追加到 inspector_pages 并去重（`:84`）；清空 feedback | candidate_pages, inspector_pages, step+1, history, tokens |
| `inspector` | `build_view` → `run` | last_view, route, pending_zoom / draft_answer / feedback, step+1 |
| `zoom` | 把 `pending_zoom.image` 映射到 view 项：是 page 时 bbox 直接作为页坐标；是 crop 时先做 `crop_box_to_page_box`；然后执行 `zoom()` | crops, zoom_count+1, step+1, pending_zoom=None |
| `synthesizer` | `Synthesizer.run` | result |
| `finalize` | 根据 route 生成 FinalResult，写入 `run_dir/result.json` | result |

**Inspector 之后的路由函数 `route_after_inspector(state) -> str`**（按顺序判定）：

```
1. state.step >= max_steps:
       有 inspector_pages 或 crops → "synthesizer"（draft=None，view=last_view）
       否则 → "finalize"（status="budget_exhausted"）
2. action == "answer":
       len(reference) == len(last_view.items) → "finalize"（status="answered"，answer 直接采用；对应 vidorag_agents.py:118-119）
       否则 → synth_view = 被引用项组成的 view；draft = answer → "synthesizer"（对应 :120-122）
3. action == "zoom" → "zoom"
4. action == "seek":
       inspector_pages = choice 中的 page 项；crops 只保留 page 属于新 inspector_pages 的项（对应 :126）
       feedback = information
       candidate_pages 非空 → "seeker"
       candidate_pages 为空且 inspector_pages 非空 → "synthesizer"（draft=None）
       两者都空 → "finalize"（status="no_answer"）
```

**Seeker 之后的路由 `route_after_seeker`**：

```
本轮没有选中页 且 inspector_pages 为空 → "finalize"（no_answer；对应 vidorag_agents.py:79-80）
本轮没有选中页 且 inspector_pages 非空 → "synthesizer"（对应 :81-82）
其他 → "inspector"
```

**ingest 之后**：页数为 0 → "finalize"（no_answer）；否则 → "seeker"。

#### `builder.py`

```python
def build_graph(vlm: VLMClient, settings: Settings, logger: TraceLogger) -> CompiledGraph:
    g = StateGraph(AgentState)
    g.add_node("ingest", ...); g.add_node("seeker", ...); g.add_node("inspector", ...)
    g.add_node("zoom", ...); g.add_node("synthesizer", ...); g.add_node("finalize", ...)
    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", route_after_ingest, {"seeker": "seeker", "finalize": "finalize"})
    g.add_conditional_edges("seeker", route_after_seeker, {...})
    g.add_conditional_edges("inspector", route_after_inspector, {...})
    g.add_edge("zoom", "inspector")
    g.add_edge("synthesizer", "finalize")
    g.add_edge("finalize", END)
    return g.compile()

def run(question: str, pdf_path: str, vlm: VLMClient | None = None,
        settings: Settings | None = None) -> FinalResult: ...
```

`recursion_limit` 设为 `max_steps * 2 + 10`，防止 LangGraph 的默认上限先于业务预算触发。

```mermaid
stateDiagram-v2
    [*] --> ingest
    ingest --> seeker
    ingest --> finalize: no pages
    seeker --> inspector: selected
    seeker --> synthesizer: none selected, buffer non-empty
    seeker --> finalize: none selected, buffer empty
    inspector --> zoom: action=zoom
    zoom --> inspector
    inspector --> seeker: action=seek, candidates left
    inspector --> synthesizer: answer with partial refs / seek exhausted / step budget
    inspector --> finalize: answer with full refs
    synthesizer --> finalize
    finalize --> [*]
```

### 5.12 测试与验收断言（M1 完成的唯一判据）

全部测试使用 `FakeVLM`，不访问网络。`tests/fake_vlm.py`：

```python
class FakeVLM:
    def __init__(self, scripted: list[str]): ...    # 按调用顺序返回 VLMResponse(text=scripted[i], usage=...)
    calls: list[list[Part]]                          # 记录每次调用的输入，供断言使用
```

| 测试文件 | 断言（必须精确） |
|---|---|
| `test_geometry.py` | ① `vlm_bbox_to_norm((100,200,300,250),"xyxy_1000")` = (0.1,0.2,0.3,0.25)；② `pad_and_clamp(上面结果, 0.1, 0.05)` ≈ (0.08,0.195,0.32,0.255)，误差 1e-9；③ `norm_to_pixel(②, (1700,2200))` == (136,429,544,561)；④ `upsample_size((136,429,544,561),1024,4.0)` == (1024,331)；⑤ `yxyx_1000` 输入 (200,100,250,300) 与 ① 结果相同；⑥ 颠倒顺序的 (300,250,100,200) 被交换为 ①；⑦ (100,100,100,300) 抛 GeometryError；⑧ 框 (0.98,0.5,1.0,0.51) 经 pad_and_clamp 后 x2 ≤ 1 且宽度 ≥ 0.05；⑨ `crop_box_to_page_box((0.5,0.5,1,1), (0.2,0.2,0.4,0.6))` ≈ (0.3,0.4,0.4,0.6)（`pytest.approx(abs=1e-9)`，浮点结果为 0.30000000000000004）。所有浮点比较统一用 approx，像素整数比较用 == |
| `test_grid.py` | n=3、单格 (512,662)、L=40：画布尺寸为 (1064,1364)；点 (256/1064, 1033/1364) 映射到 (i=2, 0.5, 0.5)；Y=680 的点抛 GeometryError（分隔线）；格 (r=1,c=1) 中的点抛 GeometryError（填充格）；跨两格的框抛 GeometryError；1..20 的 ARRANGEMENT 与 `references/ViDoRAG/agent/map_dict.py` 逐项一致（测试运行时用 `ast` 解析该文件对比；文件不存在时 skip） |
| `test_page_map.py` | `build_page_map(1)` 等于 ViDoRAG 原文；n=4 时包含 "The second row, the first one is number 2" 与 "from 0-3!"；n=3 时包含 "The first row"（修正 V7） |
| `test_zoom.py` | 用 PIL 生成 1700×2200 白图，在像素 (200,500)-(400,540) 画红色矩形；以框 (0.1,0.2,0.3,0.25) 执行 zoom；输出尺寸为 (1024,331)；输出图中红色像素（R>200,G<50,B<50）占比 > 0；第二次执行返回同一路径（幂等） |
| `test_json_parser.py` | 能解析包在 ```json 中的输出；能解析前后带说明文字的输出；无花括号时抛 JSONDecodeError；顶层为 list 时抛 ValueError |
| `test_agents.py` | 第 1 次返回非法 JSON、第 2 次合法 → attempts==2，且第 2 次调用带有 correction 消息；连续 3 次非法 → AgentOutputError；Seeker 的 choice 越界 → 触发重试；Inspector 在 zoom_left=0 时返回 zoom → 触发重试 |
| `test_graph_e2e.py` | 用 `scripts/make_synthetic_pdf.py` 生成 4 页 PDF。**场景 Z（主动视觉闭环）**：脚本 = [Seeker 选 [2]，Inspector zoom image 0，Inspector answer reference [0,1]] → status=="answered"，`len(crops)==1`，第 3 次调用的输入含 2 张图且第 2 个标签以 "Image 1: ZOOMED CROP" 开头，trace.jsonl 中出现 node=="zoom"。**场景 S（反馈重选）**：Seeker [0] → Inspector seek choice [] → Seeker（候选池 3 页）[1] → Inspector answer → 第二次 Seeker 的 prompt 含 "## Additional Information"。**场景 P（部分引用）**：answer 的 reference 是 view 的子集 → 调用 synthesizer，status=="synthesized"。**场景 B（预算）**：Inspector 连续返回 zoom，max_zooms=3 → zoom 节点恰好执行 3 次，第 4 次 zoom 请求在校验阶段被拒并重试。**场景 N**：Seeker 返回 [] → status=="no_answer" |

**验收命令（全部 exit code 0）**：

```powershell
.\.venv\Scripts\python -m compileall -q zoomdoc scripts tests
.\.venv\Scripts\python -m pytest -q
```

**人工冒烟（需要真实 API，非 CI 项）**：

```powershell
.\.venv\Scripts\python scripts\make_synthetic_pdf.py --out runs\synthetic.pdf
.\.venv\Scripts\python scripts\run_m1.py --pdf runs\synthetic.pdf --question "What is the serial code printed in the small table at the bottom right of page 3?"
```

`make_synthetic_pdf.py` 规格：A4，4 页；每页有一个 24pt 标题（"Section 1".."Section 4"）和若干 11pt 正文；第 3 页右下角有一个 3×2 小表格，字号 5pt，其中一格内容为 `ZD-7319-QX`。

`[Hypothesis H2]`：真实 VLM 在 view 分辨率（长边 1024）下无法稳定读出 5pt 文本，会选择 zoom。证伪：对冒烟样例重复运行 5 次（temperature=0，seed 固定），记录 zoom 触发次数和答案正确率。如果不 zoom 就能 5/5 答对，说明该样例不足以检验主动视觉，需要把字号降到 4pt 或把 view_long_side 降到 768 后重测。结果写入 `runs/smoke_report.md`。

### 5.13 施工顺序（Flash 必须按序执行，每步完成后在 PROGRESS.md 勾选）

1. 创建 `.venv`，安装依赖，生成 `requirements.lock`。
2. `errors.py` → `config.py` → `schemas.py`。
3. `vision/geometry.py` + `tests/test_geometry.py` → 运行 pytest，通过后再继续。
4. `vision/grid.py` + `test_grid.py` + `test_page_map.py` → 运行 pytest。
5. `vision/render.py`、`vision/zoom.py` + `test_zoom.py` → 运行 pytest。
6. `llm/json_parser.py` + `test_json_parser.py`；`llm/vlm_client.py`。
7. `observability.py`、`prompts.py`、`agents/*` + `fake_vlm.py` + `test_agents.py` → 运行 pytest。
8. `graph/*` + `scripts/make_synthetic_pdf.py` + `test_graph_e2e.py` → 运行 pytest。
9. `scripts/run_m1.py`（argparse：`--pdf`、`--question`；输出 `FinalResult.model_dump_json(indent=2)`）。
10. 执行 §5.12 的验收命令，把完整输出贴到 PROGRESS.md。

### 5.14 Flash 禁止事项

- 不得删除测试断言或放宽测试数值来"让测试通过"。几何测试失败时，应修正实现中的公式。
- 不得引入本蓝图之外的框架（如 LangChain Agents、LlamaIndex）。
- 不得在状态或日志中写入 base64 图像。
- 不得把 `references/` 下的代码 import 进项目（只能移植并注明出处）。
- 测试失败时，先原样贴出 pytest 错误输出和堆栈，再修改代码。

---

## 6. 风险与局限

| 类型 | 风险 | 应对 / 证伪 |
|---|---|---|
| 内部有效性 | VLM 的 bbox 定位误差可能导致裁剪区域偏离目标 | pad_ratio 和 min_side 作为可调参数；M5 中统计 zoom 后答案正确率，与"不允许 zoom"的消融组比较 |
| 内部有效性 | 不同 VLM 的坐标约定不同（0–1000、绝对像素、yxyx） | `bbox_format` 适配器；接入新模型前，用 test_zoom 的红框图做一次真实定位探针 |
| 外部有效性 | M1 只支持 ≤ 20 页、单文档 | 在 M3 用树导航解除该限制（v0.3） |
| 内部有效性 | VLM 不 zoom 时自信给出错误答案（`runs/4ef2be254cef` 观测到 1 例） | M2 Critic 闸门；M2.5 统计"未 zoom 且错误"的比例与 confidence 分布 |
| 进度风险 | 主动视觉前提（H2）只有 N=2 证据，若不成立，M3 之后的工作建立在无效前提上 | M2.5 评测门，判决规则见 §10.4 |
| 结构有效性 | Seeker 在缩略图上可能漏选小字页 | M5 记录 Seeker 召回率（参考页 ∈ 选中页的比例），作为独立指标报告 |
| 成本 | Inspector 多图模式下每张图都有固定 Token 开销 | 设 max_inspector_images 上限；每次运行在 trace 中汇总 prompt_tokens |
| 法律 | ViDoRAG 没有许可证文件 | 不逐字复制其 Prompt 示例；README 中注明借鉴出处 |


---

## 7. Milestone 1 代码审查（2026-11-10，总架构师）

### 7.1 基线观测

| 项 | 观测结果 | 证据 |
|---|---|---|
| 测试基线 | `24 passed in 9.62s`，exit 0 | `.venv\Scripts\python -m pytest -q` |
| 源码编码 | `zoomdoc/**/*.py` 全部为合法 UTF-8 | Python `bytes.decode('utf-8')` 逐文件检查；PowerShell 中的乱码只是控制台代码页问题 |
| 版本控制 | 项目根目录**不是 git 仓库** | `git log` → `fatal: not a git repository` |
| LangGraph 版本 | `langgraph==1.2.20` | `requirements.lock` |

> [!WARNING]
> 项目没有 git，违反"事务性变更需恢复锚点"约束。M2 施工 Step 0 必须先执行 `git init` 并提交 M1 基线。

### 7.2 已落地模块

| 模块 | 文件 | 状态 |
|---|---|---|
| 三级渲染（source 200DPI / view 1024 / thumb 448），页数上限 20 | `vision/render.py:42-49` | 已实现，有测试 |
| BBox 归一化（`xyxy_1000` / `yxyx_1000` / `xyxy_abs` → `NormBBox`），crop 嵌套坐标回推 | `vision/geometry.py`、`graph/nodes.py:484-489` | 已实现，5 个测试 |
| Pillow 局部裁剪 + pad + clamp + 上采样 | `vision/zoom.py` | 已实现，1 个测试 |
| 缩略图网格拼图 + page_map 文本 | `vision/grid.py` | 已实现，5 个测试 |
| JSON 抽取 + Pydantic 判别联合 + 带错误回传的重试（上限 3） | `agents/base.py:634-705` | 已实现 |
| Seeker / Inspector(answer/seek/zoom) / Synthesizer | `agents/*.py` | 已实现 |
| **扁平** LangGraph 状态机（ingest→seeker→inspector⇄zoom→synthesizer→finalize） | `graph/builder.py`、`graph/nodes.py` | 已实现，5 个 e2e 场景 |
| JSONL trace | `observability.py` | 已实现，但有缺陷（见 7.3） |

> [!NOTE]
> 需求描述中的"单脚本 ReAct 循环"与代码不符：M1 已经使用 LangGraph `StateGraph`，只是**单个扁平图 + 单个全局 `AgentState`**。M2 要解决的不是"引入 LangGraph"，而是"拆分状态作用域"。

### 7.3 工程缺陷清单（M2 Step 1 必须修复）

| ID | 位置 | 缺陷 | 影响 | 修复要求 |
|---|---|---|---|---|
| D1 | `agents/base.py:654-705` | 重试失败的那几次调用的 `usage` 被丢弃，只返回最后一次成功调用的 `resp` | `total_prompt_tokens` 系统性偏低；偏差 = 失败次数 × 单次 prompt token | 每次调用的 usage 都写入调用方传入的 `usage_sink` |
| D2 | `graph/nodes.py:332-333` 等 4 处 | `resp.usage.prompt_tokens or 0`：API 不返回 usage 时静默计 0 | Token 统计失真且无法察觉 | 计数器增加 `missing_usage_calls: int`，usage 为 None 时 +1 |
| D3 | `observability.py` `Span` | `__enter__/__exit__` 不计时；`zoom_node` 硬编码 `latency_ms=0.0`（`nodes.py:505`） | 无法统计 zoom 延迟与 P95 | `Span` 在进入时记录 `perf_counter`，提供 `elapsed_ms()` |
| D4 | `observability.py` | trace 无命名空间字段 | 子图化后无法区分事件属于哪一轮子图 | `event()` 增加 `namespace: str = "main"` 关键字参数（向后兼容） |
| D5 | `schemas.py` `StepRecord.output: dict` | 非类型化；`route_after_seeker`（`nodes.py:348-349`）靠 `output.get("choice")` 读取 | 字段改名不会报错，会静默路由到 finalize | M2 新代码禁止从 `history` 反读路由信息，路由只读 state 中的类型化字段 |
| D6 | `graph/state.py` | `crops`、`last_view`、`synth_view`、`pending_zoom` 与 `question`、`result` 同处一个全局 state | 上下文污染：多轮检视时旧 crop 一直累积在 Inspector 输入中（只受 `max_inspector_images=6` 截断） | M2 的核心任务（§8） |
| D7 | `graph/nodes.py` 全文 | `history: state.history + [rec]` 全量复制，无 reducer | O(n²) 复制；与子图合并时容易覆盖 | M2 使用 `Annotated[list, operator.add]` reducer |
| D8 | `graph/nodes.py:462-473` | `route` 字段跨节点残留，`zoom_node` 写入的 `route="inspector"` 从未被读取 | 路由依赖隐式残留值 | M2 每个路由节点写入本轮 `next`，路由函数只读 `next` |
| D9 | `config.py:17` | `vlm_model` 有代码内默认值，违反 §3 第 3 条 | 未配置 `.env` 时静默使用默认模型 | 本里程碑不改（会破坏 M1 测试的构造方式）；登记为 M3 债务 |

异常处理评估：`call_with_schema` 只捕获 `(JSONDecodeError, ValidationError, ValueError)`，网络与超时异常直接上抛，符合 fail-fast。`zoom_node` 用 `assert` 做前置检查，`python -O` 下会失效；M2 新代码改用显式 `raise ZoomDocError`。

### 7.4 为什么扁平图无法支撑多页文档推理

1. **上下文单调增长**：D6 中所有 crop 进入全局 `crops`，每次 Inspector 调用都通过 `build_view` 重新发送全部 view 页 + crop。设每轮检视 k 页、累计 z 个 crop，单次 Inspector 输入图像数为 `min(k+z, 6)`，被截断丢弃的是**最早的 crop**，与"证据是否已被采纳"无关。
2. **无证据压缩**：Inspector 得到的结论只以 `draft_answer: str` 保存，没有结构化证据。Seeker 重选页之后，上一轮检视的推理只剩一句 `feedback`。
3. **无质量闸门**：`InspectorAnswer` 只要引用全部图像就直接 `answered`（`nodes.py:405`），没有置信度，也没有独立校验节点可以打回重裁。
4. **预算是全局单一计数**：`max_steps=8` 由 Seeker、Inspector、Zoom 共用，一次深度 zoom 会耗尽后续选页的预算，无法按"轮"分配。

结论：M2 将"视觉细查"封装成一次性子图调用。子图内部可以使用任意多的高分辨率图像，退出时只交回一个文本 `EvidenceReport`。主图上下文的大小与 zoom 次数无关，只随检视轮数线性增长。

---

## 8. Milestone 2 架构契约

### 8.1 设计决策（帕累托对比）

| 决策 | 选项 A | 选项 B | 选择与理由 |
|---|---|---|---|
| 子图接入方式 | `add_node("x", compiled_subgraph)`（共享 state key） | 在包装节点函数内 `subgraph.invoke(mapped_input)` | **B**：父子 schema 完全不同，输入/输出映射在一个函数里显式写出，隔离可被单测断言。代价：子图内部步骤不出现在父图 stream 中，由 trace 的 `namespace` 弥补 |
| Critic 实现 | VLM 评审 | 确定性规则 | **B**：Critic 只接触文本报告，看不到图像，VLM 评审只能复读 Inspector 的结论；规则可以用 FakeVLM 做二值测试。代价：依赖 Inspector 自报的置信度（见 H3） |
| Synthesizer 输入 | 图像（M1 方式） | 只用 `EvidenceReport` 文本 | **B**：保持主图纯文本。代价：Synthesizer 无法自行复核像素 |
| crop 文件 | 子图结束后删除 | 保留在磁盘、不进入主 state | 默认保留（`keep_sub_artifacts=True`，供 trace 审计）；设为 False 时删除 `sub_dir` |
| M1 代码 | 原地改造 | 新建 M2 图，M1 图保持不变 | **新建**：M1 的 24 个测试作为回归基线不被触碰 |

`[Hypothesis H3]`：VLM 自报的 `confidence` 与答案正确率正相关。证伪路径：M5 中（N ≥ 50，temperature=0，固定 seed）按 confidence 分 5 桶统计准确率；如果 Spearman ρ ≤ 0.2 或高置信桶准确率不高于低置信桶，就把 Critic 的置信度规则替换为"二次独立检视的答案一致性"。

> [!IMPORTANT]
> 路径冲突：需求建议的 `schemas/states.py` 会与现有模块 `zoomdoc/schemas.py` 同名冲突（包与模块不能同名共存）。M2 统一放在 `zoomdoc/graph/` 下：`states.py`、`supervisor.py`、`inspector_subgraph.py`、`critic.py`。`zoomdoc/agents/` 继续只放"一次 VLM 调用"的封装。

### 8.2 状态定义

```python
# zoomdoc/graph/states.py （签名级契约，Flash 按此实现）
class UsageTotals(BaseModel):          # 可加；reducer = merge_usage
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    missing_usage_calls: int = 0
    latency_ms: float = 0.0

class CitedRegion(BaseModel):          # 只有坐标，没有路径
    page: int = Field(ge=1)
    page_box: NormBBox | None          # None 表示整页引用
    target: str | None

class EvidenceReport(BaseModel):       # 子图 → 主图唯一出口；extra="forbid"
    round: int = Field(ge=1)
    status: Literal["answered", "need_more_pages", "budget_exhausted"]
    answer: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str
    seek_hint: str | None              # Inspector seek 分支的 information
    reference_pages: list[int]
    cited_regions: list[CitedRegion]
    zoom_count: int
    sub_steps: int
    usage: UsageTotals
    # model_validator：递归检查所有 str 字段，含 "data:image" / "base64," / "crops" 路径分隔片段则 raise ValueError

class CriticVerdict(BaseModel):
    accepted: bool
    reason_code: Literal["ok", "low_confidence", "no_answer", "need_more_pages",
                         "budget_exhausted", "invalid_reference"]
    detail: str

class SupervisorState(BaseModel):      # 主图，全文本 + 页资产路径
    trace_id: str; question: str; pdf_path: str; run_dir: str
    pages: dict[int, PageAsset] = {}               # 只含路径字符串，Seeker 需要 thumb
    candidate_pages: list[int] = []                # 粗筛候选池（Seeker 选中即移除）
    selected_pages: list[int] = []                 # 本轮交给子图的页
    feedback: str | None = None                    # 传给 Seeker 或子图的文本反馈
    evidence: Annotated[list[EvidenceReport], operator.add] = []
    verdict: CriticVerdict | None = None
    seek_rounds: int = 0
    inspect_rounds: int = 0
    step: int = 0                                  # 主图节点步数（子图内部步数不计入）
    next: Literal["seeker", "inspector", "critic", "synthesizer", "finalize"] | None = None
    final_answer: str | None = None
    result: FinalResultM2 | None = None
    usage: Annotated[UsageTotals, merge_usage] = UsageTotals()
    history: Annotated[list[StepRecord], operator.add] = []   # 只记录主图节点

class InspectorSubState(BaseModel):    # 子图私有，生命周期 = 一次 invoke
    trace_id: str; question: str; round: int; sub_dir: str
    pages: dict[int, PageAsset]                    # 只含 selected_pages
    feedback: str | None
    crops: dict[str, CropAsset] = {}               # ROI 高清裁剪（仅子图内）
    view: InspectorView | None = None              # 本次送入 VLM 的图像序列
    pending_zoom: InspectorZoom | None = None
    last_output: InspectorOutputM2 | None = None
    sub_step: int = 0
    zoom_count: int = 0
    max_zooms: int; max_sub_steps: int
    usage: UsageTotals = UsageTotals()
    report: EvidenceReport | None = None           # 唯一被映射回主图的字段
```

### 8.3 主图拓扑

```mermaid
flowchart TD
    S((START)) --> ING["ingest: render_pdf"]
    ING --> SUP{"supervisor: decide_next"}
    SUP -->|"next=seeker"| SEEK["seeker: thumb grid, select pages"]
    SEEK --> SUP
    SUP -->|"next=inspector"| INSP["inspector_subgraph_node: map in, invoke, map out"]
    INSP --> CRI["critic: rule check on last EvidenceReport"]
    CRI --> SUP
    SUP -->|"next=synthesizer"| SYN["synthesizer_text: evidence text only"]
    SUP -->|"next=finalize"| FIN["finalize: write result.json"]
    SYN --> FIN
    FIN --> E((END))
```

`decide_next(state, settings)` 是纯函数，按以下顺序取第一条命中的规则：

| # | 条件 | next | 写入的副作用字段 |
|---|---|---|---|
| R0 | `state.result is not None` | finalize | — |
| R1 | `state.step >= max_steps` | 有 answered 证据 → synthesizer，否则 finalize | — |
| R2 | `verdict is None and not evidence and not selected_pages` | 候选非空 → seeker，否则 finalize | — |
| R3 | 上一节点为 seeker（`selected_pages` 非空且未检视） | inspector | `inspect_rounds += 1` |
| R4 | 上一节点为 seeker 且 `selected_pages` 为空 | 有 answered 证据 → synthesizer，否则 finalize | — |
| R5 | `verdict.accepted` | finalize | `final_answer = evidence[-1].answer` |
| R6 | `reason_code ∈ {low_confidence, invalid_reference, budget_exhausted}` 且 `inspect_rounds < max_inspect_rounds` | inspector（同一批页，重新开子图） | `feedback = verdict.detail`，`inspect_rounds += 1` |
| R7 | `reason_code == need_more_pages` 且 `candidate_pages` 非空 且 `seek_rounds < max_seek_rounds` | seeker | `feedback = evidence[-1].seek_hint` |
| R8 | 其余情况 | 有 answered 证据 → synthesizer，否则 finalize | — |

"未检视"判定：`supervisor` 节点用 `state.history[-1].node` 判断上一个主图节点（`StepRecord.node` 的 Literal 扩展 `"critic"`）。这是唯一允许读 history 的地方，且只读 `node` 这个类型化字段。

### 8.4 子图拓扑

```mermaid
flowchart TD
    S((START)) --> P["prepare: build view from pages + crops"]
    P --> I["inspect: VLM call, InspectorOutputM2"]
    I --> R{"route_inspect"}
    R -->|"zoom and zoom_count < max_zooms and sub_step < max_sub_steps"| Z["zoom: crop source image"]
    Z --> P
    R -->|"answer"| PK["pack_report: status=answered"]
    R -->|"seek"| PK2["pack_report: status=need_more_pages"]
    R -->|"sub_step >= max_sub_steps"| PK3["pack_report: status=budget_exhausted"]
    PK --> E((END))
    PK2 --> E
    PK3 --> E
```

- `sub_step`：每次 `inspect` 与 `zoom` 各 +1。
- `zoom_left = max_zooms - zoom_count` 传给 Prompt；为 0 时 `validate` 拒绝 zoom（沿用 M1 `inspector.py:818`）。
- 预算判断在 `route_inspect` 中先于 action 分支执行。

### 8.5 上下文隔离契约

```mermaid
flowchart LR
    subgraph Parent["SupervisorState (text + page asset paths)"]
        A1["question, selected_pages, pages[selected], feedback, round"]
        A2["evidence += [report]; usage += report.usage"]
    end
    subgraph Child["InspectorSubState (private, discarded after invoke)"]
        B1["crops: CropAsset paths"]
        B2["view, pending_zoom, last_output"]
        B3["sub_step, zoom_count"]
        B4["report: EvidenceReport"]
    end
    A1 -->|"map_in"| Child
    B4 -->|"map_out: only report"| A2
```

| 数据 | 存在位置 | 退出子图后 |
|---|---|---|
| base64 图像 | 只在 `OpenAICompatVLM.generate` 局部变量 `content_items` 中 | 函数返回即释放；任何 state 中都不出现（M1 已满足） |
| ROI 裁剪 `CropAsset`（路径、pixel_box） | `InspectorSubState.crops` | 随子图 state 丢弃；只把 `page` + `page_box` + `target` 转成 `CitedRegion` 留下 |
| `InspectorView`、`pending_zoom`、`last_output` | 子图 state | 丢弃 |
| 子图 `StepRecord` | 只写 trace（`namespace="inspector_sub/r{round}"`） | 不进入主图 `history` |
| crop PNG 文件 | `run_dir/sub/r{round}/crops/` | `keep_sub_artifacts=False` 时 `shutil.rmtree` |
| Token 计数 | `InspectorSubState.usage` | 折叠进 `EvidenceReport.usage`，再由主图 reducer 累加 |

包装节点 `inspector_subgraph_node` 的返回值只允许包含这几个 key：`{"evidence", "usage", "history", "step", "next"}`，由测试断言。

### 8.6 预算参数（新增到 `Settings` 与 `.env.example`）

| 参数 | 默认 | 含义 |
|---|---|---|
| `max_inspect_rounds` | 2 | 子图被调用的总次数上限（含重裁） |
| `max_seek_rounds` | 2 | Seeker 被调用的总次数上限 |
| `max_sub_steps` | 6 | 单次子图内 inspect + zoom 步数上限 |
| `critic_min_confidence` | 0.6 | 低于此值判 `low_confidence` |
| `keep_sub_artifacts` | true | 是否保留子图 crop 文件 |

主图递归上限：`recursion_limit = 4 * (max_seek_rounds + max_inspect_rounds) + 10`；子图：`recursion_limit = 2 * max_sub_steps + 6`。

---

## 9. Milestone 2 施工指导手册（读者：Gemini 3.8 Flash）

> [!IMPORTANT]
> 执行规则：按 §9.5 顺序施工；每一步跑完 pytest 再进入下一步；失败时先把完整报错原样贴进 PROGRESS.md，再改代码。不得修改任何 M1 测试文件，也不得修改 `graph/builder.py`、`graph/nodes.py`、`graph/state.py` 的行为。

### 9.1 文件清单

| 动作 | 文件 | 内容 |
|---|---|---|
| 修改 | `zoomdoc/agents/base.py` | D1：`call_with_schema` 增加 `usage_sink: list[VLMUsage] \| None = None`，每次 `vlm.generate` 后 append |
| 修改 | `zoomdoc/agents/inspector.py` | `Inspector.run` 增加 `usage_sink`、`prompt_template`、`adapter` 关键字参数，默认值保持 M1 行为 |
| 修改 | `zoomdoc/agents/seeker.py`、`synthesizer.py` | `run` 增加 `usage_sink` 关键字参数并透传 |
| 修改 | `zoomdoc/observability.py` | D3 + D4 |
| 修改 | `zoomdoc/schemas.py` | 新增 `InspectorAnswerM2`、`InspectorOutputM2`、`FinalResultM2`；`StepRecord.node` 的 Literal 增加 `"critic"`, `"supervisor"`, `"ingest"`, `"pack_report"` |
| 修改 | `zoomdoc/config.py`、`.env.example` | §8.6 五个参数 |
| 修改 | `zoomdoc/prompts.py` | 新增 `INSPECTOR_PROMPT_M2`、`SYNTHESIZER_TEXT_PROMPT`、`render_evidence_block()` |
| 新增 | `zoomdoc/graph/states.py` | §8.2 全部模型 + `merge_usage` + `usage_from_sink` |
| 新增 | `zoomdoc/graph/inspector_subgraph.py` | 子图节点、路由、`build_inspector_subgraph`、`run_inspector_round` |
| 新增 | `zoomdoc/graph/critic.py` | `critic_node`、`evaluate_report` |
| 新增 | `zoomdoc/graph/supervisor.py` | `decide_next`、主图节点、`build_supervisor_graph`、`run_m2` |
| 新增 | `scripts/run_m2.py` | CLI：`--pdf --question`，输出 `FinalResultM2` JSON |
| 新增 | `tests/test_langgraph_probe.py` | Pydantic state + Annotated reducer 行为探针 |
| 新增 | `tests/test_m2_units.py` | `decide_next` 真值表、`evaluate_report`、`EvidenceReport` 防泄漏校验、`merge_usage` |
| 新增 | `tests/test_milestone2.py` | 端到端验收（§9.6） |

### 9.2 基础层修改规格

**`agents/base.py`**
```python
def call_with_schema(vlm, parts, adapter, *, agent, max_attempts, logger,
                     validate=None, usage_sink: list[VLMUsage] | None = None,
                     namespace: str = "main") -> tuple[T, VLMResponse, int]:
    # 在 resp = vlm.generate(...) 的下一行：
    #   if usage_sink is not None: usage_sink.append(resp.usage)
    # logger.event(...) 调用全部透传 namespace
    # 其余逻辑与 M1 完全一致
```

**`observability.py`**
- `Span.__enter__` 记录 `self._t0 = time.perf_counter()`；新增 `elapsed_ms() -> float`。
- `TraceLogger.event(..., namespace: str = "main")`，写入 record 的 `"namespace"` 字段。
- `event()` 在写入前检查 `json.dumps(record)` 是否包含 `"data:image"`，包含则 `raise ZoomDocError("base64 image leaked into trace")`。

**`schemas.py`**
```python
class InspectorAnswerM2(InspectorAnswer):
    confidence: float = Field(ge=0.0, le=1.0)

InspectorOutputM2 = Annotated[Union[InspectorAnswerM2, InspectorSeek, InspectorZoom],
                              Field(discriminator="action")]

class FinalResultM2(BaseModel):
    status: Literal["answered", "synthesized", "no_answer", "budget_exhausted"]
    answer: str | None
    reason: str | None
    reference_pages: list[int]
    cited_regions: list["CitedRegion"]   # 从 graph.states 导入时注意循环引用：CitedRegion 定义在 schemas.py，states.py 导入它
    inspect_rounds: int
    seek_rounds: int
    usage: "UsageTotals"                 # 同上，UsageTotals 也定义在 schemas.py
    trace_id: str
```
为避免循环导入：`UsageTotals`、`CitedRegion` 定义在 `schemas.py`，`graph/states.py` 从 `schemas.py` 导入。

**`prompts.py`**
- `INSPECTOR_PROMPT_M2`：复制 `INSPECTOR_PROMPT_TEMPLATE`，只在 answer 分支 JSON 示例中增加 `"confidence": <float 0-1, your probability that the answer is correct>`；新增占位符 `{feedback}`，为 None 时渲染为空字符串。
- `SYNTHESIZER_TEXT_PROMPT`：输入 `{question}` + `{evidence_block}`，输出 JSON `{"reason", "answer", "reference_pages": [int]}`。
- `render_evidence_block(reports: list[EvidenceReport]) -> str`：每条输出 `[Round r] status=..., confidence=..., pages=[...], answer=..., reason=...`；不得输出任何路径。

### 9.3 新文件规格

#### `zoomdoc/graph/states.py`

```python
def merge_usage(a: UsageTotals, b: UsageTotals) -> UsageTotals:
    """Reducer：逐字段相加。必须是纯函数，不修改入参。"""

def usage_from_sink(sink: list[VLMUsage]) -> UsageTotals:
    """calls=len(sink)；token 为 None 的条目 missing_usage_calls += 1，token 计 0；latency 求和。"""
```
其余模型按 §8.2。所有模型设 `model_config = ConfigDict(extra="forbid")`。

`EvidenceReport` 的 `model_validator(mode="after")`：调用 `_assert_no_image_payload(self.model_dump())`，递归遍历 dict/list/str，命中 `"data:image"`、`"base64,"`、`".png"`、`".jpg"` 任一子串即 `raise ValueError(f"image payload leaked in field {path}")`。

#### `zoomdoc/graph/inspector_subgraph.py`

```python
def prepare_node(state: InspectorSubState, settings: Settings) -> dict:
    """用 Inspector.build_view(pages, crops) 构造 view。返回 {"view": view}。"""

def inspect_node(state: InspectorSubState, vlm: VLMClient, settings: Settings,
                 logger: TraceLogger) -> dict:
    """调用 Inspector.run(prompt_template=INSPECTOR_PROMPT_M2, adapter=TypeAdapter(InspectorOutputM2),
    usage_sink=sink, namespace=f"inspector_sub/r{state.round}", feedback=state.feedback)。
    返回 {"last_output", "pending_zoom"(仅 zoom 分支), "sub_step": +1, "usage": state.usage 与 usage_from_sink(sink) 合并}。
    注意子图 state 不使用 reducer，usage 在节点内显式合并。"""

def route_inspect(state: InspectorSubState) -> Literal["zoom", "pack_report"]:
    """顺序：sub_step >= max_sub_steps → pack_report；
    last_output 为 InspectorZoom 且 zoom_count < max_zooms → zoom；其余 → pack_report。"""

def zoom_node(state: InspectorSubState, settings: Settings, logger: TraceLogger) -> dict:
    """坐标换算逻辑照搬 graph/nodes.py:481-489（page 与 crop 两种目标）。
    out_dir = state.sub_dir；crop_id = f"r{state.round}c{state.zoom_count}"。
    用 Span.elapsed_ms() 记录真实延迟。前置条件不满足时 raise ZoomDocError，不使用 assert。
    返回 {"crops": 新 dict, "zoom_count": +1, "sub_step": +1, "pending_zoom": None}。"""

def pack_report_node(state: InspectorSubState) -> dict:
    """根据 last_output 与预算构造 EvidenceReport：
    - sub_step >= max_sub_steps 且 last_output 不是 answer → status="budget_exhausted"，answer=None
    - InspectorAnswerM2 → status="answered"；reference 索引经 view.items 映射为页码与 CitedRegion
      （page 项 → page_box=None；crop 项 → crops[id].page_box、target）
    - InspectorSeek → status="need_more_pages"，seek_hint=information
    返回 {"report": report}。"""

def build_inspector_subgraph(vlm, settings, logger) -> CompiledStateGraph:
    """START→prepare→inspect→(route_inspect)→zoom→prepare | pack_report→END。"""

def run_inspector_round(*, question: str, round_: int, pages: dict[int, PageAsset],
                        feedback: str | None, run_dir: str, trace_id: str,
                        vlm, settings, logger) -> EvidenceReport:
    """1. sub_dir = run_dir/sub/r{round_}，mkdir。
    2. 构造 InspectorSubState，invoke（recursion_limit 见 §8.6）。
    3. 从输出 dict 中只取 "report"；为 None 则 raise ZoomDocError。
    4. keep_sub_artifacts=False 时删除 sub_dir。
    5. 返回 report。子图 state 的其余字段在此函数返回后不可达。"""
```

#### `zoomdoc/graph/critic.py`

```python
def evaluate_report(report: EvidenceReport, valid_pages: set[int],
                    min_conf: float) -> CriticVerdict:
    """纯函数，按顺序：
    status=budget_exhausted → (False, "budget_exhausted")
    status=need_more_pages  → (False, "need_more_pages")
    answer 为空或纯空白       → (False, "no_answer")
    reference_pages 为空或不是 valid_pages 子集 → (False, "invalid_reference")
    confidence is None 或 < min_conf → (False, "low_confidence")，detail 写明
        f"confidence={c:.2f} < {min_conf}; re-inspect and zoom on the region supporting: {answer}"
    否则 → (True, "ok")"""

def critic_node(state: SupervisorState, settings: Settings, logger: TraceLogger) -> dict:
    """对 state.evidence[-1] 调用 evaluate_report（valid_pages = state.selected_pages）。
    返回 {"verdict", "step": +1, "history": [StepRecord(node="critic", ...)]}，写 trace。"""
```

#### `zoomdoc/graph/supervisor.py`

```python
def ingest_node(state: SupervisorState, settings) -> dict
    # 与 M1 相同：render_pdf → pages、candidate_pages；history 追加 node="ingest"

def supervisor_node(state: SupervisorState, settings) -> dict
    # 调用 decide_next，返回 {"next": ..., 以及 §8.3 表中的副作用字段}；不计 step

def decide_next(state: SupervisorState, settings) -> tuple[NextNode, dict]
    # 纯函数，实现 §8.3 R0–R8；返回 (next, 副作用 updates)

def route_from_supervisor(state: SupervisorState) -> str
    # return state.next；为 None 时 raise ZoomDocError

def seeker_node(state, vlm, settings, logger) -> dict
    # 复用 agents.Seeker；selected_pages = 选中页；candidate_pages 移除选中页；
    # seek_rounds += 1；feedback 置 None；usage 通过 usage_sink 计量

def inspector_subgraph_node(state, vlm, settings, logger) -> dict
    # report = run_inspector_round(question, round_=state.inspect_rounds,
    #          pages={p: state.pages[p] for p in state.selected_pages}, feedback=state.feedback, ...)
    # return {"evidence": [report], "usage": report.usage, "step": state.step + 1,
    #         "history": [StepRecord(node="inspector", input_images=[], output=report.model_dump(), usage=None, attempts=1)],
    #         "next": None}
    # 返回 dict 的 key 集合必须恰好等于这 5 个

def synthesizer_text_node(state, vlm, settings, logger) -> dict
    # 只发 TextPart；从 answered 的 evidence 生成 FinalResultM2(status="synthesized")

def finalize_node(state, settings) -> dict
    # result 已存在则直接落盘；verdict.accepted → status="answered"，
    # 由 evidence[-1] 构造；否则 status = "budget_exhausted"（任一 report 为 budget_exhausted 或 step>=max_steps）或 "no_answer"
    # 写 run_dir/result.json；同时写 run_dir/supervisor_state.json = state.model_dump_json(indent=2)（供验收检查）

def build_supervisor_graph(vlm, settings, logger) -> CompiledStateGraph
def run_m2(question, pdf_path, vlm=None, settings=None, trace_id=None) -> tuple[FinalResultM2, SupervisorState]
    # 第二个返回值是 SupervisorState.model_validate(final_output)，供测试检查隔离
```

### 9.4 编码约束

1. 所有新函数带完整类型注解与 docstring（说明前置条件、返回 key、异常）。
2. 禁止 `assert` 作为运行时检查；禁止 `except Exception`；禁止 `print`。
3. 任何 state 字段不得保存 bytes、PIL 对象或 base64 字符串。
4. 主图节点返回 reducer 字段（`evidence`、`history`、`usage`）时只返回**增量**，不要返回 `state.history + [...]`。

### 9.5 施工顺序

| Step | 内容 | 退出条件 |
|---|---|---|
| 0 | `git init`；`.gitignore` 加入 `.venv/ runs/ .env __pycache__/ .pytest_cache/ *.egg-info/`；提交 M1 基线 | `git log --oneline` 有 1 条提交 |
| 1 | `tests/test_langgraph_probe.py`：验证 langgraph 1.2.20 中 Pydantic `BaseModel` state 的 `Annotated[list, operator.add]` 与自定义 reducer 按增量合并；验证在节点函数中 invoke 另一个编译图可行 | 探针通过；**若失败，停止并上报，不得自行改用 TypedDict** |
| 2 | §9.2 基础层修改（base / observability / schemas / config / prompts / agents 透传） | M1 原有 24 个测试全部通过 |
| 3 | `graph/states.py` + `tests/test_m2_units.py` 中 reducer 与防泄漏测试 | pytest 通过 |
| 4 | `graph/critic.py` + `evaluate_report` 真值表测试（6 条分支各 1 例） | pytest 通过 |
| 5 | `graph/inspector_subgraph.py` + 子图单独测试（直接调用 `run_inspector_round`） | pytest 通过 |
| 6 | `graph/supervisor.py` + `decide_next` 真值表测试（R0–R8 各 1 例） | pytest 通过 |
| 7 | `tests/test_milestone2.py` 端到端验收 | §9.6 全部通过 |
| 8 | `scripts/run_m2.py`；`python -m compileall -q zoomdoc scripts tests`；`python -m pytest -q` | exit 0，输出原样写入 PROGRESS.md 的 M2 小节；`git commit` |

### 9.6 验收标准（`tests/test_milestone2.py`）

全部使用 `tests/fake_vlm.py` 与 `scripts/make_synthetic_pdf.py` 生成的 4 页 PDF；`FakeVLM` 每次调用返回 `prompt_tokens=100, completion_tokens=50`。

**A1 隔离主用例（两次裁剪）**

脚本：Seeker `choice=[2]` → Inspector zoom（image 0，整页）→ Inspector zoom（image 1，在 crop 上嵌套）→ Inspector answer（`confidence=0.9`，`reference=[0,2]`）。

断言：
1. `result.status == "answered"`，`result.answer == "ZD-7319-QX"`，`3 in result.reference_pages`。
2. `fake_vlm.call_count == 4`；`result.usage.calls == 4`，`prompt_tokens == 400`，`completion_tokens == 200`。
3. `evidence[-1].zoom_count == 2`；trace.jsonl 中 `node=="zoom"` 且 `namespace=="inspector_sub/r1"` 的事件恰好 2 条，`latency_ms > 0`。
4. 第 4 次 VLM 调用的 `ImagePart` 数量为 3（1 页 + 2 crop）——证明子图内部确实看到了高清图。
5. 隔离：`dump = state.model_dump_json()`，以及磁盘上的 `supervisor_state.json`：
   - `dump` 不含子串 `data:image`、`base64,`；
   - `"/sub/" not in dump.replace("\\\\", "/")`（子图目录下的任何路径都不出现在主 state 中）；
   - `set(SupervisorState.model_fields) ∩ {"crops", "view", "pending_zoom", "last_output", "sub_step", "zoom_count"} == ∅`；
   - `result.cited_regions` 中至少 1 项 `page_box is not None`（crop 坐标以纯数值形式保留）。
6. 主图 `history` 的 `node` 序列等于 `["ingest", "seeker", "inspector", "critic"]`（子图内部步骤不进入主图）。

**A2 置信度不足打回重裁**

脚本：Seeker `[2]` → 子图 r1：answer `confidence=0.3` → Critic 拒绝（`low_confidence`）→ 子图 r2：zoom → answer `confidence=0.85`。

断言：`state.inspect_rounds == 2`；`len(state.evidence) == 2`；r2 的第一次 Inspector 调用中 `ImagePart` 数量 == 1（r1 的上下文没有带入 r2）且某个 `TextPart` 包含 `"confidence=0.30"`（Critic 反馈被注入）；`result.status == "answered"`。

**A3 子图步数耗尽强制退出**

设置 `max_sub_steps=3, max_zooms=5, max_inspect_rounds=1`。脚本：Seeker `[2]` → zoom → zoom（此时 sub_step=4 ≥ 3）。

断言：`evidence[-1].status == "budget_exhausted"`；`verdict.reason_code == "budget_exhausted"`；`result.status == "budget_exhausted"`；`fake_vlm.call_count == 3`（不再调用 VLM）。

**A4 seek → 回到 Seeker**

脚本：Seeker `[0]` → 子图 seek（`information="look for the table page"`）→ Seeker（候选池为剩余 3 页，prompt 含该 information）选 `[1]`（对应原第 3 页）→ answer `confidence=0.9`。

断言：`seek_rounds == 2`；第二次 Seeker 调用的 grid 只包含 3 页；最终 answered。

**A5 重试 Token 计量（D1 回归）**

脚本中 Inspector 第一次返回非法 JSON `"not json"`，第二次返回合法 answer。断言 `result.usage.calls == fake_vlm.call_count`，`prompt_tokens == 100 * fake_vlm.call_count`。

**A6 防泄漏校验**

`EvidenceReport(..., reason="see runs/x/sub/r1/crops/c0.png")` 和 `answer="data:image/png;base64,AAA"` 都必须抛 `pydantic.ValidationError`。

**A7 回归**

`python -m pytest -q` 全量通过，测试数 ≥ 24 + M2 新增数，M1 测试文件未被修改（Step 8 用 `git diff --stat HEAD~1 -- tests/test_graph_e2e.py tests/test_agents.py` 输出为空来验证）。

### 9.7 禁止事项（在 §5.14 基础上追加）

- 不得为了让 A1 第 5 条通过而把 crop 路径改名绕过子串检查；隔离必须通过 state schema 实现。
- 不得在 `SupervisorState` 中新增任何保存 `CropAsset`、`InspectorView` 或图像路径列表的字段。
- 不得把子图以 `add_node("inspector", compiled_subgraph)` 的方式直接挂载（父子 key 会合并，破坏隔离）。
- Step 1 探针失败、或 langgraph 行为与本手册描述不一致时，停止施工并上报原始输出，不得自行改变架构。

### 9.8 M2 局限

| 类型 | 局限 |
|---|---|
| 内部有效性 | Critic 依赖自报置信度（H3 未验证）；FakeVLM 测试只验证控制流，不验证 VLM 真实定位能力 |
| 外部有效性 | 仍受 M1 的 20 页上限约束（树索引在 M3 解决） |
| 结构有效性 | A1 第 5 条的子串检查只能证明"已知泄漏模式不存在"，不能证明不存在其他形式的图像引用；schema 字段集合断言作为主判据 |
| 成本 | 重裁（R6）会重新发送整页 view，单轮成本约等于一次完整子图；由 `max_inspect_rounds` 限制上限 |


---

## 10. Milestone 2.5 评测门规格（真实 VLM 小规模消融）

> 前置条件：M2 的 §9.6 全部通过。M2.5 不修改 `zoomdoc/` 下任何业务逻辑，只新增评测脚本与数据生成器。

### 10.1 目标

用真实 VLM 回答三个问题，结果决定是否进入 M3：
1. Q1（H2）：主动 zoom 是否提高小字读取准确率？
2. Q2（H3）：Inspector 自报的 `confidence` 是否与正确性相关？Critic 阈值 0.6 是否有效？
3. Q3（成本）：zoom 与 Critic 回环带来的 Token 与延迟增量是多少？

### 10.2 数据集（`scripts/make_eval_set.py`）

- 生成 N = 24 个 4 页合成 PDF，种子固定 `seed=20261110`，输出 `eval/m25/manifest.jsonl`，每行：`{"case_id", "pdf_path", "question", "gold", "gold_page", "font_pt", "region", "confusable"}`。
- 因子设计（全因子 2×3×2 = 12，每格 2 个样本）：
  - `font_pt ∈ {4, 6}`；
  - `region ∈ {top_left, center, bottom_right}`；
  - `confusable ∈ {true, false}`：true 时 gold 中包含易混字符对（`Q/D`、`O/0`、`1/l`、`5/S`、`8/B`），false 时只用无歧义字符。
- 每个 PDF 只有 `gold_page` 页含目标字段，其余页放置格式相同、内容不同的干扰字段（防止只靠"页面上唯一的表格"作答）。
- gold 字符串格式 `ZD-\d{4}-[A-Z]{2}`，由种子随机生成，不复用 M1 的 `ZD-7319-QX`（M1 字符串已出现在 prompt 调试中，存在泄漏风险）。

### 10.3 配置组（同一模型快照、temperature=0、seed=42、相同 Prompt 模板）

| 组 | 运行入口 | 设置 |
|---|---|---|
| G0 no-zoom | `run_m2` | `max_zooms=0`，`max_inspect_rounds=1`（Critic 判决只记录，不触发重裁） |
| G1 zoom | `run_m2` | `max_zooms=3`，`max_inspect_rounds=1` |
| G2 zoom+critic | `run_m2` | `max_zooms=3`，`max_inspect_rounds=2`，`critic_min_confidence=0.6` |

每组 24 次，总计 72 次运行。运行前在报告头部记录：`ZOOMDOC_VLM_MODEL` 确切快照名、`requirements.lock` 的 sha256、开始/结束时间。

`[UNVERIFIED]` G0 中 `max_zooms=0` 时 Prompt 显示 `zoom_left=0`，validate 会拒绝 zoom 输出并触发重试；需在实施前用 1 个样本确认不会因重试耗尽抛 `AgentOutputError`。若会，把 G0 的 Prompt 中 zoom 分支整段移除（新增 `INSPECTOR_PROMPT_M2_NOZOOM`），并在报告中注明 G0 与 G1 的 Prompt 差异。

### 10.4 指标与判决规则（`scripts/eval_m25.py`）

判分：`normalize(answer) == normalize(gold)`，`normalize` = 去空白、转大写、去首尾标点。不使用 LLM 判分。

每组输出：准确率（附 95% Wilson 区间）、zoom 触发率、平均 `prompt_tokens`/`completion_tokens`、单次运行延迟 P50/P95、`missing_usage_calls` 总数、异常运行数（含堆栈摘要）。

额外输出：
- 配对比较：G1 vs G0、G2 vs G1 的 McNemar 精确检验 p 值（同一 case 配对）；
- confidence 分析（G1、G2 中 answered 的样本）：按 confidence 分桶 `[0,0.6)`、`[0.6,0.8)`、`[0.8,1]` 的准确率，及 confidence 与正确性的点二列相关系数；
- 失败样例集 `eval/m25/bad_cases.jsonl`：所有错误样本的 case_id、组、答案、gold、是否 zoom、confidence、trace_id。

判决（写入 `eval/m25/report.md` 首行，取第一条命中）：

| 结果 | 条件 | 后续动作 |
|---|---|---|
| FAIL-H2 | G1 准确率 − G0 准确率 < 10 个百分点，或 McNemar p ≥ 0.05 | 停止进入 M3；回到设计评审（检查 view 分辨率、bbox 格式适配、zoom 参数） |
| PASS-WEAK-CRITIC | H2 通过，但 G2 − G1 < 5 个百分点，或高置信桶准确率 ≤ 低置信桶 | 进入 M3；Critic 置信度规则标记为待替换（H3 证伪路径） |
| PASS | 其余 | 进入 M3 |

> [!NOTE]
> 统计功效局限：N=24 时，McNemar 只能检出约 25 个百分点以上的差异（`[Hypothesis]`，基于不一致对数估计，未做正式功效分析）。FAIL-H2 不能证明"zoom 无效"，只表示"在 N=24 的合成集上没有观察到可检出的提升"。M4 用更大的真实数据集复核。

### 10.5 成本预算

按 `runs/57ad6979395a` 单次运行 4389 + 1626 ≈ 6.0k token 估算，72 次 ≈ 43 万 token（G2 重裁会上浮，按 1.3 倍上限 ≈ 56 万）。延迟按每次运行约 4 次调用 × 15 s 估算，串行约 72 分钟。脚本必须支持断点续跑：已存在 `eval/m25/results/{group}/{case_id}.json` 的样本跳过（幂等）。

### 10.6 文件清单

| 文件 | 内容 |
|---|---|
| `scripts/make_eval_set.py` | §10.2 生成器，参数 `--out eval/m25 --n 24 --seed 20261110` |
| `scripts/eval_m25.py` | 参数 `--group G0\|G1\|G2\|all --manifest ... --resume`；逐样本调用 `run_m2`，异常时记录 `status="error"` 与 traceback 并继续下一样本（这是评测脚本中唯一允许的逐样本异常捕获，捕获类型限定为 `ZoomDocError` 与 `openai.APIError`） |
| `scripts/report_m25.py` | 读取 results 生成 `report.md` 与 `bad_cases.jsonl` |
| `tests/test_eval_m25.py` | 用 FakeVLM 跑 2 个样本 × 3 组，验证结果文件格式、resume 跳过逻辑、判分 normalize、Wilson 区间数值（对照手算值） |


---

## 11. Milestone 3 现状摸排与架构契约（PageIndex 树索引 + Navigator 子图）

### 11.1 长文档瓶颈审查（为什么 M1/M2 锁死在 `max_pages=20`）

1. **代码硬上限三处阻塞 `[Fact]`**：
   - `zoomdoc/vision/render.py:46`：`if total_pages > settings.max_pages: raise ValueError(...)`（默认 `max_pages=20`），且在 `ingest` 阶段对全量页面同步执行 3 级 PNG 渲染。对于 100 页 PDF，预渲染 300 张高分辨率 PNG 将造成严重的 I/O 阻塞。
   - `zoomdoc/vision/grid.py:11-32`：`ARRANGEMENT` 字典仅定义了 `1..20` 的行列映射（上限 `20: (4, 5)`），传入 $n > 20$ 将直接触发 `KeyError`。
   - `zoomdoc/agents/seeker.py:40`：`assert 1 <= n <= 20` 显式断言拦截。
2. **50~100+ 页单图网格的三大物理死锁 `[Fact / Mathematical Bound]`**：
   - **Token 爆炸或分辨率坍缩的二元死锁**：若将 $N=100$ 页缩略图（单格 `317×448` px，分隔线 `40` px）按 $10 \times 10$ 拼合为单张画布，画布像素高达 $3530 \times 4840 \approx 1.71 \times 10^7$ px。
     - **情况 A（不降采样直接送入 VLM）**：以 Qwen2.5-VL 的 $28 \times 28$ Patch 计，单次 Seeker 请求将消耗 $\lceil 3530/28 \rceil \times \lceil 4840/28 \rceil = 127 \times 173 = 21,971$ 视觉 Token，延迟与 API 成本失控，且超出多数 VLM 的单图最大像素阈值。
     - **情况 B（VLM 服务端自动下采样至长边 1024~2048 px）**：若画布长边被压缩至 `1536` px，分摊到 $10 \times 10$ 网格的每一页仅剩约 **$110 \times 150$ px（等效不足 15 DPI）**。在此分辨率下，11pt 正文退化为 $< 2$ px 的灰斑，章节标题与表格结构完全不可辨识（M2.5 已证明 448px 缩略图下 4pt 字号 0% 准确率，150px 下连 14pt 标题都将失效）。
   - **VLM 空间位置编码与跨格计数崩溃**：当网格达到 $10 \times 10$（编号 `0..99`）时，`build_page_map` 生成 10 行冗长序数文本，VLM 在二维空间上进行跨行跨列的视觉-索引对齐（Visual-Index Grounding）会出现严重的行列偏移幻觉（Off-by-One / Row-Shift Error），导致 `choice` 召回率断崖式下跌。

### 11.2 选型裁决：无向量层级树（PageIndex）vs. 传统多模态向量检索（ColPali / 向量 RAG）

| 评估维度 | 传统多模态/文本向量 RAG（如 ColPali / Patch Embedding） | ZoomDoc M3：PageIndex 无向量层级树 + 视觉-语义双轨路由 |
|---|---|---|
| **跨页表格与图文分离** | **语义割裂（Semantic Fragmentation）**：固定窗口切块或单页孤立 Embedding 破坏“正文论述在第 $p$ 页、对应财务附表在第 $p+3$ 页”的章内拓扑关联；续表脱离表头页后无法被向量余弦相似度召回。 | **章内连续性保全（Structural Continuity）**：树节点严格绑定物理页码区间 `[start_page, end_page]`，整个逻辑小节（含跨页表格、图注与上下文）作为原子候选集（$\le 20$ 页）整体移交 Seeker 视觉网格。 |
| **长文档全局视野与回溯** | **Top-$K$ 静态截断**：一次性检索 Top-$K$ 页面，若首轮 Embedding 漏检（如财报术语同义词漂移），下游 Agent 无结构化上下文可供推理回溯。 | **状态机剪枝与树回溯（Tree Backtracking）**：Navigator 维护 `visited_node_ids`，若下游 Seeker/Critic 判定当前章节无解（`need_more_pages` 且章内候选池耗尽），主图自动路由回 Navigator 探索次优兄弟/父节点分支。 |
| **索引构建成本与可解释性** | **高昂算力与黑盒**：100 页 PDF 需对每页渲染并运行重型视觉编码器（ColPali 产生十万级 Patch 向量）及维护向量库；检索失败时难以定位原因。 | **零向量库、毫秒级原生提取**：优先解析 PDF 原生目录树（`doc.get_toc()`）+ 字体字号启发式大纲切分（借鉴 `PageIndex`），纯文本 JSON 树结构完全透明、幂等落盘，Navigator 仅消耗千级纯文本 Token 即可将 100 页收敛至 $\le 20$ 页。 |

### 11.3 树结构与数据模型契约（`zoomdoc/index/tree.py` & `zoomdoc/schemas.py`）

严格遵循 `ConfigDict(extra="forbid")` 与 1-based 物理页码锚点（对齐 `PageIndex` 的 `physical_index`）：

```python
class DocTreeNode(BaseModel):
    """文档层级树节点契约（对齐 PageIndex 思想，支持递归嵌套）。"""
    model_config = ConfigDict(extra="forbid")

    node_id: str                           # 4 位零填充唯一标识，如 "0001", "0002"（对齐 page_index_md.py:205）
    structure: str                         # 层级编码，如 "1", "1.1", "1.2.1"（对齐 page_index_classic.py:651）
    title: str                             # 章节标题文本
    level: int = Field(ge=1)               # 深度层级，1 表示顶层章
    start_page: int = Field(ge=1)          # 1-based 闭区间起始物理页码
    end_page: int = Field(ge=1)            # 1-based 闭区间结束物理页码（满足 end_page >= start_page）
    summary: str = ""                      # 章节首尾页轻量文本摘要（前 200 字符脱敏文本，无图像）
    children: list["DocTreeNode"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_span(self) -> "DocTreeNode":
        if self.end_page < self.start_page:
            raise ValueError(f"Invalid page span [{self.start_page}, {self.end_page}] in node {self.node_id}")
        return self

    @property
    def page_span(self) -> int:
        return self.end_page - self.start_page + 1

class DocumentTree(BaseModel):
    """整树索引模型。"""
    model_config = ConfigDict(extra="forbid")

    doc_id: str                            # PDF sha256[:16]
    total_pages: int = Field(ge=1)
    max_leaf_pages: int = Field(default=20, ge=1, le=20)
    roots: list[DocTreeNode] = Field(default_factory=list)
```

**树索引提取与大节点自动拆分机制（`build_document_tree`）**：
1. **三级提取流水线**：
   - **路径 A（原生 TOC 优先）**：调用 PyMuPDF `doc.get_toc()` 获取 `[[level, title, page], ...]`，通过栈式建树算法（移植 `page_index_md.py:192 build_tree_from_nodes`）构建层级关系，并根据下一同级/父级节点的 `start_page` 自动推算闭区间 `end_page`。若首页前存在无目录覆盖的前言页（`1 .. first_toc_page - 1`），自动补齐 `"0000"` Front-Matter 节点（借鉴 `tree_optimize.py:add_intro_nodes`）。
   - **路径 B（启发式字体/正则大纲解析）**：当 `doc.get_toc()` 为空时，扫描每页文本块（`page.get_text("dict")`），提取字号显著大于全书正文中位数字号或匹配章节正则（`^(Chapter|Section|\d+(\.\d+)*)\s+`）的行作为标题锚点，生成层级树。
   - **路径 C（确定性分块兜底 + 叶子跨度硬约束）**：继承 `page_index_classic.py:1175` 的大节点拆分思想，任何叶子节点若 `page_span > max_leaf_pages`（默认 20），自动按 `max_leaf_pages` 步长均匀拆分为子分段节点 `"{title} (Part k)"`，确保**任意叶子节点的页跨度严格 $\le 20$**。
2. **按需渲染解耦（Lazy / On-Demand Rendering）**：
   - 修改 `vision/render.py`：新增 `render_pdf_pages(pdf_path, page_numbers, out_dir, settings)`，仅对被选中的物理页码集合渲染 `source/view/thumb` 三级图像；保留原 `render_pdf` 供 $\le 20$ 页的 M1/M2 兼容调用。100 页长文档在 `ingest` 阶段只读取页数并校验 PDF，**绝不预渲染全书 100 页图像**，仅在 Navigator 锁定 $\le 20$ 页候选池后，由 `seeker_node` 按需渲染这 $\le 20$ 页！
3. **Prompt 注入防护**：提取页面首段文本生成节点 `summary` 时，强制经过 `_sanitize_doc_text` 过滤指令注入词（移植 `page_index_classic.py:14-28`）。

### 11.4 状态契约升级（`SupervisorState` & `NavigatorSubState`）

1. **`NavigatorReport`（Navigator 子图 → 主图的唯一纯文本出口契约）**：
   ```python
   class NavigatorReport(BaseModel):
       """Navigator 子图向主图返回的唯一路由报告契约。extra='forbid'，严禁包含图像数据。"""
       model_config = ConfigDict(extra="forbid")

       nav_round: int = Field(ge=1)
       status: Literal["located", "exhausted", "budget_exhausted"]
       selected_node_ids: list[str]
       candidate_pages: list[int]          # 1-based 升序去重页码列表，长度严格 <= max_pages (20)
       reason: str                         # 路由选择理由
       sub_steps: int
       usage: UsageTotals

       @model_validator(mode="after")
       def _check_leak_and_bounds(self) -> "NavigatorReport":
           _assert_no_image_payload(self.model_dump())
           if len(self.candidate_pages) > 20:
               raise ValueError(f"NavigatorReport candidate_pages count {len(self.candidate_pages)} exceeds hard limit 20")
           return self
   ```
2. **`NavigatorSubState`（第二个独立子图私有状态，纯文本，零图像）**：
   ```python
   class NavigatorSubState(BaseModel):
       """Navigator 子图私有状态：自顶向下或全局检索文档树，生命周期仅限单次 invoke。"""
       model_config = ConfigDict(extra="forbid")

       trace_id: str
       question: str
       nav_round: int = Field(ge=1)
       doc_tree: DocumentTree
       visited_node_ids: list[str] = Field(default_factory=list)   # 跨轮已访问过的节点 ID，防止重复进入死胡同
       feedback: str | None = None                                 # 上一轮未命中时的回溯提示
       current_frontier: list[str] = Field(default_factory=list)   # 当前层待评估的节点 ID 列表
       selected_node_ids: list[str] = Field(default_factory=list)  # 最终选定的目标节点 ID
       last_reason: str = ""
       sub_step: int = 0
       max_nav_steps: int = 4
       max_candidate_pages: int = 20
       usage: UsageTotals = Field(default_factory=UsageTotals)
       report: NavigatorReport | None = None
   ```
3. **`SupervisorState` 扩展（向后兼容 M2）**：
   - 新增字段：
     - `total_pages: int = 0`
     - `doc_tree: DocumentTree | None = None`
     - `nav_reports: Annotated[list[NavigatorReport], operator.add] = Field(default_factory=list)`
     - `visited_node_ids: list[str] = Field(default_factory=list)`
     - `nav_rounds: int = 0`
   - `next` 的 `Literal` 扩展为：`Literal["tree_builder", "navigator", "seeker", "inspector", "critic", "synthesizer", "finalize"] | None = None`。

### 11.5 双子图有向图拓扑结构（Graph Topology）

```mermaid
flowchart TD
    S((START)) --> ING["ingest: check PDF & render if <=20 pages"]
    ING --> TB["tree_builder: build_document_tree (TOC / Heuristic / Split <=20)"]
    TB --> SUP{"supervisor: decide_next"}

    subgraph NavSub["Navigator Subgraph (Pure Text, Isolated State)"]
        direction TB
        NP["nav_prepare: format unvisited tree outline"] --> NS["nav_step: VLM/LLM expand or select nodes"]
        NS --> NR{"route_nav"}
        NR -->|"action=expand & sub_step < max_nav_steps"| NP
        NR -->|"action=select or leaf reached or budget"| NPK["nav_pack_report: prune pages <= 20"]
    end

    SUP -->|"next=navigator (candidate_pages empty & nav_rounds < max_nav_rounds)"| NAV["navigator_subgraph_node: map_in -> invoke -> map_out (5 keys)"]
    NAV --> SUP

    SUP -->|"next=seeker (candidate_pages non-empty <= 20)"| SEEK["seeker: on-demand render <=20 thumbs & grid select"]
    SEEK --> SUP

    subgraph InspSub["Inspector Subgraph (Multi-Res Vision, Isolated State)"]
        direction TB
        IP["prepare: build view"] --> II["inspect: VLM answer / zoom / seek"]
        II --> IR{"route_inspect"}
        IR -->|"zoom"| IZ["zoom: high-res crop"]
        IZ --> IP
        IR -->|"answer / seek / budget"| IPK["pack_report: EvidenceReport"]
    end

    SUP -->|"next=inspector"| INSP["inspector_subgraph_node: map_in -> invoke -> map_out (5 keys)"]
    INSP --> CRI["critic: deterministic rule check"]
    CRI --> SUP

    SUP -->|"next=synthesizer"| SYN["synthesizer_text: pure text synthesis"]
    SUP -->|"next=finalize"| FIN["finalize: write result.json & supervisor_state.json"]
    SYN --> FIN
    FIN --> E((END))
```

**`decide_next(state, settings)` 升级规则表（R0–R9，兼容 M2 且支持树导航与树回溯）**：

| 规则 | 触发条件 | 目标节点 `next` | 状态更新副作用（Updates） |
|---|---|---|---|
| **R0** | `state.result is not None` | `finalize` | `{}` |
| **R1** | `state.step >= settings.max_steps` | 有 answered 证据 → `synthesizer`，否则 `finalize` | `{}` |
| **R2a** | `verdict is None and not evidence and not selected_pages` 且 `candidate_pages` 非空 | `seeker` | `{}` |
| **R2b** | `verdict is None and not evidence and not selected_pages` 且 `candidate_pages` 为空 | 若 `doc_tree is not None` 且 `nav_rounds < max_nav_rounds` 且上一节点不是刚返回空集的 `navigator` → `navigator`；否则 `finalize` | `{}` |
| **R2c** | 上一节点为 `navigator` | 若 `candidate_pages` 非空 → `seeker`；否则（Navigator `exhausted`）若有 answered 证据 → `synthesizer` 否则 `finalize` | `{}` |
| **R3** | 上一节点为 `seeker` 且 `selected_pages` 非空 | `inspector` | `{"inspect_rounds": state.inspect_rounds + 1}` |
| **R4** | 上一节点为 `seeker` 且 `selected_pages` 为空（本章节粗筛未命中） | 若 `doc_tree is not None` 且 `nav_rounds < max_nav_rounds` → `navigator`（**树回溯**：换章节）；否则若有 answered 证据 → `synthesizer` 否则 `finalize` | 回溯 `navigator` 时：`{"feedback": "Previous section thumbnails did not contain relevant pages; select alternative sections."}` |
| **R5** | `verdict is not None and verdict.accepted` | `finalize` | `{"final_answer": state.evidence[-1].answer}` |
| **R6** | `verdict.reason_code ∈ {low_confidence, invalid_reference, budget_exhausted}` 且 `inspect_rounds < max_inspect_rounds` | `inspector` | `{"feedback": state.verdict.detail, "inspect_rounds": state.inspect_rounds + 1}` |
| **R7a** | `verdict.reason_code == need_more_pages` 且 `candidate_pages` 非空 且 `seek_rounds < max_seek_rounds` | `seeker`（**章内重筛**） | `{"feedback": state.evidence[-1].seek_hint}` |
| **R7b** | `verdict.reason_code == need_more_pages` 且（`candidate_pages` 为空 或 `seek_rounds >= max_seek_rounds`）且 `doc_tree is not None` 且 `nav_rounds < max_nav_rounds` | `navigator`（**跨章树回溯**） | `{"feedback": state.evidence[-1].seek_hint, "candidate_pages": []}` |
| **R8** | 其余情况 | 有 answered 证据 → `synthesizer`，否则 `finalize` | `{}` |

> **向后兼容不变量**：当文档页数 $\le 20$ 且走 `run_m2` 时，或当 `candidate_pages` 已由 `ingest` 填充（$\le 20$ 页）时，R2a 直接命中 `seeker`，M2 的全部 45 项测试 **100% 零改动通过**。当文档 $> 20$ 页（或显式启用树导航模式 `use_tree_index=True`）时，`ingest_node` 不全量填充 `candidate_pages`，而是由 `tree_builder_node` 生成 `doc_tree`，随后通过 R2b 进入 `navigator` 将候选页收敛至 $\le 20$ 页。

### 11.6 两级上下文隔离机制（Two-Level Context Isolation）

```mermaid
flowchart LR
    subgraph Sup["SupervisorState (Global Text Context)"]
        S1["question, doc_tree, visited_node_ids, feedback"]
        S2["candidate_pages (<=20), visited_node_ids, nav_reports += [report], usage += report.usage"]
    end
    subgraph Nav["NavigatorSubState (Destroyed on Exit)"]
        N1["current_frontier, multi-turn tree expansion prompts & raw LLM outputs"]
        N2["sub_step, internal reasoning tokens"]
        N3["report: NavigatorReport"]
    end
    S1 -->|"map_in"| Nav
    N3 -->|"map_out (7 strict keys)"| S2
```

1. **第一级隔离（Navigator 子图隔离）**：
   - 在 `NavigatorSubState` 内部，Agent 执行自顶向下层级展开（`expand` 顶层章 $\to$ 查看子节标题与摘要 $\to$ `select` 目标叶节点）产生的所有中间 Prompt、树节点展开列表与多步推理过程，全部封存在 `NavigatorSubState` 局部作用域内，日志以 `namespace=f"navigator_sub/r{nav_round}"` 写入 `trace.jsonl`。
   - 子图执行完毕后，`navigator_subgraph_node` **仅向主图映射返回 7 个确定性状态键**：
     `{"candidate_pages", "visited_node_ids", "nav_reports", "nav_rounds", "usage", "step", "history", "next", "feedback"}`（其中核心业务产出仅为 `NavigatorReport`：包含 `candidate_pages`（$\le 20$ 页）、`selected_node_ids` 与 `reason` 路由理由）。
   - `NavigatorSubState` 的私有字段（`current_frontier`, `last_reason`, `max_nav_steps`）严禁出现在 `SupervisorState` 中。
2. **第二级隔离（Inspector 子图隔离）**：
   - 保持 M2 的 5-key 严格映射契约不变（`{"evidence", "usage", "step", "history", "next"}`），局部高清 Crop 图像与 Base64 载荷在子图内销毁。

---

## 12. Milestone 3 施工指导手册（读者：Gemini 3.8 Flash）

> [!IMPORTANT]
> **施工铁律**：
> 1. 严禁破坏 M1/M2/M2.5 现有的 45 个单元测试。所有现有函数签名保持向后兼容（新增参数必须带默认值）。
> 2. 严格遵守 `ConfigDict(extra="forbid")`、零宽泛 `except Exception`、零 `print`、零 `assert` 充当运行时校验（统一抛 `ZoomDocError` 或 `ValueError`）。
> 3. 每完成一个 Step 必须运行 `pytest -q` 并更新 `PROGRESS.md`。

### 12.1 新增与修改文件清单

| 动作 | 文件路径 | 模块职责 |
|---|---|---|
| 新增 | `zoomdoc/index/__init__.py` | 索引包初始化，导出 `DocTreeNode`, `DocumentTree`, `build_document_tree`, `format_tree_outline` |
| 新增 | `zoomdoc/index/tree.py` | PDF 目录树提取（原生 TOC + 字体/正则启发式 + $>20$ 页大节点自动拆分）、页面首段摘要脱敏提取、树结构格式化与页码截断器 |
| 新增 | `zoomdoc/agents/navigator.py` | `Navigator` Agent 封装：纯文本调用 VLM/LLM，在树大纲上执行 `expand`（展开子节点）或 `select`（选定目标章节） |
| 新增 | `zoomdoc/graph/navigator_subgraph.py` | Navigator 独立编译子图（`nav_prepare_node` $\to$ `nav_step_node` $\to$ `route_nav` $\to$ `nav_pack_report_node`）及包装入口 `run_navigator_round` |
| 修改 | `zoomdoc/schemas.py` | 新增 `DocTreeNode`, `DocumentTree`, `NavigatorActionExpand`, `NavigatorActionSelect`, `NavigatorOutput`, `FinalResultM3`；扩展 `StepRecord.node` 的 Literal（增加 `"tree_builder"`, `"navigator"`） |
| 修改 | `zoomdoc/config.py` & `.env.example` | 新增 M3 配置项：`max_leaf_pages: int = 20`, `max_nav_rounds: int = 3`, `max_nav_steps: int = 4`, `use_tree_index: bool = False`（`max_pages` 仍为 20，代表单次进入 Seeker 的视觉网格硬上限） |
| 修改 | `zoomdoc/prompts.py` | 新增 `NAVIGATOR_PROMPT_TEMPLATE` 与树节点文本渲染辅助函数 |
| 修改 | `zoomdoc/vision/render.py` | 新增 `get_pdf_page_count(pdf_path: str) -> int` 与按需渲染函数 `render_pdf_pages(pdf_path: str, page_numbers: list[int], out_dir: str, settings: Settings) -> dict[int, PageAsset]`；原 `render_pdf` 内部复用 `render_pdf_pages` |
| 修改 | `zoomdoc/graph/states.py` | 新增 `NavigatorReport`, `NavigatorSubState`；扩展 `SupervisorState` 支持树索引与导航轨迹 |
| 修改 | `zoomdoc/graph/supervisor.py` | 新增 `tree_builder_node`, `navigator_subgraph_node`；升级 `ingest_node`, `seeker_node`（支持按需渲染）, `decide_next`（R0–R8 含树导航与回溯）, `build_supervisor_graph`, 新增 `run_m3` |
| 新增 | `scripts/make_synthetic_long_pdf.py` | 生成 60~100 页带 3 级原生 TOC、章节标题、跨页干扰表与 4pt 黄金目标码的合成长文档生成器 |
| 新增 | `scripts/run_m3.py` | M3 命令行运行入口（`--pdf --question`） |
| 新增 | `tests/test_milestone3.py` | M3 单元测试与 100 页长文档端到端验收测试套件 |

### 12.2 接口规范与函数签名契约

#### 12.2.1 `zoomdoc/schemas.py` 新增模型

```python
class DocTreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    structure: str
    title: str
    level: int = Field(ge=1)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    summary: str = ""
    children: list["DocTreeNode"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_span(self) -> "DocTreeNode":
        if self.end_page < self.start_page:
            raise ValueError(f"Invalid page span [{self.start_page}, {self.end_page}] in node {self.node_id}")
        return self

    @property
    def page_span(self) -> int:
        return self.end_page - self.start_page + 1


class DocumentTree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    total_pages: int = Field(ge=1)
    max_leaf_pages: int = Field(default=20, ge=1, le=20)
    roots: list[DocTreeNode] = Field(default_factory=list)


class NavigatorExpand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["expand"]
    reason: str
    node_ids: list[str] = Field(min_length=1)


class NavigatorSelect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["select"]
    reason: str
    node_ids: list[str] = Field(min_length=1)


NavigatorOutput = Annotated[
    Union[NavigatorExpand, NavigatorSelect],
    Field(discriminator="action"),
]


class FinalResultM3(FinalResultM2):
    """继承 FinalResultM2，新增树导航统计字段。"""
    nav_rounds: int = 0
    selected_node_ids: list[str] = Field(default_factory=list)
    total_pages: int = 0
```

同时将 `StepRecord.node` 的 `Literal` 更新为：
```python
    node: Literal[
        "seeker",
        "inspector",
        "zoom",
        "synthesizer",
        "critic",
        "supervisor",
        "ingest",
        "pack_report",
        "tree_builder",
        "navigator",
    ]
```

#### 12.2.2 `zoomdoc/config.py` 新增配置字段

在 `Settings` 类中追加：
```python
    # Milestone 3 parameters
    max_leaf_pages: int = Field(20, ge=1, le=20)
    max_nav_rounds: int = Field(3, ge=1)
    max_nav_steps: int = Field(4, ge=1)
    use_tree_index: bool = False  # 当 total_pages > max_pages 或 use_tree_index=True 时启用 Tree + Navigator
```

#### 12.2.3 `zoomdoc/vision/render.py` 按需渲染契约

```python
def get_pdf_page_count(pdf_path: str) -> int:
    """用 PyMuPDF 打开 PDF 返回总页数并立即关闭。"""

def render_pdf_pages(
    pdf_path: str,
    page_numbers: list[int],
    out_dir: str,
    settings: Settings,
) -> dict[int, PageAsset]:
    """按需渲染指定的 1-based 物理页码列表（去重升序）。
    - 前置条件：len(page_numbers) <= settings.max_pages，且每个 p 满足 1 <= p <= total_pages，违反则 raise ValueError。
    - 幂等缓存：若 {out_dir}/assets/{doc_hash[:16]}/p{p:03d}_{source|view|thumb}.png 已存在则直接读取尺寸返回。
    - 返回 dict[int, PageAsset]。
    """

def render_pdf(pdf_path: str, out_dir: str, settings: Settings) -> list[PageAsset]:
    """保留 M1/M2 签名与行为不变：若 total_pages > settings.max_pages 则抛 ValueError；
    内部调用 render_pdf_pages(pdf_path, list(range(1, total_pages + 1)), out_dir, settings) 并按页码升序返回 list。"""
```

#### 12.2.4 `zoomdoc/index/tree.py` 树构建与检索工具函数

```python
def sanitize_summary_text(text: str, max_chars: int = 200) -> str:
    """移除换行与多余空白，过滤 prompt injection 模式（system override / ignore previous instructions 等），
    并过滤任何形如 '.png' / '.jpg' / 'data:image' / 'base64,' 的子串以防触发防泄漏误报，截断至 max_chars。"""

def build_document_tree(
    pdf_path: str,
    max_leaf_pages: int = 20,
    out_dir: str | None = None,
) -> DocumentTree:
    """从 PDF 构建层级树 DocumentTree：
    1. 计算 doc_id = sha256_file(pdf_path)[:16]。若 out_dir 提供且 {out_dir}/assets/{doc_id}/tree.json 已存在，直接反序列化返回（幂等）。
    2. 尝试读取 doc.get_toc()（[[level, title, page], ...]）：
       - 过滤非法页码（<1 或 >total_pages），按页码非递减修正；
       - 若第一个 TOC 项起始页 > 1，自动在头部插入 [1, "Front Matter & Overview", 1]；
       - 计算每个条目的 end_page：对第 i 个条目，其 end_page 为下一个满足 level <= current_level 的条目的 start_page - 1（若与 start_page 重叠则取 max(start_page, next_start - 1)），最后一个顶层条目的 end_page = total_pages；
       - 用栈式算法构建父子层级并分配 structure ("1", "1.1"...) 与 4 位 node_id ("0001"...)。
    3. 若 doc.get_toc() 为空，执行启发式扫描：
       - 遍历各页提取顶层大字号标题或匹配 r'^(?:Chapter|Section|Part|\d+(?:\.\d+)*)\b' 的行；
       - 若仍未检出任何标题，按 max_leaf_pages 均匀切分为顶层节点 "Section 1 (Pages 1-20)" 等。
    4. 大节点拆分（递归后处理）：
       - 对任意节点，若 children 为空且 page_span > max_leaf_pages，将其按 max_leaf_pages 步长拆分为多个子叶节点，node_id 依次顺延，确保所有叶子节点 page_span <= max_leaf_pages。
       - 对任意父节点，若其自身 start_page < children[0].start_page，插入一个代表章首导言页的子叶节点 "{parent.title} (Intro)"，覆盖 [parent.start_page, children[0].start_page - 1]（若跨度 > max_leaf_pages 同样拆分）。
    5. 为每个节点提取 start_page 页的前 200 字符经 sanitize_summary_text 处理后填入 summary。
    6. 若 out_dir 提供，将 DocumentTree 序列化保存至 {out_dir}/assets/{doc_id}/tree.json。
    """

def flatten_tree_nodes(tree: DocumentTree) -> dict[str, DocTreeNode]:
    """返回 node_id -> DocTreeNode 的全树映射字典。"""

def get_leaf_nodes(tree: DocumentTree) -> list[DocTreeNode]:
    """按前序遍历返回所有 children 为空的叶子节点列表。"""

def collect_candidate_pages(
    tree: DocumentTree,
    selected_node_ids: list[str],
    visited_node_ids: list[str],
    max_pages: int = 20,
) -> tuple[list[int], list[str]]:
    """根据 Navigator 选中的 node_ids 收集物理页码并截断至 <= max_pages：
    - 对每个选中的 node_id，若它是内部节点（有 children），将其展开为其子树下所有未访问（不在 visited_node_ids 中）的叶子节点；若它是叶子节点且未访问，直接纳入。
    - 按选择顺序累加叶子节点的页区间 [start_page, end_page]（升序去重）：
      - 第一个叶子节点直接加入（因为任何叶子节点 page_span <= max_leaf_pages <= max_pages，必能完整放入）；
      - 后续叶子节点：若加入后总页数 <= max_pages，则完整加入该叶子节点并记录其 node_id 到 actual_visited_ids；若加入后超出 max_pages，则停止吸纳后续节点（留待下一轮回溯访问），确保返回页数严格 <= max_pages 且单个叶子章节不被腰斩。
    - 返回 (candidate_pages, newly_visited_leaf_ids)。
    """
```

#### 12.2.5 `zoomdoc/agents/navigator.py` 与 `zoomdoc/prompts.py`

在 `zoomdoc/prompts.py` 中新增 `NAVIGATOR_PROMPT_TEMPLATE`：
```text
You are a document navigation agent. You are given a user question and a hierarchical outline of sections from a long document. Each node shows its `node_id`, `structure`, `title`, page range `[start_page-end_page]`, whether it has child subsections (`has_children`), and a short text preview (`summary`).

## Task
Choose ONE action to locate the sections most likely to answer the question:
- "expand": if one or more parent nodes (`has_children=True`) look relevant and you want to inspect their child subsections before deciding.
- "select": if you have identified the specific leaf sections (or small sections) whose pages should be visually inspected.

## Question
{question}
{feedback}
## Available Outline Nodes (Unvisited)
{outline_block}

## Response Format (JSON only, one of the following)
A. {
    "action": "expand",
    "reason": "Explain which parent sections may contain the target information",
    "node_ids": ["<node_id>", ...]
}
B. {
    "action": "select",
    "reason": "Explain why these specific sections are selected for visual inspection",
    "node_ids": ["<node_id>", ...]
}

Return JSON only.
```

在 `zoomdoc/agents/navigator.py` 中实现：
```python
class Navigator:
    """Tree outline navigation agent (pure text, zero image payload)."""

    def __init__(self, vlm: VLMClient, settings: Settings, logger: TraceLogger) -> None: ...

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
        """纯文本调用 call_with_schema(adapter=TypeAdapter(NavigatorOutput))：
        - 前置条件：frontier_nodes 非空，否则 raise ZoomDocError。
        - validate 校验函数：
          1. output.node_ids 中每个 nid 必须存在于 {n.node_id for n in frontier_nodes}；
          2. 若 output.action == "expand"：必须满足 can_expand is True，且选中的 nid 至少有一个 children 非空；否则 raise ValueError 触发纠正重试。
        """
```

#### 12.2.6 `zoomdoc/graph/navigator_subgraph.py` 子图与包装函数

```python
def nav_prepare_node(state: NavigatorSubState) -> dict[str, Any]:
    """若 state.current_frontier 为空（初始进入）：
    - 将 doc_tree.roots 中未完全访问的顶层节点 ID 设为 current_frontier；
    - 若所有顶层节点下的叶子均在 visited_node_ids 中，则 current_frontier 设为空列表。
    """

def nav_step_node(
    state: NavigatorSubState,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> dict[str, Any]:
    """若 current_frontier 为空，直接返回 {"sub_step": state.sub_step + 1}。
    否则调用 Navigator.run(...)（can_expand = (state.sub_step + 1 < state.max_nav_steps)）：
    - 若返回 NavigatorExpand：将选中父节点的 children（过滤掉已全部访问的子树）加上未选中的可疑同级叶节点更新为新的 current_frontier；若展开结果全部是叶子且 sub_step + 1 >= max_nav_steps，同时将它们记入 selected_node_ids 作为保底。
    - 若返回 NavigatorSelect：将 output.node_ids 写入 selected_node_ids。
    - 累加 usage，sub_step += 1。
    """

def route_nav(state: NavigatorSubState) -> Literal["nav_prepare", "nav_pack_report"]:
    """若 current_frontier 为空 或 selected_node_ids 非空 或 sub_step >= state.max_nav_steps -> 'nav_pack_report'；
    否则 -> 'nav_prepare'。"""

def nav_pack_report_node(state: NavigatorSubState) -> dict[str, Any]:
    """构造 NavigatorReport：
    - 若 selected_node_ids 非空：调用 collect_candidate_pages(state.doc_tree, state.selected_node_ids, state.visited_node_ids, state.max_candidate_pages) 获取 (pages, newly_visited_ids)，status = "located" if pages else "exhausted"。
    - 若 selected_node_ids 为空但 sub_step >= state.max_nav_steps（步数耗尽仍只 expand 未 select）：
      从 current_frontier 中调用 collect_candidate_pages 兜底提取 <= max_candidate_pages 的页，status = "budget_exhausted" if not pages else "located"。
    - 若 current_frontier 为空且无可选页：status = "exhausted", candidate_pages = []。
    - 返回 {"report": report}。
    """

def build_navigator_subgraph(
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> CompiledStateGraph: ...

def run_navigator_round(
    *,
    question: str,
    nav_round: int,
    doc_tree: DocumentTree,
    visited_node_ids: list[str],
    feedback: str | None,
    trace_id: str,
    vlm: VLMClient,
    settings: Settings,
    logger: TraceLogger,
) -> NavigatorReport:
    """独立编译并 invoke NavigatorSubState，仅提取并返回 NavigatorReport。"""
```

#### 12.2.7 `zoomdoc/graph/supervisor.py` 升级与 `run_m3` 入口

1. **`ingest_node(state, settings)` 升级**：
   - 先调用 `total_pages = get_pdf_page_count(state.pdf_path)`。
   - 若 `total_pages == 0`：返回 `{"total_pages": 0, "pages": {}, "candidate_pages": [], "step": state.step + 1, "history": [step_rec]}`。
   - 若 `total_pages <= settings.max_pages` 且 `not settings.use_tree_index`（M2 兼容快速路径）：
     调用 `render_pdf` 渲染全部页面，`candidate_pages = [1..total_pages]`，不构建树（或 `doc_tree=None`），直接进入 `supervisor`。
   - 若 `total_pages > settings.max_pages` 或 `settings.use_tree_index is True`（M3 树索引路径）：
     **不在此处全量渲染 PDF**！返回 `{"total_pages": total_pages, "pages": {}, "candidate_pages": [], "step": state.step + 1, "history": [step_rec]}`，后接 `tree_builder_node`。
2. **`tree_builder_node(state, settings, logger)`**：
   - 当 `state.total_pages > settings.max_pages` 或 `settings.use_tree_index` 时，调用 `build_document_tree(state.pdf_path, max_leaf_pages=settings.max_leaf_pages, out_dir=state.run_dir)` 生成 `doc_tree`，记录 `StepRecord(node="tree_builder", ...)` 并写入 trace。
   - 若 `state.total_pages <= settings.max_pages and not settings.use_tree_index`（M2 兼容模式），`ingest` 直接连 `supervisor`（或在 `decide_next` 中跳过 `tree_builder`），确保 M2 测试中 `history` 的节点序列仍然是 `["ingest", "seeker", "inspector", "critic"]`！
   - **关键设计（保证 M2 A1 第 6 条断言 `history == ["ingest", "seeker", "inspector", "critic"]` 零回归）**：
     `ingest` 之后通过 `supervisor` 路由！在 `decide_next` 中增加一条前置规则 **R1.5**：
     若 `(state.total_pages > settings.max_pages or settings.use_tree_index) and state.doc_tree is None and state.total_pages > 0` $\to$ 返回 `("tree_builder", {})`！
     这样：
     - 对于 $\le 20$ 页且 `use_tree_index=False`（M2 默认）：`START -> ingest -> supervisor -> seeker ...`，`history` 序列与 M2 **完全一字不差**！
     - 对于 $> 20$ 页或 `use_tree_index=True`（M3 模式）：`START -> ingest -> supervisor -> tree_builder -> supervisor -> navigator -> supervisor -> seeker ...`，清晰完整！
3. **`seeker_node(state, vlm, settings, logger)` 按需渲染升级**：
   - 检查 `state.candidate_pages` 中是否有尚未存在于 `state.pages` 的页码：`missing = [p for p in state.candidate_pages if p not in state.pages]`。
   - 若 `missing` 非空，调用 `newly_rendered = render_pdf_pages(state.pdf_path, missing, state.run_dir, settings)`，合并进 `updated_pages = {**state.pages, **newly_rendered}` 并在返回字典中包含 `"pages": updated_pages`。
4. **`run_m3` 入口函数**：
   ```python
   def run_m3(
       question: str,
       pdf_path: str,
       vlm: VLMClient | None = None,
       settings: Settings | None = None,
       trace_id: str | None = None,
   ) -> tuple[FinalResultM3, SupervisorState]:
       """Milestone 3 主入口：强制开启 use_tree_index=True（若调用方未显式关闭），
       运行 build_supervisor_graph，返回 (FinalResultM3, SupervisorState)。"""
   ```

### 12.3 单测验收标准与合成长文档测试桩设计（`tests/test_milestone3.py`）

#### 12.3.1 合成 100 页长文档生成器（`scripts/make_synthetic_long_pdf.py`）

实现函数 `create_synthetic_long_pdf(out_path: str, total_pages: int = 100, gold_page: int = 73, gold_code: str = "ZD-9042-M3") -> dict`：
1. 生成 `total_pages`（默认 100 页）A4 PDF。
2. 构建 3 级原生目录（通过 `doc.set_toc(toc)` 写入 PyMuPDF Bookmarks），并在每页顶部绘制 20pt 章节大标题与 11pt 章节导言文本：
   - **Part 1: Corporate Governance & Strategy** (Pages 1–25)
     - 1.1 Board Overview (Pages 1–12)
     - 1.2 Risk Factors & Compliance (Pages 13–25)
   - **Part 2: Operational Infrastructure & Supply Chain** (Pages 26–55)
     - 2.1 Logistics Network (Pages 26–40)
     - 2.2 Warehouse Telemetry (Pages 41–55)
   - **Part 3: Financial Statements & Audit Notes** (Pages 56–85)
     - 3.1 Consolidated Balance Sheets (Pages 56–68)
     - 3.2 Off-Balance-Sheet Derivative Instruments (Pages 69–80) $\leftarrow$ **包含第 73 页（Gold Page）**
     - 3.3 Tax Reconciliation (Pages 81–85)
   - **Part 4: Appendices & Regulatory Filings** (Pages 86–100)
     - 4.1 Subsidiary List (Pages 86–100)
3. 在第 `gold_page`（默认第 73 页，位于 3.2 节 `[69, 80]` 共 12 页区间内）右下角绘制 3×2 极小字号（5pt）核验表格，写入目标码 `gold_code`（`"ZD-9042-M3"`），在其他章节各放一个干扰表格（内容为 `"ZD-0000-XX"`）。

#### 12.3.2 自动化验收测试套件（`tests/test_milestone3.py`）

必须包含以下 6 项硬性验收测试（全部使用 `FakeVLM`，离线毫秒级运行）：

1. **`test_m3_t1_tree_builder_with_toc_and_large_node_split`**：
   - 用 `create_synthetic_long_pdf` 生成 100 页 PDF，验证 `build_document_tree`：
   - 断言 `tree.total_pages == 100`；
   - 断言所有叶子节点满足 `1 <= leaf.page_span <= 20`；
   - 构造一个无 TOC 的 55 页 PDF，验证启发式/兜底切分后所有叶子节点同样满足 `leaf.page_span <= 20`，且全树叶子页码并集完整覆盖 `1..55`。
2. **`test_m3_t2_100_page_e2e_navigation_and_zoom`（100 页端到端主用例）**：
   - 100 页合成 PDF（金标在第 73 页，属于 3.2 节 `[69..80]`，共 12 页）。
   - `FakeVLM` 脚本：
     1. Navigator Step 1：`{"action": "expand", "reason": "Financial statements in Part 3", "node_ids": ["<Part 3 node_id>"]}`；
     2. Navigator Step 2：`{"action": "select", "reason": "Section 3.2 covers derivative instruments", "node_ids": ["<Section 3.2 node_id>"]}`；
     3. Seeker（收到 3.2 节的 12 页缩略图 `69..80`，第 73 页对应索引 `4`）：`{"reason": "Page 73 has table", "summary": "Table found", "choice": [4]}`；
     4. Inspector Step 1（收到第 73 页 view 图）：`{"action": "zoom", "reason": "Small table", "image": 0, "bbox_2d": [638, 831, 940, 926], "target": "serial code"}`；
     5. Inspector Step 2（收到第 73 页 + 高清 crop）：`{"action": "answer", "reason": "Read code", "answer": "ZD-9042-M3", "confidence": 0.95, "reference": [0, 1]}`。
   - **刚性断言**：
     - `result.status == "answered"` 且 `result.answer == "ZD-9042-M3"` 且 `73 in result.reference_pages`；
     - **候选页数硬上限断言**：流入 Seeker 的候选页列表长度 `len(seeker_step.input_images) == 12 <= 20`；
     - **按需渲染断言**：`len(state.pages) == 12`（100 页 PDF 中仅渲染了 3.2 节的 12 页，其余 88 页未触发 PNG 渲染）；
     - **双子图隔离断言**：`supervisor_state.json` 中不含 `data:image`、`base64,`、`/sub/`，且 `SupervisorState.model_fields` 与 `NavigatorSubState` / `InspectorSubState` 私有字段（`current_frontier`, `crops`, `view` 等）完全正交；
     - 主图 `history` 节点序列为 `["ingest", "tree_builder", "navigator", "seeker", "inspector", "critic"]`。
3. **`test_m3_t3_tree_backtracking_on_seeker_miss_or_need_more_pages`（跨章树回溯测试）**：
   - 验证当第 1 轮 Navigator 选错章节（例如选了 1.1 节 `1..12` 页），Seeker 检视后返回选中 `[0]`、Inspector 返回 `action="seek"`（`need_more_pages`）且候选池耗尽（或 Seeker 直接返回 `choice=[]`）时：
   - `decide_next` 触发 **R4 / R7b 树回溯**，再次路由至 `navigator`（`nav_rounds == 2`）；
   - 第 2 轮 Navigator 的输入中自动排除了 `visited_node_ids`（1.1 节不再重复选中），并携带 `feedback` 选中正确的 3.2 节（`69..80` 页），最终命中第 73 页完成作答！
4. **`test_m3_t4_candidate_pages_hard_cap_20`（多节点选择截断测试）**：
   - 当 Navigator 一次性 `select` 了多个叶子节点（例如总页数达 35 页）时，`collect_candidate_pages` 与 `NavigatorReport` 必须将单轮输出的 `candidate_pages` 严格截断至 `<= 20` 页，未纳入的节点保留在未访问池中供下一轮回溯。
5. **`test_m3_t5_section_recall_benchmark_10_queries`（章节召回率 $\ge 90\%$ 基准验证）**：
   - 在 100 页多章节合成文档上构造 10 个不同章节的测试 Query（黄金页分布在 `p5, p18, p32, p47, p60, p73, p82, p91` 等 10 个位置），验证树导航 + Seeker 链路对黄金参考页的章节召回率（`gold_page in candidate_pages` 且 `gold_page in selected_pages`）达到 $10/10 = 100\% \ge 90\%$，且每次 Seeker 调用的输入页数全部 $\le 20$。
6. **`test_m3_t6_m1_m2_zero_regression`**：
   - 全量运行 `pytest -q`，确保 M1/M2/M2.5 原有 45 项测试 + M3 新增测试 100% 通过。


---

## 13. Milestone 4 架构契约与科学评测设计（正式基准评测、H1 判决与 M5 触发门）

### 13.1 为什么 M2.5 不足以替代 M4？（评测认知落差分析）

1. **文档规模落差**：M2.5 仅在 4 页短文档上评测了 `Inspector + Zoom + Critic` 的局部阅读增益，从未在 50~100 页长文档上评测 `Tree Builder + Navigator + Seeker + Inspector` 的**全链路召回率与端到端准确率**。
2. **核心假说 `H1`（Token 节省率 $\ge 60\%$）尚未检验**：§2.2 提出的 `[Hypothesis H1]`（树路由 + 缩略图粗筛 + 局部按需放大相比 ViDoRAG 式全量网格扫描节省 $\ge 60\%$ 提示词 Token，即 Token 比值 $\le 0.40$）在 M1–M3 均未做配对基线实验。
3. **Milestone 5（Redis 缓存）的条件触发门（§2.5）需要物理剖析数据**：§2.5 规定，仅当 M4 实测 **PDF 渲染耗时占单次端到端运行总耗时 $\ge 20\%$，或单次运行 P95 延迟 $\ge 120,000\text{ ms}$** 时，才启动 M5（Redis 内容寻址缓存）；否则判定按需渲染 + 本地文件幂等已足够高效，避免过度工程化。

### 13.2 对等基线设计（Fair Baselines & Anti-Strawman Protocol）

遵循学术准则第 17 条（严禁设立弱基线），在 60~100 页长文档上，原版 ViDoRAG 因 20 页网格硬上限无法直接运行。为此，我们设计 **4 组严格受控的对比系统**（使用完全相同的模型快照、相同的 `temperature=0`、`seed=42` 与相同的判分函数）：

| 组别代号 | 系统配置名称 | 路由与感知机制（60~100 页长文档） | 消融对比目的 |
|---|---|---|---|
| **B0 (`SlidingGrid-FullRes`)** | ViDoRAG 对等扩展基线（无树、1024px 高清网格滑窗） | 将长文档按 20 页/窗切分为 $\lceil P/20 \rceil$ 个滑窗；对每个窗口以 `thumb_long_side=1024`（ViDoRAG `scale=1` 等效高分辨率）运行 Seeker 选页，汇总所有窗口选中页（截断至 `max_inspector_images=6`）交由 Inspector（`max_zooms=0`）作答。 | **检验 H1 的基准分母**：代表无树索引、无主动放大时，靠高分辨率全书网格扫描所需的真实 Token 与延迟成本。 |
| **B1 (`SlidingGrid-Thumb`)** | 缩略图滑窗 + 主动放大（无树索引消融组） | 同 B0 按 20 页滑窗扫描全书，但使用 `thumb_long_side=448` 低分辨率网格；汇总各窗选中页后交由 `Inspector + Zoom + Critic`（`max_zooms=3`）作答。 | **消融 Navigator 树索引的独立贡献**：对比 `M3-Full` vs `B1`，量化 PageIndex 树路由在减少滑窗调用次数、降低跨章干扰表误召回上的净收益。 |
| **B2 (`Tree-NoZoom`)** | 树导航 + 缩略图粗筛（无 Zoom 消融组） | 启用 `run_m3`（`Tree + Navigator + Seeker`），但设 `max_zooms=0, max_inspect_rounds=1`。 | **长文档下复核 H2**：证明即使 Navigator 精准命中章节、Seeker 命中目标页，缺乏局部高清 Zoom 仍无法读出密集表格中的极小字。 |
| **M3-Full (`ZoomDoc-M3`)** | 本项目完整体（Tree + Navigator + Seeker + Zoom + Critic） | 启用 `run_m3` 默认完整配置（`use_tree_index=True, max_zooms=3, max_inspect_rounds=2, critic_min_confidence=0.6`）。 | **主力评测系统**：同时考核章节召回率、页面召回率、最终答案准确率、Token 节省率与渲染耗时占比。 |

### 13.3 双轨基准数据集与防数据污染审计（Dual-Track Benchmark & Contamination Audit）

为兼顾**离线 CI 零成本确定性测试**与**真实文档泛化能力评估**，M4 数据集生成器 `scripts/make_eval_m4.py` 采用双轨设计（总样本量 $N = 20$，支持 `--n` 扩展至 $50$）：

1. **Track A：长文档合成压力集（`Synthetic-Long-Hard`，默认 $N_A = 12$）**：
   - 跨 3 个不同的 60~100 页多层级长 PDF（涵盖财务衍生品附表、供应链遥测日志、合规审计附录 3 类主题），每个 PDF 含 4 层目录结构、跨页正文描述 + 后续页附表（测试跨页表格关联）、每章设置 2 个同构干扰表格（测试抗干扰能力）。
   - 正交因子：文档页数 $P \in \{60, 100\}$ × 目标字号 $\text{font\_pt} \in \{4, 5\}$ × 问题类型 `query_type ∈ {"direct_section", "cross_page_table", "backtrack_trap"}`（其中 `backtrack_trap` 在标题相似的干扰章节放置”详见第 X.Y 节附表”的交叉引用提示，专门测试 R7b 跨章树回溯能力）。
2. **Track B：真实/外部多模态文档基准适配集（`External-Doc-Subset`，默认 $N_B = 8$）**：
   - 自动扫描本地是否存在外部真实 PDF 样本（如 `eval/external/` 或 `references/PageIndex/tests/data/` 下的真实多页报告 PDF）并构造带精确物理页码锚点与标准答案的测试清单；若本地无外部大文件，则自动使用 PyMuPDF 渲染生成包含真实多栏学术排版、跨页财务资产负债表与密集脚注的仿真财报集（`Realistic-10K-Bench`）。
3. **防数据污染与记忆重放审计（Contamination & Memorization Audit，准则第 18 条）**：
   - 实现 `zoomdoc/eval/metrics.py::compute_ngram_overlap(prompts: list[str], gold_answers: list[str], n: int = 4) -> dict`：
   - 审计所有测试样本的 `gold` 答案及关键数值是否曾出现在 `zoomdoc/prompts.py` 的 Few-Shot 示例中（计算字符级与 token 级 Overlap Ratio），并校验所有合成金标码采用时间戳隔离的随机哈希种子（`seed=2026111004`），确保先验记忆重放率为 **0.0%**。

### 13.4 渲染耗时埋点、统计检验与雙门禁判决规则（H1 + M5 Gate）

1. **全链路耗时拆分埋点（Render vs. Tree vs. VLM Profiling）**：
   - 在 `FinalResultM3`（或评测记录 `EvalCaseRecordM4`）中精确计量三个正交时间分量：
     - `render_latency_ms: float`：由 `render_pdf` / `render_pdf_pages` 实际执行 PyMuPDF 光栅化与 Pillow 缩放落盘的耗时总和；
     - `tree_latency_ms: float`：`build_document_tree` 构建目录树的耗时；
     - `vlm_latency_ms: float`：`state.usage.latency_ms`（所有 VLM API 调用耗时总和）；
     - `total_wall_ms: float`：单次运行端到端挂钟总耗时。
2. **核心指标与非参数统计检验（`zoomdoc/eval/metrics.py`）**：
   - **检索召回率漏斗**：
     - 章节召回率 $\text{Recall}_{\text{nav}} = \mathbb{I}(\text{gold\_page} \in \bigcup \text{nav\_reports.candidate\_pages})$；
     - 粗筛召回率 $\text{Recall}_{\text{seek}} = \mathbb{I}(\text{gold\_page} \in \bigcup \text{selected\_pages})$；
     - 端到端准确率 $\text{Acc} = \mathbb{I}(\text{normalize}(\text{pred}) == \text{normalize}(\text{gold}))$（附 95% Wilson 置信区间）。
   - **H1 视觉/提示 Token 节省率与 Bootstrap 置信区间**：
     - 对同一测试样本 $i \in \{1 \dots N\}$，记完整系统 `M3-Full` 的总 `prompt_tokens` 为 $T_i^{\text{M3}}$，全分辨率滑窗基线 `B0` 的总 `prompt_tokens` 为 $T_i^{\text{B0}}$。
     - 定义样本级 Token 消耗比 $r_i = T_i^{\text{M3}} / T_i^{\text{B0}}$，总体 Token 比值 $\hat{R} = \frac{\sum_{i=1}^N T_i^{\text{M3}}}{\sum_{i=1}^N T_i^{\text{B0}}}$，总体 Token 节省率 $\hat{S} = 1 - \hat{R}$。
     - 采用 **Paired Bootstrap Resampling**（重采样次数 $B = 2000$，固定种子 `seed=42`）计算 $\hat{R}$ 与 $\hat{S}$ 的 **95% 置信区间 $[R_{\text{low}}, R_{\text{high}}]$**。
3. **双门禁形式化判决规则（写入 `eval/m4/report.md` 首部）**：
   - **H1 判决（Token 节省率 $\ge 60\%$ 假说）**：
     - `PASS-H1`：$\hat{R} \le 0.40$（即总体节省率 $\hat{S} \ge 60.0\%$）且 `M3-Full` 准确率 $\ge$ `B0` 准确率；
     - `PARTIAL-H1`：$0.40 < \hat{R} \le 0.65$（节省率在 $35\% \sim 60\%$ 之间）且准确率不低于 `B0`；
     - `FAIL-H1`：$\hat{R} > 0.65$ 或 `M3-Full` 准确率显著低于 `B0`。
   - **M5 触发判决（Redis 内容寻址缓存门禁，对齐 §2.5）**：
     - 计算 `M3-Full` 组的平均渲染耗时占比 $\rho_{\text{render}} = \text{mean}(\text{render\_latency\_ms} / \text{total\_wall\_ms})$ 与端到端延迟 $P_{95}(\text{total\_wall\_ms})$：
     - 若 $\rho_{\text{render}} \ge 0.20$ **或** $P_{95}(\text{total\_wall\_ms}) \ge 120,000\text{ ms}$ $\longrightarrow$ 输出 `M5_REDIS_TRIGGER: REQUIRED`；
     - 否则 $\longrightarrow$ 输出 `M5_REDIS_TRIGGER: OPTIONAL_SKIPPED (On-demand lazy rendering sufficient)`。

---

## 14. Milestone 4 施工指导手册（读者：Gemini 3.8 Flash）

> [!IMPORTANT]
> **施工铁律**：
> 1. 严禁破坏 M1/M2/M2.5/M3 已有的 **51 个测试**。
> 2. 所有统计计算函数（Bootstrap 置信区间、n-gram 污染审计、耗时分位数、召回率漏斗）必须封装在独立模块 `zoomdoc/eval/` 下，做到 100% 纯 Python 标准库实现（`math`, `random`, `statistics`），不引入 `scipy` 等重型外部依赖。
> 3. 严格遵守 `ConfigDict(extra="forbid")`、零宽泛 `except Exception`、零 `print`（CLI 与评测脚本终端进度汇报除外）。

### 14.1 新增与修改文件清单

| 动作 | 文件路径 | 模块职责 |
|---|---|---|
| 修改 | `zoomdoc/schemas.py` | 在 `FinalResultM3` 中新增可选性能剖析字段：`render_latency_ms: float = 0.0`, `tree_latency_ms: float = 0.0`, `total_wall_ms: float = 0.0` |
| 修改 | `zoomdoc/graph/supervisor.py` | 在 `ingest_node`, `tree_builder_node`, `seeker_node`, `finalize_node`, `run_m3` 中用 `time.perf_counter()` 记录 `render_latency_ms`, `tree_latency_ms`, `total_wall_ms` 并写入 `SupervisorState` 与 `FinalResultM3` |
| 修改 | `zoomdoc/graph/states.py` | 在 `SupervisorState` 中新增 `render_latency_ms: Annotated[float, operator.add] = 0.0`, `tree_latency_ms: Annotated[float, operator.add] = 0.0` |
| 新增 | `zoomdoc/eval/__init__.py` | 评测包初始化，导出基线运行器与全部统计指标函数 |
| 新增 | `zoomdoc/eval/baselines.py` | 实现对等滑窗网格基线运行器 `run_sliding_grid_baseline`（支持 `B0` 1024px 无放大 与 `B1` 448px + Inspector 子图放大） |
| 新增 | `zoomdoc/eval/metrics.py` | 实现 `paired_bootstrap_ratio_ci`, `compute_ngram_overlap`, `audit_prompt_contamination`, `compute_recall_funnel`, `evaluate_m5_trigger` |
| 新增 | `scripts/make_eval_m4.py` | 生成 M4 双轨评测集（Track A 长文档多因子 + Track B 仿真财报/外部集），输出 `eval/m4/manifest.jsonl` |
| 新增 | `scripts/eval_m4.py` | 支持 `--group B0\|B1\|B2\|M3\|all --manifest eval/m4/manifest.jsonl --resume --limit N` 的幂等评测执行器 |
| 新增 | `scripts/report_m4.py` | 读取 `eval/m4/results/` 生成完整学术级评测报告 `eval/m4/report.md`（含 H1 Bootstrap 判决、M5 Redis 触发判决、召回漏斗、防污染审计表）与 `eval/m4/bad_cases.jsonl` |
| 新增 | `tests/test_milestone4.py` | M4 全链路单元测试与 FakeVLM 4 组对比闭环验收测试套件（`T1`–`T6`） |

### 14.2 接口规范与函数签名契约

#### 14.2.1 `zoomdoc/schemas.py` 与 `zoomdoc/graph/states.py` 耗时埋点扩展

1. 在 `zoomdoc/schemas.py` 的 `FinalResultM3` 中追加字段（带默认值，100% 兼容 M3 现有测试）：
   ```python
   class FinalResultM3(FinalResultM2):
       """继承 FinalResultM2，新增树导航与性能剖析统计字段。"""
       nav_rounds: int = 0
       selected_node_ids: list[str] = Field(default_factory=list)
       total_pages: int = 0
       render_latency_ms: float = 0.0
       tree_latency_ms: float = 0.0
       total_wall_ms: float = 0.0
   ```
2. 在 `zoomdoc/graph/states.py` 的 `SupervisorState` 中追加：
   ```python
   render_latency_ms: Annotated[float, operator.add] = 0.0
   tree_latency_ms: Annotated[float, operator.add] = 0.0
   ```
3. 在 `zoomdoc/graph/supervisor.py` 中：
   - `ingest_node`：用 `t0 = time.perf_counter()` 测量 `render_pdf` 耗时（毫秒），返回 `"render_latency_ms": round((time.perf_counter() - t0) * 1000.0, 2)`；
   - `tree_builder_node`：测量 `build_document_tree` 耗时，返回 `"tree_latency_ms": round((time.perf_counter() - t0) * 1000.0, 2)`；
   - `seeker_node`：测量 `render_pdf_pages` 按需渲染耗时，返回 `"render_latency_ms": round(render_ms, 2)`；
   - `finalize_node` 與 `synthesizer_text_node`：在构造 `FinalResultM3` 时填入 `render_latency_ms=round(state.render_latency_ms, 2)` 与 `tree_latency_ms=round(state.tree_latency_ms, 2)`；
   - `run_m3`：在入口处记录 `t_start = time.perf_counter()`，图执行完毕后计算 `wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)`，通过 `res_m3 = res_m3.model_copy(update={"total_wall_ms": wall_ms})` 更新并重新写回 `run_dir / "result.json"`。

#### 14.2.2 `zoomdoc/eval/baselines.py` 对等滑窗网格基线运行器

```python
def run_sliding_grid_baseline(
    question: str,
    pdf_path: str,
    *,
    mode: Literal["B0_fullres_nozoom", "B1_thumb_zoom"],
    vlm: VLMClient | None = None,
    settings: Settings | None = None,
    trace_id: str | None = None,
    window_size: int = 20,
) -> tuple[FinalResultM3, list[int]]:
    """ViDoRAG 对等扩展的长文档滑窗网格基线（无树索引）：
    1. 获取 PDF 总页数 total_pages = get_pdf_page_count(pdf_path)。
    2. 根据 mode 构造基准 Settings：
       - 若 mode == "B0_fullres_nozoom"：设置 thumb_long_side = settings.view_long_side (1024), max_zooms = 0, max_inspect_rounds = 1；
       - 若 mode == "B1_thumb_zoom"：设置 thumb_long_side = 448, max_zooms = 3, max_inspect_rounds = 1。
    3. 将 1..total_pages 按 window_size (20) 切分为连续窗口：
       对每个窗口 win_pages：
       - 调用 render_pdf_pages(pdf_path, win_pages, run_dir, cfg) 渲染并累加 render_latency_ms；
       - 调用 Seeker.run(question, candidate_assets, feedback=None, ...) 获取该窗口选中的物理页码 win_selected，累加 usage。
    4. 汇总所有窗口选中的页码 all_selected（保持顺序去重）：
       - 若 all_selected 为空：直接返回 status="no_answer" 的 FinalResultM3 与 []；
       - 若 len(all_selected) > cfg.max_inspector_images (6)：截取前 cfg.max_inspector_images 页作为 final_selected_pages。
    5. 调用 run_inspector_round(question=question, round_=1, pages=sub_pages, ...) 执行检视（B0 下 max_zooms=0 不裁剪；B1 下允许最多 3 次裁剪），累加 usage。
    6. 返回 (FinalResultM3, all_selected)。
    """
```

#### 14.2.3 `zoomdoc/eval/metrics.py` 统计检验与审计工具函数

```python
def paired_bootstrap_ratio_ci(
    numerator_vals: list[float],
    denominator_vals: list[float],
    *,
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict[str, float]:
    """计算配对样本比值 R = sum(num) / sum(den) 及节省率 S = 1 - R 的 Bootstrap 置信区间：
    - 前置条件：len(numerator_vals) == len(denominator_vals) >= 1，且 sum(denominator_vals) > 0，否则 raise ValueError。
    - 使用 random.Random(seed) 进行 n_boot 次有放回抽样（每次抽取 N 个配对索引），计算每次重采样的 ratio_b = sum(num_b) / sum(den_b)。
    - 返回 {
        "ratio": round(point_ratio, 4),
        "ratio_ci_low": round(r_low, 4),
        "ratio_ci_high": round(r_high, 4),
        "savings": round(1.0 - point_ratio, 4),
        "savings_ci_low": round(1.0 - r_high, 4),
        "savings_ci_high": round(1.0 - r_low, 4),
      }。
    """

def compute_ngram_overlap(
    candidate_texts: list[str],
    reference_corpus: str,
    n: int = 4,
) -> dict[str, Any]:
    """防数据污染审计：计算候选金标答案/问题与系统内置 Prompt 语料（reference_corpus）的字符级及词级 n-gram 重叠率：
    - 返回 {
        "exact_match_count": int,
        "exact_match_rate": float,
        "char_ngram_overlap_rate": float,
        "leaked_items": list[str],
      }。
    """

def evaluate_m5_trigger(
    render_latencies_ms: list[float],
    total_wall_latencies_ms: list[float],
    *,
    render_ratio_threshold: float = 0.20,
    p95_wall_threshold_ms: float = 120000.0,
) -> dict[str, Any]:
    """根据 §2.5 规则判定是否需要触发 Milestone 5 (Redis 缓存)：
    - 计算每个样本的 render_share = render_ms / max(wall_ms, 1e-6) 的均值 mean_render_share；
    - 计算 total_wall_latencies_ms 的 P95 分位数 p95_wall_ms；
    - triggered = (mean_render_share >= render_ratio_threshold) or (p95_wall_ms >= p95_wall_threshold_ms)；
    - 返回 {
        "triggered": bool,
        "decision": "REQUIRED" if triggered else "OPTIONAL_SKIPPED",
        "mean_render_share": round(mean_render_share, 4),
        "p95_wall_ms": round(p95_wall_ms, 2),
        "reason": str,
      }。
    """
```

### 14.3 单测验收标准（`tests/test_milestone4.py`）

必须包含以下 6 项确定性单元测试（全部使用 `FakeVLM` 离线运行，毫秒/秒级完成）：

1. **`test_m4_t1_latency_profiling_in_run_m3`**：
   - 在合成长文档上运行 `run_m3`，验证返回的 `FinalResultM3` 中 `render_latency_ms > 0`、`tree_latency_ms >= 0`、`total_wall_ms >= render_latency_ms`，且落盘的 `result.json` 包含这三个字段。
2. **`test_m4_t2_sliding_grid_baselines_b0_and_b1`**：
   - 生成一个 45 页合成 PDF（含 3 个 20/20/5 页滑窗，金标在第 33 页）。
   - 验证 `run_sliding_grid_baseline(..., mode="B0_fullres_nozoom")` 依次调用 3 次 Seeker（对应 3 个滑窗）+ 1 次 Inspector（共 4 次 VLM 调用），且在 `B0` 下不触发任何 `zoom`；
   - 验证 `B1_thumb_zoom` 模式下 Inspector 可正常触发 `zoom` 高清裁剪并正确作答。
3. **`test_m4_t3_paired_bootstrap_ratio_ci_and_h1_verdict`**：
   - 给定一组已知的配对 Token 数据（如 `B0 = [10000, 12000, 11000, 9000]`，`M3 = [3000, 3600, 3300, 2700]`，真实比值恒为 `0.30`，节省率 `0.70`），验证 `paired_bootstrap_ratio_ci` 返回 `ratio == 0.30`、`savings == 0.70`，且置信区间紧收敛于 `0.30`。
   - 验证当 `denominator_vals` 全为 0 或长度不匹配时显式抛出 `ValueError`。
4. **`test_m4_t4_contamination_audit_zero_leakage`**：
   - 调用 `make_eval_m4.py` 生成 M4 评测集 `manifest.jsonl`，读取全部样本的 `gold` 答案，与 `zoomdoc/prompts.py` 全文进行 `compute_ngram_overlap` 审计：
   - 断言 `exact_match_count == 0`（M1 调试用过的 `ZD-7319-QX` 绝不出现在 M4 评测集中），证明零先验记忆泄漏。
5. **`test_m4_t5_m5_redis_trigger_gate_logic`**：
   - 测试两组耗时输入：
     - 组 A（`render=[500, 600]`, `wall=[25000, 30000]`，占比 `2%`，P95 `< 120s`）$\to$ 断言 `decision == "OPTIONAL_SKIPPED"`；
     - 组 B（`render=[8000, 9000]`, `wall=[30000, 32000]`，占比 `> 20%`）或 P95 `>= 120000` $\to$ 断言 `decision == "REQUIRED"`。
6. **`test_m4_t6_end_to_end_eval_and_report_m4_pipeline`**：
   - 用 `FakeVLM` 在 2 个长文档样本上跑通 `eval_m4.py` 的 `B0, B1, B2, M3` 四组评测（测试 `--resume` 幂等跳过），并调用 `report_m4.py` 生成 `report.md` 与 `bad_cases.jsonl`。
   - 断言 `report.md` 中包含 `H1_VERDICT:`、`M5_REDIS_TRIGGER:`、召回率漏斗表与防污染审计表，且 M1–M3 原有 51 项测试 100% 零回归。
