# Milestone 1 施工进度清单

- [x] Step 1: 虚拟环境 `.venv` 初始化与基础配置
  - [x] 创建 `pyproject.toml`
  - [x] 创建 `.env.example`（阿里云百炼 Qwen-VL / Moonshot Kimi）
  - [x] 依赖安装完成并生成 `requirements.lock`
- [x] Step 2: 错误类与核心数据契约
  - [x] `zoomdoc/errors.py`
  - [x] `zoomdoc/config.py`
  - [x] `zoomdoc/schemas.py`
- [x] Step 3: 视觉几何引擎与坐标换算测试
  - [x] `zoomdoc/vision/geometry.py`
  - [x] `tests/test_geometry.py` (5/5 PASSED)
- [x] Step 4: 网格拼图与页面编号映射
  - [x] `zoomdoc/vision/grid.py`
  - [x] `tests/test_grid.py` (2/2 PASSED)
  - [x] `tests/test_page_map.py` (3/3 PASSED)
- [x] Step 5: PDF 多级渲染与高精度局部放大 (Zoom)
  - [x] `zoomdoc/vision/render.py`
  - [x] `zoomdoc/vision/zoom.py`
  - [x] `tests/test_zoom.py` (1/1 PASSED)
- [x] Step 6: 结构化输出解析器与 OpenAI 兼容 VLM 客户端
  - [x] `zoomdoc/llm/json_parser.py`
  - [x] `tests/test_json_parser.py` (4/4 PASSED)
  - [x] `zoomdoc/llm/vlm_client.py`
- [x] Step 7: 可观测性、提示词工程与三阶段 Agent
  - [x] `zoomdoc/observability.py`
  - [x] `zoomdoc/prompts.py`
  - [x] `zoomdoc/agents/base.py`
  - [x] `zoomdoc/agents/seeker.py`
  - [x] `zoomdoc/agents/inspector.py`
  - [x] `zoomdoc/agents/synthesizer.py`
  - [x] `tests/fake_vlm.py`
  - [x] `tests/test_agents.py` (4/4 PASSED)
- [x] Step 8: LangGraph 状态图与状态机编排
  - [x] `zoomdoc/graph/state.py`
  - [x] `zoomdoc/graph/nodes.py`
  - [x] `zoomdoc/graph/builder.py`
  - [x] `scripts/make_synthetic_pdf.py`
  - [x] `tests/test_graph_e2e.py` (5/5 PASSED: 场景 Z, S, P, B, N)
- [x] Step 9: 命令行运行脚本
  - [x] `scripts/run_m1.py`
- [x] Step 10: Milestone 1 验收闭环测试
  - [x] `python -m compileall -q zoomdoc scripts tests` (Exit code 0)
  - [x] `python -m pytest -q` (24 passed in 4.77s, Exit code 0)


# Milestone 2 施工进度清单

- [x] Step 0: Git 仓库初始化与 M1 基线提交
  - [x] 配置 `.gitignore` 排除虚拟环境、测试缓存、日志和引用
  - [x] `git init` 并创建 baseline commit
- [x] Step 1: LangGraph 状态机探针测试
  - [x] 创建 `tests/test_langgraph_probe.py`
  - [x] 验证 Pydantic state 的 `Annotated[list, operator.add]` 与自定义 reducer
  - [x] 验证节点函数内部以 mapped input 独立 invoke 编译子图
  - [x] Pytest 验证输出：
    ```
    .........................                                                [100%]
    25 passed in 4.04s
    ```
