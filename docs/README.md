# Documentation

This directory documents how this fork works — its project tooling (tests, coverage, linting, CI)
and its own design decisions — and _why_ it's built the way it is. It exists mainly to stop a future
change from silently undoing a non-obvious decision.

- [`security-review.md`](security-review.md) — the audit done before forking upstream, and what it found
- [`testing.md`](testing.md) — test suite, the 80%-per-file branch coverage gate, and the `app.js`/`index.js` split
- [`linting.md`](linting.md) — structured-file linting (JSON/YAML/Markdown/TOML) and pre-commit autofix
- [`ci.md`](ci.md) — the GitHub Actions workflow
- [`media-content.md`](media-content.md) — how image/video responses are shaped as MCP content blocks (`return_images`, `SDNEXT_MAX_IMAGES`, why video is never inlined)

## Quick reference

```bash
npm test              # run the unit test suite
npm run coverage       # run tests with coverage; fails if any src/*.js file is under 80% branch coverage
npm run lint            # check JSON/YAML/Markdown formatting + Markdown rules + TOML
npm run lint:fix        # auto-fix the above
```

A pre-commit hook (husky + lint-staged) runs the `lint:fix` equivalent on staged files automatically —
see [`linting.md`](linting.md).
