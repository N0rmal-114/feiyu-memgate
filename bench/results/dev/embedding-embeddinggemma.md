# 开发集阈值搜索：embedding, embeddinggemma (plain, floor 0.30 ceil 0.75)

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.55 relative=0.85         78.8   98.8    73.8   83.4   78.3   76.7   77.8   0.81
min_score=0.55 relative=0.80         78.8   98.8    75.0   81.6   78.1   76.7   77.7   0.89
min_score=0.55 relative=0.90         78.8   98.8    72.5   83.7   77.7   76.7   77.4   0.76
min_score=0.55 relative=0.75         78.8   98.8    75.0   78.4   76.7   76.7   76.7   0.96
min_score=0.55 relative=0.95         78.8   98.8    70.0   83.3   76.1   76.7   76.2   0.71
```

选定：**min_score=0.55 relative=0.85**，交付命中 73.8%，精确率 83.4%，安静率 76.7%，balanced 77.8%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.50 relative=0.85   78.8   97.5    56.2   85.2   67.8   86.7   72.9   0.57
chosen: min_score=0.55 relative=0.85   78.8   98.8    73.8   83.4   78.3   76.7   77.8   0.81
```

上面的“before”是同一份余弦用 bge-m3 的旧默认（floor 0.40、ceil 0.80、min_score 0.50、比例 0.85）换算的结果；“chosen”是按静态配方（floor 0.30、ceil 0.75）换算后的最优点。模型：`embeddinggemma`，前缀方案：plain。