- [x] Step 2: 基础层契约修改与透传
  - [x] `zoomdoc/agents/base.py` 增加 `usage_sink` 全量计量与 `namespace`
  - [x] `zoomdoc/observability.py` `Span` 计时能力、`namespace` 支持与 base64 泄漏检测
  - [x] `zoomdoc/schemas.py` 扩展 `InspectorAnswerM2`, `InspectorOutputM2`, `FinalResultM2`, `UsageTotals`, `CitedRegion`
  - [x] `zoomdoc/config.py` 与 `.env.example` 增加 M2 超参数
  - [x] `zoomdoc/prompts.py` 增加 `INSPECTOR_PROMPT_M2`, `SYNTHESIZER_TEXT_PROMPT`, `render_evidence_block`
  - [x] `zoomdoc/agents/inspector.py`, `seeker.py`, `synthesizer.py` 透传 `usage_sink` 与 `namespace`
  - [x] Pytest 验证输出（25/25 PASSED）：
    ```
    .........................                                                [100%]
    25 passed in 4.36s
    ```
- [x] Step 3: `graph/states.py` 状态模型与单元契约测试
  - [x] 实现 `EvidenceReport`, `CriticVerdict`, `SupervisorState`, `InspectorSubState`
  - [x] 实现 `merge_usage` 与 `usage_from_sink`
  - [x] 实现 `EvidenceReport` 防图像/Base64/路径泄漏递归校验器
  - [x] 编写 `tests/test_m2_units.py` 覆盖 reducer、sink 统计、泄漏拦截与字段断言
  - [x] Pytest 验证输出（30/30 PASSED）：
    ```
    ..............................                                           [100%]
    30 passed in 4.25s
    ```
- [x] Step 4: `graph/critic.py` 确定性规则评审与真值表测试
  - [x] 实现 `evaluate_report` 6 分支确定性评审函数
  - [x] 实现 `critic_node` 状态机节点与无图文本 trace 记录
  - [x] 单元测试覆盖 6 种判决分支（`budget_exhausted`, `need_more_pages`, `no_answer`, `invalid_reference`, `low_confidence`, `ok`）
  - [x] Pytest 验证输出（31/31 PASSED）：
    ```
    ...............................                                          [100%]
    31 passed in 4.78s
    ```
- [x] Step 5: `graph/inspector_subgraph.py` 子图状态机与隔离测试
  - [x] 实现 `prepare_node`, `inspect_node`, `route_inspect`, `zoom_node`, `pack_report_node`
  - [x] 实现 `build_inspector_subgraph` 与 `run_inspector_round` 包装函数
  - [x] 验证子图内部执行 zoom 与嵌套裁剪，并仅返回 `EvidenceReport`
  - [x] 单元测试 `test_inspector_subgraph_isolated_round` 验证子图独立闭环
  - [x] Pytest 验证输出（32/32 PASSED）：
    ```
    ................................                                         [100%]
    32 passed in 4.72s
    ```
- [x] Step 6: `graph/supervisor.py` 主图编排与真值表测试
  - [x] 实现 `decide_next` (R0–R8 完整确定性规则)
  - [x] 实现 `ingest_node`, `supervisor_node`, `route_from_supervisor`, `seeker_node`, `inspector_subgraph_node`, `synthesizer_text_node`, `finalize_node`
  - [x] 实现 `build_supervisor_graph` 与 `run_m2`
  - [x] 单元测试 `test_decide_next_truth_table` 覆盖 R0–R8 各分支条件
  - [x] Pytest 验证输出（33/33 PASSED）：
    ```
    .................................                                        [100%]
    33 passed in 4.79s
    ```
- [x] Step 7: `tests/test_milestone2.py` 端到端验收测试（A1–A7）
  - [x] A1: 两次裁剪与嵌套放大的上下文隔离主用例（零图像路径/Base64 泄漏、字段正交断言）
  - [x] A2: 置信度不足（`low_confidence`）由 Critic 拦截并注入 Feedback 触发 r2 重裁
  - [x] A3: 子图步数耗尽（`sub_step >= max_sub_steps`）安全退出
  - [x] A4: `seek` 反馈回环触发 Seeker 二次粗筛
  - [x] A5: 格式错误重试的 Token 全量累计（修复 D1 缺陷回归）
  - [x] A6: `EvidenceReport` 防泄漏校验拦截
  - [x] A7: 纯文本 Synthesizer 兜底回环测试与 M1 零回归断言
  - [x] Pytest 验证输出（40/40 PASSED）：
    ```
    ........................................                                 [100%]
    40 passed in 8.15s
    ```
