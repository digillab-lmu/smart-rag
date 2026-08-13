// ═════════════════════════════════════════════════════════════════════════════
// The learning-record chain, run rather than read
// ═════════════════════════════════════════════════════════════════════════════
//
// This workflow failed on every scheduled run for weeks and nobody noticed:
// ten executions, all red, while it kept writing records. Its DELETE node's
// URL contained an unevaluated expression, so the previous record was never
// removed — one duplicate per run, ten for the most active learner. Reading
// the node text had not found it; the check for stray expression markers
// looked for "={{" and the broken one was preceded by a colon.
//
// So the same treatment as chathistory-sync: the real Code nodes are taken
// out of the workflow file and executed in order, with the transport
// stubbed. What is asserted is what the chain does — which records it looks
// up, which ids it removes, what it writes, and what it refuses.
//
// The shape under test is the one that makes the old bug impossible: the
// record's id is derived from the (course, learner) pair, everything that
// exists for that pair is removed before the write, and a failed write
// leaves nothing behind rather than a duplicate.
// ═════════════════════════════════════════════════════════════════════════════

const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const WF = path.join(REPO, 'n8n', 'workflows', 'usermemory-summary.json');

const wf = JSON.parse(fs.readFileSync(WF, 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map(n => [n.name, n]));

const failures = [];
function check(name, ok, detail = '') {
  if (!ok) failures.push(`${name}: ${JSON.stringify(detail)}`);
}

function code(name) {
  if (!nodes[name]) {
    console.error(`FAILURES:\n  - the workflow has no node "${name}". This ` +
                  `suite runs the chain by name and cannot check a chain it ` +
                  `cannot find. Known nodes: ${Object.keys(nodes).join(', ')}`);
    process.exit(1);
  }
  return nodes[name].parameters.jsCode;
}

// ─── The bits of n8n the Code nodes touch ────────────────────────────────────
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

const ENV = { WEAVIATE_HTTP_PORT: '8080', WEAVIATE_API_KEY: 'test-key' };

// n8n only lets a Code node require what NODE_FUNCTION_ALLOW_BUILTIN lists,
// so the harness allows exactly the same set — read from the compose file
// rather than restated here. The record's id is a version-5 UUID, which is
// SHA-1: take crypto out of that list and this suite fails with the
// workflow's own message, instead of the workflow failing at 4am in
// production.
const COMPOSE = fs.readFileSync(
  path.join(REPO, 'docker', 'docker-compose.yml'), 'utf8');
const allowMatch = COMPOSE.match(/NODE_FUNCTION_ALLOW_BUILTIN:\s*"([^"]*)"/);
const ALLOWED = new Set((allowMatch ? allowMatch[1] : '')
  .split(',').map(s => s.trim()).filter(Boolean));

function sandboxRequire(name) {
  if (!ALLOWED.has(name)) {
    const err = new Error(`Cannot find module '${name}'`);
    err.code = 'MODULE_NOT_FOUND';
    throw err;
  }
  return require(name);
}

// Which ids the chain asked Weaviate to delete, and what it was told.
const deleted = [];
let missingIds = new Set();
let failStatusFor = null;

const helpers = {
  async httpRequest(opts) {
    if (opts.method === 'DELETE') {
      const id = String(opts.url).split('/').pop();
      deleted.push(id);
      if (id === failStatusFor) return { statusCode: 500, body: '' };
      return { statusCode: missingIds.has(id) ? 404 : 204, body: '' };
    }
    throw new Error('unexpected request: ' + opts.method + ' ' + opts.url);
  },
};

let cursor = 0;
function inputList(items) {
  const arr = items.slice();
  arr[Symbol.iterator] = function* () {
    for (let i = 0; i < this.length; i++) { cursor = i; yield this[i]; }
  };
  return arr;
}

const OUT = {};
function accessor(name) {
  if (!(name in OUT)) throw new Error(`$('${name}') has no output in this run`);
  return {
    all: () => OUT[name],
    get item() { return OUT[name][cursor] ?? OUT[name][0]; },
  };
}

