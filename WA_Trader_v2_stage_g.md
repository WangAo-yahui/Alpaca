# WA Trader v2：Stage G Paper订单提交、对账与日报

版本：2026-07-25-stage-g-v1

## 0. 当前稳定基线

```text
main merge commit: 6d23503
tag: stage-f-complete
tests: 168/168 PASS
default profile: paper1
strategy: core_long@1.2.0
risk profile: paper_standard@1.1.0
order policy: paper_equity@1.0.0
current step: SUBMIT_ORDERS
```

Stage F 已完成两次真实验证：

```text
普通dry-run：20260725T023051
--allow-trade：20260725T023332
```

两次均生成完整订单产物、停在 `SUBMIT_ORDERS`，提交、取消、替换数量均为0。

Stage G 是第一个允许执行 Alpaca paper 写操作的阶段。

---

## 1. Stage G目标

实现完整 paper 闭环：

```text
读取validated orders
→ 最终提交前复核
→ 执行取消依赖
→ 确认取消结果
→ 刷新账户、持仓、挂单和资金
→ 重新校验dependent replacement
→ 提交approved paper订单
→ 原子保存每次券商写结果
→ 即时对账
→ 保存broker_submission和reconciliation
→ 写cycle summary
→ 创建或更新日报
→ 标记cycle完成
```

完成后：

```bash
python3 -u src/v2/main.py \
  --unattended \
  --allow-trade
```

会真正提交 **paper1** 的已批准订单。

没有 `--allow-trade` 时仍跑完整流程，但不执行任何券商写操作。

---

## 2. 网络写操作不能承诺绝对exactly-once

以下情况可能发生：

```text
请求已到达券商并被接受
→ 本地在收到响应前网络超时
```

盲目重试可能重复下单。

Stage G使用：

```text
稳定client_order_id
+ 写前journal
+ 超时后按client_order_id查询
+ 不盲目重试
+ 状态不确定时停止并对账
```

无法确认的写操作标记：

```text
submission_uncertain
```

不得假设失败后重新提交。

---

## 3. Git与版本边界

先运行168项测试：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_g_baseline \
PYTHONPATH=src \
.Alpaca/bin/python -m unittest discover \
  -s tests/v2 \
  -p 'test_*.py' \
  -v
```

创建分支：

```bash
git switch -c feature/stage-g-paper-submission
```

不得修改：

```text
strategies/core_long/1.2.0/
config/v2/risk_profiles/paper_standard-1.1.0.json
config/v2/order_policies/paper_equity-1.0.0.json
```

新增：

```text
config/v2/submission_policies/
└── alpaca_paper-1.0.0.json
```

paper1 profile新增：

```json
{
  "submission_policy": "alpaca_paper@1.0.0"
}
```

Cycle release metadata记录submission policy及其hash。

---

## 4. Submission policy

建议：

```json
{
  "schema_version": "1.0",
  "policy_id": "alpaca_paper",
  "version": "1.0.0",
  "environment": "paper",
  "allow_submit": true,
  "allow_cancel": true,
  "allow_direct_replace": false,
  "submit_orders_sequentially": true,
  "stop_after_uncertain_write": true,
  "status_poll": {
    "enabled": true,
    "initial_seconds": 1,
    "maximum_seconds": 10,
    "interval_seconds": 1
  },
  "write_retry": {
    "blind_retry_count": 0,
    "lookup_by_client_order_id_after_error": true
  },
  "immediate_reconciliation": true,
  "persist_before_each_write": true,
  "persist_after_each_response": true
}
```

Stage G v1禁用直接replace，统一使用：

```text
cancel old
→ 确认终态
→ 刷新
→ 重新校验
→ submit replacement
```

---

## 5. 新增文件

```text
src/v2/models/submission.py

src/v2/trading/
├── order_submitter.py
├── order_action_executor.py
├── submission_journal.py
└── reconciliation.py

src/v2/reports/
├── __init__.py
└── daily_report.py

