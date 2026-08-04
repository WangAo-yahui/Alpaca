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
   the first three symbols by coarse `rank` after excluding current security
   positions, and cash. Do not substitute a lower-ranked subjective alternative
   for any of those three required comparators; additional alternatives may be
   appended after them. For the `CASH` comparator, use
   `comparator_type="cash"` and `current_position=false`: that boolean describes
   a security position only, not whether the account has a cash balance.
   Holding status is not
   quality, valuation or expected-return evidence.
3. Research the strongest candidates deeply. Prefer primary sources and current
   company filings. Distinguish verified facts, explicit assumptions and model
   judgement. Missing fields in the supplied Python screen are a research task,
   not evidence that network access is unavailable. When live web search is
   available, actually use it before finalizing the decision. At minimum,
   research every current holding and the three required highest-ranked
   non-held comparators; grouped searches are acceptable, but each symbol must
   have current, symbol-specific evidence or an explicit record of what could
   not be verified. Prefer current filings, issuer fund pages and index-provider
   material, and attach every material claim to `source_references`.
4. For every selected or held symbol, compare current market price with an
   explicit intrinsic-value or justified-value range. Use a method appropriate
   to the asset: normalized owner earnings/FCF, earnings-power and multiple,
   scenario DCF, sum-of-parts, replacement value, or a transparent ETF/factor
   return framework. `latest_quote.reference_price` is a time-stamped strategic
   reference only; respect its `status` and `observed_at`, never describe it as
   live, and require the execution stage to revalidate price before any order.
   Daily data older than two trading days should normally prevent an `open` or
   `increase` recommendation until refreshed. Missing stock sector,
   fundamentals or valuation evidence should normally lead to `watch` or
   `avoid`; depart only with explicit current evidence and a clearly stated
   reason. These are model judgement requirements, not hidden Python scores.
5. A precise estimate is not compulsory, but `no_reliable_estimate` is a last
   resort rather than a shortcut. For every held or allocation-eligible symbol,
   attempt at least two asset-appropriate methods, record each in
   `valuation.attempted_methods`, and capture at least two current calculation
   inputs with dates and source IDs. Examples are normalized owner earnings plus
   a scenario DCF for stocks, or earnings yield plus a transparent factor/holdings
   return framework for ETFs. When an estimate is possible, provide its range,
   evidence date and source references; uncertainty belongs in the range and
   caveats, not automatically in `insufficient`. Only after two methods fail may
   status be `no_reliable_estimate`; then use null estimate numbers,
   `evidence_quality="insufficient"`, and a specific `no_estimate_reason` naming
   the missing facts. Never invent inputs or pretend that price equals value.
6. State bear/base/bull annualized return expectations as ranges, including the
   calculation basis, confidence reason and assumptions most likely to make them
   wrong. Current, traceable inputs can support medium confidence even when the
   range is wide. Do not promise returns.
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
   For each `hold` or `increase`, explain why it beats cash and the three
   strongest non-held candidates, whether it would be bought at the same
   weight from zero, what could replace it, and whether the expected
   improvement plausibly exceeds trading and tax friction. Quantify estimated
   switching cost. If a holding would not be bought today, name unresolved
   evidence, set a review date no more than 30 days away, and commit to reduce or
   exit if it remains unresolved; a narrative preference may not preserve it
   indefinitely. Use confidence-aware judgement; do not apply a permanently
   fixed percentage threshold.
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
16. For every held or positively weighted ETF, perform a look-through review
    using current issuer or other primary data when available: top holdings,
    sector/factor concentration, overlap with other ETFs, and direct plus
    indirect single-company exposure. Fill `etf_lookthrough`. If exact weights
    cannot be verified, mark the review partial or unavailable and state the
   limitation; never manufacture precision.
17. If target cash is at least 25%, fill `cash_management` with the expected
    cash return range, explicit opportunity cost, a review date no more than 14
    days away and at least two measurable deployment triggers. Cash may be 0%;
    neither holding cash nor investing it is the default.
18. Give every security decision a `monitoring_plan` with a review date no more
    than 30 days away and at least two measurable valuation, fundamental,
    concentration, event or time triggers. These triggers drive future review;
    do not spend another model call merely to repeat an unchanged narrative.

Network failure requires `success_local_only` and a non-empty warning, but only
after an actual web-search attempt fails technically. Do not use
`success_local_only` merely because local input fields are missing, research is
time-consuming, or a conservative conclusion is convenient. In a genuine
network failure, do not manufacture valuation inputs; choose
`no_reliable_estimate` where the supplied data cannot support the estimate.

Output only the strict JSON object required by the schema.
