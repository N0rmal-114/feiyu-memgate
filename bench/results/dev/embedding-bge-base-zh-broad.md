# 开发集：宽松档（broad）的选择：embedding-bge-base-zh

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。同一份候选列表、同一张网格（176 个点）、同一套真实的召回步骤（见 `embedding-bge-base-zh.md` 的搜索）。
规则（事先写定）：在精确率 >= 50% 且安静率 >= 精确档安静率的一半（36.7%）的格子里取交付命中最高的；平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 173 个。

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
precise: min_score=0.25 relative=0.75   81.2   96.2    72.5   81.8   76.9   73.3   75.9   0.91
broad:   min_score=0.05 relative=0.50   81.2   96.2    90.0   70.6   79.1   46.7   70.3   1.64
```

结论：**min_score=0.05 relative=0.50** 是宽松档；交付命中 +17.5 点，精确率 -11.2 点，安静率 -26.7 点，每题条数 +0.73。