schemas/v2/
├── submission_intent.schema.json
├── broker_submission.schema.json
├── reconciliation.schema.json
└── cycle_summary.schema.json
```

扩展：

```text
src/v2/data/orders.py
src/v2/models/state.py
src/v2/state_machine.py
src/v2/main.py
```

---

## 6. Stage G目录产物

```text
cycles/<cycle_id>/
├── orders/
│   ├── pretrade_snapshot.json
│   ├── proposed.json
│   ├── validated.json
│   ├── request_specs.json
│   ├── action_plan.json
│   ├── submission_intent.json
│   ├── submission_journal.json
│   ├── broker_submission.json
│   └── reconciliation.json
├── cycle_summary.json
└── cycle_state.json
```

日报：

```text
reports/v2/accounts/paper1/
strategies/core_long/1.2.0/
daily/YYYY-MM-DD.md
```

---

## 7. Submission intent

任何写调用前原子保存：

```text
orders/submission_intent.json
```

至少包含：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper1",
  "environment": "paper",
  "run_date": "...",
  "cycle_id": "...",
  "created_at": "...",
  "allow_trade": true,
  "validated_orders_hash": "...",
  "request_specs_hash": "...",
  "action_plan_hash": "...",
  "submission_policy": "alpaca_paper@1.0.0",
  "approved_plan_ids": [],
  "dependent_plan_ids": [],
  "cancel_action_ids": [],
  "expected_write_count": 0,
  "status": "prepared"
}
```

写入成功前不得调用券商。

---

## 8. Submission journal

每个操作状态：

```text
prepared
request_started
response_received
lookup_confirmed
reconciled
completed
failed_definite
uncertain
skipped
```

示例：

```json
{
  "operation_id": "op-...",
  "operation_type": "submit",
  "plan_id": "plan-...",
  "client_order_id": "wa2-...",
  "broker_order_id": null,
  "symbol": "MU",
  "state": "prepared",
  "attempt_count": 0,
  "prepared_at": "...",
  "request_started_at": null,
  "response_received_at": null,
  "last_checked_at": null,
  "error": null
}
```

每次状态变化均原子保存。

---

## 9. 最终写前安全检查

必须同时确认：

1. `--allow-trade`存在；
2. profile是paper1；
3. environment是paper；
4. account hash匹配；
5. profile enabled；
6. live=false；
7. cycle在SUBMIT_ORDERS；
8. validated.json合法；
9. submission_requested=true；
10. submission_performed=false；
11. validated、request specs和action plan hash匹配；
12. 文件未被手工修改；
13. submission policy允许；
14. 没有uncertain操作；
15. 当前cycle没有已完成submission；
16. release metadata一致；
17. 凭据属于paper1；
18. 账户可交易；
19. 没有全局kill switch；
20. emergency stop=false。

任一失败均不得写。

---

## 10. 双重权限与Kill switch

新增：

```json
{
  "paper_submission_enabled": true,
  "live_submission_enabled": false,
  "cancel_enabled": true,
  "emergency_stop": false
}
```

权限必须同时满足：

```text
CLI --allow-trade
AND profile.environment=paper
AND profile.enabled
AND submission policy allow_submit
AND paper_submission_enabled
AND emergency_stop=false
```

这仍然保持用户只需一条命令。

---

## 11. 操作顺序

顺序执行，不并发：

```text
处理cancel动作
→ 确认cancel结果
→ 刷新并重校验replacement
→ 提交独立approved订单
→ 提交已解锁replacement
```

原因：

- 每次写可能改变buying power；
- 每次卖单可能改变available quantity；
- 依赖错误必须阻止后续；
- 顺序日志更容易恢复。

---

## 12. Cancel执行

流程：

```text
查询目标订单最新状态
→ 若已terminal则跳过取消
→ journal=request_started
→ cancel_order_by_id
→ 保存同步结果或异常
→ 轮询订单直到terminal或超时
```

终态：

```text
filled
canceled
expired
rejected
replaced
```

如果取消前成交：

```text
视为filled
→ 不提交原replacement
→ 刷新持仓
→ replacement重新规划或阻止
```

`pending_cancel` 不代表取消完成。

---

## 13. Replace意图

Stage G v1不调用直接replace API。

流程：

```text
确认旧订单存在
→ cancel
→ 确认canceled/expired/rejected
→ 刷新account/positions/orders/quote
→ 局部重新运行硬校验
→ 合法后提交replacement
```

以下情况不得直接提交replacement：

```text
filled
partially_filled
pending_cancel
pending_replace
状态不确定
```

部分成交后必须重新计算剩余目标。

---

## 14. 提交approved订单

`order_submitter.py` 只读取：

```text
validated.json
request_specs.json
submission_journal.json
```

只提交：

```text
status=approved
dependencies全部完成
尚不存在broker order
```

流程：

```text
按client_order_id查询
→ 已存在则记录lookup_confirmed
→ journal=request_started
→ 本地构造SDK OrderRequest
→ TradingClient.submit_order
→ 立即保存返回Order
→ 再按ID或client_order_id查询
→ 更新journal
```