// n8n runs a Code node either once over all items or once per item, and the
// two see a different $input. The mode is in the file, so it is read from
// there rather than assumed — a node switched from one to the other would
// otherwise be tested in a shape it no longer has.
async function runCode(name, input) {
  const fn = new AsyncFunction('$input', '$', '$env', 'require', code(name));

  if (nodes[name].parameters.mode === 'runOnceForEachItem') {
    const out = [];
    for (let i = 0; i < input.length; i++) {
      cursor = i;
      const one = await fn.call({ helpers },
                                { item: input[i], all: () => [input[i]] },
                                accessor, ENV, sandboxRequire);
      out.push(one);
    }
    OUT[name] = out;
    return out;
  }

  const $input = {
    all: () => inputList(input),
    first: () => input[0],
    last: () => input[input.length - 1],
  };
  const out = await fn.call({ helpers }, $input, accessor, ENV, sandboxRequire);
  OUT[name] = out;
  return out;
}

// ─── Fixture ─────────────────────────────────────────────────────────────────
const LEARNER = '84bdbee2-4221-4f3d-a7ff-e7644b87e799';
const OTHER = '99410aab-4875-47aa-a062-e6ca5bca86c0';

// Two courses have chat history; the third group is what messages written
// before the sync carried a course look like.
const COURSE_GROUPS = [
  { groupedBy: { value: 'mathe-1' }, meta: { count: 16 } },
  { groupedBy: { value: 'chemie-1' }, meta: { count: 14 } },
  { groupedBy: { value: '' }, meta: { count: 4 } },
];

const USERS_PER_COURSE = {
  'mathe-1': [{ groupedBy: { value: LEARNER }, meta: { count: 16 } }],
  'chemie-1': [{ groupedBy: { value: LEARNER }, meta: { count: 8 } },
               { groupedBy: { value: OTHER }, meta: { count: 6 } }],
};

// What the lookup finds. The Mathe pair has three duplicates — the state a
// failing delete leaves behind — with different last_updated values.
const EXISTING = {
  [`mathe-1|${LEARNER}`]: [
    { _additional: { id: 'dup-newest' }, last_updated: '2026-08-12T18:00:00.000Z',
      concepts_mastered: ['Ableitung'], concepts_struggling: [],
      open_questions: [], learning_patterns: 'fragt nach Beispielen', summary: 'neu' },
    { _additional: { id: 'dup-older' }, last_updated: '2026-08-10T09:00:00.000Z',
      concepts_mastered: ['nichts'], concepts_struggling: [],
      open_questions: [], learning_patterns: 'alt', summary: 'alt' },
    { _additional: { id: 'dup-oldest' }, last_updated: '2026-08-01T09:00:00.000Z',
      concepts_mastered: [], concepts_struggling: [],
      open_questions: [], learning_patterns: '', summary: '' },
  ],
  [`chemie-1|${LEARNER}`]: [],
  [`chemie-1|${OTHER}`]: [],
};

// Messages old enough that the 30-minute "session still active" guard does
// not apply.
const OLD = new Date(Date.now() - 4 * 3600 * 1000).toISOString();

function chatFor(course, user) {
  return [
    { user_id: user, course_id: course, role: 'user', text: `Frage in ${course}`,
      timestamp: OLD, chapter: 1, concepts_mentioned: [] },
    { user_id: user, course_id: course, role: 'assistant', text: 'Antwort',
      timestamp: OLD, chapter: 1, concepts_mentioned: [] },
  ];
}

const LLM_ANSWER = JSON.stringify({
  concepts_mastered: ['Ableitung'],
  concepts_struggling: ['Kettenregel'],
  open_questions: ['Wozu braucht man das?'],
  learning_patterns: 'arbeitet mit Beispielen',
  summary: 'Eine Sitzung über Ableitungen.',
});

// A GraphQL body built by a Code node, answered from the fixture. Parsed
// rather than pattern-matched, so a query that asks for the wrong thing
// shows up as the wrong answer.
function answerUserMemory(body) {
  const q = body.query;
  const user = (q.match(/"user_id"\]\s+operator: Equal valueText: "([^"]+)"/) || [])[1];
  const course = (q.match(/"course_id"\]\s+operator: Equal valueText: "([^"]+)"/) || [])[1];
  return { key: `${course}|${user}`, user, course,
           data: { Get: { UserMemory: EXISTING[`${course}|${user}`] ?? [] } } };
}

