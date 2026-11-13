# Shared Python interfaces

This contract freezes the public seams approved with the ticket plan. Ticket
implementations may choose internal helpers, but should keep the behavior and
field meanings below stable. Code-point offsets count Unicode characters in
the unchanged, decoded source text. Diagnostic line and character values are
one-based; an insertion anchor is a zero-based boundary offset from `0` through
the source length.

## Command and configuration

`audiobook.cli.parse_settings(argv: Sequence[str] | None = None) -> RunSettings`
loads `.env` from the current directory, or `~/.audiobook.env` if the local
file is absent, and then parses the approved options. Process environment
values take precedence. Relative input and output paths are resolved against
the current working directory. API keys are optional fields while parsing and
are excluded from the settings representation; a run reports missing required
keys without displaying any key value.

`audiobook.cli.discover_chapters(input_dir: Path) -> tuple[Path, ...]` scans
only immediate regular files matching `[0-9]+-[a-zA-Z-]+.md`. It returns
absolute paths ordered by numeric prefix and then by unchanged filename. It
raises `InputError` for a missing/non-directory path or for a directory with
no matching chapters.

`audiobook.cli.main(argv: Sequence[str] | None = None) -> int` is the console
entry point. User-correctable failures return a nonzero code and write a
diagnostic to stderr. Until T-10 connects the application workflow, valid
processing requests also fail clearly rather than reporting a partial run as
successful.

The command accepts `--input-dir/-i`, `--output-dir/-o`, `--language/-l`,
`--voice/-v`, `--model/-m`, `--speed/-s`, `--force/-f`, `--dry-run/-n`,
`--sample`, and `--no-cache`. The model is restricted to the approved
`eleven_v4_turbo` and `eleven_v4` IDs. The defaults are `.`, `audiobook`,
automatic language, `Dahlia`, `eleven_v4_turbo`, speed `1`, and false for each
mode flag.
`RunSettings.language` is the explicit override or `None`; detected language is
chosen later for the directory.

## Shared value types

The public types live in `audiobook.contracts` and are frozen dataclasses unless
noted otherwise.

| Type | Meaning |
| --- | --- |
| `RunSettings` | Resolved paths, language override, voice/model/speed, mode flags, and hidden-repr optional API keys. |
| `SourceIdentity` | Absolute `path` plus `content_sha256` of the original file bytes. |
| `SourcePosition` | Unicode `offset` plus one-based `line` and `character`. |
| `SourceDiagnostic` | Stable `code`, user-readable `message`, source `position`, and `error` or `warning` severity. |
| `PreparedChapter` | Source identity, exact original Markdown, narration projection, legal original insertion offsets, and diagnostics. |
| `Insertion` | Original-coordinate `anchor` and vocal-direction `tag`. |
| `InsertionRecord` | Source digest, fixed prompt version, validated ordered insertions, and optional provider response ID. |
| `AnnotationIdentity` | Source, explicit model, reasoning effort, and prompt version required for annotation-cache compatibility. |
| `AnnotatedChapter` | Prepared source, source-preserving annotated Markdown, derived narration, and insertion provenance. |
| `ModelCapabilities` | Explicit model ID, maximum submitted characters, supported language codes, per-1,000-character cost, and currency. |
| `SpeechChunk` | Zero-based order, exact submitted text, submitted character count, and estimated cost. |
| `ChunkPlan` | Model ID, ordered chunks, total submitted characters, rate, total estimate, and currency. |
| `AudioFormat` | Raw encoding, sample rate in Hz, sample width in bytes, and channel count. |
| `RawAudio` | Complete containerless PCM bytes and their verified format metadata. |
| `AudioIdentity` | Submitted-text digest, model, voice, language, and digest of all synthesis settings. |
| `FinalOutputIdentity` | Audio identity plus output playback speed; changing speed does not invalidate raw audio. |
| `AssemblyResult` | Retained chunk WAV paths and the atomically published chapter MP3 path. |
| `ChapterCompletion` | Source identity, final-output identity, and published MP3 digest. |
| `ArtifactResult[T]` | Cache lookup state (`absent`, `valid`, `stale`, or `incomplete`), optional value, and optional reason. |

