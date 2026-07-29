# Coarse workspace rules

- Work only inside this workspace.
- Read `data/input.json`, `config/coarse_policy.json`, and the output schema.
- Select exactly 60 unique symbols from the eligible input universe.
- Use the separately ranked `python_shortlists.stock` and
  `python_shortlists.etf` for ordinary selections. When web access succeeds,
  select 3–5 separately tagged `codex_supplement` stocks or ETFs from the
  remaining Python-eligible input universe.
- Independently add 2–5 primary-source-backed stock or ETF
  `external_discoveries`; score the two asset types separately. They remain
  research-only until a later Python input verifies asset identity, liquidity,
  history and tradability.
- Initial guidance is a preference and cannot override exclusions or data quality.
- Preserve high-quality, growth, ETF, valuation and credible contrarian
  opportunity types without imposing category quotas.
- A drawdown is not evidence of undervaluation; never invent fundamental data.
- Do not produce portfolio decisions or executable orders.
- Write only `output/coarse_output.json`.
