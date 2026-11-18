# FoveaDoc (ZoomDoc-Agent) 关键技术九问、业务五问与源码绑定学习路线

> **认识论与证据标注规范**：
> 1. 本文档严格拒绝“业内常用 / 效果更好 / 提高效率”等空泛表述；所有“为什么”均按 **「具体物理问题 $\to$ 作用机理 $\to$ 引入代价 $\to$ 物理验证指标」** 四元组展开。
> 2. 凡由本仓库源码行号、离线单元测试或受控基准评测 JSON 直接支撑的结论，统一标注 **`[实测证据]`**；凡涉及跨团队协作维护、外部替代技术栈运维成本或企业投产流程推演等无仓库历史日志支撑的判断，统一显式标注 **`[合理推断]`**。

---

## 第一部分：关键技术点“技术九问”（覆盖 Agent 工程与多模态算法六大支柱）

---

### 技术点一：LangGraph 主从状态机与双物理隔离子图（`Supervisor + NavigatorSubgraph / InspectorSubgraph`）

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：50–100 页长文档视觉推理需经历「目录选章 $\to$ 缩略图筛页 $\to$ 多次局部放大 $\to$ 跨章回溯」多轮闭环。若使用单层对话历史，前序轮次产生的 `1024px` 巡检图与局部放大切片（单图约 `1,200` 视觉 Tokens，Base64 体积 `0.5–1.5 MB`）会持续驻留在全局上下文中，造成视觉注意力稀释、Token 线性累加膨胀及状态持久化文件体积失控。
   - **作用机理**：将主图 `SupervisorState` 限制为纯文本控制流态，将局部目录展开与局部多图放大封装进函数调用级私有子图 `NavigatorSubState` 与 `InspectorSubState`；子图函数返回时局部状态随 Python 栈帧立即释放，仅将纯文本 `EvidenceReport` 与归一化坐标 `CitedRegion` 映射回主图。
   - **引入代价**：需要维护主图与两个子图共 3 套状态 Schema 及显式出入参转换代码（增加约 `350` 行编排代码）。
   - **验证手段**：`[实测证据]` `tests/test_milestone5.py` 与 `zoomdoc/server/app.py:L532-L542` 扫描全部运行落盘的 `supervisor_state.json`，断言 `data:image`、`base64,`、`/sub/` 出现次数恒为 `0`。

2. **不用它行不行？最简单的可行方案是什么？**
   - 可以运行，但仅适用于页数 $\le 10$ 页的短文档（本项目 Milestone 1 即采用无子图的扁平单图流水线 `zoomdoc/graph/builder.py`）。
   - **最简可行方案**：写一个普通 Python `while step < max_steps:` 循环，维护一个全局 `messages: list[dict]`，把模型每次请求的裁剪图不断 `append` 到 `messages` 末尾直到输出答案。代价是在跨章回溯场景下上下文图像数突破 `15+` 张，触发上游 VLM 单次多图上限报错或耗时翻倍。

3. **有哪些替代方案？**
   - 方案 A：普通 Python `while` 循环手工编排工作流（无框架状态图）；
   - 方案 B：LangChain / LangGraph 预置高层 `create_react_agent`（单消息列表 + ToolNode）；
   - 方案 C：AutoGen / CrewAI 多智能体自由对话群聊（GroupChat）；
   - 方案 D：固定单向 DAG 流水线（`Navigator -> Seeker -> Inspector -> End`，不允许回溯）。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本（Token 开销）**：`[实测证据]` 相比无隔离全页滑窗 `B0`（`16,532` Prompt Tokens），本方案将平均消耗压至 `9,056` Tokens（节省 `45.22%`）；相比单列表 `create_react_agent` 累积历史图片，`[合理推断]` 在触发 2 轮回溯时可少传 `4–8` 张失效高清图（节省约 `5,000–10,000` Tokens）。
   - **效果（准确率）**：`[实测证据]` 单向无回溯流水线遇到 `backtrack_trap` 跨章引用陷阱题（如 `case_002`, `case_005`）一旦首章未命中即直接失败；带 `R4/R7b` 树回溯的主从状态机在 $N=16$ 基准上达成 `100.0% (16/16)`。
   - **延迟**：`[实测证据]` 子图本地内存调用开销 `< 2 ms`，远低于网络 VLM 延迟（单次 `5–18 s`）；避免历史冗余图片重传反而降低了单步首字延迟（TTFT）。
   - **稳定性**：`[实测证据]` 相比自由对话群聊（AutoGen）易陷入智能体互相客套或死循环，本方案通过 `decide_next` 纯函数规则表 + `recursion_limit` 实现 `112 + 36 = 148` 次实测批跑 `errors = 0`。
   - **可控性**：`[实测证据]` 每一跳转移由 `zoomdoc/graph/supervisor.py:L59-L194` 的 `R0–R8` 确定性优先级表裁决，可对任意状态快照做纯单元测试（`tests/test_m2_units.py`）。
   - **安全性**：`[实测证据]` `EvidenceReport`（`zoomdoc/graph/states.py:L84-L106`）通过 Pydantic 后置校验器在类型层面禁止任何图像字节或 `.png` 路径回流主图。
   - **团队维护成本**：`[合理推断]` 相比纯 `if-else/while` 脚本，新成员需花约半天理解 LangGraph `Annotated` Reducer 机制，但换来了统一的状态序列化、清晰的节点职责边界与前端拓扑图 `1:1` 可视化映射能力。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：核心多智能体编排运行时层（Agent Orchestration Runtime Layer）。
   - **输入**：`question: str`, `pdf_path: str`, `Settings`, 可选 `on_event` 回调。
   - **输出**：`(FinalResultM3, SupervisorState)` 元组，以及持久化落盘的 `result.json`、`supervisor_state.json`、`trace.jsonl`。
   - **核心依赖**：`langgraph.graph.StateGraph`, `pydantic.BaseModel`, `zoomdoc/agents/*`, `zoomdoc/vision/*`。
   - **精确调用链**：`run_m5`（`supervisor.py:L869`）$\to$ `build_supervisor_graph`（`L700`）$\to$ `ingest_node` $\to$ `supervisor_node`（调用 `decide_next` `L59`）$\to$ `tree_builder_node` $\to$ `navigator_subgraph_node`（调用 `run_navigator_round` `navigator_subgraph.py:L231`）$\to$ `seeker_node` $\to$ `inspector_subgraph_node`（调用 `run_inspector_round` `inspector_subgraph.py:L435`）$\to$ `critic_node`（调用 `evaluate_report` `critic.py:L14`）$\to$ `synthesizer_text_node` / `finalize_node`。

6. **它在 Agent / 系统里扮演什么角色？**
   - 它是整个 Agent 系统的**控制中枢与内存隔离边界（Control Plane & Isolation Harness）**，决定了哪个子智能体在何时被唤醒、允许看哪些局部数据、何时回溯以及何时终止。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：让百页级复杂文档问答在可控 Token 成本与延迟下稳定收敛，且每一步决策可审计、不爆显存/上下文。
   - **验证指标与实测证据**：`[实测证据]`
     1. 物理泄漏计数：`supervisor_state.json` 中 Base64 与 `/sub/` 路径出现次数 = `0`（见 `eval/m4/runs/*/supervisor_state.json`）；
     2. 端到端成功率：`manifest_full16.jsonl` 上 `16/16` 零异常收敛，`P95` 延迟 `103.6 s < 120 s`（见 `eval/m4/report.md`）；
     3. 跨章节回溯命中率：`test_m3_t3_cross_section_tree_backtracking` 离线单测 `100%` 通过。

8. **如果替换或移除它，系统在准确率、成本、性能、可靠性、安全性或体验上可能怎样变化？依据是什么？**
   - 若退化为无树导航与无子图回溯的滑窗流水线（对应基线 `B0` / `B1`）：`[实测证据]` 在 `eval/m4/report.md` 中，准确率从 `100.0%` 跌至 `31.2%`（`B0`）或 `43.8%`（`B1`），平均 Prompt Tokens 从 `9,056` 暴涨至 `16,532–17,385`（增加 `82%–92%`），P50 延迟从 `70.6 s` 恶化至 `170.2 s`。
   - 若移除子图隔离改为全局状态挂载切片：`[实测证据]` 将直接触发 `EvidenceReport._no_leak` 校验器抛出 `ValueError: image payload leaked`。

