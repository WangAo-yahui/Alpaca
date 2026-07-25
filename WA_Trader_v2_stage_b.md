# WA Trader v2：阶段B实施任务与交易权限补充

版本：2026-07-23-stage-b-v1  
前提：阶段A（runtime、state models、CLI、exceptions、config、state machine及基础测试）已经完成。

本文件同时修订 v2 的交易权限设计。

---

# 1. 最终用户命令

最终最常用的无人值守 paper 交易命令必须是：

```bash
python3 -u src/v2/main.py --no-review --allow-trade
```

为了兼容已有习惯，CLI必须同时支持：

```text
--no-review
--no-need-review
--no_need_review

--allow-trade
--allow_trade
```

以上别名映射到同一个内部字段。

`--no review` 和 `--allow trade` 不是合法的标准命令行写法，因为空格会把它们拆成两个参数。正式写法使用连字符。

---

# 2. `--allow-trade` 的准确语义

## 2.1 默认不提交

未提供 `--allow-trade` 时：

```bash
python3 -u src/v2/main.py --no-review
```

系统仍然必须：

1. 运行维护；
2. 运行第一阶段或复用候选池；
3. 运行第二阶段或复用组合方案；
4. 运行第三阶段；
5. 构建订单；
6. 执行Python硬校验；
7. 保存 proposed.json 和 validated.json；
8. 生成日报或简报。

但是不得调用 Alpaca 提交订单。

本轮应标记为：

```text
dry_run = true
submission_enabled = false
```

没有订单提交不属于错误。

---

## 2.2 提供 `--allow-trade`

提供：

```bash
python3 -u src/v2/main.py --no-review --allow-trade
```

表示：

```text
submission_enabled = true
extended_hours_requested = true
session_policy = broker_capability
```

系统应：

1. 允许 Alpaca paper 订单提交；
2. 取消系统自身“零持仓只能在regular_session开仓”的硬门；
3. 允许第三阶段在盘前、盘后和券商支持的隔夜时段提出并执行新仓；
4. 根据券商当前支持能力转换为合法订单；
5. 仍然遵守用户硬限制、Python风险限制、资产状态、报价新鲜度和购买力；
6. 如果券商或订单类型不支持，则阻止、降级或排队，不得伪装为成功。

`--allow-trade` 不得：

- 启用真实账户；
- 绕过 Alpaca；
- 绕过用户禁止；
- 绕过购买力；
- 绕过报价年龄；
- 绕过资产 tradable 状态；
- 绕过最大仓位；
- 绕过重复订单检查；
- 将券商拒绝记录为成功。

v2初期仍只允许paper。

---

# 3. 盘前、盘后与隔夜的订单适配

当 `--allow-trade` 存在时，市场时段不再作为“是否允许研究或新开仓”的内部硬门。

市场时段改为决定“应该构造哪种合法订单”。

## 3.1 常规交易时段

`regular_session`：

- 可以按策略使用支持的 market、limit、stop、stop_limit、trailing_stop；
- 仍需遵守 order_policy；
- 第三阶段可以选择立即成交或限价等待。

## 3.2 扩展交易时段

包括券商当前支持的：

- pre-market；
- after-hours；
- overnight。

股票扩展时段订单默认适配为：

```text
type = limit
time_in_force = day 或 gtc
extended_hours = true
limit_price = 必填
```

如果第三阶段给出 market、stop、stop_limit、trailing_stop 或不支持的组合：

- 不得原样提交；
- order_builder 根据配置尝试转换为合法 limit；
- 无法安全转换时标记 blocked；
- 在 validation 中记录明确原因。

## 3.3 券商不接受的时段

例如：

- 周末没有可用交易时段；
- 交易所休市且券商不接受；
- 资产不支持扩展交易；
- 当前订单类型不支持；
- 报价不可用或严重过旧。

处理顺序：

