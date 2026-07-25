# Coarse workspace rules

- Work only inside this workspace.
- Read `data/input.json`, `config/coarse_policy.json`, and the output schema.
- Select exactly 60 unique symbols from the eligible input universe.
- Initial guidance is a preference and cannot override exclusions or data quality.
- Do not produce portfolio decisions or executable orders.
- Write only `output/coarse_output.json`.
