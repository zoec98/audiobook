# Markdown to audiobook

Turn numbered Markdown chapters into narrated audio with sparse vocal
directions, lossless intermediate WAV files, and one MP3 per chapter. The tool
uses GPT-6 Luna for annotations and ElevenLabs for speech generation.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` on `PATH` to create chapter MP3s
- An OpenAI API key and, for speech generation, an ElevenLabs API key

`OPENAI_API_KEY` is required for every run; an uncached annotation may make a
Luna API request even during a dry run. `ELEVENLABS_API_KEY` is required only
when synthesizing speech, so dry runs do not need it.

Install the project dependencies:

```sh
uv sync
```

Copy `.env.example` to `.env` and add your API keys. The tool reads `.env` from
the current working directory. If that file is absent, it tries
`~/.audiobook.env`. Existing process environment values take precedence over
either file. Never commit a populated `.env`.

```sh
cp .env.example .env
# Edit .env and set OPENAI_API_KEY and ELEVENLABS_API_KEY.
```

Check the installed command and its options with:

```sh
uv run audiobook --help
```

## Run

Chapter files must be immediate files in the input directory whose complete
names match `[0-9]+-[a-zA-Z-]+.md`, such as `1-opening.md` or
`12-the-river.md`. The tool does not recurse into subdirectories. Chapters are
ordered by numeric prefix, then by filename; duplicate numbers are allowed.
Output names preserve each source stem.

Preview annotations, chunk boundaries, and estimated speech usage before
synthesis:

```sh
uv run audiobook --input-dir ./chapters --output-dir ./audiobook --dry-run
```

Generate every chapter:

```sh
uv run audiobook --input-dir ./chapters --output-dir ./audiobook
```

Generate only the first chapter's first speech chunk as a sample:

```sh
uv run audiobook --input-dir ./chapters --output-dir ./audiobook --sample
```

Use standard v4, set English explicitly, and play the finished MP3 10% faster:

```sh
uv run audiobook --input-dir ./chapters --output-dir ./audiobook \
  --model eleven_v4 --language en --speed 1.1
