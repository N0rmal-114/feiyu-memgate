# 开发集阈值搜索（精确档 precise）：openviking

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.55 relative=0.95         82.5   97.5    87.5   81.4   84.3   50.0   75.0   1.09
min_score=0.65 relative=0.90         82.5   97.5    67.5   85.6   75.5   73.3   74.9   0.73
min_score=0.65 relative=0.95         82.5   97.5    66.2   85.9   74.8   73.3   74.4   0.69
min_score=0.60 relative=0.95         82.5   97.5    76.2   83.0   79.5   60.0   74.2   0.91
min_score=0.55 relative=0.90         82.5   97.5    91.2   74.9   82.3   50.0   73.5   1.45
```

选定：**min_score=0.55 relative=0.95**，交付命中 87.5%，精确率 81.4%，安静率 50.0%，balanced 75.0%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.50 relative=0.60   82.5   97.5    90.0   21.5   34.7   23.3   31.6   4.10
chosen: min_score=0.55 relative=0.95   82.5   97.5    87.5   81.4   84.3   50.0   75.0   1.09
```

