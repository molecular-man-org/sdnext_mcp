# sdnext-mcp

Model Context Protocol (MCP) server for **SD.Next** (Stable Diffusion Next) WebUI.

It wraps SD.Next's REST API (`/sdapi/v1/*` + `/sdapi/v2/civitai/*`) as **128 MCP tools** so an LLM host (Claude Desktop, DSH, etc.) can drive every SD.Next usage scenario: text-to-image, image-to-image, control-guided generation, video generation, upscaling, detailer, LoRA, scripts, captioning/tagging/VQA, model & option management, server introspection, file/gallery, and CivitAI.

> **This is a thin HTTP client.** It does not embed Python and does not launch the WebUI. You must run SD.Next yourself with `--api --listen`; the MCP server just talks to it over HTTP.

The core reason this exists: SD.Next's request parameters **differ from A1111** (modular guidance, 1024 default canvas, built-in YOLO detailer, schedulers as a separate axis, UNet/DiT, etc.), so A1111 MCP code does not work against it. Every tool here is modeled on SD.Next's actual pydantic request fields.

## Requirements

- Node.js ≥ 18 (uses global `fetch`, `FormData`, `AbortSignal.timeout`)
- A running SD.Next instance started with `--api --listen` (add `--api-auth user:pass` to enable auth)

## Install

```bash
cd sdnext_mcp
npm install          # installs @modelcontextprotocol/sdk + zod
```

## Run

Default is **stdio** transport (spawned by your MCP host):

```bash
# in your MCP host config
{
  "mcpServers": {
    "sdnext": {
      "command": "node",
      "args": ["/abs/path/to/sdnext_mcp/src/index.js"],
      "env": {
        "SDNEXT_BASE_URL": "http://127.0.0.1:7860",
        "SDNEXT_USERNAME": "user",
        "SDNEXT_PASSWORD": "pass"
      }
    }
  }
}
```

Or run the **Streamable HTTP** transport directly:

```bash
SDNEXT_MCP_TRANSPORT=http SDNEXT_MCP_PORT=8787 node src/index.js
# → http://127.0.0.1:8787/mcp
```

## Configuration (env vars)

| Var                                   | Default                 | Meaning                                |
| ------------------------------------- | ----------------------- | -------------------------------------- |
| `SDNEXT_BASE_URL`                     | `http://127.0.0.1:7860` | WebUI address                          |
| `SDNEXT_API_PATH`                     | `/sdapi/v1`             | v1 prefix                              |
| `SDNEXT_API_KEY`                      | —                       | Bearer token (highest priority)        |
| `SDNEXT_USERNAME` / `SDNEXT_PASSWORD` | —                       | HTTP Basic auth                        |
| `SDNEXT_TIMEOUT_MS`                   | `300000`                | Request timeout (generation is slow)   |
| `SDNEXT_MCP_TRANSPORT`                | `stdio`                 | `stdio` or `http`                      |
| `SDNEXT_MCP_HOST` / `SDNEXT_MCP_PORT` | `127.0.0.1` / `8787`    | HTTP transport bind                    |
| `SDNEXT_MCP_DRY_RUN`                  | `false`                 | `1` = print requests, don't send       |
| `SDNEXT_SAVE_DIR`                     | —                       | Default dir for `save_dir` disk writes |

## Logs

The server writes no log files. It prints a few lines to standard error (stderr) and nothing else:

- **What:** a start-up line (transport, SD.Next address, dry-run flag) and, when a request on the HTTP transport fails, one error line. That error line can include the text of an error that SD.Next returned. Passwords and API keys are never printed.
- **Where:** the process's stderr only. The server keeps nothing itself, so it has no retention time and nothing of its own to delete: a line lives as long as whatever started the server keeps it (for example your MCP host's log, a terminal, or a service manager's journal). Set that program's log retention if you need a fixed time.
- **Who can read it:** whoever can read the stderr of the process, normally the person who started it and the administrators of that machine.
- **SD.Next's own log:** the `sdnext_log` tool reads the log of the SD.Next server you point this at; how long that log is kept is set by SD.Next, not here.
- **CI logs:** the GitHub Actions logs of this repository are kept by GitHub for its default 90 days and can be read by people with access to the repository.

