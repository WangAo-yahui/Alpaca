# WA Trader v2：Stage C.5 启动建议、多账户与版本控制实施任务

版本：2026-07-23-stage-c5-v1

## 0. 当前基线

Stage C 已完成：

- 基础快照；
- 完整候选池；
- 日线摘要；
- SHA-256 输入签名；
- Codex隔离工作区；
- 严格Schema；
- 一次安全重试；
- 60只候选业务校验；
- 同日复用；
- `--force-full`；
- 失败保留旧有效输出；
- 主流程停在 `RUN_PORTFOLIO`；
- 87项测试通过；
- 无v1导入；
- 无订单提交；
- `.env` 未修改。

本阶段必须保留这些能力。

---

# 1. 实施顺序

不要直接进入Stage D。

先按顺序完成：

```text
冻结Stage C基线
→ 运行一次真实Stage C烟雾测试
→ 创建Git功能分支
→ 实施profile和strategy release
→ 实施启动建议initial guidance
→ 迁移runtime路径
→ coarse支持revision
→ 迁移状态模型
→ 扩展测试
→ 再进入Stage D
```

---

# 2. 冻结Stage C基线

先运行完整测试：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_c_baseline \
PYTHONPATH=src \
.Alpaca/bin/python -m unittest discover \
  -s tests/v2 \
  -p 'test_*.py' \
  -v
```

预期：

```text
Ran 87 tests
OK
```

检查Git：

```bash
git status
git diff --stat
```

提交基线：

```bash
git add \
  src/v2 \
  tests/v2 \
  config/v2 \
  prompts/v2 \
  schemas/v2 \
  docs

git commit -m "Complete WA Trader v2 Stage C coarse selection"
```

创建基线标签：

```bash
git tag stage-c-complete
```

如果仓库尚未初始化Git：

```bash
git init
git add .
git commit -m "Stage C baseline"
git tag stage-c-complete
```

确认 `.env` 已被忽略：

```bash
git check-ignore .env
```

如果没有输出，先把以下加入 `.gitignore`：

```text
.env
.Alpaca/
decision_runtime_v2/
reports/v2/
account_bindings/
__pycache__/
*.pyc
.DS_Store
```

然后再次确认。

---

# 3. Stage C真实烟雾测试

在架构迁移前先运行一次旧Stage C真实烟雾测试，以验证：

- Alpaca paper凭据；
- 基础数据；
- 真实Codex调用；
- Schema；
- 60只输出；
- 本地工作区权限；
- 当前主流程。

命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_c_smoke \
PYTHONPATH=src \
.Alpaca/bin/python -u src/v2/main.py \
  --no-review \
  --allow-trade
```

当前Stage C不会下单。

保存终端结果。

如果真实烟雾失败，先修复Stage C，不要把错误和C.5迁移混在一起。

---

# 4. 创建功能分支

```bash
git switch -c feature/guidance-profiles-versioning
```

如果Git版本较旧：

```bash
git checkout -b feature/guidance-profiles-versioning
```

---

# 5. 最终CLI语义

## 5.1 无人值守

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --unattended \
  --allow-trade
```

`--unattended` 等价于：

```text
--no-guidance
--no-review
```

## 5.2 启动时提供贯穿三个阶段的建议

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --guidance "考虑MU与半导体，但避免过度集中" \
  --no-review \
  --allow-trade
```

## 5.3 两次人工介入

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --allow-trade
```

交互顺序：

```text
程序开头：
输入贯穿三个阶段的建议

第二阶段结束：
输入执行前复查意见
```

---

# 6. 两类意见不可混合

## 6.1 initial guidance

文件：

```text
cycles/<cycle_id>/initial_guidance.json
```

发生在第一阶段前。

影响：

```text
coarse
portfolio
execution
```

示例：

```text
考虑MU
关注半导体
偏向低估值
减少科技集中
```

它是研究偏好或正向请求，不是强制交易。

## 6.2 post-portfolio review

文件：

```text
cycles/<cycle_id>/user_review.json
```

发生在第二阶段之后。

默认只影响：

```text
execution
```

示例：

```text
MU不超过5%
今天不要买TSLA
不要追高
```

用户明确禁止和硬限制必须高于第三阶段。

---

# 7. CLI修改

修改：

```text
src/v2/cli.py
```

`CLIOptions`至少增加：

```python
profile: str

