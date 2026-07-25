# WA Trader v2：Stage D 第二阶段组合决策实施任务

版本：2026-07-23-stage-d-v1

## 0. 当前稳定基线

当前 main：

```text
commit: 3be0094
tag: stage-c5-complete
```

已完成：

- Stage A 基础设施
- Stage B Alpaca paper基础数据
- Stage C 60只候选池与真实烟雾测试
- Stage C.5 initial guidance、多账户Profile、账户Hash绑定
- 策略与风险版本
- Runtime按账户和策略隔离
- Coarse revision
- Shared market data
- 103项测试通过
- 主流程停在 `RUN_PORTFOLIO`
- 没有订单提交
- Live仍被拒绝

Stage D必须建立在此基线上。

---

## 1. Stage D目标

实现：

```text
读取profile与strategy release
→ 读取initial guidance
→ 读取当前有效coarse revision
→ 读取账户、持仓、挂单和资金
→ 准备候选行情与研究资料
→ 判断portfolio是run还是reuse
→ 调用Codex形成组合方案
→ Schema校验
→ Python业务校验
→ 原子保存portfolio结果
→ 收集第二阶段后的user review
→ 推进到REFRESH_EXECUTION_DATA
```

完成后的命令：

```bash
python3 -u src/v2/main.py   --profile paper2   --unattended   --allow-trade
```

预期：

```text
基础数据
→ coarse运行或复用
→ portfolio运行或复用
→ 自动跳过执行前复查
→ 停在REFRESH_EXECUTION_DATA
```

本阶段不得：

- 运行第三阶段
- 计算最终股数
- 构建实际订单
- 调用submit_order
- 提交任何订单

---

## 2. Git与策略版本

先确认基线并运行测试：

```bash
git status
git log -1 --oneline
git tag --points-at HEAD

PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_d_baseline PYTHONPATH=src .Alpaca/bin/python -m unittest discover   -s tests/v2   -p 'test_*.py'   -v
```

创建分支：

```bash
git switch -c feature/stage-d-portfolio
```

不得修改：

```text
strategies/core_long/1.0.0/
```

创建：

```text
strategies/core_long/1.1.0/
├── manifest.json
├── prompts/
│   ├── coarse.md
│   ├── coarse_AGENTS.md
│   ├── portfolio.md
│   └── portfolio_AGENTS.md
├── schemas/
│   ├── coarse_output.schema.json
│   └── portfolio_output.schema.json
└── config/
    ├── coarse_policy.json
    └── portfolio_policy.json
```

从1.0.0复制coarse内容并保持其内容一致，再增加portfolio能力。

Stage D完成后可只将 `paper2` 升级到：

```text
core_long@1.1.0
```

paper1和paper3可继续使用1.0.0。

---

## 3. 第二阶段职责

可以：

- 研究60只候选
- 综合initial guidance
- 考虑账户资金、持仓和挂单
- 决定目标现金比例
- 决定目标持仓和目标权重
- 决定open/increase/hold/reduce/close/watch/avoid
- 给出战略价格区间
- 给出保护和风险思路
- 拒绝initial guidance中的正向建议
- 标记第三阶段需重点复核的事项
- 请求未来重新平衡

不能：

- 输出最终股数或notional
- 输出实际订单类型
- 调用Alpaca
- 声称已下单
- 绕过用户硬限制
- 为候选池外零持仓标的新建仓
- 修改源码、配置、Schema或状态文件

---

## 4. 标的范围

允许研究：

```text
当前coarse revision的60只候选
+ 当前持仓
+ 当前未完成订单涉及的标的
```

新开仓必须：

```text
在60只候选中
且 screen_new_position_eligible = true
```

候选池外已有持仓只允许：

```text
hold
reduce
close
```

不得：

```text
open
increase
```

未完成订单可以给出战略建议：

```text
keep
review
cancel
replace
```

但本阶段不能执行取消或替换。

---

## 5. 新增文件

```text
src/v2/models/portfolio.py
src/v2/stages/portfolio.py
src/v2/review.py
```

至少定义：

