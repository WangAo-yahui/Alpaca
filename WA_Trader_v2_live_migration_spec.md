<!--
作用：保存 WA Trader v2 从 Alpaca Paper 迁移到 Live 实盘前的逐阶段、逐文件设计方案和风险分析。
重要性：这是历史设计与审计材料，不再是当前操作基线；已实现文件、参数和运行命令以 README.md 与当前源码为准。
-->

# WA Trader v2 Live 实盘迁移开发规范

版本：1.0  
制定日期：2026-07-27  
当前状态：历史迁移设计已被一次性 Live 实现取代；Live 只读账户与 dry-run 已验证，尚未由 Codex 发送任何真实 Live 订单  
目标 profile：`live1`  
目标服务：`com.wa.trader.live1`

## 1. 本文件怎样使用

> 2026-07-27 状态说明：用户随后要求“一步到位”完成开发，因此下面的 L0–L8
> 分阶段门禁、影子/canary 命令和待开发描述只用于回看原始设计，不表示当前 CLI
> 已实现这些历史命令。当前可执行命令与最终配置只参考 `README.md`。

这是迁移前的逐文件开发清单，保留用于核对最初设计取舍。原计划顺序为：

1. GPT 只实现当前阶段列出的文件，不顺带实现下一阶段。
2. 用户逐文件核对 diff、配置值、测试和运行证据。
3. 当前阶段所有验收项通过后，形成一次独立提交或明确的工作区快照。
4. 用户明确批准后，才进入下一阶段。
5. L4 以前禁止调用 Alpaca Live 写接口；L5 以前禁止任何真实订单。

本文中的“不保守”是指：

- 不省略文件、字段、命令、错误分支、测试和回滚细节；
- 给出明确的目标结构和建议参数，而不是只说“以后注意安全”；
- Paper 与 Live 都采用完整功能链路，不把 Live 永久限制成只能查看；
- 但不删除账户绑定、幂等、写前日志、风险上限、紧急停止和人工晋级门禁。这些属于真实资金系统的正确性，不是可选的“保守模式”。

## 2. 迁移目标和非目标

### 2.1 最终目标

完成后系统应同时支持：

- `paper1`：现有 Paper 自动交易，行为和历史产物保持兼容；
- `live1`：独立凭据、独立账户绑定、独立决策与订单产物、独立部署 release、独立运行锁、独立 launchd 服务；
- Live 只读、Live 影子决策、Live 人工 canary、Live 自动交易四种明确状态；
- 正常时段、盘前、盘后和 Alpaca 支持的 overnight 时段；
- 下单、取消、部分成交、拒绝、超时未知、进程恢复、交易更新流和 REST 对账；
- 代码回滚和真实订单/持仓处置分开执行；
- 每次 Live 写调用都可追溯到 profile、release、策略、风险、订单政策、提交政策、授权记录、写前 journal、`client_order_id` 和券商结果。

### 2.2 本次迁移不做

- 不支持期权、加密货币、做空和多腿订单；
- 不允许模型直接调用 Alpaca SDK；
- 不在 Git、日志、日报或 release manifest 中保存密钥和原始 account id；
- 不让 `--live`、`--allow-trade` 或环境变量单独决定交易环境；
- 不将停止服务等同于取消订单或平仓；
- 不对网络超时后的 Live 写操作进行盲目重试；
- 不自动把 Paper 的成交表现当作 Live 的成交保证。

## 3. 当前代码的真实状态

当前 v2 不是“已有 Live、只差开关”，而是 Paper 专用实现。主要硬编码如下：

| 范围 | 当前行为 | Live 迁移影响 |
|---|---|---|
| `src/v2/data/alpaca_client.py` | 拒绝 `live`，始终创建 `TradingClient(..., paper=True)` | 必须改成 profile 决定环境，并新增只读/写能力边界 |
| `src/v2/main.py` | profile 不是 `paper` 即拒绝；Stage G 只允许 `paper1` | 必须把环境与 profile 贯穿整个 cycle |
| `src/v2/models/state.py` | `live=True` 或 `paper=False` 均校验失败 | 必须用单一环境枚举替代矛盾布尔组合 |
| `src/v2/trading/order_validator.py` | 只接受 `paper1` 和 Paper policy | 必须校验“所有组件环境一致”，而非硬编码 Paper |
| `src/v2/trading/submission_guard.py` | `paper1_only`、`paper_client`、Live switch false | 必须改成按环境选择的完整写权限矩阵 |
| `src/v2/models/submission.py` | intent 和 submission 文档强制 `environment=paper` | 必须支持 Live 且保留历史 Paper 文档读取 |
| `src/v2/trading/submission_journal.py` | journal 固定写入 `paper` | 必须由 cycle 身份写入真实环境 |
| `src/v2/trading/reconciliation.py` | reconciliation 固定写入 `paper` | 必须按环境对账并增加 Live 成交活动核对 |
| `schemas/v2/*.schema.json` | 多个 schema 对 `paper1`、Paper policy 使用 `const` | 必须版本化为环境参数化合同 |
| `src/v2/deployment/constants.py` | 服务名、profile、策略版本全局固定 | 必须改成 profile 派生的部署身份 |
| `src/v2/deployment/paths.py` | Paper/Live 会共享 current、previous、release、日志和 deploy 状态 | 必须在 profile 维度隔离所有可变状态 |
| `src/v2/deployment/launchd.py` | 固定 `com.wa.trader.paper1` | 必须为每个 profile 生成独立 plist |
| `src/v2/deployment/manager.py` | doctor、deploy、run、health 均读取固定 Paper 文件 | 必须由显式 `--profile` 构造 manager |
| `config/v2/profiles/live.json` | 只是 disabled 占位，策略版本陈旧且缺订单/提交 policy | 不应直接启用，应由完整 `live1.json` 取代 |
| `config/v2/risk_profiles/live_conservative-1.0.0.json` | 字段不足，不能满足现有 Stage F 风控合同 | 不应直接使用，应创建完整 Live 风险版本 |

结论：第一步必须先把“Paper 专用硬编码”重构为“环境通用、默认仍为 Paper”，然后才接入 Live。直接翻转 SDK 的 `paper` 参数会造成部署状态和账户状态串用。

## 4. 外部事实和实现约束

以下事实以 Alpaca 官方文档为准：