guidance: str | None
no_guidance: bool
no_review: bool
unattended: bool

allow_trade: bool
```

参数：

```python
parser.add_argument(
    "--profile",
    required=True,
)

guidance_group = parser.add_mutually_exclusive_group()

guidance_group.add_argument(
    "--guidance",
)

guidance_group.add_argument(
    "--no-guidance",
    "--no_initial_guidance",
    dest="no_guidance",
    action="store_true",
)

parser.add_argument(
    "--no-review",
    "--no-need-review",
    "--no_need_review",
    dest="no_review",
    action="store_true",
)

parser.add_argument(
    "--unattended",
    action="store_true",
)
```

规则：

- `--guidance` 与 `--no-guidance` 冲突；
- `--guidance` 与 `--unattended` 冲突；
- `--unattended` 设置 `no_guidance=True`、`no_review=True`；
- `--profile`必须存在且enabled；
- `--live`仍然拒绝提交；
- `--allow-trade`只表示允许paper提交。

---

# 8. 启动建议模块

新增：

```text
src/v2/guidance.py
```

职责：

- 解析CLI guidance；
- 交互式询问；
- 处理非TTY；
- 计算SHA-256；
- 原子写入；
- 读取并校验。

建议接口：

```python
@dataclass(frozen=True)
class InitialGuidance:
    schema_version: str
    profile_id: str
    strategy_id: str
    strategy_version: str
    run_date: str
    cycle_id: str
    mode: str
    raw_text: str
    guidance_hash: str
    applies_to: tuple[str, ...]
    created_at: str
    created_at_new_york: str
```

模式：

```text
cli
prompt
reviewed_no_comment
skipped_by_flag
```

非交互运行且未提供明确参数时：

```text
ConfigurationError
```

提示用户使用：

```text
--guidance
--no-guidance
--unattended
```

---

# 9. Profile配置

新增：

```text
src/v2/profiles.py

config/v2/profiles/
├── paper1.json
├── paper2.json
├── paper3.json
└── live.json
```

Profile不得写死数量。

示例：

```json
{
  "schema_version": "1.0",
  "profile_id": "paper2",
  "enabled": true,
  "broker": "alpaca",
  "environment": "paper",
  "credential_key_env": "ALPACA_PAPER2_API_KEY",
  "credential_secret_env": "ALPACA_PAPER2_SECRET_KEY",
  "strategy": {
    "strategy_id": "core_long",
    "strategy_version": "1.0.0"
  },
  "risk_profile": "paper_standard@1.0.0"
}
```

Profile配置只保存环境变量名称，不保存密钥。

为向后兼容，可以提供：

```text
config/v2/profiles/default.json
```

但最终用户命令建议始终显式使用 `--profile`。

---

# 10. 账户绑定

新增：

```text
account_bindings/<profile>.json
```

新增或扩展：

```text
src/v2/profiles.py
src/v2/data/alpaca_client.py
```

首次连接：

1. 获取account id；
2. SHA-256；
3. 保存hash；
4. 后续验证。

结构：

```json
{
  "profile_id": "paper2",
  "environment": "paper",
  "account_id_hash": "...",
  "bound_at": "...",
  "last_verified_at": "..."
}
```

账户hash变化：

```text
failed_terminal
```

不得继续。

提供显式首次绑定流程，避免静默绑定错误账户。

建议参数：

```text
--bind-account
```

首次未绑定且没有该参数时，显示hash并停止。

这样比首次运行自动绑定更安全。

---

# 11. 策略发布

新增：

```text
src/v2/releases.py

strategies/
└── core_long/
    └── 1.0.0/
        ├── manifest.json
        ├── prompts/
        ├── schemas/
        └── config/