- PortfolioInput
- PortfolioCandidate
- PortfolioHolding
- PortfolioOpenOrder
- PortfolioMarketContext
- PortfolioOutput
- PortfolioAllocation
- PortfolioDecision
- PortfolioPricePlan
- PortfolioProtectionPlan
- PortfolioValidationResult
- PortfolioReuseDecision

建议阶段结果：

```python
@dataclass(frozen=True)
class PortfolioStageResult:
    action: Literal["run", "reuse"]
    source_cycle_id: str | None
    input_path: Path
    output_path: Path
    validation_path: Path
    input_signature: str
    target_cash_weight: Decimal
    target_symbol_count: int
    warnings: tuple[str, ...]
```

---

## 6. 运行目录

```text
cycles/<cycle_id>/
├── initial_guidance.json
├── base_snapshot.json
├── portfolio/
│   ├── input.json
│   ├── output.json
│   ├── validation.json
│   ├── codex_call.json
│   ├── reuse.json
│   └── workspace/
├── user_review.json
└── ...
```

复用旧方案时，当前cycle仍应拥有本地：

```text
portfolio/output.json
portfolio/validation.json
portfolio/reuse.json
```

`reuse.json`记录：

```json
{
  "schema_version": "1.0",
  "reused": true,
  "source_cycle_id": "...",
  "source_output_path": "...",
  "source_input_signature": "...",
  "reused_at": "...",
  "reasons": []
}
```

第三阶段始终读取当前cycle的 `portfolio/output.json`。

---

## 7. Portfolio输入

`portfolio/input.json`至少包含：

```json
{
  "schema_version": "1.0",
  "stage": "portfolio_decision",
  "profile": {},
  "release": {},
  "run_date": "2026-07-23",
  "cycle_id": "...",
  "generated_at": "...",
  "input_signature": "...",
  "trigger": {},
  "initial_guidance": {},
  "coarse": {},
  "account": {},
  "capital": {},
  "positions": [],
  "open_orders": [],
  "candidates": [],
  "market_context": {},
  "previous_portfolio": {},
  "data_quality": {},
  "policy": {}
}
```

Initial guidance必须完整传入，但它是建议，不是强制交易命令。

---

## 8. 账户和资本

至少包含：

```json
{
  "account": {
    "status": "ACTIVE",
    "trading_blocked": false,
    "cash": "100000",
    "buying_power": "100000",
    "portfolio_value": "100000",
    "equity": "100000"
  },
  "capital": {
    "cash": "100000",
    "buying_power": "100000",
    "open_order_reserved_estimate": "0",
    "allocatable_capital_estimate": "100000"
  }
}
```

关键金额使用字符串或Decimal，不使用二进制float做关键计算。

第二阶段可以使用金额决定权重和现金比例，但不能计算最终订单数量。

---

## 9. 持仓与挂单

持仓至少包含：

```json
{
  "symbol": "MU",
  "side": "long",
  "quantity": "10",
  "available_quantity": "10",
  "average_entry_price": "90",
  "current_price": "100",
  "market_value": "1000",
  "current_weight": "0.01",
  "in_current_coarse": true,
  "new_position_screen_eligible": true
}
```

挂单至少包含：

```json
{
  "client_order_id": "...",
  "symbol": "MU",
  "side": "buy",
  "type": "limit",
  "quantity": "5",
  "filled_quantity": "0",
  "remaining_quantity": "5",
  "limit_price": "95",
  "status": "new",
  "extended_hours": false,
  "reserved_capital_estimate": "475"
}
```

未完成买单必须视为潜在持仓和已占用资金。

---

## 10. 候选与市场数据

60只candidate至少包含：

```json
{
  "rank": 1,
  "symbol": "MU",
  "asset_type": "stock",
  "sector": "Information Technology",
  "industry": "Semiconductors",
  "research_eligible": true,
  "screen_new_position_eligible": true,
  "selection_reason": "...",
  "main_risks": [],
  "key_factors": [],
  "daily_summary": {},
  "intraday_summary": {},
  "latest_quote": {},
  "asset_status": {},
  "source_references": []
}
```

