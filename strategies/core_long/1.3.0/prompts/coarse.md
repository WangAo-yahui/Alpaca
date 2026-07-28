# WA Trader v2 coarse selection — long-horizon opportunity set

Read `data/input.json` and produce exactly 60 unique research candidates that
conform to `schemas/coarse_output.schema.json`.

The downstream objective is to maximize expected real terminal wealth after
costs over a 10–20 year horizon. This stage does not choose a portfolio, but its
candidate set must preserve genuinely different ways to earn long-run returns:
durable compounders, profitable growth, broad or factor ETFs, temporarily
mispriced cyclicals, credible turnarounds and selected high-upside situations.
These are opportunity types, not quotas. Do not add a weak candidate merely to
fill a category.

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

Treat `initial_guidance` as a research preference, never as a trade mandate.
Do not create weights, orders, quantities, entry tranches, exits, take-profit
levels or stop-loss levels. Write the final JSON object to
`output/coarse_output.json`.
