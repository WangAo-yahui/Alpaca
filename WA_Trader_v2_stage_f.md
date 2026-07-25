# WA Trader v2：Stage F 订单构建与Python硬校验

版本：2026-07-25-stage-f-v1

## 0. 当前基线

```text
main merge commit: 2ab8f80
tag: stage-e-complete
tests: 136/136 PASS
default profile: paper1
strategy: core_long@1.2.0
current step: BUILD_ORDERS
```

Stage E已实现执行级快照和第三阶段，但尚未进行真实Stage E烟雾测试，也不存在任何订单提交、取消或替换调用。

Stage F负责第一次生成最终数量和精确订单参数，但仍不得执行任何券商写操作。


## 1. 先补Stage E真实烟雾

在修改代码前运行：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_e_smoke \
PYTHONPATH=src \
.Alpaca/bin/python -u src/v2/main.py \
  --profile paper1 \
  --unattended
```

不要加 `--allow-trade`。

预期：

```text
基础快照成功
coarse成功或复用
portfolio成功或复用
execution snapshot成功
execution output通过Schema和业务校验
停在BUILD_ORDERS
提交、取消、替换订单数均为0
```

若失败，先修复Stage E，不进入Stage F。


## 2. Stage F与Stage G边界

Stage F：

```text
刷新pre-trade数据
→ 计算目标市值差额
→ 计算最终股数
→ 生成挂单动作计划
→ 生成proposed orders
→ Python硬校验
→ 生成精确broker request specs
→ 停在SUBMIT_ORDERS
```

Stage G：

```text
执行取消/替换依赖
→ 提交paper订单
→ 保存broker结果
→ 对账
→ 更新日报
→ 完成cycle
```

Stage F不得调用：

```text
submit_order
cancel_order
replace_order
close_position
```


## 3. Git与版本

先运行全部136项测试，然后：

```bash
git switch -c feature/stage-f-order-planning
```

不得修改：

```text
strategies/core_long/1.2.0/
config/v2/risk_profiles/paper_standard-1.0.0.json
```

Stage F属于交易引擎和硬风控，不创建新的策略版本。

新增：

```text
config/v2/order_policies/paper_equity-1.0.0.json
config/v2/risk_profiles/paper_standard-1.1.0.json
```

paper1更新为：

```json
{
  "strategy": {
    "strategy_id": "core_long",
    "strategy_version": "1.2.0"
  },
  "risk_profile": "paper_standard@1.1.0",
  "order_policy": "paper_equity@1.0.0"
}
```

Cycle release metadata必须记录order policy及其hash。


## 4. 新增文件

```text
src/v2/models/orders.py

src/v2/data/pretrade_snapshot.py

src/v2/trading/
├── __init__.py
├── order_builder.py
├── order_validator.py
├── order_request_factory.py
└── idempotency.py

schemas/v2/
├── pretrade_snapshot.schema.json
├── proposed_orders.schema.json
└── validated_orders.schema.json
```

Stage F不得新增可达生产流程的 `order_submitter.py`。


## 5. Pre-trade快照

第三阶段完成后必须重新获取：

- account
- cash
- buying power
- portfolio value
- positions
- available quantity
- open orders
- today orders
- latest quote
- bid、ask、midpoint、spread
- quote timestamp与age
- asset active/tradable/fractionable/shortable
- market phase
- broker capabilities

保存：

```text
cycles/<cycle_id>/orders/pretrade_snapshot.json
```

该快照必须晚于execution output。

关键刷新失败时全局block，不得用旧execution snapshot继续生成可提交订单。


## 6. 订单目录

```text
cycles/<cycle_id>/orders/
├── pretrade_snapshot.json
├── proposed.json
├── validated.json
├── request_specs.json
├── action_plan.json
└── validation_summary.json
```

Stage F不生成：

```text
broker_submission.json
```


## 7. 模型

`models/orders.py` 至少定义：

- PreTradeSnapshot
- ProposedOrderPlan
- ProposedOrder
- ProposedOrderAction
- ValidatedOrderPlan
- ValidatedOrder
- BrokerRequestSpec
- OrderValidationIssue
- OrderPlanSummary
- SubmissionPermission

订单状态：

```text
proposed
approved
blocked
skipped
dependent
dry_run_approved
```

动作：

```text
submit
keep
cancel
replace
review
none
```

所有金额、价格、权重和数量使用Decimal或十进制字符串。


## 8. Exposure计算

每个标的计算：

```text
current_position_value
open_buy_remaining_value
open_sell_remaining_value
potential_position_value
target_position_value
raw_delta_value
execution_delta_value
```

默认：

```text
potential_position_value
=
current_position_value
+ open_buy_remaining_value
- open_sell_remaining_value
```

目标：

```text
target_position_value
=
portfolio_value × execution.target_weight
```

差额：

```text
raw_delta_value
=
target_position_value - potential_position_value
```

本轮部署：

```text
execution_delta_value
=
raw_delta_value × execution.execution_fraction
```

必须防止：

- 同一挂单重复计入
- 卖单估值超过可卖持仓
- 缺失价格被当成0
- cancel计划在真正取消前释放资金
- replace计划提前重复占用


## 9. 买入数量

买入上限取以下约束的最小值：

```text
execution delta
allocatable capital
buying power
minimum cash reserve
per-cycle deployment limit
single-symbol remaining capacity
sector remaining capacity
```

数量：

```text
planned_qty
=
allowed_buy_value / validated_reference_price
```

量化：

```text
fractionable=true
→ 按order policy配置的精度向下量化

