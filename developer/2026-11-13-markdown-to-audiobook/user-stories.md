# Markdown to audiobook

## Problem statement

The owner wants a personal command-line tool that turns numbered Markdown
chapters into expressive audiobooks. GPT-6 Luna, at medium reasoning effort,
proposes sparse vocal direction without rewriting the author's text. ElevenLabs
generates speech directly, using its cheaper v4 Turbo model by default. The tool
preserves lossless audio, produces chapter MP3s, and supports inspection and
resumption without repeating completed paid work.

## Out of scope

- A web interface, distribution to other users, and multiple narrators or
  character casting.
- DOCX input, recursive discovery, combined whole-book output, M4B packaging,
  and audiobook metadata or chapter navigation.
- Editorial rewriting, punctuation correction, environmental sound effects,
  and automatic audio transcription or correction.
- Reading application configuration from front matter in this epic. Front
  matter is retained in Markdown artifacts and excluded from narration.

## Status and approval

- Epic ID: `markdown-to-audiobook`.
- Status: whole story set and proposed test seams approved on 2026-11-13.
- Basis: the project interview through acceptance of Q21 and Q22 on 2026-11-13.
- Approval: the owner said, "Ok, all clear and you can go to ticket generation."
  The specialization workflow requires separate ticket-plan approval before
  implementation.
- No existing glossary or ADRs were found in this repository.

## Shared behavior

### Command and configuration

Proposed invocation: `uv run audiobook [options]`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--input-dir`, `-i` | `.` | Input directory; scan its immediate files only. |
| `--output-dir`, `-o` | `audiobook` | Directory for finished audio. |
| `--language`, `-l` | detected | One language for the complete input directory. |
| `--voice`, `-v` | Dahlia | One narrator for the complete run; default ID `JaagUurP1dmW3WscoJ79`. |
| `--model`, `-m` | `eleven_v4_turbo` | ElevenLabs speech model. |
| `--speed`, `-s` | `1` | Pitch-preserving output tempo adjustment. |
| `--force`, `-f` | off | Continue after reporting source-validation errors. |
| `--dry-run`, `-n` | off | Prepare annotations and chunks, report estimates, then stop. |
| `--sample` | off | Prepare the first chapter and render only its first chunk. |
| `--no-cache` | off | Ignore and replace caches and regenerate completed outputs. |

Relative paths resolve from the current working directory. Working artifacts
live under `./audiobook_tmp`, including annotated Markdown, insertion records,
chunk plans, WAVs, and completion information. Preserve originals throughout.

Load `.env` from the current working directory; if absent, try
`~/.audiobook.env`. Existing process variables take precedence. Required
credentials are `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`.
Keep secrets out of artifacts and diagnostic output.

### Processing order

1. Discover and sort chapters; validate discovered sources before paid work.
2. Choose a directory-wide language unless explicitly supplied.
3. Obtain or reuse annotations for the selected chapter, validate insertions,
   and construct annotated copies without modifying source characters.
4. Derive speech text, prepare semantic chunks in memory, and print chunk
   statistics and narration estimates. Stop here for `--dry-run`.
5. Generate the selected chunks with the chosen model, retaining native
   lossless audio as WAV. Stop after one chunk for `--sample`.
6. Join chapter audio, apply requested tempo, and encode the final MP3.

`--sample` selects only the first numerically ordered chapter for annotation,
planning, and synthesis. Local discovery, validation, and language selection
may inspect the directory. `--dry-run --sample` prepares only that chapter and
makes no speech-generation request. A dry run can incur Luna charges.

## User stories

### US-01 — Run the tool with predictable inputs and settings

As the owner, I can select directories and narration settings so that I can
process my existing chapter collection from the terminal.

**Acceptance criteria**

- Support the command, options, defaults, and environment lookup above.
- Match entire filenames against `[0-9]+-[a-zA-Z-]+\.md`; preserve their stems.
  Ignore other files and subdirectories, including generated directories.
- Sort by the numeric prefix, allowing duplicate prefixes. Use filename order
  to make ties deterministic; do not rename or normalize files.
- Report missing directories, no matching chapters, invalid options, missing
  required credentials, and unavailable audio tools clearly.
- Resolve voice selection against the account's ElevenLabs voice catalogue,
  using the paginated `GET /v2/voices` endpoint and provider voice IDs. The
  earlier voice-name list is unverified and is not an allowed-values list for
  this tool. Accept an exact name or ID; report missing or ambiguous
  names rather than choosing another voice.
- Always request `include_live_moderated=false` during voice discovery and
  resolution, including explicit voice-ID lookups. Exclude voices with
  `sharing.live_moderation_enabled=true`; reject an explicitly selected
  ineligible voice before synthesis with a clear explanation. Apply the same
  rule to the default voice, samples, and resumed runs. `--force` does not
  override voice eligibility. This selects voices without optional live
  moderation; it is not a global speech-request moderation switch.

**Suggested completion evidence**

CLI scenarios demonstrate defaults, overrides, numeric ordering, duplicate
prefixes, ignored nested files, configuration fallback, and actionable failures.
Voice fixtures cover pagination, current names/IDs, ambiguous names, and a
voice name that is absent from the ElevenLabs account catalogue.
Verify exclusion of live-moderated voices for name and ID selection, including
with `--force`, and that no synthesis request is sent for an ineligible voice.
Verify credentials using dummy values in isolated temporary directories.

### US-02 — Validate chapters and derive the intended narration

As the owner, I receive precise source diagnostics before spending money and
can deliberately continue past source problems.

**Acceptance criteria**

- Report each source problem with filename, one-based line and character
  position, and a clear explanation. Report errors before stopping.
- Fail source validation for unknown markup, existing bracketed speech
  constructs, or unsupported content. With `--force`, print the same diagnostics
  and continue; preserve questionable prose rather than silently deleting it.
- Exclude valid Markdown link/image syntax and front matter from the existing
  director-tag check. Brackets in extracted speech remain subject to it.
- Skip an optional leading `---` front-matter block through its closing `---`.
  Diagnose an unclosed block instead of dropping the rest of the chapter.
- Speak headings as `[chapter] Heading text`; `#` and `##` have the same
  narration behavior. Apply the same rule to other supported heading levels.