```

To explicitly refresh annotations and speech artifacts, including a previously
uncertain paid request, use:

```sh
uv run audiobook --input-dir ./chapters --output-dir ./audiobook --no-cache
```

The command accepts `--input-dir` (`-i`), `--output-dir` (`-o`), `--language`
(`-l`), `--voice` (`-v`), `--model` (`-m`), `--speed` (`-s`), `--force` (`-f`),
`--dry-run` (`-n`), `--sample`, and `--no-cache`. Defaults are the current
directory for input, `./audiobook` for output, automatic language detection,
Dahlia, `eleven_v4_turbo`, speed `1`, and all mode flags off.

## Source handling

The tool validates every discovered source before provider requests. It skips
an optional leading `---` front-matter block through its closing `---` and
excludes it from narration; an unclosed block is an error. In the body,
CommonMark thematic breaks such as `* * *`, `---`, and `___` are silent
separators. Headings become a spoken `[chapter]` cue followed by the heading
text. Prose, emphasis contents, and Markdown link labels are spoken; formatting
characters, link destinations, images, and associated italic-only image
descriptions are omitted. An italic-only caption can follow an image on the
same line or the next nonblank line.

Code blocks, unsupported block structures, unclosed front matter, and existing
square-bracketed speech constructs in retained narration are reported with
source positions. A source diagnostic includes the filename, line and column,
offending source line, and a caret at the reported position. Without `--force`,
source errors stop the run before paid work. `--force` reports the same
diagnostics and continues while preserving questionable source text. It does
not allow Luna to rewrite the chapter, ignore invalid cached audio, or bypass
voice/model checks. Original source files are never modified.

## Voice, language, and speech models

The default narrator is Dahlia, resolved to the verified account voice ID
`JaagUurP1dmW3WscoJ79`. You can select an exact voice name or ID with `--voice`.
Each run looks up the account's paginated voice catalogue and checks that the
chosen voice supports the selected model and has live moderation disabled.
Missing, ambiguous, or ineligible voices stop before synthesis.

The default speech model is `eleven_v4_turbo`, which uses the Text to Dialogue
WebSocket. `--model eleven_v4` selects the standard-v4 REST adapter. Both
requests use the selected language and ask for native `pcm_24000` audio. The
retained WAV wraps the returned PCM directly with its verified metadata; the
account evidence records 24 kHz, mono, 16-bit PCM for the selected format. The
provider response itself does not include WAV metadata.

By default, the tool detects one language from original narration prose across
the collection before adding English vocal directions. It uses at most 2,000
characters per chapter and 20,000 characters total, in chapter order. Use
`--language en` (or another supported language code) to override detection.
The chosen language is checked against the selected speech model and used for
every speech request. Detector scores are not reported as confidence values.

## Annotations, plans, and costs

For uncached chapters, GPT-6 Luna (`gpt-6-luna`) proposes sparse vocal
directions at medium reasoning effort. The fixed prompt permits only a
conservative vocal-direction vocabulary, such as `[calmly]`, `[warmly]`,
`[whispering]`, and `[sighing]`; it excludes editorial changes and
environmental effects. Valid directions are inserted into a copy of the
source. Every original character remains in order, including whitespace.
The response schema restricts both tags and insertion anchors to legal values.
Chapters with more than 990 legal anchors use multiple annotation requests,
each with the full chapter as context, to stay within the schema's enum budget.

Speech is split in order at paragraph boundaries, then sentence boundaries,
within the verified 10,000-character request limit. A direction stays with the
passage it directs. Reported character counts include text, tags, and
whitespace. The current estimate rates are $0.04 per 1,000 characters for
`eleven_v4_turbo` and $0.08 per 1,000 for `eleven_v4`. These are published
narration-value estimates, not an account invoice or remaining-credit check;
subscription allowances and Luna charges are separate.

`--dry-run` saves or reuses annotations and chunk plans, reports language and
cost estimates, and sends no speech-generation request. An uncached annotation
can still incur Luna usage. `--dry-run --sample` prepares only the first
chapter's annotation and plan; source validation and directory language
selection still consider all discovered chapters. `--sample` annotates and
plans only the first chapter, then generates or reuses its first planned chunk.
A compatible cache hit avoids a new speech request. It writes
`<stem>.sample.mp3` and reusable WAV artifacts, but no complete-chapter record;
it does not replace `<stem>.mp3`.

## Cache, resume, and artifacts

Working artifacts go under `./audiobook_tmp` relative to the current working
directory. Source-scoped folders hold `annotation.json`, audio chunk records
and PCM, completion records, and uncertain-operation markers. Chunk plans are
stored under `audiobook_tmp/plans/`; retained chunk WAVs are stored under
`audiobook_tmp/wav/`. These files include source text and generated narration,
so keep the directory private. The repository ignores the default generated
directories and secrets files.

Compatible annotations are reused only when their source and prompt provenance
match. Chunk audio is reused only when submitted text, model, voice, language,
and synthesis settings match; playback speed is a separate output setting, so
a speed-only change can rebuild the MP3 from cached WAVs. A valid completed
MP3 is skipped during a normal run when its compatibility record and file
digest match.

If a chunk fails, completed chunks and annotations remain available for a
later compatible run. The voice is checked again before resumed synthesis.
Timed voice-catalogue GET requests have a 10-second timeout and at most three
attempts. Paid speech requests are never retried automatically after an
uncertain outcome. The tool records that uncertainty and explains that a
fresh operation requires `--no-cache`; such an operation may incur another
charge. `--force` does not bypass stale or corrupt cache validation.

Final chapter files are written to the selected output directory, which
defaults to `./audiobook`, with the original stem and `.mp3` extension. Assembly
uses `ffmpeg` and publishes via atomic replacement after successful encoding;
a failed refresh preserves the previous complete MP3. Use `--speed` with any
finite positive value supported by ffmpeg's pitch-preserving tempo filters;
`1` leaves tempo unchanged. The MP3 is a lossy delivery format; the retained
WAVs preserve native PCM.

## Verification

See [the epic verification record](developer/2026-11-13-markdown-to-audiobook/verification.md)
for the existing test evidence mapped to each user story, dependency and check
results, provider-probe limits, and the pending listening check.
