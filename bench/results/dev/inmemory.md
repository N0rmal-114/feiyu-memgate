# 开发集阈值搜索：inmemory

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.30 relative=0.75         53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.36
min_score=0.30 relative=0.80         53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.36
min_score=0.30 relative=0.85         53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.36
min_score=0.30 relative=0.90         53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.35
min_score=0.30 relative=0.95         53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.35
```

选定：**min_score=0.30 relative=0.75**，交付命中 36.2%，精确率 84.8%，安静率 86.7%，balanced 60.6%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.20 relative=0.60   53.8   82.5    55.0   65.6   59.8   40.0   54.4   1.07
chosen: min_score=0.30 relative=0.75   53.8   82.5    36.2   84.8   50.8   86.7   60.6   0.36
```