## Tool families (128 total)

- **Generation** — `sdnext_txt2img`, `sdnext_img2img`, `sdnext_control`, `sdnext_process_image`, `sdnext_process_batch`, `sdnext_detail`, `sdnext_video`, `sdnext_prompt_enhance`
- **Enumerators** — `sdnext_list_samplers` … `sdnext_list_video_models` (30 list tools)
- **Image understanding** — `sdnext_caption`, `sdnext_caption_dispatch`, `sdnext_tagger`, `sdnext_vqa`, `sdnext_analyze`
- **Models/options** — refresh/select/load checkpoint, VAE, UNet, LoRA; `sdnext_get_options`/`sdnext_set_options`
- **Server** — `sdnext_status`, `sdnext_progress`, interrupt/skip, restart/shutdown, log, history, storage, GPU
- **Files/Gallery** — `sdnext_upload`, `sdnext_file`, `sdnext_video_file`, `delete-*`, `browser/*`, `sdnext_png_info`
- **Control processing** — `sdnext_preprocess`, `sdnext_mask`, `sdnext_detect`
- **Moderation** — `sdnext_nudenet`, `sdnext_prompt_lang`, `sdnext_image_guard`, `sdnext_prompt_banned`
- **CivitAI** — search, model/version, download queue, settings, bookmarks, banned, history (27 tools)
- **Meta** — `sdnext_list_tools`, `sdnext_call_api` (generic passthrough), autocomplete file mgmt

Generation tools that produce images/video return a small JSON summary plus the images as native MCP
image content (capped at `SDNEXT_MAX_IMAGES`, inlined unless `save_dir` is set) — see
[`docs/media-content.md`](docs/media-content.md) for the `return_images`/`save_dir` behavior. Video bytes are
never inlined (no MCP video content type, and files are far too large); pass `save_dir` on
`sdnext_video` to save the `.mp4` and thumbnail to disk, or fetch a previously generated one with
`sdnext_video_file`.

Run `sdnext_list_tools` at runtime for the full catalog, or `node scripts/dump_tools.mjs`.

## Typical workflow

```text
sdnext_list_checkpoints   → pick a model
sdnext_list_samplers      → pick a sampler
sdnext_txt2img { prompt, sd_model_checkpoint, sampler_name, width:1024, height:1024, steps:20 }
sdnext_progress           → poll while it runs
sdnext_caption / sdnext_tagger → understand a result
```

Images are raw base64 PNG in and out (no `data:` prefix); `"upload:<id>"` refs are accepted where an image is an input. Pass `save_dir` on any generation tool to also write the result to disk.

## Scripts

```bash
npm run check          # syntax-check the entry
node scripts/smoke.mjs # DRY_RUN stdio smoke test (no live server needed)
node scripts/dump_tools.mjs  # export the tool catalog
```

## Testing & linting

```bash
npm test              # run the unit test suite
npm run coverage       # run tests with coverage; fails if any src/*.js file is under 80% branch coverage
npm run lint            # check JSON/YAML/Markdown formatting + Markdown rules + TOML
npm run lint:fix        # auto-fix the above
```

CI (`.github/workflows/ci.yml`) runs `npm run coverage` (per-file branch coverage gate) and `npm run lint`
on every push/PR. A pre-commit hook (husky + lint-staged) auto-fixes JSON/YAML/Markdown/TOML formatting
on staged files before each commit.

## Layout

See `../framework/` for the architecture documentation (API facts in `01-sdnext-api/`, design decisions in `02-mcp-design/`). Source under `src/`: `config.js`, `client.js`, `image.js`, `registry.js`, `app.js` (builds the server and its transports), `index.js` (tiny CLI bootstrap), and `tools/` (one file per domain).