9. **它会怎样失败？超时、无结果、错误参数、模型异常、权限不足、数据不一致时如何处理？**
   - **步数/轮次超限**：当反复回溯达到 `step >= max_steps (16)` 时，`decide_next` 规则 `R1` 拦截，若有历史已答证据则路由至 `synthesizer` 兜底汇总，否则进入 `finalize` 返回 `status="budget_exhausted"`，绝不死循环。
   - **模型输出格式错误**：`call_with_schema`（`agents/base.py:L17-L112`）捕获 `ValidationError` 自动携带错误原因局部重试最多 `max_retry (2)` 次，记录 `status="retry"` 日志；仍失败则抛出 `AgentOutputError`。
   - **空 PDF / 损坏 PDF**：`ingest_node` 检测 `total_pages == 0` 直接短路至 `finalize` 返回 `no_answer`；损坏文件由 PyMuPDF 抛出异常并在入口转换为清晰状态码。
   - **重跑数据不一致**：`_prepare_clean_run_dir`（`supervisor.py:L784`）在启动前清空旧 `sub/` 与 `trace.jsonl`，并通过 `_atomic_persist_json` 原子替换写入结果文件。

---

### 技术点二：无向量层级目录树 `PageIndex` 与按需惰性渲染（Lazy Rendering）

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：100 页 PDF 若在启动阶段全量渲染为 200 DPI PNG，仅光栅化 I/O 即耗时数秒并产生数百 MB 磁盘文件；若采用传统固定字数切块（Chunking）+ 向量数据库 Embedding 检索，会切断跨页大表格表头，且对无文本层的纯位图扫描页完全漏检。
   - **作用机理**：启动阶段零图像渲染，通过 `build_document_tree`（`zoomdoc/index/tree.py:L181`）按「原生 PDF 书签 TOC $\to$ 字体字号启发式标题聚类 $\to$ 均匀分块兜底」三级策略构建章节树，并将跨度 $>20$ 页的大节点强制切分为 $\le 20$ 页叶子节点；当总节点数 $\le 40$ 时启用自适应单步目录展平，由纯文本 `Navigator` 一次锁定目标叶子页区间，随后 `seeker_node` **仅按需渲染被选中的这 $\le 20$ 页**。
   - **引入代价**：对完全无书签且无文本层的扫描文档会退化为 20 页均匀分块树；构建树需一次轻量级文本/目录解析（未缓存时约 `20–80 ms`）。
   - **验证手段**：`[实测证据]` `tests/test_milestone3.py::test_m3_t2_long_pdf_100_pages_e2e` 物理断言 100 页长文档运行结束时 `len(state.pages) == 12`（其余 `88` 页零图像渲染），且 `manifest_full16` 与 `manifest_m6` 上 `Nav Recall` 均达 `100.0%`。

2. **不用它行不行？最简单的可行方案是什么？**
   - 对 20 页以内短文档完全可以不用（`supervisor.py:L232` 对 $\le 20$ 页且 `use_tree_index=False` 时保留全页预渲染兼容路径）。
   - **百页文档最简替代方案**：把全书每页抽纯文本跑 `BM25` 关键词检索取 Top-6 页再渲染图片，但遇到无文本扫描页或“问题用同义词描述章节而表格里只有数字代码”的场景会直接漏召回。

3. **有哪些替代方案？**
   - 方案 A：全文档滑窗缩略图暴力扫描（本项目基线 `B0` / `B1`）；
   - 方案 B：文本切片 + Embedding 向量数据库（Milvus / Chroma / FAISS）稠密检索；
   - 方案 C：多模态视觉向量检索（ColPali / ColQwen 将每页图像编码为多向量索引）。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本**：`[实测证据]` `Navigator` 纯文本选章单次仅消耗约 `600–900` Prompt Tokens，无需额外部署 Embedding 模型或 GPU 视觉索引服务。
   - **效果**：`[实测证据]` 在 `manifest_full16` 上，`PageIndex` 章节召回率 `100.0%` + `Seeker` 召回率 `100.0%`，显著高于滑窗基线 `B0`（`75.0%`）与 `B1`（`81.2%`）。
   - **延迟**：`[实测证据]` 树构建冷启动 `< 100 ms`，命中缓存 `< 5 ms`；相比 ColPali 对 100 页 PDF 逐页跑视觉编码器建索引（`[合理推断]` 需数秒至十余秒 GPU 推理），本方案在单篇临时上传即问即答（Ad-hoc QA）场景下首问延迟极低。
   - **稳定性**：`[实测证据]` 纯本地确定性算法构建树，不依赖外部向量库网络连接，零召回随机抖动。
   - **可控性**：严格保证任意叶子节点跨度 `[start_page, end_page] <= 20`，从结构上确保下游 `Seeker` 缩略图网格不会过密失真。
   - **安全性**：`[实测证据]` `sanitize_summary_text`（`tree.py:L33-L45`）在生成节点摘要时正则剥离 `ignore previous instructions` 等间接提示词注入文本。
   - **团队维护成本**：`[合理推断]` 省去了维护 Milvus/Qdrant 向量库集群、切片重叠超参与 Embedding 模型版本升级的运维负担。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：结构化文档索引与粗粒度路由层（Document Indexing & Coarse Routing Layer）。
   - **输入**：`pdf_path: str`, `max_leaf_pages: int = 20`。
   - **输出**：`DocumentTree`（含嵌套 `DocTreeNode` 树、页码区间与脱敏摘要），经 `Navigator` 转化为 `candidate_pages: list[int]`（长度 $\le 20$）。
   - **依赖**：`pymupdf (fitz)`、`zoomdoc/schemas.py::DocumentTree`。
   - **调用链**：`tree_builder_node`（`supervisor.py:L287`）$\to$ `get_cached_or_build_tree`（`cache.py:L265`）$\to$ `build_document_tree`（`tree.py:L181`）$\to$ `run_navigator_round`（`navigator_subgraph.py:L231`）$\to$ `collect_candidate_pages`（`tree.py:L78`）$\to$ `seeker_node` 按需调用 `get_cached_or_render_pages`。

6. **它在 Agent / 系统里扮演什么角色？**
   - 扮演 **“先看目录再翻书”的漏斗第一级剪枝器**，负责把 $O(N)$ 全书页空间压缩至 $O(1)$（$\le 20$ 页）局部候选窗口，使后续重型多模态感知聚焦在正确章节。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：支持用户临时上传一份百页招股书或合规手册后秒级建树开问，无需漫长向量化预处理，同时省去 `80%+` 无关页面的光栅化与视觉 Token 费用。
   - **验证指标与实测证据**：`[实测证据]` `Nav Recall = 100.0% (16/16)`，`tree_latency_ms` 缓存命中后 `< 5 ms`，渲染耗时占总端到端时间比例 $\rho_{\text{render}}$ 仅 `1.6%–2.3%`（见 `PROGRESS.md` 与 `eval/m4/report.md`）。

8. **如果替换或移除它，系统怎样变化？依据是什么？**
   - `[实测证据]` 移除树索引改用全书滑窗扫描（`B0` / `B1`）后，Prompt Token 消耗从 `9,056` 上升至 `16,532+`，P50 耗时从 `70.6 s` 恶化至 `170.2 s`，且因过多无关页分散注意力导致 `Seek Recall` 从 `100.0%` 跌至 `75.0%–81.2%`。

9. **它会怎样失败？如何处理？**
   - **无原生书签 TOC**：自动降级到第二级「全书字体字号分布统计启发式标题提取」（`tree.py`）；
   - **既无书签又无文本层（纯扫描 PDF）**：自动降级到第三级「固定 20 页均匀分块叶子树」，保证 `1..total_pages` 连续全覆盖绝不抛错（已由 `test_m3_t1_toc_and_heuristic_tree_coverage` 单测验证）；
   - **Navigator 选错章节**：`Seeker` 返回空或 `Inspector` 返回 `need_more_pages` 时，主图 `R4/R7b` 自动将已选节点加入排除列表回溯重选，并记录 `navigator_sub/r2` 轨迹日志。

---

### 技术点三：多锚定带各向异性拓扑凹面镜（Anisotropic Topological Foveation v2.0）

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：在大型财务矩阵表中，目标单元格（如第 15 行第 4 列）与顶栏列头（`FY2022..FY2026`）、左侧行名（`Intraday Settlement`）相距大半页。传统局部矩形裁剪在放大目标单元格时把表头与行名切掉；当首步定位框出现 1 行偏差或一行有多个同格式编码时，VLM 失去坐标轴参照直接张冠李戴。而传统径向鱼眼镜头会把水平/垂直表格线扭曲成弧线，破坏行列对齐。
   - **作用机理**：构造一维可分离分段严格单调重采样 $T(x,y) = (g_x^{-1}(x), g_y^{-1}(y))$：先通过 `detect_table_anchor_bands` 墨水密度投影自动锁定左侧行名分隔线 $x_{\text{stub}}$ 与页中表头横线带 $[ty_1, ty_2]$ 作为高保真锚定带，再分配高权重放大焦点框 $[x_1, x_2] \times [y_1, y_2]$，并对中间冗余过道区做高倍线性折叠压缩，同时满足宽跨度非反向压缩不变量 $s_{\text{focus}}^{\text{eff}} = \max(s_{\text{focus}}, \min(0.95, \Delta x / W))$。由于水平与垂直映射正交解耦，表头列与焦点列保持 `100%` 垂直像素共线，行名与焦点行保持 `100%` 水平像素共线。
   - **引入代价**：单次变形需执行约 `15–25 ms` 的 NumPy 双线性插值计算，且因保留了表头与行名带，单张放大图平均比极窄单格裁剪多消耗约 `10.1%` Prompt Tokens（`9,056` vs `8,222`）。
   - **验证手段**：`[实测证据]` `tests/test_milestone5.py::test_m5_t1` 与 `tests/test_milestone6.py::test_m6_t1` 物理断言变形后表头参考红线与焦点红线像素列坐标误差为 **`0 px`**，分段雅可比正定，且 `fovea_box_to_page_box` 逆映射往返坐标误差 $< 10^{-4}$；在纯扫描矩阵 `m6_case_007` 中验证了首步框下偏 1 行时的同图自纠偏。

