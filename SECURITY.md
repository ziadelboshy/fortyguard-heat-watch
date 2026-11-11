# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on this repository. Don't open a public issue for security problems. You can expect a first reply within a week.

This repository is an example integration. Vulnerabilities in FortyGuard's services or in n8n itself should go to those vendors.

## How the project handles secrets and risk

| Area | Measure |
|---|---|
| Secrets in git | None. Keys live in the environment or a git-ignored `.env`, and `.env.example` holds names only. `scripts/export_workflows.py` puts the chat-ID placeholder back and strips pinned data and metadata when you export from n8n. CI rejects workflow files that contain a real chat ID, pinned data, credential data or token-like strings (`scripts/check_workflows.py`, with tests). |
| Secrets in transit to n8n | `scripts/setup_n8n.py` streams credentials over `docker exec` stdin into a `mktemp` file (mode 0600, random name) inside the container. It imports them with the n8n CLI, which encrypts them at rest, and deletes the file on exit, even on failure. Secrets are never printed. |
| Secrets inside n8n | Stored only in n8n's encrypted credential store. The encryption key lives in the same Docker volume, so protect the host and remove the volume when you're done. Workflows reference credentials by ID. `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` stops workflow code from reading environment variables. |
| Network exposure | n8n binds to `127.0.0.1:5678` only. Don't publish the port or put n8n on the internet without authentication and TLS. |
| File access | `N8N_RESTRICT_FILE_ACCESS_TO=/files`: file nodes can only reach the mounted `data/` folder. |
| Container | Image pinned by version **and** digest. `no-new-privileges`, all Linux capabilities dropped, runs as the image's non-root `node` user, healthcheck. Telemetry and version pings are off. |
| Untrusted text | Every value placed in a Telegram HTML message is escaped. CSV fields are quoted and protected against formula injection. IDs placed in URLs are URL-encoded. |
| Failure handling | Any failed run triggers a Telegram notice, so a broken integration doesn't look like a quiet day. |
| Supply chain | No runtime dependencies (Python and Node standard libraries only). GitHub Actions are pinned to commit SHAs with least-privilege `contents: read`. Dependabot proposes Action and image updates. |

## Operator checklist

- Keep `.env` private (`chmod 600 .env`) and rotate the FortyGuard key and bot token if they leak.
- Use a bot dedicated to these alerts. The workflow only sends messages; it never reads or acts on messages sent to the bot.
- Delete FortyGuard monitors you no longer need. They keep running, and some operations cost credits.
- Run `bash scripts/cleanup.sh` when you're done, to remove the container, its data volume (credentials included) and the image.
