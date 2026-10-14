# Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push to `main`/`master` and
every pull request, as two independent jobs:

| Job    | What it does                                                                                                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test` | Matrix over Node `20.x` and `22.x` (the package's declared minimum, and current LTS). `npm ci`, then `npm run coverage` — fails the build if any `src/**/*.js` file is under 80% branch coverage. |
| `lint` | Single run on Node `22.x`. `npm ci`, then `npm run lint` (Prettier + markdownlint-cli2 + Taplo).                                                                                                  |

Both jobs use `actions/checkout@v4` and `actions/setup-node@v4` with `cache: npm`, and both run
`npm ci` (not `npm install`) so the build fails loudly if `package-lock.json` and `package.json`
ever drift out of sync, rather than silently resolving something different than what's committed.

## Why two jobs instead of one

Splitting `test` and `lint` means they run in parallel and fail independently and legibly in the
GitHub UI — a red "Lint" check tells you immediately it's a formatting problem, not a logic
regression, without having to open the log. The `test` job's matrix over two Node versions exists
because this package declares `"engines": { "node": ">=20" }`; the matrix is what actually verifies
that claim rather than just asserting it in `package.json`.

## Verifying changes to this workflow

There's no cheap way to fully simulate GitHub Actions locally, but every command the workflow runs
is also a local npm script — before pushing a change to `ci.yml` or to anything the workflow
depends on, run the same two commands locally first:

```bash
rm -rf node_modules && npm ci   # matches the CI install step exactly
npm run coverage
npm run lint
```

If those pass locally with a clean `npm ci`, the workflow will almost certainly pass too — the one
thing local runs can't catch is environment drift across Node versions, which is what the matrix is
for.
