# Epic verification

Status: T-12 documentation and automated verification are complete on the
integrated T-01–T-14 result. The listening gate remains **pending**; no listener
or listening observations were recorded for this verification.

T-14 is the owner-requested extension recorded in `tickets.md`: thematic
breaks are silent separators and source diagnostics show the offending line
and caret. Its behavior is included in the US-02 evidence below.

## User-story evidence

| Story | Evidence |
| --- | --- |
| US-01 — predictable inputs and settings | `tests/test_cli.py` covers defaults and overrides, numeric ordering and ties, non-recursive discovery, dotenv precedence/fallback, actionable errors, and public help. `tests/test_elevenlabs.py` covers paginated catalogue lookup, exact name/ID resolution, model eligibility, and ineligible or ambiguous voices. `tests/test_application.py::test_ineligible_voice_stops_the_run_before_any_speech_request` checks eligibility before speech. |
| US-02 — source validation and narration | `tests/test_markdown.py` covers headings, links, emphasis, image captions, front matter, existing brackets, unsupported structures, exact source text, Unicode and CRLF. `test_prepare_chapter_treats_common_thematic_breaks_as_silent_separators` covers the T-14 accepted separators. `tests/test_application.py::test_all_sources_are_validated_before_any_provider_request`, `test_source_diagnostic_shows_the_offending_line_and_caret`, and `test_force_reports_source_diagnostics_and_keeps_questionable_prose` verify preflight, source excerpts, and `--force`. |
| US-03 — exact-preserving directions | `tests/test_annotation.py` exercises the real annotation adapter with controlled HTTP responses, literal expected Markdown, Unicode/repeated prose, BOM/CRLF, malformed/refused responses, invalid anchors, and prohibited or edit-like output. `annotation_prompt_v1.py` contains the versioned fixed prompt. |
| US-04 — directory language | `tests/test_language.py` covers English, German, French, explicit overrides, empty/unsupported prose, original narration before directions, and the 2,000-per-chapter/20,000-total sample limits. The controlled CLI provider tests assert the selected code in speech requests. |
| US-05 — chunk plans and estimates | `tests/test_planning.py` covers ordered exact coverage, paragraph/sentence boundaries, exact limits, tag placement, oversize errors, and the $0.04/$0.08 rates. `tests/test_application.py::test_dry_run_keeps_annotation_and_plan_without_speech_generation` checks the public plan output and absence of speech synthesis. |
| US-06 — selected speech model | `tests/test_elevenlabs.py` exercises the real REST and Turbo adapters against controlled HTTP/WebSocket peers, including voice/model/language transmission, native PCM metadata, complete streams, provider errors, timeouts, and no automatic paid retries. T-01's retained probes provide the bounded live capability evidence below. |
| US-07 — WAV preservation and MP3 delivery | `tests/test_audio.py` uses known PCM/tone fixtures and real ffmpeg to check WAV bytes/metadata, MP3 decoding, output naming, tempo duration/pitch, and old-output preservation on failure. T-08's merged verification also recorded successful ffmpeg checks. |
| US-08 — cache and recovery | `tests/test_store.py` covers source/prompt provenance, cache identity, integrity, and completion states. `tests/test_application.py` covers annotation/audio reuse, later-chunk resume, eligibility rechecks, uncertain requests, corrupt cache rejection, `--no-cache`, synthesis-setting invalidation, speed-only WAV reuse, interrupted Turbo streams, and failed-refresh preservation. `tests/test_elevenlabs.py::test_voice_lookup_retries_a_timed_out_safe_get` and `test_voice_lookup_stops_after_three_transient_responses` check bounded idempotent lookup retries. |

## Provider evidence and limits

The T-01 report records read-only account lookups for `gpt-6-luna`, both
ElevenLabs speech models, and the verified Dahlia account voice. It also records
exactly one 113-character synthesis probe for each speech model: one Turbo
WebSocket probe and one standard-v4 REST probe. The combined published
narration value was $0.01356, below T-01's $0.12 cap. The PCM and derived WAV
files, request summaries, response markers, and hashes are retained under
`provider-probes/`. No OpenAI generation probe or additional live synthesis was
performed during T-12.

Both speech probes requested `pcm_24000` and returned raw PCM rather than a WAV
container. The report's 24 kHz, mono, signed 16-bit WAV description combines
the requested format, provider documentation, and inspected wrappers; sample
rate, channel count, and width were not carried as metadata in the provider
response bodies. The narration estimates in the application use the published
rates of $0.04/1,000 characters for Turbo and $0.08/1,000 for standard v4;
they are not account invoices or credit balances.

T-10 and T-11 exercise the complete application with controlled HTTP and
loopback WebSocket peers, temporary source/output directories, dummy
credentials, and real ffmpeg. T-14 adds a public CLI diagnostic fixture and
thematic-break Markdown fixtures. Automated tests make no paid provider
requests. No tests were added solely for this documentation ticket.

## T-12 checks on the merged T-14 result

- Fresh offline `uv sync` using `uv.lock`: **passed** with CPython 3.13.11 and
  uv 0.9.27; created a clean temporary environment, resolved 20 locked
  packages, and installed 19 packages including the project and dev tools.
- `uv run audiobook --help`: **inspected**; it lists the approved options,
  defaults, behavioral hints, and examples for dry-run, sample, standard v4,
  and cache refresh.
- `ffmpeg`: **available** at `/opt/homebrew/bin/ffmpeg` (version 9.0.2).
- `ruff format --check src tests`: **passed**, 22 files already formatted.
- `ruff check src tests`: **passed**.
- `pytest`: **passed**, 120 tests, including T-14 diagnostics and thematic-break
  regressions. Loopback WebSocket tests ran with local socket access; there
  were no external provider calls.
- `ty check src tests`: **passed**.

## Listening gate

**Pending.** No listener or observations were recorded. The retained T-01
samples have not been listened to for sparse directions, the generated
`[chapter]` cue, pitch/pace, or chunk joins. The T-01 fixture is a single short
turn and does not establish those end-to-end listening qualities. No additional
live generation was made; close this gate only after an authorized listener
assesses an available full sample and records their observations.
