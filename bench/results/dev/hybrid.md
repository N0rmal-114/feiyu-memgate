# 开发集阈值搜索：hybrid

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.70 relative=0.85         82.5   98.8    68.8   88.5   77.4   73.3   76.3   0.72
min_score=0.70 relative=0.90         82.5   98.8    68.8   88.5   77.4   73.3   76.3   0.72
min_score=0.70 relative=0.80         82.5   98.8    68.8   83.9   75.6   73.3   75.0   0.78
min_score=0.65 relative=0.90         82.5   98.8    70.0   84.2   76.4   70.0   74.7   0.77
min_score=0.65 relative=0.85         82.5   98.8    70.0   83.3   76.1   70.0   74.4   0.79
```

选定：**min_score=0.70 relative=0.85**，交付命中 68.8%，精确率 88.5%，安静率 73.3%，balanced 76.3%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.55 relative=0.80   82.5   98.8    78.8   78.1   78.4   53.3   71.6   1.11
chosen: min_score=0.70 relative=0.85   82.5   98.8    68.8   88.5   77.4   73.3   76.3   0.72
```