不得直接从execution或proposed提交。

---

## 15. 写异常处理

### 确定失败

本地构造失败或HTTP明确拒绝且确认未创建订单：

```text
failed_definite
```

### 不确定失败

连接超时、响应丢失或进程中断：

```text
按client_order_id查询
```

找到则：

```text
lookup_confirmed
```

无法证明未受理：

```text
uncertain
→ 停止相关写操作
→ 不盲目重试
→ 下一次运行先对账
```

---

## 16. Broker submission

保存：

```text
orders/broker_submission.json
```

至少包含：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper1",
  "environment": "paper",
  "run_date": "...",
  "cycle_id": "...",
  "started_at": "...",
  "completed_at": "...",
  "submission_requested": true,
  "submission_performed": true,
  "validated_orders_hash": "...",
  "submitted_count": 0,
  "existing_count": 0,
  "rejected_count": 0,
  "uncertain_count": 0,
  "cancel_requested_count": 0,
  "cancel_confirmed_count": 0,
  "operations": [],
  "global_errors": [],
  "global_warnings": []
}
```

每个操作记录：

- plan id；
- client order id；
- broker order id；
- 请求摘要；
- broker状态；
- filled qty；
- average fill price；
- 错误码与脱敏信息。

---

## 17. Order状态

必须认识：

```text
accepted
pending_new
new
partially_filled
filled
done_for_day
canceled
expired
replaced
pending_cancel
pending_replace
rejected
suspended
calculated
accepted_for_bidding
stopped
```

活跃或可能继续变化：

```text
accepted
pending_new
new
partially_filled
done_for_day
pending_cancel
pending_replace
accepted_for_bidding
stopped
suspended
```

终态：

```text
filled
canceled
expired
rejected
replaced
```

---

## 18. 即时对账

写阶段结束后重新获取：

- account；
- positions；
- open orders；
- today orders；
- 每个提交订单；
- 每个取消目标；
- cash；
- buying power；
- portfolio value。

保存：

```text
orders/reconciliation.json
```

至少包含：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper1",
  "cycle_id": "...",
  "reconciled_at": "...",
  "account": {},
  "positions": [],
  "open_orders": [],
  "tracked_orders": [],
  "summary": {
    "filled": 0,
    "partially_filled": 0,
    "open": 0,
    "canceled": 0,
    "rejected": 0,
    "expired": 0,
    "uncertain": 0
  },
  "capital": {},
  "requires_next_cycle_rebalance": false,
  "reasons": [],
  "errors": [],
  "warnings": []
}
```

---

## 19. 不长期等待成交

只短时间poll：

- 立即fill；
- 立即reject；
- cancel确认；
- submit同步状态。

订单仍为：

```text
accepted
new
partially_filled
done_for_day
```

时：

```text
保存为open
完成当前cycle
下一次运行再对账
```

未成交不是程序错误。

---

## 20. 每次运行先维护旧订单

主流程开头：

```text
解析profile
→ 查找未完成订单与cycle
→ 对账旧订单
→ 更新旧cycle
→ 更新日报
→ 再进入当前轮次
```

触发 `intraday_rebalance`：

- rejected；
- canceled；
- expired；
- partial fill导致持仓变化；
- fill后资金变化；
- 资金释放；
- replacement失败；
- execution要求replan。

触发 `execution_refresh`：

- portfolio结构仍有效；
- 订单仍open；
- 只是价格变化；
- 需要重新判断保留或调整。

仅状态维护时使用 `maintenance_only`。

---

## 21. Partial fill

部分成交拆分：

```text
filled_quantity
remaining_quantity
average_fill_price
```

下一轮：

- 已成交部分进入持仓；
- 未成交部分仍是open-order暴露；
- 不重复提交原完整数量；
- 是否保留、取消、调整由新execution判断；
- 持仓指纹改变后由materiality规则决定是否重跑portfolio。

---

## 22. Cycle状态

必须能区分：

```text
completed_dry_run
completed_no_action
completed_with_submissions
completed_with_open_orders
completed_with_partial_fills
completed_with_rejections
blocked_submission_uncertain
```

若不扩大顶层枚举，也必须在cycle summary中清楚记录。

正常完成：

```text
current_step=COMPLETE
resume_allowed=false
```

状态不确定时不得重复提交，只允许后续维护对账。

---

## 23. Cycle summary

保存：

```text
cycle_summary.json
```

