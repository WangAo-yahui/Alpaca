# WA Trader v2：Stage C 第一阶段候选池实施任务

版本：2026-07-23-stage-c-v1

## 1. 目标

完成第一阶段候选池粗选，使主流程从 `RUN_COARSE` 推进到 `RUN_PORTFOLIO`。

本阶段只实现：

```text
完整股票/ETF候选池
→ Python基础筛选与输入构建
→ Codex第一阶段粗选
→ Schema校验
→ Python业务校验
→ 原子保存当天60只候选
→ 更新daily_state与cycle_state
→ 主流程停在RUN_PORTFOLIO
```

不得实现：

- 第二阶段组合决策
- 第三阶段执行决策
- 人工意见处理
- 订单生成
- Alpaca提交
- 完整日报

Stage C完成后：

```bash
python3 -u src/v2/main.py --no-review --allow-trade
```

应完成基础快照和第一阶段，然后正常停在：

```text
RUN_PORTFOLIO
```

不得提交订单。

---

## 2. 第一阶段职责

第一阶段只负责从完整候选池中选择恰好60只研究候选。

允许：

- 使用日线数据
- 使用基础统计指标
- 使用公开信息和联网研究
- 判断研究价值
- 判断是否适合进入第二阶段的新仓筛选
- 排除明显不适合当前策略的标的
- 保留当前持仓、挂单和必须覆盖标的

禁止：

- 生成目标权重
- 生成最终持仓
- 生成订单
- 输出最终开仓许可
- 输出最终数量
- 调用Alpaca
- 修改项目源码或配置
- 选择输入候选池之外的标的

---

## 3. 日期级复用

路径：

```text
decision_runtime_v2/YYYY-MM-DD/coarse/
├── input.json
├── output.json
├── validation.json
├── codex_call.json
└── workspace/
```

同一天默认复用第一阶段结果。

只在以下情况重跑：

1. 当天没有合法输出
2. output.json缺失
3. Schema失败
4. 业务校验失败
5. 不是恰好60只
6. 输入签名变化
7. 股票池版本变化
8. 日线输入结构变化
9. must_include或exclusions变化
10. 使用 `--force-full`

账户、持仓、挂单、最新报价的普通变化不能单独触发第一阶段重跑。

---

## 4. 新增或完成的文件

### 模型

```text
src/v2/models/coarse.py
```

至少定义：

- `CoarseInput`
- `CoarseUniverseItem`
- `CoarseOutput`
- `CoarseSelection`
- `CoarseResearchStatus`
- `CoarseValidationResult`

要求：

- `to_dict`
- `from_dict`
- Python内部校验
- 与JSON Schema一致

### 第一阶段

```text
src/v2/stages/coarse.py
```

建议接口：

```python
@dataclass(frozen=True)
class CoarseStageResult:
    action: Literal["run", "reuse"]
    output_path: Path
    validation_path: Path
    selected_symbols: tuple[str, ...]
    input_signature: str
    network_status: str
    warnings: tuple[str, ...]

def run_coarse_stage(
    context: PipelineContext,
) -> CoarseStageResult:
    ...
```

### Prompt

```text
prompts/v2/coarse.md
prompts/v2/coarse_AGENTS.md
```

### Schema

```text
schemas/v2/coarse_output.schema.json
```

### 通用Codex层

扩展或完成：

```text
src/v2/codex/workspace.py
src/v2/codex/runner.py
src/v2/codex/validation.py
```

---

## 5. 第一阶段输入

`coarse/input.json` 至少包含：

```json
{
  "schema_version": "1.0",
  "stage": "coarse_selection",
  "run_date": "2026-07-23",
  "generated_at": "...",
  "input_signature": "...",
  "universe": [],
  "must_include": [],
  "exclusions": [],
  "current_positions": [],
  "open_order_symbols": [],
  "market_context": {},
  "data_quality": {},
  "policy": {}
}
```

