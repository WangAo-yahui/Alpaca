# WA Trader v2 Stage D Portfolio Decision

<!--
作用：指导 Codex 把粗选候选、现有风险敞口和用户建议整合为目标权重组合。
重要性：输出只供未来执行阶段复核，绝不代表订单已经生成或提交。
-->

读取 `data/portfolio_input.json`，并遵守 `AGENTS.md`、风险配置和输出 Schema。

完成以下工作：

1. 阅读账户、资本、已有持仓和未完成订单；不得重复使用挂单已预留资金。
2. 阅读 initial guidance，明确记录接受、修改和拒绝的部分。
3. 深入研究最可能进入组合的候选；可联网，优先公司、SEC、政府、交易所及可靠媒体。
4. 综合质量、估值、趋势、催化剂、下行风险、行业集中与相关性。
5. 决定目标现金比例、目标投资比例、目标标的和战略目标权重。
6. 对已有持仓选择 open/increase/hold/reduce/close/watch/avoid 中合规的动作。
7. 对未完成订单只给出 keep/review/cancel/replace 战略评估；用 `order_reference` 标识。
8. 给出战略价格区间和保护性复核阈值，但不得生成实际订单参数或最终股数。
9. 标记未来执行阶段必须重新核验的价格、数据、集中度和流动性风险。
10. `valid_until` 应按 portfolio policy 的有效分钟数设置。

任何网络不可用必须使用 `success_local_only` 并写入 warning。最终只返回严格 JSON。
