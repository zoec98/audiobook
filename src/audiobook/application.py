"""Orchestrate chapter preparation and the audiobook command workflow."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from audiobook import audio, elevenlabs
from audiobook.annotation import annotate_chapter
from audiobook.annotation_prompt_v1 import PROMPT_VERSION
from audiobook.contracts import (
    AnnotatedChapter,
    AnnotationIdentity,
    AudioError,
    AudioIdentity,
    ChapterCompletion,
    ChunkPlan,
    ConfigurationError,
    FinalOutputIdentity,
    IncompleteAudioError,
    ModelCapabilities,
    PreparedChapter,
    RawAudio,
    RunSettings,
    SourceValidationError,
    SpeechChunk,
    UncertainOperationError,
)
from audiobook.language import choose_language
from audiobook.markdown import prepare_chapter
from audiobook.planning import plan_chapter
from audiobook.store import WorkStore

_LANGUAGES = frozenset(
    [
        "af",
        "am",
        "ar",
        "hy",
        "as",
        "ast",
        "az",
        "be",
        "bn",
        "bs",
        "bg",
        "my",
        "yue",
        "ca",
        "ceb",
        "ny",
        "hr",
        "cs",
        "da",
        "nl",
        "en",
        "et",
        "fil",
        "fi",
        "fr",
        "ff",
        "gl",
        "ka",
        "de",
        "el",
        "gu",
        "ha",
        "he",
        "hi",
        "hu",
        "is",
        "id",
        "ga",
        "it",
        "ja",
        "jv",
        "kam",
        "kn",
        "kk",
        "ky",
        "ko",
        "lo",
        "lv",
        "ln",
        "lt",
        "lg",
        "lb",
        "mk",
        "ms",
        "ml",
        "mt",
        "zh",
        "mi",
        "mr",
        "mn",
        "ne",
        "no",
        "oc",
        "or",
        "ps",
        "fa",
        "pl",
        "pt",
        "pa",
        "ro",
        "ru",
        "sr",
        "sn",
        "sd",
        "sk",
        "sl",
        "so",
        "ckb",
        "es",
        "sw",
        "sv",
        "tg",
        "ta",
        "te",
        "th",
        "tr",
        "uk",
        "ur",
        "uz",
        "vi",
        "cy",
        "wo",
        "yo",
        "zu",
    ]
)
_MODEL_RATES = {
    "eleven_v4_turbo": Decimal("0.04"),
    "eleven_v4": Decimal("0.08"),
}
_MODEL_LIMIT = 10_000
_ANNOTATION_MODEL = "gpt-6-luna"
_ANNOTATION_EFFORT = "medium"


@dataclass(frozen=True)
class _ChapterWork:
    chapter: PreparedChapter
    store: WorkStore
    plan: ChunkPlan


def run(settings: RunSettings) -> int:
    """Run source validation, planning, cache checks, and requested rendering."""
    from audiobook.cli import discover_chapters

    chapter_paths = discover_chapters(settings.input_dir)
    prepared = tuple(prepare_chapter(path) for path in chapter_paths)
    _report_diagnostics(prepared)
    if not settings.force and any(
        diagnostic.severity == "error"
        for chapter in prepared
        for diagnostic in chapter.diagnostics
    ):
        raise SourceValidationError(
            "Source validation failed before paid work; correct the files or use --force."
        )

    capabilities = _model_capabilities(settings.model)
    openai_api_key = settings.openai_api_key
    if not openai_api_key:
        raise ConfigurationError(
            "OPENAI_API_KEY is required; set it in .env or ~/.audiobook.env"
        )
    elevenlabs_api_key = settings.elevenlabs_api_key
    if not settings.dry_run and not elevenlabs_api_key:
        raise ConfigurationError(
            "ELEVENLABS_API_KEY is required for audio generation; "
            "set it in .env or ~/.audiobook.env"
        )
    if not settings.dry_run and shutil.which("ffmpeg") is None:
        raise AudioError("ffmpeg is required to encode the chapter MP3")

    language = choose_language(
        prepared, settings.language, capabilities.supported_languages
    )
    print(f"Language: {language}")

    selected = prepared[:1] if settings.sample else prepared
    work: list[_ChapterWork] = []
    with httpx.Client(
        timeout=60.0,
        headers={"Authorization": f"Bearer {openai_api_key}"},
    ) as openai_client:
        for chapter in selected:
            store = WorkStore(chapter.source)
            identity = AnnotationIdentity(
                source=chapter.source,
                model_id=_ANNOTATION_MODEL,
                reasoning_effort=_ANNOTATION_EFFORT,
                prompt_version=PROMPT_VERSION,
            )
            annotation = _load_or_create_annotation(
                chapter, store, identity, openai_client, settings.no_cache
            )
            plan = plan_chapter(annotation.narration, capabilities)
            _save_plan(chapter, plan, language)
            _report_plan(chapter, plan)
            work.append(_ChapterWork(chapter, store, plan))

    if settings.dry_run:
        return 0
    with httpx.Client(timeout=60.0) as elevenlabs_client:
        voice_id = elevenlabs.resolve_voice(
            _voice_selector(settings.voice),
            elevenlabs_api_key or "",
            model_id=settings.model,
            client=elevenlabs_client,
        )
        for item in work:
            _render_chapter(
                item,
                settings,
                language,
                voice_id,
                elevenlabs_api_key or "",
                elevenlabs_client,
            )
    return 0


def _model_capabilities(model_id: str) -> ModelCapabilities:
    rate = _MODEL_RATES.get(model_id)
    if rate is None:
        raise ConfigurationError(f"Unsupported speech model {model_id!r}")
    return ModelCapabilities(
        model_id=model_id,
        max_characters=_MODEL_LIMIT,
        supported_languages=_LANGUAGES,
        cost_per_1000_characters=rate,
    )


def _report_diagnostics(chapters: tuple[PreparedChapter, ...]) -> None:
    for chapter in chapters:
        for diagnostic in chapter.diagnostics:
            position = diagnostic.position
            print(
                f"{chapter.source.path.name}:{position.line}:{position.character}: "
                f"{diagnostic.severity}: {diagnostic.message}",
                file=sys.stderr,
            )


def _load_or_create_annotation(
    chapter: PreparedChapter,
    store: WorkStore,
    identity: AnnotationIdentity,
    provider: httpx.Client,
    no_cache: bool,
) -> AnnotatedChapter:
    if not no_cache:
        cached = store.load_annotation(identity)
        if cached.state == "valid" and cached.value is not None:
            print(f"using cached file {chapter.source.path.name}")
            return cached.value
        if cached.state in {"stale", "incomplete"}:
            reason = cached.reason or "the cached annotation is incompatible"
            raise ConfigurationError(
                f"Cannot use the cached annotation for {chapter.source.path.name}: "
                f"{reason}. Run with --no-cache to refresh it."
            )

    annotated = annotate_chapter(chapter, provider)
    store.save_annotation(identity, annotated)
    return annotated


def _save_plan(
    chapter: PreparedChapter,
    plan: ChunkPlan,
    language: str,
) -> None:
    source_path = chapter.source.path.expanduser().absolute()
    path_digest = hashlib.sha256(os.fsencode(source_path)).hexdigest()
    destination = (
        Path.cwd()
        / "audiobook_tmp"
        / "plans"
        / f"{source_path.stem}.{path_digest}.json"
    )
    document = {
        "version": 1,
        "source": str(source_path),
        "source_sha256": chapter.source.content_sha256,
        "language": language,
        "model_id": plan.model_id,
        "cost_per_1000_characters": str(plan.cost_per_1000_characters),
        "submitted_characters": plan.submitted_characters,
        "estimated_cost": str(plan.estimated_cost),
        "currency": plan.currency,
        "chunks": [
            {
                "index": chunk.index,
                "text": chunk.text,
                "character_count": chunk.character_count,
                "estimated_cost": str(chunk.estimated_cost),
            }
            for chunk in plan.chunks
        ],
    }
    _atomic_write_json(destination, document)


def _atomic_write_json(destination: Path, document: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(document, temporary, ensure_ascii=False, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _report_plan(chapter: PreparedChapter, plan: ChunkPlan) -> None:
    print(f"{chapter.source.path.name}: {len(plan.chunks)} ordered chunk(s)")
    for chunk in plan.chunks:
        print(
            f"  Chunk {chunk.index + 1}: {chunk.character_count} characters, "
            f"estimated ${chunk.estimated_cost:.4f} {plan.currency}"
        )
    print(
        f"  Estimated narration: ${plan.estimated_cost:.4f} {plan.currency} "
        f"at ${plan.cost_per_1000_characters:.2f} per 1,000 characters."
    )


def _voice_selector(selector: str) -> str | None:
    """Resolve the product's Dahlia label through its approved default ID."""
    return None if selector == "Dahlia" else selector