```

`manifest.json`：

```json
{
  "strategy_id": "core_long",
  "strategy_version": "1.0.0",
  "compatible_app_version": ">=2.0.0,<3.0.0",
  "description": "Long-horizon US equity strategy",
  "prompt_hashes": {},
  "schema_hashes": {},
  "config_hashes": {}
}
```

发布版本不可原地修改。

任何Prompt、Schema或策略配置变化都创建新版本。

Stage C现有：

```text
prompts/v2/coarse.md
prompts/v2/coarse_AGENTS.md
schemas/v2/coarse_output.schema.json
```

可作为 `core_long/1.0.0` 的初始内容来源。

代码可以暂时支持从原路径构建manifest，但最终运行应解析策略release。

---

# 12. 风险Profile

新增：

```text
config/v2/risk_profiles/
├── paper_standard-1.0.0.json
└── live_conservative-1.0.0.json
```

Profile引用：

```text
paper_standard@1.0.0
```

策略版本与风险版本分离。

同一个策略晋级live时可以使用更保守的风险Profile。

---

# 13. Runtime路径迁移

当前旧路径：

```text
decision_runtime_v2/YYYY-MM-DD/
```

新路径：

```text
decision_runtime_v2/
└── accounts/
    └── <profile>/
        └── strategies/
            └── <strategy_id>/
                └── <strategy_version>/
                    └── YYYY-MM-DD/
```

轮次：

```text
.../<date>/cycles/<cycle_id>/
```

日报：

```text
reports/v2/
└── accounts/
    └── <profile>/
        └── strategies/
            └── <strategy_id>/
                └── <strategy_version>/
                    └── daily/
                        └── YYYY-MM-DD.md
```

修改：

```text
src/v2/runtime.py
src/v2/models/state.py
src/v2/main.py
tests/v2
```

路径构建必须显式要求：

```text
profile_id
strategy_id
strategy_version
```

---

# 14. 共享市场数据

账户无关数据放入：

```text
shared_data/
├── universe/
├── market/
│   ├── daily/
│   ├── intraday/
│   └── quotes/
├── assets/
└── public_research_cache/
```

Stage B当前行情路径应迁移或适配为共享路径。

不得把以下共享：

- account；
- positions；
- orders；
- guidance；
- portfolio；
- execution；
- broker submission；
- reports；
- logs。

---

# 15. Coarse revision

Stage C现有单一coarse输出改为：

```text
coarse/
├── current.json
└── revisions/
    └── <input_signature>/
        ├── input.json
        ├── output.json
        ├── validation.json
        ├── codex_call.json
        └── workspace/
