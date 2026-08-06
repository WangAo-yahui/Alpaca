<!--
作用：WA Trader v2 的统一操作手册、架构说明与文件索引。
重要性：本文件替代旧阶段性实施文档，任何运行、部署、交易授权或维护操作都应先参考这里。
-->

# WA Trader v2 操作手册

## 1. 系统定位

WA Trader v2 当前是只运行 `live1` 的美股组合决策、订单规划、提交、对账、
自然语言日报和 macOS 自动运行系统。当前生产入口是根目录的 `./wa`，业务代码全部
位于 `src/v2`，旧版 `src/v1` 和 Paper 运行项目均已删除。源码中保留的 Paper
类型分支与测试夹具只用于离线回归，不是可运行的账户或服务。

当前默认运行身份：

| 项目 | 当前值 |
| --- | --- |
| Profile | `live1` |
| Broker 环境 | Alpaca Live |
| 策略 | `core_long@1.6.2` |
| 风险配置 | `live_full@1.2.0` |
| 订单配置 | `live_equity@1.0.0` |
| 提交配置 | `alpaca_live@1.0.0` |
| macOS 服务 | `com.wa.trader.live1` |
| 自动调度检查间隔 | 60 秒 |
| 凭据文件 | `.env_live` |

系统的核心原则是 fail closed：数据、市场阶段、账户绑定、配置、模型输出、
订单能力或券商写入结果只要不确定，就停止交易，而不是猜测或重试。

## 2. 最快开始

### 2.1 环境要求

- macOS；
- 项目虚拟环境 `.Alpaca/`；
- 可用的 `codex` CLI；
- 根目录 `.env_live` 中存在 `live1` 的 Alpaca Live 凭据；
- `account_bindings/live1.json` 已绑定正确 Live 账户，或首次运行显式使用
  `--bind-account`；
- 当前工作树在部署前必须干净。

`.env_live` 使用：

```dotenv
ALPACA_LIVE_API_KEY=...
ALPACA_LIVE_SECRET_KEY=...
```

不要把真实值写入 README、Git、LaunchAgent plist、日志或 release。

### 2.2 首次检查

```bash
./wa doctor --live
./wa bootstrap --live
```

`doctor` 只检查环境。`bootstrap` 会核验锁定依赖、准备共享目录、复制账户绑定、
运行完整测试和静态检查，并执行一次真实数据 dry-run。dry-run 会访问 Alpaca 和
Codex，但不会提交订单。

### 2.3 安全部署

```bash
./wa deploy --live
./wa status --live
./wa health --live
./wa logs --live
```

不带 `--enable-trading` 的部署永远以 dry-run 服务模式运行。

### 2.4 手工运行

只读决策和订单规划：

```bash
./wa run --live
./wa run --live --force-full
```

第一条允许程序按状态机复用同日有效组合；第二条强制生成新的完整决策，但仍是
dry-run，不授予提交权限。

允许自然产生的 approved 订单写入 Alpaca Live：

```bash
./wa run --live --allow-trade
```

手工 `run` 直接使用当前工作区源码，不需要先 `deploy`。其中
`--allow-trade` 会强制启动一个新的完整决策轮次，不会复用同日旧的空组合；
但它只表示“允许通过全部门禁的 Live 订单提交”，不会强制模型生成订单，
也不会绕过 Python 风控。没有合格订单时，正常结果仍是
`completed_no_action`，退出码为 20。

LaunchAgent 与 `./wa start/restart` 始终使用最后一次部署的不可变 release；
源码小改动要进入自动服务仍需提交并运行 `./wa deploy --live`。

### 2.5 可交易时段

系统支持 Alpaca 美股常规、盘前、盘后和 24/5 overnight 时段：

- overnight：周日到周四 `20:00–04:00 ET`；
- 盘前：交易日 `04:00–09:30 ET`；
- 常规：交易日 `09:30–16:00 ET`；
- 盘后：交易日 `16:00–20:00 ET`。

隔夜时段会使用 Alpaca overnight 行情 feed，并要求资产具备
`overnight_tradable` 能力。订单仍必须是限价单、带
`extended_hours=true`，且满足订单、风险、报价时效和价差门禁。
周五 `20:00 ET` 到周日 `20:00 ET` 仍是周末闭市；节假日前一晚按下一交易日
日历判断。Live 在真正闭市时可提交下一交易日排队的保守限价单：
`limit + day + extended_hours=false + allow_queue=true`。开仓或加仓每次最多
执行目标差额的 25%，减仓或平仓可到 100%；最后报价不得超过 96 小时。
排队订单不会在闭市时成交，开盘跳空仍可能导致不成交。

### 2.6 自动 Live 交易

只有 Live 环境、账户绑定、策略、风险、提交 journal 和对账门禁均通过后，才允许：

```bash
./wa deploy --live --enable-trading
```

该参数只授予通过全部门禁后的写入权限；它不会强制产生订单，也不会绕过任何风险、
市场、报价、账户或幂等检查。

## 3. 整体运行流程

```text
./wa
  └─ deployment CLI / manager
      ├─ 手工 run：读取当前工作区源码
      ├─ 服务运行：读取 current release
      ├─ 获取部署锁或运行锁
      ├─ 注入共享 runtime / report / market-data 路径
      └─ 启动 src/v2/main.py
           ├─ 环境、Profile、账户绑定和策略 release 校验
           ├─ 维护历史未完成订单
           ├─ 账户、持仓、订单、资产和行情快照
           ├─ Coarse：大股票池缩减为候选集
           ├─ Portfolio：形成分散目标组合
           ├─ Execution：根据当前市场阶段调整执行意图
           ├─ Python 硬风控和订单规格构建
           ├─ 可选的 Live submit / cancel
           ├─ 券商状态查询与 reconciliation
           └─ cycle summary 和 daily report
```

各阶段的职责严格分离：

1. Codex 负责研究判断和结构化建议；
2. Python 负责数据合同、状态机、数量、现金、集中度、市场阶段和订单能力硬约束；
3. 只有交易模块中的白名单调用点能执行券商写操作；
4. 所有计划、写入前状态、响应和对账都先后落盘；
5. 不允许模型、命令行参数或部署选项绕过风控。

## 4. `core_long@1.6.2` 整体策略

