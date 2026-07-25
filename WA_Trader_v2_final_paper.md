# WA Trader v2：最终 Paper 完整运行实施指令

版本：2026-07-25-final-paper-v1

## 0. 当前真实基线

当前完成到 Stage F：

```text
main merge commit: 6d23503
tag: stage-f-complete
tests: 168/168 PASS
profile: paper1
strategy: core_long@1.2.0
risk: paper_standard@1.1.0
order policy: paper_equity@1.0.0
current step: SUBMIT_ORDERS
```

已完成：

- 三阶段决策；
- initial guidance；
- post-portfolio review；
- 60只候选；
- portfolio；
- execution；
- pre-trade refresh；
- Decimal数量计算；
- proposed orders；
- validated orders；
- request specs；
- 幂等client_order_id；
- 扩展时段校验；
- 无broker写调用；
- 真实dry-run；
- `--allow-trade`真实验证但仍提交0。

当前尚未完成：

```text
Stage G：真实paper写入闭环
Stage H：macOS一键部署闭环
```

---

# 1. 最终目标

完成后，用户只需：

```bash
./wa bootstrap
./wa deploy --enable-trading
```

系统将自动：

```text
读取paper1
→ 对账旧订单
→ coarse
→ portfolio
→ review
→ execution
→ build orders
→ validate
→ submit paper orders
→ 处理cancel/replace依赖
→ reconcile
→ 写日报
→ COMPLETE
→ launchd定时再次运行
```

最终常用命令：

```bash
./wa status
./wa logs
./wa restart
./wa rollback
```

---

# 2. Stage G：Paper写入闭环

## 2.1 目标

从当前：

```text
SUBMIT_ORDERS
```

推进为：

```text
SUBMIT_ORDERS
→ RECONCILE_ORDERS
→ WRITE_REPORT
→ COMPLETE
```

允许：

- submit paper orders；
- cancel明确订单；
- cancel-confirm-revalidate-submit replacement；
- 查询broker order；
- 保存broker响应；
- 即时对账；
- 处理partial fill；
- 更新日报；
- 同日后续cycle维护旧订单。

禁止：

- live提交；
- direct replace API；
- close_all_positions；
- close_position；
- 批量cancel全部；
- broker写盲目重试；
- 强制买一股测试后门。

---

## 2.2 新增版本

不得修改：

```text
core_long@1.2.0
paper_standard@1.1.0
paper_equity@1.0.0
```

新增：

```text
submission policy:
alpaca_paper@1.0.0
```

文件：

```text
config/v2/submission_policies/alpaca_paper-1.0.0.json
```

paper1引用：

```json
{
  "submission_policy": "alpaca_paper@1.0.0"
}
```

---

## 2.3 新增核心文件

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

---

## 2.4 写前Journal

任何broker写调用前必须原子保存：

```text
orders/submission_intent.json
orders/submission_journal.json
```

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

如果本地尚未成功写入journal：

```text
不得调用broker写API
```

---

## 2.5 最终写权限

必须同时满足：

```text
CLI --allow-trade
AND profile=paper1
AND environment=paper
AND profile enabled
AND account hash匹配
AND submission policy allow_submit=true
AND paper_submission_enabled=true
AND emergency_stop=false
AND validated.json合法
AND submission_performed=false
```

任何一项失败：

```text
不得写broker
```

live始终拒绝。

---

## 2.6 允许的Broker写API

Stage G v1只允许：

```text
TradingClient.submit_order
TradingClient.cancel_order_by_id
```

禁止：

```text
replace_order_by_id
cancel_orders
close_position
close_all_positions
```

Broker写调用只能存在于：

```text
order_submitter.py
order_action_executor.py
```

其他生产模块不得直接写broker。

---

## 2.7 Submit流程

只提交：

```text
validated status=approved
dependency_ids全部完成
尚未存在相同client_order_id
```

顺序：

```text
查询client_order_id
→ 已存在则记录，不重复提交
→ journal request_started
→ 本地构造SDK request
→ submit_order
→ 立即持久化broker响应
→ 再查询确认
→ journal completed
```