至少记录：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper1",
  "strategy_id": "core_long",
  "strategy_version": "1.2.0",
  "run_date": "...",
  "cycle_id": "...",
  "cycle_kind": "daily_full",
  "started_at": "...",
  "completed_at": "...",
  "final_status": "...",
  "coarse": {},
  "portfolio": {},
  "execution": {},
  "orders": {
    "proposed": 0,
    "approved": 0,
    "submitted": 0,
    "filled": 0,
    "partially_filled": 0,
    "open": 0,
    "rejected": 0
  },
  "capital": {},
  "warnings": [],
  "errors": []
}
```

---

## 24. 日报

实现：

```text
src/v2/reports/daily_report.py
```

首轮详细日报包含：

- profile与账户hash前缀；
- app、strategy、risk、order、submission版本；
- initial guidance；
- 市场概况；
- coarse摘要；
- portfolio目标；
- execution结果；
- proposed和validated订单；
- broker submission；
- fill/open/reject；
- 当前持仓和现金；
- 风险与后续事项。

后续轮次只追加：

```markdown
## 14:05 ET 更新

- Cycle：...
- 类型：execution_refresh
- 旧订单变化：...
- 新提交：...
- 成交：...
- 部分成交：...
- 拒绝/取消：...
- 资金变化：...
- 当前持仓变化：...
- 下一轮建议：...
```

---

## 25. Dry-run和no-action

没有 `--allow-trade` 时：

- 券商写调用为0；
- 仍生成broker_submission；
- mode为dry_run；
- 完成只读对账；
- 写cycle summary和日报；
- 状态为completed_dry_run。

有 `--allow-trade` 但没有approved订单时：

```text
submitted_count=0
completed_no_action
```

这是正常成功。

---

## 26. 写权限白名单

生产写调用只能存在于：

```text
order_submitter.py
order_action_executor.py
```

Stage G v1允许：

```text
TradingClient.submit_order
TradingClient.cancel_order_by_id
```

不允许：

```text
replace_order_by_id
cancel_orders
close_all_positions
close_position
```

其他模块不得直接写券商。

---

## 27. 恢复逻辑

如果在SUBMIT_ORDERS中断：

1. 读取submission intent；
2. 读取journal；
3. 对request_started但无response的操作按ID查询；
4. 找到订单则补写结果；
5. 找不到且不能证明未受理则标记uncertain；
6. prepared但从未started的操作重新执行最终校验；
7. completed操作不重复；
8. 完成后即时对账。

恢复时不重新运行Coarse或Codex，除非另开新cycle。

---

## 28. 网络重试规则

只读请求可以有限重试。

券商写请求：

```text
blind retry=0
```

异常后先查询client_order_id或broker order id。

无法证明未受理时不重试。

---

## 29. 测试

新增：

```text
tests/v2/
├── test_submission_models.py
├── test_submission_policy.py
├── test_submission_journal.py
├── test_submission_permissions.py
├── test_order_submitter.py
├── test_order_submit_timeout.py
├── test_order_submit_idempotency.py
├── test_order_action_executor.py
├── test_cancel_race.py
├── test_replacement_dependency.py
├── test_reconciliation.py
├── test_partial_fill_reconciliation.py
├── test_cycle_completion.py
├── test_daily_report.py
├── test_same_day_rerun.py
├── test_stage_g_write_whitelist.py
└── test_main_stage_g.py
```

必须覆盖：

1. 无allow-trade绝不写；
2. kill switch关闭不写；
3. live不写；
4. account hash不匹配不写；
5. 产物hash变化不写；
6. 已完成submission不重复；
7. journal写前持久化；
8. submit成功立即持久化；
9. submit明确reject；
10. timeout后按client ID找到；
11. timeout后无法确认标记uncertain；
12. uncertain不盲目重试；
13. existing client ID不重复；
14. cancel成功；
15. cancel时先成交；
16. pending_cancel不当作完成；
17. replacement等待取消确认；
18. partial fill后重算replacement；
19. direct replace调用为0；
20. 顺序提交；
21. buying power变化后复核后续订单；
22. 依赖失败阻止replacement；
23. broker响应脱敏；
24. open order正常完成；
25. partial fill正常完成；
26. reject/cancel触发下轮rebalance；
27. 启动先对账；
28. same-day execution_refresh；
29. dry-run完整但0写；
30. no-action正常完成；
31. detailed report只创建一次；
32. 后续cycle只追加；
33. journal恢复不重复写；
34. 写API只存在白名单模块；
35. 最终current_step=COMPLETE；
36. 原168项测试保持通过。

---

## 30. 真实paper验证顺序

### 第一步：再次dry-run

```bash
python3 -u src/v2/main.py \
  --profile paper1 \
  --unattended