每个候选至少包含：

```json
{
  "symbol": "MU",
  "name": "Micron Technology",
  "asset_type": "stock",
  "sector": "Information Technology",
  "industry": "Semiconductors",
  "source": "sp500",
  "must_include": false,
  "currently_held": false,
  "has_open_order": false,
  "asset_status": {
    "tradable": true,
    "fractionable": true,
    "shortable": true
  },
  "daily_summary": {},
  "data_quality": {}
}
```

ETF允许sector或industry为空。

股票池数量不得写死，必须从Stage B的 `universe.py` 和配置实际计算。

---

## 6. Python基础筛选

Codex前允许确定性基础筛选。

建议硬排除：

- asset不是active
- 不可交易
- 日线严重不足
- 数据损坏
- 明确排除标的
- 不支持资产类型
- 重复symbol
- 价格或流动性低于配置阈值
- 长期停牌或无有效行情

必须保留：

- 当前持仓
- 当前挂单
- must_include

如果这些标的数据有问题，应进入输入并附警告，不得静默删除。

记录：

```json
{
  "input_count": 537,
  "eligible_count": 520,
  "excluded_count": 17,
  "exclusions": [
    {
      "symbol": "...",
      "reason_code": "...",
      "reason": "..."
    }
  ]
}
```

示例数字不能写死。

---

## 7. 日线摘要

coarse input不需要塞入每只股票全部300根bar。

每个标的摘要至少包含：

```json
{
  "bars_available": 300,
  "last_bar_date": "2026-07-22",
  "last_close": 0,
  "average_dollar_volume_20d": 0,
  "return_5d": 0,
  "return_20d": 0,
  "return_60d": 0,
  "return_252d": 0,
  "volatility_20d": 0,
  "volatility_60d": 0,
  "drawdown_from_52w_high": 0,
  "distance_from_sma_20": 0,
  "distance_from_sma_50": 0,
  "distance_from_sma_200": 0,
  "rsi_14": 0,
  "volume_ratio_20d": 0
}
```

缺失值使用 `null`，不得用0冒充。

原始日线文件可按需复制进工作区：

```text
coarse/workspace/data/daily/
```

---

## 8. 输入签名

使用SHA-256。

至少包含：

- run_date
- universe版本
- universe symbols
- must_include
- exclusions
- 日线最新日期
- 基础筛选配置版本
- stages配置版本
- Prompt版本
- Schema版本

不要包含：

- 账户现金小变化
- 最新报价
- cycle_id
- 日内持仓市值变化

否则会破坏同日复用。

---

## 9. 工作区权限

第一阶段工作区：

```text
coarse/workspace/
├── AGENTS.md
├── data/
├── config/
├── prompts/
├── schemas/
├── .tmp/codex/
└── output/
```

Codex可读：

```text
AGENTS.md
data/
config/
prompts/
schemas/
```

Codex只允许写：

```text
.tmp/codex/
```

禁止直接写：

- output.json
- validation.json
- daily_state.json
- cycle_state.json
- 源码
- 配置
- `.env`

正式文件由Python捕获、校验和原子保存。

---

## 10. Codex调用器

`runner.py` 要求：

- 指定工作目录
- 指定Prompt
- 严格JSON Schema
- 捕获stdout/stderr
- 记录开始、结束和耗时
- 不记录凭据
- 一次主调用
- 最多一次安全重试
- 不无限重试
- fake runner可测试

建议返回：

```python
@dataclass(frozen=True)
class CodexRunResult:
    success: bool
    raw_output: str
    stdout: str
    stderr: str
    attempts: int
    started_at: str
    completed_at: str
    duration_seconds: float
    retry_reason: str | None
```

---

## 11. Prompt要求

`coarse_AGENTS.md` 必须明确：

