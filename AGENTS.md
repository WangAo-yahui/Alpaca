# WA Trader Live monitoring rules

- Scope scheduled monitoring and repair to `live1`. Do not run, deploy, or
  modify `paper1` unless the user explicitly expands scope.
- Start every monitor pass with `./wa status --live --json`,
  `./wa health --live --json`, the latest redacted Live log, scheduler state,
  run-lock owner, latest cycle state, submission journal, broker submission,
  and reconciliation.
- Treat exit code 10 as an active run and 20 as normal no-action. Retry code 40
  at most within the persisted slot limit. Never blindly retry code 60.
- When submission is uncertain, stop new trading and reconcile by stable
  `client_order_id`; use maintenance-only until every write has a broker result.
- Transient network, Alpaca, VPN, or Codex failures are not evidence of a code
  defect. Edit code only for a reproducible software error.
- Before an automated repair, record `git status` and preserve all existing
  changes. Never reset, clean, overwrite, or discard user work.
- A repair must not expose credentials, relax risk limits, bypass account or
  session gates, enable shorting, add leverage, or add a broker write API.
- Validate repairs with focused tests, the complete `tests/v2` suite,
  compileall, `./wa doctor --live`, and the Stage H static broker-write scan.
- Deployment requires a clean local commit on the current `codex/` branch.
  Do not push automatically. Roll back if post-deploy health is worse.
- Report facts, planned actions, submitted orders, fills, and unresolved states
  separately. Write same-day monitor evidence before claiming a repair or trade
  succeeded.

