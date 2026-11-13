"""Persistent, user-inspectable work artifacts for one source chapter."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from audiobook.contracts import (
    AnnotatedChapter,
    AnnotationIdentity,
    ArtifactResult,
    AudioError,
    AudioFormat,
    AudioIdentity,
    ChapterCompletion,
    FinalOutputIdentity,
    Insertion,
    InsertionRecord,
    PreparedChapter,
    RawAudio,
    SourceDiagnostic,
    SourceIdentity,
    SourcePosition,
    StaleArtifactError,
)

_FORMAT_VERSION = 1
_UNREADABLE = object()


class WorkStore:
    """Read and atomically write artifacts scoped to one source path."""

    def __init__(self, source: SourceIdentity, root: Path | None = None) -> None:
        self.source = source
        self.root = (root or Path.cwd() / "audiobook_tmp").expanduser()
        source_path = _absolute_path(source.path)
        path_digest = hashlib.sha256(os.fsencode(source_path)).hexdigest()
        source_name = source_path.name[:96]
        self._source_directory = self.root / f"{source_name}.{path_digest}"

    def load_annotation(
        self, identity: AnnotationIdentity
    ) -> ArtifactResult[AnnotatedChapter]:
        """Load an annotation only when its source, prompt, and insertions agree."""
        path = self._source_directory / "annotation.json"
        document = _read_document(path)
        if document is None:
            return ArtifactResult(state="absent")
        if document is _UNREADABLE or not isinstance(document, dict):
            return ArtifactResult(
                state="incomplete", reason="Annotation record is not an object"
            )
        version = document.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            return ArtifactResult(
                state="incomplete", reason="Annotation record version is malformed"
            )
        if version != _FORMAT_VERSION:
            return ArtifactResult(
                state="stale", reason="Annotation record version is incompatible"
            )
        try:
            stored_identity = _annotation_identity_from(document["identity"])
        except (KeyError, TypeError, ValueError):
            return ArtifactResult(
                state="incomplete", reason="Annotation identity is malformed"
            )
        if not _same_annotation_identity(stored_identity, identity):
            return ArtifactResult(
                state="stale", reason="Annotation source or prompt has changed"
            )
        try:
            chapter = _annotated_chapter_from(document["chapter"])
        except (KeyError, TypeError, ValueError):
            return ArtifactResult(
                state="incomplete", reason="Annotation artifact is malformed"
            )
        if not _annotation_preserves_source(identity, chapter):
            return ArtifactResult(
                state="incomplete",
                reason="Annotation does not preserve its original source and insertions",
            )
        return ArtifactResult(state="valid", value=chapter)

    def save_annotation(
        self, identity: AnnotationIdentity, chapter: AnnotatedChapter
    ) -> None:
        """Atomically retain a source-preserving annotation and its provenance."""
        self._require_source(identity.source)
        if not _annotation_preserves_source(identity, chapter):
            raise StaleArtifactError(
                "Annotation source, prompt provenance, or exact-preservation evidence is invalid"
            )
        document = {
            "version": _FORMAT_VERSION,
            "identity": _annotation_identity_to(identity),
            "chapter": _annotated_chapter_to(chapter),
        }
        _atomic_write_json(self._source_directory / "annotation.json", document)

    def _chunk_path(self, identity: AudioIdentity) -> Path:
        if not _valid_sha256(identity.submitted_text_sha256):
            raise StaleArtifactError(
                "Chunk submitted text digest must be a SHA-256 hex value"
            )
        return _chunk_record_path(identity, self._source_directory)

    def load_chunk(self, identity: AudioIdentity) -> ArtifactResult[RawAudio]:
        """Load a complete PCM chunk only when every generation setting matches."""
        path = self._chunk_path(identity)
        document = _read_document(path)
        if document is None:
            return ArtifactResult(state="absent")
        if document is _UNREADABLE or not isinstance(document, dict):
            return ArtifactResult(
                state="incomplete", reason="Chunk record is malformed"
            )
        version = document.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            return ArtifactResult(
                state="incomplete", reason="Chunk record version is malformed"
            )
        if version != _FORMAT_VERSION:
            return ArtifactResult(
                state="stale", reason="Chunk record version is incompatible"
            )
        try:
            stored_identity = _audio_identity_from(document["identity"])
        except (KeyError, TypeError, ValueError):
            return ArtifactResult(
                state="incomplete", reason="Chunk identity is malformed"
            )
        if stored_identity != identity:
            return ArtifactResult(
                state="stale", reason="Chunk synthesis settings have changed"
            )
        try:
            pcm_name = _required_string(document, "pcm_file")
            if Path(pcm_name).name != pcm_name or not pcm_name.endswith(".pcm"):
                raise ValueError("PCM path is invalid")
            pcm_path = path.parent / pcm_name
            pcm = pcm_path.read_bytes()
            audio_format = _audio_format_from(document["format"])
            expected_size = _required_int(document, "pcm_size")
            expected_digest = _required_string(document, "pcm_sha256")
        except (OSError, KeyError, TypeError, ValueError):
            return ArtifactResult(
                state="incomplete", reason="Chunk audio is missing or malformed"
            )
        audio = RawAudio(pcm=pcm, format=audio_format)
        if (
            len(pcm) != expected_size
            or hashlib.sha256(pcm).hexdigest() != expected_digest
            or not _valid_audio(audio)
        ):
            return ArtifactResult(
                state="incomplete", reason="Chunk audio failed integrity checks"
            )
        return ArtifactResult(state="valid", value=audio)

    def save_chunk(self, identity: AudioIdentity, audio: RawAudio) -> None:
        """Publish raw PCM before its atomic compatibility record."""
        if not _valid_audio(audio):
            raise AudioError("Raw audio is empty or has invalid PCM format metadata")
        record_path = self._chunk_path(identity)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        old_document = _read_document(record_path)
        pcm_name = f"{identity.submitted_text_sha256}.{os.urandom(8).hex()}.pcm"
        pcm_path = record_path.parent / pcm_name
        _atomic_write_bytes(pcm_path, audio.pcm)
        document = {
            "version": _FORMAT_VERSION,
            "identity": _audio_identity_to(identity),
            "format": _audio_format_to(audio.format),
            "pcm_file": pcm_name,
            "pcm_size": len(audio.pcm),
            "pcm_sha256": hashlib.sha256(audio.pcm).hexdigest(),
        }
        try:
            _atomic_write_json(record_path, document)
        except OSError:
            pcm_path.unlink(missing_ok=True)
            raise
        if isinstance(old_document, dict):
            old_name = old_document.get("pcm_file")
            if isinstance(old_name, str) and Path(old_name).name == old_name:
                (record_path.parent / old_name).unlink(missing_ok=True)

    def load_completion(
        self, identity: FinalOutputIdentity
    ) -> ArtifactResult[ChapterCompletion]:
        """Load completion only for the same source, synthesis, and playback speed."""
        document = _read_document(self._source_directory / "completion.json")
        if document is None:
            return ArtifactResult(state="absent")
        if document is _UNREADABLE or not isinstance(document, dict):
            return ArtifactResult(
                state="incomplete", reason="Completion record is malformed"
            )
        version = document.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            return ArtifactResult(
                state="incomplete", reason="Completion record version is malformed"
            )
        if version != _FORMAT_VERSION:
            return ArtifactResult(
                state="stale", reason="Completion record version is incompatible"
            )
        try:
            stored_identity = _final_output_identity_from(document["identity"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return ArtifactResult(
                state="incomplete", reason="Completion identity is malformed"
            )
        if stored_identity != identity:
            return ArtifactResult(
                state="stale", reason="Completed output settings have changed"
            )
        try:
            completion = _chapter_completion_from(document["completion"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return ArtifactResult(
                state="incomplete", reason="Completion artifact is malformed"
            )
        if not _same_source(completion.source, self.source):
            return ArtifactResult(state="stale", reason="Completed source has changed")
        if completion.final_output != identity:
            return ArtifactResult(
                state="stale", reason="Completed output settings have changed"
            )
        if not _valid_sha256(completion.mp3_sha256):
            return ArtifactResult(
                state="incomplete", reason="Completed MP3 digest is invalid"
            )
        return ArtifactResult(state="valid", value=completion)

    def save_completion(
        self, identity: FinalOutputIdentity, completion: ChapterCompletion
    ) -> None:
        """Atomically mark a chapter complete after its replacement is published."""
        self._require_source(completion.source)
        if completion.final_output != identity or not _valid_sha256(
            completion.mp3_sha256
        ):
            raise StaleArtifactError(
                "Completion source, output identity, or MP3 digest is incompatible"
            )
        document = {
            "version": _FORMAT_VERSION,
            "identity": _final_output_identity_to(identity),
            "completion": _chapter_completion_to(completion),
        }
        _atomic_write_json(self._source_directory / "completion.json", document)

    def _require_source(self, source: SourceIdentity) -> None:
        if (
            _absolute_path(source.path) != _absolute_path(self.source.path)
            or source.content_sha256 != self.source.content_sha256
        ):
            raise StaleArtifactError("Work store belongs to a different source chapter")


def _source_identity_to(source: SourceIdentity) -> dict[str, str]:
    return {
        "path": str(_absolute_path(source.path)),
        "content_sha256": source.content_sha256,
    }


def _absolute_path(path: Path) -> Path:
    return path.expanduser().absolute()


def _same_source(left: SourceIdentity, right: SourceIdentity) -> bool:
    return (
        _absolute_path(left.path) == _absolute_path(right.path)
        and left.content_sha256 == right.content_sha256
    )


def _same_annotation_identity(
    left: AnnotationIdentity, right: AnnotationIdentity
) -> bool:
    return (
        _same_source(left.source, right.source)
        and left.model_id == right.model_id
        and left.reasoning_effort == right.reasoning_effort
        and left.prompt_version == right.prompt_version
    )


def _source_identity_from(value: Any) -> SourceIdentity:
    if not isinstance(value, dict):
        raise TypeError("source identity must be an object")
    return SourceIdentity(
        path=Path(_required_string(value, "path")),
        content_sha256=_required_string(value, "content_sha256"),
    )


def _annotation_identity_to(identity: AnnotationIdentity) -> dict[str, Any]:
    return {
        "source": _source_identity_to(identity.source),
        "model_id": identity.model_id,
        "reasoning_effort": identity.reasoning_effort,
        "prompt_version": identity.prompt_version,
    }


def _annotation_identity_from(value: Any) -> AnnotationIdentity:
    if not isinstance(value, dict):
        raise TypeError("annotation identity must be an object")
    return AnnotationIdentity(
        source=_source_identity_from(value["source"]),
        model_id=_required_string(value, "model_id"),
        reasoning_effort=_required_string(value, "reasoning_effort"),
        prompt_version=_required_string(value, "prompt_version"),
    )


def _audio_identity_to(identity: AudioIdentity) -> dict[str, str]:
    return {
        "submitted_text_sha256": identity.submitted_text_sha256,
        "model_id": identity.model_id,
        "voice_id": identity.voice_id,
        "language": identity.language,
        "generation_settings_sha256": identity.generation_settings_sha256,
    }


def _audio_identity_from(value: Any) -> AudioIdentity:
    if not isinstance(value, dict):
        raise TypeError("audio identity must be an object")
    return AudioIdentity(
        submitted_text_sha256=_required_string(value, "submitted_text_sha256"),
        model_id=_required_string(value, "model_id"),
        voice_id=_required_string(value, "voice_id"),
        language=_required_string(value, "language"),
        generation_settings_sha256=_required_string(
            value, "generation_settings_sha256"
        ),
    )


def _final_output_identity_to(identity: FinalOutputIdentity) -> dict[str, Any]:
    return {"audio": _audio_identity_to(identity.audio), "speed": str(identity.speed)}


def _final_output_identity_from(value: Any) -> FinalOutputIdentity:
    if not isinstance(value, dict):
        raise TypeError("final-output identity must be an object")
    speed = Decimal(_required_string(value, "speed"))
    if not speed.is_finite() or speed <= 0:
        raise ValueError("playback speed must be finite and positive")
    return FinalOutputIdentity(
        audio=_audio_identity_from(value["audio"]),
        speed=speed,
    )


def _chapter_completion_to(completion: ChapterCompletion) -> dict[str, Any]:
    return {
        "source": _source_identity_to(completion.source),
        "final_output": _final_output_identity_to(completion.final_output),
        "mp3_sha256": completion.mp3_sha256,
    }


def _chapter_completion_from(value: Any) -> ChapterCompletion:
    if not isinstance(value, dict):
        raise TypeError("chapter completion must be an object")
    return ChapterCompletion(
        source=_source_identity_from(value["source"]),
        final_output=_final_output_identity_from(value["final_output"]),
        mp3_sha256=_required_string(value, "mp3_sha256"),
    )


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _audio_format_to(audio_format: AudioFormat) -> dict[str, Any]:
    return {
        "sample_rate_hz": audio_format.sample_rate_hz,
        "sample_width_bytes": audio_format.sample_width_bytes,
        "channels": audio_format.channels,
        "encoding": audio_format.encoding,
    }


def _audio_format_from(value: Any) -> AudioFormat:
    if not isinstance(value, dict):
        raise TypeError("audio format must be an object")
    return AudioFormat(
        sample_rate_hz=_required_int(value, "sample_rate_hz"),
        sample_width_bytes=_required_int(value, "sample_width_bytes"),
        channels=_required_int(value, "channels"),
        encoding=_required_string(value, "encoding"),
    )


def _valid_audio(audio: RawAudio) -> bool:
    audio_format = audio.format
    if (
        not audio.pcm
        or audio_format.sample_rate_hz <= 0
        or audio_format.sample_width_bytes <= 0
        or audio_format.channels <= 0
        or not audio_format.encoding
    ):
        return False
    frame_width = audio_format.sample_width_bytes * audio_format.channels
    return len(audio.pcm) % frame_width == 0


def _chunk_record_path(identity: AudioIdentity, source_directory: Path) -> Path:
    return source_directory / "chunks" / f"{identity.submitted_text_sha256}.json"


def _annotated_chapter_to(chapter: AnnotatedChapter) -> dict[str, Any]:
    prepared = chapter.prepared
    return {
        "prepared": {
            "source": _source_identity_to(prepared.source),
            "original_markdown": prepared.original_markdown,
            "narration": prepared.narration,
            "insertion_positions": list(prepared.insertion_positions),
            "diagnostics": [
                {
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "position": {
                        "offset": diagnostic.position.offset,
                        "line": diagnostic.position.line,
                        "character": diagnostic.position.character,
                    },
                    "severity": diagnostic.severity,
                }
                for diagnostic in prepared.diagnostics
            ],
        },
        "markdown": chapter.markdown,
        "narration": chapter.narration,
        "insertion_record": {
            "source_sha256": chapter.insertion_record.source_sha256,
            "prompt_version": chapter.insertion_record.prompt_version,
            "insertions": [
                {"anchor": insertion.anchor, "tag": insertion.tag}
                for insertion in chapter.insertion_record.insertions
            ],
            "provider_response_id": chapter.insertion_record.provider_response_id,
        },
    }


def _annotated_chapter_from(value: Any) -> AnnotatedChapter:
    if not isinstance(value, dict):
        raise TypeError("annotated chapter must be an object")
    prepared_value = value["prepared"]
    if not isinstance(prepared_value, dict):
        raise TypeError("prepared chapter must be an object")
    positions = prepared_value["insertion_positions"]
    diagnostics_value = prepared_value["diagnostics"]
    insertions_value = value["insertion_record"]["insertions"]
    if not isinstance(positions, list) or not isinstance(diagnostics_value, list):
        raise TypeError("prepared chapter collections must be arrays")
    if not isinstance(insertions_value, list):
        raise TypeError("insertions must be an array")
    diagnostics: list[SourceDiagnostic] = []
    for item in diagnostics_value:
        if not isinstance(item, dict) or not isinstance(item.get("position"), dict):
            raise TypeError("source diagnostic is malformed")
        location = item["position"]
        diagnostics.append(
            SourceDiagnostic(
                code=_required_string(item, "code"),
                message=_required_string(item, "message"),
                position=SourcePosition(
                    offset=_required_int(location, "offset"),
                    line=_required_int(location, "line"),
                    character=_required_int(location, "character"),
                ),
                severity=_diagnostic_severity(_required_string(item, "severity")),
            )
        )
    insertions = tuple(
        Insertion(
            anchor=_required_int(item, "anchor"), tag=_required_string(item, "tag")
        )
        for item in insertions_value
        if isinstance(item, dict)
    )
    if len(insertions) != len(insertions_value):
        raise TypeError("insertion is malformed")
    insertion_value = value["insertion_record"]
    response_id = insertion_value.get("provider_response_id")
    if response_id is not None and not isinstance(response_id, str):
        raise TypeError("provider response ID is malformed")
    return AnnotatedChapter(
        prepared=PreparedChapter(
            source=_source_identity_from(prepared_value["source"]),
            original_markdown=_required_string(prepared_value, "original_markdown"),
            narration=_required_string(prepared_value, "narration"),
            insertion_positions=tuple(_require_int(item) for item in positions),
            diagnostics=tuple(diagnostics),
        ),
        markdown=_required_string(value, "markdown"),
        narration=_required_string(value, "narration"),
        insertion_record=InsertionRecord(
            source_sha256=_required_string(insertion_value, "source_sha256"),
            prompt_version=_required_string(insertion_value, "prompt_version"),
            insertions=insertions,
            provider_response_id=response_id,
        ),
    )


def _annotation_preserves_source(
    identity: AnnotationIdentity, chapter: AnnotatedChapter
) -> bool:
    prepared = chapter.prepared
    record = chapter.insertion_record
    source = identity.source
    if (
        _absolute_path(prepared.source.path) != _absolute_path(source.path)
        or prepared.source.content_sha256 != source.content_sha256
        or record.source_sha256 != source.content_sha256
        or record.prompt_version != identity.prompt_version
    ):
        return False
    original = prepared.original_markdown
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != source.content_sha256:
        return False
    allowed_positions = set(prepared.insertion_positions)
    pieces: list[str] = []
    cursor = 0
    for insertion in record.insertions:
        if (
            insertion.anchor < cursor
            or insertion.anchor > len(original)
            or insertion.anchor not in allowed_positions
            or not insertion.tag
        ):
            return False
        pieces.append(original[cursor : insertion.anchor])
        pieces.append(insertion.tag)
        cursor = insertion.anchor
    pieces.append(original[cursor:])
    return "".join(pieces) == chapter.markdown


def _read_document(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _UNREADABLE


def _atomic_write_json(path: Path, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, data)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TypeError(f"{key} must be a string")
    return item


def _diagnostic_severity(value: str) -> Literal["error", "warning"]:
    if value == "error":
        return "error"
    if value == "warning":
        return "warning"
    raise ValueError("diagnostic severity is invalid")


def _required_int(value: dict[str, Any], key: str) -> int:
    return _require_int(value[key])


def _require_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("value must be an integer")
    return value