```

coarse签名增加：

```text
profile_id
strategy_id
strategy_version
guidance_hash
```

但不要加入：

- cycle_id；
- 账户现金；
- 日内持仓市值变化；
- 最新报价。

同一profile、同一strategy、同一建议、同一市场输入可以复用。

建议变化时创建新revision，不覆盖旧输出。

---

# 16. Cycle状态版本信息

每个 `cycle_state.json` 增加：

```json
{
  "profile_id": "paper2",
  "release": {
    "app_version": "2.0.0",
    "git_commit": "...",
    "strategy_id": "core_long",
    "strategy_version": "1.0.0",
    "risk_profile": "paper_standard@1.0.0",
    "prompt_hashes": {},
    "schema_hashes": {},
    "config_hashes": {}
  },
  "guidance": {
    "path": ".../initial_guidance.json",
    "guidance_hash": "..."
  }
}
```

Git commit读取失败时允许：

```text
unknown
```

但要记录warning。

---

# 17. Git版本方案

不使用：

```bash
python3 main.py --v2
```

使用：

```text
Git commit/tag
+ profile
+ strategy version
+ risk profile
```

推荐分支：

```text
main
feature/*
release/*
```

当前分支：

```text
feature/guidance-profiles-versioning
```

完成C.5后：

```bash
git add .
git commit -m "Add guidance profiles and strategy releases"
git tag stage-c5-complete
```

然后合并：

```bash
git switch main
git merge --no-ff feature/guidance-profiles-versioning
```

---

# 18. paper策略晋级live

晋级的是策略release，不是paper账户。

流程：

```text
paper2运行 core_long@1.0.0
→ 充分paper验证
→ 固定Git commit
→ 固定Prompt、Schema、配置hash
→ 创建release manifest
→ approved_for_live=true
→ live profile引用同一strategy version
→ live使用独立risk profile
→ 先dry-run
→ 再单独启用live
```

v2当前仍拒绝live提交。

---

# 19. 迁移旧Stage C运行数据

不要自动移动旧runtime。

旧目录：

```text
decision_runtime_v2/YYYY-MM-DD/
```

处理：

- 保留；
- 加入Git忽略；
- 不作为新profile运行状态；
- 必要时人工归档为 `decision_runtime_v2_legacy/`。

新架构从干净路径开始。

不要为了迁移历史测试数据增加长期兼容复杂度。

---

# 20. 测试

保留现有87项测试。

新增：

```text
tests/v2/
├── test_initial_guidance_cli.py
├── test_initial_guidance_prompt.py
├── test_noninteractive_guidance.py
├── test_guidance_signature.py
├── test_profile_loading.py
├── test_profile_paths.py
├── test_account_binding.py
├── test_strategy_release.py
├── test_risk_profile.py
├── test_coarse_revision.py
├── test_cycle_release_metadata.py
└── test_shared_data_paths.py
```

必须覆盖：

1. `--guidance`；
2. `--no-guidance`；
3. `--unattended`；
4. unattended同时跳过两次人工输入；
5. 非TTY无明确参数时报错；
6. guidance hash稳定；
7. guidance变化触发coarse新revision；
8. 相同guidance复用revision；
9. paper1与paper2runtime隔离；
10. 策略版本隔离；
11. market data共享；
12. 订单、持仓和报告不共享；
13. profile不存在失败；
14. profile disabled失败；
15. profile凭据变量名称正确；
16. account hash首次绑定；
17. account hash变化致命失败；
18. release manifest hash校验；
19. 发布目录被修改时检测失败；
20. cycle记录Git与release信息；
21. `--live`仍拒绝；
22. 现有Stage A、B、C测试保持通过。

测试命令：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/alpaca_v2_stage_c5 \
PYTHONPATH=src \
.Alpaca/bin/python -m unittest discover \
  -s tests/v2 \
  -p 'test_*.py' \
  -v
```

---

# 21. C.5完成后的真实命令

首次绑定paper2：

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --bind-account \
  --unattended
```

Stage C烟雾测试：

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --unattended \
  --allow-trade
```

有启动建议：

```bash
python3 -u src/v2/main.py \
  --profile paper2 \
  --guidance "考虑MU和半导体" \
  --no-review \
  --allow-trade
```

Stage C.5完成时仍停在：

```text
RUN_PORTFOLIO
```

仍不提交订单。

---

# 22. 完成标准

C.5只有满足以下条件才完成：

- Stage C真实烟雾已验证或明确记录未执行；
- 现有87项测试保持通过；
- initial guidance在coarse前采集；
- guidance贯穿三个阶段的数据合同；
- 两次人工输入独立；
- profile配置可加载；
- 账户身份hash绑定；
- runtime按profile和strategy隔离；
- shared market data不重复；
- coarse支持revision；
- strategy和risk版本分离；
- cycle记录Git commit与release hash；
- live仍被拒绝；
- 不导入v1；
- `.env` 未修改；
- 不提交订单；
- 新增测试通过。

---

# 23. 交给Codex的指令

```text
Stage C已经完成，87项测试通过。

请阅读：
- docs/WA_Trader_v2_implementation_spec.md
- docs/WA_Trader_v2_stage_b.md
- docs/WA_Trader_v2_stage_c.md
- docs/WA_Trader_v2_stage_c5.md

先不要实现Stage D。

按Stage C.5实施：
1. 先确认Stage C基线测试通过；
2. 增加initial guidance；
3. 支持--guidance、--no-guidance和--unattended；
4. 保留第二阶段后的--no-review；
5. 增加--profile；
6. 实现profile配置与账户hash绑定；
7. 增加strategy release与risk profile；
8. runtime按profile、strategy id和strategy version隔离；
9. market data保持共享；
10. coarse按input signature保存revision；
11. guidance hash进入coarse签名；
12. cycle记录app、Git、strategy、risk和配置hash；
13. 旧runtime不迁移，只保留；
14. v2继续拒绝live提交；
15. 不导入v1；
16. 不修改.env；
17. 不实现第二阶段或订单提交；
18. 保持现有测试通过并增加C.5测试；
19. 完成后报告路径迁移、文件变更、测试命令和结果；
20. 最后给出paper2绑定和Stage C真实烟雾命令。
```
