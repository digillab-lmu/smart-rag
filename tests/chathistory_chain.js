// ═════════════════════════════════════════════════════════════════════════════
// The chat-history chain, run rather than read
// ═════════════════════════════════════════════════════════════════════════════
//
// Every message a learner sends is copied into Weaviate five minutes later,
// and it has to be filed under the course it came from. The mapping is read
// from the database, the writer names `$json.course_id`, and the workflow
// tests checked both of those — while the field was never emitted in between.
// `Prepare messages` looked the course up, used it to decide whether to skip
// the message, and then built its output object without it. Both ends were
// right, the middle was empty, and four conversations were filed under no
// course at all without a single error.
//
// A check on the text of the nodes cannot see that: it would have to know
// which fields each node passes on. So this suite executes the real Code
// nodes out of the workflow file — with the transport stubbed, because what
// is being tested is what the chain carries, not that Weaviate answers — and
// asserts on what arrives at the writer.
//
// Run through: Prepare messages → Deduplicate → (Only new messages) →
// Restore message data → [the LLM's answer replaces the item] →
// Parse LLM Response → Embedding → the writer's own body expression.
// ═════════════════════════════════════════════════════════════════════════════

const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const WF = path.join(REPO, 'n8n', 'workflows', 'chathistory-sync.json');

