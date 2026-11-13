# FoveaDoc (Milestone 5) 组件级正交消融实验报告

> 评测基准：`eval/m4/manifest_full16.jsonl` ($N = 16$)，受控超参数：`temperature = 0`, `seed = 42`。

### 6. FoveaDoc 组件级正交消融实验 (Component-Wise Orthogonal Ablation)

| 指标 | B2 (Tree-NoZoom) | M3 (Verbal-Gate + Crop) | M5_no_sboed (w/o S-BOED, Residual Only) | M5_crop (S-BOED + Linear Crop) | M5 (Full FoveaDoc) |
|---|---|---|---|---|---|
| 样本量 $N$ | 16 | 16 | 16 | 16 | 16 |
| 端到端准确率 (Accuracy) | 37.5% | 43.8% | 87.5% | 93.8% | 81.2% |
| 95% Wilson 置信区间 | [18.5%, 61.4%] | [23.1%, 66.8%] | [64.0%, 96.5%] | [71.7%, 98.9%] | [57.0%, 93.4%] |
| Zoom 高清放大触发率 | 0.0% | 31.2% | 37.5% | 100.0% | 100.0% |
| 跨模态残差纠偏次数 | N/A | N/A | 3 | 0 | 1 |
| 平均 Prompt Tokens | 5903 | 6631 | 6978 | 8107 | 8874 |
| 平均 Completion Tokens | 2802 | 2839 | 3782 | 3531 | 2756 |
| 端到端耗时 P50 (ms) | 44438 | 50690 | 41800 | 72394 | 64609 |
| 端到端耗时 P95 (ms) | 115426 | 127166 | 154553 | 171452 | 160505 |
| 异常运行数 (Errors) | 0 | 0 | 0 | 0 | 0 |

#### 6.1 消融配对显著性检验 (vs Full M5)
- **M5 vs M5_no_sboed (S-BOED 熵门控与光学定位边际增益)**: M5胜=2, M5_no_sboed胜=3, 准确率差值 = `-6.2%`, **McNemar p = 1.0000**
- **M5 vs M5_crop (各向异性凹面镜 + 残差校验边际增益)**: M5胜=1, M5_crop胜=3, 准确率差值 = `-12.5%`, **McNemar p = 0.6250**