Stage D的盘中数据用于组合判断，不要求达到第三阶段的最终执行实时性。

市场上下文至少包括：

- market phase
- SPY、QQQ、IWM、DIA摘要
- 主要行业ETF摘要
- 风险代理
- 当前现金比例
- 当前行业暴露
- 数据质量
- market data cutoff

缺失值必须使用 `null`、`no_data` 或warning，不得使用0冒充。

---

## 11. 输入签名与指纹

Portfolio输入签名至少包含：

```text
profile_id
strategy_id
strategy_version
risk_profile及hash
guidance_hash
coarse revision signature
coarse output hash
positions fingerprint
open orders fingerprint
allocatable capital
market data cutoff
portfolio Prompt hash
portfolio AGENTS hash
portfolio Schema hash
portfolio policy hash
```

不要包含cycle_id和临时路径。

实现：

```python
build_positions_fingerprint(...)
build_open_orders_fingerprint(...)
build_capital_fingerprint(...)
```

持仓指纹包含：

```text
symbol
side
quantity
available_quantity
average_entry_price
```

不要包含不断变化的current_price、market_value或unrealized_pl。

挂单指纹包含：

```text
client_order_id
symbol
side
type
remaining_quantity
limit_price
stop_price
status
extended_hours
```

---

## 12. Run / reuse / block

实现：

```python
should_run_portfolio(context) -> PortfolioReuseDecision
```

返回：

```text
run
reuse
block
```

必须run：

- `--force-rebalance`
- 没有同日有效portfolio
- strategy version变化
- risk profile变化
- guidance hash变化
- coarse revision变化
- 持仓指纹变化
- 挂单指纹变化
- 可分配资本变化达到阈值
- 旧方案过期
- 旧方案业务校验失效
- 未来reconciliation标记资金释放
- 未来execution要求replan

可以reuse：

- 同profile
- 同strategy version
- 同risk profile
- 同guidance
- 同coarse revision
- 持仓与挂单一致
- 资本变化低于阈值
- 方案未过期
- output和validation合法
- 无force参数

block：

- 基础快照关键失败
- coarse无有效输出
- account blocked
- Schema损坏
- strategy release不包含portfolio能力

---

## 13. Portfolio策略配置

新增：

```text
strategies/core_long/1.1.0/config/portfolio_policy.json
```

建议结构：

```json
{
  "schema_version": "1.0",
  "portfolio_valid_minutes": 240,
  "capital_change_materiality": {
    "absolute_usd": "100",
    "relative_fraction": "0.01"
  },
  "target_holdings": {
    "minimum": 3,
    "maximum": 20
  },
  "weight_tolerance": "0.005",
  "minimum_target_weight": "0.005",
  "allow_increase_outside_coarse": false,
  "allow_empty_portfolio": true
}
```

具体值属于策略配置，不得散落硬编码。

---

## 14. Codex工作区

```text
portfolio/workspace/
├── AGENTS.md
├── data/
│   ├── portfolio_input.json
│   ├── initial_guidance.json
│   ├── coarse_output.json
│   ├── base_snapshot.json
│   └── market/
├── config/
│   ├── portfolio_policy.json
│   └── risk_profile.json
├── prompts/
│   └── portfolio.md
├── schemas/
│   └── portfolio_output.schema.json
└── .tmp/codex/
```

Codex只允许写 `.tmp/codex/`。

不得读取：

- `.env`
- account bindings
- broker credentials
- 其他profile runtime

---

## 15. Portfolio Prompt与AGENTS

AGENTS必须明确：

- 只执行第二阶段
- 从60只候选和已有持仓形成战略组合
- 候选外已有持仓只能hold/reduce/close
- 不输出最终数量
- 不生成实际订单
- 不调用Alpaca
- initial guidance是建议，不是强制
- 必须保留合理现金
- 不重复分配挂单占用资金
- 最终只返回严格JSON

Prompt要求：