2. **不用它行不行？最简单的可行方案是什么？**
   - 在单页只有一张简单小表且首步定位框零偏差时，不用它也能做：最简方案是**局部矩形硬裁剪（Linear Crop）+ 同时在上下文里保留整页缩略图**（即本项目消融组 `M5_crop`）。但一旦遇到密集多行矩阵且首步框偏了 1 行（如 `m6_case_007`），局部矩形裁剪图里没有行名参照，模型就会把错行数据当成正确答案。

3. **有哪些替代方案？**
   - 方案 A：局部矩形硬裁剪 + 双三次插值放大（`LinearCrop`）；
   - 方案 B：直接将整页渲染为 `2048×2048` 超高清大图整张送入 VLM；
   - 方案 C：拼贴画方案（Three-Patch Collage：分别裁出表头条、行名条、目标格三张碎片拼在一起）；
   - 方案 D：各向同性径向鱼眼变形（Radial Fisheye / Thin-Plate Spline）。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本**：`[实测证据]` 相比整页 `2048×2048` 超高清输入（单页即占 `~5,200` 视觉 Tokens），自适应长宽比凹面镜画布（如 `1024×768`）仅占约 `~1,000` 视觉 Tokens，单图节省约 `80%` 像素预算。
   - **效果**：`[实测证据]` 相比径向鱼眼扭曲直线网格，可分离正交重采样保证表头列与数据列零像素偏移；相比三碎片拼贴画（方案 C，`[合理推断]` 若表头裁宽了而单元格裁窄了会导致上下列严重错位），连续单调映射在数学上消除了拼接错位可能。
   - **延迟**：`[实测证据]` 纯 NumPy 向量化两步一维插值（`fovea.py:L375-L389`）在 CPU 上单次耗时仅 `15–25 ms`，无 GPU 依赖。
   - **稳定性**：引入宽跨度非反向压缩不变量后，无论 VLM 给出极小单格框还是横跨 `95%` 页宽的整行框，均不会出现空白边缘反向放大退化。
   - **可控性**：`[实测证据]` 映射由不超过 6 个显式单调节点 `(u_knots, x_knots)` 唯一决定，可保存为 `.meta.json` 并在前端 `Fovea Lens Lab` 实时绘制分段斜率曲线。
   - **安全性**：纯确定性像素重采样数学运算，不引入任何生成式图像修复（GAN/Diffusion Hallucination）伪造笔画风险。
   - **团队维护成本**：`[实测证据]` 核心实现内聚在单文件 `zoomdoc/vision/fovea.py`（549 行，零第三方重型 CV 库依赖，仅用 `Pillow + NumPy`），易于跨平台部署。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：高分辨率主动视觉感知算子层（Visual Perception Operator Layer）。
   - **输入**：`PageAsset`（`200 DPI` 源图）、归一化目标框 `NormBBox`、`Settings`（`focus_share`, `header_ratio`, `stub_ratio`, `fovea_auto_anchors`, `fovea_adaptive_aspect`）。
   - **输出**：变形后的 PNG 图像、`.meta.json` 节点文件及包含逆映射元数据的 `CropAsset`。
   - **依赖**：`numpy`, `PIL.Image`, `zoomdoc/vision/geometry.py`。
   - **调用链**：
     - 推理链路：`zoom_node`（`inspector_subgraph.py:L205`）$\to$ `crop_zoom` $\to$ `foveate_zoom`（`fovea.py:L450`）$\to$ `detect_table_anchor_bands`（`L176`）& `compute_adaptive_fovea_size`（`L260`）$\to$ `fovea_warp_image`（`L327`）$\to$ `build_1d_fovea_map`（`L16`）；报告打包时由 `pack_report_node` 调用 `fovea_box_to_page_box`（`L421`）还原全页坐标。
     - 交互预览链路：`POST /api/vision/fovea-preview`（`server/app.py:L313`）直接调用上述算子返回实时对比图。

6. **它在 Agent / 系统里扮演什么角色？**
   - 它是多模态 Agent 的**“带上下文锚定的光学变焦显微镜”**——不属于通用文本 Agent 框架组件，而是专门解决文档视觉推理空间拓扑断裂的核心算法算子。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：使输出的局部核验截图自带同行名称与同列表头，既防止模型跨行/跨列读串，又让业务审计员在前端看单张切片即可人工复核行列对应关系。
   - **验证指标与实测证据**：`[实测证据]` ① 正交共线像素误差 `0 px`；② 逆映射最大坐标误差 `<= 2.5e-4`；③ `manifest_full16` 准确率 `100.0% (16/16)`；④ `manifest_m6` 纯扫描子集准确率 `83.3% (5/6)` 且成功纠偏 `m6_case_007` 单行框偏移。

8. **如果替换或移除它，系统怎样变化？依据是什么？**
   - 若彻底移除放大能力（`B2` / `NoZoom`）：`[实测证据]` 在 `manifest_full16` 上准确率从 `100.0%` 暴跌至 `37.5%`，在 3.8pt 密集矩阵 `manifest_m6` 上直接跌至 **`0.0% (0/12)`**。
   - 若替换为普通局部矩形裁剪（`LinearCrop`）：`[实测证据]` 在简单单表页上准确率持平（但失去单图自包含表头可读性），在密集矩阵首步框发生单行偏移时（`m6_case_007`）因看不到左侧行名直接误读相邻行。

9. **它会怎样失败？如何处理？**
   - **扫描件重度倾斜（Skew $> 5^\circ$）**：一维正交网格变形不再与倾斜表格行平行，同行左右文字会出现垂直错位（已在 `README.md` 有效性威胁中明确声明边界，需前置霍夫旋转校正）。
   - **非法或极小/越界框输入**：`pad_and_clamp` 强制保证最小归一化边长 `zoom_min_norm_side` 并将坐标钳制在 `[0, 1]`；若 `src_len < 2` 或区间反转，`build_1d_fovea_map` 立即抛出显式 `ValueError` 快速失败。
   - **无页中横线表头**：`detect_table_anchor_bands` 未检出显著水平表头线时优雅返回 `sec_band_y = None`，无缝回退至标准页顶锚定带模式。

---

### 技术点四：S-BOED Token 对数概率香农熵主动感知门禁与去确认偏置机制

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：VLM 在 `1024px` 全页图上读 4pt 微字时，经常把 `'ZD-2664-DX'` 错看成 `'ZD-2664-0X'`，却在 JSON 里填 `"confidence": 0.95` 直接结束回答（言语过度自信），导致传统架构 `M3` 的放大触发率仅 `31.2%`。此外，如果拦截后把模型首轮瞎猜的字符串写进放大提示词里让它“核对”，模型在第 2 轮会产生强烈的先验确认偏置继续答错。
   - **作用机理**：仅在 `Inspector` 调用时开启 `logprobs=True, top_logprobs=5`，通过 `compute_answer_span_entropy`（`vlm_client.py:L66`）精确定位 JSON 中 `"answer"` 值域对应的实质字符 Token，计算归一化 Top-5 香农熵最大值 $H_{\max}(Y_{\text{ans}})$；当 `zoom_count == 0` 且满足 $H_{\max} > \tau_H (0.15\text{ bits})$ 或命中细粒度编码模式时，`inspect_node` 强制驳回首轮直答并合成**不含任何未验证盲猜值**的中性 `InspectorZoom` 指令转入放大回路。
   - **引入代价**：被拦截的微字样本必须多走 1 轮高清视觉复核（增加 1 次 VLM 调用，耗时增加约 `12–18 s`，Prompt Tokens 增加约 `1,200–1,800`）。
   - **验证手段**：`[实测证据]` 对比正交消融组 `M5_no_sboed` 与 `M5`（`eval/m4/ablation_report.md`）：开启 S-BOED 后 Zoom 触发率从 `50.0%` 升至 **`100.0%`**，消除了 `case_005` 未放大导致的三字符重度失真错例，准确率从 `93.8% (15/16)` 提升至 **`100.0% (16/16)`**。

2. **不用它行不行？最简单的可行方案是什么？**
   - 最简方案是**纯靠 Prompt 叮嘱模型“看不清就选 `zoom`”或者无脑对每一个问题强制放大一次**。前者实测触发率只有 `31.2%`（漏放大）；后者虽然不漏，但对“文档标题是什么”这类大字宏观题也会白白浪费一轮高清放大算力。