### 4.1 Coarse 候选筛选

- 从静态股票池和客观市场数据形成候选输入；
- Python 先缩减到股票 100、ETF 20，再由 Coarse 输出 20 个深度研究候选；
- 每日完整轮次先从 Alpaca IEX 批量增量刷新日线，避免用陈旧收盘价做估值；
- 用户 guidance 只能作为偏好，不能成为强制交易指令；
- Coarse 输出禁止包含数量、订单、目标权重和强制开仓字段；
- 数据缺失或质量告警必须保留，不能静默删除问题标的。

### 4.2 Portfolio 组合构建

- 目标持仓数 0–20；
- 允许空组合，因此没有合格机会时可以全部持有现金；
- 组合建议有效期 1440 分钟；
- 权重容差和最小目标权重均为 0.01；
- 策略允许模型在充分证据下选择集中、分散、满仓或全现金；
- 所有持仓必须与现金及至少 3 个未持有候选按同一口径竞争；
- 可靠估值必须至少记录 2 个可复算输入和对应来源；只有尝试至少 2 种合适方法仍失败，才允许 `no_reliable_estimate`；
- 开仓和加仓要求估值证据质量及预期回报置信度至少为中等；低置信度只能观察、持有、减仓或退出；
- 不会从零重新买入的既有持仓必须量化换仓成本、列出待解决证据、设定最长 30 天复核期，并说明证据仍缺失时是否减仓或退出；
- 目标现金达到 25% 时必须列出至少 2 个部署触发器并在 14 天内复核；最低现金仍可为 0%；
- 每个标的都有最多 30 天的复核日期和至少 2 个估值、基本面、集中度、事件或时间触发器；
- 新兴成长观察池初始单标的上限 3%、合计上限 10%，且必须分批进入；
- 不允许在 Coarse 候选集之外增加仓位；
- 资金绝对变化 100 美元或相对变化 1% 时视为需要重新判断。

### 4.3 Execution 执行阶段

- 执行意图有效期为 30 分钟；
- 单次目标权重绝对调整不超过 0.02；
- 单次目标权重相对调整不超过 25%；
- 执行比例只能在 0–1 之间；
- 目标权重差不足 1% 时禁止无意义买入；
- 每标的每日自主建仓/加仓最多 2 次；
- 执行比例不得超过 Portfolio 分批计划的单档上限，`wait/no_add` 不得买入；
- 扩展时段必须使用 limit 意图并具备新鲜报价；
- 执行阶段只能缩减或调整组合意图，不能绕过 Python 风控扩大权限。

### 4.4 Python 硬风控

当前 `live_full@1.2.0`：

| 风控项 | 限制 |
| --- | --- |
| 最大总敞口 | 100% |
| 最大单一仓位 | 40% |
| 最大行业权重 | 65% |
| 最低现金 | 0% |
| 每轮最大新增资金 | 35% |
| 每轮最大订单数 | 20 |
| 最小订单价值 | 1 美元 |
| 做空 | 禁止 |
| 报价最大年龄 | 15 秒 |
| 闭市排队报价最大年龄 | 96 小时 |
| 常规/扩展时段最大点差 | 50 bps |
| IEX 点差复核 | 6.5 秒窗口、连续 3 次通过 |

当前 profile 的版本化风险会覆盖执行输入，所以 Live 不再受到旧兼容
`config/v2/risk.json` 中 15% 单标的、10% 现金或 25 美元最小订单限制。

### 4.5 订单规则

Live 美股订单：

- 常规时段允许 market 或 limit；
- 扩展时段只允许 limit；
- 默认 TIF 为 `day`，支持 `day` 和 `gtc`；
- 美股数量最多 6 位小数、价格最多 2 位小数；Crypto 使用 Alpaca
  资产记录中的 `min_order_size`、`min_trade_increment` 和
  `price_increment`；
- 周末/节假日按上述保守闭市规则允许排队；
- `unknown` 市场状态始终禁止订单；
- client order ID 最大 48 个字符；
- fractional、extended-hours 和资产能力均在提交前检查。

`live_equity@1.0.0` 还允许处置账户中已经存在的 Alpaca Crypto 持仓：

- 数据请求自动把历史符号 `USDTUSD` 转为 Alpaca 要求的 `USDT/USD`，
  内部状态与幂等 ID 继续使用原符号；
- Crypto 是 7×24 资产，不套用美股周末、节假日或 overnight 门禁；
- Live 运行发现可用 Crypto 后，不再交给 Codex 判断：Python 自动生成
  全量 `close + sell + market + gtc`，并且 `extended_hours=false`；
- 该轮跳过耗时的 Codex execution 调用，直接进入订单刷新、硬校验和提交；
- 自动清仓不要求 15 秒新鲜报价，数量仍按资产的
  `min_order_size`/`min_trade_increment` 向下量化；
- 已有同方向挂单时不重复提交；未确认成交前不把预计卖出款用于股票；
- 不允许策略持有、新开或增加 Crypto。

### 4.6 提交和对账规则

`alpaca_live@1.0.0` 使用独立 Live 凭据、绑定、journal 与运行目录：

- 只允许当前 profile 环境的 submit 和按订单 ID cancel；
- 禁止券商 direct replace；
- 多个订单必须顺序提交，不能并发；
- 每次写入前后都持久化 journal；
- 写错误后盲重试次数为 0；
- 写错误后先使用稳定 client order ID 查询券商；
- 无法确认结果时标记 uncertain，并停止后续写入；
- 提交后立即轮询并对账；
- cancel race 必须以券商最终状态为准；
- 不允许批量取消或直接平仓 API。

## 5. 状态机与常见结果

主要轮次类型：

- `daily_full`：当天第一次完整研究、组合和执行轮次；
- `execution_refresh`：复用仍有效的研究或组合，只刷新执行；
- `maintenance_only`：只处理历史未完成订单和对账；
- `execution_only`：只运行明确允许的执行阶段；
- `force_full` / `force_rebalance`：人工要求重新研究或组合，但仍不能绕过风控。

正常终态：

- `completed_dry_run`：完整运行但不允许券商写入；
- `completed_no_action`：允许券商写入，但自然没有订单；
- `completed_with_submissions`：存在已提交订单；
- `completed_with_open_orders`：存在仍未终结的订单；
- `completed_with_partial_fills`：存在部分成交；
- `completed_with_rejections`：券商明确拒绝，结果已记录。

