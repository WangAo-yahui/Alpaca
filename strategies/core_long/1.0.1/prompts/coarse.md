<!--
作用：定义 core_long@1.0.1 的粗选研究任务和输出边界。
重要性：这是固定 release 的策略行为输入，修改后必须更新哈希并发布新策略版本。
-->

# WA Trader v2 coarse selection

Read `data/input.json` and produce exactly 60 unique research candidates that
conform to `schemas/coarse_output.schema.json`. Treat `initial_guidance` as a
research preference, never as a trade mandate. Do not create weights, orders,
quantities, entries, exits, take-profit levels, or stop-loss levels. Use only
the provided objective data for factual claims. Write the final JSON object to
`output/coarse_output.json`.