3. **有哪些替代方案？**
   - 方案 A：仅依赖模型输出的言语 `"confidence" < 0.8` 阈值（本项目 `M3` 基线）；
   - 方案 B：Self-Consistency 多次采样投票（设 `temperature=0.7` 采样 5 次看答案是否一致）；
   - 方案 C：训练专门的小型外挂二分类器判断是否需要放大。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本**：`[实测证据]` 单次前向解码顺带返回 `top_logprobs=5` 的额外 Token 费用为 **`0`**；而 Self-Consistency 采样 5 次会使首轮推理成本与并发负载直接乘以 `500%`。
   - **效果**：`[实测证据]` 言语置信度方案 `M3` 准确率仅 `43.8%`；S-BOED 结合细粒度编码门禁在 $N=16$ 上实现 `100.0%` 必放大覆盖与 `100.0%` 准确率。
   - **延迟**：`[实测证据]` 本地解析答案子串香农熵耗时 `< 1 ms`；仅对 `Inspector` 局部开启 `logprobs` 避免了上游 `Seeker` 解码延迟增加。
   - **稳定性**：设 `temperature = 0` 贪婪解码配合解析对数概率，结果确定可复现，不像高温采样投票存在随机波动。
   - **可控性**：熵阈值 `entropy_threshold = 0.15` bits 与正则开关 `force_zoom_on_alnum_code` 均可在 `Settings` 与前端面板独立开关调参。
   - **安全性**：剥离盲猜字符串消除了跨步幻觉自增强回路。
   - **团队维护成本**：`[合理推断]` 无需收集标注数据训练和维护额外分类小模型，升级底座 VLM 时零重训成本。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：智能体认知不确定性量化与主动感知门控层（Epistemic Uncertainty & Active Perception Gate）。
   - **输入**：OpenAI 兼容响应中的 `choice.logprobs.content` 列表与模型初稿 `InspectorAnswerM2`。
   - **输出**：`VLMUsage.max_token_entropy` 浮点数；若触发拦截则输出改写后的 `pending_zoom: InspectorZoom`。
   - **依赖**：`math.exp / math.log2`、`zoomdoc/vision/residual.py::locate_target_zoom_box`。
   - **调用链**：`OpenAICompatVLM.generate`（`vlm_client.py:L185`, 当 `collect_logprobs=True`）$\to$ `compute_answer_span_entropy`（`L66`）$\to$ `inspect_node`（`inspector_subgraph.py:L104-L185` 门禁判定与去偏置重路由）$\to$ `evaluate_report`（`critic.py:L74` 二次高熵兜底检查）。

6. **它在 Agent / 系统里扮演什么角色？**
   - 扮演 Agent 的**“元认知自知之明探针（Metacognitive Probe）”**——在模型“以为自己看清了”的时候，直接从输出层概率分布识破其底层犹豫并强制启动物理变焦。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：堵住金融/合规微字提取中最危险的“高置信度静默错读（Silent Hallucination）”。
   - **验证指标与实测证据**：`[实测证据]` 在 `manifest_full16` 上将微字题 Zoom 触发率从 `31.2%`（`M3`）/ `50.0%`（`M5_no_sboed`）拉升至 `100.0%`，端到端准确率达 `100.0%`，且每个拦截事件在 `trace.jsonl` 中留有精确 `answer_max_entropy` 数值证据（如 `m6_case_007` 实测记录 `2.295 bits`）。

8. **如果替换或移除它，系统怎样变化？依据是什么？**
   - `[实测证据]` 保持其余所有 M5 组件不变、仅关闭该门禁（即消融组 `M5_no_sboed`）：在 $N=8$ 子集上准确率从 `100.0%` 跌至 `75.0% (6/8)`，在 $N=16$ 全集上跌至 `93.8% (15/16)`（`case_005` 因未放大发生 3 字符错读连残差校验都无法挽回）。

9. **它会怎样失败？如何处理？**
   - **上游模型 API 不支持返回 `logprobs`（返回 `None`）**：`compute_answer_span_entropy` 安全返回 `(None, None)`，门禁自动平滑降级为 `force_zoom_on_alnum_code` 模式规则 + 言语置信度检查，绝不报空指针异常。
   - **纯位图扫描页触发拦截但无矢量文字可供符号定位**：`inspect_node`（`inspector_subgraph.py:L150-L176`）检测到 `locate_target_zoom_box` 返回大范围默认框（`width > 0.55`）时，自动向 VLM 发起一次轻量级紧致框视觉定位请求，拒绝盲目放大空白角落。

---

### 技术点五：多阶十字准星几何定位与光学混淆矩阵 Indel 残差校验（`zoomdoc/vision/residual.py`）

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：① 当 S-BOED 拦截首轮盲猜需要强制放大时，系统本身还没有模型给出的 `bbox_2d`，必须快速算出目标表格在哪一块区域；② 即便经过高清放大或跳过了放大，VLM 面对极小字号仍可能发生单字符光学形近混淆（如 `0` 看成 `D`、`8` 看成 `B`、或漏读 1 个字符把 `ZD-1763-BV` 读成 `ZD-170-BV`），而电子版 PDF 底层往往自带精确矢量字符流，纯视觉方案白白浪费了这层零误差真值。
   - **作用机理**：
     - **定位侧（`locate_target_zoom_box`）**：按四阶优先级检索——Tier 0 在密集矩阵页（编码数 $\ge 4$）联合问题引号中的行名与列头做行列坐标十字交集；Tier 1/2/3 做词簇匹配与关键词锚定；Tier 4 对零矢量纯扫描页调用 `locate_raster_dense_roi` 计算水平/垂直笔画梯度能量投影锁定密集表格区。
     - **校验侧（`verify_and_correct_answer`）**：在最终局部引用框内提取底层矢量 Token，计算带光学混淆权重的编辑距离 `optical_edit_distance`（如 `0<->D` 代价 `0.25`，单字符 Indel 代价 `0.65`）；**仅当存在唯一候选满足 $0 < d_{\text{opt}} \le 1.5$ 且领先次优候选裕度 $> 0.05$ 时才修正**，否则保持原判弃权。
   - **引入代价**：每次打包报告需在单页局部矩形内执行一次 PyMuPDF 词提取与动态规划距离计算（耗时 `< 8 ms`）。
   - **验证手段**：`[实测证据]` `tests/test_milestone5.py::test_m5_t3` 与 `test_r6` 物理验证 5 类典型光学错读与单字符漏读 `100%` 精准纠偏且对等距歧义候选严格弃权；在 `M5_no_sboed` 实测（`eval/m4/ablation_report.md`）中真实触发并救回 `2` 道错题（`case_001` 与 `case_002`）。

2. **不用它行不行？最简单的可行方案是什么？**
   - 不用也能跑（如消融组 `M5_crop` 关闭了残差校验）。最简替代方案是直接拿 PyMuPDF 提取整页全部文本塞进 Prompt 让 LLM 对照着看——但在百单元格密集大表里，整页平铺文本失去了二维行列网格对齐关系，极易把相邻行的编码混在一起。

3. **有哪些替代方案？**
   - 方案 A：外部独立 OCR 引擎（PaddleOCR / Tesseract）对裁剪图二次识别投票；
   - 方案 B：无加权标准 Levenshtein 距离最近邻强行替换；
   - 方案 C：纯视觉直读不做任何符号层后验校验。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本**：`[实测证据]` PyMuPDF 直接读取电子 PDF 内嵌字元耗时 `< 5 ms`、零额外模型推理成本；外挂 PaddleOCR 需额外部署几百 MB 检测/识别权重。
   - **效果**：`[实测证据]` 标准无加权编辑距离中 `0->D` 与 `0->W` 距离都是 `1`，在多编码密集表里极易出现多个并列距离为 `1` 的假阳性冲突；光学混淆加权矩阵精准区分真光学错读（`d=0.25`）与无关编码（`d=1.0`）。
   - **延迟**：单页局部微框词扫描 `< 8 ms`。
   - **稳定性**：确定性动态规划算法，相同输入 `100%` 产出相同结果。
   - **可控性**：通过 `max_edit_dist = 1.5` 与次优裕度 `0.05` 双阈值严格限制最大修改幅度，杜绝篡改正常长句答案。
   - **安全性**：采用“歧义即弃权（Abstention on Tie）”保守契约，宁可不改也绝不乱改。
   - **团队维护成本**：纯 Python + NumPy 约 `200` 行无状态函数，无外部服务依赖。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：跨模态符号—视觉对齐与后验校准层（Cross-Modal Alignment & Post-Verification Layer）。
   - **输入**：预测答案字符串、`pdf_path`、引用页码与局部归一化框 `NormBBox`。
   - **输出**：`(corrected_answer: str, is_corrected: bool)`。
   - **调用链**：① S-BOED 定位时：`inspect_node`（`inspector_subgraph.py:L135`）$\to$ `locate_target_zoom_box`（`residual.py:L285`）$\to$ 零文本回退 `locate_raster_dense_roi`（`L214`）；② 报告打包时：`pack_report_node`（`inspector_subgraph.py:L349`）$\to$ `verify_and_correct_answer`（`residual.py:L119`）$\to$ `optical_edit_distance`（`L25`）。

