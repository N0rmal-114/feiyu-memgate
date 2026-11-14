# 开发集：宽松档（broad）的选择：embedding-qwen3-embedding

数据：`bench/dev`，160 条记忆，110 个问题（80 个可回答，30 个没有答案）。同一份候选列表、同一张网格（176 个点）、同一套真实的召回步骤（见 `embedding-qwen3-embedding.md` 的搜索）。
规则（事先写定）：在精确率 >= 50% 且安静率 >= 精确档安静率的一半（23.3%）的格子里取交付命中最高的；平手取 balanced 高的，再取 min_score 高的，再取比例高的（不用 relative_score 算最低）。精确档是后端声明的默认点，不是重新搜索的结果。满足条件的格子 144 个。

```
stages                              Hit@1  Hit@5 dep.hit   prec     F1  quiet    bal  items
precise: min_score=0.20 relative=0.95   73.8   93.8    75.0   74.0   74.5   46.7   66.9   0.98
broad:   min_score=0.15 relative=0.65   73.8   93.8    86.2   52.1   65.0   40.0   58.2   2.21
```

结论：**min_score=0.15 relative=0.65** 是宽松档；交付命中 +11.3 点，精确率 -21.9 点，安静率 -6.7 点，每题条数 +1.23。

