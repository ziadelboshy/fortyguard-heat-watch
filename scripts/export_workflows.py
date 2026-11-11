"""Export the workflows from the local n8n back into the repository, safely.

Use this after editing the node graph in the n8n editor:

  python3 scripts/export_workflows.py

For each workflow it exports the live copy, then:
  - puts the chat-ID placeholder back (Settings value and any literal Telegram chatId),
  - drops pinned data, version and sharing metadata, and anything but credential references,
  - forces "active": false,
then rebuilds the Code nodes from n8n/src (the source of truth for Code-node logic) and runs
scripts/check_workflows.py. Review the result with `git diff` before committing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_workflows
import check_workflows
from setup_n8n import CHAT_ID_PLACEHOLDER, CONTAINER, WORKFLOWS, docker, ensure_container_running

KEEP_KEYS = ["id", "name", "active", "settings", "nodes", "connections", "pinData"]
NODE_KEYS = [
    "id",
    "name",
    "type",
    "typeVersion",
    "position",
    "parameters",
    "credentials",
    "onError",
    "disabled",
    "notes",
]


def sanitize(exported: dict[str, Any]) -> dict[str, Any]:
    workflow = {key: exported.get(key) for key in KEEP_KEYS if key in exported}
    workflow["active"] = False
    workflow["pinData"] = {}
    nodes = []
    for raw in exported["nodes"]:
        node = {key: raw[key] for key in NODE_KEYS if key in raw}
        params = node.get("parameters", {})
        if "chatId" in params and not str(params["chatId"]).startswith("="):
            params["chatId"] = CHAT_ID_PLACEHOLDER
        for item in params.get("assignments", {}).get("assignments", []):
            if item.get("name") == "telegram_chat_id":
                item["value"] = CHAT_ID_PLACEHOLDER
        if "credentials" in node:
            node["credentials"] = {
                kind: {"id": ref.get("id"), "name": ref.get("name")} for kind, ref in node["credentials"].items()
            }
        nodes.append(node)
    workflow["nodes"] = nodes
    return workflow


def main() -> int:
    ensure_container_running()
    for workflow_id, path in WORKFLOWS:
        _, output = docker("exec", CONTAINER, "n8n", "export:workflow", f"--id={workflow_id}")
        start = output.find("[")
        if start < 0:
            raise SystemExit(f"Unexpected export output for {workflow_id}:\n{output[-500:]}")
        exported = json.loads(output[start:])
        workflow = sanitize(exported[0] if isinstance(exported, list) else exported)
        path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"exported {workflow_id} -> {path.relative_to(build_workflows.ROOT).as_posix()}")
    build_workflows.main([])
    return check_workflows.main()


if __name__ == "__main__":
    sys.exit(main())
