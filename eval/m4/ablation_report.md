# FoveaDoc (Milestone 5) 组件级正交消融实验报告

> 评测基准：`eval/m4/manifest_sub8.jsonl` ($N = 8$)，受控超参数：`temperature = 0`, `seed = 42`。

### 6. FoveaDoc 组件级正交消融实验 (Component-Wise Orthogonal Ablation)

| 指标 | B2 (Tree-NoZoom) | M3 (Verbal-Gate + Crop) | M5_no_sboed (w/o S-BOED, Residual Only) | M5_crop (S-BOED + Linear Crop) | M5 (Full FoveaDoc) |
|---|---|---|---|---|---|
| 样本量 $N$ | 8 | 8 | 8 | 8 | 8 |
| 端到端准确率 (Accuracy) | 25.0% | 37.5% | 75.0% | 87.5% | 100.0% |
| 95% Wilson 置信区间 | [7.1%, 59.1%] | [13.7%, 69.4%] | [40.9%, 92.8%] | [52.9%, 97.8%] | [67.6%, 100.0%] |
| Zoom 高清放大触发率 | 0.0% | 25.0% | 37.5% | 100.0% | 100.0% |
| 跨模态残差纠偏次数 | N/A | N/A | 3 | 0 | 0 |
| 平均 Prompt Tokens | 6077 | 6748 | 7073 | 8191 | 8988 |
| 平均 Completion Tokens | 2600 | 3356 | 3434 | 3478 | 3572 |
| 端到端耗时 P50 (ms) | 47028 | 51428 | 56253 | 92672 | 97934 |
| 端到端耗时 P95 (ms) | 82695 | 127807 | 134002 | 177365 | 187193 |
| 异常运行数 (Errors) | 0 | 0 | 0 | 0 | 0 |

#### 6.1 消融配对显著性检验 (vs Full M5)
- **M5 vs M5_no_sboed (S-BOED 熵门控与光学定位边际增益)**: M5胜=2, M5_no_sboed胜=0, 准确率差值 = `+25.0%`, **McNemar p = 0.5000**
- **M5 vs M5_crop (各向异性凹面镜 + 残差校验边际增益)**: M5胜=1, M5_crop胜=0, 准确率差值 = `+12.5%`, **McNemar p = 1.0000**
