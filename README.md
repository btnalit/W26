# W26

WC26 handicap analysis system core snapshot.

This repository intentionally contains only source code, profile guidance, deterministic scripts, tests, and durable design notes. It excludes live secrets, API keys, auth files, Telegram sessions, direct request logs, odds snapshots, generated reports, cron output, runtime caches, and profile backups.

## Layout

- `profile/skills/odds-analysis/` - WC26 odds-analysis skill, contracts, summaries, Deep Research guidance, and deterministic pipeline scripts.
- `profile/plugins/wc26-direct-summary-enforcer/` - Telegram output safety/enforcer plugin.
- `profile/scripts/` - WC26 cron wrappers and deterministic cron payload.
- `profile/tests/` - regression tests for contracts, summaries, enforcer, recovery, market profile, role engine, and watcher.
- `profile/MEMORY.md`, `profile/USER.md`, `profile/SOUL.md` - worker policy and operating discipline.
- `workspace/scripts/` - standalone WC26 analysis/model/data helper scripts used by the workspace.
- `workspace/proposals/` - durable calibration/proposal notes.

## Safety Boundary

No automatic betting. Actionable signals are review candidates only. Deep Research is a post-report interpretation layer and must not rewrite artifact-backed probabilities, EV, Kelly, or `relay_actionable`.
