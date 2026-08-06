# live1 operational evidence — 2026-08-06

This private snapshot contains live1 operational logs and the most complete
natural-language daily report for each available trading date.

- Logs: 126 unique files retained from 139 source files.
- Exact-content duplicate logs omitted: 13.
- Complete daily reports: 9; latest report: `2026-08-06.md`.
- Credential scan: 2 values from `.env_live` checked, zero exact matches.

The snapshot intentionally excludes `.env_live`, account bindings, market-data
caches, release/runtime state, locks, and any other broker-writable artifacts.
Financial positions and account-value facts may remain in reports because this
repository is private and those facts are part of the requested daily evidence.
See `manifest.json` for SHA-256 checksums and duplicate-log aliases.
