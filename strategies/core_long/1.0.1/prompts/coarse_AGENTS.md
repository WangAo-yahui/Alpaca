<!--
作用：约束粗选 Codex 工作区允许读取、写入和决策的范围。
重要性：该文件阻止访问凭据、越过阶段边界或生成可执行订单，是代理侧安全边界。
-->

# Coarse workspace rules

- Work only inside this workspace.
- Read `data/input.json`, `config/coarse_policy.json`, and the output schema.
- Select exactly 60 unique symbols from the eligible input universe.
- Initial guidance is a preference and cannot override exclusions or data quality.
- Do not produce portfolio decisions or executable orders.
- Write only `output/coarse_output.json`.