- Speak prose, emphasis contents, and link labels, excluding formatting syntax
  and link destinations. Omit images and associated optional italic image
  descriptions. Diagnose code blocks and unsupported structures initially.
- Retain the complete original Markdown, including omitted narration content,
  in the annotated copy except for the permitted tag insertions.

**Suggested completion evidence**

CLI fixtures establish exact speech text for headings, emphasis, links, images
with and without descriptions, front matter, and existing tags. Invalid fixtures
establish exact diagnostic locations and that no paid request occurs before
validation succeeds or the owner supplies `--force`.

### US-03 — Add sparse direction while preserving every source character

As the owner, I get expressive direction without allowing Luna to rewrite my
chapter or alter its punctuation, formatting, or whitespace.

**Acceptance criteria**

- Send chapter text directly to GPT-6 Luna at medium reasoning effort, using a
  fixed, versioned prompt. File uploads are unnecessary.
- Ask for structured insertion proposals anchored to original source positions;
  the model does not return an authoritative replacement document.
- Apply only validated vocal-direction insertions. Preserve all original
  characters in order, including existing tags when `--force` permits them.
- The prompt asks for sparse, context-appropriate vocal directions. A
  conservative permitted vocabulary excludes environmental effects.
- Reject malformed responses, invalid anchors, edits inside excluded Markdown
  structures, and disallowed additions. `--force` does not bypass these checks.
- Retain annotated Markdown and sufficient insertion provenance to verify the
  preservation guarantee, including when loading a cache.

**Suggested completion evidence**

Through the CLI and a controlled OpenAI boundary, demonstrate accepted
insertions against literal expected documents containing Unicode, repeated
passages, original brackets, and significant whitespace. Demonstrate rejection
of text replacements, invalid insertion locations, and prohibited effects.
Original files remain byte-for-byte unchanged.

### US-04 — Choose one narration language for the directory

As the owner, I can override the language or let the tool select it locally for
the complete collection.

**Acceptance criteria**

- `--language` takes precedence over automatic detection.
- Otherwise use the working `py3langid` approach from
  `/Users/kris/Source/lang-guesser`, applied to original narration prose across
  the directory before English direction tags are inserted.
- Print the selected language and use it consistently for every speech request.
- Validate detected or supplied codes against the chosen model's supported
  languages. Explain unsupported results and direct the owner to `--language`.
- Do not present raw detector scores as confidence percentages.

**Suggested completion evidence**

CLI examples establish directory-wide selection from known-language prose and
explicit override. Provider-boundary fixtures establish that a supported code
is transmitted consistently and unsupported codes fail before synthesis.

### US-05 — Inspect chunk plans and estimated costs before synthesis

As the owner, I can inspect prepared narration and understand the amount of
audio generation before committing to a full run.

**Acceptance criteria**

- Split validated speech text in memory, preferring paragraph boundaries and
  falling back to sentence boundaries within the chosen model's verified limit.
- Keep direction tags attached to the speech they direct. Every speech passage
  occurs exactly once and in source order across the chunks.
