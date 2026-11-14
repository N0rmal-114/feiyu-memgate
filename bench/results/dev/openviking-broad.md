# 开发集：宽松档（broad）的选择：openviking

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。同一份候选列表、同一张网格（176 个点）、同一套真实的召回步骤（见 `openviking.md` 的搜索）。
规则（事先写定）：在精确率 >= 50% 且安静率 >= 精确档安静率的一半（25.0%）的格子里取交付命中最高的；平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 59 个。

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
precise: min_score=0.55 relative=0.95   82.5   97.5    87.5   81.4   84.3   50.0   75.0   1.09
broad:   min_score=0.55 relative=0.90   82.5   97.5    91.2   74.9   82.3   50.0   73.5   1.45
```

结论：**min_score=0.55 relative=0.90** 是宽松档；交付命中 +3.7 点，精确率 -6.5 点，安静率 +0.0 点，每题条数 +0.36。

