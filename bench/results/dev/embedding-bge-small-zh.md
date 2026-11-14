# 开发集阈值搜索：embedding, bge-small-zh (plain, floor 0.40 ceil 0.80)

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。
网格：min_score 0.05 到 0.80（步长 0.05）x relative_score 比例 0.50 到 0.95（步长 0.05）或不用该步骤，共 176 个点；其余召回步骤是默认的，limit 5。
目标（事先定好）：`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`；交付F1 是交付命中和精确率的调和平均。平手：交付命中高的，再是 min_score 低的，再是比例低的（不用该步骤算最低）。

前 5 名：

```
point                               Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
min_score=0.30 relative=0.90         73.8   91.2    76.2   80.6   78.3   56.7   72.4   0.88
min_score=0.30 relative=0.95         73.8   91.2    73.8   81.2   77.3   56.7   71.7   0.80
min_score=0.30 relative=0.85         73.8   91.2    76.2   77.9   77.1   56.7   71.5   1.00
min_score=0.35 relative=0.90         73.8   91.2    68.8   81.1   74.4   63.3   71.4   0.78
min_score=0.35 relative=0.95         73.8   91.2    67.5   81.8   74.0   63.3   71.1   0.72
```

选定：**min_score=0.30 relative=0.90**，交付命中 76.2%，精确率 80.6%，安静率 56.7%，balanced 72.4%。

对照（这次搜索之前代码里的值，同一份候选）：

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
before: min_score=0.50 relative=0.85   73.8   91.2    50.0   77.0   60.6   70.0   63.2   0.63
chosen: min_score=0.30 relative=0.90   73.8   91.2    76.2   80.6   78.3   56.7   72.4   0.88
```

上面的“before”是同一份余弦用 bge-m3 的旧默认（floor 0.40、ceil 0.80、min_score 0.50、比例 0.85）换算的结果；“chosen”是按静态配方（floor 0.40、ceil 0.80）换算后的最优点。模型：`hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16`，前缀方案：plain。
