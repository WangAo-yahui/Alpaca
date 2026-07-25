# WA Trader v2 coarse selection

Read `data/input.json` and produce exactly 60 unique research candidates that
conform to `schemas/coarse_output.schema.json`. Treat `initial_guidance` as a
research preference, never as a trade mandate. Do not create weights, orders,
quantities, entries, exits, take-profit levels, or stop-loss levels. Use only
the provided objective data for factual claims. Write the final JSON object to
`output/coarse_output.json`.