- Live Trading API 使用 `https://api.alpaca.markets`，Paper 使用 `https://paper-api.alpaca.markets`；二者凭据互不通用。Market Data 使用 `https://data.alpaca.markets`。  
  参考：[Authentication](https://docs.alpaca.markets/us/v1.4.2/docs/authentication)
- Live 订单状态应同时通过 REST 查询和交易更新流维护；`trade_updates` 包含成交、部分成交、取消和拒绝。  
  参考：[Websocket Streaming](https://docs.alpaca.markets/us/docs/websocket-streaming)
- 每个 Trading API 响应带 `X-Request-ID`，官方建议持久化，且该 ID 不能通过其他接口事后查询。  
  参考：[Getting Started with Trading API](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api)
- `client_order_id` 可由客户端指定并用于查询订单；本系统必须在写前生成并持久化它。  
  参考：[Working with /orders](https://docs.alpaca.markets/us/v1.1/docs/working-with-orders)
- 扩展时段订单必须显式设置 `extended_hours`，本项目继续只允许扩展时段限价单。  
  参考：[Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca) 和 [24/5 Trading](https://docs.alpaca.markets/us/docs/245-trading-for-trading-api)
- 成交事实还可通过 Account Activities 的 `FILL`/`partial_fill` 记录核对。  
  参考：[Account Activities](https://docs.alpaca.markets/us/docs/account-activities)
- 自 2026-07-06 起，Trading API 已移除旧 PDT 相关字段，包括 `pattern_day_trader`、`daytrade_count`、`daytrading_buying_power`、`dtbp_check` 和 `pdt_check`。新实现不得把这些字段设为必需。  
  参考：[Trading API Update – PDT Fields Removed](https://docs.alpaca.markets/us/changelog/2026-07-06-pdt-db49dba) 和 [The Intraday Margin Rule](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule)

因此 Live 实现必须满足：

1. 环境由已加载 profile 决定，CLI 只能断言，不能覆盖。
2. Live 与 Paper 端点、凭据、账户绑定和写状态必须一一对应。
3. REST 是恢复和最终对账事实源；stream 用于降低状态延迟，不能成为唯一事实源。
4. 每个写调用先落盘，再调用券商；超时后先按 `client_order_id` 查询，禁止直接重发。
5. 旧 PDT 字段缺失必须被正常接受，不能导致账户解析失败或错误放行。

## 5. 目标身份模型

### 5.1 统一环境枚举

新增：

```python
class BrokerEnvironment(StrEnum):
    PAPER = "paper"
    LIVE = "live"
```

删除内部的双布尔语义：

```python
paper: bool
live: bool
```

改为：

```python
environment: BrokerEnvironment
profile_id: str
```

可以保留只读兼容属性：

```python
@property
def is_paper(self) -> bool:
    return self.environment is BrokerEnvironment.PAPER
```

但不得再允许 `paper=True, live=True`、`paper=False, live=False` 这类非法组合进入状态。

### 5.2 profile 是环境唯一权威

最终入口采用：

```bash
./wa doctor --profile paper1
./wa doctor --profile live1
./wa run --profile live1 --shadow --force-full
./wa run --profile live1 --allow-trade --authorization-id <id>
./wa deploy --profile live1 --mode shadow
```

规则：

- 无 `--profile` 时仍默认 `paper1`，保证现有使用方式兼容；
- `--profile live1` 加载 `config/v2/profiles/live1.json` 后，环境只能来自该文件；
- 废除可以改变环境的 `--live`；
- 如果保留 `--paper`/`--live` 兼容参数，只能作为 `--expect-environment` 断言，断言不一致立即失败；
- `--allow-trade` 只表达“本次调用请求写权限”，绝不单独授予写权限。

### 5.3 Live 写权限必须同时满足

Live submit/cancel 前必须全部为真：

1. `profile_id == live1`；
2. profile 已启用且 `environment == live`；
3. 客户端环境证明为 Live；
4. 当前 account hash 与 `live1` 绑定一致；
5. strategy、risk、order、submission policy 引用及内容 hash 与 cycle 一致；
6. submission policy 的 Live 写开关为真；
7. 部署模式为 `canary` 或 `auto`；
8. 本地 `global_kill_switch == false`；
9. `live1` emergency-stop 不存在或为 false；
10. 存在未过期、未消费且匹配当前 release/policy/risk hash 的 Live authorization；
11. 人工运行带 `--allow-trade`，或 launchd release 已明确部署为 `auto`；
12. 账户未被 `trading_blocked`、`account_blocked`、`trade_suspended_by_user` 等状态阻止；
13. 当前行情、时段、资产能力、订单类型、数量、价格、现金、集中度、日内损失和订单数全部通过；
14. submission intent 与 journal 已原子写入磁盘；
15. 当前没有 unresolved/uncertain Live 写操作。

任意一项失败都只能产生无写入的阻止结果，不得降级为绕过检查。

## 6. 目标目录与隔离

### 6.1 部署目录

从当前共享部署根：

```text
var/deployment/
  current.json
  previous.json
  releases/
  history/
  paper_submit_verified.json
```

迁移为：

```text
var/deployment/
  profiles/
    paper1/
      current.json
      previous.json
      releases/
      staging/
      history/
      verification/
        paper_submit_verified.json
    live1/
      current.json
      previous.json
      releases/
      staging/
      history/
      verification/
        live_read_verified.json
        live_shadow_verified.json
        live_canary_verified.json
```

L0 必须为现有 Paper 路径提供一次性、可重复执行的迁移：

- 旧路径存在且新路径不存在：原子移动到 `profiles/paper1/`；
- 新旧同时存在且内容一致：保留新路径并记录 migration complete；
- 新旧同时存在且内容不一致：停止，禁止自动选择；
- 不删除历史 release、日志、日报、订单 journal；
- migration 过程写 `var/deployment/path_migration_v1.json`，含源/目标、hash、时间和结果。

### 6.2 运行产物

```text
var/shared/runtime/accounts/<profile_id>/...
var/shared/reports/accounts/<profile_id>/...
var/shared/logs/<profile_id>/...
var/shared/market_data/...
var/locks/deploy.<profile_id>.lock
var/locks/run.<profile_id>.lock
```

账户、订单、授权、journal、reconciliation 和报告禁止跨 profile 共享。Market Data 可以共享，但必须：

- 只含公开行情/资产数据，不含账户字段；
- 原子写入；
- 每份快照带数据源、时间戳和内容 hash；
- cycle 通过 hash 引用，不原地覆盖已使用的事实快照。

### 6.3 launchd

```text
~/Library/LaunchAgents/com.wa.trader.paper1.plist
~/Library/LaunchAgents/com.wa.trader.live1.plist
```

两者必须有独立：

- Label；
- `--profile`；
- runtime/reports/logs 路径；
- run lock；
- current release；
- 标准输出/错误日志。

plist 中不得包含密钥、account id 或授权 token。

## 7. 新配置文件

### 7.1 `config/v2/profiles/live1.json`

替代现有占位 `live.json`。建议初始内容：

```json
{
  "_comment": "作用：定义唯一 live1 实盘账户身份、凭据变量和已审核 release 引用。重要性：profile 只允许读取指定 Live 凭据；写权限仍需 submission policy、部署模式和临时授权共同批准。",
  "schema_version": "1.1",
  "profile_id": "live1",
  "enabled": true,
  "broker": "alpaca",
  "environment": "live",
  "credential_key_env": "ALPACA_LIVE_API_KEY",
  "credential_secret_env": "ALPACA_LIVE_SECRET_KEY",
  "strategy": {
    "strategy_id": "core_long",
    "strategy_version": "1.2.0"
  },
  "risk_profile": "live_canary@1.0.0",
  "order_policy": "live_equity@1.0.0",
  "submission_policy": "alpaca_live_shadow@1.0.0"
}
```

说明：

- `enabled: true` 只允许该 profile 被加载和执行只读阶段，不代表可以下单；
- 初始 submission policy 必须是 shadow；
- `live.json` 在 L1 删除，但要先确认没有代码或部署引用；不把它重命名后直接启用；
- `.env` 只保存上述两个变量的值，不提交 Git。

### 7.2 `config/v2/risk_profiles/live_canary-1.0.0.json`

建议的首次真实单硬上限如下，后续实现 L5 前由用户逐项确认：

```json
{
  "_comment": "作用：限制 live1 首次人工 canary 订单的资金、数量、行情和损失边界。重要性：即使策略、模型或 CLI 给出更大订单，Python 也必须裁剪或拒绝，绝不允许覆盖。",
  "schema_version": "1.1",
  "risk_profile_id": "live_canary",
  "risk_profile_version": "1.0.0",
  "environment": "live",
  "settings": {
    "maximum_gross_exposure": "0.10",
    "maximum_single_position_weight": "0.05",
    "maximum_sector_weight": "0.10",
    "minimum_cash_weight": "0.90",
    "maximum_new_capital_per_cycle_weight": "0.02",
    "maximum_new_capital_per_cycle_notional": "50.00",
    "maximum_order_notional": "50.00",
    "maximum_order_count": 1,
    "maximum_open_order_count": 1,
    "minimum_order_value": "10.00",
    "allow_short_positions": false,
    "quote_max_age_seconds": "5",
    "regular_spread_limit_bps": "20",
    "extended_spread_limit_bps": "30",
    "maximum_daily_equity_drawdown_weight": "0.005",
    "allow_regular_session": true,
    "allow_extended_hours": false,
    "allow_overnight": false,
    "allowed_symbols": []
  }
}
```

首次 canary 的 `allowed_symbols` 不得为空；L5 前由用户指定一个实际标的并创建新版本 `1.0.1`，不修改已发布的 `1.0.0`。

### 7.3 `config/v2/risk_profiles/live_standard-1.0.0.json`

这是通过 canary 后的建议初始自动化参数，不在 L0-L5 自动启用：

```json
{
  "_comment": "作用：定义 live1 自动运行时的完整资金与损失边界。重要性：它是实盘仓位、单轮部署、订单数、行情质量和日内停止的最终 Python 硬约束。",
  "schema_version": "1.1",
  "risk_profile_id": "live_standard",
  "risk_profile_version": "1.0.0",
  "environment": "live",
  "settings": {
    "maximum_gross_exposure": "0.75",
    "maximum_single_position_weight": "0.08",
    "maximum_sector_weight": "0.25",
    "minimum_cash_weight": "0.25",
    "maximum_new_capital_per_cycle_weight": "0.20",
    "maximum_new_capital_per_cycle_notional": "5000.00",
    "maximum_order_notional": "2000.00",
    "maximum_order_count": 10,
    "maximum_open_order_count": 10,
    "minimum_order_value": "10.00",
    "allow_short_positions": false,
    "quote_max_age_seconds": "5",
    "regular_spread_limit_bps": "30",
    "extended_spread_limit_bps": "30",
    "maximum_daily_equity_drawdown_weight": "0.02",
    "allow_regular_session": true,
    "allow_extended_hours": true,
    "allow_overnight": true,
    "allowed_symbols": []
  }
}
```

这些是明确的开发建议值，不是假装适合所有账户的最终投资决策。进入 L7 前，用户必须根据 Live 账户净值逐项批准绝对金额和百分比；批准后以新版本固化。

### 7.4 `config/v2/order_policies/live_equity-1.0.0.json`

从 `paper_equity-1.0.0.json` 独立复制规则，但不得只改文件名。必须：

- `order_policy_id = live_equity`；
- `environment = live`；
- regular 支持 `market`、`limit`；
- extended/overnight 只支持 `limit`；
- extended 必须 `extended_hours = true`；
- canary 阶段只允许 `day`；
- 数量和价格使用 `Decimal`，禁止二进制浮点直接参与资金上限；
- 加入 `reject_when_asset_fractionable_unknown`；
- 加入 `reject_when_extended_hours_capability_unknown`；
- 加入 `reject_when_overnight_tradable_unknown`；
- 明确 `closed_session_queue = false`；
- 明确 `client_order_id` 最大长度；
- 明确 limit price tick 和 fractional precision 验证。

### 7.5 三个 Live submission policy

按晋级阶段创建不可变 policy：

| 文件 | 用途 | `allow_submit` | Live switch | 自动服务 |
|---|---:|---:|---:|---:|
| `alpaca_live_shadow-1.0.0.json` | Live 账户/行情/决策，只生成 intent 预览 | false | false | 可运行 |
| `alpaca_live_canary-1.0.0.json` | 人工、一次性、单订单 | true | true | 禁止 |
| `alpaca_live_auto-1.0.0.json` | canary 验证后自动运行 | true | true | 允许 |

三个文件都必须保留：

- `allow_direct_replace: false`；
- `blind_retry_count: 0`；
- 写失败后按 `client_order_id` 查询；
- `persist_before_each_write: true`；
- `persist_after_each_response: true`；
- `stop_after_uncertain_write: true`；
- `immediate_reconciliation: true`；
- sequential submit；
- cancel 与 submit 分开授权；
- request timeout、REST poll 和 stream gap 参数；
- `X-Request-ID` 持久化要求；
- emergency stop 和 global kill switch。

`canary` 额外要求：

- `maximum_write_count = 1`；
- `authorization_single_use = true`；
- `authorization_max_ttl_seconds = 900`；
- `automated_service_enabled = false`；
- regular session only；
- 禁止 cancel-all、replace 和自动追价。

## 8. 新运行控制文档

### 8.1 环境证明 `environment_attestation.json`

路径：

```text
var/shared/runtime/accounts/live1/environment_attestation.json
```

字段：

```json
{
  "schema_version": "1.0",
  "profile_id": "live1",
  "environment": "live",
  "trading_base_domain": "api.alpaca.markets",
  "market_data_base_domain": "data.alpaca.markets",
  "sdk_paper_flag": false,
  "credential_key_env": "ALPACA_LIVE_API_KEY",
  "credential_secret_env": "ALPACA_LIVE_SECRET_KEY",
  "account_id_hash": "<sha256>",
  "account_status": "<normalized>",
  "verified_at": "<UTC>",
  "expires_at": "<UTC>",
  "release_hash": "<sha256>"
}
```

禁止写入凭据值、完整 URL 中的认证信息或原始 account id。

### 8.2 Live authorization

路径：

```text
var/shared/runtime/accounts/live1/authorizations/<authorization_id>.json
```

字段至少包括：

- `authorization_id`；
- `profile_id`、`environment`、`account_id_hash`；
- `mode`：`canary_submit`、`cancel` 或 `auto_window`；
- `created_at`、`expires_at`；
- `single_use`、`consumed_at`；
- `allowed_symbols`；
- `maximum_write_count`；
- `maximum_total_notional`；
- `strategy_release_hash`；
- `risk_policy_hash`；
- `order_policy_hash`；
- `submission_policy_hash`；
- `deployment_release_hash`；
- `approved_by = user`；
- `approval_context_hash`。

授权不得包含自由文本密钥。消费授权必须与写前 journal 在同一临界区完成；进程崩溃后，已进入 `request_started` 的 single-use 授权视为已消费。

### 8.3 emergency stop

路径：

```text
var/shared/runtime/accounts/live1/controls/emergency_stop.json
```

命令：

```bash
./wa emergency-stop --profile live1 --reason "..."
./wa emergency-status --profile live1
./wa emergency-clear --profile live1 --authorization-id <id>
```

`emergency-stop` 只禁止新 submit/cancel，不自动取消已挂订单、不自动平仓。需要处置挂单时执行独立、可预览的：

```bash
./wa reconcile --profile live1
./wa cancel-plan --profile live1 --all-open
./wa cancel-execute --profile live1 --authorization-id <cancel-id>
```

## 9. 逐文件迁移表

### 9.1 L0：环境通用化，Paper 行为必须完全不变

| 文件 | 操作 | 精确改动 | 必须新增/修改的测试 |
|---|---|---|---|
| `src/v2/models/environment.py` | 新增 | 定义 `BrokerEnvironment`、解析、序列化和 `is_paper/is_live` | 新增 `test_environment.py` |
| `src/v2/cli.py` | 修改 | 内部 options 改为 `environment`；现有默认仍为 paper1；兼容参数只做断言 | 修改 `test_cli.py`、`test_cli_trade_flags.py` |
| `src/v2/models/state.py` | 修改 | `InvocationState` 使用单环境；历史状态中的 paper/live 布尔字段只读迁移；新文档写 `environment` | 修改 `test_state_models.py`、`test_state_machine.py` |
| `src/v2/config.py` | 修改 | 不再要求系统只能 Paper；仍要求默认 profile 为启用的 Paper；校验 profile 与 policy 环境一致 | 修改 `test_config.py`、`test_default_profile.py` |
| `src/v2/profiles.py` | 修改 | submission policy 允许 paper/live；公共安全字段仍强制；增加环境一致性校验 | 修改 `test_profile_loading.py`、`test_submission_policy.py` |
| `src/v2/data/alpaca_client.py` | 修改 | 工厂以 profile.environment 选择 SDK `paper`；删除全局 `ALPACA_PAPER` 权威；L0 仍用 feature gate 拒绝加载 Live | 修改 `test_alpaca_client.py` |
| `src/v2/main.py` | 修改 | 把硬编码 `"paper"` 改为 runtime identity；L0 入口仍通过 `LIVE_FEATURE_DISABLED` 阻止 Live | 修改 main 各阶段测试 |
| `src/v2/models/execution.py` | 修改 | execution identity 不再 const paper1；组件环境必须相同 | 修改 execution model/schema 测试 |
| `src/v2/models/submission.py` | 修改 | intent/result 接收显式 environment；禁止空值，不再固定 Paper | 修改 `test_submission_models.py` |
| `src/v2/trading/submission_journal.py` | 修改 | 构造器必须接收 environment，加载时核对环境 | 修改 `test_submission_journal.py` |
| `src/v2/trading/reconciliation.py` | 修改 | 文档环境来自调用方，不再固定 Paper | 修改 `test_reconciliation.py`、`test_partial_fill_reconciliation.py` |
| `src/v2/trading/order_validator.py` | 修改 | 校验 profile/risk/order 环境一致；L0 写权限仍只允许 paper1 | 修改 `test_order_validator.py` |
| `src/v2/trading/submission_guard.py` | 修改 | 把 Paper 专用布尔表抽象为环境矩阵；L0 Live 行始终 false | 修改 `test_submission_permissions.py` |
| `src/v2/trading/order_submitter.py` | 修改 | 注释与错误语义改为 broker submitter；行为不变 | 修改 `test_order_submitter.py`、timeout/idempotency 测试 |
| `src/v2/trading/order_action_executor.py` | 修改 | 注释与身份改成环境通用；取消仍走 guard | 修改 `test_order_action_executor.py`、`test_cancel_race.py` |
| `src/v2/data/account.py` | 修改 | `source` 改成 `alpaca_<environment>`；兼容 PDT 字段缺失 | 修改 `test_account_normalization.py` |
| `src/v2/data/orders.py` | 修改 | source/environment 参数化 | 修改 `test_orders_normalization.py` |
| `src/v2/data/positions.py` | 修改 | source/environment 参数化 | 修改 `test_positions_normalization.py` |
| `src/v2/stages/execution.py` | 修改 | 删除 `paper1` const，使用 identity；L0 feature gate 保持 Paper-only | 修改 `test_execution_stage.py` |
| `src/v2/exceptions.py` | 修改 | `LiveTradingRejected` 改为 `LiveFeatureDisabled`；新增 Live 专用稳定错误码 | 修改 `test_exceptions.py` |

L0 不修改策略逻辑，不创建 Live 凭据，不接触 Alpaca Live API。完成标准：

- 全部现有 Paper 测试通过；
- `./wa doctor` 和 `./wa run` 行为与迁移前一致；
- `--profile live1` 明确返回 `LIVE_FEATURE_DISABLED`；
- 新代码中除兼容层、测试和 Paper 配置外，不再用 `paper=True/live=False` 组合表达环境；
- 旧 Paper cycle 文档仍可读取。

### 9.2 L0：部署按 profile 隔离

| 文件 | 操作 | 精确改动 | 测试 |
|---|---|---|---|
| `src/v2/deployment/constants.py` | 修改 | 只保留退出码和默认 interval；移除固定 `PROFILE_ID`、`SERVICE_LABEL`、策略版本 | `test_stage_h_cli.py` |
| `src/v2/deployment/identity.py` | 新增 | `DeploymentIdentity(profile_id, environment, service_label)`；严格过滤路径和 label | 新增 `test_stage_h_identity.py` |
| `src/v2/deployment/paths.py` | 修改 | `for_project(..., profile_id=...)`；current/release/log/lock/marker 全部 profile 化 | `test_stage_h_paths.py`、`test_shared_data_paths.py` |
| `src/v2/deployment/path_migration.py` | 新增 | 实现旧 Paper 路径到新路径的幂等迁移和冲突阻止 | 新增 `test_stage_h_path_migration.py` |
| `src/v2/deployment/release.py` | 修改 | manifest identity 来自 profile；release hash 包含 profile 和 environment | `test_stage_h_release.py`、`test_cycle_release_metadata.py` |
| `src/v2/deployment/launchd.py` | 修改 | label、参数、环境路径来自 identity；ProgramArguments 显式带 profile | `test_stage_h_launchd.py` |
| `src/v2/deployment/manager.py` | 修改 | 构造器要求 profile；doctor/deploy/run/status/health/rollback 只操作该 profile | `test_stage_h_manager.py` |
| `src/v2/deployment/cli.py` | 修改 | 所有命令支持 `--profile`；默认 paper1；输出显示环境和 profile | `test_stage_h_cli.py` |
| `src/v2/deployment/locks.py` | 修改 | deploy/run lock 按 profile；部署全局资源时另用短期 global lock | `test_stage_h_locks.py` |
| `wa` | 核对，通常不改 | 仍只负责加载部署 CLI，禁止直接选择 SDK 环境 | CLI 集成测试 |

L0 部署验收：

```bash
./wa doctor --profile paper1
./wa status --profile paper1 --json
./wa health --profile paper1 --json
./wa run --profile paper1
```

必须证明：

- 旧 current release 被完整迁入 `profiles/paper1`；
- Paper launchd 可正常恢复；
- 没有删除任何历史 journal/reconciliation/report；
- `paper1` 与一个虚构测试 profile 的锁、日志、current、previous 不相同。

### 9.3 L0：Schema 兼容迁移

修改：

- `schemas/v2/proposed_orders.schema.json`
- `schemas/v2/validated_orders.schema.json`
- `schemas/v2/submission_intent.schema.json`
- `schemas/v2/broker_submission.schema.json`
- `schemas/v2/reconciliation.schema.json`
- `schemas/v2/cycle_state.schema.json`
- `schemas/v2/cycle_summary.schema.json`
- `schemas/v2/daily_state.schema.json`
- `schemas/v2/pretrade_snapshot.schema.json`

策略：

1. 新文档使用 `schema_version: "1.1"`；
2. `profile_id` 从 `const: paper1` 改成受限 pattern；
3. `environment` 使用 `enum: [paper, live]`；
4. policy 名称不再写死，但文档中的 release 引用必须符合 `name@version`；
5. Python 交叉校验 profile/risk/order/submission 环境和 hash，不能只靠 JSON Schema；
6. 历史 `1.0` 文档由兼容 reader 验证，禁止原地改写历史证据；
7. 新增一组 `1.0 -> domain model -> 1.1` 的只读迁移 fixture；
8. `coarse_output.schema.json`、`base_snapshot.schema.json` 若没有 Paper const，只增加身份交叉测试，不做无意义改动。

对应测试：

- `test_execution_schema.py`
- `test_coarse_schema.py`
- `test_portfolio_schema.py`
- `test_submission_models.py`
- `test_cycle_release_metadata.py`
- 新增 `test_schema_1_0_compatibility.py`
- 新增 `test_schema_environment_mismatch.py`

### 9.4 L1：Live 只读连接和账户绑定

| 文件 | 操作 | 精确改动 |
|---|---|---|
| `config/v2/profiles/live1.json` | 新增 | 使用 shadow policy；不授予写权限 |
| `config/v2/profiles/live.json` | 删除 | 先以 `rg` 和测试证明无引用 |
| `src/v2/data/alpaca_client.py` | 修改 | 允许创建 Live client；按 profile 凭据名读取；SDK `paper=False` |
| `src/v2/data/broker_capabilities.py` | 新增 | 将客户端包装为 `read_only` 或 `trade_write` 能力；L1 不暴露 submit/cancel |
| `src/v2/data/environment_attestation.py` | 新增 | 读取账户后写环境证明；检查域名、SDK flag、account hash |
| `src/v2/profiles.py` | 修改 | 允许 `live1` enabled，绑定仍强制 |
| `src/v2/data/account.py` | 修改 | 规范化 Live 账户阻止字段、现金、equity、buying power；旧 PDT 字段可缺失 |
| `src/v2/main.py` | 修改 | 增加 `live_read_only` cycle；不进入 Stage G |
| `src/v2/reports/daily_report.py` | 修改 | 报告明确 `live/read_only`，计划与成交分开 |
| `schemas/v2/environment_attestation.schema.json` | 新增 | 约束证明文档且禁止额外敏感字段 |

L1 唯一允许的外部调用：

- get account；
- get account configuration（若 SDK/账户支持）；
- get positions；
- get open orders；
- get assets；
- get market clock/calendar；
- get market data。

禁止构造或调用 submit、cancel、replace。测试必须使用 fake Live client，真实验证只在用户显式批准后运行一次：

```bash
./wa doctor --profile live1
./wa bind-account --profile live1 --read-only
./wa run --profile live1 --read-only
```

验收证据必须只显示 account hash，不显示原始账户号或凭据。

### 9.5 L2：Live 影子决策

修改：

- `src/v2/main.py`
- `src/v2/stages/portfolio.py`
- `src/v2/stages/execution.py`
- `src/v2/models/execution.py`
- `src/v2/trading/order_builder.py`
- `src/v2/trading/order_validator.py`
- `src/v2/reports/daily_report.py`
- `config/v2/order_policies/live_equity-1.0.0.json`
- `config/v2/risk_profiles/live_canary-1.0.0.json`
- `config/v2/submission_policies/alpaca_live_shadow-1.0.0.json`

影子模式必须完整执行：

- 使用 Live 账户的现金、持仓、挂单和 buying power；
- 使用当前真实行情；
- 生成 portfolio decisions；
- 生成 proposed/validated order request specs；
- 生成 submission intent preview；
- `expected_write_count` 可以表示“若授权会写几次”，但 `submission_performed=false`；
- 报告中将 shadow proposed 与真实 broker orders 分栏；
- 不创建可被 submitter 直接消费的 active authorization。

命令：

```bash
./wa run --profile live1 --shadow --force-full
```

验收：

- monkeypatch submit/cancel 为“一旦调用就测试失败”；
- Live shadow 与相同输入下的 Paper 规划差异只能来自账户/持仓/挂单/环境 policy；
- 周末、假日和不支持的时段仍生成决策结论，但不得生成当前不可提交的 request；
- overnight 可交易时段能产生符合限价/extended-hours 合同的 shadow request。

### 9.6 L3：Live 写路径离线完成，仍禁止真实写

新增：

- `src/v2/models/live_control.py`
- `src/v2/trading/live_authorization.py`
- `src/v2/trading/environment_guard.py`
- `schemas/v2/live_authorization.schema.json`
- `schemas/v2/live_control.schema.json`
- `tests/v2/test_live_authorization.py`
- `tests/v2/test_live_environment_guard.py`
- `tests/v2/test_live_write_matrix.py`

修改：

- `src/v2/trading/submission_guard.py`
- `src/v2/trading/submission_journal.py`
- `src/v2/trading/order_submitter.py`
- `src/v2/trading/order_action_executor.py`
- `src/v2/models/submission.py`
- `src/v2/main.py`
- `src/v2/deployment/manager.py`

必须离线覆盖的故障：

- Paper 凭据连到 Live profile；
- Live 凭据连到 Paper profile；
- account hash 变化；
- authorization 过期、重复消费、hash 不匹配；
- release 在授权后改变；
- limit price/qty 超上限；
- 写前 journal 落盘失败；
- submit 网络超时但按 client id 查到订单；
- submit 网络超时且查不到订单，进入 `UNCERTAIN`；
- 进程在 request started 后崩溃；
- cancel 与 fill 竞态；
- emergency stop 在计划后、写前出现；
- stream 断开；
- 旧 PDT 字段不存在。

L3 仍使用 fake transport；真实 Alpaca Live 写方法必须由 compile-time/runtime capability guard 阻止。

### 9.7 L4：Live 交易更新和双重对账

新增：

| 文件 | 作用 |
|---|---|
| `src/v2/trading/trade_updates.py` | 消费 Alpaca `trade_updates`，保存完整订单生命周期事件 |
| `src/v2/trading/activity_reconciliation.py` | 使用 account activities 核对 fill/partial fill、手续费和更正 |
| `src/v2/models/broker_event.py` | 规范化 stream/REST/activity 事件 |
| `schemas/v2/broker_event.schema.json` | 事件合同 |
| `schemas/v2/activity_checkpoint.schema.json` | 分页/恢复 checkpoint |
| `tests/v2/test_trade_updates.py` | stream 顺序、重复、断线、重连测试 |
| `tests/v2/test_activity_reconciliation.py` | fill、partial fill、更正和分页测试 |

规则：

- stream 事件 append-only，按稳定事件标识或内容键去重；
- 收到 stream fill 后仍需 REST/account activity 收敛确认；
- stream gap 时标记 degraded，禁止依赖旧订单状态继续扩大仓位；
- REST order、positions、account activities 不一致时状态为 `RECONCILIATION_MISMATCH`；
- Live submit 响应元数据增加 `x_request_id`；SDK 无法暴露响应头时允许 `null`，但必须明确记录 `request_id_capture_supported=false`，不能伪造；
- 每轮结束必须输出：broker order id、client order id、最终状态、成交量、均价、活动 id、最后 REST/stream 时间。

### 9.8 L5：首次 Live canary

进入条件：

- L0-L4 全部通过；
- `live1` 环境证明有效；
- 用户逐项批准 canary risk 和唯一 symbol；
- 用户批准 `alpaca_live_canary@1.0.0`；
- 没有 unresolved Live order；
- emergency stop 关闭；
- 当前在 regular session；
- 服务模式仍为 shadow，禁止 launchd 自动 submit。

建议操作序列：

```bash
./wa doctor --profile live1
./wa reconcile --profile live1
./wa run --profile live1 --shadow --force-full
./wa authorize-live \
  --profile live1 \
  --mode canary-submit \
  --symbol <APPROVED_SYMBOL> \
  --max-orders 1 \
  --max-notional 50 \
  --ttl-seconds 900
./wa run \
  --profile live1 \
  --allow-trade \
  --authorization-id <AUTHORIZATION_ID>
./wa reconcile --profile live1
```

首次订单只允许：

- 一个已批准 symbol；
- 单笔不超过 50 USD；
- regular session；
- `DAY`；
- 用户核对过的 limit order；
- 不自动改单、不追价；
- 不自动提交第二笔；
- 订单状态未知时立即停在 uncertain。

这 50 USD 不是最终 Live 仓位上限，只是验证真实写链路、账户绑定、幂等、日志和对账是否正确。验证成功后通过新 policy/risk 版本扩大，不修改历史版本。

### 9.9 L6：扩展时段与 overnight Live

只有 regular-session canary 完整成交或明确取消并对账后，才实现：

- `before_market_open`；
- `after_market_close`；
- `overnight_session`。

必须验证：

- 资产 `tradable`；
- fractional 能力；
- `overnight_tradable`/halt 状态可用；缺失时 fail closed；
- order type 为 limit；
- `extended_hours=true`；
- TIF 符合 policy；
- quote 新鲜度和点差；
- limit price 没有跨越配置的最大滑点；
- Sunday overnight 的日历归属和日报 run_date 一致；
- 未成交 DAY/GTC 订单在下个 session 的处理方式明确。

L6 单独做一个扩展时段 canary authorization，不能沿用 regular canary 授权。

### 9.10 L7：Live 自动服务

进入条件：

- 至少一次 regular canary 和一次计划使用的扩展时段 canary 完整对账；
- 没有 uncertain/reconciliation mismatch；
- `live_canary_verified.json` 与当前代码、风险、订单、提交 policy hash 一致；
- 用户批准 `live_standard` 的所有金额和百分比；
- 用户批准 `alpaca_live_auto`；
- Live service 的 start interval 和运行时段明确；
- 监控和 emergency stop 已演练。

部署：

```bash
./wa deploy --profile live1 --mode auto
./wa health --profile live1 --json
./wa start --profile live1
./wa status --profile live1 --json
```

不得用通用的 `./wa deploy --enable-trading` 同时影响 Paper 和 Live。

### 9.11 其余现有文件的明确处置

为了避免“表中没写，所以不知道是否需要改”，其余活跃文件按下表处理：

| 文件或目录 | 阶段 | 处置 |
|---|---|---|
| `config/v2/system.json` | L0 | 保留 `default_profile=paper1`；以 `default_environment=paper` 替代全局 `trading_mode` 权威；`allow_live` 只能作为 feature availability，不能授予写权限 |
| `config/v2/order_policy.json` | L0/L2 | 标记为 legacy；L0 保持 Paper 回归，L2 移除其 `paper_only` 对 Live 的全局阻止，所有可执行订单规则以 profile 引用的版本化 order policy 为准 |
| `config/v2/risk.json` | L2 | 继续作为策略/模型的全局建议约束，但 Python 最终硬约束只取 profile 的版本化 risk profile；二者冲突时取更严格值并记录来源 |
| `config/v2/stages.json` | L2 | 增加 read-only/shadow/canary/auto 的阶段有效期和复用规则；不保存账户或授权状态 |
| `config/v2/market_data.json` | L6 | 核对 overnight phase 和数据新鲜度；它只定义共享行情采集，不授予交易权限 |
| `config/v2/universe.json`、`config/universe/*.json` | 不改 | 股票池可由 Paper/Live 共享；Live 可交易性仍以实时 asset 能力二次过滤 |
| `config/v2/profiles/default.json` | 不改或删除 | 当前是示例 profile；不得成为运行默认。L0 核对引用后，若 README/测试无需示例则删除，不能改成 Live |
| `config/v2/profiles/paper1.json` | 回归基线 | 不就地改成 Live；仅在 schema 1.1 迁移时做等价字段升级 |
| `config/v2/profiles/paper2.json`、`paper3.json` | 不改 | 继续 disabled，作为多 profile 隔离测试 fixture |
| `config/v2/risk_profiles/paper_standard-*.json` | 不改 | 作为 Paper 不回归基线；新增 Live 文件而不是覆盖 |
| `config/v2/order_policies/paper_equity-1.0.0.json` | 不改 | 作为 Paper 不回归基线；新增 `live_equity` |
| `config/v2/submission_policies/alpaca_paper-1.0.0.json` | 不改 | Live policy 必须独立版本；Paper policy 的 Live switch 继续 false |
| `src/v2/codex/runner.py` | L2 核对 | 不授予 broker 能力；只确保 prompt/runtime identity 显示 live1 和 Live 账户摘要 |
| `src/v2/codex/validation.py` | L2 核对 | 校验模型输出 identity 与 cycle 一致；任何环境/profile 漂移都拒绝 |
| `src/v2/codex/workspace.py` | L0 核对 | 工作区路径必须已按 profile 隔离；不得读取另一 profile 的账户产物 |
| `src/v2/data/_normalization.py` | L1 核对 | Decimal/时间/枚举规范化可复用；若新增账户字段只加通用 helper |
| `src/v2/data/assets.py` | L1/L6 | 增加 Live 下单所需的 `tradable`、`fractionable`、extended/overnight 能力规范化及 missing-field 状态 |
| `src/v2/data/daily_bars.py`、`intraday.py`、`quotes.py`、`snapshots.py`、`universe.py` | L2/L6 核对 | 行情逻辑共享；文档 source 不得错误写成 Paper；保留时间戳和 feed |
| `src/v2/data/execution_snapshot.py`、`pretrade_snapshot.py` | L2 | identity/environment 参数化，加入 authorization 前后的账户/行情 hash 核对 |
| `src/v2/guidance.py`、`src/v2/review.py` | L2 核对 | 允许 Live 影子建议，但永远不能生成或消费 Live authorization |
| `src/v2/models/coarse.py`、`portfolio.py`、`orders.py` | L2 核对 | 模型结构可复用；所有输出 identity 必须由 runtime 注入，不能由模型自行选择 |
| `src/v2/releases.py` | L0/L2 核对 | 策略 release 对 Paper/Live 共用；release hash 不因 profile 改变，cycle 另行记录 profile |
| `src/v2/reports/daily_report.py` | L1-L5 修改 | 明确 environment、mode、authorization、真实订单与 shadow 订单分栏 |
| `src/v2/runtime.py` | L0 | 路径 helper 必须要求 profile；原子写和时间 helper 保持不变 |
| `src/v2/state_machine.py` | L0/L3 | 新增 live read/shadow/armed/uncertain 状态转换，禁止从 read-only 直接跳到 submitted |
| `src/v2/stages/coarse.py`、`portfolio.py` | L2 核对 | 允许 Live identity，但保持无 broker 写能力 |
| `src/v2/trading/idempotency.py` | L3 | client order id 必须包含 `live1` profile 命名空间，并校验长度；环境/account hash 不直接明文写入 ID |
| `src/v2/trading/order_builder.py`、`order_request_factory.py` | L2/L3 | 使用 Live order policy 和 Decimal；factory 仍只创建本地 request object，不调用券商 |
| `src/v2/deployment/redaction.py` | L1 | 加入 Live 凭据、HTTP header 和 SDK 异常脱敏 |
| `src/v2/deployment/__init__.py`、各 package `__init__.py` | 按需 | 只导出新增稳定类型，不放运行逻辑 |
| `README.md` | 每阶段末 | 更新已完成状态和用户命令；不能提前声称 Live 已启用 |
| `WA_Trader_v2_live_migration_spec.md` | 每阶段末 | 只更新阶段状态、用户批准值和实际偏差；保留原设计与变更理由 |
| `.env` | 用户控制 | 仅在 L1 真实只读验证前由用户写入 Live 密钥；Codex 不回显、不提交、不复制到 release |
| `account_bindings/*.json` | L1 | Paper binding 保留；Live 绑定只写 hash 和环境，不写原始 account id |
| `data/bars/**` | 不改 | 共享历史行情事实，不随 Live 迁移批量改写或删除 |
| `var/**`、`reports/**`、`decision_runtime_v2/**` | 不纳入源码修改 | 只由对应 profile 的运行过程写入；迁移不得清空历史证据 |

任何未来发现但未列在本表的新文件，必须先把“作用、是否改、所属阶段、测试、回滚”补回本规范，再实施。

## 10. 现有测试文件的处理原则

### 10.1 必须保持为 Paper 回归的测试

以下测试继续证明 Paper 不受 Live 迁移影响，不能简单改成 Live：

- `test_paper1_deployment.py`
- `test_default_profile.py`
- `test_stage_c_safety.py`
- `test_stage_f_safety.py`
- `test_stage_g_write_whitelist.py`
- 所有 order submit idempotency/timeout/cancel/partial-fill 测试
- 所有 Stage H release/lock/rollback 测试

### 10.2 参数化为 Paper/Live 的测试

- `test_alpaca_client.py`
- `test_account_binding.py`
- `test_profile_loading.py`
- `test_risk_profile.py`
- `test_order_policy_version.py`
- `test_submission_policy.py`
- `test_submission_models.py`
- `test_submission_journal.py`
- `test_reconciliation.py`
- `test_execution_models.py`
- `test_execution_schema.py`
- `test_cycle_release_metadata.py`
- `test_daily_report.py`
- `test_stage_h_paths.py`
- `test_stage_h_launchd.py`
- `test_stage_h_manager.py`
- `test_stage_h_cli.py`

每个参数化测试至少覆盖：

- 正确 Paper；
- 正确 Live；
- profile 与 policy 环境不一致；
- profile 与 client 环境不一致；
- profile 与 binding 不一致。

### 10.3 新增 Live 专用测试

```text
tests/v2/test_environment.py
tests/v2/test_schema_1_0_compatibility.py
tests/v2/test_schema_environment_mismatch.py
tests/v2/test_stage_h_identity.py
tests/v2/test_stage_h_path_migration.py
tests/v2/test_live_read_only.py
tests/v2/test_live_environment_attestation.py
tests/v2/test_live_authorization.py
tests/v2/test_live_environment_guard.py
tests/v2/test_live_write_matrix.py
tests/v2/test_live_canary_limits.py
tests/v2/test_live_emergency_stop.py
tests/v2/test_live_uncertain_recovery.py
tests/v2/test_live_account_without_pdt_fields.py
tests/v2/test_trade_updates.py
tests/v2/test_activity_reconciliation.py
tests/v2/test_live_launchd_isolation.py
```

## 11. 错误码

新增稳定错误码，CLI、cycle state、日报和部署 health 必须一致：

| 错误码 | 含义 | 是否允许自动重试 |
|---|---|---:|
| `LIVE_FEATURE_DISABLED` | 当前 release 尚未进入 Live 阶段 | 否 |
| `LIVE_READ_ONLY` | 当前 capability 不允许写 | 否 |
| `LIVE_ENVIRONMENT_ATTESTATION_FAILED` | 端点/SDK/profile/account 环境不一致 | 否 |
| `LIVE_ACCOUNT_BINDING_REQUIRED` | 未绑定或 hash 不匹配 | 否 |
| `LIVE_NOT_ARMED` | 缺少有效 authorization | 否 |
| `LIVE_AUTHORIZATION_EXPIRED` | 授权过期 | 否 |
| `LIVE_AUTHORIZATION_MISMATCH` | 授权与 release/policy/hash 不一致 | 否 |
| `LIVE_AUTHORIZATION_CONSUMED` | single-use 授权已使用 | 否 |
| `LIVE_CAP_EXCEEDED` | 金额、数量、仓位或订单数超限 | 否 |
| `LIVE_ACCOUNT_BLOCKED` | 券商账户不允许交易 | 检查后再运行 |
| `LIVE_ORDER_UNCERTAIN` | 写请求结果未知 | 禁止 submit 重试，只允许查询 |
| `LIVE_STREAM_GAP` | 订单更新流中断或缺口 | 可重连，但扩大仓位前需 REST 对账 |
| `LIVE_RECONCILIATION_MISMATCH` | REST/stream/activity/positions 不一致 | 否 |
| `LIVE_EMERGENCY_STOP` | 本地紧急停止生效 | 否 |
| `LIVE_RELEASE_NOT_VERIFIED` | 当前 release 未通过对应晋级验证 | 否 |

## 12. 安全日志和敏感信息

允许写入：

- profile id；
- environment；
- account id 的 SHA-256；
- credential 环境变量名称；
- symbol、side、qty、limit price；
- client order id、broker order id；
- order status、fill qty、fill price；
- Alpaca `X-Request-ID`；
- policy/reference/hash；
- UTC/ET 时间。

禁止写入：

- API key/secret；
- Authorization header；
- `.env` 内容；
- 原始 account id；
- SDK 异常中可能包含的请求头；
- 未经 redaction 的 HTTP request/response dump。

`src/v2/deployment/redaction.py` 和错误包装需要增加以下测试：

- Live credential 变量值；
- `APCA-API-KEY-ID`；
- `APCA-API-SECRET-KEY`；
- Bearer/Authorization；
- 查询字符串中的敏感字段；
- 多行 SDK 错误；
- `.env` 中的 Paper 和 Live 值同时存在。

## 13. 回滚和事故处置

### 13.1 代码回滚

```bash
./wa stop --profile live1
./wa rollback --profile live1
./wa doctor --profile live1
./wa health --profile live1
```

代码回滚只切换 `live1` release，不触碰：

- open orders；
- positions；
- account cash；
- journal；
- reconciliation；
- reports；
- market data；
- Paper service。

### 13.2 真实资金状态处置

停止或回滚后必须单独执行：

```bash
./wa reconcile --profile live1
./wa positions --profile live1
./wa orders --profile live1 --status open
```

如果需要取消：

1. 生成 cancel plan；
2. 用户核对每个 broker/client order id；
3. 创建 cancel authorization；
4. 执行取消；
5. REST/stream 对账；
6. 再决定是否保留或平掉持仓。

禁止把“取消全部订单”和“平掉全部持仓”塞进 rollback 命令。

### 13.3 uncertain

任何 Live 写操作出现 unknown/timeout：

1. journal 保持 `request_started` 或 `uncertain`；
2. 停止本轮剩余写操作；
3. 按 `client_order_id` 查询；
4. 查询所有 open/closed orders；
5. 查询 positions 和 account activities；
6. 有证据后转成 confirmed/failed definite；
7. 仍无证据则保持 blocked，禁止生成替代订单。

## 14. 完整命令合同

最终建议命令：

```bash
# 检查
./wa doctor --profile <profile>
./wa status --profile <profile> [--json]
./wa health --profile <profile> [--json]

# 账户与事实
./wa bind-account --profile live1 --read-only
./wa account --profile live1
./wa positions --profile live1
./wa orders --profile live1 --status open
./wa reconcile --profile live1

# 决策
./wa run --profile paper1 [--force-full] [--allow-trade]
./wa run --profile live1 --read-only
./wa run --profile live1 --shadow [--force-full]
./wa run --profile live1 --allow-trade --authorization-id <id>

# Live 授权
./wa authorize-live --profile live1 --mode canary-submit ...
./wa authorization-status --profile live1 --authorization-id <id>
./wa revoke-authorization --profile live1 --authorization-id <id>

# 应急
./wa emergency-stop --profile live1 --reason "..."
./wa emergency-status --profile live1
./wa emergency-clear --profile live1 --authorization-id <id>

# 部署
./wa deploy --profile paper1 --mode auto
./wa deploy --profile live1 --mode shadow
./wa deploy --profile live1 --mode auto
./wa start|stop|restart|rollback --profile <profile>
```

所有改变券商状态的命令必须在输出顶部打印：

```text
PROFILE=live1
ENVIRONMENT=live
ACCOUNT_HASH=<hash-prefix>
MODE=<canary|auto|cancel>
MAX_WRITES=<n>
MAX_NOTIONAL=<amount>
AUTHORIZATION=<id>
```

不得打印密钥或原始账户号。

## 15. CI/本地验收命令

每个阶段至少运行：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/wa_trader_pycache \
  .Alpaca/bin/python -m compileall -q src/v2 tests/v2

.Alpaca/bin/python -m unittest discover -s tests/v2 -p 'test_*.py'

rg -n 'paper1_only|paper_client|environment": "paper"|TradingClient\\(.*paper=True' \
  src/v2 schemas/v2

git diff --check
git status --short
```

说明：

- `rg` 命中兼容层、Paper policy 或 Paper 回归测试可以接受，但每一处必须解释；
- 测试不得读取真实 `.env`；
- Live 测试默认使用 fake client/transport；
- 只有 L1 只读验收和 L5 canary 在用户明确批准后访问真实 Live；
- 测试报告必须分别列出 Paper 回归、Live 离线、Live 只读、Live 写入四类，不能只给总数。

## 16. 每阶段交付模板

GPT 每完成一个阶段，必须按以下格式交付：

```text
阶段：
实现范围：
未实现范围：

逐文件：
- path
  - 为什么改
  - 实际改动
  - 新增合同/字段
  - 可能影响

测试：
- 命令
- 结果
- 未运行项及原因

外部调用：
- 是否访问 Alpaca
- 环境：none/paper/live-read/live-write
- 是否发生 broker 写入
- client/broker order id（如有）

安全状态：
- live enabled?
- live armed?
- emergency stop?
- unresolved write?

下一阶段：
- 需要用户逐项批准的文件和值
```

## 17. 分阶段提交边界

建议提交顺序：

1. `live-l0-environment-model`：环境枚举和历史状态兼容；
2. `live-l0-profile-deployment-isolation`：profile 级路径、锁、release、launchd；
3. `live-l0-schema-compatibility`：1.1 schema 与 1.0 reader；
4. `live-l1-read-only-client`：Live client、环境证明、账户绑定；
5. `live-l2-shadow-decision`：Live 账户驱动的影子决策；
6. `live-l3-write-guards-offline`：authorization、guard、journal 故障矩阵；
7. `live-l4-stream-reconciliation`：trade updates 和 activity 对账；
8. `live-l5-canary`：只包含用户已批准的 canary 配置和真实验收证据；
9. `live-l6-extended-hours`：扩展/overnight canary；
10. `live-l7-auto-service`：Live launchd 自动模式。

不要把 L0-L7 合并成一次大改。每次提交必须可独立审查、可回滚，并且上一阶段的安全状态不依赖下一阶段才能成立。

## 18. 第一轮应实施什么

下一步只实施 **L0 第一小步：统一环境模型**，建议限定为：

- 新增 `src/v2/models/environment.py`；
- 修改 `src/v2/cli.py`；
- 修改 `src/v2/models/state.py`；
- 修改必要的 state/CLI 测试；
- 保持 Live feature gate；
- 不改 Alpaca client；
- 不改 deployment；
- 不改配置；
- 不访问网络。

用户核对这几个文件后，再进入 L0 的 profile 部署隔离。这样可以逐文件控制细节，同时避免一次改动同时触碰运行身份、部署指针和券商接口。

## 19. 最终完成定义

只有以下全部成立，才可以称为 Live 实盘迁移完成：

- Paper 与 Live 可同时部署、运行、停止、回滚且状态完全隔离；
- Live 默认是只读或 shadow，不存在隐式写权限；
- 每次 Live 写入有有效 authorization 和写前 journal；
- 超时不会盲目重试；
- stream、REST、activity、positions 能收敛对账；
- regular、extended、overnight 各自通过对应 canary；
- 当前 Alpaca 账户字段变化（包括 PDT 字段移除）不会错误阻止或错误放行；
- 用户已逐项批准 live standard 风险值；
- emergency stop、取消计划和代码回滚均演练；
- 日报明确区分计划、提交、成交、未成交、拒绝、取消和 uncertain；
- 没有任何凭据或原始 account id 出现在 Git、release、日志或报告中；
- Live 自动服务使用单独的 `com.wa.trader.live1`，不会影响 `paper1`。