const wf = JSON.parse(fs.readFileSync(WF, 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map(n => [n.name, n]));

const failures = [];
function check(name, ok, detail = '') {
  if (!ok) failures.push(`${name}: ${JSON.stringify(detail)}`);
}

// A renamed node must fail here, not quietly reduce the run to nothing.
function code(name) {
  if (!nodes[name]) {
    console.error(`FAILURES:\n  - the workflow has no node "${name}" — ` +
                  `this suite runs the chain by name and cannot check a ` +
                  `chain it cannot find. Known nodes: ` +
                  Object.keys(nodes).join(', '));
    process.exit(1);
  }
  return nodes[name].parameters.jsCode;
}

// ─── The bits of n8n the Code nodes touch ────────────────────────────────────
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

const ENV = {
  WEAVIATE_HTTP_PORT: '8080',
  WEAVIATE_API_KEY: 'test-key',
  EMBEDDING_BASE_URL: 'http://embed/v1',
  EMBEDDING_MODEL: 'test-model',
  EMBEDDING_API_KEY: 'test-key',
};

const requested = [];
const helpers = {
  async httpRequest(opts) {
    requested.push(String(opts.url));
    if (String(opts.url).includes('/graphql')) {
      return { data: { Get: { ChatHistory: [] } } };   // nothing is a duplicate
    }
    if (String(opts.url).includes('/embeddings')) {
      return { data: [{ embedding: [0.1, 0.2, 0.3] }] };
    }
    throw new Error('unexpected request to ' + opts.url);
  },
};

// n8n resolves `$('Node').item` to the item paired with the one being
// processed. Every node in this chain returns one item per input item, so
// the pairing is by position — modelled here by advancing a cursor while the
// node's code iterates `$input.all()`. That assumption is itself asserted
// below: if the pairing slipped, one message's text would be written under
// another message's hash.
let cursor = 0;
function inputList(items) {
  const arr = items.slice();
  arr[Symbol.iterator] = function* () {
    for (let i = 0; i < this.length; i++) { cursor = i; yield this[i]; }
  };
  return arr;
}

const OUT = {};                       // node name → the items it produced
function accessor(name) {
  if (!(name in OUT)) throw new Error(`$('${name}') has no output in this run`);
  return {
    all: () => OUT[name],
    get item() { return OUT[name][cursor] ?? OUT[name][0]; },
  };
}

async function runCode(name, input) {
  const fn = new AsyncFunction('$input', '$', '$env', code(name));
  const out = await fn.call({ helpers }, { all: () => inputList(input) },
                            accessor, ENV);
  OUT[name] = out;
  return out;
}

// ─── One batch, two courses, and one message nobody can place ────────────────
const ROWS = [
  { id: 'm1', sessionId: 'user-7|s1|agent-01', role: 'userMessage',
    content: 'Was ist eine Ableitung?', chatflowid: 'CF-MATHE',
    createdDate: '2026-08-12T17:10:10.000Z' },
  { id: 'm2', sessionId: 'user-7|s1|agent-01', role: 'apiMessage',
    content: 'Die Steigung einer Funktion.', chatflowid: 'CF-MATHE',
    createdDate: '2026-08-12T17:10:28.000Z' },
  { id: 'm3', sessionId: 'user-9|s2|agent-02', role: 'userMessage',
    content: 'Was ist ein Mol?', chatflowid: 'CF-CHEM',
    createdDate: '2026-08-12T17:11:00.000Z' },
  // A chatflow that is in Flowise but in no slot: someone built an agent by
  // hand, or a slot was cleared. Its messages cannot be filed.
  { id: 'm4', sessionId: 'user-9|s3|agent-03', role: 'userMessage',
    content: 'Bitte nicht einsortieren.', chatflowid: 'CF-UNKNOWN',
    createdDate: '2026-08-12T17:12:00.000Z' },
];

(async () => {
  OUT['Fetch new messages'] = ROWS.map(json => ({ json }));
  OUT['Look up the course per chatflow'] = [
    { json: { chatflow_id: 'CF-MATHE', course_id: 'mathe-1' } },
    { json: { chatflow_id: 'CF-CHEM', course_id: 'testkurs2' } },
  ];

  let items = await runCode('Prepare messages', []);
  check('the course leaves the node that looks it up',
        items.every(i => i.json.course_id),
        items.map(i => i.json.course_id));
  check('a message whose chatflow is in no slot is dropped',
        !items.some(i => i.json.text === 'Bitte nicht einsortieren.'),
        items.map(i => i.json.text));

  items = await runCode('Deduplicate against Weaviate', items);
  // "Only new messages": the IF node keeps what Weaviate does not have yet.
  items = items.filter(i => (i.json.ChatHistory || []).length === 0);
  check('the course survives the duplicate check',
        items.every(i => i.json.course_id), items.map(i => i.json.course_id));

  items = await runCode('Restore message data', items);
  check('…and the rebuild before the LLM call',
        items.every(i => i.json.course_id), items.map(i => i.json.course_id));

  // The HTTP node "Extract Metadata (LLM)" replaces the item with the
  // model's answer — which is why the node after it reaches back for the
  // message. Anything not carried across that gap is gone.
  items = items.map(() => ({ json: { choices: [{ message: { content:
      '{"message_type":"question","question_type":"factual",' +
      '"concepts_mentioned":["Ableitung"]}' } }] } }));

  items = await runCode('Parse LLM Response', items);
  check('…and the gap the LLM answer leaves behind',
        items.every(i => i.json.course_id), items.map(i => i.json.course_id));

  items = await runCode('Embedding', items);
  check('…and the embedding step',
        items.every(i => i.json.course_id), items.map(i => i.json.course_id));

  // ─── What the writer actually sends ────────────────────────────────────────
  // The body is an n8n expression in the file; it is evaluated here rather
  // than pattern-matched, so a field named in the body but missing from the
  // item shows up as undefined instead of as a passing grep.
  const raw = nodes['Write to Weaviate ChatHistory'].parameters.jsonBody;
  const expr = raw.replace(/^=\s*\{\{/, '').replace(/\}\}\s*$/, '');
  const written = items.map(item =>
    new Function('$json', '$', '$env', 'return (' + expr + ');')
        (item.json, accessor, ENV).properties);

  check('every written message carries a course',
        written.every(p => typeof p.course_id === 'string' && p.course_id),
        written.map(p => p.course_id));
  check('the two Mathe messages are filed under Mathe',
        written.filter(p => p.course_id === 'mathe-1').length === 2,
        written.map(p => [p.text, p.course_id]));
  check('the chemistry message is filed under its own course',
        written.filter(p => p.course_id === 'testkurs2').length === 1,
        written.map(p => [p.text, p.course_id]));
  check('the unplaceable message was not written',
        written.length === 3, written.map(p => p.text));

  // The pairing across the LLM call. If it slipped, every message would be
  // written with the first message's text under its own hash — the sort of
  // damage that is only visible by reading the conversations back.
  const texts = written.map(p => p.text);
  check('each written message keeps its own text',
        new Set(texts).size === texts.length, texts);
  check('…and its own hash', new Set(written.map(p => p.message_hash)).size === 3,
        written.map(p => p.message_hash));
  check('the roles are not smeared across messages',
        written.filter(p => p.role === 'assistant').length === 1,
        written.map(p => [p.text, p.role]));

  // Nothing else may have gone missing along the same route.
  for (const field of ['user_id', 'session_id', 'agent_id', 'timestamp',
                       'trace_id', 'chapter', 'message_type']) {
    check(`${field} arrives at the writer`,
          written.every(p => p[field] !== undefined && p[field] !== ''),
          written.map(p => p[field]));
  }
  check('the vector is attached', items.every(i => Array.isArray(i.json.embedding)),
        items.map(i => typeof i.json.embedding));
  check('the course is never read from the environment',
        !raw.includes('COURSE_ID'), raw.slice(0, 200));

  if (failures.length) {
    console.log('FAILURES:');
    for (const f of failures) console.log('  -', f);
    process.exit(1);
  }
  console.log(
    'All chat-history chain checks passed: the workflow\'s own Code nodes ' +
    'were executed in order against a batch holding two courses and one ' +
    'message whose chatflow belongs to no slot — the course is emitted where ' +
    'it is looked up and survives the duplicate check, the rebuild, the LLM ' +
    'answer that replaces the item and the embedding, each message reaches ' +
    'the writer with its own text, role and hash under its own course, and ' +
    'the message nobody can place is dropped rather than written without one.'
  );
})().catch(e => {
  console.error('The chain could not be run:', e && e.stack || e);
  process.exit(1);
});