1. 阅读账户、持仓、挂单和资金
2. 阅读initial guidance
3. 深入研究最可能进入组合的候选
4. 可以自由联网
5. 优先官方公司、SEC、政府、交易所和可靠媒体
6. 综合质量、估值、趋势、催化剂、风险和相关性
7. 决定目标现金比例
8. 决定目标持仓和权重
9. 对已有持仓给出动作
10. 对挂单给出战略建议
11. 说明如何处理initial guidance
12. 标记第三阶段需重点复核的风险
13. 输出严格JSON，不加Markdown

---

## 16. Portfolio输出

顶层建议：

```json
{
  "schema_version": "1.0",
  "stage": "portfolio_decision",
  "profile_id": "paper2",
  "strategy_id": "core_long",
  "strategy_version": "1.1.0",
  "run_date": "2026-07-23",
  "cycle_id": "...",
  "generated_at": "...",
  "input_signature": "...",
  "status": "success",
  "network_research": {},
  "guidance_response": {},
  "market_assessment": {},
  "allocation": {},
  "decisions": [],
  "open_order_assessments": [],
  "watchlist": [],
  "execution_focus": [],
  "requires_rebalance_next_cycle": false,
  "valid_until": "...",
  "warnings": [],
  "source_references": []
}
```

status：

```text
success
success_local_only
```

Allocation：

```json
{
  "target_cash_weight": "0.30",
  "target_invested_weight": "0.70",
  "target_position_count": 8,
  "maximum_single_symbol_weight": "0.10",
  "maximum_sector_weight": "0.30",
  "deployment_posture": "gradual",
  "rationale": "..."
}
```

必须满足：

```text
target_cash_weight + target_invested_weight = 1
```

---

## 17. Decision结构

```json
{
  "symbol": "MU",
  "current_position": true,
  "in_current_coarse": true,
  "action": "increase",
  "conviction": "medium",
  "target_weight": "0.05",
  "maximum_weight": "0.07",
  "priority": 2,
  "price_plan": {
    "currency": "USD",
    "entry_zone_low": "95",
    "entry_zone_high": "102",
    "do_not_chase_above": "108",
    "review_below": "85",
    "notes": "..."
  },
  "protection_plan": {
    "style": "review_threshold",
    "reference_price": "85",
    "maximum_loss_fraction": "0.10",
    "notes": "..."
  },
  "thesis": "...",
  "risks": [],
  "catalysts": [],
  "portfolio_role": "...",
  "execution_checks": [],
  "source_references": []
}
```

action：

```text
open
increase
hold
reduce
close
watch
avoid
```

conviction：

```text
high
medium
low
```

---

## 18. 禁止字段

Portfolio output任何位置不得出现：

```text
quantity
qty
notional
order_type
time_in_force
extended_hours
client_order_id
broker_order_id
submit
submitted
filled
```

`price_plan`只是战略价格区间，不是实际订单。

Python必须递归检查禁止字段。

---

## 19. Open order assessment

```json
{
  "client_order_id": "...",
  "symbol": "MU",
  "assessment": "keep",
  "reason": "...",
  "conflicts_with_target": false
}
```

assessment：

```text
keep
review
cancel
replace
```

Stage D不能调用取消接口。

---

## 20. Guidance response

输出说明如何处理initial guidance：

```json
{
  "summary": "考虑了半导体方向，但限制行业集中度",
  "accepted_points": ["研究MU"],
  "modified_points": ["控制半导体总权重"],
  "rejected_points": [],
  "constraint_conflicts": []
}
```

不要求接受用户的正向建议。

---

## 21. Python业务校验

至少检查：

1. stage、profile、strategy正确
2. run_date和cycle_id正确
3. input_signature匹配
4. status合法
5. valid_until晚于generated_at
6. cash与invested合计为1
7. 正权重合计与invested一致
8. 权重非负
9. target_weight不大于maximum_weight
10. 单标的不超风险上限
11. 行业不超硬上限
12. target position count一致
13. 持仓数量符合策略配置
14. 新仓来自coarse且eligible
15. 候选外持仓不得open/increase
16. close/watch/avoid权重为0
17. open标的当前quantity为0
18. increase标的已有持仓
19. reduce目标权重低于当前权重
20. symbol不重复
21. 未完成买单占用资金被考虑
22. 目标资本不超可用范围
23. 禁止字段不存在
24. 来源结构合法
25. local_only产生warning
26. guidance response存在
27. 空组合仅在配置允许时通过
28. 不包含最终订单数量