1. 若配置允许且券商接受排队订单，构造可排队订单；
2. 否则标记 `blocked_by_broker_capability`；
3. 不应把它视为程序崩溃；
4. 不得无限重试；
5. 日报写明未提交原因。

---

# 4. 建议新增的CLI字段

`src/v2/cli.py`：

```python
@dataclass(frozen=True)
class CLIOptions:
    run_date: str | None
    cycle_id: str | None

    no_review: bool
    allow_trade: bool

    force_full: bool
    force_rebalance: bool
    execution_only: bool
    maintenance_only: bool
    new_cycle: bool

    paper: bool
    live: bool
```

参数定义：

```python
parser.add_argument(
    "--no-review",
    "--no-need-review",
    "--no_need_review",
    dest="no_review",
    action="store_true",
)

parser.add_argument(
    "--allow-trade",
    "--allow_trade",
    dest="allow_trade",
    action="store_true",
)
```

`--allow-trade` 与 `--maintenance-only` 冲突。

`--live` 仍然拒绝。

`--allow-trade` 默认只代表允许提交paper订单。

---

# 5. 状态文件需要增加的字段

## 5.1 cycle_state.json

增加：

```json
{
  "invocation": {
    "no_review": true,
    "allow_trade": true,
    "paper": true,
    "live": false
  },
  "trade_permission": {
    "submission_enabled": true,
    "extended_hours_requested": true,
    "session_policy": "broker_capability",
    "dry_run": false
  }
}
```

未提供 `--allow-trade`：

```json
{
  "trade_permission": {
    "submission_enabled": false,
    "extended_hours_requested": false,
    "session_policy": "analysis_only",
    "dry_run": true
  }
}
```

## 5.2 状态枚举

建议增加：

```text
completed_dry_run
completed_no_submission
completed_with_submissions
completed_with_open_orders
```

如果不希望扩大顶层状态，可在 `cycle_summary.json` 中记录 submission状态，但必须能够明确区分：

- 没有交易意图；
- 有订单但处于dry-run；
- 有批准订单且成功提交；
- 有批准订单但券商拒绝；
- 订单仍未成交。

---

# 6. 配置变更

## config/v2/order_policy.json

至少增加：

```json
{
  "submission": {
    "default_enabled": false,
    "paper_only": true
  },
  "extended_hours": {
    "enabled_when_allow_trade": true,
    "equity_order_type": "limit",
    "allowed_time_in_force": [
      "day",
      "gtc"
    ],
    "queue_when_broker_allows": true,
    "convert_market_intent_to_limit": true,
    "limit_price_reference": "conservative_quote",
    "max_spread_bps": 80,
    "max_quote_age_seconds": 30
  }
}
```

具体数值应由现有风险偏好确认，不能把示例值默认为最终生产参数。

## config/v2/risk.json

增加或确认：

```json
{
  "max_extended_hours_spread_bps": 80,
  "max_extended_hours_slippage_bps": 40,
  "allow_new_positions_extended_hours": true,
  "allow_fractional_extended_hours": true
}
```

---

# 7. 市场阶段规则修订

原来的规则：

```text
零持仓新开仓只允许regular_session
```

改为：

```text
当allow_trade=false：
    不提交任何订单。

当allow_trade=true：
    系统不因市场阶段本身禁止新仓；
    order_builder和order_validator根据券商能力、
    订单类型、报价和资产状态决定是否可提交。
```

市场阶段仍必须识别：

```text
before_market_open
regular_session
after_market_close
overnight_session
market_closed_weekend
market_closed_holiday
unknown
```

`unknown` 不得提交。

周末或休市时，如果券商不接受当前订单，则正常阻止。

---

# 8. 阶段B开发范围

本轮只完成数据层和上述交易权限接线。

不要调用Codex，不要实现三个阶段Prompt，不要真正提交订单。

需要完成：

```text
src/v2/data/
├── __init__.py
├── alpaca_client.py
├── account.py
├── positions.py
├── orders.py
├── assets.py
├── universe.py
├── daily_bars.py
├── intraday.py
├── quotes.py
└── snapshots.py
```

