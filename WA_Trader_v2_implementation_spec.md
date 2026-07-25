# WA Trader v2 完整实施规格

版本：2026-07-23  
用途：交给 Codex 直接实施  
优先级：本文件高于现有 v1 代码和临时实现。

## 一、项目目标

WA Trader v2 是一个面向美股中长期投资的自动研究、组合决策和 Alpaca paper 下单系统。

目标：

1. 每个纽约交易日第一次运行时完成完整调查；
2. 同一天后续调用自动跳过可复用步骤；
3. 支持未来小时级、分钟级调用；
4. 第一阶段筛选候选，第二阶段分配组合，第三阶段按最新报价决定执行；
5. Codex只做研究和结构化决策，Python拥有最终风控和下单权；
6. 每次调用形成独立轮次，不覆盖历史；
7. 首轮生成详细日报，后续轮次只追加带时间戳的短更新；
8. 自动模式除真实错误或安全阻断外不暂停；
9. v2 初期只允许 paper 自动交易；
10. v2 不得导入 src/v1。

时区统一使用：

```text
America/New_York
```


## 二、不可变权限规则

权限顺序：

```text
Python硬风控
>
用户明确禁止与硬限制
>
第三阶段执行判断
>
第二阶段组合方案
>
用户软偏好与正向交易请求
>
第一阶段候选意见
```

含义：

- 用户说“买入MU”只是请求，第三阶段可以拒绝；
- 用户说“禁止买入TSLA”属于硬限制，第三阶段不能绕过；
- 第三阶段可以修改或拒绝第二阶段；
- 第二阶段只能在当天候选池范围内选股；
- Codex不能读取交易凭据，不能调用Alpaca，不能声称订单已经执行；
- 最终数量、价格复核、时段检查、重复订单检查和提交均由Python完成。


## 三、日期与轮次模型

目录分两层。

### 日期级

```text
decision_runtime_v2/YYYY-MM-DD/
```

保存：

- 当天第一阶段输入、输出和校验；
- daily_state.json；
- 当天全部轮次索引；
- 当天详细日报状态。

### 轮次级

每次调用主函数创建或恢复：

```text
cycles/YYYYMMDDTHHMMSS/
```

保存：

- 本轮账户与行情快照；
- 第二阶段输入输出；
- 人工意见；
- 第三阶段输入输出；
- Python订单计划；
- Alpaca返回；
- 对账结果；
- 本轮摘要。

轮次类型：

- `daily_full`：当天第一次完整运行；
- `intraday_rebalance`：当天后续重新分配资金或仓位；
- `execution_refresh`：组合仍有效，只刷新报价并重新执行判断；
- `maintenance_only`：只维护历史订单和日报。


## 四、完整运行流程

### 1. daily_full

```text
维护历史订单与上一日报
→ 刷新账户、持仓、挂单、资产状态
→ 更新日线
→ 第一阶段筛选60只
→ 获取候选盘中摘要
→ 第二阶段形成组合方案
→ 可选人工意见
→ 再次刷新账户、持仓、挂单、报价
→ 第三阶段决定执行
→ Python生成订单
→ Python硬校验
→ Alpaca paper提交
→ 保存本轮结果
→ 生成详细日报
```

### 2. intraday_rebalance

触发条件包括：

- 上轮订单被拒绝、取消、过期；
- 部分成交后有剩余资金；
- 上轮交易没有被采用；
- 持仓或挂单变化；
- 新增可用资金；
- 上轮第三阶段要求重新分配；
- 第二阶段方案过期；
- 用户使用 `--force-rebalance`。

流程：

```text
维护旧订单
→ 刷新状态
→ 复用当天60只候选
→ 重跑第二阶段
→ 可选人工意见
→ 第三阶段
→ Python订单流程
→ 追加简报
```

### 3. execution_refresh

适用：

- 当天候选有效；
- 第二阶段方案有效；
- 组合结构未变化；
- 只是报价、价差、时段或限价条件变化。

流程：