`running` 是处理中状态，不是失败。服务运行期间 health 可能暂时显示
`degraded/no_recent_normal_terminal_cycle`，轮次正常结束后应恢复 healthy。

## 6. 运维命令

| 命令 | 作用 | 是否可能写券商订单 |
| --- | --- | --- |
| `./wa doctor --live` | 检查 `.env_live`、live1 绑定和 Live policy | 否 |
| `./wa bootstrap --live` | 准备依赖和共享目录、测试并执行 dry-run | 否 |
| `./wa deploy --live` | 构建不可变 release，dry-run 验证并安装 LaunchAgent | 否 |
| `./wa run --live` | 前台执行一次 dry-run | 否 |
| `./wa run --live --force-full` | 用当前源码强制完整决策，保持 dry-run | 否 |
| `./wa run --live --maintenance-only` | 新建只读维护轮次，仅对账既有订单、持仓并更新日报 | 否 |
| `./wa run --live --allow-trade` | 复用或刷新同日决策，并允许 approved Live 订单 | 是 |
| `./wa run --live --force-full --allow-trade` | 强制完整 Live 研究并允许 approved Live 订单 | 是 |
| `./wa deploy --live --enable-trading` | 部署独立的交易日动态 Live 服务 | 是 |
| `./wa start --live` | 加载并启动当前 LaunchAgent | 取决于当前 release 模式 |
| `./wa stop --live` | 停止并卸载 LaunchAgent | 否 |
| `./wa restart --live` | 重启 LaunchAgent | 取决于当前 release 模式 |
| `./wa status --live --json` | 输出机器可读状态 | 否 |
| `./wa health --live --json` | 输出机器可读健康报告 | 否 |
| `./wa logs --live` | 显示最近脱敏日志 | 否 |
| `./wa logs --live --follow` | 持续跟随脱敏日志 | 否 |
| `./wa rollback --live` | current/previous 原子互换并重启服务 | 否 |

`./wa _service-run` 是 LaunchAgent 内部入口，不应手工调用。

### 6.1 Live 实盘运行

Live 首次运行：

```bash
./wa doctor --live
./wa run --live --bind-account --allow-trade --force-full
```

账户 hash 已绑定后的同日计划运行：

```bash
./wa run --live --allow-trade
```

行为：

- 当天没有有效完整决策时自动运行 Coarse、Portfolio 和 Execution；
- 同日已有有效候选池/组合时优先复用，只刷新账户、持仓、挂单、行情和执行；
- `--allow-trade` 允许通过全部检查的 Live 订单，不要求每次必须产生订单；
- `--maintenance-only` 总是新建纯维护轮次，跳过 Coarse/Portfolio/Execution，
  不提交新订单；用于立即确认成交、挂单变化和修复/刷新日报；
- 没有账户、持仓、订单、新闻或策略实质变化时，可以维持原策略；
- overnight、盘前和盘后允许调仓或建仓，必须使用新鲜报价和扩展时段限价单；
- 周末/节假日允许保守排队：开仓/加仓最多 25% 执行差额，减仓/平仓最多
  100%，只使用 `limit + day`，等待下一交易日；
- Live 的 `live_full@1.2.0` 允许使用 100% 账户权益、最低现金 0；单标的、行业和
  单轮新增资本分别受 40%、65% 和 35% 的最低必要硬边界约束；
- regular session 内策略可在 0–100% 权益之间自由决定现金和资金使用，
  不强制留现金，也不强制满仓；
- 不启用做空，也不主动使用超过账户权益的额外杠杆。

Live 自动交易日服务：

```bash
./wa deploy --live --enable-trading
./wa start --live
./wa status --live --json
```

Live LaunchAgent 每分钟只执行一次轻量调度检查，真正的交易轮次按
`America/New_York` 和 Alpaca 交易日历触发。首轮为开盘后 30 分钟，常规维护间隔
为 120 分钟，并保留收盘前检查；提前收盘日自动减少盘中轮次。实际收盘后 15 分钟只运行 `maintenance-only` 完成对账和日报，并在
没有不确定写入或活跃运行时关闭显示器。每个时点使用持久化槽位认领，服务重启
不会重复执行；可重试错误最多按 profile 配置重试两次，写入不确定绝不自动重提。

Paper 服务、凭据、账户绑定、runtime、reports 和部署指针均已删除；生产运行面只剩
`live1`。源码中的环境泛化分支仅用于离线回归。

每个 Live cycle 仍生成确定性日报，同时额外调用一次 Codex 维护：

```text
var/shared/reports/accounts/live1/strategies/core_long/<strategy_version>/daily/
  YYYY-MM-DD.md
  natural_language/
    YYYY-MM-DD.md
    latest.md
  natural_language_report_output/
    YYYY-MM-DD.md
```

`natural_language/` 保存合并后的用户日报，`natural_language_report_output/`
单独保存 Codex 每次调用的当日原始 Markdown 输出；调用状态、错误、事实 JSON、Prompt
与其他工作文件保存在同级隐藏目录 `.natural_language_report/`，三类内容不再混放。
根目录 `natural_language/` 是最完整用户日报的统一索引：每个日期只建立一个符号链接，
指向该日报所属策略版本的 `daily/natural_language/YYYY-MM-DD.md`，因此升级策略后旧日报
仍连续可见但不复制正文；`latest.md` 指向最新日期。

当天第一次是完整自然语言日报，包含前序日报/账户变化、持仓分析、订单解读、联网
新闻、资金风险、净入金校正后的每日/累计时间加权收益和未来策略指导。后续轮次只有
账户、持仓、订单、保护计划或组合判断发生实质变化时才调用 Codex；无变化时直接跳过，
文件不会被重复追加，也不会强行建议交易。确定性日报每轮仍更新绩效数字。
如果第四次 Codex/新闻调用临时断网，交易主流程不会被回滚：程序立即写入完整的
事实降级版，明确标注“没有联网新闻”，后续计划轮次自动重试；恢复后只追加
可核验新闻和实质变化。

#### 6.1.1 自动止盈止损与 Codex 执行权限

