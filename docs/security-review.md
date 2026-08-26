# Security review (pre-fork audit)

Before forking [`Congenital/sdnext_mcp`](https://github.com/Congenital/sdnext_mcp), the full source
tree was read end to end (all of `src/`, ~1,554 lines) looking for anything an MCP server shouldn't
be doing: shelling out, unexpected outbound network calls, credential exfiltration, obfuscation, or
supply-chain red flags.

## Findings

**No malicious code found.** Specifically:

- No `eval`, `new Function`, `child_process`, or shell-out anywhere in `src/`.
- The only outbound HTTP calls go to `SDNEXT_BASE_URL` (defaults to `127.0.0.1:7860`, i.e. the
  user's own locally-run SD.Next instance) — nothing is hardcoded to a third-party host.
- Auth headers (API key / basic auth) are only ever attached to requests against that same
  configured `baseUrl`.
- Only two runtime dependencies: `@modelcontextprotocol/sdk` and `zod`, both mainstream. Their
  transitive deps (hono, express, ajv, cors, etc.) are all well-known packages, no typosquats.
- Single commit, single author at the time of the audit — a small, fresh project with no long
  track record, but nothing indicating compromise either.

## Things that were fixed rather than just noted

1. **`node_modules/` was committed to the repo** (3,376 tracked files). This means what a `git clone`
   gave you was whatever was bundled at commit time, not something freshly resolved and verified by
   npm's own integrity checks. Fixed by untracking it (`.gitignore`) and reinstalling fresh.
2. **`package-lock.json` resolved packages from `registry.npmmirror.com`** (Alibaba's npm mirror)
   instead of `registry.npmjs.org`. Not inherently malicious — common for developers in China — but
   it means the lockfile wasn't generated against the official registry. Fixed by deleting the
   lockfile and `node_modules`, then running `npm install` fresh with `npm config get registry`
   pointed at `https://registry.npmjs.org/`.

Both fixes landed in the first commit on this fork
([`c851880`](https://github.com/THE-MOLECULAR-MAN/sdnext_mcp/commit/c851880)), verified afterward with
`npm run check` and `node scripts/smoke.mjs` (a dry-run stdio smoke test — see
[`testing.md`](testing.md)) before anything else was built on top.

## Non-findings worth knowing about anyway

- Two tools are destructive by design (`sdnext_shutdown`, `sdnext_restart`, plus file-delete tools).
  Expected for a full API wrapper, but your MCP client's tool-permission settings are the actual
  trust boundary here, not this code.
- `sdnext_call_api` is a generic REST passthrough to any SD.Next endpoint — also by design. The real
  attack surface is "whatever your SD.Next server exposes," which is governed by your SD.Next
  instance's own auth config (`--api-auth user:pass`), not this MCP wrapper.

## Ongoing dependency hygiene

See [`testing.md`](testing.md#a-note-on-the-glob-deprecation-warning) for why the `glob@10.5.0`
deprecation warning from `npm ci` is left alone rather than "fixed."
