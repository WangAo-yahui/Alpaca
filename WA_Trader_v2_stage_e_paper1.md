# WA Trader v2：Paper1部署修正与 Stage E 执行代理

版本：2026-07-25-stage-e-v2

## 1. 当前真实账户情况

目前只有一个真实可用的 Alpaca paper 账户：

```text
paper1
```

当前不存在、未启用、也不应验证：

```text
paper2
paper3
live
```

未来新增账户时，再新增对应 profile 配置。

程序当前不得假设一定存在三个 paper 账户。

---

## 2. 先修正 Stage D 部署

Stage D 代码已经完成，120/120 测试通过。

但策略 `core_long@1.1.0` 应部署给当前真实账户：

```text
paper1
```

而不是 paper2。

修改：

```text
config/v2/profiles/paper1.json
```

使其指向：

```json
{
  "profile_id": "paper1",
  "enabled": true,
  "environment": "paper",
  "strategy": {
    "strategy_id": "core_long",
    "strategy_version": "1.1.0"
  },
  "risk_profile": "paper_standard@1.0.0"
}
```

如果仓库中已经存在 paper2、paper3、live 示例文件：

- 可以删除；
- 或放入 `config/v2/profiles/examples/`；
- 或保留但设置 `enabled: false`。

它们不得参与启动、凭据检查、测试默认值或账户绑定。

---

## 3. Profile 参数建议

当前只有一个账户时，建议：

```text
--profile
```

保持可选。

在：

```text
config/v2/system.json
```

配置：

```json
{
  "default_profile": "paper1"
}
```

因此以下两条等价：

```bash
python3 -u src/v2/main.py   --profile paper1   --unattended   --allow-trade
```

```bash
python3 -u src/v2/main.py   --unattended   --allow-trade
```

未来新增 paper2 时，再显式使用：

```bash
--profile paper2
```

安全要求：

1. 默认 profile 必须存在；
2. 必须 enabled；
3. 当前必须是 paper；
4. 不得默认选择 live；
5. 启动时打印实际 profile；
6. 只读取当前 profile 对应凭据；
7. 不扫描其他 profile 的环境变量。

---

## 4. 当前凭据

不要为了将来可能存在的 paper2、paper3 改动 `.env`。

paper1 应继续使用当前已经工作的凭据变量。

例如项目当前若使用：

```text
ALPACA_API_KEY
ALPACA_SECRET_KEY
```

则 paper1 profile 继续引用它们。

配置文件只保存变量名称，不保存密钥。

---

## 5. Paper1 账户绑定

如果 paper1 尚未绑定：

```bash
.Alpaca/bin/python -u src/v2/main.py   --profile paper1   --bind-account   --unattended
```

或使用默认 profile：

```bash
.Alpaca/bin/python -u src/v2/main.py   --bind-account   --unattended
```

绑定时只显示：

```text
profile: paper1
environment: paper
account hash: 前12位
strategy: core_long@1.1.0
risk: paper_standard@1.0.0
```

不得显示完整账户 ID 或密钥。

---

## 6. Stage D 真实烟雾测试

在 Stage E 前先运行：

```bash
.Alpaca/bin/python -u src/v2/main.py   --profile paper1   --unattended
```

预期：

```text
基础快照成功
第一阶段运行或复用
第二阶段运行
Portfolio Schema通过
Portfolio业务校验通过
Review自动跳过
停在REFRESH_EXECUTION_DATA
提交订单数0
```

若失败，先修复 paper1 部署，不进入 Stage E。

---

# 7. Stage E 目标

Stage E 实现第三阶段执行代理：

```text
重新刷新执行级数据
→ 读取initial guidance
→ 读取user review
→ 读取portfolio output
→ 调用第三阶段Codex
→ Schema校验
→ Python业务校验
→ 保存execution output
→ 推进到BUILD_ORDERS
```

完成后：

```bash
python3 -u src/v2/main.py   --unattended   --allow-trade
```

应运行：

```text
基础数据
→ coarse
→ portfolio
→ review
→ execution data refresh
→ execution agent
→ 停在BUILD_ORDERS
```

Stage E 仍不得：

- 计算最终股数；
- 生成 Alpaca OrderRequest；
- 提交订单；
- 取消订单；
- 替换订单。

---

## 8. 策略版本

现有版本：

```text
core_long@1.0.0
core_long@1.1.0
```

均视为不可变。

Stage E 新建：

```text
strategies/core_long/1.2.0/
```

包含：

```text
manifest.json
prompts/coarse.md
prompts/coarse_AGENTS.md
prompts/portfolio.md
prompts/portfolio_AGENTS.md
prompts/execution.md
prompts/execution_AGENTS.md
schemas/coarse_output.schema.json
schemas/portfolio_output.schema.json
schemas/execution_output.schema.json
config/coarse_policy.json
config/portfolio_policy.json
config/execution_policy.json
```