必须顺序提交，不并发。

---

## 2.8 写异常与幂等

禁止broker写请求盲目重试：

```text
blind retry count = 0
```

如果：

```text
请求可能已到券商
但本地未收到明确响应
```

则：

```text
按client_order_id查询
```

找到：

```text
lookup_confirmed
```

仍无法确认：

```text
uncertain
→ 停止相关后续写入
→ 不重复提交
→ 退出码60
```

---

## 2.9 Cancel与Replacement

Direct replace禁用。

Replacement必须：

```text
查询旧订单
→ cancel
→ 确认canceled/expired/rejected
→ 重新刷新账户/持仓/挂单/quote
→ 重新计算剩余目标
→ 重新硬校验
→ submit replacement
```

如果旧订单：

```text
filled
partially_filled
pending_cancel
uncertain
```

不得直接提交原replacement。

Partial fill必须按剩余目标重新计算数量。

---

## 2.10 Broker submission产物

保存：

```text
orders/broker_submission.json
```

至少记录：

```text
submission_requested
submission_performed
submitted_count
existing_count
rejected_count
uncertain_count
cancel_requested_count
cancel_confirmed_count
operations
errors
warnings
```

每个operation记录：

```text
plan_id
client_order_id
broker_order_id
symbol
side
qty
request summary
broker status
filled qty
average fill price
error
timestamps
```

不得保存密钥。

---

## 2.11 即时对账

提交后立即刷新：

- account；
- positions；
- open orders；
- today orders；
- tracked orders；
- cash；
- buying power。

保存：

```text
orders/reconciliation.json
```

正确区分：

```text
filled
partially_filled
open
canceled
expired
rejected
uncertain
```

不要无限等待限价单成交。

订单仍open或partial fill时，可以正常完成当前cycle。

---

## 2.12 启动时先维护旧订单

每次main启动：

```text
解析profile
→ 查找未完成cycle/journal
→ 对账旧订单
→ 更新旧cycle
→ 更新日报
→ 再开始当前cycle
```

旧订单变化可能触发：

```text
intraday_rebalance
execution_refresh
maintenance_only
```

---

## 2.13 Cycle完成状态

至少区分：

```text
completed_dry_run
completed_no_action
completed_with_submissions
completed_with_open_orders
completed_with_partial_fills
completed_with_rejections
blocked_submission_uncertain
```

正常终点：

```text
current_step=COMPLETE
```

---

## 2.14 日报

路径：

```text
reports/v2/accounts/paper1/
strategies/core_long/1.2.0/
daily/YYYY-MM-DD.md
```

当天第一次详细报告包括：

- profile与版本；
- guidance；
- market；
- coarse；
- portfolio；
- execution；
- order plan；
- broker submission；
- fill/open/reject；
- current positions/cash；
- risks。

后续cycle只追加时间戳更新，不重写全部正文。

---

## 2.15 Stage G测试

必须覆盖：

- 无allow-trade绝不写；
- live绝不写；
- account hash不匹配；
- validated/hash被篡改；
- submission重复运行不重复；
- journal写前持久化；
- submit成功；
- submit明确reject；
- submit超时后查到订单；
- submit超时无法确认；
- uncertain不重试；
- duplicate client_order_id；
- cancel成功；
- cancel时先成交；
- pending_cancel；
- cancel失败；
- replacement依赖；
- partial fill重算；
- direct replace调用0；
- 顺序提交；
- buying power变化；
- 即时reconciliation；
- open order正常完成；
- partial fill正常完成；
- dry-run完整完成但0写；
- daily report；
- main最终COMPLETE；
- 原168项测试保持通过。

---

# 3. Stage H：macOS一键部署

## 3.1 目标

仅支持当前：

```text
macOS
paper1
launchd
本地部署
```

不做Windows、Linux、Web面板、云集群或live部署。

---

## 3.2 最终入口

仓库根目录新增：

```text
wa
```

用户命令：

```bash
./wa bootstrap
./wa doctor
./wa deploy
./wa deploy --enable-trading
./wa run
./wa run --allow-trade
./wa start
./wa stop
./wa restart
./wa status
./wa health
./wa logs
./wa rollback
```

