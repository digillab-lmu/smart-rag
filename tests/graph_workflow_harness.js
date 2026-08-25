
// Runs the workflow's own Code nodes, taken from the committed JSON, against
// stubbed Weaviate answers and a stubbed model. See test_graph_workflow.sh
// for why this exists.
const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const wf = JSON.parse(fs.readFileSync(path.join(REPO, 'n8n/workflows/graph-build.json'), 'utf8'));
const nodeCode = {};
for (const n of wf.nodes) {
  if (n.type === 'n8n-nodes-base.code') nodeCode[n.name] = n.parameters.jsCode;
}

const failures = [];
function check(name, ok, detail) {
  if (!ok) failures.push(`${name}: ${detail === undefined ? '' : JSON.stringify(detail).slice(0, 300)}`);
}

// -- A very small n8n --------------------------------------------------------
// Enough of the runtime for these nodes: $input, $('Node Name'), $env, and
// this.helpers.request. Deliberately not a general emulator -- if a node
// starts needing more than this, that is worth noticing rather than papering
// over.
function makeContext(inputItems, previous, env, requestImpl) {
  const wrap = (items) => ({
    first: () => items[0],
    all: () => items,
    last: () => items[items.length - 1],
  });
  const ctx = {
    $input: wrap(inputItems),
    $: (name) => {
      if (!(name in previous)) throw new Error(`node ${name} not recorded in this harness`);
      return wrap(previous[name]);
    },
    $env: env,
    $json: inputItems[0] && inputItems[0].json,
    helpers: { request: requestImpl },
  };
  return ctx;
}

async function run(nodeName, inputItems, previous, env, requestImpl) {
  const src = nodeCode[nodeName];
  if (!src) throw new Error(`no code node called ${nodeName} in the workflow`);
  const ctx = makeContext(inputItems, previous, env, requestImpl);
  const fn = new Function('$input', '$', '$env', '$json',
    `return (async () => { ${src} }).call(this);`);
  return await fn.call(ctx, ctx.$input, ctx.$, ctx.$env, ctx.$json);
}

const ENV = { LLM_PROVIDER: 'openai', LLM_API_KEY: 'k', LLM_MODEL_STRONG: 'm' };