Stage E 完成后：

```text
paper1 → core_long@1.2.0
```

---

## 9. 执行级快照

新增：

```text
src/v2/data/execution_snapshot.py
```

或扩展现有 snapshots 模块。

保存：

```text
cycles/<cycle_id>/execution/snapshot.json
```

必须重新获取：

- account；
- positions；
- open orders；
- today orders；
- latest trade；
- latest quote；
- bid；
- ask；
- midpoint；
- spread；
- quote timestamp；
- recent minute bars；
- market phase；
- asset tradable；
- fractionable；
- shortable；
- broker extended-hours capability。

执行快照必须晚于 portfolio output。

---

## 10. Execution 输入

至少包含：

```json
{
  "schema_version": "1.0",
  "stage": "execution_decision",
  "profile": {
    "profile_id": "paper1",
    "environment": "paper"
  },
  "release": {},
  "run_date": "...",
  "cycle_id": "...",
  "generated_at": "...",
  "input_signature": "...",
  "trade_permission": {},
  "initial_guidance": {},
  "user_review": {},
  "portfolio": {},
  "execution_snapshot": {},
  "risk_profile": {},
  "execution_policy": {},
  "data_quality": {}
}
```

必须读取：

```text
initial_guidance.json
user_review.json
portfolio/output.json
execution/snapshot.json
```

---

## 11. 第三阶段职责

可以：

- approve；
- modify；
- defer；
- reject；
- no_action；
- 根据最新价格降低或提高执行比例；
- 调整目标权重，但不能超出配置范围；
- 选择限价或市场执行意图；
- 提议分批；
- 提议保留、取消或替换挂单；
- 判断不要追价；
- 请求重新运行第二阶段；
- 拒绝用户的正向交易请求；
- 联网检查最新重大变化。

不能：

- 输出最终股数；
- 输出最终 notional；
- 直接调用 Alpaca；
- 声称已提交或成交；
- 新建候选池外零持仓标的；
- 绕过用户禁止；
- 绕过 Python 风控。

---

## 12. Execution 输出

建议：

```json
{
  "schema_version": "1.0",
  "stage": "execution_decision",
  "profile_id": "paper1",
  "strategy_id": "core_long",
  "strategy_version": "1.2.0",
  "run_date": "...",
  "cycle_id": "...",
  "generated_at": "...",
  "input_signature": "...",
  "status": "success",
  "network_research": {},
  "market_assessment": {},
  "review_response": {},
  "portfolio_response": {},
  "decisions": [],
  "open_order_actions": [],
  "requires_portfolio_replan": false,
  "requires_manual_review": false,
  "valid_until": "...",
  "warnings": [],
  "source_references": []
}
```

每个 decision：

```json
{
  "symbol": "MU",
  "portfolio_action": "open",
  "execution_decision": "modify",
  "side": "buy",
  "target_weight": "0.04",
  "maximum_weight": "0.06",
  "execution_fraction": "0.50",
  "urgency": "normal",
  "price_condition": {
    "reference": "ask",
    "limit_price": "102.50",
    "do_not_execute_above": "104.00",
    "review_below": "90.00"
  },
  "order_intent": {
    "preferred_type": "limit",
    "time_in_force_preference": "day",
    "extended_hours_requested": true,
    "allow_queue": true,
    "allow_partial_fill": true
  },
  "decision_reason": "...",
  "execution_risks": [],
  "required_checks": [],
  "source_references": []
}
```

允许：

```text
target_weight
maximum_weight
execution_fraction
limit_price intent
```

禁止：

```text
quantity
qty
shares
notional
dollar_amount
final_order
broker_order_request
submitted
filled
```

---

## 13. 用户意见优先级

```text
Python硬风控
>
用户明确禁止与硬限制
>
第三阶段判断
>
第二阶段组合
>
initial guidance和正向交易请求
```

如果 user review 中存在无法可靠解释的硬限制：

```text
requires_manual_review = true
execution_decision = defer
```

无人值守模式不得猜测后继续。

---

## 14. 市场时段

至少识别：

```text
before_market_open
regular_session
after_market_close
overnight_session
market_closed_weekend
market_closed_holiday
unknown
```

当 `--allow-trade` 存在时，第三阶段可以在券商支持的扩展时段提出新仓意图。

但：

- 非regular新仓一般需要 limit intent；
- 必须有价格条件；
- 必须标记 extended_hours；
- broker不支持时应 defer 或 reject；
- unknown时不得 approve。

Stage E 只产生意图，不进行正式订单转换。

---

## 15. Execution Policy

新增：

```text
strategies/core_long/1.2.0/config/execution_policy.json
```

至少包括：

```json
{
  "schema_version": "1.0",
  "valid_minutes": 30,
  "target_weight_adjustment": {
    "maximum_absolute_change": "0.02",
    "maximum_relative_change": "0.25"
  },
  "execution_fraction": {
    "minimum": "0",
    "maximum": "1"
  },
  "extended_hours": {
    "require_limit_intent": true,
    "require_fresh_quote": true
  }
}
```

