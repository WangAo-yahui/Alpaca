# Stage E Execution Workspace

<!--
作用：约束执行代理只能把已验证组合转换为执行意图。
重要性：本阶段禁止最终数量、订单请求和任何 Alpaca 写操作，并必须服从 Python 风控与用户禁止。
-->

- 只执行第三阶段 `execution_decision`。
- 以 `data/execution_input.json` 中的最新执行快照为事实来源。
- `market_assessment.market_phase` 必须与
  `execution_snapshot.market_phase` 完全一致，不得改写或添加后缀。
- 优先级：Python 硬风控 > 用户明确禁止与硬限制 > 第三阶段判断 > portfolio > initial guidance。
- 可以 approve、modify、defer、reject 或 no_action。
- 除已成交 `open` 转为 `increase` 或中性 `hold` 的既有兼容规则外，
  `portfolio_action` 必须逐字复制 portfolio 的 action。若当日成交已使原动作
  无需继续，保留原 `portfolio_action` 并用 `execution_decision=no_action`
  表达不下单；不得把 reduce/close/increase 改写成 hold。
- 不得增加 portfolio 决策之外的零持仓标的。
- 不输出 quantity、qty、shares、notional、dollar_amount、final_order 或 broker_order_request。
- 不声称 submitted 或 filled，不调用 Alpaca，不创建、取消或替换实际订单。
- 必须为现有多头和批准的新入场输出 `protection_plans`；Codex 负责策略选择和
  数字条件，Python 负责数量、券商能力、价格关系、幂等及最终提交。
- 长期持仓可按 policy 选择 `mode=none`，但必须 coverage=0，并以明确 thesis、
  估值和集中度复查条件替代自动止损；不得把 `none` 描述为有效券商保护。
- 仅执行当前满足条件的 accumulation tranche；避免同日反复微额补仓和无意义漂移。
- 允许的保护模式只有 Schema 枚举；不得把 trailing stop 嵌入 bracket/OCO，
  不得把 regular-session Stop 说成扩展时段也会触发。
- 非 regular 时段只有在券商能力、最新报价和 limit intent 全部满足时才可形成执行意图。
- `defer`、`reject`、`no_action` 必须完全中性：`side=none`、执行比例为 `0`、
  urgency 为 `none`、价格 reference 为 `none` 且价格为 null、订单 type/TIF 为
  `none`、所有订单布尔开关为 false；复查条件只写入原因、风险和 required checks。
- 用户原始评论存在无法可靠解释的硬限制时，必须 manual review 并 defer。
- 只允许写 `.tmp/codex/`，最终只返回一个符合 Schema 的 JSON 对象。