6. **它在 Agent / 系统里扮演什么角色？**
   - 扮演 **“视觉初读后的高精度游标卡尺”**：不替代 VLM 的二维版面语义理解，只在最后 `1` 个字符精度上消除高频笔画混淆残差。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：在电子原生财报/合同场景下，为细粒度证券代码、流水号、批号提供最后一道零成本防错保险。
   - **验证指标与实测证据**：`[实测证据]` 在未开启强制放大的 `M5_no_sboed` 组（$N=16$）中真实拦截修正 `2` 次形近错读，使未放大组准确率维持在 `93.8%`；而在已开启凹面镜高清放大的 `M5` 组中，`residual_corrections = 0` 且准确率 `100.0%`，证明高清凹面镜本身已让视觉直读足够清晰，残差层作为防御性冗余零误伤。

8. **如果替换或移除它，系统怎样变化？依据是什么？**
   - `[实测证据]` 在高清放大已触发时影响较小；但在低清直答或放大框轻微失焦场景下（如 `M5_no_sboed` 的 `case_001`、`case_002`），移除残差校验会导致准确率直接下降 `12.5%`（2/16 题因单字符 `0/D`、`8/9` 混淆判错）。

9. **它会怎样失败？如何处理？**
   - **纯位图扫描 PDF（`words == []`）**：`extract_bbox_vector_tokens` 返回空集，`verify_and_correct_answer` 立即无副作用返回 `(pred_answer, False)`，绝不报错；定位器则自动切入 `locate_raster_dense_roi` 纯像素梯度模式。
   - **预测值错得太远（如 `m6_case_001` 双字符错读 `9164` vs `9195`，`d = 2.0 > 1.5`）**：超出安全纠偏半径，严格按契约放弃修改，保留原始输出并在日志记录未触发纠偏。

---

### 技术点六：`SHA-256` 细粒度锁原子缓存 + FastAPI 有界并发 SSE 流式引擎（`cache.py` & `server/app.py`）

1. **为什么用它？它解决什么具体问题？**
   - **具体问题**：① 对同一份百页财报连续追问多个问题时，每次重新构建目录树与 200 DPI 光栅化会浪费大量 CPU 与磁盘 I/O；且在 Web 多线程并发请求同一未缓存页面时，直接写目标 PNG 会引发文件截断损坏（Torn Write）；② 在线长链路推理耗时 `40–100 s`，普通同步 HTTP 接口会让前端长时间假死或被网关 `60 s` 超时掐断，且无并发上限会被突发流量打爆内存。
   - **作用机理**：
     - 缓存层以 `SHA-256(PDF)[:16]` 内容寻址，通过按文档哈希分片的互斥锁 `_get_doc_lock` 防并发击穿，所有 PNG/JSON 均先写含 `time_ns + pid + thread_ident` 的临时文件再执行操作系统级 `tmp.replace(dest)` 原子替换，并支持可选 Redis 双层加速；
     - 服务层通过 `BoundedSemaphore(4)` 实施非阻塞准入控制（超限快速返回 HTTP `429`），工作线程经 `call_soon_threadsafe` 桥接至 `asyncio.Queue` 实时流式推送 SSE 事件，每 `15.0 s` 发送 `: keepalive\n\n` 注释帧保活，并在客户端断连 `finally` 块置位 `disconnected` 信号。
   - **引入代价**：本地 `.cache/zoomdoc` 目录会占用磁盘空间（每渲染 12 页约占 `15–25 MB` PNG 缓存）。
   - **验证手段**：`[实测证据]` `test_m5_t5_content_addressed_cache_hit_and_speedup` 验证二次查询缓存命中后渲染+建树耗时下降 **`> 80%`**；`test_enterprise_cache_thread_safety_and_idempotent_rerun` 验证 8 线程并发读写零截断、零 `.tmp` 残留；$N=16$ 实测跨样本缓存命中率达 **`75.0%`**。

2. **不用它行不行？最简单的可行方案是什么？**
   - 命令行单次跑脚本可以不用缓存也不需要 SSE（每次在临时目录现渲现删、同步等待打印最终 JSON）。但一旦放上 Web 供多用户交互体验，没有缓存会导致每次拖拽凹面镜预览卡顿数秒，没有 SSE 会导致点击运行后页面白等一分半钟毫无反馈。

3. **有哪些替代方案？**
   - 方案 A：Celery + RabbitMQ/Redis 异步任务队列 + 前端定时轮询（Polling `GET /api/jobs/{id}`）；
   - 方案 B：全双工 WebSocket 长连接；
   - 方案 C：将渲染后的图像存入 PostgreSQL `BYTEA` / MySQL `LONGBLOB` 关系型数据库。

4. **为什么没有选择替代方案？（七维逐项对比）**
   - **成本与部署复杂度**：`[实测证据]` 本方案单条 `python scripts/serve.py` 即可在任意机器零外部中间件启动完整流式服务；若引入 Celery + RabbitMQ（方案 A，`[合理推断]` 至少需维护 3 个常驻进程与消息队列运维），对单机/轻量容器部署过重。
   - **效果与体验**：SSE 实时推送每个节点阶段与 Span 耗时，比 `2s` 轮询延迟更低且无无效空轮询请求。
   - **延迟**：`[实测证据]` 本地文件系统直接通过 `FileResponse` 零拷贝发送缓存 PNG，比从关系型数据库 `BLOB` 反序列化读取快一个数量级（交互式凹面镜预览延迟压至 `200–400 ms`）。
   - **稳定性**：`15s` 心跳帧彻底解决 Nginx / 云网关 `60s` 空闲断连问题；Redis 故障时零报错自动退回本地磁盘缓存。
   - **可控性**：`BoundedSemaphore` 硬性封顶并发重任务内存峰值。
   - **安全性**：`[实测证据]` 分块流式上传超 `50 MB` 即刻抛 `413`，静态资产读取强制 `is_relative_to` 拦截目录穿越抛 `403`。
   - **团队维护成本**：前后端通过严格对齐的 TypeScript `StreamEvent` 判别联合通信，协议清晰极易扩展。

5. **它在架构哪一层？输入、输出、依赖和调用链是什么？**
   - **所属层级**：生产服务接入层与持久化存储底座（Serving Gateway & Persistence Layer）。
   - **输入**：HTTP REST / Multipart PDF 文件流 / JSON `RunAgentRequest`。
   - **输出**：`text/event-stream` SSE 事件帧、`image/png` 静态流与结构化评测/审计 JSON。
   - **调用链**：`frontend/src/api.ts::streamAgentRun` $\to$ `POST /api/run/stream`（`server/app.py:L632`）$\to$ 守护线程 `run_m5` $\to$ `TraceLogger.on_event` $\to$ `_push` $\to$ `_event_gen` SSE 帧输出。

6. **它在 Agent / 系统里扮演什么角色？**
   - 它不属于 Agent 认知推理算法本身，而是让 Agent **从实验室脚本蜕变为企业级可交互产品的外骨骼（Production Serving & Storage Infrastructure）**。

7. **它对应什么业务价值？用什么指标可以验证？实际指标是否有证据？**
   - **业务价值**：让面试官或业务方无需配置 API Key 即可毫秒级回放真实推理全过程、拖拽体验凹面镜变形，并在配置 Key 后稳定执行实时流式问答。
   - **验证指标与实测证据**：`[实测证据]` 缓存命中率 `75.0%`；渲染耗时占比 `2.3%`；8 线程并发压测 `100%` 成功；浏览器 `1440×900` 四视图全量交互审计 `0` 控制台报错、`0` 网络失败。

8. **如果替换或移除它，系统怎样变化？依据是什么？**
   - 移除内容寻址缓存后：`[实测证据]` 同一 100 页 PDF 重复查询时每次均需重新解析建树与 200 DPI 光栅化，单次 I/O 延迟增加 `80%+`；移除 `BoundedSemaphore` 与分块上传限额后，并发重请求或超大文件上传可直接导致服务进程 OOM 崩溃。

9. **它会怎样失败？如何处理？**
   - **并发打满**：第 5 个同时发起的实时推理请求立即收到 HTTP `429 Too Many Requests` 明确提示，不挂起阻塞；
   - **上传伪造非 PDF 或损坏 PDF**：校验 `%PDF-` 魔数头与 `get_pdf_page_count` 失败后立即清理临时文件并返回 HTTP `400`；
   - **客户端中途关闭页面**：生成器 `finally` 置位 `disconnected` 标志并确保工作线程 `finally` 释放信号量许可，无信号量永久泄漏。

