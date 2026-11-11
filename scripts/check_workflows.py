"""Static checks for the n8n workflow files (no n8n or network needed).

  python3 scripts/check_workflows.py

Per workflow file: valid JSON; unique node names and ids; every connection and every $('Node')
reference points to an existing node; no pinned data; credentials are references only; every
Telegram chat ID is the placeholder or an expression; no token-like strings anywhere in the file.
Across files: the main workflow's errorWorkflow is the error workflow, and the workflow and
credential ids match what scripts/setup_n8n.py imports. Exits 1 if any check fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_n8n import CHAT_ID_PLACEHOLDER, WORKFLOWS, Secrets, credentials

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    "long secret-like string": re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
}
NODE_REF = re.compile(r"\$\('([^']+)'\)")
KNOWN_CREDENTIAL_IDS = {c["id"] for c in credentials(Secrets("key", "token", "1"))}


def strings(node):
    """Every string value anywhere in a JSON document."""
    if isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


def check_workflow(workflow: dict) -> list[str]:
    problems: list[str] = []
    names = [n["name"] for n in workflow["nodes"]]
    known = set(names)
    if len(known) != len(names):
        problems.append("duplicate node names")
    if len({n["id"] for n in workflow["nodes"]}) != len(names):
        problems.append("duplicate node ids")
    if workflow.get("pinData"):
        problems.append("pinData is not empty (pinned run data can contain account data)")
    if workflow.get("active"):
        problems.append('"active" must be false in the repository copy')

    for src, outputs in workflow.get("connections", {}).items():
        if src not in known:
            problems.append(f"connection from unknown node {src!r}")
        for branch in outputs.get("main", []):
            for link in branch or []:
                if link["node"] not in known:
                    problems.append(f"{src!r} connects to unknown node {link['node']!r}")

    for node in workflow["nodes"]:
        params = node.get("parameters", {})
        for text in strings(params):
            for ref in NODE_REF.findall(text):
                if ref not in known:
                    problems.append(f"node {node['name']!r} references unknown node $('{ref}')")
        for cred in (node.get("credentials") or {}).values():
            if set(cred) - {"id", "name"}:
                problems.append(f"node {node['name']!r} embeds credential data")
            elif cred.get("id") not in KNOWN_CREDENTIAL_IDS:
                problems.append(
                    f"node {node['name']!r} uses credential id {cred.get('id')!r} that setup_n8n.py doesn't create"
                )
        chat = params.get("chatId")
        if chat is not None and chat != CHAT_ID_PLACEHOLDER and not str(chat).startswith("="):
            problems.append(f"node {node['name']!r} has a hard-coded chat ID")
        for item in params.get("assignments", {}).get("assignments", []):
            if item.get("name") == "telegram_chat_id" and item.get("value") != CHAT_ID_PLACEHOLDER:
                problems.append(f"node {node['name']!r}: telegram_chat_id must be {CHAT_ID_PLACEHOLDER!r}")

    for text in strings(workflow):
        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"contains a {label}")
    return problems


def main() -> int:
    failed = False
    loaded = {}
    for workflow_id, path in WORKFLOWS:
        workflow = json.loads(path.read_text(encoding="utf-8"))
        loaded[workflow_id] = workflow
        problems = check_workflow(workflow)
        if workflow.get("id") != workflow_id:
            problems.append(f"id is {workflow.get('id')!r}, setup_n8n.py expects {workflow_id!r}")
        print(f"{path.relative_to(ROOT).as_posix()}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print("  -", problem)
        failed |= bool(problems)

    error_id, main_id = WORKFLOWS[0][0], WORKFLOWS[1][0]
    if loaded[main_id].get("settings", {}).get("errorWorkflow") != error_id:
        print(f"main workflow's settings.errorWorkflow must be {error_id!r}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
