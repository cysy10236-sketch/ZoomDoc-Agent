# FoveaDoc (Milestone 5) 组件级正交消融实验报告

> 评测基准：`eval/m4/manifest_full16.jsonl` ($N = 16$)，受控超参数：`temperature = 0`, `seed = 42`。

### 6. FoveaDoc 组件级正交消融实验 (Component-Wise Orthogonal Ablation)

| 指标 | B2 (Tree-NoZoom) | M3 (Verbal-Gate + Crop) | M5_no_sboed (w/o S-BOED, Residual Only) | M5_crop (S-BOED + Linear Crop) | M5 (Full FoveaDoc) |
|---|---|---|---|---|---|
| 样本量 $N$ | 16 | 16 | 16 | 16 | 16 |
| 端到端准确率 (Accuracy) | 37.5% | 43.8% | 93.8% | 100.0% | 100.0% |
| 95% Wilson 置信区间 | [18.5%, 61.4%] | [23.1%, 66.8%] | [71.7%, 98.9%] | [80.6%, 100.0%] | [80.6%, 100.0%] |
| Zoom 高清放大触发率 | 0.0% | 31.2% | 50.0% | 100.0% | 100.0% |
| 跨模态残差纠偏次数 | N/A | N/A | 2 | 0 | 0 |
| 平均 Prompt Tokens | 5903 | 6631 | 7186 | 8222 | 9056 |
| 平均 Completion Tokens | 2802 | 2839 | 1923 | 2034 | 2268 |
| 端到端耗时 P50 (ms) | 44438 | 50690 | 49512 | 59906 | 70599 |
| 端到端耗时 P95 (ms) | 115426 | 127166 | 88177 | 100647 | 103558 |
| 异常运行数 (Errors) | 0 | 0 | 0 | 0 | 0 |

#### 6.1 消融配对显著性检验 (vs Full M5)
- **M5 vs M5_no_sboed (S-BOED 熵门控与光学定位边际增益)**: M5胜=1, M5_no_sboed胜=0, 准确率差值 = `+6.2%`, **McNemar p = 1.0000**
- **M5 vs M5_crop (各向异性凹面镜 + 残差校验边际增益)**: M5胜=0, M5_crop胜=0, 准确率差值 = `+0.0%`, **McNemar p = 1.0000**
