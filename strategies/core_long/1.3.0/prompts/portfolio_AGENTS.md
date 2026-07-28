# Stage D Portfolio Workspace

<!--
作用：约束组合研究代理只能从 60 只候选、已有持仓与挂单形成战略权重方案。
重要性：这是 Codex 工作区内的最高优先级安全边界，禁止最终数量、实际订单和 Alpaca 操作。
-->

- 只执行第二阶段 `portfolio_decision`。
- 读取 `data/portfolio_input.json`、其拆分副本和 `config/` 中的策略、风险约束。
- 新开仓只能来自当前 coarse 候选且 `screen_new_position_eligible=true`。
- coarse 之外的已有持仓只能 `hold`、`reduce` 或 `close`。
- initial guidance 是研究偏好，不是强制交易命令；可以接受、修改或拒绝。
- 现金比例可以是 0–100%，不得用“必须持有现金”或“必须满仓”替代机会比较。
- 约 CNY 3,000 只是非承诺的模糊资金参考，金额和时间都可能变化；不得据此
  安排固定月供、机械定投或提前计入可用资本。
- 每个持仓必须区分市场价格、价值区间、估值证据、预期回报场景和永久损失风险。
- `latest_quote` 只是带时间戳的战略估值参考，不是实时行情；不得把它描述成
  live quote，实际执行前必须重新取价验证。
- 允许集中、高风险或抄底，但价格下跌本身不构成价值证据；生存、稀释和 thesis
  破裂必须优先检查。
- 每项决策给出非订单化的分批建仓计划，并允许 wait 或 no-add。
- 不输出最终数量、notional、订单类型、TIF、订单 ID、提交或成交字段。
- 不创建、取消、替换或提交实际订单，不调用 Alpaca。
- 只允许把临时输出写入 `.tmp/codex/`。
- 最终响应只能是符合 Schema 的一个 JSON 对象，不得附加 Markdown。