同时修改：

```text
src/v2/cli.py
src/v2/models/state.py
src/v2/main.py
src/v2/config.py
src/v2/state_machine.py
config/v2/order_policy.json
config/v2/risk.json
config/v2/market_data.json
```

---

# 9. 数据层详细要求

## 9.1 alpaca_client.py

提供统一工厂：

```python
@dataclass(frozen=True)
class AlpacaClients:
    trading: TradingClient
    stock_data: StockHistoricalDataClient
```

要求：

- 从环境变量读取凭据；
- 不打印密钥；
- 默认paper；
- 阶段B拒绝live；
- 缺少凭据给出致命配置错误；
- 提供清晰的API异常包装；
- 其他模块不得单独创建客户端。

支持依赖注入，以便测试时传入fake client。

---

## 9.2 account.py

输出规范化账户对象：

```json
{
  "account_id_hash": "...",
  "status": "ACTIVE",
  "trading_blocked": false,
  "account_blocked": false,
  "trade_suspended_by_user": false,
  "cash": 0,
  "buying_power": 0,
  "portfolio_value": 0,
  "equity": 0,
  "long_market_value": 0,
  "short_market_value": 0,
  "currency": "USD",
  "retrieved_at": "...",
  "source": "alpaca_paper"
}
```

不要将完整账户ID写入普通日志；可以保存hash或截断值。

---

## 9.3 positions.py

每个持仓规范化：

```json
{
  "symbol": "MU",
  "asset_id": "...",
  "side": "long",
  "quantity": 0,
  "available_quantity": 0,
  "average_entry_price": 0,
  "market_value": 0,
  "cost_basis": 0,
  "unrealized_pl": 0,
  "current_price": 0,
  "lastday_price": 0,
  "change_today": 0
}
```

按symbol排序。

---

## 9.4 orders.py

分别查询：

- open orders；
- today orders；
- 最近历史订单；
- 系统之前提交的订单。

规范化字段：

```json
{
  "broker_order_id": "...",
  "client_order_id": "...",
  "symbol": "MU",
  "side": "buy",
  "type": "limit",
  "time_in_force": "day",
  "quantity": 0,
  "filled_quantity": 0,
  "limit_price": 0,
  "stop_price": 0,
  "status": "new",
  "extended_hours": true,
  "submitted_at": "...",
  "updated_at": "..."
}
```

---

## 9.5 assets.py

至少提供：

```json
{
  "symbol": "MU",
  "tradable": true,
  "fractionable": true,
  "shortable": true,
  "easy_to_borrow": true,
  "exchange": "NASDAQ",
  "asset_class": "us_equity",
  "status": "active"
}
```

必须支持按标的批量或缓存读取。

---

## 9.6 quotes.py

提供：

```json
{
  "symbol": "MU",
  "bid_price": 0,
  "bid_size": 0,
  "ask_price": 0,
  "ask_size": 0,
  "midpoint": 0,
  "spread": 0,
  "spread_bps": 0,
  "quote_timestamp": "...",
  "quote_age_seconds": 0
}
```

无报价时：

```text
status = no_data
```

绝不能写成价格0后继续使用。

---

## 9.7 intraday.py

提供：

- 最近分钟bar；
- 当日开高低收；
- 当日成交量；
- 从开盘到当前涨跌；
- 最近5/15/30/60分钟变化；
- 波动和成交量异常摘要；
- window_status；
- market_phase。

不要求阶段B实现交易决策。

---

## 9.8 daily_bars.py

从v1逻辑重新实现，不导入v1。

要求：

- 至少300根；
- 增量更新；
- 单标的文件；
- 原子写入；
- OHLCV校验；
- 成功/no_data/failed分别记录；
- 允许部分失败；
- 支持输入标的列表。

---

## 9.9 universe.py

读取现有静态股票池和ETF池，但代码位于v2。

输出：