每次完整 Execution 都要求 Codex 为每个现有美股多头和每个批准的新入场输出
`protection_plans`。Codex 可以根据持仓成本、当前报价、波动和组合风险选择下列模式；
Python 再决定合法的 Alpaca 请求形态：

| Codex 模式 | 现有持仓 | 新入场 |
| --- | --- | --- |
| `stop` | 独立止损单 | OTO 止损 |
| `stop_limit` | 独立止损限价单 | OTO 止损限价 |
| `take_profit` | 独立止盈限价单 | OTO 止盈 |
| `trailing_stop` | 独立移动止损 | 入场后转固定 OTO 止损，或成交后下一轮保护 |
| `oco` | 止盈和止损互斥退出 | 转成 bracket |
| `bracket` | 转成 OCO 退出 | 原生 bracket |
| `oto_stop` / `oto_take_profit` | 转成对应独立退出单 | 原生 OTO |
| `staged_oco` | 最多五组分级 OCO | 压缩为覆盖整笔入场的保守 bracket |

运行授权不需要新增危险开关：

```bash
./wa run --live --allow-trade --force-full
./wa run --live --allow-trade
```

第一条用于当天需要重做完整策略时；后续计划维护通常只需第二条。`--allow-trade`
同时授权经过硬校验的普通订单和保护单，最终写前仍要求 Live submission policy 的
`protective_order_submission_enabled=true`。不带 `--allow-trade` 时只生成和校验计划，
不会提交。

保护规则：

- 多头止盈必须高于参考价，止损必须低于参考价；卖出 stop-limit 必须满足
  `limit <= stop`，所有距离和 trailing 百分比还受 strategy policy 上下限约束。
- bracket、OCO、OTO 和 trailing 均不以 `extended_hours=true` 执行。盘前、盘后或闭市
  可以向 Alpaca 提交合法的常规时段保护/排队请求，但止损触发和高级 legs 仍按券商
  支持的交易时段工作；不要把“已提交”误认为“已触发”。
- Alpaca 碎股按当前公开能力只使用 `day` 的 simple limit/stop/stop-limit。碎股 OCO、
  bracket、分级 OCO 或 trailing 会自动降级为单一固定 stop 或 stop-limit；这比发送
  可能被券商拒绝的高级组合更可靠。
- stop 或 trailing 触发后通常成为 market order，成交价可能滑点；stop-limit 可以控制
  最差限价，但快速跳空时可能不成交。两类风险都会写入日报。
- 后续轮次策略和价格未变化时保留既有保护单，不撤单重挂；只有策略、价格或覆盖数量
  变化时，才先取消旧保护、刷新持仓和可用数量，再提交替代保护。
- Codex 只输出保护策略，不能直接调用 Alpaca。数量、覆盖上限、价格精度、账户身份、
  幂等 ID、取消确认和真正提交全部由 Python 唯一写路径控制。
- 日报会分别显示 Codex 计划、validated 保护单和 broker open/tracked 保护单。只有
  Alpaca 对账中可核验的 open/held/new 保护订单才应视为已经生效。

### 6.2 稳定退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 10 | 已有运行中的同类进程 |
| 20 | 正常无操作 |
| 30 | 配置或文件错误 |
| 40 | 可重试的读取/外部服务错误 |
| 50 | 安全门禁阻止 |
| 60 | 券商写入结果不确定，禁止自动重试 |
| 70 | 部署、release 或服务错误 |

自动化脚本应把 20 当作正常业务结果，不能当作系统故障。

## 7. 推荐操作流程

### 7.1 每日观察

```bash
cd /Users/wangao/Alpaca
./wa status --live --json
./wa health --live --json
./wa logs --live
```

重点检查：

- `current_release` 是否有效；
- `trading_enabled` 是否符合预期；
- `last_cycle_status` 是否为正常终态；
- `last_submit_count`、`open_orders` 和 `uncertain_operations`；
- health 的 `reasons` 是否为空。

### 7.2 手工 Live 验收

```bash
./wa run --live --allow-trade
```

如果出现自然 approved 订单，核对：

1. `submission_journal.json`；
2. `broker_submission.json`；
3. `reconciliation.json`；
4. `cycle_summary.json`；
5. 当日日报；
6. Alpaca Dashboard。

必须确认 profile、symbol、side、qty、limit price 和 client order ID 一致，没有重复
提交，并且券商最终状态与 reconciliation 一致。

手工验收会读取当前未部署源码，并在轮次身份中记录
`source_tree_hash` 和 `source_tree_dirty`。因此可以先验证小改动；若要让
LaunchAgent 使用同一改动，再提交代码并执行部署。

### 7.3 发布新代码

```bash
git status --short
PYTHONPATH=src PYTHONPYCACHEPREFIX=/private/tmp/wa_v2_pycache \
  .Alpaca/bin/python -m unittest discover -s tests/v2 -p 'test_*.py'
./wa doctor --live
./wa deploy --live
./wa health --live
```

部署要求 Git 工作树干净。release 会从已跟踪白名单文件构建，并记录每个文件
SHA-256。验证失败时不会切换 current 指针。

### 7.4 回滚

```bash
./wa rollback --live
./wa status --live
./wa health --live
```

回滚只切换代码和配置 release，不删除 runtime、不撤销已提交订单，也不覆盖报告。

## 8. 根目录结构

```text
Alpaca/
├── README.md                 # 本操作手册
├── wa                        # 唯一运维入口
├── requirements.txt          # 兼容依赖范围
├── requirements.lock         # 部署使用的精确依赖版本
├── src/v2/                   # v2 应用源码
├── config/v2/                # 系统、Profile、风险和订单配置
├── config/universe/          # 静态股票池
├── strategies/               # 不可变策略 release
├── prompts/v2/               # 应用级 Coarse prompt
├── schemas/v2/               # 运行工件 JSON Schema
├── tests/v2/                 # 完整自动化测试
├── data/                     # 可用于 bootstrap 的历史行情和资产种子
├── var/                      # 部署、共享 runtime、报告、日志和锁
├── .Alpaca/                  # Python 虚拟环境，不进入 Git
├── .env_live                 # Live 凭据，不进入 Git
├── account_bindings/         # Live 账户绑定，不进入 Git
└── natural_language/          # 每日最完整自然语言日报的统一符号链接索引
```

