# WA Trader v2 Stage C: Coarse Selection

You are performing coarse research selection only.

Read only the files explicitly allowed by `AGENTS.md`. The authoritative input
is `data/input.json`; the output contract is
`schemas/coarse_output.schema.json`.

Requirements:

1. Select exactly 60 unique symbols from `universe`.
2. Include every symbol in `must_include`.
3. Preserve each selected symbol's `asset_type`,
   `research_eligible`, and `screen_new_position_eligible` values exactly as
   given in the input. Use the input sector and industry when present.
4. Rank selections with every integer from 1 through 60 exactly once.
5. Explain research relevance in `selection_reason`, material risks in
   `main_risks`, and key factors. This is not a portfolio allocation or
   execution decision.
6. Web research is allowed. Record concise source metadata and reference source
   IDs from selections. If web access is unavailable, set status to
   `success_local_only`, describe that limitation in both network research and
   warnings, and use input/local references only.
7. Do not produce target weights, new-position permission, quantities, prices,
   orders, order instructions, or broker submission advice.
8. Do not access Alpaca credentials, `.env`, source code, unrelated
   configuration, or other runtime dates/cycles. Do not modify project files.
9. Return only one JSON object that conforms exactly to the output schema. Do
   not use Markdown fences or add commentary outside the JSON.

Research approach:

- Prefer official company disclosures, SEC and government material, exchange
  information, and reliable reporting.
- Do not mechanically chase recent price performance.
- Consider quality, valuation, trend, liquidity, risk, and soft sector
  diversification. Sector balance is not a hard rejection rule here.

The result is a research candidate pool. A later stage, not this one, will make
portfolio and execution decisions.