```text
维护旧订单
→ 刷新状态和报价
→ 复用候选池
→ 复用第二阶段方案
→ 第三阶段
→ Python订单流程
→ 追加简报
```

### 4. maintenance_only

```text
查询历史订单
→ 更新成交、部分成交、取消和拒绝
→ 更新持仓和日报
→ 正常结束
```


## 五、三阶段职责

### 第一阶段：候选池

输入：

- 股票和ETF完整候选池；
- Python基础筛选结果；
- 至少300根日线摘要；
- 必须覆盖和排除标的；
- 股票池版本和输入签名；
- 必要公开信息。

输出必须：

- 恰好60只；
- 去重后仍为60只；
- 全部来自输入股票池；
- 包含研究资格与新仓筛选资格；
- 包含联网状态、时间、输入签名；
- 通过Schema与业务校验。

禁止输出：

- 目标权重；
- 最终订单；
- 最终数量；
- 最终开仓许可。

同一纽约交易日默认只运行一次。只有新交易日、结果缺失或无效、股票池变化、输入签名变化或 `--force-full` 时重跑。

### 第二阶段：组合决策

输入：

- 当天60只候选；
- 最新账户、持仓和挂单；
- 可分配资金与挂单占用；
- 日线和盘中摘要；
- 最近订单结果；
- 上一有效组合方案；
- 本轮重新平衡原因。

输出：

- 市场判断；
- 目标现金比例；
- 目标投资比例；
- 目标持仓和目标权重；
- 单标的最大权重；
- open/increase/hold/reduce/close/watch/avoid；
- 入场价格区间；
- 保护方案；
- 方案有效期；
- 是否允许第三阶段微调；
- 是否要求下轮重平衡；
- 来源与联网状态。

禁止：

- 输出最终股数；
- 调用Alpaca；
- 选择60只候选外的新仓；
- 绕过用户硬限制。

### 第三阶段：执行代理

第三阶段前必须重新获取：

- account；
- positions；
- open orders；
- latest trade/quote；
- bid、ask、midpoint、spread；
- recent minute bars；
- market phase；
- asset tradable状态。

输入还包括：

- 第二阶段方案；
- 人工意见；
- 最近订单结果；
- 当前剩余资金；
- Python风险配置。

第三阶段可以：

- 接受、修改、延后或拒绝第二阶段；
- 调高或调低目标执行比例；
- 根据价格过高或过低调整；
- 修改订单类型或限价；
- 分批执行；
- 保留、取消或替换挂单；
- 请求下一轮重新平衡；
- 自由联网核查最新信息。

联网原则：

> 可以自由联网，但没有重大变化时无需机械重复第二阶段的完整调查。优先检查最新报价、持仓、挂单、市场状态和执行风险。

禁止：

- 扫描候选池外的新仓；
- 输出最终股数；
- 直接调用Alpaca；
- 绕过用户禁止和Python风控。


## 六、人工意见

默认人工模式：

```bash
python3 -u src/v2/main.py
```

第二阶段结束后显示一次：

```text
请输入本轮补充意见，直接回车继续：
```

直接回车也继续。

无人值守：

```bash
python3 -u src/v2/main.py --no-need-review
```

兼容：

```bash
python3 -u src/v2/main.py --no_need_review
```

自动生成：

```json
{
  "mode": "skipped_by_flag",
  "raw_comment": "",
  "constraints": [],
  "prohibitions": [],
  "preferences": [],
  "trade_requests": []
}
```

人工意见分类：

- `constraints`：硬限制；
- `prohibitions`：明确禁止；
- `preferences`：软偏好；
- `trade_requests`：正向交易请求。


## 七、市场时段硬规则

市场阶段至少包括：

```text
before_market_open
regular_session
after_market_close
market_closed_weekend
market_closed_holiday
unknown
```

规则：

1. 零持仓标的新开仓只允许在 `regular_session`；
2. 盘前、盘后、周末、休市日不能提交零持仓新开仓；
3. 已有持仓可以被第三阶段分析和管理；
4. 实际提交必须符合Alpaca订单时段规则；
5. 不能安全提交时生成延后或无操作结果；
6. `unknown` 时段不得提交新订单；
7. Python下单前必须再次确认市场阶段。


