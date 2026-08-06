# WA Trader v2 coarse selection — long-horizon opportunity set

Read `data/input.json` and produce exactly 20 unique deep-research candidates plus a
separately tagged external discovery list that conforms to
`schemas/coarse_output.schema.json`.

Write `market_summary` in Chinese. Keep schema enums, symbol names, source IDs
and URLs unchanged; other structured detail may remain in the language best
suited to precise source attribution.

The downstream objective is to maximize expected real terminal wealth after
costs over a 10–20 year horizon. This stage does not choose a portfolio, but its
candidate set must preserve genuinely different ways to earn long-run returns:
durable compounders, profitable growth, broad or factor ETFs, temporarily
mispriced cyclicals, credible turnarounds and selected high-upside situations.
These are opportunity types, not quotas. Do not add a weak candidate merely to
fill a category.

You MUST attempt live web research before finalizing the result. When the web
tool is available, use it to inspect current primary sources for both stocks
and ETFs; `network_research.status="not_requested"` is not an acceptable
local-only outcome. Use `success_local_only` only after the live research tool
is actually unavailable or returns an error, record that concrete limitation,
and never fabricate a successful lookup.

For every candidate:

1. Separate a falling price from genuine undervaluation. A drawdown alone is
   never evidence of value.
2. Consider balance-sheet survival, dilution risk, durable earnings power,
   competitive advantage, reinvestment runway, valuation risk, liquidity and
   the probability of permanent capital loss.
3. Treat extreme recent momentum, volatility or drawdown as a research signal,
   not a buy or sell command.
4. Keep currently held positions in the research set when input policy requires
   them, but do not rank them highly merely because they are already owned.
5. Use current web research when available, favoring company filings, SEC,
   government, exchange and other primary sources. Do not invent a fundamental,
   valuation, event or source when it cannot be verified.
6. When web research is unavailable, use `success_local_only`, state the
   limitation, and keep all factual claims within the supplied objective input.
7. Explicitly cite the candidate's `research_features.research_priority_score`,
   material `factor_contributions`, `data_as_of`, confidence and missing fields
   in the reasoning. These are transparent research-priority inputs, not an
   investment conclusion. Do not reuse a generic liquidity-only explanation.

## Python shortlist and Codex supplements

Python first narrows the configured universe to at most 120 research-input
symbols: separate `python_shortlists.stock` and `python_shortlists.etf` plus a
small eligible supplement pool. Stocks and ETFs are never
scored against each other: stock priority emphasizes company research
readiness and observable dislocation signals, while ETF priority emphasizes
mandate clarity, history, liquidity, volatility, concentration and
implementation quality. Neither list is an intrinsic-value ranking and either
may miss exceptional, contrarian, emerging, satellite or diversifying ideas.

- When web research succeeds, select 3–5 of the 20 candidates from the eligible
  input universe but outside the corresponding stock or ETF Python shortlist.
  Mark them
  `selection_origin="codex_supplement"`. Use current primary-source evidence to
  explain why the Python research-priority ranking may have missed them.
- Mark every other selection `selection_origin="python_shortlist"` and select it
  from the corresponding asset-type shortlist.
- A Codex supplement remains subject to the same Python eligibility flags. It
  cannot revive an excluded, untradable or otherwise ineligible input symbol.
- When web research is unavailable, use only `python_shortlist`; do not claim
  that an internet-discovered supplement was verified.

## External web discoveries

When web research succeeds, independently discover 2–5 listed U.S. stocks or
ETFs that are not already among the 20 selections and may deserve future
research. Score stocks and ETFs under separate frameworks. Stocks may be
satellite, emerging compounder, contrarian, turnaround or special situations;
ETFs may be broad/factor, thematic or diversifying opportunities.
Write them to `external_discoveries` with explicit primary web sources, the
reason Python may miss them, key risks and next validation steps.

External discoveries are research leads outside the verified input universe.
Set `research_only=true`. To become a portfolio candidate they must first enter
a later Python input with verified asset identity, tradability, liquidity and
history; this preserves the existing eligibility boundary without adding a new
portfolio-risk rule.

Treat `initial_guidance` as a research preference, never as a trade mandate.
Do not create weights, orders, quantities, entry tranches, exits, take-profit
levels or stop-loss levels. Do not emit interim placeholder candidates or
continue researching after the final object is complete. Return the strict
final JSON object immediately and stop.
