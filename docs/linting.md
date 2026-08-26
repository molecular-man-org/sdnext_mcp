# Structured-file linting

Three separate tools cover the four structured formats — there's no single tool that does all of
JSON, YAML, Markdown, and TOML well:

| Format   | Tool                                                                                             | Config                                                               |
| -------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| JSON     | [Prettier](https://prettier.io) (format + syntax check)                                          | [`.prettierrc.json`](../.prettierrc.json)                            |
| YAML     | Prettier                                                                                         | same                                                                 |
| Markdown | Prettier (format) + [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2) (rules) | same, plus [`.markdownlint-cli2.jsonc`](../.markdownlint-cli2.jsonc) |
| TOML     | [Taplo](https://taplo.tamasfe.dev) (lint + format)                                               | [`taplo.toml`](../taplo.toml)                                        |

Prettier does double duty for JSON/YAML: since both formats have no real "style rules" beyond valid
syntax and consistent formatting, a failing `prettier --check` already catches both malformed files
and inconsistent style. Markdown gets both a formatter (Prettier, for whitespace/structure) and a
real linter (markdownlint-cli2, for content rules like missing code-fence languages or malformed
tables) because Markdown has actual authoring mistakes that formatting alone won't catch. TOML has no
Prettier support at all, hence Taplo.

All three are explicitly scoped away from `node_modules/` and `coverage/` (see `.prettierignore` and
the `ignores`/`exclude` fields in the other two configs) — linting a lockfile-scale `node_modules`
tree would be both pointless and slow.

```bash
npm run lint              # check everything (prettier --check + markdownlint-cli2 + taplo lint/fmt --check)
npm run lint:fix           # auto-fix everything
npm run lint:json-yaml-md   # just the prettier check
npm run lint:md             # just markdownlint-cli2
npm run lint:toml           # just taplo
```

## Why `MD013` (line-length) is disabled

`.markdownlint-cli2.jsonc` turns off `MD013`. Prettier is configured with `proseWrap: "preserve"`
(it deliberately never rewraps prose paragraphs), so enabling a hard line-length rule in
markdownlint would create a permanent disagreement between the two tools — one wants prose
hard-wrapped at 80 columns, the other refuses to touch it — that would fight on every future edit.
Every other default markdownlint rule stays on.

## A formatting bug Prettier surfaced (and how it was fixed, not suppressed)

Running `prettier --write` on the original README turned `delete-*, browser/*` (two literal
asterisks used as wildcard-glob shorthand in prose) into `delete-_, browser/_`. Prettier's Markdown
formatter interpreted the unmatched `*` characters as malformed emphasis markers and "normalized"
them to underscores — silently corrupting the meaning of the text.

**The fix was in the source, not the tool**: the two glob patterns were wrapped in backticks
(`` `delete-*` ``, `` `browser/*` ``), matching how every other tool-name-ish token on that line was
already formatted. Inside a code span, Prettier treats content as literal and won't reinterpret it as
Markdown syntax. Re-running `prettier --write` afterward is a no-op (idempotent), confirming the fix
is stable.

The general lesson: when introducing an auto-formatter to an existing document, diff every change it
makes before trusting `--write` (let alone a pre-commit hook) to run unattended — a formatter can be
byte-perfect according to its own rules and still be wrong.

## Pre-commit autofix

[Husky](https://typicode.github.io/husky) + [lint-staged](https://github.com/lint-staged/lint-staged)
run the fixers above — and only the fixers, not the test suite — against whatever's actually staged,
on every `git commit`:

- `.husky/pre-commit` → `npx lint-staged`
- [`.lintstagedrc.json`](../.lintstagedrc.json) maps `*.{json,yml,yaml}` → `prettier --write`,
  `*.md` → `prettier --write` then `markdownlint-cli2 --fix`, `*.toml` → `taplo fmt`.

Since lint-staged only ever touches files git has staged, and `node_modules/` is gitignored (so it
can never be staged short of `git add -f`), there's no separate exclusion needed here the way the
`npm run lint` scripts need one.

`package.json`'s `prepare` script is `husky || true` rather than a bare `husky` — the `|| true` is
Husky's own documented recommendation, so that a production-only install (`npm ci --omit=dev`, where
`husky` itself wouldn't be present as a devDependency) doesn't fail outright. It doesn't affect this
repo's own CI, which runs a full `npm ci`.