## 八、源码文件清单

```text
src/v2/
├── __init__.py
├── main.py
├── cli.py
├── runtime.py
├── state_machine.py
├── config.py
├── exceptions.py
│
├── models/
│   ├── __init__.py
│   ├── state.py
│   ├── snapshots.py
│   ├── coarse.py
│   ├── portfolio.py
│   ├── execution.py
│   └── orders.py
│
├── data/
│   ├── __init__.py
│   ├── alpaca_client.py
│   ├── account.py
│   ├── positions.py
│   ├── orders.py
│   ├── assets.py
│   ├── daily_bars.py
│   ├── intraday.py
│   ├── quotes.py
│   ├── universe.py
│   └── snapshots.py
│
├── codex/
│   ├── __init__.py
│   ├── runner.py
│   ├── workspace.py
│   └── validation.py
│
├── stages/
│   ├── __init__.py
│   ├── coarse.py
│   ├── portfolio.py
│   └── execution.py
│
├── trading/
│   ├── __init__.py
│   ├── order_builder.py
│   ├── order_validator.py
│   ├── order_submitter.py
│   └── reconciliation.py
│
└── reports/
    ├── __init__.py
    └── daily_report.py
```

关键职责：

- `main.py`：唯一主入口；
- `cli.py`：参数与冲突校验；
- `runtime.py`：纽约日期、轮次、路径、原子写入；
- `state_machine.py`：步骤跳转、恢复和阶段跳过；
- `config.py`：加载并校验v2配置；
- `exceptions.py`：可重试、安全阻断、致命错误；
- `models/`：结构化数据模型；
- `data/`：Alpaca与行情数据；
- `codex/`：工作区、调用、Schema和业务校验；
- `stages/`：三个阶段统一接口；
- `trading/`：订单生成、校验、提交、对账；
- `reports/`：详细日报和增量简报。


## 九、Prompt、Schema与配置

Prompt：

```text
prompts/v2/
├── coarse.md
├── coarse_AGENTS.md
├── portfolio.md
├── portfolio_AGENTS.md
├── execution.md
└── execution_AGENTS.md
```

Schema：

```text
schemas/v2/
├── coarse_output.schema.json
├── portfolio_output.schema.json
├── execution_output.schema.json
├── proposed_orders.schema.json
├── validated_orders.schema.json
├── daily_state.schema.json
└── cycle_state.schema.json
```

配置：

```text
config/v2/
├── system.json
├── risk.json
├── stages.json
├── market_data.json
├── order_policy.json
└── universe.json
```

配置职责：

- `system.json`：时区、超时、重试、paper策略、目录；
- `risk.json`：单标的上限、行业上限、最低现金、最大滑点、报价年龄、做空与分数股；
- `stages.json`：候选数量、阶段有效期、重平衡规则；
- `market_data.json`：日线根数、分钟数据窗口、时段规则；
- `order_policy.json`：支持订单类型、TIF、限价和重复订单规则；
- `universe.json`：股票池、ETF池、必须覆盖与排除标的。


## 十、运行目录

```text
decision_runtime_v2/
└── YYYY-MM-DD/
    ├── daily_state.json
    ├── coarse/
    │   ├── input.json
    │   ├── output.json
    │   ├── validation.json
    │   └── workspace/
    └── cycles/
        └── YYYYMMDDTHHMMSS/
            ├── cycle_state.json
            ├── base_snapshot.json
            ├── user_review.json
            ├── cycle_summary.json
            ├── portfolio/
            │   ├── input.json
            │   ├── output.json
            │   ├── validation.json
            │   └── workspace/
            ├── execution/
            │   ├── input.json
            │   ├── output.json
            │   ├── validation.json
            │   └── workspace/
            └── orders/
                ├── proposed.json
                ├── validated.json
                ├── broker_submission.json
                └── reconciliation.json
```

日报：

```text
reports/v2/daily/YYYY-MM-DD.md
```


## 十一、状态机

步骤：

