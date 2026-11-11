"""Build the n8n workflow files from the JavaScript sources in n8n/src/.

n8n stores each Code node's JavaScript inside the workflow JSON, which is hard to review
and impossible to unit-test. Here the logic lives in real files instead:

  n8n/src/lib.js          shared, pure functions (unit-tested with `node --test`)
  n8n/src/nodes/*.js      a few lines of glue per Code node

This script writes `lib.js` + the node's glue into the matching Code node of each workflow.

  python3 scripts/build_workflows.py          # rebuild n8n/workflows/*.json
  python3 scripts/build_workflows.py --check  # exit 1 if the workflows are out of date (CI)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "n8n" / "src"
WORKFLOWS = ROOT / "n8n" / "workflows"

# workflow file -> {Code node name: glue file in n8n/src/nodes}
CODE_NODES: dict[str, dict[str, str]] = {
    "heat-watch.json": {
        "Monitors": "monitors.js",
        "New alerts": "new-alerts.js",
        "Reports to attach": "reports-to-attach.js",
        "Build state": "build-state.js",
        "Log lines": "log-lines.js",
    },
    "heat-watch-errors.json": {
        "Build notice": "error-notice.js",
    },
}

HEADER = (
    "// GENERATED from n8n/src by scripts/build_workflows.py. Edit the source files, not this node.\n"
    "// Shared library (n8n/src/lib.js) followed by this node's glue (n8n/src/nodes/{glue}).\n\n"
)
EXPORT_BLOCK = re.compile(r"\n// @export-start.*?// @export-end\n", re.DOTALL)


def library() -> str:
    text = (SRC / "lib.js").read_text(encoding="utf-8")
    stripped = EXPORT_BLOCK.sub("\n", text)
    if stripped == text:
        raise SystemExit("n8n/src/lib.js: export block markers not found")
    return stripped.rstrip() + "\n"


def render(workflow_file: str, lib: str) -> str:
    path = WORKFLOWS / workflow_file
    workflow = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in workflow["nodes"]}
    for node_name, glue in CODE_NODES[workflow_file].items():
        node = nodes.get(node_name)
        if node is None or node["type"] != "n8n-nodes-base.code":
            raise SystemExit(f"{workflow_file}: no Code node named {node_name!r}")
        code = (SRC / "nodes" / glue).read_text(encoding="utf-8")
        node["parameters"]["jsCode"] = HEADER.format(glue=glue) + lib + "\n// ---- node glue ----\n" + code
    return json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a workflow file is out of date")
    args = parser.parse_args(argv)
    lib = library()
    stale = []
    for workflow_file in CODE_NODES:
        path = WORKFLOWS / workflow_file
        rendered = render(workflow_file, lib)
        if path.read_text(encoding="utf-8") == rendered:
            continue
        if args.check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(rendered, encoding="utf-8", newline="\n")
            print(f"rebuilt {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("Out of date (run: python3 scripts/build_workflows.py):", ", ".join(stale))
        return 1
    if args.check:
        print("workflows are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
