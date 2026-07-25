# Stage C Workspace Rules

This workspace is isolated for one coarse-selection call.

Allowed reads:

- `data/input.json`
- `config/coarse_policy.json`
- `prompts/coarse.md`
- `schemas/coarse_output.schema.json`
- this `AGENTS.md`

Allowed writes:

- `.tmp/codex/` only

Forbidden:

- files outside this workspace
- Alpaca or other broker APIs and credentials
- `.env`, source code, Git metadata, other dates or cycles
- editing input, configuration, prompts, schemas, or `output/`
- portfolio weights, quantities, prices, orders, and submissions

Use web research only as supporting context. The symbols and eligibility facts
must come from `data/input.json`. Return exactly one schema-valid JSON object.