```

### 第二步：allow-trade但自然无订单

如果策略仍保持100%现金：

```bash
python3 -u src/v2/main.py \
  --profile paper1 \
  --unattended \
  --allow-trade
```

验证no-action提交路径。

### 第三步：自然产生approved订单

运行同一命令并允许提交paper订单。

不得加入“强制买一股”后门。

若没有自然approved订单，只能声明fake集成覆盖和no-action真实路径通过，不能声称真实submit已验证。

---

## 31. 第一次真实写后检查

检查：

```text
broker_submission.json
submission_journal.json
reconciliation.json
cycle_summary.json
daily report
Alpaca dashboard
```

确认：

- client_order_id一致；
- symbol/side/qty/limit正确；
- 使用paper1；
- 没有重复订单；
- buying power变化合理；
- 下次运行不重复提交。

异常时：

```text
emergency_stop=true
```

只运行maintenance和对账。

---

## 32. 完成标准

- 只有paper1可写；
- CLI与部署开关双重授权；
- 写前intent与journal落盘；
- 写请求不盲目重试；
- client_order_id查询去重；
- cancel race安全；
- replacement取消确认后重校验；
- direct replace禁用；
- 每个响应即时持久化；
- 即时对账；
- partial fill正确；
- open order不阻塞结束；
- 下一轮先对账；
- dry-run完整但0写；
- no-action正常；
- 日报完整；
- cycle最终COMPLETE；
- live继续拒绝；
- 全部测试通过；
- `.env`未修改；
- 无v1导入；
- 工作树干净。

---

## 33. Git发布

```bash
git add .
git commit -m "Implement WA Trader v2 Stage G paper execution"

git switch main
git merge --no-ff feature/stage-g-paper-submission
git tag stage-g-paper-complete
```

标签必须明确是paper，不得暗示live已就绪。

---

## 34. 给Codex的指令

```text
Stage F已经完成并发布：
- main commit 6d23503
- tag stage-f-complete
- 168项测试通过
- 两次真实Stage F运行均停在SUBMIT_ORDERS
- submit/cancel/replace均为0
- paper1使用core_long@1.2.0、paper_standard@1.1.0、paper_equity@1.0.0

请阅读docs/WA_Trader_v2_stage_g.md并只实施Stage G。

要求：
1. 先运行168项基线测试；
2. 创建feature/stage-g-paper-submission；
3. 不修改现有strategy、risk或order policy版本；
4. 新增alpaca_paper@1.0.0 submission policy；
5. 实现models/submission.py；
6. 实现submission_journal.py；
7. 实现order_submitter.py；
8. 实现order_action_executor.py；
9. 实现reconciliation.py；
10. 实现daily_report.py；
11. 只有paper1、--allow-trade和全部部署开关通过才允许写；
12. live始终拒绝；
13. 写前原子保存submission intent与journal；
14. broker写请求blind retry=0；
15. 写异常后按client_order_id查询；
16. 无法确认时标记uncertain并停止；
17. 只提交validated status=approved；
18. direct replace禁用；
19. replace使用cancel-confirm-refresh-revalidate-submit；
20. cancel未确认前不得释放资金或提交replacement；
21. partial fill后重新计算remaining intent；
22. 每个响应立即持久化；
23. 完成即时对账；
24. 下一次main启动首先维护旧订单；
25. 实现首轮详细日报和后续增量更新；
26. dry-run也生成cycle summary和report但0写；
27. open order和partial fill正常完成；
28. 主流程最终推进到COMPLETE；
29. 不导入v1；
30. 不修改.env；
31. 保持旧测试并增加Stage G测试；
32. broker写API只存在白名单模块；
33. 完成后报告文件、版本、测试、真实dry-run和paper写结果；
34. 若没有自然approved订单，不得加入强制买入后门，也不得声称真实提交已验证。
```

---

## 35. 实施时核对官方规则

实施前必须用当前Alpaca官方文档和当前安装的alpaca-py核对：

- submit_order(OrderRequest)；
- get order by ID/client order ID；
- cancel_order_by_id；
- 订单状态生命周期；
- extended-hours订单约束；
- overnight tradable/halted属性；
- replace竞态与可替换字段；
- fractional限制。

规则必须通过版本化policy和capability adapter管理，不能假设永远不变。
