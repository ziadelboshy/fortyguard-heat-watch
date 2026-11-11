# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-11

### Added

- n8n workflow that polls FortyGuard heat monitors hourly over the MCP (free `list_monitors` and `list_alerts`) and sends new forecast alerts to Telegram with an action line.
- Optional PDF alert report as a Telegram document: only when the report is ready, gated by `attach_report`, and capped per run. A failed download posts a "report unavailable" message instead of failing the run.
- Dedupe state, a first-run guard (existing alerts are never replayed), past-alert skipping, and a CSV audit log protected against formula injection.
- Error workflow that sends a Telegram notice with a link to the failed execution. Misconfiguration, such as invalid Settings or no monitor matching the prefix, fails loudly.
- Code-node JavaScript kept as source in `n8n/src/`, built into the workflows by `scripts/build_workflows.py`, and covered by unit and workflow-level tests.
- Setup script that imports credentials through a private temp file and can publish both workflows (`--publish`). Export script that brings editor changes back into the repo with secrets stripped. Start and cleanup scripts.
- Hardened, localhost-only n8n container pinned by digest.
- Heat-safety planner (Python standard library) with 17 tests.
- 27 Node tests (library and generated Code nodes) and 7 script tests (setup, export, secret checks).
- CI (tests, build drift, workflow and secret checks, ruff, shellcheck, compose validation) and Dependabot.

[1.0.0]: https://github.com/ziadelboshy/fortyguard-heat-watch/releases/tag/v1.0.0