- 只执行第一阶段
- 只从输入候选池选择
- 必须恰好60只
- 不决定权重
- 不生成订单
- 不拥有开仓权限
- 不调用Alpaca
- 不读取 `.env`
- 不修改源码、配置、Schema
- 只允许写 `.tmp/codex/`
- 最终只返回一个合法JSON对象

`coarse.md` 要求Codex：

1. 读取coarse input
2. 查看必要日线
3. 可以联网
4. 优先官方公司、SEC、政府、交易所和可靠媒体
5. 不机械追逐近期涨幅
6. 综合质量、估值、趋势、流动性、风险与行业分散
7. 保留must_include
8. 输出恰好60只
9. 不重复symbol
10. 每只给出粗选理由和主要风险
11. 不输出Markdown

行业平衡是软约束，Python不因行业集中自动判定失败，除非配置明确是硬限制。

---

## 12. 输出Schema

建议顶层：

```json
{
  "schema_version": "1.0",
  "stage": "coarse_selection",
  "run_date": "2026-07-23",
  "generated_at": "...",
  "input_signature": "...",
  "status": "success",
  "network_research": {},
  "market_summary": "",
  "selection_count": 60,
  "selections": [],
  "warnings": [],
  "source_references": []
}
```

`status`：

```text
success
success_local_only
```

每个selection至少：

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
  "main_risks": ["..."],
  "key_factors": ["..."],
  "source_references": []
}
```

禁止旧字段：

```text
new_position_allowed
target_weight
order
quantity
```

---

## 13. Schema严格预检

调用Codex前检查：

- 每个object有 `additionalProperties: false`
- properties全部在required
- 不用 `oneOf`
- 不用 `anyOf`
- 不用 `allOf`
- 不依赖 `format`
- 不写复杂条件Schema
- 业务数值限制交给Python

预检失败属于致命配置错误，不调用Codex。

---

## 14. Python业务校验

至少检查：

1. stage正确
2. run_date正确
3. input_signature匹配
4. status合法
5. selection_count为60
6. selections长度为60
7. 去重后60
8. 全部来自eligible universe
9. 包含must_include
10. 不包含exclusions
11. rank为1到60且唯一
12. eligibility字段为布尔值
13. asset_type与输入一致
14. 不含目标权重、数量、订单字段
15. 来源结构合法
16. generated_at合理
17. local_only给警告
18. 每只理由非空
19. 当前持仓或挂单未入选时给提醒，但默认不硬失败

输出：

```text
coarse/validation.json
```

例如：

```json
{
  "valid": true,
  "checked_at": "...",
  "selection_count": 60,
  "unique_selection_count": 60,
  "must_include_count": 0,
  "errors": [],
  "warnings": []
}
```

---

## 15. 原子安装

流程：

```text
捕获Codex JSON
→ 临时文件
→ JSON解析
→ Schema校验
→ 业务校验
→ 原子写output.json
→ 原子写validation.json
→ 更新daily_state
→ 更新cycle_state
```

失败时：

- 不破坏旧有效coarse output
- 没有旧输出时标记可重试或致命失败
- 不进入第二阶段

---

## 16. 状态更新

成功运行：

```text
daily_state.coarse_status = valid
daily_state.coarse_output_path = coarse/output.json
daily_state.coarse_input_signature = 当前签名