- Diagnose an unsplittable oversized passage rather than truncating it.
- Report chapter names, ordered chunks, character counts, and estimated
  narration cost, including tags submitted for synthesis.
- Use published estimates of $0.04/1,000 characters for `eleven_v4_turbo` and
  $0.08/1,000 for `eleven_v4`; label the rate and estimated nature explicitly.
  Account allowances and Luna charges are separate from narration estimates.
- `--dry-run` retains annotated Markdown and planning artifacts and sends no
  synthesis request. Combine with `--sample` as specified above.

**Suggested completion evidence**

CLI fixtures establish readable plans, complete ordered coverage, tag placement,
boundary selection, and oversized-passage errors. A known 1,000-character
submitted payload estimates $0.04 for Turbo and $0.08 for standard v4. Dry-run
scenarios retain annotations and produce no speech request or finished audio.

### US-06 — Generate narration with the selected ElevenLabs model

As the owner, I can hear one representative chunk or synthesize every chapter
using the selected voice and model.

**Acceptance criteria**

- Use ElevenLabs directly with `ELEVENLABS_API_KEY`. Default to
  `eleven_v4_turbo`; support explicit `eleven_v4` selection.
- Use the selected model's documented transport, including the Turbo Text to
  Dialogue WebSocket; use httpx for REST requests.
- Select one narrator for the run and pass the chosen language. Default to
  Dahlia (`JaagUurP1dmW3WscoJ79`), whose catalogue name is
  `Dahlia - Vintage Romantic Hollywood Star`. Revalidate voice eligibility
  against the account catalogue and chosen model before synthesis.
- `--sample` plans the first chapter and renders only its first planned chunk;
  later chapters are not sent to Luna or synthesized. Compatible cached sample
  audio may be reused without another generation request.
- Clearly distinguish sample audio from a completed chapter so that a sample
  cannot replace the completed chapter MP3.
- Receive complete native lossless output, preserving chunk order. Treat an
  incomplete stream as incomplete work rather than usable cached audio.
- Use context continuity only where the chosen model and transport support it;
  never blindly send the unsupported v3 previous/next fields.

**Suggested completion evidence**

Controlled provider-boundary scenarios establish model, voice, and language
selection, sample scope, complete audio reception, and interrupted streams.
Record a small live capability check for Turbo lossless output and standard-v4
selection, then listen to the sample; document observed limitations honestly.

### US-07 — Preserve WAVs and publish complete chapter MP3s

As the owner, I keep lossless generation artifacts and obtain a conveniently
playable MP3 for each chapter.

**Acceptance criteria**

- Prefer the native lossless format available on Starter, such as 24 kHz PCM,
  wrapping raw PCM in a correct WAV container without lossy conversion.
- Preserve chunk WAVs under `audiobook_tmp`. Converting MP3 to WAV does not
  satisfy native lossless preservation.
- Assemble chunks in order and encode the final chapter MP3 once with ffmpeg.
- Preserve the chapter stem: `834-a-slug.md` produces `834-a-slug.mp3` in the
  selected output directory.
- Implement `--speed` through pitch-preserving tempo processing; `1` leaves
  generated pace unchanged. Validate the supported positive speed range.
- Publish a final output only after successful assembly. An assembly failure
  preserves any previous complete output and the reusable chunk WAVs.

**Suggested completion evidence**

Use small known audio fixtures and real ffmpeg to establish playable WAV/MP3
outputs, ordered content, expected duration, tempo adjustment without a pitch
shift, correct names, and safe failure behavior. No provider call is needed for
these audio-processing tests.

### US-08 — Reuse paid work and recover from interruption

As the owner, I avoid repeating completed annotation and audio generation and
can explicitly request fresh results.

**Acceptance criteria**

- Reuse valid annotated caches with `using cached file {filename}`. Retain
  annotations after dry runs and failures.
- Identify cached work by source and provenance so that different input
  directories with identical filenames cannot collide. Detect incompatible
  cached annotations and explain that `--no-cache` is required; never silently
  narrate an older source as though it were current.
- Reuse audio only when its submitted text, model, voice, and generation
  settings match. Apply current speed during output assembly.
- Skip completed chapter outputs during normal runs and report the skip.
  Completion records must establish compatibility with the current request;
  stale or incomplete work is not marked complete.
- `--no-cache` ignores caches, replaces them with fresh artifacts, and
  regenerates completed chapter outputs. Replace final MP3s only after success.
- Stop on a failed chunk, retain completed work, and resume at unfinished work
  on the next compatible run.
- Retry safe downloads where appropriate. Do not blindly repeat synthesis after
  an uncertain response that may already have incurred a charge. Explain what
  happened and how the owner can recover.

