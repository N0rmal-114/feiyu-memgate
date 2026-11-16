# 开发集阈值搜索：embedding, qwen3-embedding (prefixed, floor 0.35 ceil 0.80)

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.20 relative=0.95         73.8   93.8    75.0   74.0   74.5   46.7   66.9   0.98
min_score=0.25 relative=0.95         73.8   93.8    73.8   73.7   73.7   46.7   66.3   0.97
min_score=0.20 relative=0.85         73.8   93.8    78.8   69.3   73.7   46.7   66.3   1.31
min_score=0.25 relative=0.85         73.8   93.8    77.5   69.5   73.3   46.7   66.0   1.29
min_score=0.10 relative=0.95         73.8   93.8    77.5   73.8   75.6   40.0   65.9   1.03
```

选定：**min_score=0.20 relative=0.95**，交付命中 75.0%，精确率 74.0%，安静率 46.7%，balanced 66.9%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.50 relative=0.85   73.8   92.5    51.2   72.8   60.1   66.7   61.9   0.74
chosen: min_score=0.20 relative=0.95   73.8   93.8    75.0   74.0   74.5   46.7   66.9   0.98
```

上面的“before”是同一份余弦用 bge-m3 的旧默认（floor 0.40、ceil 0.80、min_score 0.50、比例 0.85）换算的结果；“chosen”是按静态配方（floor 0.35、ceil 0.80）换算后的最优点。模型：`qwen3-embedding:0.6b`，前缀方案：prefixed。