```text
START
MAINTAIN_PREVIOUS
REFRESH_BASE_DATA
DECIDE_CYCLE_KIND
RUN_COARSE
RUN_PORTFOLIO
COLLECT_REVIEW
REFRESH_EXECUTION_DATA
RUN_EXECUTION
BUILD_ORDERS
VALIDATE_ORDERS
SUBMIT_ORDERS
SAVE_CYCLE
UPDATE_REPORT
COMPLETE
```

允许跳过：

- 当天粗选有效：跳过 `RUN_COARSE`；
- 第二阶段方案有效：跳过 `RUN_PORTFOLIO`；
- 无人值守：不等待人工输入，但仍生成空意见文件；
- 无合法订单：跳过提交并正常完成；
- maintenance_only：只维护和写报告。

状态有效性不能只看文件存在，还必须检查：

- Schema；
- 业务校验；
- 日期和cycle；
- 输入签名；
- 数据年龄；
- 配置版本。


## 十二、数据层要求

### Alpaca客户端

统一由 `data/alpaca_client.py` 创建。

要求：

- 默认paper；
- v2初期拒绝live；
- 不写密钥到日志；
- 统一超时与异常包装；
- 其他模块不得重复创建客户端。

### 基础快照

每轮生成 `base_snapshot.json`，包含：

- account；
- positions；
- open_orders；
- today_orders；
- assets摘要；
- cash、buying_power、portfolio_value；
- 挂单占用资金估算；
- 可分配资金估算；
- market_phase；
- UTC和纽约时间；
- 数据年龄与错误。

### 日线

- 至少300根；
- 增量更新；
- 每标的独立保存；
- 校验OHLCV；
- 无数据不得解释为价格0；
- 单标的失败不能破坏其他标的。

### 第三阶段实时数据

必须有：

- latest trade；
- latest quote；
- bid/ask/midpoint/spread；
- quote timestamp；
- recent minute bars；
- tradable状态；
- market phase。


## 十三、Codex调用规则

每个阶段使用独立工作区。

Codex允许读取：

```text
data/
config/
prompts/
schemas/
AGENTS.md
```

Codex只允许写：

```text
.tmp/codex/
```

禁止直接写：

- 正式output；
- validation；
- 状态文件；
- 源码；
- 配置；
- 报告。

Python负责：

1. 准备工作区；
2. 调用Codex；
3. 捕获最终JSON；
4. Schema校验；
5. 业务校验；
6. 原子保存；
7. 更新状态。

`codex/runner.py`要求：

- 统一超时；
- stdout/stderr捕获；
- 不泄露环境变量；
- 最多一次主调用和一次安全重试；
- 不无限重试；
- 保存调用摘要。

Schema通过不代表业务通过。


## 十四、Python订单流程

### order_builder.py

输入第三阶段结果和最新状态，计算：

- 当前权重；
- 目标市值；
- 差额；
- 最终可买/可卖数量；
- 分数股；
- 最小订单价值；
- 现金保留；
- 限价。

输出：

```text
orders/proposed.json
```

### order_validator.py

提交前必须再次刷新：

- account；
- positions；
- open orders；
- quotes；
- assets；
- market phase。

校验：

1. paper账户；
2. 账户可交易；
3. 购买力；
4. 实际持仓；
5. 重复订单；
6. 相反方向冲突；
7. 最大权重；
8. 最低现金；
9. 最小订单价值；
10. 报价年龄；
11. 最大滑点；
12. 市场时段；
13. tradable/fractionable/shortable；
14. 用户硬限制；
15. 候选资格；
16. 订单参数合法性。

输出：

```text
orders/validated.json
```

每单状态：

```text
approved
blocked
skipped
```

### order_submitter.py

只提交 `approved`。

要求：

- 只允许paper；
- 本地幂等键；
- 提交前再检查未完成订单；
- 逐单保存broker order id和响应；
- 单个失败不能丢失其他结果；
- 不记录密钥。

输出：

```text
orders/broker_submission.json
```


## 十五、订单对账与重新分配

`trading/reconciliation.py` 在每次主函数启动时首先执行。