超过允许调整范围时：

```text
requires_portfolio_replan = true
execution_decision = defer
```

---

## 16. Python 业务校验

至少检查：

1. profile是paper1；
2. strategy、run date、cycle匹配；
3. input signature匹配；
4. valid_until有效；
5. symbol不重复；
6. 新仓来自coarse且eligible；
7. side与portfolio方向一致；
8. execution decision合法；
9. reject/defer/no_action不能形成可执行买卖；
10. target weight非负；
11. 不超过maximum weight；
12. 调整幅度符合policy；
13. execution fraction在0到1；
14. price condition一致；
15. unknown phase不得approve；
16. quote不过期；
17. spread符合risk profile；
18. tradable有效；
19. user prohibition得到执行；
20. unresolved硬限制必须defer；
21. open order action合法；
22. 不含数量字段；
23. 不声称submitted或filled；
24. requires replan时不得越权修改组合。

---

## 17. 状态机

Stage D 终点：

```text
REFRESH_EXECUTION_DATA
```

Stage E：

```text
REFRESH_EXECUTION_DATA
→ RUN_EXECUTION
→ BUILD_ORDERS
```

成功后：

```text
stages.execution = completed
current_step = BUILD_ORDERS
```

最终停在：

```text
BUILD_ORDERS
```

---

## 18. 主流程输出

```text
Profile：paper1
账户环境：paper
策略：core_long@1.2.0
风险：paper_standard@1.0.0
交易提交权限：enabled

第一阶段：reuse
第二阶段：run或reuse
执行前复查：skipped
执行数据刷新：成功
第三阶段：run
第三阶段校验：通过
Approve：3
Modify：2
Defer：1
Reject：1
下一步骤：BUILD_ORDERS
订单构建尚未实现
提交订单数：0
```

---

## 19. 测试

新增：

```text
tests/v2/
├── test_default_profile.py
├── test_paper1_deployment.py
├── test_execution_snapshot.py
├── test_execution_models.py
├── test_execution_input.py
├── test_execution_signature.py
├── test_execution_schema.py
├── test_execution_validation.py
├── test_execution_workspace.py
├── test_execution_stage.py
├── test_execution_review_constraints.py
├── test_execution_extended_hours.py
├── test_strategy_1_2_release.py
└── test_main_stage_e.py
```

必须覆盖：

1. 默认profile是paper1；
2. 未配置paper2/3不影响运行；
3. 只加载paper1凭据；
4. paper1指向正确strategy；
5. execution snapshot晚于portfolio；
6. quote age；
7. spread；
8. market phase；
9. 合法execution通过；
10. 新仓不在coarse失败；
11. weight调整越界；
12. execution fraction越界；
13. quantity等禁止字段；
14. unknown phase approve失败；
15. 过期quote失败；
16. spread过大失败；
17. 扩展时段intent规则；
18. user prohibition被执行；
19. unresolved硬限制导致defer；
20. guidance可以被拒绝；
21. portfolio可被修改；
22. 超范围修改要求replan；
23. open order action；
24. 不调用订单API；
25. main停在BUILD_ORDERS；
26. allow-trade仍提交0单；
27. 现有120项测试保持通过。

---

## 20. 给 Codex 的指令

```text
当前只有paper1真实账户。
paper2、paper3只是未来可能新增的账户，目前不应启用、加载或验证。

Stage D已经完成，120项测试通过，但请先修正部署：
1. paper1指向core_long@1.1.0；
2. paper2、paper3不存在时不得报错；
3. 如保留示例profile，必须disabled；
4. 增加安全default_profile=paper1；
5. --profile可选；
6. 只加载当前profile凭据；
7. 不修改.env；
8. 完成paper1真实Stage D烟雾测试。

然后实施Stage E：
9. 不修改core_long@1.0.0和1.1.0；
10. 创建core_long@1.2.0；
11. 增加execution Prompt、AGENTS、Schema和policy；
12. 实现执行级最新数据刷新；
13. 实现models/execution.py；
14. 实现stages/execution.py；
15. 输入包含initial guidance、user review、portfolio和执行快照；
16. 支持approve、modify、defer、reject、no_action；
17. 支持扩展时段执行意图；
18. 禁止输出quantity、notional和最终订单；
19. Python严格校验；
20. 主流程推进到BUILD_ORDERS；
21. 不实现order builder；
22. 不调用submit、cancel或replace订单API；
23. 即使--allow-trade存在，提交数必须为0；
24. live继续拒绝；
25. 不导入v1；
26. 保持全部旧测试并增加Stage E测试；
27. 完成后报告paper1部署修正、策略版本、文件变更和测试结果；
28. 只有实际执行后才能宣称paper1烟雾通过。
```
