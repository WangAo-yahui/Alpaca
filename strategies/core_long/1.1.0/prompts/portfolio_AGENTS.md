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
- 保留合理现金，并把未完成买单的预留资金视为已占用。
- 不输出最终数量、notional、订单类型、TIF、订单 ID、提交或成交字段。
- 不创建、取消、替换或提交实际订单，不调用 Alpaca。
- 只允许把临时输出写入 `.tmp/codex/`。
- 最终响应只能是符合 Schema 的一个 JSON 对象，不得附加 Markdown。