职责：

- 查询过去提交订单；
- 更新filled、partially_filled、cancelled、rejected、expired；
- 更新成交数量和均价；
- 更新挂单；
- 判断资金是否重新释放；
- 判断是否触发 `intraday_rebalance`；
- 更新上一轮reconciliation和日报。

规则：

- 有效挂单对应资金视为占用；
- 取消、拒绝、过期后资金重新可分配；
- 部分成交部分进入持仓；
- 未成交部分按实际挂单状态处理；
- 不允许对同一资金重复分配。


## 十六、第二阶段是否重跑

实现：

```python
should_run_portfolio(context) -> Decision
```

返回：

```text
run
reuse
block
```

附带原因。

默认重跑：

- 无有效方案；
- 方案过期；
- 持仓变化；
- 挂单变化；
- 可分配资金显著增加；
- 原订单取消、拒绝、过期；
- 上轮第三阶段要求重平衡；
- 新人工意见；
- `--force-rebalance`。

默认复用：

- 组合结构未变化；
- 只是价格变化；
- 限价未到；
- 方案仍有效；
- 只需第三阶段重新执行判断。


## 十七、输入签名与幂等性

使用SHA-256。

第一阶段签名至少包含：

- 股票池版本；
- ETF池版本；
- 日线截止时间；
- 筛选配置；
- 必须覆盖与排除标的。

第二阶段签名至少包含：

- 候选池hash；
- 持仓hash；
- 挂单hash；
- 可分配资金；
- 行情截止时间；
- 风控版本。

第三阶段签名至少包含：

- 第二阶段输出hash；
- 人工意见hash；
- 持仓与挂单hash；
- 最新报价时间；
- market phase；
- 风控版本。

所有正式JSON使用：

```text
临时文件
→ flush
→ fsync
→ os.replace
```

订单本地幂等键建议：

```text
run_date + cycle_id + symbol + side + intent_index
```


## 十八、日报

首轮 `daily_full` 创建完整日报：

- 市场概况；
- 第一阶段摘要；
- 第二阶段组合；
- 目标现金；
- 目标持仓；
- 实际订单；
- 当前持仓；
- 未完成订单；
- 风险；
- 后续关注。

后续轮次只追加：

```markdown
## 12:05 ET 更新

- 轮次：20260723T120502
- 类型：execution_refresh
- 持仓变化：...
- 挂单变化：...
- 关键价格变化：...
- 本轮动作：...
- 简短结论：...
```

日报不必逐条记录用户意见被接受或拒绝。

内部原因保存在阶段输出和 `cycle_summary.json`。


## 十九、错误分类

### 可重试

- Codex超时；
- 网络临时失败；
- Alpaca暂时无响应；
- 报价暂时不可用。

处理：

```text
failed_retriable
resume.allowed = true
```

### 安全阻断

- 市场时段不允许；
- 报价过旧；
- 购买力不足；
- 标的不可交易；
- 用户禁止；
- 价格超范围；
- 重复订单；
- 第三阶段决定不交易。

处理：

```text
blocked
或 completed_no_action
```

进程应正常结束。

### 致命错误

- Schema损坏；
- 状态文件无法解析；
- 账户身份不一致；
- 关键配置缺失；
- paper/live混淆。

处理：

```text
failed_terminal
resume.allowed = false
```


## 二十、测试

单元测试：

```text
tests/v2/
├── test_runtime.py
├── test_state_models.py
├── test_state_machine.py
├── test_cycle_kind.py
├── test_coarse_validation.py
├── test_portfolio_validation.py
├── test_execution_validation.py
├── test_order_builder.py
├── test_order_validator.py
├── test_reconciliation.py
└── test_daily_report.py
```

集成测试：

```text
tests/v2/integration/
├── test_first_daily_run.py
├── test_same_day_execution_refresh.py
├── test_same_day_rebalance.py
├── test_resume_after_codex_timeout.py
├── test_order_rejected_then_reallocate.py
├── test_partial_fill.py
├── test_no_action_success.py
├── test_before_market_open_new_position_block.py
└── test_report_append.py
```