fractionable=false
→ 向下取整到整股
```

不得四舍五入导致超买。

量化后低于最小订单价值：

```text
skipped: below_minimum_order_value
```


## 10. 卖出数量

卖出不得超过：

```text
available_quantity
```

默认：

```text
allow_short=false
```

清仓时可以计划卖出全部available quantity，但必须扣除有效未完成卖单占用。

存在冲突卖单时：

```text
dependent
或 blocked
```

不得形成重复卖单。


## 11. 价格生成

Python根据以下共同生成价格：

- execution price intent
- bid/ask/midpoint
- spread
- quote age
- do-not-execute boundary
- market phase
- broker capability
- order policy

Stage F初期自动订单类型建议只支持：

```text
market
limit
```

暂不自动创建：

```text
stop
stop_limit
trailing_stop
bracket
oco
oto
```

除非实现完整语义、Schema和测试。

所有价格使用Decimal，不得使用float。


## 12. 扩展时段

非regular session的立即执行意图必须经过broker capability adapter。

一般要求：

```text
limit intent
limit_price存在
extended_hours=true
time_in_force属于支持集合
quote足够新鲜
spread在阈值内
```

overnight、pre-market、after-hours不得被视为完全相同。

合法性由：

```text
broker capabilities
+ versioned order policy
+ Alpaca SDK本地模型校验
```

共同决定。

不支持时：

```text
blocked
或 dependent/deferred
```

不得因为 `--allow-trade` 绕过券商限制。


## 13. 周末、休市与unknown

市场阶段：

```text
market_closed_weekend
market_closed_holiday
unknown
```

规则：

- broker明确允许排队时可以形成queue intent
- 否则blocked
- unknown永远不得approved
- Stage F不得声称订单可以立即成交


## 14. 挂单动作计划

根据execution output生成：

```text
orders/action_plan.json
```

规则：

- keep：保留，并计入潜在暴露
- cancel：仍视为占用，直到Stage G确认取消
- replace：replacement order标记dependent
- review：不自动修改
- 找不到目标订单：blocked或skipped并记录原因

Replace依赖：

```text
取消成功
→ 重新刷新订单、资金和持仓
→ 重新校验replacement
→ 才能提交replacement
```


## 15. 幂等性

`idempotency.py` 为每个计划生成稳定：

```text
plan_id
client_order_id
```

输入至少包括：

```text
profile
strategy id/version
cycle id
symbol
side
intent index
order role
idempotency version
```

建议格式：

```text
wa2-<profile>-<cycle>-<side>-<symbol>-<hash>
```

要求：

- 相同输入得到相同ID
- 不同意图不碰撞
- 字符和长度符合broker限制
- 不包含账户ID或密钥
- Stage G可按client_order_id查询去重
- 算法改变时升级idempotency_version


## 16. Proposed orders

`orders/proposed.json` 顶层至少包含：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper1",
  "strategy_id": "core_long",
  "strategy_version": "1.2.0",
  "risk_profile": "paper_standard@1.1.0",
  "order_policy": "paper_equity@1.0.0",
  "run_date": "...",
  "cycle_id": "...",
  "generated_at": "...",
  "execution_output_hash": "...",
  "pretrade_snapshot_hash": "...",
  "submission_requested": true,
  "orders": [],
  "actions": [],
  "summary": {},
  "warnings": []
}
```

每个订单可首次包含：

```text
quantity
planned_value
reference_price
limit_price
time_in_force
extended_hours
client_order_id
```


## 17. Request specs

保存：

```text
orders/request_specs.json
```

示例：

```json
{
  "requests": [
    {
      "plan_id": "plan-...",
      "request_class": "LimitOrderRequest",
      "symbol": "MU",
      "qty": "20",
      "side": "buy",
      "time_in_force": "day",
      "limit_price": "100.20",
      "extended_hours": false,
      "client_order_id": "wa2-..."
    }
  ]
}
```

Stage F可以在测试中本地创建官方SDK的request对象验证参数，但不得调用TradingClient提交。


