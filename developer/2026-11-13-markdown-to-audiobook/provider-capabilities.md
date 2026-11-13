# Provider capability contract

Reviewed 2026-11-13 for the approved `markdown-to-audiobook` stories and
tickets. This report separates current public documentation, account evidence
already captured during story planning, and live probe results.

## Capability summary

| Area | Verified contract | Evidence and limits |
| --- | --- | --- |
| OpenAI annotation | The exact public model ID is `gpt-6-luna`. Use `POST /v1/responses` with `model: "gpt-6-luna"`, `reasoning: {"effort":"medium"}`, direct text input, and `text.format` set to strict `json_schema`. The model page says Structured Outputs and Responses are supported. | [GPT-6 Luna model reference](https://developers.openai.com/api/docs/models/gpt-6-luna), [Responses create reference](https://developers.openai.com/api/reference/resources/responses/methods/create), [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses). The account was not queried, so this verifies the public contract, not this key's access. |
| Default speech model | `eleven_v4_turbo` is a documented model ID and uses the Text to Dialogue WebSocket: `wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`. Set `model_id=eleven_v4_turbo` and `output_format=pcm_24000` in the handshake. The first frame registers the voice; later `inputs` carry text and that voice ID. | [Models reference](https://elevenlabs.io/docs/overview/models/), [Text to Dialogue WebSocket reference](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket). No live handshake or synthesis was made. |
| Standard v4 speech | `eleven_v4` is documented through the synchronous REST endpoint `POST /v1/text-to-speech/{voice_id}`. Send `text`, `model_id: "eleven_v4"`, and optional `language_code` in JSON; request a format such as `pcm_24000` in the query. The response is the generated audio file. | [Create speech reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [ElevenAPI overview and v4 example](https://elevenlabs.io/docs/overview/intro). No live request was made. |
| Voice lookup | `GET /v2/voices` supports `search`, `voice_ids`, `page_size`, `next_page_token`, and `include_live_moderated=false`. Continue while `has_more` is true and pass the returned token. A voice's `sharing.live_moderation_enabled` field is the eligibility fact to validate. | [List voices reference](https://elevenlabs.io/docs/api-reference/voices/search). The earlier account lookup recorded in [the approved story notes](user-stories.md#notes) found the specified Dahlia voice. It is not a fresh lookup from this ticket. |
| Supported languages | The v4 family documents 90+ languages and codes. The complete published code list is below. Both REST and Text to Dialogue requests accept `language_code`; the API docs warn that an unsupported code is ignored, so the application must reject unsupported input before synthesis. | [Models and language codes](https://elevenlabs.io/docs/overview/models/), [REST request fields](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [WebSocket query fields](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket). Account-specific model metadata can be read from `GET /v1/models`. |
| Per-request character limits | Static model documentation lists `eleven_v4` at 10,000 characters. It does not publish a separate `eleven_v4_turbo` limit. `GET /v1/models` supplies `maximum_text_length_per_request` per model; use that field for the authenticated account. | [Character-limit table](https://elevenlabs.io/docs/overview/models/#character-limits), [List models response schema](https://elevenlabs.io/docs/api-reference/models/list). The account-specific value was not retrieved. |
| Native lossless format | ElevenLabs documents API PCM output at 8, 16, 22.05, and 24 kHz on Free, Starter, and Creator plans; 44.1 kHz PCM is listed for Pro, Scale, and Business. Both relevant API shapes accept an `output_format`; `pcm_24000` is the proposed format. | [Supported audio formats](https://elevenlabs.io/docs/help-center/troubleshooting/what-audio-formats-do-you-support), [REST format parameter](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [WebSocket format parameter](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket). Docs do not specify raw PCM sample width, channel count, or byte order, and this account's entitlement was not checked. |
| Published narration rates | v4 Turbo is listed at $0.04 per 1,000 characters; v4 is $0.08 per 1,000 characters. Thus the approved 1,000-character maximum probe for each speech model is within the $0.12 total narration-value cap, before any account-specific allowance. | [ElevenLabs API pricing](https://elevenlabs.io/pricing/api). These are published estimates, not an account invoice or remaining-credit check. |

## OpenAI request shape

The documented Responses API shape for Luna is:

```http
POST https://api.openai.com/v1/responses
Authorization: Bearer $OPENAI_API_KEY
Content-Type: application/json
```

```json
{
  "model": "gpt-6-luna",
  "reasoning": {"effort": "medium"},
  "input": "<chapter text and fixed instructions>",
  "text": {
    "format": {
      "type": "json_schema",
      "name": "chapter_insertions",
      "strict": true,
      "schema": "<the approved insertion schema>"
    }
  }
}
```

The schema above is a placeholder, not a second contract definition; T-02/T-05
own its exact fields. Consume a completed structured result only after checking
the response status and refusal/incomplete cases. Schema adherence does not
validate source anchors or other application rules.

The model reference documents the exact ID and `medium` effort. The OpenAI
reasoning guide also says Luna defaults to `medium`, but the request should set
it explicitly to preserve the approved behavior. The model supports Structured
Outputs; use the Responses API `text.format` JSON Schema form rather than
Chat Completions function calling, whose Luna support has a separate effort
restriction.

## ElevenLabs request and stream contracts

### Turbo Text to Dialogue WebSocket

The public protocol shape for a one-voice session is:

```text
WSS wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input
  ?model_id=eleven_v4_turbo&output_format=pcm_24000
Header: xi-api-key: <key>
```

```json
{"voices": ["<voice_id>"]}
{"inputs": [{"text": "A short, non-sensitive sample.", "voice_id": "<voice_id>"}]}
{"close_socket": true}
```

The documented server sends base64 audio chunks, then a final-audio-for-turn
event; closing the socket flushes pending text, sends a final event, and closes
the connection. Error payloads arrive before a WebSocket close code. The
protocol buffers until it has roughly 40 characters and 8 words, unless a
`flush` is sent. It times out after 20 seconds without a client message; a
`keep_alive` frame resets that timer without generating speech. v4 Turbo allows
one registered voice per connection. The WebSocket's `new_turn` describes
turn/prosody boundaries; the docs do not establish that it provides the same
cross-request continuity as the REST request-ID fields below.

### Standard v4 REST

The documented request is:

```http
POST https://api.elevenlabs.io/v1/text-to-speech/<voice_id>?output_format=pcm_24000
xi-api-key: <key>
Content-Type: application/json
```

```json
{
  "text": "A short, non-sensitive sample.",
  "model_id": "eleven_v4",
  "language_code": "en"
}
```

This route returns audio bytes. Its request schema documents `previous_text`,
`next_text`, and up to three `previous_request_ids` / `next_request_ids` for
continuity, with best results when the same model is used. The docs describe
these fields but this account's continuity behavior was not exercised.

### Audio format and limits

`pcm_24000` is raw PCM, not a WAV container; wrapping it in WAV requires correct
format metadata. The published format table establishes the sample-rate option
and its plan availability, but not sample width, channel count, or endianness.
Do not claim a fully specified WAV layout from the format string alone.

The static limit table gives `eleven_v4` 10,000 submitted characters. For
`eleven_v4_turbo`, the page identifies the model and transport but omits a
character limit. The account model list exposes the authoritative
`maximum_text_length_per_request` field, so the application should obtain and
validate the selected model's limit instead of assuming the v4 value also
applies to Turbo. The older per-plan maximum-character fields are marked
deprecated and not enforced.

The v4 language-code list published by ElevenLabs is:

```text
af, am, ar, hy, as, ast, az, be, bn, bs, bg, my, yue, ca, ceb, hr, cs, da,
nl, en, et, fil, fi, fr, ff, gl, ka, de, el, gu, ha, he, hi, hu, is, id,
it, ja, jv, kam, kn, kk, ko, ky, lo, lv, ln, lt, lg, lb, mk, ms, ml, mt,
zh, mi, mr, mn, ne, no, oc, or, ps, fa, pl, pt, pa, ro, ru, sr, sn, sd,
sk, sl, so, ckb, es, sw, sv, tg, ta, te, th, tr, uk, ur, uz, vi, cy, wo,
zu
```

The overview describes ISO 639-1 codes for most languages and ISO 639-3 codes
for `ast`, `yue`, `ceb`, `fil`, `kam`, and `ckb`. The endpoint schema's
`language_code` description says ISO 639-1 while the model list includes those
three-letter codes. Use the model catalogue's language IDs for the actual
selected model; do not infer API behavior for those six codes from the generic
field description alone.

## Existing account evidence

The approved story notes record a read-only `GET /v2/voices` observation from
2026-11-13: one exact Dahlia match, `Dahlia - Vintage Romantic Hollywood Star`,
ID `JaagUurP1dmW3WscoJ79`. The record says it was a non-legacy professional
voice, its `sharing.live_moderation_enabled` value was false, and its
`high_quality_base_model_ids` included both `eleven_v4` and
`eleven_v4_turbo`. This evidence supports the approved default and model
eligibility at that earlier query time. Revalidate the voice before synthesis;
the catalogue is mutable.

## Probe status and remaining verification

No live provider calls were made. `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`
were absent from the process environment; the checkout `.env` and
`~/.audiobook.env` were also absent. Therefore the authorized one-probe-per-
speech-model allowance was not consumed, there is no probe usage to report, and
no audio artifact is retained. No secrets were requested or printed.

The live requirements remain explicitly unverified: current account access to
Luna and both ElevenLabs speech models; the Turbo account-specific character
limit; actual 24 kHz PCM acceptance on both speech transports; raw PCM sample
width, channel count, and byte order; stream completion/error behavior against
the account; continuity behavior; and a listenable sample. A future authorized
probe can cover each ElevenLabs speech model once, using no more than 1,000
non-sensitive submitted characters each and at most $0.12 total published
narration value. Do not automatically retry an uncertain paid request.
