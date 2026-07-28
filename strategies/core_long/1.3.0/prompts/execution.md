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
   把 portfolio 的 accumulation plan 视为当前可执行上限，而不是必须买入的配额。
5. 当 `trade_permission.submission_enabled=false` 时，本轮所有普通买卖决定必须
   使用 defer/reject/no_action 的完整非执行形态；仍要为现有美股多头输出
   `protection_plans`，供 Python 做 dry-run 保护校验，但不得 approve/modify。
6. overnight、盘前和盘后必须核对券商扩展时段能力、报价新鲜度、价差和
   limit intent；这些时段允许调仓或建仓，不得因为不是 regular session
   就自动 defer。
7. Live 账户一旦存在 `asset_class=crypto` 的可用持仓，Python 会强制覆盖为
   全量 `close + sell + market + gtc`，不要求 15 秒新鲜报价，也不允许模型
   选择 hold/defer、新开或增加。尚未确认成交前，不得把预计卖出款当成 USD
   buying power；股票部署必须等待后续对账确认。
8. Live 美股在周末或节假日可以形成下一交易日排队意图：调仓可按计划执行，
   open/increase 的 `execution_fraction` 最多 0.25；必须使用 limit、day、
   `extended_hours_requested=false`、`allow_queue=true`，价格取可用的最后报价并
   明确开盘跳空风险。Paper 或 unknown 状态仍不得 approve。闭市排队不代表已成交。
9. regular session 内允许在 `live_full` 的 0–100% 总权益范围中自由决定现金、
   标的权重和执行比例；允许长期空仓、长期满仓、集中或分散，不强制防守仓位，
   但仍禁止做空和额外杠杆。较大回撤容忍度不能替代价值与永久损失分析。
10. 挂单只给出 keep、review、cancel 或 replace 意图，不执行动作。
11. `protection_plans` 必须覆盖每个现有 Live 美股多头持仓，以及本轮每个
    approve/modify 的新开或加仓标的。Codex 可以选择：
    `stop`、`stop_limit`、`take_profit`、`trailing_stop`、`oco`、
    `bracket`、`oto_stop`、`oto_take_profit`、`staged_oco` 或 `none`。
    保护价格必须来自当前价、成本、波动和策略失效条件，给出明确数字与原因。
    长期核心或高确信度持仓可以选择 `none`，coverage 必须为 0，并说明为什么
    自动止损会损害长期策略、用哪些基本面/估值/集中度条件替代自动卖出。
12. Bracket 用于新入场且必须同时含止盈与止损；OCO 用于已有持仓；
    OTO 只带一个止盈或止损。Trailing Stop 只能独立使用，不能作为
    Bracket/OCO 的 leg。`staged_oco` 的各阶段 coverage_fraction 合计必须
    等于总 coverage_fraction。
13. 保护单不启用 extended hours。碎股保护使用 `day`；Stop、Trailing Stop
    只在 regular session 触发。扩展时段风险必须写入 reason，不能声称全天保护。
14. 多头保护价格关系必须满足：止盈高于当前/成本，止损低于当前价；
    Stop-Limit 的 sell limit 不得高于 stop。覆盖比例必须在 0–1。
15. 网络不可用时使用 `success_local_only` 并写 warning。
16. 不计算最终股数或 notional，不生成 Alpaca OrderRequest，不声称已提交或成交。
17. `defer`、`reject`、`no_action` 是完全非执行决定，必须同时使用：
    `side="none"`、`execution_fraction="0"`、`urgency="none"`；
    `price_condition.reference="none"` 且三个价格字段为 `null`；
    `order_intent.preferred_type="none"`、
    `time_in_force_preference="none"`，三个布尔字段全部为 `false`。
    可在 `decision_reason`、`execution_risks` 和 `required_checks` 中保留未来复查条件。
18. 执行分批建仓：只有当前报价落入相应 tranche 条件、估值证据仍有效且资金已经
    settled 时才 approve/modify。约 CNY 3,000 只是金额和时间均不保证的未来参考，
    不得据此安排固定月供或机械买入已明显高估、thesis 破裂的标的；也不得因为计划
    分批而拒绝证据充分、价格优势明显的一次性建仓。
19. 避免同日反复微额补仓。普通建仓/加仓的目标权重差小于 policy 的 material
    gap，或同一标的当天已完成两次 discretionary entry 时，原则上 defer；
    只有估值或风险出现新的重大变化才可例外并明确解释。
20. 每个 approve/modify 必须核对 portfolio 的价值区间和 bear/base/bull 回报假设。
    当前价格超过价值上沿、估值为 `no_reliable_estimate` 且没有其他可靠框架、或
    永久损失风险恶化时，不得仅凭价格下跌而抄底。

最终只返回严格 JSON。