cycle_state.stages.coarse.status = completed
cycle_state.current_step = RUN_PORTFOLIO
```

复用：

```text
cycle_state.stages.coarse.status = skipped
cycle_state.stages.coarse.message = reused valid daily coarse output
cycle_state.current_step = RUN_PORTFOLIO
```

可记录：

```text
reused_coarse_cycle_id
```

---

## 17. 主流程接线

Stage C完成后主流程必须：

1. 判断coarse是run还是reuse
2. 执行或复用
3. 更新状态
4. 停在RUN_PORTFOLIO
5. 不调用第二阶段
6. 不提交订单

输出示例：

```text
基础数据刷新成功
第一阶段动作：run
候选数量：60
第一阶段联网：success
第一阶段校验：通过
下一步骤：RUN_PORTFOLIO
当前阶段尚未实现第二阶段，未提交任何订单
```

复用：

```text
第一阶段动作：reuse
候选数量：60
下一步骤：RUN_PORTFOLIO
```

---

## 18. `--allow-trade`

本阶段不提交订单。

即使：

```bash
python3 -u src/v2/main.py --no-review --allow-trade
```

也必须是：

```text
交易提交权限：enabled
第一阶段完成
订单阶段尚未实现
未提交任何订单
```

---

## 19. 测试

新增建议：

```text
tests/v2/
├── test_coarse_models.py
├── test_coarse_input.py
├── test_coarse_signature.py
├── test_coarse_schema.py
├── test_coarse_business_validation.py
├── test_coarse_workspace.py
├── test_codex_runner.py
├── test_coarse_stage.py
└── test_main_stage_c.py
```

测试必须fake Codex，不访问真实网络。

覆盖：

1. 60只通过
2. 59只失败
3. 61只失败
4. 重复symbol失败
5. 输入池外symbol失败
6. must_include缺失失败
7. exclusion进入失败
8. rank重复失败
9. input_signature不匹配失败
10. run_date不匹配失败
11. 旧字段被拒绝
12. target_weight/order/quantity被拒绝
13. success通过
14. success_local_only通过但警告
15. Schema预检失败时不调用Codex
16. 主调用成功
17. 一次安全重试成功
18. 两次失败后标记可重试
19. 原子安装不破坏旧output
20. 同日第二轮复用
21. `--force-full`重跑
22. 输入签名变化重跑
23. 账户现金变化不触发重跑
24. 主流程停在RUN_PORTFOLIO
25. 不提交订单

测试命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_c_pycache PYTHONPATH=src .Alpaca/bin/python -m unittest discover   -s tests/v2   -p 'test_*.py'   -v
```

---

## 20. 完成标准

Stage C完成必须满足：

- coarse input可构建
- 日线摘要可用
- Prompt和AGENTS存在
- Schema严格预检通过
- Codex runner支持一次安全重试
- 输出恰好60只
- 业务校验完整
- 原子安装
- 同日复用
- `--force-full`可重跑
- 状态更新正确
- 主流程推进到RUN_PORTFOLIO
- 不调用第二阶段
- 不提交订单
- 全部测试通过
- 不导入v1
- 不修改 `.env`
- 凭据不进工作区或日志

---

## 21. 给Codex的指令

```text
请阅读：
- docs/WA_Trader_v2_implementation_spec.md
- docs/WA_Trader_v2_stage_b.md
- docs/WA_Trader_v2_stage_c.md

Stage A和Stage B已经完成，现有70个测试通过。

现在只实施Stage C：第一阶段候选池粗选。

要求：
1. 主流程从RUN_COARSE推进到RUN_PORTFOLIO；
2. 构建日期级coarse input、output、validation和workspace；
3. 从实际股票/ETF候选池中严格选择恰好60只；
4. 实现coarse models、Prompt、AGENTS、Schema；
5. 扩展通用Codex workspace、runner和validation；
6. Codex只允许写.tmp/codex；
7. Python负责Schema校验、业务校验和原子保存；
8. 同一纽约日期默认复用合法第一阶段结果；
9. --force-full强制重跑；
10. 输入签名变化触发重跑；
11. 不导入src/v1；
12. 不修改.env；
13. 不实现第二阶段、第三阶段或订单提交；
14. 即使--allow-trade存在，本阶段也不得提交订单；
15. 所有测试使用fake runner，不访问真实Codex或真实网络；
16. 完成后运行全部tests/v2；
17. 报告新增、修改、删除文件，测试命令和结果；
18. 最后可给出真实第一阶段烟雾测试命令，但不要宣称已执行，除非确实运行并提供日志。
```