## 18. Python硬校验

至少检查：

### 账户
1. profile为paper1
2. environment为paper
3. account hash匹配
4. account可交易
5. trading_blocked=false
6. buying power足够
7. cash reserve满足
8. live继续拒绝

### 一致性
9. execution output合法
10. pretrade snapshot晚于execution
11. hash匹配
12. cycle/profile/strategy一致
13. risk与order policy版本一致
14. quote不过期

### 标的
15. asset active
16. tradable
17. fractionable与数量匹配
18. shortable规则
19. symbol范围合法
20. 用户禁止未被绕过

### 数量和资本
21. qty大于0
22. qty量化正确
23. 买入不超可用资本
24. 卖出不超available quantity
25. 默认不做空
26. 不超单标的上限
27. 不超行业上限
28. 不低于现金下限
29. 不超单轮部署上限
30. 不低于最小订单价值
31. 挂单资金不重复分配

### 价格和市场
32. limit price有效
33. spread在阈值内
34. do-not-execute边界满足
35. market phase合法
36. extended-hours组合合法
37. unknown不批准
38. broker capability支持
39. 价格精度合法

### 重复和依赖
40. client_order_id唯一
41. broker不存在相同client_order_id
42. 同方向重复订单检查
43. 相反方向冲突
44. cancel/replace依赖正确
45. replacement未提前独立批准
46. 已提交或成交计划不重复


## 19. 校验状态与allow-trade

无 `--allow-trade`：

```text
submission_requested=false
dry_run=true
合法订单状态=dry_run_approved
```

有 `--allow-trade`：

```text
submission_requested=true
dry_run=false
合法独立订单状态=approved
```

但Stage F始终：

```text
submission_performed=false
submitted_order_count=0
```

Validated顶层至少包含approved、blocked、skipped、dependent统计。


## 20. Block和skip

`blocked`：

- 风控不允许
- 数据不可靠
- 参数非法
- broker不支持
- 需要人工处理
- 可能造成错误交易

`skipped`：

- 差额太小
- 低于最小订单价值
- 已有订单基本覆盖
- execution为no_action
- 量化后数量为0

单个订单blocked不必阻止其他独立订单。

账户、配置、身份或关键数据错误应阻止全部。


## 21. 多订单资本顺序

多个买单不得同时看到全部现金。

使用确定性优先级：

```text
execution urgency
→ portfolio priority
→ conviction
→ symbol稳定排序
```

每批准一个订单后递减：

```text
remaining allocatable capital
remaining buying power
remaining sector capacity
remaining order count
```

相同输入必须得到相同排序、数量、plan ID和client order ID。


## 22. 风险Profile与Order Policy

`paper_standard@1.1.0` 应包含或继承：

```text
minimum cash weight
maximum single-symbol weight
maximum sector weight
maximum new capital per cycle
maximum order count
minimum order value
allow_short=false
quote max age
regular spread limit
extended spread limit
```

如果现有1.0.0更严格，保留更严格值。

`paper_equity@1.0.0` 至少配置：

```text
supported asset classes
market/limit支持范围
default TIF
fractional precision
idempotency version
extended-hours require-limit
extended-hours supported TIF
queue policy
```

不要把券商规则散落硬编码在多个模块。


## 23. 状态机

Stage E终点：

```text
BUILD_ORDERS
```

Stage F：

```text
BUILD_ORDERS
→ VALIDATE_ORDERS
→ SUBMIT_ORDERS
```

最终：

```text
current_step=SUBMIT_ORDERS
status=running
resume_allowed=true
```

不要将cycle标记completed，因为Stage G尚未执行。


## 24. 主流程输出

有 `--allow-trade`：

```text
Profile：paper1
策略：core_long@1.2.0
风险：paper_standard@1.1.0
订单策略：paper_equity@1.0.0
交易提交权限：enabled

Pre-trade刷新：成功
拟定订单：3
批准订单：2
阻止订单：1
跳过订单：0
依赖订单：0
预计买入资金：...
预计卖出金额：...
下一步骤：SUBMIT_ORDERS
订单提交阶段尚未实现
实际提交订单数：0
```

Dry-run时显示 `dry_run_approved` 数量。


## 25. 测试

新增建议：

```text
tests/v2/
├── test_pretrade_snapshot.py
├── test_order_models.py
├── test_order_exposure.py
├── test_order_builder.py
├── test_order_quantity.py
├── test_order_price.py
├── test_order_idempotency.py
├── test_order_action_plan.py
├── test_order_request_factory.py
├── test_order_validator.py
├── test_order_capital_allocation.py
├── test_order_extended_hours.py
├── test_order_dry_run.py
├── test_order_policy_version.py
├── test_risk_profile_1_1.py
└── test_main_stage_f.py
```

必须覆盖：