def _render_chapter(
    item: _ChapterWork,
    settings: RunSettings,
    language: str,
    voice_id: str,
    api_key: str,
    provider: httpx.Client,
) -> None:
    chapter = item.chapter
    chunks = item.plan.chunks[:1] if settings.sample else item.plan.chunks
    generation_settings = _generation_settings_digest()
    final_audio_identity = AudioIdentity(
        submitted_text_sha256=_chapter_text_digest(item.plan.chunks),
        model_id=settings.model,
        voice_id=voice_id,
        language=language,
        generation_settings_sha256=generation_settings,
    )
    final_identity = FinalOutputIdentity(
        audio=final_audio_identity,
        speed=settings.speed,
    )
    destination = settings.output_dir / (
        f"{chapter.source.path.stem}.sample.mp3"
        if settings.sample
        else f"{chapter.source.path.stem}.mp3"
    )

    if not settings.sample and not settings.no_cache:
        completion = item.store.load_completion(final_identity)
        if (
            completion.state == "valid"
            and completion.value is not None
            and _file_digest(destination) == completion.value.mp3_sha256
        ):
            print(f"using completed file {destination.name}")
            return

    raw_chunks: list[RawAudio] = []
    for chunk in chunks:
        identity = _chunk_identity(
            chunk, settings.model, voice_id, language, generation_settings
        )
        cached = None if settings.no_cache else item.store.load_chunk(identity)
        if cached is not None and cached.state == "valid" and cached.value is not None:
            raw_chunks.append(cached.value)
            print(
                f"using cached audio chunk {chunk.index + 1} "
                f"for {chapter.source.path.name}"
            )
            continue
        if cached is not None and cached.state in {"stale", "incomplete"}:
            reason = cached.reason or "the cached audio is incompatible"
            raise ConfigurationError(
                f"Cannot use cached audio chunk {chunk.index + 1} for "
                f"{chapter.source.path.name}: {reason}. "
                "Run with --no-cache to refresh it."
            )

        if not settings.no_cache:
            uncertain = item.store.load_uncertain_chunk(identity)
            if uncertain.state == "valid":
                raise UncertainOperationError(
                    f"The previous synthesis outcome for chunk {chunk.index + 1} "
                    f"of {chapter.source.path.name} is uncertain and may have been "
                    "charged. Do not retry it automatically; use --no-cache for an "
                    "explicit fresh operation."
                )
            if uncertain.state in {"stale", "incomplete"}:
                reason = uncertain.reason or "the uncertain-operation record is invalid"
                raise ConfigurationError(
                    f"Cannot safely resume chunk {chunk.index + 1} for "
                    f"{chapter.source.path.name}: {reason}. "
                    "Run with --no-cache to start a fresh operation."
                )

        try:
            generated = elevenlabs.synthesize_chunk(
                chunk.text,
                voice_id,
                settings.model,
                language,
                api_key,
                client=provider,
                websocket_url=elevenlabs.TURBO_WS_URL,
            )
        except (IncompleteAudioError, UncertainOperationError):
            _record_uncertain_chunk(item.store, identity)
            raise
        try:
            item.store.save_chunk(identity, generated)
        except (AudioError, OSError) as storage_error:
            _record_uncertain_chunk(item.store, identity)
            raise UncertainOperationError(
                "ElevenLabs returned PCM that could not be validated or stored; "
                "the paid result is uncertain. Run with --no-cache for a fresh "
                "operation."
            ) from storage_error
        try:
            item.store.clear_uncertain_chunk(identity)
        except OSError:
            pass
        raw_chunks.append(generated)

    if not raw_chunks:
        raise AudioError(
            f"No speech chunks were planned for {chapter.source.path.name}"
        )

    stage = destination.with_name(f".{destination.stem}.{uuid4().hex}.pending.mp3")
    try:
        result = audio.assemble_chapter(raw_chunks, stage, settings.speed)
        _preserve_wavs(item.store, chapter, result.chunk_wav_paths)
        os.replace(stage, destination)
    except OSError as error:
        raise AudioError(f"Could not publish {destination.name}: {error}") from error
    finally:
        stage.unlink(missing_ok=True)

    if not settings.sample:
        completion = ChapterCompletion(
            source=chapter.source,
            final_output=final_identity,
            mp3_sha256=_file_digest(destination),
        )
        item.store.save_completion(final_identity, completion)
    print(f"created {destination.name}")