必须覆盖：

1. 首轮完整三阶段；
2. 同日第二轮跳过第一阶段；
3. 第二阶段复用；
4. 订单取消后重新分配；
5. 部分成交；
6. 用户要求买MU但第三阶段拒绝；
7. 用户禁止TSLA；
8. 盘前零持仓开仓被阻止；
9. Codex安全重试一次；
10. Schema通过但业务失败；
11. 过期报价；
12. 重复订单；
13. 无订单正常成功；
14. 首轮详细日报；
15. 后续增量简报；
16. 指定cycle恢复；
17. `--live`拒绝。


## 二十一、开发顺序

### A. 基础设施

1. runtime
2. state models
3. CLI
4. exceptions
5. config
6. state machine
7. 单元测试

### B. 数据层

8. Alpaca客户端
9. account/positions/orders/assets
10. universe
11. daily bars
12. intraday/quotes
13. snapshots
14. 测试

### C. 第一阶段

15. models/schema
16. Prompt与AGENTS
17. workspace与runner
18. validator
19. stage
20. 测试

### D. 第二阶段

21. models/schema
22. Prompt与AGENTS
23. input与validator
24. stage
25. 测试

### E. 人工意见与第三阶段

26. user_review
27. execution models/schema
28. Prompt与AGENTS
29. input与validator
30. stage
31. 测试

### F. 订单

32. order models
33. builder
34. validator
35. paper submitter
36. reconciliation
37. 测试

### G. 报告与接线

38. detailed report
39. incremental report
40. 完整state machine
41. main接线
42. 集成测试
43. paper端到端

### H. 清理

44. 引用扫描
45. requirements与README
46. 删除v2临时文件
47. 用户确认后归档或删除v1。


## 二十二、v1规则与最终清理

v2不得：

```python
from v1.xxx import ...
```

允许阅读v1后重新实现：

- Alpaca连接；
- 账户、持仓、订单查询；
- 日线增量更新；
- Codex调用；
- 原子写入；
- 已验证Schema限制。

v2 paper端到端通过前不要删除v1。

删除前必须：

1. 列出目标；
2. 搜索引用；
3. Git提交或备份；
4. 用户确认。

必须保留：

- `.env`；
- requirements；
- 静态股票池；
- 有效历史行情；
- v2源码、测试、Prompt、Schema和配置；
- paper运行记录与报告。


## 二十三、完成标准

v2完成必须同时满足：

- 不导入v1；
- 日期级与轮次级分离；
- 历史轮次不覆盖；
- 状态机可恢复；
- 自动模式不中途等待；
- 第一阶段恰好60只；
- 同日默认复用第一阶段；
- 第二阶段可重新分配剩余资金；
- 第三阶段可按最新报价微调或拒绝；
- 用户硬限制不可绕过；
- Codex不直接下单；
- Python计算最终数量；
- 提交前重新刷新状态；
- 防重复订单；
- 盘前零持仓开仓被阻止；
- paper结果完整保存；
- 部分成交和拒绝可在下轮处理；
- 首轮详细日报；
- 后续时间戳简报；
- 无订单正常完成；
- 单元、集成和paper端到端测试通过；
- `--live`明确拒绝。


## 二十四、交给Codex的第一条指令

```text
请阅读 docs/WA_Trader_v2_implementation_spec.md，并扫描当前仓库。

先审查已经存在的：
- src/v2/runtime.py
- src/v2/models/state.py
- src/v2/cli.py
- src/v2/main.py

保留符合规格的部分，重写不符合部分。

本次只完成阶段A：
- runtime.py
- models/state.py
- cli.py
- exceptions.py
- config.py
- state_machine.py
- 对应单元测试

要求：
1. 不导入src/v1；
2. 不接入Alpaca；
3. 不调用Codex；
4. 不实现下单；
5. 不修改.env；
6. 默认paper；
7. --live必须拒绝；
8. 所有JSON原子保存；
9. 测试通过后给出文件变更清单、测试命令、测试结果和下一阶段计划。
```