- [x] Step 8: `scripts/run_m2.py` CLI 脚本与 Milestone 2 全量验收交付
  - [x] 编写 `scripts/run_m2.py` 命令行调用入口
  - [x] 执行 `python -m compileall -q zoomdoc scripts tests` (Exit code 0)
  - [x] 执行 `python -m pytest -q` (40 passed in 8.02s, Exit code 0)

## Milestone 2 交付报告

### 1. 语法检查与单元测试退出码
- `python -m compileall -q zoomdoc scripts tests`: 退出码 **0**
- `python -m pytest -q` 完整原始输出：
```
........................................                                 [100%]
40 passed in 8.02s
```

### 2. A1 隔离测试审计数据
- `tests/test_milestone2.py::test_a1_context_isolation_and_nested_zoom`:
  - `supervisor_state.json` 文件大小：**7565 字节**
  - `supervisor_state.json` 中 `data:image`、`base64,`、`/sub/` 出现次数：**0 次**
  - `trace.jsonl` 中 `namespace == "inspector_sub/r1"` 且 `node == "zoom"` 的事件条数：**2 条**（均记录真实毫秒级耗时）

### 3. 新增 / 修改文件清单与行数
- **基础与契约层（修改）**：
  - `zoomdoc/agents/base.py`: 95 行（新增 `usage_sink` 全量计量与 `namespace`）
  - `zoomdoc/agents/inspector.py`: 143 行（支持 M2 prompt、adapter 与透传）
  - `zoomdoc/agents/seeker.py`: 94 行（透传 `usage_sink` 与 `namespace`）
  - `zoomdoc/agents/synthesizer.py`: 81 行（透传 `usage_sink` 与 `namespace`）
  - `zoomdoc/observability.py`: 86 行（`Span` 真实耗时统计与 base64 泄漏拦截）
  - `zoomdoc/schemas.py`: 165 行（新增 `UsageTotals`, `CitedRegion`, `InspectorOutputM2`, `FinalResultM2`）
  - `zoomdoc/config.py`: 53 行（新增 M2 预算与置信度参数）
  - `zoomdoc/prompts.py`: 201 行（新增 `INSPECTOR_PROMPT_M2`, `SYNTHESIZER_TEXT_PROMPT`, `render_evidence_block`）
  - `.env.example`: 55 行（同步配置项）
- **状态机与子图层（新增）**：
  - `zoomdoc/graph/states.py`: 155 行（`SupervisorState`, `InspectorSubState`, `EvidenceReport`, `CriticVerdict`, reducers）
  - `zoomdoc/graph/inspector_subgraph.py`: 322 行（子图定义、局部裁剪、`run_inspector_round` 包装）
  - `zoomdoc/graph/critic.py`: 126 行（6 分支确定性规则评审与节点实现）
  - `zoomdoc/graph/supervisor.py`: 433 行（主图拓扑、`decide_next` R0–R8、`run_m2` 入口）
  - `scripts/run_m2.py`: 59 行（CLI 运行脚本）
- **测试与验证层（新增）**：
  - `tests/test_langgraph_probe.py`: 105 行（Pydantic reducer 与子图独立 invoke 探针）
  - `tests/test_m2_units.py`: 384 行（状态防泄漏、reducer、critic 6 分支、decide_next R0–R8 真值表）
  - `tests/test_milestone2.py`: 421 行（A1–A7 完整验收套件）

### 4. 实施偏离说明
- **无偏离**：完全严格依照《Milestone 2 施工指导手册》的类名、函数签名、拓扑与隔离规范实现，M1 的 24 个基线测试未改动任何一行且 100% 保持通过。