def _chunk_identity(
    chunk: SpeechChunk,
    model_id: str,
    voice_id: str,
    language: str,
    generation_settings: str,
) -> AudioIdentity:
    return AudioIdentity(
        submitted_text_sha256=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        model_id=model_id,
        voice_id=voice_id,
        language=language,
        generation_settings_sha256=generation_settings,
    )


def _chapter_text_digest(chunks: tuple[SpeechChunk, ...]) -> str:
    submitted_text = json.dumps(
        [chunk.text for chunk in chunks], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(submitted_text.encode("utf-8")).hexdigest()


def _generation_settings_digest() -> str:
    settings = json.dumps(
        {"output_format": "pcm_24000"}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(settings.encode("utf-8")).hexdigest()


def _record_uncertain_chunk(store: WorkStore, identity: AudioIdentity) -> None:
    try:
        store.save_uncertain_chunk(identity)
    except OSError as storage_error:
        raise UncertainOperationError(
            "The synthesis outcome is uncertain and could not be recorded; "
            "do not retry the paid operation blindly."
        ) from storage_error


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _preserve_wavs(
    store: WorkStore, chapter: PreparedChapter, paths: tuple[Path, ...]
) -> None:
    source_path = chapter.source.path.expanduser().absolute()
    path_digest = hashlib.sha256(os.fsencode(source_path)).hexdigest()
    directory = store.root / "wav" / f"{source_path.stem}.{path_digest}"
    directory.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = directory / f"chunk-{index:04d}-{digest}.wav"
        os.replace(source, destination)
