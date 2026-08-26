# Image and video responses

How generation tools (`sdnext_txt2img`, `sdnext_img2img`, `sdnext_control`, `sdnext_process_image`,
`sdnext_process_batch`, `sdnext_detail`, `sdnext_video`) shape their MCP responses, and why.

## The problem this replaced

Every tool response used to go through [`registry.js`](../src/registry.js)'s `ok()`, which
`JSON.stringify()`s whatever the handler returns into a single `{ type: 'text', ... }` content
block — including the base64 image bytes embedded inside it. Calling `sdnext_txt2img` for one
1024×1024 image blew past the tool-response token limit outright (267,161 characters), and even
when a response did fit, Claude Desktop's chat UI has no way to render an image that's just a giant
base64 string sitting inside a JSON blob — it only renders images it recognizes as MCP `image`
content blocks. Both problems trace back to the same root cause: images were never emitted as
proper MCP content, only as JSON-embedded text.

## The fix: real MCP content blocks

[`registry.js`](../src/registry.js)'s `ok()` recognizes a `{ __mcpContent: [...] }` marker — mirroring
the existing `__dryRun` marker convention in [`client.js`](../src/client.js) — and passes those content
blocks through unchanged instead of stringifying them:

```js
export function ok(data) {
  if (data && typeof data === 'object' && Array.isArray(data.__mcpContent)) {
    return { content: data.__mcpContent };
  }
  return { content: [{ type: 'text', text: ... }] }; // unchanged path for every other tool
}
```

`imageContent(images)` converts an array of base64 PNG strings into `{ type: 'image', data, mimeType:
'image/png' }` blocks — the shape the MCP SDK's `ImageContentSchema` actually expects (verified
against `node_modules/@modelcontextprotocol/sdk/dist/esm/types.d.ts` before writing this, not
guessed). A generation tool's response is now always: one small `text` block with a JSON summary
(saved paths, seed/sampler info, counts), followed by zero or more `image` blocks — never base64
inside the text.

## `return_images`: deciding what actually gets inlined

[`generation.js`](../src/tools/generation.js)'s `shapeImageResult()` is the shared shaping function
every image-producing tool's handler calls. The default rule: **inline images only when there's
nowhere else to point to.**

```js
function resolveReturnImages(args) {
  return typeof args.return_images === "boolean"
    ? args.return_images
    : !args.save_dir;
}
```

- No `save_dir` → images are inlined by default (nothing else to look at).
- `save_dir` given → images are saved to disk and _not_ inlined by default (the file already exists;
  echoing the same bytes back through the response is pure token cost with no new information).
- `return_images` always overrides the default explicitly in either direction.

This required decoupling two previously-conflated things: SD.Next's own `send_images` (or
`show_extras_results`) request flag, which controls whether _SD.Next_ returns pixels to _us_, from
whether _we_ then relay those pixels to the _MCP client_. The wrapper now force-enables SD.Next's
flag server-side whenever it needs the bytes for either saving or inlining, regardless of what the
caller passed:

```js
const needsBytes = !!args.save_dir || resolveReturnImages(args);
const body = clean({
  ...rest,
  send_images: needsBytes ? true : rest.send_images,
});
```

Without this, `save_dir` could silently fail to save anything if a caller (or a future default
change) ever set `send_images: false` — previously nothing would have caught that.

**Watch out for wrapper-only fields leaking into the SD.Next request body.** `return_images` (and,
for video, `save_dir`/`mp4_ext` handling) must be destructured out of `args` before building the
request body — SD.Next's pydantic models generally ignore unknown fields, but there's no guarantee
every endpoint is configured that leniently, and leaking internal flags into a third-party API call
is sloppy regardless. Every handler does `const { return_images, ...rest } = args` before `clean()`.

## `SDNEXT_MAX_IMAGES`: bounding batch responses

`config.maxImagesInResponse` (env `SDNEXT_MAX_IMAGES`, default 4) existed in
[`config.js`](../src/config.js) since the very first version of this fork but was never read
anywhere — a dead config knob. `shapeImageResult()` now caps how many images get embedded in the
response on a batch generation (`n_iter`/`batch_size` > 1), while still saving _all_ of them to disk
when `save_dir` is given. The response's `note` field says explicitly when images were dropped from
the inline set and why, rather than silently truncating.

## Video: why nothing about it is ever inlined

The MCP SDK has `ImageContentSchema` and `AudioContentSchema` but no video content type (checked the
same `types.d.ts` — no `VideoContent` anywhere). Combined with video files being far larger than
images, `sdnext_video` never inlines the actual video bytes under any circumstance — there's no
`return_images:true` override for the video itself, only for its thumbnail and (in still-frame mode)
its generated frames, which follow the exact same `shapeImageResult`-style inline/cap rules as every
other generation tool.

- `save_dir` given → video and thumbnail are saved to disk (`sdnext_video_<ts>.mp4`,
  `sdnext_thumbnail_<ts>.png`); the response reports the paths.
- No `save_dir` → the video is generated but not persisted anywhere reachable; the response says so
  explicitly (`note: "Video bytes were generated but not saved — pass save_dir..."`) rather than
  just quietly discarding it.
- `sdnext_video`'s own `save_dir` field is optional (matching every other generation tool), but
  **`sdnext_video_file`'s `save_dir` is required, not optional** — there's no image-content-block
  fallback for video the way there is for `sdnext_video`'s thumbnail, so there's no sensible default
  behavior if the caller doesn't say where to put the bytes. Zod rejects the call up front with a
  clear schema error instead of the handler silently no-op'ing.

## `client.getBinary()`: a second HTTP path for non-JSON responses

[`client.js`](../src/client.js)'s existing `request()` always does `await res.text()` and
`JSON.parse()`s the result — correct for every `/sdapi/v1/*` JSON endpoint, but `GET
/sdapi/v1/video/file` serves a raw file (its OpenAPI schema is `{}`, a FastAPI `FileResponse` with a
dynamic content-type). Reading binary content through `res.text()` decodes it as UTF-8, which
corrupts anything that isn't valid UTF-8 — i.e., every actual video/image file. `getBinary()` is a
separate, additive function that reads via `res.arrayBuffer()` and returns `{ base64 }` instead,
used by `sdnext_video_file`. It mirrors `request()`'s dry-run/auth/timeout/error-handling behavior as
closely as possible so it isn't a second thing to reason about — the only real difference is how the
response body gets read.

This same `res.text()`-corrupts-binary issue technically also affects the pre-existing
`sdnext_file`/`sdnext_browser_thumb`/`sdnext_network_thumb` tools in `files.js`, which have always
served binary content through the JSON-oriented `request()` path. That predates this work and wasn't
in scope to fix here — `getBinary()` exists so `sdnext_video_file` doesn't have the same bug from
day one, not as a fix for the older tools. Worth revisiting.
