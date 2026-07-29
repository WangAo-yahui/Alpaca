# WA Trader v2 Stage D — long-horizon capital allocation

Read `data/portfolio_input.json` and obey `AGENTS.md`, the risk configuration,
portfolio policy and output schema.

## Objective and freedom

Maximize expected real terminal wealth after costs over 10–20 years. The user
may add unpredictable amounts at unpredictable times; there is no monthly
contribution amount, cadence or commitment. Never schedule a mandatory
purchase, assume a future deposit or count money before it is actually
available in the account.

No allocation style is mandatory. The model may choose 100% cash, fully
invested, balanced, concentrated, broadly diversified, or move between those
states according to the opportunity comparison. Moderate drawdown is acceptable
when compensated by the long-run thesis; larger drawdown tolerance requires
stronger evidence about survival and permanent-loss risk. Defensive assets, a
core/satellite structure and an opportunistic sleeve are optional tools, not
quotas. Volatility, drawdown or excitement alone is not an edge.

## Required analysis

1. Reconcile account capital, existing positions and unfinished orders. Do not
   reuse cash reserved by open buys. If an existing USDT position or equivalent
   broker-recognized crypto cash balance is detected, make conversion to USD the
   first capital-preparation priority and do not treat proceeds as equity buying
   power until broker reconciliation confirms the sale.
2. Evaluate the whole portfolio, not each symbol independently: overlap,
   concentration, common risk drivers, liquidity, opportunity cost and expected
   contribution flows.
   Build `capital_competition` on one common basis for current holdings,
   at least two non-held coarse candidates and cash. Holding status is not
   quality, valuation or expected-return evidence.
3. Research the strongest candidates deeply. Prefer primary sources and current
   company filings. Distinguish verified facts, explicit assumptions and model
   judgement.
4. For every selected or held symbol, compare current market price with an
   explicit intrinsic-value or justified-value range. Use a method appropriate
   to the asset: normalized owner earnings/FCF, earnings-power and multiple,
   scenario DCF, sum-of-parts, replacement value, or a transparent ETF/factor
   return framework. `latest_quote.reference_price` is a time-stamped strategic
   reference only; respect its `status` and `observed_at`, never describe it as
   live, and require the execution stage to revalidate price before any order.
5. A precise estimate is not compulsory. If evidence is insufficient, set
   valuation status to `no_reliable_estimate`, use null numeric estimates, lower
   confidence and avoid pretending that price equals value.
6. State bear/base/bull annualized return expectations as ranges, including the
   assumptions most likely to make them wrong. Do not promise returns.
7. Judge high-risk and bottom-fishing candidates using survival first:
   refinancing, balance sheet, cash burn, dilution, governance, unit economics,
   demand durability and a plausible path to normalized cash generation. A
   large decline is not a thesis.
8. Rank uses of capital by forward expected return adjusted for permanent-loss
   risk. Cash is a valid position when all available opportunities have weak or
   negative edge.
9. Choose target cash anywhere from 0% to 100%, invested weight, symbols and
   strategic weights freely from the capital comparison. Concentration requires unusually strong evidence and a clear
   disconfirming thesis; diversification must not be used to conceal weak ideas.
   For every current holding, provide a counterfactual decision: whether it
   would still be bought if not already held, its best non-held alternative,
   and the evidence-based reason for any holding hysteresis. An `increase`
   requires `would_buy_if_not_held=true`.
10. Give every decision an accumulation plan. It may be immediate, staged,
    valuation-triggered, future-settled-cash-priority, wait, or no-add. Planned
    tranche fractions must sum to at most 1 and must stop after thesis break.
11. Prefer low turnover. Rebalance when expected-return ranking, valuation,
    thesis, concentration or settled capital changes materially—not merely
    because a small weight drift occurred.
12. For open orders, return only keep/review/cancel/replace strategic assessment.
    Do not create or modify an actual broker order.
13. `valid_until` must use the policy validity window.
14. Candidates whose `source` is `watchlist_non_sp500` are speculative
    emerging-growth research candidates, not purchase mandates. Zero allocation
    is always acceptable. Before allocating, use current primary evidence to
    test durable revenue or adoption, addressable market, balance-sheet runway,
    unit economics or the path to cash generation, governance and dilution,
    valuation, and explicit thesis-break conditions. New positions must be
    staged, each initial target must not exceed 3%, and their combined target
    weight must not exceed 10%.
15. `coarse.output.external_discoveries` are research-only leads. They may be
    carried into `watchlist`, but they are not same-day allocation candidates
    and must never appear in `decisions` until Python later promotes them into
    the eligible coarse universe.

Network failure requires `success_local_only` and a non-empty warning. In that
case, do not manufacture valuation inputs; choose `no_reliable_estimate` where
the supplied data cannot support the estimate.

Output only the strict JSON object required by the schema.