---

## 第二部分：项目整体执行“业务五问”

### 1. 为什么做？谁使用、频率如何、原流程的痛点是什么？
- **目标用户与使用频率** `[合理推断]`：面向券商投研分析师、财务审计员、医药注册合规专员及法律尽调人员；日常高频场景为每日审阅数十份 50–300 页 PDF 报告并核验其中上百个具体指标、科目附注或监管注册编号。
- **原流程痛点**：
  1. **纯人工 Ctrl+F 检索**：面对扫描件 PDF 或同义词跨表引用无法搜索，人工肉眼在密密麻麻的百行多列附注表里逐行比对极易串行看错；
  2. **传统文本 RAG / PDF 转 Markdown 工具**：前置版面解析器经常把复杂跨列财报表格拆错列，`3.8pt–5pt` 微字 OCR 误识率高，且给出的答案没有原始页面坐标框，审计员仍需花同样时间翻回原 PDF 人工复查真伪。
- **本项目解决方式与代价**：直接以原生 PDF 视觉页面为真值源，按需聚焦放大微字区域并输出带原页归一化红框（`CitedRegion`）与同行表头切片的可核验结论；代价是单次复杂微字查询需 `2–4` 次多模态模型调用（端到端中位耗时约 `50–70 s`，适合高精度异步核验而非毫秒级闲聊）。

### 2. 为什么需要 AI？哪一步必须依赖模型，哪一步更适合规则、普通代码或人工？

本项目严格遵循 **“非结构化语义归模型，结构化契约与几何数学归代码”** 的解耦原则，全链路 9 个步骤切分如下：

| 步骤 | 执行主体 | 为什么必须用 AI / 为什么坚决不用 AI？ |
|---|---|---|
| 1. 目录树构建与防注入脱敏 | **普通确定性代码**（`tree.py`） | PDF 书签层级、字号聚类与正则脱敏具备明确物理规则，写代码耗时 `< 80 ms` 且 `100%` 可复现，用 LLM 既贵又慢。 |
| 2. 跨同义词章节语义匹配（`Navigator`） | **必须依赖 AI 模型** | 用户问“法定税务调节表里的证券号”，而目录标题叫 `Section 3.3 Statutory Reconciliations`，关键词不完全重合，需模型做语义对齐。 |
| 3. 缩略图网格拼接与 `[idx]` 打标 | **普通确定性代码**（`grid.py`） | 图像缩放排版与右上角角标绘制是纯像素几何操作，代码确定无误。 |
| 4. 版面视觉粗筛（`Seeker`） | **必须依赖多模态 VLM** | 需从 12–20 张缩略图中一眼识别“哪几页含有目标形态的数据附注表而非纯文字叙述”，特别是零文本纯扫描页无法靠规则判断。 |
| 5. 复杂版面理解与微字识读（`Inspector`） | **必须依赖多模态 VLM** | 理解跨列合并表头、脚注说明与问题意图之间的非结构化对应关系必须依赖 VLM。 |
| 6. 香农熵计算与是否强制放大裁决 | **数学公式 + 规则门禁**（`vlm_client.py`） | `[实测证据]` 让模型自己判断要不要放大（`M3`）触发率仅 `31.2%`；直接用对数概率算香农熵 $H_{\max} > 0.15$ 是客观物理量，零主观偏差。 |
| 7. 双锚定凹面镜重采样与坐标逆映射 | **纯数学/NumPy 代码**（`fovea.py`） | 图像非均匀插值与分段线性反演必须保证像素级正交共线与精确可逆，绝不能用生成式 AI 重绘图像（会伪造数字）。 |
| 8. 光学混淆残差校验与 `Critic` 路由 | **动态规划 + 纯函数状态机** | 编辑距离计算、引用页越界检查与 `R0–R8` 状态跳转属于零容错安全契约，必须用 `100%` 确定性代码把关。 |
| 9. 最终高风险合规签字采信 | **必须由人类专家最终确认** | 系统负责将百页搜寻时间压缩为展示「答案 + 原页红框 + 共线放大切片」，最终法律/财务责任由人类审计员看图一秒确认后承担。 |

### 3. 如何判断成功？业务指标、质量指标、时延、成本和风险分别怎么衡量？真实数据在哪里？

| 维度 | 核心量化指标 | 本仓库实测数值 `[实测证据]` | 原始证据文件位置 |
|---|---|---|---|
| **质量指标** | 端到端精确匹配准确率（Exact Match）及 95% Wilson CI、配对 McNemar $p$ 值 | 50–100p 主基准 **`100.0% (16/16)`**（`p = 0.0010` vs `B0`）；`18×5` 矩阵与纯扫描集 **`83.3% (10/12)`**（`p = 0.0020` vs `NoZoom`） | `eval/m4/report.md`, `eval/m6_matrix/report.md` |
| **漏斗召回指标** | 章节导航召回率 `Nav Recall` / 缩略图粗筛召回率 `Seek Recall` | 主基准双项均为 **`100.0%`**（相比滑窗基线 `75.0%`） | `eval/m4/results/M5/case_*.json` |
| **成本指标** | 单次查询平均 Prompt Tokens 及配对 Bootstrap（$B=2000$）节省比 95% CI | 平均 **`9,056` Tokens**，相对全图滑窗节省 **`45.22%`**（95% CI `[38.05%, 51.50%]`） | `eval/m4/report.md` |
| **时延指标** | 端到端 P50 / P95 物理墙钟耗时、缓存命中率、渲染耗时占比 | `P50 = 70.6 s`, `P95 = 103.6 s (< 120 s)`；缓存命中率 **`75.0%`**；渲染占比 **`2.3%`** | `eval/m4/report.md` |
| **安全与风险指标** | 主状态图像泄漏数、Prompt 4-gram 数据污染率、异常崩溃率 | 泄漏数 **`0`**；4-gram 污染率 **`0.0%`**；148 次评测批跑 `errors = 0`；76 项离线单测全绿 | `tests/test_milestone4.py`, `tests/test_server_api.py` |

### 4. 系统边界与责任如何划分？什么动作必须确认、授权或由人最终决策？
1. **系统承诺的确定性边界**：输入合法未加密 PDF（$\le 50\text{ MB}$，倾斜角 $|\theta| \le 3^\circ$），系统保证在有限步数预算内终止、不泄漏跨轮图像状态、不越权访问宿主机目录，并为每一个 `answered` 结论提供可回溯至原页的具体页码与局部视觉切片。
2. **系统明确声明不处理的边界（认知弃权）**：当穷尽回溯仍无依据时明确返回 `status="no_answer"`；当光学残差校验遇到多个等距候选或编辑距离 $> 1.5$（如 `m6_case_001`）时强制放弃自动纠偏，绝不静默伪造真值。
3. **必须由外部授权或人类最终决策的动作** `[合理推断]`：
   - 接入外部云端模型 API 发送敏感企业财报前，必须由企业安全管理员确认 API 端点合规性（或切换 `ZOOMDOC_VLM_BASE_URL` 至内网私有化部署的 vLLM 实例）；
   - 当输出结果带 `status="synthesized"`、`budget_exhausted` 或置信度接近阈值边界时，前端观测台展示原页高亮框与凹面镜切片，**必须由人类业务人员人工核验截图后再用于正式财务披露或合同签署**。

### 5. 如果把团队能力剥离，候选人实际做了什么？本人独立完成、协作完成和基座已有能力的边界分别是什么？

| 能力模块 | 开源库 / 大模型基座已有能力（非本人功劳，绝不冒领） | 候选人本人独立设计并编码完成的核心工作 `[实测证据]` |
|---|---|---|
| **底层模型与推理** | `Qwen-VL (qwen3.8-flash)` 原生具备通用图文理解、JSON 生成与返回 Top-5 Token Logprobs 的基础能力；`OpenAI Python SDK` 提供 HTTP 传输。 | 封装 `OpenAICompatVLM` 栈帧级 Base64 隔离、按 `agent=="inspector"` 作用域精准采集 Logprobs、实现 `compute_answer_span_entropy` 答案值域字符对齐香农熵提取，以及 `call_with_schema` 多轮带锚点自愈重试。 |
| **图编排框架** | `LangGraph` 提供通用 `StateGraph`、条件边与 `Annotated` 状态归约底座。 | 独立设计 `SupervisorState`（纯函数 `decide_next` `R0–R8` 路由表）与 `NavigatorSubState` / `InspectorSubState` 双物理隔离子图协议、Pydantic 递归防泄漏校验器、`build_view_items` 裁剪保活窗口与溢出页回填机制。 |
| **PDF 与图像处理底座** | `PyMuPDF` 提供 PDF 书签读取、光栅化与词坐标提取原语；`Pillow` / `NumPy` 提供基础数组与插值运算。 | 原创实现 `PageIndex` 三级建树与防注入清洗、Set-of-Mark `[idx]` 缩略图角标、多锚定带各向异性拓扑凹面镜算子（含页中表头/行名墨水投影检测、宽跨度非反向压缩不变量、自适应长宽比与解析逆映射）、四阶十字准星/纯扫描梯度定位器及光学混淆矩阵 Indel 残差校验。 |
| **工程服务与评测体系** | `FastAPI` / `Uvicorn` 提供 ASGI Web 容器；`React 19` / `Vite` 提供前端渲染底座。 | 独立完成细粒度文档锁 + `os.replace` 崩溃原子内容寻址缓存、有界信号量 SSE 流式引擎（含 `15s` 保活与分块上传防 OOM）、4 视图全栈可视化观测台（含手写交互式 SVG 图表），以及零污染双基准构建、Paired Bootstrap / McNemar 统计检验与全部 `76` 项离线单测。 |