---

## 3.3 Bootstrap

```bash
./wa bootstrap
```

完成：

1. 检查macOS；
2. 检查Python；
3. 创建或验证 `.Alpaca`；
4. 安装锁定依赖；
5. 检查Codex；
6. 检查paper1；
7. 检查现有 `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`；
8. 不打印值；
9. 检查账户绑定；
10. 创建部署/runtime/log目录；
11. 运行全部测试；
12. 编译检查；
13. Broker写白名单扫描；
14. 运行真实dry-run；
15. 不提交订单。

失败时不得安装服务。

---

## 3.4 Deploy模式

```bash
./wa deploy
```

部署dry-run服务，不带 `--allow-trade`。

```bash
./wa deploy --enable-trading
```

部署paper交易服务，带 `--allow-trade`。

交易模式仍需满足Stage G所有硬授权。

---

## 3.5 Release目录

```text
var/
├── deployment/
│   ├── current.json
│   ├── previous.json
│   ├── history/
│   └── releases/
├── shared/
│   ├── runtime/
│   ├── reports/
│   ├── market_data/
│   └── logs/
└── locks/
```

Release不可变。

Runtime、reports、logs、market data不随release切换。

---

## 3.6 Deploy流程

```text
deploy lock
→ doctor
→ build temporary release
→ file hashes
→ full tests
→ compile
→ static scan
→ real dry-run
→ release manifest
→ atomic install
→ current→previous
→ switch current
→ install launchd
→ start
→ health
→ failure auto rollback
```

失败时不得留下半安装current。

---

## 3.7 Launchd

安装：

```text
~/Library/LaunchAgents/com.wa.trader.paper1.plist
```

Plist不得包含：

```text
API key
secret
完整account ID
```

服务通过现有安全env加载方式获得凭据。

---

## 3.8 防重入

运行锁：

```text
var/locks/paper1.run.lock
```

部署锁：

```text
var/locks/deploy.lock
```

任意时刻：

```text
最多一个paper1运行进程
最多一个deploy
```

---

## 3.9 稳定退出码

```text
0 success
10 already running
20 no action
30 configuration error
40 retriable broker/data error
50 safety block
60 submission uncertain
70 deployment error
```

Launchd和health根据退出码判断状态。

---

## 3.10 Runtime根目录

主程序支持：

```text
WA_RUNTIME_ROOT
WA_REPORTS_ROOT
WA_SHARED_DATA_ROOT
WA_LOG_ROOT
```

部署服务使用：

```text
var/shared/runtime
var/shared/reports
var/shared/market_data
var/shared/logs
```

---

## 3.11 状态、健康和日志

```bash
./wa status
./wa health
./wa logs
./wa logs --follow
```

Status输出：

- paper1；
- account hash前缀；
- current/previous release；
- strategy/risk/order/submission版本；
- service状态；
- trading enabled；
- emergency stop；
- last cycle；
- last submit；
- open orders；
- uncertain operations；
- next run。

Health状态：

```text
healthy
degraded
unhealthy
blocked
```

支持：

```bash
./wa status --json
./wa health --json
```

---

## 3.12 Rollback

```bash
./wa rollback
```

流程：

```text
deploy lock
→ stop service
→ validate previous
→ atomic switch
→ start
→ health
→ save history
```

Rollback不：

- 撤销已提交订单；
- 删除runtime；
- 取消open orders。

回滚后第一轮先reconcile。

---

## 3.13 Stage H测试

必须覆盖：

- bootstrap clean install；
- 缺Python/Codex/凭据/绑定；
- 测试失败阻止deploy；
- dry-run失败阻止deploy；
- `.env`不复制；
- plist无密钥；
- release hash；
- atomic current/previous；
- health失败自动rollback；
- deploy/run lock；
- stale lock；
- dry-run service；
- trading-enabled paper service；
- live拒绝；
- status/health JSON；
- runtime持久化；
- logs脱敏；
- SIGTERM；
- 稳定退出码；
- 所有Stage G测试保持通过。

---

# 4. 最终真实验收顺序