所有关键计算使用Decimal。

---

## 22. Schema严格预检与Codex失败

复用coarse的严格Schema预检：

- additionalProperties false
- properties全部required
- 避免oneOf、anyOf、allOf
- 不依赖format
- 复杂关联交给Python

Codex：

- 一次主调用
- 最多一次安全重试
- stdout/stderr脱敏
- fake runner测试
- 不无限重试

失败标记：

```text
failed_retriable
```

不得破坏旧有效portfolio。

---

## 23. Portfolio索引

账户、策略、日期级 `daily_state.json` 增加：

```json
{
  "latest_valid_portfolio_cycle_id": null,
  "latest_valid_portfolio_output_path": null,
  "latest_portfolio_input_signature": null,
  "latest_portfolio_valid_until": null
}
```

不得跨profile、strategy version或纽约日期复用。

---

## 24. 第二阶段后复查

新增 `src/v2/review.py`。

人工模式提示：

```text
请输入本轮执行前补充意见，直接回车继续：
```

保存：

```text
cycles/<cycle_id>/user_review.json
```

结构：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper2",
  "strategy_id": "core_long",
  "strategy_version": "1.1.0",
  "run_date": "2026-07-23",
  "cycle_id": "...",
  "mode": "user_comment",
  "raw_comment": "MU最多5%，今天不要买TSLA",
  "review_hash": "...",
  "constraints": [],
  "prohibitions": [],
  "preferences": [],
  "trade_requests": [],
  "applies_to": ["execution"],
  "created_at": "...",
  "created_at_new_york": "..."
}
```

Stage D不需要AI解析raw_comment。

`--no-review` 或 `--unattended`：

```text
mode = skipped_by_flag
raw_comment = ""
```

非TTY且需要review时应报错并提示使用 `--no-review` 或 `--unattended`。

---

## 25. 状态机

成功run：

```text
stages.portfolio = completed
```

成功reuse：

```text
stages.portfolio = skipped
reused_portfolio_cycle_id = source cycle
```

随后：

```text
current_step = COLLECT_REVIEW
```

Review完成：

```text
stages.review = completed或skipped
current_step = REFRESH_EXECUTION_DATA
```

Stage D最终停在：

```text
REFRESH_EXECUTION_DATA
```

不得进入RUN_EXECUTION。

---

## 26. 主流程输出

示例：

```text
第一阶段动作：reuse
候选数量：60
第二阶段动作：run
目标现金比例：30.00%
目标持仓数量：8
第二阶段联网：success
第二阶段校验：通过
执行前复查：skipped_by_flag
下一步骤：REFRESH_EXECUTION_DATA
第三阶段尚未实现
未生成或提交订单
```

即使有 `--allow-trade`：

```text
交易提交权限：enabled
提交订单数：0
```

---

## 27. 测试

新增：

```text
tests/v2/
├── test_portfolio_models.py
├── test_portfolio_input.py
├── test_portfolio_signature.py
├── test_portfolio_schema.py
├── test_portfolio_validation.py
├── test_portfolio_workspace.py
├── test_portfolio_stage.py
├── test_portfolio_reuse.py
├── test_portfolio_review.py
├── test_strategy_1_1_release.py
└── test_main_stage_d.py
```

必须覆盖：

1. 合法组合通过
2. cash+invested不为1失败
3. 目标权重合计错误
4. 单标的超限
5. 行业超硬上限
6. 新仓不在60只
7. eligible=false
8. 候选外持仓hold/reduce/close通过
9. 候选外持仓increase失败
10. close但权重非0失败
11. duplicate symbol
12. quantity等禁止字段
13. profile或strategy不匹配
14. signature不匹配
15. valid_until过期
16. 挂单资金被考虑
17. 目标资本超限
18. guidance传入且可被拒绝
19. local_only警告
20. Schema预检失败不调用Codex
21. 安全重试
22. 失败不破坏旧方案
23. 同日状态不变复用
24. 持仓变化重跑
25. 挂单变化重跑
26. 资本变化阈值
27. coarse revision变化
28. guidance变化
29. strategy或risk变化
30. `--force-rebalance`
31. `--no-review`
32. unattended
33. 人工review
34. 非TTY安全
35. paper2使用1.1.0
36. Stage D停在REFRESH_EXECUTION_DATA
37. 没有订单提交
38. 原103项测试保持通过

测试命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_d PYTHONPATH=src .Alpaca/bin/python -m unittest discover   -s tests/v2   -p 'test_*.py'   -v
```

