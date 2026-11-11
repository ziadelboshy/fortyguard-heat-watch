"""Load the FortyGuard and Telegram credentials and both workflows into the local n8n container.

Secrets come from environment variables or a dotenv file (see .env.example). Each payload is
streamed into a private, randomly named temp file inside the container (`mktemp`, mode 0600),
imported with the n8n CLI (n8n encrypts credentials at rest), and deleted even if the import
fails. Secrets are never written to this repository or printed.

  FORTYGUARD_API_KEY   FortyGuard API key (Bearer token for the MCP, `api-key` header for REST)
  TELEGRAM_BOT_TOKEN   bot token from @BotFather
  TELEGRAM_CHAT_ID     chat that receives alerts: a numeric user/group ID or a @channel name

Usage:
  python3 scripts/setup_n8n.py --env-file .env             # import credentials + workflows
  python3 scripts/setup_n8n.py --env-file .env --publish   # ...and switch the hourly run on
  python3 scripts/setup_n8n.py --chat-id-var MY_CHAT_ID    # chat ID under another variable name
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "heatwatch-n8n"
CHAT_ID_PLACEHOLDER = "YOUR_TELEGRAM_CHAT_ID"
# The error workflow is imported first so the main workflow's errorWorkflow reference resolves.
WORKFLOWS = [
    ("HeatWatchError01", ROOT / "n8n" / "workflows" / "heat-watch-errors.json"),
    ("HeatWatchMain001", ROOT / "n8n" / "workflows" / "heat-watch.json"),
]
CHAT_ID_PATTERN = re.compile(r"-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")
ENV_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


@dataclass(frozen=True)
class Secrets:
    fortyguard_key: str
    telegram_token: str
    chat_id: str


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal dotenv reader.

    KEY=value lines with optional `export`; quotes stripped; full-line and inline `# comments`
    ignored (outside quotes). Empty values are skipped so they can't mask real environment values.
    """
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = ENV_LINE.fullmatch(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if value:
            values[match.group(1)] = value
    return values


def load_secrets(env: dict[str, str], chat_id_var: str) -> Secrets:
    names = {"FORTYGUARD_API_KEY": "fortyguard_key", "TELEGRAM_BOT_TOKEN": "telegram_token", chat_id_var: "chat_id"}
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise SystemExit(f"Missing: {', '.join(missing)} (set them in the environment or pass --env-file)")
    secrets = Secrets(**{field: env[name].strip() for name, field in names.items()})
    if not CHAT_ID_PATTERN.fullmatch(secrets.chat_id):
        raise SystemExit(f"{chat_id_var} must be a numeric chat ID or a @channel name")
    return secrets


def credentials(secrets: Secrets) -> list[dict[str, Any]]:
    """n8n credential records; the ids match the references in the workflow files."""
    return [
        {
            "id": "fgMcpCred0000001",
            "name": "FortyGuard MCP (Bearer)",
            "type": "httpHeaderAuth",
            "data": {"name": "Authorization", "value": f"Bearer {secrets.fortyguard_key}"},
        },
        {
            "id": "fgRestCred000001",
            "name": "FortyGuard REST (api-key)",
            "type": "httpHeaderAuth",
            "data": {"name": "api-key", "value": secrets.fortyguard_key},
        },
        {
            "id": "tgBotCred0000001",
            "name": "Telegram bot",
            "type": "telegramApi",
            "data": {"accessToken": secrets.telegram_token},
        },
    ]


def fill_placeholders(node: Any, chat_id: str) -> Any:
    """Replace the chat-ID placeholder in parsed workflow JSON values (never in raw text)."""
    if isinstance(node, dict):
        return {key: fill_placeholders(value, chat_id) for key, value in node.items()}
    if isinstance(node, list):
        return [fill_placeholders(value, chat_id) for value in node]
    return chat_id if node == CHAT_ID_PLACEHOLDER else node


def docker(*args: str, payload: str | None = None, check: bool = True) -> tuple[int, str]:
    """Run a docker CLI command (argument list, no shell); returns (exit code, combined output)."""
    proc = subprocess.run(
        ["docker", *args],
        input=payload.encode("utf-8") if payload is not None else None,
        capture_output=True,
        check=False,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
    if check and proc.returncode != 0:
        raise SystemExit(f"docker {args[0]} failed:\n{output[-2000:]}")
    return proc.returncode, output


def ensure_container_running() -> None:
    if not shutil.which("docker"):
        raise SystemExit("docker not found on PATH. Install Docker, then run: bash scripts/start.sh")
    code, state = docker("inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
    if code != 0 or state != "true":
        raise SystemExit(f"Container {CONTAINER} is not running. Start it with: bash scripts/start.sh")


def wait_until_healthy(timeout_s: int = 120) -> None:
    """Wait for the compose healthcheck after a restart."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _, status = docker("inspect", "-f", "{{.State.Health.Status}}", CONTAINER, check=False)
        if status == "healthy":
            return
        time.sleep(2)
    raise SystemExit(f"{CONTAINER} did not become healthy within {timeout_s} s; check: docker logs {CONTAINER}")


def import_into_n8n(payload: str, n8n_command: str) -> None:
    """Stream payload into a private temp file in the container, import it, always delete it."""
    script = f'f="$(mktemp)" && trap \'rm -f "$f"\' EXIT && cat > "$f" && n8n {n8n_command} --input="$f"'
    _, output = docker("exec", "-i", CONTAINER, "sh", "-c", script, payload=payload)
    print(output.splitlines()[-1] if output else f"n8n {n8n_command}: ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--env-file", type=Path, help="dotenv file with the three values (see .env.example)")
    parser.add_argument("--chat-id-var", default="TELEGRAM_CHAT_ID", help="variable that holds the chat ID")
    parser.add_argument("--publish", action="store_true", help="publish both workflows and restart n8n")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env.update(read_env_file(args.env_file))
    secrets = load_secrets(env, args.chat_id_var)
    ensure_container_running()

    import_into_n8n(json.dumps(credentials(secrets)), "import:credentials")
    for _, path in WORKFLOWS:
        workflow = fill_placeholders(json.loads(path.read_text(encoding="utf-8")), secrets.chat_id)
        import_into_n8n(json.dumps(workflow), "import:workflow")
    print("Imported 3 credentials and 2 workflows.")

    if args.publish:
        for workflow_id, _ in WORKFLOWS:
            docker("exec", CONTAINER, "n8n", "publish:workflow", f"--id={workflow_id}")
        docker("restart", CONTAINER)
        wait_until_healthy()
        print("Published both workflows; the hourly run is on. Open http://localhost:5678 to watch it.")
    else:
        print("Not published yet: test with a manual run, then re-run with --publish (see README).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
