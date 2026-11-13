# Provider capability contract

Reviewed 2026-11-13 for the approved `markdown-to-audiobook` stories and
tickets. This report separates current public documentation, account evidence
already captured during story planning, and live probe results.

## Capability summary

| Area | Verified contract | Evidence and limits |
| --- | --- | --- |
| OpenAI annotation | The exact public model ID is `gpt-6-luna`. Use `POST /v1/responses` with `model: "gpt-6-luna"`, `reasoning: {"effort":"medium"}`, direct text input, and `text.format` set to strict `json_schema`. The model page says Structured Outputs and Responses are supported. | [GPT-6 Luna model reference](https://developers.openai.com/api/docs/models/gpt-6-luna), [Responses create reference](https://developers.openai.com/api/reference/resources/responses/methods/create), [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), [retained account evidence](provider-probes/account-evidence.json). The authenticated `GET /v1/models/gpt-6-luna` returned 200 with the expected ID. No OpenAI generation request was made. |
| Default speech model | `eleven_v4_turbo` is a documented model ID and uses the Text to Dialogue WebSocket: `wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`. Set `model_id=eleven_v4_turbo` and `output_format=pcm_24000` in the handshake. The first frame registers the voice; later `inputs` carry text and that voice ID. | [Models reference](https://elevenlabs.io/docs/overview/models/), [Text to Dialogue WebSocket reference](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket), [one-shot probe metadata](provider-probes/eleven_v4_turbo.json). The probe completed and emitted final audio markers. |
| Standard v4 speech | `eleven_v4` is documented through the synchronous REST endpoint `POST /v1/text-to-speech/{voice_id}`. Send `text`, `model_id: "eleven_v4"`, and optional `language_code` in JSON; request a format such as `pcm_24000` in the query. The response is the generated audio file. | [Create speech reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [ElevenAPI overview and v4 example](https://elevenlabs.io/docs/overview/intro), [one-shot probe metadata](provider-probes/eleven_v4.json). The probe returned HTTP 200 with PCM bytes. |
| Voice lookup | `GET /v2/voices` supports `search`, `voice_ids`, `page_size`, `next_page_token`, and `include_live_moderated=false`. Continue while `has_more` is true and pass the returned token. A voice's `sharing.live_moderation_enabled` field is the eligibility fact to validate. | [List voices reference](https://elevenlabs.io/docs/api-reference/voices/search), [retained account evidence](provider-probes/account-evidence.json). The fresh filtered lookup returned one Dahlia match with moderation disabled and both model IDs in `high_quality_base_model_ids`, consistent with [the approved story notes](user-stories.md#notes). |
| Supported languages | The v4 family documents 90+ languages and codes. The authenticated model list returned the same 94 language IDs for `eleven_v4` and `eleven_v4_turbo`; it additionally included `ga`, `ny`, and `yo`, which are absent from the static v4 list. Both REST and Text to Dialogue requests accept `language_code`; unsupported codes are ignored by the provider, so the application must reject them before synthesis. | [Static model language list](https://elevenlabs.io/docs/overview/models/), [REST request fields](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [WebSocket query fields](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket), [retained account evidence](provider-probes/account-evidence.json). |
| Per-request character limits | Static model documentation lists `eleven_v4` at 10,000 characters. The authenticated model list confirmed `maximum_text_length_per_request: 10000` for both models, with `can_do_text_to_speech: true` and `requires_alpha_access: false`. Turbo's limit is no longer unknown for this account. | [Character-limit table](https://elevenlabs.io/docs/overview/models/#character-limits), [List models response schema](https://elevenlabs.io/docs/api-reference/models/list), [retained account evidence](provider-probes/account-evidence.json). |
| Native lossless format | The account accepted `pcm_24000` for each model. REST returned `audio/pcm`; neither response contained a WAV header. Official docs specify PCM as S16LE at 16-bit depth, and describe `pcm_*` output as mono. A 24 kHz, 16-bit, mono WAV wrapper was derived for listening; sample rate is selected by the request format, and channel count is inferred from the documented mono contract and absence of stereo effects, not reported in the response body. | [PCM S16LE and sample-rate documentation](https://elevenlabs.io/docs/overview/capabilities/text-to-speech), [plan availability](https://elevenlabs.io/docs/help-center/troubleshooting/what-audio-formats-do-you-support), [REST format parameter](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [WebSocket format parameter](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket), [retained probes](provider-probes/). |
| Published narration rates | v4 Turbo is listed at $0.04 per 1,000 characters; v4 is $0.08 per 1,000 characters. Thus the approved 1,000-character maximum probe for each speech model is within the $0.12 total narration-value cap, before any account-specific allowance. | [ElevenLabs API pricing](https://elevenlabs.io/pricing/api). These are published estimates, not an account invoice or remaining-credit check. |

## OpenAI request shape

The documented Responses API shape for Luna is shown below. It is a request
template: `schema` must be a JSON Schema object supplied by T-02/T-05.

```http
POST https://api.openai.com/v1/responses
Authorization: Bearer $OPENAI_API_KEY
Content-Type: application/json
```

```jsonc
{
  "model": "gpt-6-luna",
  "reasoning": {"effort": "medium"},
  "input": "<chapter text and fixed instructions>",
  "text": {
    "format": {
      "type": "json_schema",
      "name": "chapter_insertions",
      "strict": true,
      "schema": {
        // Exact property schemas are defined by T-02/T-05.
      }
    }
  }
}
```

The abbreviated schema above is not a second contract definition; T-02/T-05
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

The static limit table gives `eleven_v4` 10,000 submitted characters and omits
Turbo. The authenticated account model list returned
`maximum_text_length_per_request=10000` for both selected models. Use that
field for the selected model at runtime instead of assuming the static v4
limit applies to Turbo for every account. The older per-plan maximum-character
fields are marked deprecated and not enforced.

The authenticated `GET /v1/models` returned the same 94 language IDs for both
selected speech models. That account list includes `ga`, `ny`, and `yo` beyond
the codes on the static v4 page. The model account response is retained in
[account-evidence.json](provider-probes/account-evidence.json). Its full code
list is:

```text
af, am, ar, hy, as, ast, az, be, bn, bs, bg, my, yue, ca, ceb, ny, hr, cs,
da, nl, en, et, fil, fi, fr, ff, gl, ka, de, el, gu, ha, he, hi, hu, is,
id, ga, it, ja, jv, kam, kn, kk, ky, ko, lo, lv, ln, lt, lg, lb, mk, ms,
ml, mt, zh, mi, mr, mn, ne, no, oc, or, ps, fa, pl, pt, pa, ro, ru, sr,
sn, sd, sk, sl, so, ckb, es, sw, sv, tg, ta, te, th, tr, uk, ur, uz, vi,
cy, wo, yo, zu
```

The overview describes ISO 639-1 codes for most languages and ISO 639-3 codes
for `ast`, `yue`, `ceb`, `fil`, `kam`, and `ckb`. The endpoint schema's
`language_code` description says ISO 639-1 while the model list includes those
three-letter codes. Use the model catalogue's language IDs for the actual
selected model; do not infer API behavior for those six codes from the generic
field description alone.

## Account evidence and bounded probes

The approved story notes record the earlier read-only voice query. This ticket
repeated the filtered `GET /v2/voices` lookup on 2026-11-13 and found one exact
Dahlia match: `Dahlia - Vintage Romantic Hollywood Star`, ID
`JaagUurP1dmW3WscoJ79`. The response says it is a non-legacy professional
voice, `sharing.live_moderation_enabled` is false, and
`high_quality_base_model_ids` includes `eleven_v4` and `eleven_v4_turbo`.

The authenticated `GET /v1/models` returned both exact ElevenLabs IDs with
`can_do_text_to_speech=true`, `requires_alpha_access=false`, and
`maximum_text_length_per_request=10000`. The returned `model_rates` values were
`character_cost_multiplier=1` for v4 and `0.5` for Turbo, both with
`cost_discount_multiplier=1`. The authenticated OpenAI model lookup returned
200 for `gpt-6-luna`. These GET results establish account access and current
catalogue metadata; the only OpenAI operation here was the read-only model
lookup.

The same model records set `serves_pro_voices=false`, while the fresh Dahlia
voice record lists both selected IDs in `high_quality_base_model_ids` and both
one-shot requests with that professional voice succeeded. The API does not
explain this field discrepancy. For the approved default, the per-voice model
IDs and successful responses are the direct eligibility evidence; do not
reject Dahlia solely from this model-level flag.

Exactly one non-sensitive, 113-character narration probe was submitted per
ElevenLabs model. The same fixture was used for both:

> At dawn, Mara crossed the quiet garden. Dew brightened the leaves, and a small bird sang from the old stone wall.

| Model | Request | Result | Retained raw audio | Published narration value |
| --- | --- | --- | --- | --- |
| `eleven_v4_turbo` | One Text to Dialogue WebSocket session, `pcm_24000`, English, Dahlia | 18 audio frames; `is_final_audio_for_turn` and final session signal observed; 376,320 raw bytes; no error frame. The client closed after receiving the final signal; a server-initiated close event was not observed. | [raw PCM](provider-probes/eleven_v4_turbo.pcm), [derived WAV](provider-probes/eleven_v4_turbo.wav), [probe metadata](provider-probes/eleven_v4_turbo.json) | 113 × $0.04/1,000 = $0.00452 |
| `eleven_v4` | One `POST /v1/text-to-speech/{voice_id}` request, `pcm_24000`, English, Dahlia | HTTP 200; response `Content-Type: audio/pcm`, `Content-Length: 384000`; no RIFF/WAV header; no usage field was returned in the response body. | [raw PCM](provider-probes/eleven_v4.pcm), [derived WAV](provider-probes/eleven_v4.wav), [probe metadata](provider-probes/eleven_v4.json) | 113 × $0.08/1,000 = $0.00904 |

The combined published narration value was $0.01356, below the $0.12 cap.
This is the public per-character value for the submitted text, not a provider
usage field or account invoice amount. The REST response exposed only the
allowlisted content type and length; the WebSocket response exposed chunk and
completion messages. Neither raw PCM response carried a WAV header or embedded
sample-rate/channel metadata. The exact PCM bytes and their SHA-256 hashes are
recorded in the probe metadata; no secrets or authentication headers are
retained. The compact [account snapshot](provider-probes/account-evidence.json)
and [probe summary](provider-probes/probe-summary.json) retain the model,
language, voice, and usage evidence.

Official format docs identify PCM as signed 16-bit little-endian. They also
describe `pcm_*` outputs as mono and say stereo-producing effects are rejected
with these formats. Both probes omitted effects. Thus 16-bit S16LE is the
documented sample encoding; mono is the documented output contract inferred
for these no-effects requests. The WAV artifacts were wrapped from the raw
bytes using that contract and inspected with Python's `wave` reader: both are
24,000 Hz, mono, 16-bit PCM; durations under that layout are 7.84 s for Turbo
and 8.0 s for v4. Those WAV fields describe the wrapper we produced; they were
not transmitted as response metadata by ElevenLabs.

No retries were made, and no other paid requests were sent. Live continuity
quality was not probed: the REST continuity fields are documented, while this
single WebSocket session exercised one input turn only. T-12's listening check
can use the retained WAVs; this ticket does not claim a listening result.