---

## 28. 真实烟雾测试

无人值守：

```bash
.Alpaca/bin/python -u src/v2/main.py   --profile paper2   --unattended   --allow-trade
```

带启动建议：

```bash
.Alpaca/bin/python -u src/v2/main.py   --profile paper2   --guidance "考虑MU和半导体，但限制行业集中度"   --no-review   --allow-trade
```

预期：

```text
coarse run或reuse
portfolio run
组合Schema通过
停在REFRESH_EXECUTION_DATA
提交订单数0
```

只有实际执行并提供日志后，才可宣称真实烟雾通过。

---

## 29. 完成与发布

完成后：

```bash
git add .
git commit -m "Implement WA Trader v2 Stage D portfolio decisions"

git switch main
git merge --no-ff feature/stage-d-portfolio
git tag stage-d-complete
```

此时 `core_long@1.1.0` 变为不可变。

---

## 30. 完成标准

- 1.0.0未修改
- 创建1.1.0策略release
- paper2可使用1.1.0
- Portfolio input完整
- Initial guidance传入
- 账户、持仓、挂单和资金纳入决策
- 输出目标现金和目标权重
- 不输出最终数量或订单
- 严格Schema和业务校验
- 同日可复用
- Material变化触发重跑
- Post-portfolio review实现
- `--no-review`和unattended正确
- 主流程停在REFRESH_EXECUTION_DATA
- 无submit_order
- Live仍拒绝
- 原103项测试保持通过
- 新测试全部通过
- `.env`未修改
- 无v1导入
- Git工作区最终干净

---

## 31. 交给Codex的指令

```text
Stage C.5已经完成并合并main：
- 103项测试通过
- main commit 3be0094
- stage-c5-complete tag f0d0862
- 主流程停在RUN_PORTFOLIO

请阅读：
- docs/WA_Trader_v2_implementation_spec.md
- docs/WA_Trader_v2_stage_b.md
- docs/WA_Trader_v2_stage_c.md
- docs/WA_Trader_v2_stage_c5.md
- docs/WA_Trader_v2_stage_d.md

现在只实施Stage D：第二阶段组合决策。

关键要求：
1. 先运行现有103项测试；
2. 创建feature/stage-d-portfolio；
3. 不修改strategies/core_long/1.0.0；
4. 创建strategies/core_long/1.1.0；
5. 复制1.0.0 coarse内容并新增portfolio Prompt、AGENTS、Schema和policy；
6. 实现models/portfolio.py和stages/portfolio.py；
7. 实现portfolio run/reuse/block；
8. initial guidance完整传入；
9. 输入包含账户、持仓、挂单、可分配资金、60只候选和行情；
10. 输出目标现金、目标权重和战略动作；
11. 禁止输出quantity、order type和最终订单；
12. Python执行严格Schema和业务校验；
13. 复用时将合法output复制到当前cycle并记录来源；
14. 增加post-portfolio review；
15. --no-review和--unattended不得暂停；
16. 人工模式在第二阶段后只询问一次；
17. 主流程停在REFRESH_EXECUTION_DATA；
18. 不实现第三阶段；
19. 不生成或提交订单；
20. --allow-trade本阶段仍不能触发提交；
21. --live继续拒绝；
22. 不导入v1；
23. 不修改.env；
24. 保持现有103项测试通过并增加Stage D测试；
25. 完成后报告strategy version、文件变更、测试命令和结果；
26. 最后给出真实portfolio烟雾命令，但只有实际执行后才能声称通过。
```