(async () => {
  // ─── 1. Courses, then learners inside each course ──────────────────────────
  const courseAggregate = [
    { json: { data: { Aggregate: { ChatHistory: COURSE_GROUPS } } } }];
  OUT['Get distinct courses'] = courseAggregate;

  const courses = await runCode('List courses', courseAggregate);
  check('a course with no id is left out',
        courses.every(c => c.json.course_id), courses.map(c => c.json.course_id));
  check('both real courses are in', courses.length === 2,
        courses.map(c => c.json.course_id));

  // The HTTP node runs once per course and answers in order.
  const userResponses = courses.map(c => ({
    json: { data: { Aggregate: { ChatHistory: USERS_PER_COURSE[c.json.course_id] } } },
  }));
  const pairs = await runCode('Extract user_ids', userResponses);
  check('one item per course and learner', pairs.length === 3,
        pairs.map(p => [p.json.course_id, p.json.user_id]));
  check('the same learner appears in both their courses',
        pairs.filter(p => p.json.user_id === LEARNER).length === 2,
        pairs.map(p => [p.json.course_id, p.json.user_id]));

  // ─── 2. The lookup asks for a pair, and for all of its records ─────────────
  const looked = pairs.map(p => answerUserMemory(p.json.um_query_body));
  check('the lookup names the learner and the course',
        looked.every(l => l.user && l.course), looked.map(l => l.key));
  check('the lookup pairs up with the items it came from',
        looked.map(l => l.key).join(',') ===
          pairs.map(p => `${p.json.course_id}|${p.json.user_id}`).join(','),
        looked.map(l => l.key));
  check('the lookup no longer stops at the first record',
        !pairs.some(p => /limit:\s*1\b/.test(p.json.um_query_body.query)),
        'limit: 1 hides every duplicate but one');

  const withMemory = await runCode(
    'Process Memory', looked.map(l => ({ json: { data: l.data } })));

  const mathe = withMemory.find(i => i.json.course_id === 'mathe-1').json;
  check('every existing record of the pair is marked stale',
        JSON.stringify(mathe.stale_ids.slice().sort()) ===
          JSON.stringify(['dup-older', 'dup-oldest', 'dup-newest'].sort()),
        mathe.stale_ids);
  // The cursor decides which messages are summarised. Taken from an older
  // duplicate, the same conversation would be summarised again.
  check('the cursor comes from the newest of the duplicates',
        mathe.last_updated === '2026-08-12T18:00:00.000Z', mathe.last_updated);
  check('…and so does what the model is given as prior state',
        mathe.existing.learning_patterns === 'fragt nach Beispielen',
        mathe.existing);
  check('a pair with no record starts at the beginning',
        withMemory.filter(i => i.json.course_id === 'chemie-1')
                  .every(i => i.json.last_updated.startsWith('1970')),
        withMemory.map(i => i.json.last_updated));
  check('the chat query is scoped to the pair',
        withMemory.every(i =>
          i.json.ch_query_body.query.includes('"course_id"') &&
          i.json.ch_query_body.query.includes('"user_id"')),
        withMemory[0].json.ch_query_body.query.slice(0, 200));

  // ─── 3. Preparation, the model, and the record ─────────────────────────────
  const prepared = await runCode('Check and Prepare', withMemory.map(i => ({
    json: { data: { Get: { ChatHistory: chatFor(i.json.course_id, i.json.user_id) } } },
  })));
  check('nothing is skipped when the messages are old enough',
        prepared.every(p => p.json.skip === false), prepared.map(p => p.json));
  check('the course survives the preparation',
        prepared.every(p => p.json.course_id), prepared.map(p => p.json.course_id));

  // ─── The path that writes nothing still tidies up ──────────────────────────
  // A learner who has stopped writing in a course is skipped on every run,
  // and the write path is the only thing that used to clear duplicates. On
  // the machine this was found on, three pairs kept 4, 4 and 2 copies for
  // exactly that reason while the pairs that were rewritten collapsed to one.
  OUT['Process Memory'] = withMemory;
  const idle = await runCode('Check and Prepare', withMemory.map(i => ({
    json: { data: { Get: { ChatHistory: [] } } },   // nothing new to summarise
  })));
  check('a pair with no new messages is skipped',
        idle.every(p => p.json.skip === true && p.json.reason === 'no_new_messages'),
        idle.map(p => p.json.reason));
  check('the skip still carries the duplicates and the one to keep',
        idle.every(p => Array.isArray(p.json.stale_ids) && 'newest_id' in p.json),
        idle.map(p => Object.keys(p.json)));

  deleted.length = 0;
  const tidied = await runCode('Remove duplicates', idle);
  check('the duplicates of a skipped pair are removed',
        deleted.includes('dup-older') && deleted.includes('dup-oldest'), deleted);
  check('…but the record it is skipping on is kept',
        !deleted.includes('dup-newest'), deleted);
  check('…and the run says how many it cleared',
        tidied.some(t => t.json.duplicates_removed === 2),
        tidied.map(t => t.json.duplicates_removed));
  check('a pair with a single record has nothing to clear',
        tidied.filter(t => t.json.course_id === 'chemie-1')
              .every(t => t.json.duplicates_removed === 0),
        tidied.map(t => [t.json.course_id, t.json.duplicates_removed]));

  // A failed delete here must not turn a skipped pair into a red run: there
  // was nothing to write, and the reason for the skip is the useful output.
  deleted.length = 0;
  failStatusFor = 'dup-older';
  let tidyThrew = false;
  try {
    await runCode('Remove duplicates', idle);
  } catch (e) {
    tidyThrew = true;
  }
  check('a failed tidy-up does not fail the run', !tidyThrew, 'it threw');
  failStatusFor = null;

  // ─── Back to the writing path ──────────────────────────────────────────────
  OUT['Check and Prepare'] = prepared;
  const merged = await runCode('Parse and Merge', prepared.map(() => ({
    json: { choices: [{ message: { content: LLM_ANSWER } }] },
  })));
  check('nothing failed to parse',
        merged.every(m => m.json.parse_error === false),
        merged.map(m => m.json.error_reason));

  // ─── 4. The id is derived, so there can only be one record ─────────────────
  const ids = merged.map(m => m.json.object_id);
  check('every record has an id of its own', new Set(ids).size === ids.length, ids);
  check('the id is a UUID',
        ids.every(i => /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
                        .test(i)), ids);
  check('the record is written under that id',
        merged.every(m => m.json.weaviate_object.id === m.json.object_id),
        merged.map(m => [m.json.object_id, m.json.weaviate_object.id]));
  check('the record carries its course',
        merged.every(m => m.json.weaviate_object.properties.course_id ===
                          m.json.course_id),
        merged.map(m => m.json.weaviate_object.properties.course_id));

  // The same pair, computed again, has to give the same id — that is the
  // whole guarantee. Running the two steps again is the test.
  const firstIds = new Map(merged.map(m => [`${m.json.course_id}|${m.json.user_id}`,
                                            m.json.object_id]));
  await runCode('Process Memory', looked.map(l => ({ json: { data: l.data } })));
  const prepared2 = await runCode('Check and Prepare', withMemory.map(i => ({
    json: { data: { Get: { ChatHistory: chatFor(i.json.course_id, i.json.user_id) } } },
  })));
  const merged2 = await runCode('Parse and Merge', prepared2.map(() => ({
    json: { choices: [{ message: { content: LLM_ANSWER } }] },
  })));
  check('a second run derives the same ids',
        merged2.every(m => firstIds.get(`${m.json.course_id}|${m.json.user_id}`) ===
                           m.json.object_id),
        merged2.map(m => [m.json.course_id, m.json.object_id]));
  // The same learner in two courses must not collide, which is the reason
  // the pair and not the learner is what the id is derived from.
  const learnerIds = merged.filter(m => m.json.user_id === LEARNER)
                           .map(m => m.json.object_id);
  check('the same learner has a different record in each course',
        new Set(learnerIds).size === 2, learnerIds);

  // RFC 4122's own example, so the implementation is checked against the
  // standard rather than against itself: uuid5(DNS, "example.com").
  const rfc = await runCode('Parse and Merge', [{ json: { choices: [{ message:
      { content: LLM_ANSWER } }] } }]);
  void rfc;
  const uuid5 = new Function('require', `
    ${code('Parse and Merge').split('const response =')[0]}
    return uuid5;`)(sandboxRequire);
  check('the id function is RFC 4122 version 5',
        uuid5('6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'example.com') ===
          'cfbff0d1-9375-5685-968c-48ce8b15ae17',
        uuid5('6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'example.com'));

  // ─── 5. The ground is cleared before the write ─────────────────────────────
  deleted.length = 0;
  missingIds = new Set(merged.map(m => m.json.object_id));   // first run: nothing there yet
  const cleared = await runCode('Clear previous records', merged);

  check('the derived id is removed before the write',
        merged.every(m => deleted.includes(m.json.object_id)), deleted);
  check('every duplicate is removed too',
        ['dup-newest', 'dup-older', 'dup-oldest'].every(id => deleted.includes(id)),
        deleted);
  check('nothing is deleted twice', new Set(deleted).size === deleted.length, deleted);
  check('a record that is not there is not an error',
        cleared.every(c => c.json.absent >= 1), cleared.map(c => c.json));
  check('the items still carry what the write needs',
        cleared.every(c => c.json.weaviate_object && c.json.object_id),
        cleared.map(c => Object.keys(c.json)));

  // A delete that fails for any other reason must stop the item. Carrying on
  // would write a second record beside one that is still there — the exact
  // bug this shape replaces.
  deleted.length = 0;
  failStatusFor = 'dup-older';
  let threw = false;
  try {
    await runCode('Clear previous records', merged);
  } catch (e) {
    threw = /Could not remove the previous learning record/.test(e.message);
  }
  check('a delete that fails stops the write', threw,
        'the run would continue and write a duplicate');
  failStatusFor = null;

  // ─── 6. What is refused ────────────────────────────────────────────────────
  OUT['Check and Prepare'] = [{ json: { user_id: LEARNER, course_id: '',
                                        stale_ids: [], existing: {},
                                        max_chapter: 1,
                                        last_updated_now: OLD } }];
  const refused = await runCode('Parse and Merge', [{ json: { choices: [{ message:
      { content: LLM_ANSWER } }] } }]);
  check('a record with no course is refused',
        refused[0].json.parse_error === true &&
        refused[0].json.error_reason === 'no_course', refused[0].json);
  check('…and nothing is written for it',
        !refused[0].json.weaviate_object, refused[0].json);

  if (failures.length) {
    console.log('FAILURES:');
    for (const f of failures) console.log('  -', f);
    process.exit(1);
  }
  console.log(
    'All learning-record chain checks passed: the workflow\'s own Code nodes ' +
    'were executed in order over two courses and a learner who is in both — ' +
    'the loop skips a course with no id, produces one item per course and ' +
    'learner, and looks up every record of a pair rather than the first; the ' +
    'cursor and the prior state come from the newest of three duplicates, so ' +
    'nothing is summarised twice; each record\'s id is derived from its pair, ' +
    'is a version-5 UUID that matches RFC 4122\'s own example, is stable ' +
    'across runs and differs between a learner\'s two courses; every existing ' +
    'record including that id is deleted before the write, a missing one is ' +
    'not an error and a delete that fails for another reason stops the item ' +
    'instead of writing a duplicate beside it; a pair with nothing new to ' +
    'summarise is skipped but still loses every copy except the one it is ' +
    'skipping on, and a tidy-up that fails does not turn that into a red run; ' +
    'and a record with no course is refused rather than written.'
  );
})().catch(e => {
  console.error('The chain could not be run:', (e && e.stack) || e);
  process.exit(1);
});