`ArtifactState` is the four-state literal type used by `ArtifactResult`. Cache
readers must not return stale or incomplete data as a valid value. Annotation
compatibility includes source path/content and prompt provenance. Raw audio
compatibility includes exact submitted text, model, voice, language, and
synthesis settings; final-output identity adds speed.

`ModelCapabilities.max_characters` and `AudioFormat` fields have no implicit
fallbacks. Populate the model limit from the selected account's verified
capability evidence, and populate rate, language set, sample rate, width,
channel count, and encoding from their verified sources. Missing metadata is an
actionable capability error, not permission to substitute a typical provider
default.

For Markdown projection, generated `[chapter]` cues may contribute to spoken
text but are not Luna insertions. `PreparedChapter.insertion_positions` always
refers to boundaries in the unchanged original Markdown.

## Processing module seams

These names and outcomes are the agreed test boundaries. Argument details may
be chosen within the described contracts.

| Module | Public operation | Contract |
| --- | --- | --- |
| `markdown` | `prepare_chapter(source: Path) -> PreparedChapter` | Read the source without normalizing its content; return its exact Markdown, speech projection, legal insertion anchors into that original, and source-positioned diagnostics. |
| `language` | `choose_language(chapters, override, supported_languages) -> str` | Return one supported language code for the collection or raise an actionable `ConfigurationError`. |
| `annotation` | `annotate_chapter(prepared_chapter, provider) -> AnnotatedChapter` | Validate provider insertions against original coordinates and legal anchors, then derive annotated Markdown and narration from the same insertion record. |
| `planning` | `plan_chapter(narration, model_capabilities) -> ChunkPlan` | Preserve exact ordered speech coverage, count every submitted character, and estimate cost with decimal arithmetic. |
| `elevenlabs` | `resolve_voice(selector, ...)` and `synthesize_chunk(...) -> RawAudio` | Resolve one eligible account voice and return only complete native lossless audio with explicit format metadata. An uncertain paid request raises `UncertainOperationError`; an incomplete stream raises `IncompleteAudioError`. |
| `audio` | `assemble_chapter(audio, destination, speed) -> AssemblyResult` | Retain valid chunk WAVs, publish one ordered chapter MP3 atomically, and raise `AudioError` on invalid data or failed assembly. |
| `store` | `load_annotation/save_annotation(AnnotationIdentity, AnnotatedChapter)`, `load_chunk/save_chunk(AudioIdentity, RawAudio)`, and `load_completion/save_completion(FinalOutputIdentity, ChapterCompletion)` | Loaders return `ArtifactResult` and validate compatibility before exposing a value. Writers publish records atomically. |
| `application` | `run(settings) -> int` | Execute the approved workflow through the CLI and return a success code only after all requested work is complete. |

Shared domain errors live in `audiobook.contracts`: `AudiobookError` is the
base for `ConfigurationError`, `InputError`, `SourceValidationError`,
`ProviderError`, `AudioError`, and `StaleArtifactError`.
`UncertainOperationError` and `IncompleteAudioError` specialize
`ProviderError`. Raise errors with actionable messages; do not put credentials
or provider secrets in messages or artifacts.

## Provider test boundary

Provider tests exercise the real adapters against controlled external
protocols. HTTP adapters accept an injected HTTPX client or transport (sync or
async to match the adapter), so tests can use `httpx.MockTransport` or a
loopback HTTP server. WebSocket adapters accept a connection function/URL at
their module boundary, so tests can run a loopback WebSocket peer and inspect
the actual provider messages. These hooks belong to adapter construction or
public Python calls, not to product command-line options. Automated tests use
fake credentials and make no paid provider requests.