## 9. 根级文件说明

| 文件 | 作用 |
| --- | --- |
| `README.md` | 唯一维护和操作手册 |
| `wa` | 把 `src` 加入 Python 路径并进入 Stage H 运维 CLI |
| `requirements.txt` | 开发环境允许的依赖版本范围 |
| `requirements.lock` | bootstrap/deploy 使用的精确依赖版本 |
| `.gitignore` | 阻止凭据、绑定、runtime、日志、缓存进入 Git |
| `.env_live` | Alpaca Live 独立凭据；必须保留但永远不能提交 |
| `WA_Trader_v2_live_migration_spec.md` | Live 迁移前的历史设计与审计清单；当前操作以本 README 为准 |

`.git/`、`.Alpaca/` 和 `account_bindings/` 是必要的本地基础设施，不属于 release。

## 10. `src/v2` 文件逐项说明

### 10.1 应用核心

| 文件 | 作用 |
| --- | --- |
| `src/v2/__init__.py` | 声明 v2 包及与旧实现隔离的边界 |
| `src/v2/main.py` | 主编排器；连接状态机、数据、三阶段、订单、提交、对账和报告 |
| `src/v2/cli.py` | 解析业务轮次参数和互斥选项 |
| `src/v2/config.py` | 加载并校验系统、市场数据、阶段和通用配置 |
| `src/v2/profiles.py` | 加载 Profile、版本化 policy，并管理账户绑定路径 |
| `src/v2/releases.py` | 约束策略文件集合，按实际运行内容生成 hash，并记录 Git 身份 |
| `src/v2/runtime.py` | 构建日期/轮次/共享路径并提供原子 JSON 持久化 |
| `src/v2/state_machine.py` | 定义步骤顺序、状态转移、恢复和最终完成条件 |
| `src/v2/exceptions.py` | 定义配置、数据、状态、Codex 和安全相关异常 |
| `src/v2/guidance.py` | 保存、规范化并签名初始用户 guidance |
| `src/v2/review.py` | 处理人工复查与无人值守复查结果 |

### 10.2 Alpaca 数据层

| 文件 | 作用 |
| --- | --- |
| `src/v2/data/__init__.py` | 数据层包入口 |
| `src/v2/data/_normalization.py` | Decimal、时间、枚举和券商对象的公共规范化 |
| `src/v2/data/alpaca_client.py` | 从安全 dotenv 创建环境隔离的只读或交易客户端；生产只使用 Live |
| `src/v2/data/account.py` | 获取并规范化账户资金和权限 |
| `src/v2/data/assets.py` | 获取资产能力、tradable、fractionable 等字段 |
| `src/v2/data/daily_bars.py` | 增量读取和更新日线缓存 |
| `src/v2/data/intraday.py` | 获取当前交易日分时数据 |
| `src/v2/data/quotes.py` | 获取最新 bid/ask、时间和点差输入 |
| `src/v2/data/positions.py` | 获取并规范化当前持仓 |
| `src/v2/data/orders.py` | 获取 open/today/history 订单并规范化状态 |
| `src/v2/data/snapshots.py` | 组合基础账户、持仓、订单、资产和市场快照 |
| `src/v2/data/execution_snapshot.py` | 构造 Execution 阶段所需的市场和组合快照 |
| `src/v2/data/pretrade_snapshot.py` | 在生成订单请求前重新抓取关键事实 |
| `src/v2/data/universe.py` | 加载并合并 S&P 500、ETF 和核心标的股票池 |

### 10.3 领域模型

| 文件 | 作用 |
| --- | --- |
| `src/v2/models/__init__.py` | 模型包入口 |
| `src/v2/models/state.py` | Daily/Cycle 状态和步骤数据合同 |
| `src/v2/models/coarse.py` | Coarse 输入输出模型 |
| `src/v2/models/portfolio.py` | 组合目标、权重和复用模型 |
| `src/v2/models/execution.py` | 执行意图、市场阶段和约束模型 |
| `src/v2/models/orders.py` | Proposed、Validated、Action Plan 和请求规格模型 |
| `src/v2/models/submission.py` | Journal、Broker Submission 和 Reconciliation 模型 |

### 10.4 Codex 适配层

| 文件 | 作用 |
| --- | --- |
| `src/v2/codex/__init__.py` | Codex 适配包入口 |
| `src/v2/codex/workspace.py` | 为各阶段创建隔离的输入、prompt、schema 和输出工作区 |
| `src/v2/codex/runner.py` | 以受限参数调用 Codex CLI，处理超时、输出和重试边界 |
| `src/v2/codex/validation.py` | 对 Codex JSON 输出执行 Schema 和语义校验 |

### 10.5 三阶段决策

| 文件 | 作用 |
| --- | --- |
| `src/v2/stages/__init__.py` | 阶段包入口 |
| `src/v2/stages/coarse.py` | 准备、运行、校验和缓存 Coarse 筛选 |
| `src/v2/stages/portfolio.py` | 准备、运行、校验和复用组合决策 |
| `src/v2/stages/execution.py` | 准备、运行并约束执行意图 |

### 10.6 订单与 Live 写入

| 文件 | 作用 |
| --- | --- |
| `src/v2/trading/__init__.py` | 交易包入口和写入边界说明 |
| `src/v2/trading/order_builder.py` | 根据目标组合和事实生成本地 proposed orders |
| `src/v2/trading/protection.py` | 将 Codex 止盈止损计划映射为 Alpaca 合法组合、碎股降级与安全替换 |
| `src/v2/trading/order_validator.py` | 执行现金、敞口、集中度、报价和市场阶段硬校验 |
| `src/v2/trading/order_request_factory.py` | 将 validated order 转换为 alpaca-py OrderRequest |
| `src/v2/trading/idempotency.py` | 生成稳定且长度受限的 client order ID |
| `src/v2/trading/submission_guard.py` | 校验环境/profile/policy/双开关和执行授权 |
| `src/v2/trading/submission_journal.py` | 在每次券商写入前后原子记录意图与结果 |
| `src/v2/trading/order_submitter.py` | 唯一订单提交调用点，处理超时和幂等查询 |
| `src/v2/trading/order_action_executor.py` | 唯一按订单 ID 取消调用点，处理 cancel race |
| `src/v2/trading/reconciliation.py` | 查询券商最终状态并生成可审计对账结果 |

