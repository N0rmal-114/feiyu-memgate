# 开发集：宽松档（broad，默认档）的选择：embedding-embeddinggemma

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。同一份候选列表、同一张网格（176 个点）、同一套真实的召回步骤（见 `embedding-embeddinggemma-precise.md` 的搜索）。
规则（事先写定）：在精确率 >= 50% 且安静率 >= 精确档安静率的一半（38.3%）的格子里取交付命中最高的；平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 112 个。

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
precise: min_score=0.55 relative=0.85   78.8   98.8    73.8   83.4   78.3   76.7   77.8   0.81
broad:   min_score=0.30 relative=0.60   78.8   98.8    93.8   53.3   68.0   43.3   61.3   2.31
```

结论：**min_score=0.30 relative=0.60** 是宽松档；交付命中 +20.0 点，精确率 -30.1 点，安静率 -33.3 点，每题条数 +1.50。