- symbols；
- asset type；
- source/version；
- must_include；
- exclusions；
- 输入签名。

不得让静态池中的重复标的进入后续输入。

---

## 9.10 snapshots.py

统一生成：

```text
cycles/<cycle_id>/base_snapshot.json
```

包含：

```json
{
  "schema_version": "1.0",
  "run_date": "...",
  "cycle_id": "...",
  "retrieved_at": "...",
  "market_phase": "...",
  "account": {},
  "positions": [],
  "open_orders": [],
  "today_orders": [],
  "assets": {},
  "capital": {
    "cash": 0,
    "buying_power": 0,
    "open_order_reserved_estimate": 0,
    "allocatable_capital_estimate": 0
  },
  "data_quality": {
    "account_fresh": true,
    "positions_fresh": true,
    "orders_fresh": true,
    "errors": [],
    "warnings": []
  }
}
```

---

# 10. 阶段B主函数行为

阶段B完成后，以下命令：

```bash
python3 -u src/v2/main.py --no-review --allow-trade
```

暂时应执行：

```text
初始化或恢复轮次
→ 维护步骤占位
→ 创建Alpaca paper客户端
→ 获取账户、持仓、订单
→ 创建base_snapshot.json
→ 判断初步轮次类型
→ 保存状态
→ 停在下一未实现步骤
```

因为第一、第二、第三阶段尚未实现，阶段B不能真的下单。

此时应明确输出：

```text
基础数据刷新成功
交易提交权限：enabled
当前阶段尚未实现Codex决策和下单，未提交任何订单
```

这不是错误。

未提供 `--allow-trade` 时输出：

```text
交易提交权限：dry-run
```

---

# 11. 阶段B测试

新增：

```text
tests/v2/
├── test_cli_trade_flags.py
├── test_alpaca_client.py
├── test_account_normalization.py
├── test_positions_normalization.py
├── test_orders_normalization.py
├── test_assets_normalization.py
├── test_quotes_normalization.py
├── test_market_phase.py
├── test_daily_bars.py
├── test_universe.py
└── test_base_snapshot.py
```

测试不得依赖真实网络。

使用fake clients或monkeypatch。

至少覆盖：

1. `--no-review`三个别名；
2. `--allow-trade`两个别名；
3. 未提供allow-trade默认dry-run；
4. allow-trade仍然paper；
5. allow-trade与maintenance-only冲突；
6. live被拒绝；
7. 密钥不出现在异常和日志；
8. 空持仓；
9. 空挂单；
10. 报价无数据；
11. spread计算；
12. base snapshot原子写入；
13. 周末市场阶段；
14. regular/pre/after/overnight识别；
15. 部分数据失败仍能形成带警告快照；
16. 关键账户数据失败则阻止后续决策。

---

# 12. 交给Codex的下一条指令

```text
请阅读：
- docs/WA_Trader_v2_implementation_spec.md
- docs/WA_Trader_v2_stage_b.md

阶段A已经完成。

现在只实施阶段B：数据层与交易权限接线。

重点新增：
1. --no-review，兼容--no-need-review和--no_need_review；
2. --allow-trade，兼容--allow_trade；
3. 没有--allow-trade时跑完整分析但不提交订单；
4. 有--allow-trade时允许paper提交，并解除系统自身的常规时段新仓限制；
5. 扩展时段订单必须由后续order builder按券商能力转换；
6. 阶段B不调用Codex，不实现三个决策阶段，不真正提交订单；
7. 实现统一Alpaca客户端、账户、持仓、订单、资产、股票池、日线、盘中、报价和base snapshot；
8. 不导入src/v1；
9. 不修改.env；
10. 所有JSON原子保存；
11. 所有网络逻辑可通过fake client测试；
12. 完成后运行全部tests/v2并报告变更和测试结果。

最终命令目标保持为：
python3 -u src/v2/main.py --no-review --allow-trade

但本阶段执行到base snapshot后正常停止，不提交订单。
```
