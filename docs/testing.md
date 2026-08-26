# Testing

- Framework: [Vitest](https://vitest.dev) + `@vitest/coverage-v8`, configured in
  [`vitest.config.js`](../vitest.config.js).
- 132 tests across [`test/`](../test/), one file per `src/` module (`test/tools/*.test.js` mirrors
  `src/tools/*.js`).
- Every `src/**/*.js` file (except `src/index.js`, see below) must hold **80% branch coverage on
  its own** — not just in aggregate. This is enforced by `coverage.thresholds.perFile: true` in
  `vitest.config.js`, and by CI (see [`ci.md`](ci.md)).

```bash
npm test              # run once
npm run test:watch     # watch mode
npm run coverage        # run + enforce the per-file 80% branch gate
```

## Why `vitest` is pinned to `3.2.7`, not `latest`

This was **not** an arbitrary choice — `@vitest/coverage-v8@4.1.11` (the stable release at the time)
has a reproducible bug: when multiple test files in the same run import overlapping source modules,
some of those modules vanish from the coverage report entirely. Not "0% coverage" — genuinely absent
from the table, as if the file were never included.

Reproduced directly: `config.js`, `image.js`, and `registry.js` (each covered by their own dedicated
test file, and each also imported indirectly by other tested modules) disappeared from the report
under v4.1.11 as soon as more than one of their test files ran in the same process — regardless of
`pool`/worker settings. Downgrading to `3.2.7` fixed it immediately, with no other changes.

**Do not upgrade `vitest`/`@vitest/coverage-v8` past `3.x` without re-verifying this.** A quick check:
run the full suite (`npm run coverage`) and confirm every file in `src/` and `src/tools/` actually
appears in the report table — if one goes missing instead of showing a coverage percentage, that's
this bug, not a fluke.

(Separately: `@vitest/coverage-v8`'s dependency chain on `3.x` pulls in `glob@10.5.0`, which prints a
deprecation warning on `npm ci`. That's not a reason to upgrade — see below.)

### A note on the `glob` deprecation warning

`npm ci` prints:

```text
npm warn deprecated glob@10.5.0: Old versions of glob are not supported, and contain widely
publicized security vulnerabilities, which have been fixed in the current version.
```

This is dev-tooling-only (`@vitest/coverage-v8` → `test-exclude@7.0.2` → `glob@10.5.0`; never shipped
in the actual server, whose only runtime deps are `@modelcontextprotocol/sdk` and `zod`), and it is
**not an active vulnerability**: `npm audit` reports 0 vulnerabilities across all 344 dependencies,
and `glob@10.5.0`'s own `minimatch` dependency (`^9.0.4`) is a modern, unaffected version. The
warning text is glob's maintainer's standard deprecation boilerplate, stamped on every old major
version of his packages — not a specific CVE notice for this version.

Every available way to silence it is worse than leaving it:

- Forcing `glob` to a newer major via `overrides` risks breaking `test-exclude@7.0.2`'s internal
  usage of glob's API, which was written against v10 — a 3-major jump is not guaranteed compatible,
  and a silent behavior change in file inclusion/exclusion would be worse than a warning.
- Upgrading `@vitest/coverage-v8` to `4.x` (the only version that drops `glob` from its dependency
  tree entirely) reintroduces the coverage-attribution bug described above.
- No `3.x` patch release is available that bumps `test-exclude` — every `3.2.x` version pins
  `test-exclude: ^7.0.1`.

Revisit this once the upstream `vitest` v4 coverage bug is fixed and confirmed gone.

## Why there's both `src/app.js` and `src/index.js`

`src/index.js` is the `bin`/`main` entry point — intentionally two lines:

```js
import { main } from "./app.js";
await main();
```

Everything testable (`buildServer`, `runStdio`, `runHttp`, `main`) lives in [`src/app.js`](../src/app.js)
instead. This split exists because of another Vite-transform bug: a file combining a top-level
`await` with `import.meta.url` (used for the old "is this the CLI entry point" guard) crashed Vite's
SSR transform inside Vitest with `Cannot split a chunk that has already been edited` — reproducible
every time that file was imported from a test, with or without coverage enabled.

Rather than work around a transform bug in the one file that actually needs to run unconditionally
at process start, the fix was architectural: move all the logic to `app.js` (plain functions, no
`import.meta`, fully testable) and reduce `index.js` to a two-line bootstrap that nothing ever
imports — only spawned as a subprocess (`node src/index.js`, via `bin`, `npm start`, and
`scripts/smoke.mjs`). `src/index.js` is excluded from the coverage requirement in `vitest.config.js`
for the same reason: it can't be exercised in-process, only by actually running it, which
`scripts/smoke.mjs` already does as a manual smoke test.

If you need to import `buildServer` or similar from a script, import it from `app.js`, not
`index.js` — see [`scripts/dump_tools.mjs`](../scripts/dump_tools.mjs) for the pattern.

## Test harness pattern

[`test/harness.js`](../test/harness.js) provides a fake MCP server double for exercising
`tools/*.js`'s `register()` functions without a real `McpServer`:

```js
const server = fakeServer();
someToolModule.register(server);
const result = await callTool(server, "sdnext_some_tool", { arg: 1 });
resultJson(result); // parses the ok()-wrapped JSON payload
```

Every `test/tools/*.test.js` file follows the same shape:

```js
vi.mock("../../src/client.js", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
  };
});
```

This keeps `clean()`/`apiUrl()`/`SdNextError` real (they have their own dedicated tests in
`client.test.js`) while mocking only the actual HTTP-calling `client` object, so each tool test
verifies request shaping and response handling without hitting the network.

**Do not use `vi.resetModules()` + repeated dynamic `import()` of the same file within one test
file.** This was tried for `config.js` (to test env-var-driven branches with different
`process.env` values per scenario) and it broke V8 coverage attribution for that file — not a
correctness bug, a reporting bug: the tests still pass, but the file's coverage numbers become
unreliable or the file disappears from the report (see the vitest version note above; this may be
the same underlying class of bug, encountered independently of the version issue). The working
pattern instead: one scenario per test _file_ (Vitest gives each file its own fresh module
registry already), each doing a single `beforeAll`-scoped dynamic import — see
`test/config.env-defaults.test.js` and `test/config.env-overrides.test.js`.

## Dead branches removed rather than "tested"

A few helper functions had a conditional branch that no call site in that file ever actually
triggered — e.g. `models.js`'s `post()` helper accepted an `opts.destructive` flag that nothing
called it with, and `civitai.js` had the same pattern. Writing a test to hit those branches wasn't
possible without either modifying the source to actually use them (a behavior change to tool safety
annotations, out of scope for a testing/CI task) or exporting a private helper purely so a test could
poke at it. Since `grep` confirmed no call site anywhere in the file used the dead parameter, the
parameter was deleted instead — the coverage tool was correctly flagging genuinely unreachable code,
not a testing gap. If you find yourself unable to reach a branch no matter what args you pass through
the public `register(server)` API, check whether it's actually reachable before writing an awkward
test for it.