## 4.1 Bootstrap

```bash
./wa bootstrap
```

## 4.2 Dry-run部署

```bash
./wa deploy
```

## 4.3 检查

```bash
./wa status
./wa health
./wa logs
```

## 4.4 手工前台paper运行

```bash
./wa run --allow-trade
```

如果本轮自然没有approved订单：

```text
completed_no_action
```

属于正常。

不得加入强制买入后门。

## 4.5 第一次自然approved订单

出现后检查：

```text
submission_journal.json
broker_submission.json
reconciliation.json
cycle_summary.json
daily report
Alpaca dashboard
```

确认：

- paper1正确；
- client_order_id一致；
- symbol/side/qty/limit一致；
- 没有重复；
- 对账正确。

## 4.6 启用自动Paper交易

第一次真实submit验收通过后：

```bash
./wa deploy --enable-trading
```

---

# 5. 最终一键运行命令

系统开发与验收全部完成后：

```bash
./wa bootstrap
./wa deploy --enable-trading
```

之后自动运行。

日常管理：

```bash
./wa status
./wa logs
./wa restart
./wa rollback
```

手工立即运行：

```bash
./wa run --allow-trade
```

---

# 6. 最终完成标准

- Stage G真实paper写入代码完成；
- 稳定client_order_id；
- Journal；
- 不确定写不重试；
- Cancel race；
- Partial fill；
- Reconciliation；
- Daily report；
- Main最终COMPLETE；
- 顶层 `./wa`；
- Bootstrap；
- Deploy；
- Launchd；
- Run/deploy lock；
- Stable exit codes；
- Runtime与release分离；
- Health/status/logs；
- Rollback；
- Paper1唯一当前账户；
- Live始终拒绝；
- 密钥不进Git/release/plist/log；
- 全部测试通过；
- 工作树干净。

---

# 7. 交给Codex的最终指令

```text
当前正确状态是Stage F完成，不是Stage G。

基线：
- main commit 6d23503
- tag stage-f-complete
- 168项测试通过
- paper1
- core_long@1.2.0
- paper_standard@1.1.0
- paper_equity@1.0.0
- 主流程停在SUBMIT_ORDERS
- broker写调用为0

请阅读：
docs/WA_Trader_v2_final_paper.md

请按顺序完成Stage G和Stage H。

Stage G：
1. 新增alpaca_paper@1.0.0；
2. 实现submission intent和journal；
3. 实现order_submitter；
4. 实现cancel executor；
5. 禁用direct replace；
6. replacement使用cancel-confirm-refresh-revalidate-submit；
7. broker写blind retry=0；
8. 超时后按client_order_id查询；
9. uncertain时停止，不重复提交；
10. 只提交validated approved订单；
11. 完成即时reconciliation；
12. 正确处理partial fill/open/rejected/canceled；
13. main启动先维护旧订单；
14. 写cycle summary和daily report；
15. main最终COMPLETE；
16. live继续拒绝；
17. 不修改.env；
18. 保持168项旧测试并增加Stage G测试。

Stage H：
19. 新增顶层./wa；
20. 实现bootstrap、doctor、deploy、run、start、stop、restart、status、health、logs、rollback；
21. 只支持当前macOS和paper1；
22. ./wa deploy默认dry-run；
23. ./wa deploy --enable-trading才允许paper写；
24. 使用launchd；
25. 使用不可变release和current/previous原子切换；
26. Runtime/reports/market data/logs放shared目录；
27. Plist/release/log不得含密钥；
28. 实现deploy lock和paper1 run lock；
29. 实现稳定退出码和SIGTERM；
30. 实现health失败自动rollback；
31. Rollback不撤销订单、不删除runtime；
32. 保持全部Stage G测试并增加Stage H测试；
33. 不实现Windows、Linux、Web面板或live部署。

最终必须实际验证：
- ./wa bootstrap
- ./wa deploy
- ./wa status
- ./wa health
- ./wa run
- ./wa rollback

只有第一次自然approved订单真实提交并完成对账后，
才能声称真实paper submit已验证。
不得添加强制买入测试后门。
```