---

## 第三部分：Phase 3 — 对照简历 Bullet 与源码证据的“最短有效阅读路线”

> **使用说明**：不要按字母顺序通读全仓文件！以下 **6 张实战任务卡** 与简历上的核心 Bullet 一一绑定。每读完一张卡片（约 15–20 分钟），立即合上代码用自己的话讲出对应那条简历 Bullet，实现“读源码 $\leftrightarrow$ 讲简历 $\leftrightarrow$ 抗追问”完全闭环。

---

### 任务卡 1：吃透“主从状态机与双子图零泄漏物理隔离”
- **绑定简历 Bullet**：*“基于 LangGraph 设计 `Supervisor（R0–R8 纯函数路由）+ 双物理隔离子图` 架构；通过出入参映射契约与 Pydantic 后置防泄漏校验器，实现主图 `supervisor_state.json` 零 Base64 与零局部路径泄漏。”*
- **阅读目标**：弄懂一张局部放大图在子图里是怎么生、怎么用、怎么在退出子图瞬间彻底消失的，以及主图靠什么规则决定回溯或终止。
- **先看哪里**：
  1. 状态与防泄漏校验器：`zoomdoc/graph/states.py` 中的 `_assert_no_image_payload`（`L32-L47`）、`EvidenceReport`（`L84-L106`）、`SupervisorState`（`L150-L192`）与 `InspectorSubState`（`L216-L238`）；
  2. 主图纯函数路由与包装节点：`zoomdoc/graph/supervisor.py` 中的 `decide_next`（`L59-L194`）与 `inspector_subgraph_node`（`L487-L530`）；
  3. 子图生命周期边界：`zoomdoc/graph/inspector_subgraph.py` 中的 `run_inspector_round`（`L435-L481`）；
  4. 对应单测：`tests/test_m2_units.py` 与 `tests/test_milestone3.py`。
- **建议顺序**：**先看调用方（`supervisor.py:L487` 的 `inspector_subgraph_node`），再看实现方（`run_inspector_round`）**。原因：先在调用处亲眼看到主图只从子图返回值里拿了 `report` 这一个纯文本对象并只更新了 `{"evidence", "usage", "history", "step"}` 四个键，瞬间就能明白为什么子图里的 `crops` 字典传不出来。
- **阅读时要回答的 3 个问题**：
  1. 如果有人不小心在 `EvidenceReport.reason` 字符串里拼进了一个 `.png` 文件路径，哪一行代码会立刻报错拦截？
  2. 当 `Seeker` 选出了 8 张相关页，但 `max_inspector_images` 上限是 6 时，`seeker_node`（`supervisor.py:L455-L465`）怎么处理多出来的那 2 页才不会丢召回？
  3. 跨章节树回溯（`R7b`，`supervisor.py:L179-L188`）触发需要同时满足哪 4 个条件？
- **动手验证**：
  ```powershell
  # 离线运行状态机真值表、防泄漏拦截与跨章回溯测试（无需 API Key，约 5 秒）
  .\.venv\Scripts\python.exe -m pytest tests/test_m2_units.py tests/test_milestone3.py -q
  ```
  *故障复现实验*：在 Python 交互终端执行 `from zoomdoc.graph.states import EvidenceReport; EvidenceReport(round=1, status="answered", reason="see a.png", answer="X")`，亲眼观察 `ValidationError: image payload leaked` 被当场抛出。
- **完成标准**：不看屏幕，能在白纸上画出 `SupervisorState` 与 `InspectorSubState` 的出入参漏斗图，并脱口说出 `R5`（通过）、`R6`（重巡检）、`R7a`（章内补页）、`R7b`（跨章回溯）四条分支的触发条件。

---

### 任务卡 2：吃透“多锚定带各向异性拓扑凹面镜与精确逆映射”（算法核心）
- **绑定简历 Bullet**：*“设计一维可分离分段严格单调重采样算子 $T(x,y)=(g_x^{-1}(x), g_y^{-1}(y))$（$\det J_T > 0$）；自动锁定页中列头带与左侧行名分隔线，引入宽跨度非反向压缩不变量与解析逆映射（反演误差 $\le 2.5\times 10^{-4}$）。”*
- **阅读目标**：弄懂二维表格图像是如何在不弯曲任何横竖网格线的前提下，把表头、行名和远端小单元格同时挤进一张高清图里，还能把坐标精准算回原页的。
- **先看哪里**：
  1. 核心数学实现：`zoomdoc/vision/fovea.py` 中的 `build_1d_fovea_map`（`L16-L173`）、`detect_table_anchor_bands`（`L176-L257`）、`fovea_warp_image`（`L327-L418`）与 `fovea_box_to_page_box`（`L421-L447`）；
  2. 配置项：`zoomdoc/config.py` 中的 `zoom_mode`, `fovea_focus_share`, `fovea_auto_anchors`, `fovea_adaptive_aspect`；
  3. 对应单测：`tests/test_milestone5.py::test_m5_t1_fovea_collinearity_and_exact_inverse` 与 `tests/test_milestone6.py::test_m6_t1_multiband_table_header_foveation_and_exact_inversion`。
- **建议顺序**：**先看测试（`test_m6_t1`），再看实现方（`fovea.py`）**。原因：测试里用白底画了黑框表头和 1px 垂直红线，一眼就能建立几何直觉，再去读 `np.interp` 分段节点构造就极其顺畅。
- **阅读时要回答的 3 个问题**：
  1. 为什么 `fovea_warp_image`（`L375-L389`）先对行做一维水平插值、再对列做一维垂直插值，就能保证表头列和焦点列 `100%` 垂直对齐？
  2. 什么叫“宽跨度非反向压缩不变量”（`fovea.py:L59-L61`）？如果删掉 `max(focus_share, min(0.95, focus_ratio))` 这行代码，当模型框了一整行（占宽 `95%`）时会发生什么灾难？
  3. `fovea_box_to_page_box`（`L421`）是如何只用两行 `np.interp` 就完成非线性变形图的坐标逆映射的？
- **动手验证**：
  ```powershell
  # 1. 跑数学共线性与逆映射精度单测
  .\.venv\Scripts\python.exe -m pytest tests/test_milestone5.py -k "t1" tests/test_milestone6.py -k "t1" -v
  # 2. 启动前端实验室亲手拖拽鼠标对比线性裁剪 vs 凹面镜（无需 API Key）
  .\.venv\Scripts\python.exe scripts/serve.py
  # 打开 http://127.0.0.1:8000 -> 切换到「Fovea Lens · 凹面镜」标签页拖拽框选
  ```
- **完成标准**：不看代码能写出 $x = g_x(u)$ 的分段节点 `(u_knots, x_knots)` 含义，能向面试官解释清楚为什么鱼眼变形会歪曲表格而可分离映射不会，以及修改 `focus_share` 滑块对放大倍率的影响。

---

### 任务卡 3：吃透“S-BOED 香农熵主动感知门禁与去确认偏置”
- **绑定简历 Bullet**：*“仅对 `Inspector` 答案区间采集 Top-5 Token Logprobs 计算最大香农熵 $H_{\max}(Y_{\text{ans}}) > 0.15\text{ bits}$ 触发主动聚焦，剥离首轮未验证盲猜消除确认偏置，将微字放大触发率由 `31.2%` 提升至 `100.0%`。”*
- **阅读目标**：弄懂系统是怎么从 API 返回的 Token 对数概率里精准抠出 `"answer"` 那几个字算香农熵，并在拦截后构造无偏置放大指令的。
- **先看哪里**：
  1. 香农熵提取实现：`zoomdoc/llm/vlm_client.py` 中的 `compute_answer_span_entropy`（`L66-L151`）与 `OpenAICompatVLM.generate`（`L185-L255`）；
  2. 作用域控制：`zoomdoc/agents/base.py:L56`（`collect_logprobs=(agent == "inspector")`）；
  3. 门禁拦截与去偏置重写：`zoomdoc/graph/inspector_subgraph.py` 中的 `inspect_node`（`L104-L185`）；
  4. 对应单测：`tests/test_milestone5.py::test_m5_t2_sboed_entropy_gate_forces_zoom` 与 `test_r3_r4`。