### 10.7 报告

| 文件 | 作用 |
| --- | --- |
| `src/v2/reports/__init__.py` | 报告包入口 |
| `src/v2/reports/daily_report.py` | 增量生成同日 Markdown 决策、订单和对账报告 |
| `src/v2/reports/natural_language_report.py` | 额外调用 Codex，联网生成并按变化维护自然语言日报 |

### 10.8 macOS 部署

| 文件 | 作用 |
| --- | --- |
| `src/v2/deployment/__init__.py` | Stage H 部署包入口 |
| `src/v2/deployment/cli.py` | 实现 `wa` 的所有运维子命令 |
| `src/v2/deployment/constants.py` | 服务身份、运行间隔、正常终态和稳定退出码 |
| `src/v2/deployment/paths.py` | 定义 release、shared、logs、locks 和 plist 路径 |
| `src/v2/deployment/locks.py` | 原子部署锁和运行锁，识别存活 PID 与陈旧锁 |
| `src/v2/deployment/redaction.py` | 从日志中清除 dotenv 凭据值和常见 token |
| `src/v2/deployment/release.py` | 构建白名单 release、记录 hash、校验并只读安装 |
| `src/v2/deployment/launchd.py` | 生成安全 plist 并执行 bootstrap/bootout/kickstart |
| `src/v2/deployment/manager.py` | 编排 bootstrap、deploy、run、health、logs 和 rollback |

## 11. 配置文件说明

### 11.1 股票池

| 文件 | 作用 |
| --- | --- |
| `config/universe/sp500.json` | S&P 500 静态股票池 |
| `config/universe/watchlist_non_sp500.json` | 低频维护的标普 500 外补充关注池 |
| `config/universe/etfs.json` | ETF、行业和风险观察标的 |
| `config/universe/core_symbols.json` | 必须持续关注的核心标的 |

### 11.2 应用配置

| 文件 | 作用 |
| --- | --- |
| `config/v2/system.json` | 超时、默认 runtime/report 和 Codex 运行参数 |
| `config/v2/market_data.json` | 日线、分时、报价和数据新鲜度规则 |
| `config/v2/stages.json` | 阶段启用和流程级设置 |
| `config/v2/universe.json` | v2 股票池来源与合并规则 |
| `config/v2/risk.json` | 旧兼容风险入口；版本化 Profile 优先 |
| `config/v2/order_policy.json` | 旧兼容订单入口；版本化 policy 优先 |

### 11.3 Profiles

| 文件 | 作用 |
| --- | --- |
| `config/v2/profiles/live1.json` | 当前正式 Alpaca Live 实盘身份 |

`default/live` 别名和 `paper1/2/3` 配置只保留为离线兼容与回归夹具；没有凭据、
账户绑定、runtime、部署指针或 LaunchAgent，不能作为当前运行项目。所有不带 profile
的生产命令均选择 `live1`。

### 11.4 版本化 Policies

| 文件 | 作用 |
| --- | --- |
| `config/v2/risk_profiles/live_conservative-1.0.0.json` | 历史 Live 占位风险 |
| `config/v2/risk_profiles/live_full-1.0.0.json` | Live 可使用100%账户权益的硬风控 |
| `config/v2/risk_profiles/live_full-1.1.0.json` | 历史 live1 全放开边界 |
| `config/v2/risk_profiles/live_full-1.2.0.json` | 当前 live1 最低必要硬风控 |
| `config/v2/order_policies/live_equity-1.0.0.json` | Alpaca Live 美股订单能力合同 |
| `config/v2/submission_policies/alpaca_live-1.0.0.json` | Live 提交、取消、幂等和对账合同 |

文件名含 `paper` 的 policy 只为离线回归和历史 release 重现保留，不对应本机运行账户。

手工测试时，版本化 policy 的小改动会直接生效，并以新的内容 hash 进入轮次记录。
若改变策略语义、风险上限或订单能力，仍应新增版本并更新 Profile，避免同一版本名
对应两种长期行为。LaunchAgent 只有重新部署后才会读取改动。

## 12. 策略 Release

`strategies/core_long/` 保留多个版本是为了历史轮次可重现和内容身份审计：

| 版本 | 作用 |
| --- | --- |
| `1.0.0` | 初始 Coarse release |
| `1.0.1` | Coarse 修订 release |
| `1.1.0` | 加入 Portfolio 阶段 |
| `1.2.0` | 首个 Coarse + Portfolio + Execution 完整策略 |
| `1.3.0` | 中性持仓动作与执行合同修订 |
| `1.4.0` | Live 长周期策略过渡版本 |
| `1.5.0` | 历史 Live 策略；强化候选竞争、分批建仓与证据合同 |
| `1.6.0` | 刷新日线、可复算估值、限时迟滞、现金触发器与净入金校正绩效 |
| `1.6.1` | 增加跨日策略连续性、日报统一索引、本地粗选次轮刷新与中文摘要 |
| `1.6.2` | 当前 Live 策略；强制先尝试实时 Web 研究，真实工具故障才允许有界本地降级 |

每个版本中的文件类型：

- `manifest.json`：策略身份、兼容应用版本和允许的配置/prompt/schema 文件集合；
- `config/coarse_policy.json`：Coarse 数量和禁止字段；
- `config/portfolio_policy.json`：组合有效期、持仓数和权重约束；
- `config/execution_policy.json`：执行有效期和调整边界；
- `prompts/*.md`：对应阶段的模型任务；
- `prompts/*_AGENTS.md`：工作区内强制执行规则；
- `schemas/*.schema.json`：对应阶段的结构化输出合同。

manifest 继续严格限制文件集合：文件缺失、多出或路径越界都会阻止运行。允许集合内的
小内容修改不再因为 manifest 中的旧 hash 被拒绝；程序会按实际内容重新计算
`release_hash`、各文件 hash 和 `source_tree_hash`，因此修改会立即生效且仍可审计。
资产能力、筛选资格和日线摘要等 Coarse 决策事实也参与 revision 签名；事实改变会
创建新的 input/output revision，旧 revision 保留且不会与新输出混用。
账户绑定 hash、订单幂等 ID、部署 release manifest hash 和 Python 风控不受此放宽
影响，不能删除或绕过。

