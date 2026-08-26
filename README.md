# sdnext-mcp

Model Context Protocol (MCP) server for **SD.Next** (Stable Diffusion Next) WebUI.

It wraps SD.Next's REST API (`/sdapi/v1/*` + `/sdapi/v2/civitai/*`) as **125 MCP tools** so an LLM host (Claude Desktop, DSH, etc.) can drive every SD.Next usage scenario: text-to-image, image-to-image, control-guided generation, upscaling, detailer, LoRA, scripts, captioning/tagging/VQA, model & option management, server introspection, file/gallery, and CivitAI.

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

## Tool families (125 total)

- **Generation** — `sdnext_txt2img`, `sdnext_img2img`, `sdnext_control`, `sdnext_process_image`, `sdnext_process_batch`, `sdnext_detail`, `sdnext_prompt_enhance`
- **Enumerators** — `sdnext_list_samplers` … `sdnext_list_masking` (29 list tools)
- **Image understanding** — `sdnext_caption`, `sdnext_caption_dispatch`, `sdnext_tagger`, `sdnext_vqa`, `sdnext_analyze`
- **Models/options** — refresh/select/load checkpoint, VAE, UNet, LoRA; `sdnext_get_options`/`sdnext_set_options`
- **Server** — `sdnext_status`, `sdnext_progress`, interrupt/skip, restart/shutdown, log, history, storage, GPU
- **Files/Gallery** — `sdnext_upload`, `sdnext_file`, `delete-*`, `browser/*`, `sdnext_png_info`
- **Control processing** — `sdnext_preprocess`, `sdnext_mask`, `sdnext_detect`
- **Moderation** — `sdnext_nudenet`, `sdnext_prompt_lang`, `sdnext_image_guard`, `sdnext_prompt_banned`
- **CivitAI** — search, model/version, download queue, settings, bookmarks, banned, history (27 tools)
- **Meta** — `sdnext_list_tools`, `sdnext_call_api` (generic passthrough), autocomplete file mgmt

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