(async () => {

// -- Planning ----------------------------------------------------------------
const body = { build_id: 'gb-1', course_id: 'kurs-1', collection: 'Chunk',
               course_name: 'Testkurs', agents: [1, 2], language: 'de' };
let planOut = await run('Plan the run', [{ json: { body } }], {}, ENV);
const plan = planOut[0].json;
check('the plan carries the build id', plan.buildId === 'gb-1', plan);
check('and only positive agent numbers', JSON.stringify(plan.agents) === '[1,2]', plan.agents);

for (const [missing, why] of [['build_id', 'nothing could be reported back'],
                              ['course_id', 'concepts would belong to no course'],
                              ['collection', 'there is nothing to read'],
                              ['agents', 'there is no material']]) {
  const broken = Object.assign({}, body);
  if (missing === 'agents') broken.agents = []; else delete broken[missing];
  let threw = false;
  try { await run('Plan the run', [{ json: { body: broken } }], {}, ENV); }
  catch (e) { threw = true; }
  check(`a run without ${missing} is refused`, threw,
        'the webhook already answered, so a bad request can only be caught here');
}

// -- Slicing -----------------------------------------------------------------
// Two documents. The first has many sections and must be cut into pieces; the
// second is small. Chunks repeat their section, which must collapse.
const rows = [];
for (let i = 0; i < 300; i++) {
  rows.push({ source_title: 'Grosses Werk', source_file: 'agent_1/gross.md',
              chapter_title: `Kapitel ${Math.floor(i / 10) + 1}`,
              section: `Abschnitt ${i}`, section_id: `${Math.floor(i / 10) + 1}.${i % 10}`,
              chunk_index: i, text: 'Lorem ipsum dolor sit amet '.repeat(40) });
}
for (let i = 0; i < 4; i++) {
  rows.push({ source_title: 'Kleines Werk', source_file: 'agent_2/klein.md',
              chapter_title: '', section: 'Einleitung', section_id: '',
              chunk_index: i, text: 'Kurzer Text ' .repeat(10) });
}
const weaviate = { json: { data: { Get: { Chunk: rows } } } };
const sliceOut = await run('Slice the material', [weaviate],
                           { 'Plan the run': planOut }, ENV);
const slices = sliceOut.map(s => s.json);

check('the material is cut into several slices', slices.length > 1, slices.length);
check('no slice spans two documents',
      slices.every(s => s.entries.length > 0) &&
      new Set(slices.map(s => s.source_file)).size === 2,
      slices.map(s => s.source_file));
const bigSlices = slices.filter(s => s.source_file === 'agent_1/gross.md');
check('a document larger than one slice is cut up', bigSlices.length > 1, bigSlices.length);
// The node's own cost formula, not an approximation of it: a check that
// invents its own arithmetic fails for the wrong reasons.
const sliceCost = (s) => s.entries.reduce(
  (n, e) => n + e.chapter.length + e.section.length + e.section_id.length
              + e.excerpt.length + 8, 0);
check('and each slice stays inside the budget',
      slices.every(s => sliceCost(s) <= plan.SLICE_CHARS),
      slices.map(sliceCost));
check('repeated chunks of one section become one entry',
      slices.filter(s => s.source_file === 'agent_2/klein.md')
            .reduce((n, s) => n + s.entries.length, 0) === 1,
      'four chunks of one section must not be four entries');
check('every slice knows how many there are',
      slices.every(s => s.sliceTotal === slices.length), slices[0]);
check('excerpts are capped', slices[0].entries.every(e => e.excerpt.length <= plan.EXCERPT_CHARS),
      slices[0].entries[0].excerpt.length);

// Three answers that all used to look identical from here, and only one of
// them is an empty course. On the first live run a rejected query was
// reported to the operator as "this course has no indexed material", which
// sent them looking at their documents instead of at the query.
const sliceFails = async (payload) => {
  try {
    await run('Slice the material', [{ json: payload }], { 'Plan the run': planOut }, ENV);
    return null;
  } catch (e) { return e.message; }
};

let msg = await sliceFails({ data: { Get: { Chunk: [] } } });
check('a course with no material is refused rather than guessed at', Boolean(msg), '');
check('and the refusal names the agents it looked for',
      msg && /1, 2/.test(msg), msg);

msg = await sliceFails({ errors: [{ message: 'Cannot query field "agent_id"' }] });
check('a rejected query is not reported as an empty course',
      msg && /refused the query/.test(msg) && /agent_id/.test(msg), msg);
check('and says the fault is in the query',
      msg && /not an empty course/.test(msg), msg);

msg = await sliceFails({ data: { Get: { AndereKlasse: [] } } });
check('an answer about a different collection says so',
      msg && /no collection called "Chunk"/.test(msg) && /AndereKlasse/.test(msg), msg);

msg = await sliceFails({ something: 'else' });
check('an answer with no data section quotes what came back',
      msg && /no data section/.test(msg), msg);

// A read that comes back exactly full is a course larger than one read, and
// everything after it would be built from an arbitrary prefix -- arbitrary
// because Weaviate returns no order of its own. That must stop, not truncate.
const full = Array.from({ length: plan.READ_LIMIT }, (_, i) => ({
  source_title: 'W', source_file: 'agent_1/w.md', chapter_title: 'K',
  section: `S${i}`, section_id: `${i}`, chunk_index: i, text: 'x '.repeat(20) }));
msg = await sliceFails({ data: { Get: { Chunk: full } } });
check('a course larger than one read is refused, not truncated',
      msg && /as many as one read returns/.test(msg), msg);
check('and the refusal names the setting that fixes it',
      msg && /QUERY_MAXIMUM_RESULTS/.test(msg), msg);
check('the read limit stays under Weaviate default ceiling of 10000',
      plan.READ_LIMIT < 10000,
      'a limit above the cap is refused outright, not trimmed -- which is how '
      + 'the first live run was told the course had no material');

// -- Extraction --------------------------------------------------------------
let asked = [];
const answerWith = (payload) => async (opts) => {
  asked.push(JSON.parse(opts.body));
  return JSON.stringify({ choices: [{ message: { content: JSON.stringify(payload) },
                                      finish_reason: 'stop' }] });
};

const extractOut = await run('Extract concepts', [{ json: slices[0] }],
  { 'Plan the run': planOut }, ENV,
  answerWith({ concepts: [{ name: 'Medienkompetenz', description: 'Was das ist.',
                            chapter: 'Kapitel 1', section_id: '1.1' },
                          { name: '', description: 'namenlos' }] }));
const extracted = extractOut[0].json;
check('the model is asked about one slice at a time', asked.length === 1, asked.length);
check('the material reaches the prompt',
      JSON.stringify(asked[0]).includes('Lorem ipsum'), 'the excerpt was not sent');
check('a nameless concept is dropped', extracted.concepts.length === 1, extracted.concepts);
check('and provenance is attached here',
      extracted.concepts[0].source_file === 'agent_1/gross.md', extracted.concepts[0]);

// An empty answer must fail loudly rather than travel on as nothing.
let emptyAnswer = false;
try {
  await run('Extract concepts', [{ json: slices[0] }], { 'Plan the run': planOut }, ENV,
    async () => JSON.stringify({ choices: [{ message: { content: '' },
                                             finish_reason: 'length' }] }));
} catch (e) {
  emptyAnswer = /empty answer/.test(e.message) && /budget/.test(e.message);
}
check('an empty model answer fails with the reason', emptyAnswer,
      'measured live: a reasoning model spends the budget thinking and returns ""');

// -- Merging -----------------------------------------------------------------
const candidates = [
  { json: { concepts: [
      { name: 'Medienkompetenz', description: 'Erste Definition.', chapter: 'K1',
        section_id: '1.1', source_file: 'agent_1/gross.md', source_title: 'Grosses Werk' },
      { name: 'Mediendidaktik', description: 'Zweites.', chapter: 'K2',
        section_id: '2.1', source_file: 'agent_1/gross.md', source_title: 'Grosses Werk' }] } },
  { json: { concepts: [
      { name: 'medienkompetenz.', description: 'Spaetere Erwaehnung.', chapter: null,
        section_id: null, source_file: 'agent_2/klein.md', source_title: 'Kleines Werk' },
      { name: 'Medienkompetenzen', description: 'Plural, absichtlich getrennt.',
        chapter: null, section_id: null,
        source_file: 'agent_2/klein.md', source_title: 'Kleines Werk' }] } },
];
const mergeOut = await run('Merge candidates', candidates, { 'Plan the run': planOut }, ENV);
const merged = mergeOut[0].json;
// Exact match after the same normalisation the node uses. startsWith found
// "Medienkompetenzen" for "medienkompetenz" and so checked the wrong row --
// which is exactly the confusion the near-synonym case exists to guard.
const key = (s) => s.toLowerCase().replace(/[.,;:!?]+$/, '').trim();
const find = (n) => merged.concepts.find(c => key(c.name) === n);

check('the same concept from two works becomes one',
      merged.concepts.filter(c => c.name.toLowerCase().replace(/\W/g, '') === 'medienkompetenz').length === 1,
      merged.concepts.map(c => c.name));
check('and keeps both citations',
      find('medienkompetenz').sources.length === 2, find('medienkompetenz'));
check('the first description wins over a later mention',
      find('medienkompetenz').description === 'Erste Definition.',
      find('medienkompetenz').description);
check('a near-synonym is not merged by guesswork',
      merged.concepts.some(c => c.name === 'Medienkompetenzen'),
      'a wrong merge destroys a distinction the course draws, silently');
check('the merge is counted', merged.stats.merged === 1, merged.stats);

// The cap, and that it says what it dropped.
const many = [{ json: { concepts: Array.from({ length: 600 }, (_, i) => ({
  name: `Begriff ${i}`, description: 'x', chapter: null, section_id: null,
  source_file: 'agent_1/gross.md', source_title: 'Grosses Werk' })) } }];
const capped = (await run('Merge candidates', many, { 'Plan the run': planOut }, ENV))[0].json;
check('the list is cut to the limit the Content Admin accepts',
      capped.concepts.length === plan.MAX_CONCEPTS, capped.concepts.length);
check('and says how many were dropped', capped.stats.dropped === 100, capped.stats);

let noConcepts = false;
try {
  await run('Merge candidates', [{ json: { concepts: [] } }], { 'Plan the run': planOut }, ENV);
} catch (e) { noConcepts = true; }
check('a run that found nothing fails rather than proposing an empty map', noConcepts, '');

// -- Prerequisites -----------------------------------------------------------
asked = [];
const edgeAnswer = {
  prerequisites: [
    { before: 'Medienkompetenz', after: 'Mediendidaktik', sources: ['Grosses Werk'] },
    { before: 'Mediendidaktik', after: 'Medienkompetenz', sources: ['Grosses Werk'] },
    { before: 'Medienkompetenz', after: 'Gibt es nicht', sources: ['Grosses Werk'] },
    { before: 'Mediendidaktik', after: 'Medienkompetenzen', sources: [] },
    { before: 'Medienkompetenz', after: 'Medienkompetenz', sources: ['Grosses Werk'] },
  ],
};
const edgeOut = await run('Propose prerequisites', [{ json: merged }],
  { 'Plan the run': planOut }, ENV, answerWith(edgeAnswer));
const result = edgeOut[0].json;
const prereq = result.proposal.prerequisites;

check('the concept list is what is sent, not the material',
      JSON.stringify(asked[0]).includes('Medienkompetenz')
      && !JSON.stringify(asked[0]).includes('Lorem ipsum'),
      'this is the step that must not grow with the corpus');
check('a real dependency survives',
      prereq.some(e => e.before === 'Medienkompetenz' && e.after === 'Mediendidaktik'), prereq);
check('an edge naming an unknown concept is dropped',
      !prereq.some(e => e.after === 'Gibt es nicht'), prereq);
check('and counted', result.stats.edges_unresolved >= 1, result.stats);
check('an edge citing nothing is dropped',
      !prereq.some(e => e.after === 'Medienkompetenzen'), prereq);
check('and counted too', result.stats.edges_uncited === 1, result.stats);
check('a self-loop is dropped', !prereq.some(e => e.before === e.after), prereq);
check('the cycle is broken', prereq.length === 1, prereq);
check('and reported rather than hidden', result.stats.edges_cyclic === 1, result.stats);
check('citations are resolved to file paths, not titles',
      prereq[0].sources[0] === 'agent_1/gross.md', prereq[0]);
check('every concept carries its sources',
      result.proposal.concepts.every(c => Array.isArray(c.sources) && c.sources.length),
      result.proposal.concepts[0]);
check('empty optional fields are left out rather than sent as null',
      !JSON.stringify(result.proposal).includes('null'), 'nulls are noise in a review');
check('the build id travels to the callback', result.buildId === 'gb-1', result);

// -- The failure path --------------------------------------------------------
// Reached from a node's error output, so the item is whatever that node had
// plus an "error". The build id comes from the webhook, because a failure in
// the very first node leaves nothing else to read it from.
const webhookItems = [{ json: { body: { build_id: 'gb-1', course_id: 'kurs-1' } } }];

let failOut = await run('Read the failure',
  [{ json: { error: { message: 'Weaviate refused the connection' } } }],
  { 'Build Webhook': webhookItems }, ENV);
check('a failure finds the build to report against',
      failOut[0].json.buildId === 'gb-1', failOut[0].json);
check('and carries the message', /Weaviate refused/.test(failOut[0].json.error),
      failOut[0].json.error);

failOut = await run('Read the failure', [{ json: { error: 'plain string' } }],
                    { 'Build Webhook': webhookItems }, ENV);
check('an error given as a bare string is handled too',
      /plain string/.test(failOut[0].json.error), failOut[0].json);

failOut = await run('Read the failure', [{ json: {} }],
                    { 'Build Webhook': webhookItems }, ENV);
check('a failure with no message still reports something',
      failOut[0].json.error.length > 0 && failOut[0].json.buildId === 'gb-1',
      failOut[0].json);

// -- Done --------------------------------------------------------------------
if (failures.length) {
  console.log('FAILURES:');
  for (const f of failures) console.log('  - ' + f);
  process.exit(1);
}
console.log('All graph-workflow checks passed: the planner refuses a run missing');
console.log('any of the four things it cannot recover from; the material is cut');
console.log('per document into slices inside the budget, repeated chunks of one');
console.log('section collapse, and a course with nothing indexed is refused');
console.log('rather than guessed at; extraction asks about one slice at a time,');
console.log('drops a nameless concept, attaches provenance, and fails loudly on');
console.log('an empty answer with the reason; merging unites the same concept');
console.log('from two works while keeping both citations and the first');
console.log('description, refuses to merge a near-synonym by guesswork, and cuts');
console.log('to the limit the Content Admin accepts while saying what it');
console.log('dropped; the prerequisite pass sends the concept list rather than');
console.log('the corpus, drops edges naming unknown concepts, edges citing');
console.log('nothing, self-loops and the edge that closes a cycle -- counting');
console.log('each -- and resolves citations to file paths; and a failure finds');
console.log('the build it belongs to or says it cannot.');
})().catch(e => { console.log('HARNESS ERROR:', e.stack); process.exit(1); });
