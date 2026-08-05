# Tests

```bash
bash tests/run-tests.sh              # everything (~15s)
bash tests/run-tests.sh citation     # only suites matching a name
bash tests/run-tests.sh --list
```

Needs Python 3 and bash. Nothing else — no Docker, no running services, no
network. The first run builds `tests/.venv` from
`content-admin/requirements.txt` and reuses it afterwards.

## What these are for

Every suite here exists because something broke, usually in a way that was
expensive to diagnose. They are regression tests in the literal sense: each
one encodes a failure that actually happened, so it cannot happen twice
unnoticed. A few examples of what is pinned down:

- Flowise's agentflow nodes read credentials from `FLOWISE_CREDENTIAL_ID`,
  not from the `credential` key the templates ship with. Getting this wrong
  makes every agent answer "Missing credentials … set the OPENAI_API_KEY
  environment variable" — whatever provider is configured, which is what
  made it hard to place.
- Flowise's code sandbox sets `process` to `undefined`, so
  `$vars?.X || process.env.X || 'default'` throws instead of reaching the
  default the moment `X` is empty.
- n8n answers 404 both when a webhook is missing *and* when it exists but
  was asked with the wrong method. Reading those the wrong way round
  reports a working system as broken.
- The installer must never print a completion banner for a phase it
  skipped, and must never loop forever when stdin is closed.
- No public URL may be assembled from `${DOMAIN}` in `docker-compose.yml`,
  because that silently drops `SUBDOMAIN_PREFIX`.

## Why plain scripts instead of pytest

Each suite prints one sentence saying what it verified, and `run-tests.sh`
shows those sentences on success. That sentence is the deliverable: it can
be read by someone who wasn't there when the bug was found, which a row of
green dots cannot. The trade-off is deliberate — no fixtures, no
parametrisation, no plugins. If this grows past what plain scripts carry
comfortably, pytest is the obvious next step, but the printed summaries are
worth keeping in some form.

## Conventions

A suite collects failures rather than stopping at the first one, so a single
run shows everything that is wrong:

```python
failures = []

def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")

check("the thing does what it must", actual == expected, f"{actual!r}")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All … checks passed: <what was verified, in one sentence>")
```

Shell suites follow the same shape with a `FAILURES=()` array.

Paths are computed from `__file__` / `BASH_SOURCE`, never hard-coded, so a
clone works at any location.

## Writing a new one

Three things make these suites worth their maintenance:

**Assert on the mechanism, not the symptom.** The credential test checks
that `FLOWISE_CREDENTIAL_ID` carries the id — not merely that "some
credential field is set", which the broken version would also have passed.

**Prove the test fails against the bug.** Before trusting a regression
test, break the code again and watch it go red. Several tests here caught
real mistakes only because that step happened; one silently covered nothing
for a while because it grepped a function with a fixed context window that
stopped reaching the lines it was meant to check.

**Say why in the file.** The comment above a check should record what went
wrong and how it was diagnosed, with a source where one exists (a file and
line in Flowise's or n8n's own repository, at the pinned version). That
context is why the test is defensible later, when someone wonders whether
the assertion is still true.

## Known gap

There is no test for `content-admin/env_file.py`, which is the code that
writes to the real `.env` through a bind mount. Given that a web interface
patching the system's central configuration file is the most sensitive
thing in this codebase, that is the first gap worth closing.