1. current exposure
2. open buy/sell潜在暴露
3. fractional和whole-share量化
4. 卖出不超available
5. minimum order skip
6. execution fraction
7. close全量
8. cash与buying power
9. 单标的、行业、单轮和订单数限制
10. 多订单顺序资本分配
11. 输出稳定性
12. client_order_id稳定、唯一、长度合法
13. quote age和spread
14. tradable=false
15. unknown market
16. regular request spec
17. extended-hours limit spec
18. unsupported market intent
19. weekend queue capability
20. cancel不提前释放资金
21. replacement dependent
22. existing client_order_id去重
23. opposite-side conflict
24. dry-run状态
25. allow-trade批准但提交0
26. 全局错误阻止全部
27. SDK request本地构造
28. 没有任何broker写调用
29. main停在SUBMIT_ORDERS
30. 原136项测试保持通过


## 26. 测试与真实Dry-run

全部测试：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_f \
PYTHONPATH=src \
.Alpaca/bin/python -m unittest discover \
  -s tests/v2 \
  -p 'test_*.py' \
  -v
```

真实dry-run：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_f_smoke \
PYTHONPATH=src \
.Alpaca/bin/python -u src/v2/main.py \
  --profile paper1 \
  --unattended
```

允许标志测试，但仍不得提交：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_f_allow_smoke \
PYTHONPATH=src \
.Alpaca/bin/python -u src/v2/main.py \
  --profile paper1 \
  --unattended \
  --allow-trade
```

两次都必须停在SUBMIT_ORDERS，实际提交0。


## 27. 静态与运行时安全检查

运行：

```bash
grep -R "submit_order\|cancel_order\|replace_order\|close_position" \
  src/v2 \
  --exclude-dir=__pycache__
```

允许注释和测试禁止断言，不允许可达生产调用。

同时使用fake TradingClient断言：

```text
submit=0
cancel=0
replace=0
close=0
```


## 28. 完成与发布

```bash
git add .
git commit -m "Implement WA Trader v2 Stage F order planning"

git switch main
git merge --no-ff feature/stage-f-order-planning
git tag stage-f-complete
```

完成标准：

- Stage E真实烟雾通过
- core_long@1.2.0未修改
- order policy和risk profile独立版本化
- pretrade数据重新刷新
- Python确定性计算最终数量
- 挂单暴露和依赖正确
- 资金不重复使用
- 卖出不超可用持仓
- 默认不做空
- proposed、validated、request specs完整
- 稳定client_order_id
- 扩展时段按capability校验
- allow-trade仍提交0
- 主流程停在SUBMIT_ORDERS
- live继续拒绝
- 所有测试通过
- `.env`未修改
- 无v1导入
- 工作树干净


## 29. Stage G预告

Stage G才实现：

```text
order_submitter.py
cancel/replace dependency executor
broker_submission.json
reconciliation.py
cycle completion
daily report
same-day rerun
```

Stage G完成后，以下命令才会实际提交paper订单：

```bash
python3 -u src/v2/main.py \
  --unattended \
  --allow-trade
```


## 30. 交给Codex的指令

```text
Stage E已经发布：
- main commit 2ab8f80
- tag stage-e-complete
- 136项测试通过
- paper1使用core_long@1.2.0
- 主流程停在BUILD_ORDERS
- 没有broker写操作

请阅读docs/WA_Trader_v2_stage_f.md并只实施Stage F。

要求：
1. 先运行136项基线测试；
2. 先执行真实Stage E smoke，不加--allow-trade；
3. 创建feature/stage-f-order-planning；
4. 不修改core_long@1.2.0；
5. 新增paper_equity@1.0.0；
6. 新增paper_standard@1.1.0，不修改1.0.0；
7. paper1引用新risk和order policy；
8. 实现pretrade snapshot；
9. 实现models/orders.py；
10. 实现order_builder.py、order_validator.py、order_request_factory.py和idempotency.py；
11. 全部使用Decimal；
12. 根据目标权重、current exposure、挂单和execution fraction计算最终数量；
13. 买单不得重复使用资金；
14. 卖单不得超过available quantity；
15. 默认禁止做空；
16. cancel/replace形成依赖计划但不调用API；
17. 扩展时段按broker capability和当前官方规则校验；
18. 生成pretrade_snapshot、proposed、validated、request_specs和action_plan；
19. 无--allow-trade时使用dry_run_approved；
20. 有--allow-trade时可标记approved，但提交数必须为0；
21. 主流程停在SUBMIT_ORDERS；
22. 不创建可达生产的order submitter；
23. 不调用submit/cancel/replace/close；
24. live继续拒绝；
25. 不导入v1；
26. 不修改.env；
27. 保持旧测试并新增Stage F测试；
28. 完成后报告版本、文件变更、测试结果和两次真实dry-run结果。
```
