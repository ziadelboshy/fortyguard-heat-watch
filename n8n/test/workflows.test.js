// Runs the generated Code nodes from n8n/workflows/*.json in a minimal fake n8n context,
// so the exact code n8n executes is tested, not just the library. Run: npm test
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { SETTINGS, sse, alert, rawMonitors } = require('./fixtures.js');

const WORKFLOWS = path.join(__dirname, '..', 'workflows');
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;

function loadWorkflow(file) {
  return JSON.parse(fs.readFileSync(path.join(WORKFLOWS, file), 'utf8'));
}

function codeOf(workflow, nodeName) {
  const node = workflow.nodes.find((n) => n.name === nodeName);
  assert.ok(node, `node ${nodeName} exists`);
  return node.parameters.jsCode;
}

/** n8n's item accessors, backed by plain arrays of { json } items. */
function accessor(items) {
  return { first: () => items[0], all: () => items, itemMatching: (i) => items[i] };
}

function runCode(code, { nodes = {}, input = [] }) {
  const $ = (name) => {
    if (!(name in nodes)) throw new Error(`test context has no node ${name}`);
    return accessor(nodes[name]);
  };
  return new AsyncFunction('$', '$input', code)($, accessor(input));
}

const json = (value) => ({ json: value });

test('every Code node in every workflow compiles', () => {
  for (const file of fs.readdirSync(WORKFLOWS).filter((f) => f.endsWith('.json'))) {
    for (const node of loadWorkflow(file).nodes.filter((n) => n.type === 'n8n-nodes-base.code')) {
      assert.doesNotThrow(() => new AsyncFunction('$', '$input', node.parameters.jsCode), `${file} / ${node.name}`);
    }
  }
});

test('main workflow: Monitors -> New alerts -> state and log, end to end', async () => {
  const wf = loadWorkflow('heat-watch.json');
  const settings = [json(SETTINGS)];

  const monitors = await runCode(codeOf(wf, 'Monitors'), {
    nodes: { Settings: settings },
    input: [json({ sse: sse({ monitors: rawMonitors() }) })],
  });
  assert.deepEqual(monitors.map((m) => m.json.site), ['Phoenix site']);

  const alertsSse = [json({ sse: sse({ alerts: [alert('hot', { temp: 37.2 }), alert('mild')] }) })];
  const run = (state) =>
    runCode(codeOf(wf, 'New alerts'), {
      nodes: { Settings: settings, 'Extract seen': [json({ data: state })], Monitors: monitors },
      input: alertsSse,
    });

  const first = await run({ seen: [] });
  assert.deepEqual(first.map((i) => i.json.kind), ['setup']);

  const later = await run({ initialized: true, seen: [] });
  assert.deepEqual(later.map((i) => [i.json.alert_id, i.json.attach]), [['hot', true], ['mild', false]]);

  const reports = await runCode(codeOf(wf, 'Reports to attach'), { nodes: { Settings: settings, 'New alerts': later } });
  assert.deepEqual(reports.map((i) => i.json.alert_id), ['hot']);

  const state = await runCode(codeOf(wf, 'Build state'), {
    nodes: { 'Extract seen': [json({ data: { initialized: true, seen: ['x'] } })], 'New alerts': later },
  });
  assert.deepEqual(state[0].json.seen, ['x', 'hot', 'mild']);

  const lines = await runCode(codeOf(wf, 'Log lines'), { nodes: { 'New alerts': later } });
  assert.equal(lines.length, 2);
  assert.match(lines[0].json.line, /^"[^"]+","Phoenix site","hot",/);
});

test('main workflow: invalid Settings stop the run with a clear error', async () => {
  const wf = loadWorkflow('heat-watch.json');
  await assert.rejects(
    runCode(codeOf(wf, 'Monitors'), {
      nodes: { Settings: [json({ ...SETTINGS, attach_report: 'sometimes' })] },
      input: [json({ sse: sse({ monitors: [] }) })],
    }),
    /Invalid Settings: attach_report/,
  );
});

test('error workflow: builds an escaped Telegram notice', async () => {
  const wf = loadWorkflow('heat-watch-errors.json');
  const out = await runCode(codeOf(wf, 'Build notice'), {
    input: [json({ workflow: { name: 'A -> B' }, execution: { lastNodeExecuted: 'X', error: { message: 'oops' } } })],
  });
  assert.match(out[0].json.text, /Heat Watch run failed[\s\S]*Workflow: A -&gt; B[\s\S]*Error: oops/);
});