- **建议顺序**：**先看实现方 `compute_answer_span_entropy`（怎么算熵），再看调用方 `inspect_node`（算出来高熵之后怎么改写状态流）**。原因：先搞清物理量定义，再看控制流劫持逻辑最清晰。
- **阅读时要回答的 3 个问题**：
  1. 为什么要先找 `"answer"` 字段的字符串区间再取最大值 `max(ans_entropies)`，而不是直接对整段 JSON 的所有 Token 求平均熵？
  2. 为什么在 `base.py:L56` 中规定只有 `agent == "inspector"` 才传 `collect_logprobs=True`，给 `Seeker` 开了有什么坏处？
  3. 观察 `inspector_subgraph.py:L177-L183`，为什么 `synth_zoom.target` 里坚决不把模型首轮猜的 `parsed_out.answer` 塞进去？
- **动手验证**：
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_milestone5.py -k "sboed or logprob" -v
  ```
- **完成标准**：能写出 Top-5 局部归一化香农熵公式，并能讲清楚“言语过度自信（Verbal Overconfidence）”与“确认偏置（Confirmation Bias）”两个真实实验踩坑故事。

---

### 任务卡 4：吃透“多阶十字准星定位、纯扫描梯度检测与混淆矩阵残差校验”
- **绑定简历 Bullet**：*“设计行列十字准星交集 + 纯位图笔画梯度能量投影 + 光学混淆代价矩阵单字符替换/Indel 编辑距离（唯一性裕度 $>0.05$）三层校验链。”*
- **阅读目标**：弄懂系统在电子矢量 PDF 和零文字纯扫描 PDF 两种情况下分别怎么找目标框，以及最后一步怎么用加权编辑距离安全纠偏而不乱改答案。
- **先看哪里**：
  1. 核心文件：`zoomdoc/vision/residual.py` 中的 `optical_edit_distance`（`L25-L63`）、`verify_and_correct_answer`（`L119-L211`）、`locate_raster_dense_roi`（`L214-L273`）与 `locate_target_zoom_box`（`L285-L498`）；
  2. 对应单测：`tests/test_milestone5.py::test_m5_t3_geometric_cross_modal_residual_verification` 与 `tests/test_milestone6.py::test_m6_t2_matrix_crosshair_and_pure_raster_scan_roi_locator`。
- **建议顺序**：**按 `optical_edit_distance` $\to$ `verify_and_correct_answer` $\to$ `locate_target_zoom_box` 自底向上阅读**。文件独立无外部状态耦合，自底向上最直观。
- **阅读时要回答的 3 个问题**：
  1. 在 `verify_and_correct_answer` 中，如果页面局部框里同时有两个候选词 `ZD-1208-AB` 和 `ZD-1268-AB` 与模型输出 `ZD-1288-AB` 的光学编辑距离同为 `0.25`，系统会选第一个还是放弃修改？为什么？
  2. 当输入是没有任何文字层的纯扫描件（`words == []`）时，`locate_target_zoom_box` 是怎么靠 `locate_raster_dense_roi` 找到表格位置的？
- **动手验证**：
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_milestone5.py -k "t3" tests/test_milestone6.py -k "t2" -v
  ```
- **完成标准**：能准确说出 `max_edit_dist = 1.5` 与次优裕度 `0.05` 的防御意义，并能解释为什么 `m6_case_001` 错了两个字符时系统选择诚实弃权而非强行覆盖。

---

### 任务卡 5：吃透“细粒度并发锁原子缓存与有界信号量 SSE 全栈引擎”（工程核心）
- **绑定简历 Bullet**：*“构建细粒度文档互斥锁 + `os.replace` 原子落盘的 `SHA-256` 缓存（8 线程并发零损坏，命中率 `75.0%`）；基于 FastAPI 实现带 `BoundedSemaphore` 限流、`15s` 保活心跳与断连回收的 SSE 引擎及 React 19 观测工作台。”*
- **阅读目标**：弄懂高并发下文件缓存怎么防脏读防击穿、同步 LangGraph 怎么无阻塞桥接到异步 SSE 流，以及安全防线怎么防 OOM 和路径穿越。
- **先看哪里**：
  1. 原子并发缓存：`zoomdoc/cache.py` 中的 `_atomic_save_png`（`L35-L42`）、`_get_doc_lock`（`L61-L67`）与 `get_or_render_pages`（`L129-L257`）；
  2. 流式与安全网关：`zoomdoc/server/app.py` 中的 `upload_document`（`L236-L280` 分块防 OOM）、`get_run_asset`（`L614-L625` 路径穿越拦截 `403`）与 `stream_agent_run`（`L632-L725` 信号量 `429` + 队列桥接 + `15s` 心跳）；
  3. 前端流消费：`frontend/src/api.ts::streamAgentRun` 与 `frontend/src/components/ErrorBoundary.tsx`；
  4. 对应单测：`tests/test_server_api.py`（全部 2 个综合测试函数）。
- **建议顺序**：**先看 `tests/test_server_api.py`（看全套请求怎么发、怎么测并发和攻击拦截），再跳进 `server/app.py` 与 `cache.py` 看对应实现**。
- **阅读时要回答的 3 个问题**：
  1. 为什么临时文件名必须同时拼上 `time.time_ns()`、`os.getpid()` 和 `threading.get_ident()`，最后再调 `tmp.replace(dest)`？直接 `img.save(dest)` 在多线程下会出什么 Bug？
  2. 在 `stream_agent_run` 中，为什么不能直接在 `async def` 协程里同步调 `run_m5(...)`，而必须开后台线程配合 `loop.call_soon_threadsafe`？
  3. 如果攻击者请求 `/api/runs/m4-M5-case_001/asset?rel=../../../.env`，哪一行代码会把它拦下来？
- **动手验证**：
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_server_api.py -v
  ```
- **完成标准**：能手画出「浏览器 `fetch` 流 $\leftrightarrow$ FastAPI `_event_gen` 协程（含 15s 心跳）$\leftrightarrow$ `asyncio.Queue` $\leftrightarrow$ 后台守护线程 `run_m5`」的跨线程并发交互时序图。

---

### 任务卡 6：吃透“受控消融实验、统计显著性检验与 Bad Case 科学复盘”
- **绑定简历 Bullet**：*“在 50–100 页基准（$N=16$）上将准确率从 `31.2%` 提升至 `100.0%`（McNemar $p=0.0010$），节省 `45.22%` Prompt Tokens（Paired Bootstrap 95% CI `[38.05%, 51.50%]`）；在零文本纯扫描矩阵集（$N=12$）上达 `83.3%`（$p=0.0020$）。”*
- **阅读目标**：弄懂简历上的每一个百分比、置信区间和 $p$ 值是怎么算出来的，防止面试官追问统计细节时卡壳。
- **先看哪里**：
  1. 统计检验纯标准库实现：`zoomdoc/eval/metrics.py` 中的 `wilson_score_interval`（`L21`）、`paired_bootstrap_ratio_ci`（`L47`）、`compute_mcnemar` 与 `audit_prompt_contamination`；
  2. 评测执行与报告：`scripts/eval_m4.py`、`scripts/eval_m6_matrix.py`、`eval/m4/report.md`、`eval/m6_matrix/report.md`；
  3. 对应单测：`tests/test_milestone4.py`。
- **建议顺序**：**先读 `eval/m4/report.md` 与 `eval/m6_matrix/report.md` 建立全局数据印象，再看 `zoomdoc/eval/metrics.py` 掌握统计计算公式**。
- **阅读时要回答的 3 个问题**：
  1. 为什么在 $N=16$ 和 $N=12$ 规模上要用**配对双侧精确二项 McNemar 检验**，而不是普通的独立双样本 $t$ 检验？
  2. `audit_prompt_contamination` 检查了哪两项指标来证明提示词里没有偷塞测试集答案线索？
  3. 面对面试官问“你的系统在什么情况下还是会错？”，如何用 `m6_case_001` 和 `m6_case_012` 两个真实案例从容作答？
- **动手验证**：
  ```powershell
  # 离线运行统计指标计算与零污染审计测试（无需 API Key）
  .\.venv\Scripts\python.exe -m pytest tests/test_milestone4.py -v
  # 重新聚合生成 M6 统计报告（仅读取本地已跑完的 JSON 结果，无需消耗 API）
  .\.venv\Scripts\python.exe scripts/eval_m6_matrix.py --group report_only
  ```
  *注*：若需从零重跑 `eval_m4.py` 或 `eval_m6_matrix.py --no-resume` 发起真实云端大模型请求，需在 `.env` 配置有效的 `ZOOMDOC_VLM_API_KEY`。
- **完成标准**：面对简历上的每一个数字（`100.0%`、`45.22%`、`p=0.0010`、`83.3%`），都能准确说出其对照组是谁、样本量多大、统计检验方法是什么以及适用边界在哪里。
