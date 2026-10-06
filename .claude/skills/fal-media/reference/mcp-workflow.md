# fal MCP workflow

The fal MCP server (`https://mcp.fal.ai/mcp`, configured in this repo's `.mcp.json`) exposes
these tools. Parameter names are from fal's MCP documentation
(<https://fal.ai/docs/model-apis/mcp>, October 2026). If a call is refused for a parameter
name, re-read that page with `search_docs` rather than guessing.

| Tool | Required | Optional | Use it for |
|---|---|---|---|
| `search_models` | – | `query`, `category`, `limit`, `cursor` | Finding endpoints by keyword or category |
| `recommend_model` | `task` | – | A curated pick for a task ("image-to-video with first and last frame") |
| `get_model_schema` | `endpoint_id` | – | Exact input/output fields, enums, limits. **Before the first call to any endpoint.** |
| `get_pricing` | `endpoint_id` (comma-separated allowed) | – | Unit prices. Results may be cached ~5 min |
| `search_docs` | `query` | – | fal docs and code examples |
| `run_model` | `endpoint_id`, `input` | `expiration_seconds`, `store_payload` | Fast jobs (images). Waits up to ~45 s, then returns `processing` + request id |
| `submit_job` | `endpoint_id`, `input` | `expiration_seconds`, `store_payload` | Anything slow: video, music, batches. Returns a request id immediately |
| `check_job` | `endpoint_id`, `request_id` | `status_url` | Status. Honour `poll_after_seconds` when present |
| `get_job_result` | `endpoint_id`, `request_id` | `response_url` | Output (file URLs) once complete |
| `cancel_job` | `endpoint_id`, `request_id` | `cancel_url` | Stop a queued or running job you no longer need |
| `upload_file` | – | `prepare_upload`, `file_name`, `file_size`, `url`, `data` | Getting local or remote inputs onto fal's CDN |

## The job loop

1. **Estimate and reserve.** `python3 scripts/ledger.py reserve --tag <tag> --endpoint <id> ...`
   (or `--est <dollars>` when you priced it yourself with `get_pricing`). Exit code 2 = over
   the cap: stop and report.
2. **Submit.** `submit_job(endpoint_id, input)`. Write the returned request id to the
   project's job log (e.g. `jobs/<tag>.json`) right away, so it survives a context reset.
3. **Poll.** `check_job(endpoint_id, request_id)`. Wait the `poll_after_seconds` it gives
   (or ~20–30 s for video) between checks. Do other useful work while you wait; submit the
   whole batch before polling any of it.
4. **Fetch.** `get_job_result(endpoint_id, request_id)` → download every output URL to the
   project folder immediately (`curl -L -o gens/<tag>/out_0.mp4 <url>`). fal URLs are not
   a storage system.
5. **Settle.** `ledger.py settle --tag <tag> --status ok --request-id <id>`.
   Blocked by content policy, or failed with an error → `--status refunded` (not billed).

**Never call `submit_job` or `run_model` again to see whether a job finished.** That
creates a second billable job.

## Uploading local files

Model inputs such as `image_url`, `end_image_url`, `image_urls`, `audio_url` need a URL.

1. `upload_file(prepare_upload=true, file_name="K-A2a.png", file_size=<bytes>)`
   (1 byte to 90 MB) → returns `upload_url` and `file_url`.
2. PUT the raw bytes to `upload_url`. **No Authorization header, do not follow redirects,
   keep the signed URL private:**
   ```bash
   curl --fail -X PUT --data-binary @kf/K-A2a.png -H "Content-Type: image/png" "<upload_url>"
   ```
3. Only after a 2xx response, use `file_url` in model inputs.

A public URL can be passed with `upload_file(url=...)`; small data with `upload_file(data=<base64>)`.
Cache uploads: keep a `uploads.json` of `{sha256: file_url}` so the same keyframe is not
uploaded twice.

## Errors and what to do

| Symptom | Meaning | Action |
|---|---|---|
| `content_policy_violation`, `partner_validation_failed`, "may contain likenesses of real people" | Input frame blocked (Seedance likeness filter) | Settle `refunded`. Hide or soften faces (`reference/likeness-filter.md`) and resubmit under a new tag. Identical resubmission can also pass: the filter is random |
| Music prompt refused by a content check | Lyria's checker is non-deterministic | Settle `refunded`; resubmit unchanged once, then reword |
| "Invalid parameters" on a finish call whose input is shaped like ones that succeeded | Transient failure seen on `draft/complete` | Retry once with a new tag; if it fails again, finish the backup take |
| "music_length_ms cannot be used with composition_plan" | ElevenLabs Music: mutually exclusive fields | Drop `music_length_ms` |
| "Reasoning is mandatory for this endpoint" | Some router (LLM/VLM) endpoints require reasoning on | Set `reasoning: true` |
| MCP server returns 401 | The MCP key in `FAL_MCP_KEY` is invalid or expired | Tell the user; they rotate it at fal.ai/dashboard/keys. Fallback: `scripts/falgen.py` with `FAL_KEY` |

## Job log and naming

- Tag format: `<GROUP>_<CLIP>_<kind><n>` — `G3_A6_d1` (draft 1), `G3_A6_r2` (revised
  keyframe, draft 2), `HD_G3_A6_d1` (the 1080p finish of that draft).
- Keep per tag: endpoint, input (with the exact prompt), request id, output paths, and a
  one-line verdict. This is what makes a shot reproducible and a cost auditable.
- Save keyframes as `kf/K-<CLIP>a.png` (first frame) and `kf/K-<CLIP>b.png` (last frame).
