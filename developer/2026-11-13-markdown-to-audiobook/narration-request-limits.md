# Narration request limits

Verified against primary documentation on 2026-11-13. No provider requests were submitted during this research.

## ElevenLabs: model limits and endpoint guidance differ

| API context | Documented size | Meaning |
| --- | --- | --- |
| Text to Speech, `eleven_v3` | 5,000 characters | Listed maximum for one TTS request. |
| Text to Speech, `eleven_v4` | 10,000 characters | Listed maximum for one TTS request. |
| Text to Dialogue, HTTP `inputs[].text` | At most 2,000 characters total recommended | Reliability guidance across every input in the request; larger streaming generations may end prematurely or be rejected. |
| Text to Dialogue, WebSocket | No explicit hard character maximum found in the reviewed reference | Incremental dialogue transport; the TTS model table does not establish a 10,000-character limit for `eleven_v4_turbo` WebSocket turns. |

Sources: [ElevenLabs model character limits](https://elevenlabs.io/docs/overview/models/#character-limits), [Create dialogue API](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert), [Text to Dialogue WebSocket API](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket).

The HTTP dialogue reference defaults to `eleven_v3` and explicitly warns of early streaming termination above its recommended request size. This is a documented reliability recommendation, rather than evidence of a universal 2,000-character hard validation limit. [Create dialogue API](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert).

## What this application actually sends

The current default is `eleven_v4_turbo`, sent through `wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`. The adapter registers a voice, sends the entire planned chunk as one input, then closes the socket. This is Text to Dialogue, not the single-voice Text to Speech HTTP request covered by the model table. Local evidence: `src/audiobook/elevenlabs.py` (`DEFAULT_MODEL_ID`, `TURBO_WS_URL`, `_synthesize_turbo`).

The official WebSocket guide specifically demonstrates `eleven_v4_turbo`. It describes buffering incremental text, emitting audio, and flushing remaining audio when the connection is closed. Neither the guide nor the reviewed WebSocket reference establishes a hard 10,000-character maximum for a Turbo turn. [Real-time dialogue guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd), [WebSocket API](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket).

Tags count toward input character totals, alongside letters, punctuation, and whitespace. The application's planner likewise counts the annotated narration as submitted, including direction tags. [ElevenLabs character counting](https://elevenlabs.io/docs/help-center/account/general/how-is-character-length-calculated); local evidence: `src/audiobook/planning.py`.

## Local reliability evidence

The affected 4,609-character passage (after removing its terminal direction tag) produced 162 seconds in one Turbo request. The user identified a large omitted passage despite final-stream signals. Splitting the same passage into seven requests of at most 800 characters yielded 311.28 seconds; the user checked the opening two minutes and ending and reported success. These observations support a conservative 800-character operating budget, including tags. They do not establish an official provider maximum or prove every generated word correct.

The seven actual request lengths were 574, 734, 731, 630, 672, 727, and 541 characters. Evidence: `audiobook_tmp/probes/01-incomplete-failure-chunk-3-cd73b176484341ea98119e0d6b45ed38/requests.json` and corresponding WAV files. This probe remained bounded to chunk 3.

## Implication

Do not treat the generic 10,000-character TTS number as a reliable narration size for the Turbo dialogue WebSocket. A smaller application budget is justified by the observed omission and the separate official dialogue guidance. A successful transport completion alone is insufficient to establish spoken-text coverage.
# Replicate v3 schema verification

Checked the public [elevenlabs/v3 input schema](https://replicate.com/elevenlabs/v3/api/schema), including the embedded `version._extras.dereferenced_openapi_schema.components.schemas.Input.properties.prompt` JSON. On 2026-11-13 the prompt definition was:

```json
{"type": "string", "title": "Prompt", "x-order": 0, "description": "The text to convert to speech"}
```

It specifies no `maxLength` and no 1,000-character prompt limit. The [model README](https://replicate.com/elevenlabs/v3/readme) likewise does not establish that limit. This does not prove there is no runtime limit; it means a 1,000-character limit could not be verified from these current public sources. Replicate's model is v3, whereas this repository's default is ElevenLabs' direct `eleven_v4_turbo` dialogue WebSocket.
