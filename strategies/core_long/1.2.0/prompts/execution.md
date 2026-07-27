# WA Trader v2 Stage E Execution Decision

<!--
作用：用最新报价、成交、分钟线、账户与用户复查意见审核 portfolio 的执行时机。
重要性：输出仍只是权重和订单类型偏好，不是最终订单，也不代表任何成交。
-->

读取 `data/execution_input.json`、`AGENTS.md`、风险配置和 execution policy。

1. 逐项核对 portfolio decision 与最新持仓、挂单、报价、成交和资产能力。
2. 处理 initial guidance 与 user review；正向交易请求可以拒绝，明确禁止必须遵守。
3. 对每个组合标的选择 approve、modify、defer、reject 或 no_action。
4. 可以在策略范围内调整 target weight 与 execution fraction；超范围时设置 replan 并 defer。
5. regular session 外必须核对券商扩展时段能力、报价新鲜度、价差和 limit intent。
6. 周末、节假日、unknown 或数据不足时不得 approve。
7. 挂单只给出 keep、review、cancel 或 replace 意图，不执行动作。
8. 网络不可用时使用 `success_local_only` 并写 warning。
9. 不计算最终股数或 notional，不生成 Alpaca OrderRequest，不声称已提交或成交。
10. `defer`、`reject`、`no_action` 是完全非执行决定，必须同时使用：
    `side="none"`、`execution_fraction="0"`、`urgency="none"`；
    `price_condition.reference="none"` 且三个价格字段为 `null`；
    `order_intent.preferred_type="none"`、
    `time_in_force_preference="none"`，三个布尔字段全部为 `false`。
    可在 `decision_reason`、`execution_risks` 和 `required_checks` 中保留未来复查条件。

最终只返回严格 JSON。
