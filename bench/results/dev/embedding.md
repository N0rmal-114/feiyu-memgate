# 开发集阈值搜索：embedding

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.50 relative=0.85         82.5   97.5    77.5   83.7   80.5   60.0   74.9   0.94
min_score=0.60 relative=0.85         82.5   97.5    70.0   84.3   76.5   70.0   74.7   0.78
min_score=0.40 relative=0.85         82.5   97.5    86.2   81.8   84.0   50.0   74.7   1.08
min_score=0.45 relative=0.85         82.5   97.5    81.2   83.2   82.2   53.3   74.3   1.00
min_score=0.35 relative=0.85         82.5   97.5    90.0   82.3   86.0   43.3   74.3   1.15
```

选定：**min_score=0.50 relative=0.85**，交付命中 77.5%，精确率 83.7%，安静率 60.0%，balanced 74.9%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.40 relative=0.80   82.5   97.5    86.2   76.8   81.2   50.0   72.7   1.23
chosen: min_score=0.50 relative=0.85   82.5   97.5    77.5   83.7   80.5   60.0   74.9   0.94
```

