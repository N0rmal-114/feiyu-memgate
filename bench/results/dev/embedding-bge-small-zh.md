# 开发集：宽松档（broad，默认档）的选择：embedding-bge-small-zh

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。同一份候选列表、同一张网格（176 个点）、同一套真实的召回步骤（见 `embedding-bge-small-zh-precise.md` 的搜索）。
规则（事先写定）：在精确率 >= 50% 且安静率 >= 精确档安静率的一半（28.3%）的格子里取交付命中最高的；平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 163 个。

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
precise: min_score=0.30 relative=0.90   73.8   91.2    76.2   80.6   78.3   56.7   72.4   0.88
broad:   min_score=0.10 relative=0.65   73.8   91.2    83.8   60.5   70.3   36.7   61.1   1.91
```

结论：**min_score=0.10 relative=0.65** 是宽松档；交付命中 +7.5 点，精确率 -20.0 点，安静率 -20.0 点，每题条数 +1.03。