## 13. JSON Schemas

| 文件 | 作用 |
| --- | --- |
| `schemas/v2/daily_state.schema.json` | 日期级状态 |
| `schemas/v2/cycle_state.schema.json` | 单轮状态和步骤 |
| `schemas/v2/base_snapshot.schema.json` | 基础账户、持仓、订单和市场快照 |
| `schemas/v2/coarse_output.schema.json` | Coarse 输出 |
| `schemas/v2/proposed_orders.schema.json` | 本地拟议订单 |
| `schemas/v2/pretrade_snapshot.schema.json` | 下单前事实快照 |
| `schemas/v2/validated_orders.schema.json` | 通过 Python 风控的订单 |
| `schemas/v2/submission_intent.schema.json` | 写入前意图 |
| `schemas/v2/broker_submission.schema.json` | 券商提交响应 |
| `schemas/v2/reconciliation.schema.json` | 对账结果 |
| `schemas/v2/cycle_summary.schema.json` | 最终轮次摘要 |

## 14. Prompts

| 文件 | 作用 |
| --- | --- |
| `prompts/v2/coarse.md` | 应用级 Coarse 任务说明 |
| `prompts/v2/coarse_AGENTS.md` | Coarse 工作区约束 |

正式运行使用当前策略目录中的 prompts，并按实际内容 hash 记录身份；部署服务使用
不可变应用 release 内的同一组文件。

## 15. 数据目录

| 路径 | 作用 |
| --- | --- |
| `data/bars/daily/<SYMBOL>.json` | bootstrap 可用的有效历史日线种子 |
| `data/bars/intraday/<DATE>/<SYMBOL>.json` | 保留的历史分时数据 |
| `data/snapshots/assets.json` | bootstrap 可用的资产能力种子 |
| `var/shared/market_data/` | 当前部署实际使用和更新的共享行情 |

账户、持仓、订单等易变快照不再保存在 Git 的 `data/snapshots` 中。

## 16. 测试目录

`tests/v2` 是维护所必需的安全资产，不应为了缩小目录而删除。

支持文件：

- `support.py`：基础临时仓库、配置和状态 fixture；
- `fakes.py`：Alpaca/Codex 假客户端；
- `order_support.py`：订单测试 fixture；
- `submission_support.py`：提交与对账 fixture。

测试按职责分组：

- `test_account_*`、`test_assets_*`、`test_positions_*`、`test_orders_*`、
  `test_quotes_*`：券商对象规范化；
- `test_daily_bars.py`、`test_shared_data_paths.py`：行情缓存与共享路径；
- `test_coarse_*`：Coarse 模型、workspace、Codex、Schema 和复用；
- `test_portfolio_*`：组合模型、约束、复用和人工复查；
- `test_execution_*`：执行模型、市场阶段、workspace、签名和扩展时段；
- `test_order_*`、`test_pretrade_snapshot.py`：订单构建、精度、风控和幂等；
- `test_submission_*`、`test_reconciliation.py`、
  `test_partial_fill_reconciliation.py`：提交 journal、超时、恢复和对账；
- `test_cancel_race.py`、`test_replacement_dependency.py`：取消竞争与替换依赖；
- `test_main_stage_*.py`、`test_cycle_*.py`：主流程、恢复和最终状态；
- `test_stage_c_safety.py`、`test_stage_f_safety.py`、
  `test_stage_g_write_whitelist.py`：架构和券商写入白名单；
- `test_stage_h_*.py`：CLI、路径、锁、release、LaunchAgent、脱敏和部署管理；
- `test_strategy_*`、`test_risk_profile.py`、`test_order_policy_version.py`：
  版本化配置和策略 hash。

当前完整测试命令：

```bash
PYTHONPATH=src \
PYTHONPYCACHEPREFIX=/private/tmp/wa_v2_pycache \
.Alpaca/bin/python -m unittest discover -s tests/v2 -p 'test_*.py'
```

## 17. `var` 运行与部署目录

`var/` 不进入 Git，但属于当前系统的必要状态，不能随意删除。

```text
var/
├── deployment/live1/
│   ├── current.json
│   ├── previous.json
│   ├── history/
│   ├── releases/
│   └── staging/
├── shared/
│   ├── runtime/
│   ├── reports/
│   ├── market_data/
│   └── logs/
├── locks/
│   ├── deploy.lock
│   └── live1.run.lock
└── archive/                         # 仅保留仍有审计价值的显式归档
```

关键规则：

- release 只读，不保存业务状态；
- current/previous 原子切换；
- runtime/report/market data/logs 跨 release 共享；
- 部署锁和运行锁防止并发；
- 存活 PID 对应的锁不能被抢占；
- 陈旧锁可在确认进程不存在后恢复；
- rollback 不删除订单或运行状态。

## 18. 单轮产物

典型轮次目录：

```text
var/shared/runtime/accounts/live1/strategies/core_long/1.6.2/
└── YYYY-MM-DD/
        ├── daily_state.json
        ├── market_data_refresh.json
        ├── performance.json
    └── cycles/<CYCLE_ID>/
        ├── initial_guidance.json
        ├── user_review.json
        ├── base_snapshot.json
        ├── coarse/
        ├── portfolio/
        ├── execution/
        ├── orders/
        ├── cycle_state.json
        └── cycle_summary.json
```

订单目录中的重要文件：

- `action_plan.json`：取消/替换/新增依赖计划；
- `pretrade_snapshot.json`：写入前最新账户、订单、持仓和报价；
- `proposed.json`：策略与 Python 构造的拟议订单；
- `validation_summary.json`：每条订单通过或拒绝原因；
- `validated.json`：唯一允许进入提交层的订单；
- `request_specs.json`：最终 broker request 规格；
- `submission_intent.json`：每次券商写入前的持久化意图；
- `submission_journal.json`：幂等写入状态机；
- `broker_submission.json`：券商响应；
- `reconciliation.json`：券商最终状态对账。

没有自然 approved 订单时，不要求生成 submission 文件。

## 19. LaunchAgent

用户 LaunchAgent：

```text
~/Library/LaunchAgents/com.wa.trader.live1.plist
```