**Suggested completion evidence**

Run the CLI repeatedly against controlled providers: cached annotations avoid
Luna usage; interrupted runs reuse matching completed audio; `--no-cache`
requests fresh work; changed settings prevent incompatible reuse; and failures
leave earlier outputs intact. Verify observable files, messages, and provider
requests rather than private cache implementation details.

## Proposed test seams

Whole-set approval includes approval of these seams for the TDD skill:

1. **Public CLI:** arguments, exit status, diagnostics, plans, user-visible
   artifacts, and behavior across repeated invocations in temporary directories.
2. **External provider protocols:** controlled HTTP/WebSocket endpoints exercise
   real application adapters, record requests, and return representative provider
   responses and failures. Substitute external services, not internal modules.
3. **External audio tool boundary:** exercise real ffmpeg with tiny known audio
   fixtures and inspect the resulting playable audio and duration.

No tests are written in this planning step. Ticket planning will select concrete
scenarios at these seams. Any additional public module seam requires agreement
before tests are written. Implement code in vertical red-to-green slices: one
observable behavior, one failing test, then the minimal implementation.

## Shared definition of done

- Every story's acceptance criteria have recorded completion evidence at the
  approved seams. Tests assert independently specified outcomes, not a second
  copy of the implementation.
- Code changes follow the TDD skill. Documentation, research, and configuration
  work receive appropriate inspection rather than artificial tests.
- The integrated project passes:

  ```sh
  uv run ruff format --check src tests
  uv run ruff check src tests
  uv run pytest
  uv run ty check src tests
  ```

- `uv.lock` and dependency declarations support the selected Python version,
  language detection, HTTP/WebSocket transports, and CLI. Normal automated tests
  use dummy credentials and do not require paid services.
- README, `.env.example`, command help, and ignore rules describe the delivered
  ElevenLabs workflow, cache behavior, generated directories, prerequisites,
  supported markup, and dry-run/sample charges accurately.
- A small provider integration check establishes actual Turbo transport,
  lossless output format, voice availability, and relevant request limits.
  A listening check assesses sparse tags, the `[chapter]` cue, and chunk joins;
  exact source preservation does not guarantee perfect spoken transcription.
- Each ticket is committed and integrated according to the approved ticket
  plan. The merged result passes relevant checks before dependents proceed.
- The final whole-epic code review compares the result against this approved
  story set and the ticket plan; findings and resolutions are saved in this epic.
  Completion requires resolved findings and passing final checks.

## Notes

- On 2026-11-13, read-only account queries to `GET /v2/voices` found one Dahlia
  match: `Dahlia - Vintage Romantic Hollywood Star`, ID
  `JaagUurP1dmW3WscoJ79`. It is a non-legacy professional voice with
  `sharing.live_moderation_enabled=false`; it also appears when
  `include_live_moderated=false`. Its `high_quality_base_model_ids` includes
  `eleven_v4` and `eleven_v4_turbo`. This verifies catalogue eligibility;
  synthesis and listening checks remain part of implementation verification.
- Cached artifacts must still satisfy the source-preservation guarantee. The
  explicit stale-cache diagnostic in US-08 makes the earlier cache discussion
  concrete while retaining `--no-cache` as the owner's refresh mechanism.
- Sparse tagging is a conservative prompt policy and permitted vocabulary,
  assessed with representative passages; no numeric tag quota was agreed.
- `[chapter]` is the owner's tested convention, not a verified standard tag.
- Associate an italic image description only with the image it follows; ticket
  planning should establish a concrete fixture so ordinary emphasized prose is
  not accidentally omitted.
- Directory-wide detection uses prose from the collection, including in sample
  mode; it does not send later chapters to Luna. Ticket planning should define a
  bounded sampling policy and known-language fixtures.
- The published model rates are estimates verified during the interview on
  2026-11-13. They are not account-specific billing promises.

## Provider references

- [ElevenLabs API pricing](https://elevenlabs.io/pricing/api)
- [Model IDs, languages, and transports](https://elevenlabs.io/docs/overview/models/)
- [Turbo WebSocket guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)
- [Supported audio formats and plans](https://elevenlabs.io/docs/help-center/troubleshooting/what-audio-formats-do-you-support)
- [Speech settings](https://elevenlabs.io/docs/eleven-creative/playground/text-to-speech)
- [Audio-tag prompting guidance](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices)
- [List account voices](https://elevenlabs.io/docs/api-reference/voices/search)
- [Voice-owner live moderation](https://elevenlabs.io/docs/help-center/product/voices/voice-library/what-is-live-moderation)
