# 开发集阈值搜索：embedding, bge-base-zh (prefixed, floor 0.35 ceil 0.80)

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.25 relative=0.75         81.2   96.2    72.5   81.8   76.9   73.3   75.9   0.91
min_score=0.25 relative=0.70         81.2   96.2    72.5   80.7   76.4   73.3   75.6   0.97
min_score=0.20 relative=0.75         81.2   96.2    77.5   82.1   79.8   63.3   75.3   0.99
min_score=0.25 relative=0.80         81.2   96.2    70.0   82.5   75.7   73.3   75.1   0.83
min_score=0.20 relative=0.70         81.2   96.2    77.5   81.1   79.3   63.3   74.9   1.05
```

选定：**min_score=0.25 relative=0.75**，交付命中 72.5%，精确率 81.8%，安静率 73.3%，balanced 75.9%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.50 relative=0.85   75.0   87.5    27.5   84.6   41.5   96.7   56.6   0.25
chosen: min_score=0.25 relative=0.75   81.2   96.2    72.5   81.8   76.9   73.3   75.9   0.91
```

上面的“before”是同一份余弦用 bge-m3 的旧默认（floor 0.40、ceil 0.80、min_score 0.50、比例 0.85）换算的结果；“chosen”是按静态配方（floor 0.35、ceil 0.80）换算后的最优点。模型：`hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0`，前缀方案：prefixed。