plist 只包含安全路径和运行参数，不包含 Alpaca 凭据变量名或值。服务通过根目录
`wa _service-run` 解析 current release，并由 release 中的业务代码运行。与此不同，
前台 `wa run` 刻意使用当前工作区源码，以便安全小改动无需部署即可先验证。

常用检查：

```bash
launchctl print "gui/$(id -u)/com.wa.trader.live1"
./wa status --live --json
./wa health --live --json
```

优先使用 `wa` 命令，不要直接编辑 plist 或手工杀进程。

## 20. Release 安全边界

release 白名单：

- `src/v2/`
- `config/v2/`
- `config/universe/`
- `schemas/v2/`
- `strategies/`
- `prompts/v2/`
- `requirements.txt`
- `requirements.lock`
- `wa`

明确排除：

- `.env_live`
- `.Alpaca`
- `account_bindings`
- `var`
- runtime、reports、market data 和 logs
- `.git`
- 测试和本地历史数据

安装步骤：

1. 检查工作树干净；
2. 从 Git 已跟踪白名单构建 staging；
3. 记录每个文件 SHA-256；
4. 运行测试、compile 和券商写入静态扫描；
5. 检查 release 不含凭据值；
6. 在 staging release 内运行真实 dry-run；
7. 验证文件集合与 manifest 完全一致；
8. 原子安装并设为只读；
9. 切换 current；
10. 启动 LaunchAgent 并执行 health；
11. health 失败时自动回滚。

## 21. 故障处理

### 21.1 `doctor` 失败

```bash
./wa doctor --live
```

逐项修复：

- `.Alpaca/bin/python` 不存在：重新建立虚拟环境；
- `codex` 不可用：修复 Codex CLI；
- credentials 缺失：只修改 `.env_live`；
- account binding 缺失：在确认账户后重新绑定；
- Live 环境或写门禁不一致：停止操作，恢复已验证的 Live policy。

### 21.2 退出码 10

已有轮次或部署运行。使用：

```bash
./wa status --live
```

不要删除仍对应存活 PID 的锁。

### 21.3 退出码 20

正常无订单，不需要重试或强制买入。

### 21.4 Codex 长时间运行或网络不可达

每次 Codex 调用前会对 `chatgpt.com:443` 执行最多 3 次、单次最多 5 秒的
TCP 与 TLS 握手，尝试之间等待 1 秒。短暂 VPN/TUN 抖动可以自行恢复；
连续 DNS、VPN、TCP 或 TLS 不可达时返回
`CODEX_NETWORK_UNAVAILABLE`，不会进入第二次应用层重试。

正常模型运行期间每 30 秒输出一次心跳；单阶段最长 600 秒。可以按 `Ctrl-C`
安全中断，系统会终止整个 Codex 子进程组，将当前步骤保存为可恢复的
`failed_retriable`，而不是永久停在 `running`。如果 Codex 已启动但在 30 秒内
持续报告 DNS、TLS、WebSocket 或 HTTPS 传输错误，系统也会提前终止并标记为
可重试网络失败，不再等待 Codex 完成内部的多轮重连。

检查当前网络：

```bash
curl -I --max-time 8 https://chatgpt.com
curl -I --max-time 8 https://api.openai.com/v1/models
```

HTTP 403/401 仍能证明 DNS、TLS 和服务路径可达；这里不要求匿名请求成功认证。

### 21.5 退出码 50

安全门禁阻止。常见原因：

- profile、账户或 policy 不符合；
- 市场阶段禁止；
- 数据过期或能力字段缺失。

不要通过修改状态文件绕过门禁。

### 21.6 退出码 60

写入结果 uncertain。必须停止自动重试，并按 client order ID 在 Alpaca 查询；
以 `submission_journal.json`、broker 查询和 reconciliation 恢复。

### 21.7 health degraded/unhealthy

```bash
./wa status --live --json
./wa health --live --json
./wa logs --live
```

检查 health 的 `reasons`。运行中的轮次可暂时 degraded；release hash 失败、服务未加载、
uncertain 写入或长时间无正常终态需要人工处理。

### 21.8 部署失败

部署不会在验证失败时切换 current。查看：

```bash
./wa logs --live
find var/deployment/live1/history -type f -maxdepth 1 -print
```

修复代码后先运行完整测试并提交，再重新部署。

## 22. 修改代码的规则

1. 只修改 `src/v2`，不要重新引入 v1；
2. 新文件开头必须说明作用和重要性；
3. 保持 broker 写调用只位于提交器和取消执行器；
4. Live 写入只能通过 `live1`、`alpaca_live`、账户绑定、写前 journal 和提交门禁；
   不得加入强制买入、盲重试或绕过风控的后门；
5. 不要原地修改已发布策略和版本化 policy；
6. 修改后运行完整测试、compile 和静态写扫描；
7. 部署前提交代码并确保工作树干净；
8. 不得提交 `.env_live`、账户绑定或 runtime；
9. 所有交易事实、计划和已执行操作必须分开记录；
10. 日报和轮次工件是审计记录，不得为了清理目录而删除。

## 23. 备份与恢复

- 已跟踪代码和配置由 Git 恢复；
- 当前和上一 release 位于 `var/deployment/live1/releases`；
- `./wa rollback --live` 用于应用版本回退；
- `.env_live` 和 Live 账户绑定应另行安全备份，不能提交 Git；
- 不要使用 `git reset --hard` 或直接删除 `var/shared/runtime` 处理一般故障。

## 24. 当前验收基线

本手册建立时的已验证基线：

- 完整 v2 离线测试通过；
- macOS bootstrap、dry-run deploy、status、health、logs 和 rollback 已验证；
- release 文件只读、manifest hash 有效；
- LaunchAgent 不包含凭据；
- 手工 `--allow-trade` 强制完整新决策，并在无 approved 订单时返回
  `completed_no_action`；
- Sunday overnight 市场阶段、下一交易日日历和 overnight feed 已覆盖；
- `live1` 是唯一生产凭据、账户绑定、policy、运行锁和服务身份；
- Live 手工运行支持首轮完整决策和同日计划 execution refresh；
- Live 自然语言日报支持联网新闻、持仓/订单解释及无变化时不追加。
