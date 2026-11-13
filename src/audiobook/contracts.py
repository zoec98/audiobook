"""Public settings contract shared by the command and processing stages."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

SUPPORTED_SPEECH_MODELS = ("eleven_v4_turbo", "eleven_v4")


class AudiobookError(Exception):
    """Base class for user-actionable audiobook errors."""


class ConfigurationError(AudiobookError):
    """The command settings cannot be used to start a run."""


class InputError(AudiobookError):
    """The requested chapter input cannot be discovered or read."""


class SourceValidationError(AudiobookError):
    """A source chapter has diagnostics that block normal processing."""


class ProviderError(AudiobookError):
    """An external provider rejected or failed a request."""

    def __init__(self, message: str, *, debug_details: str | None = None) -> None:
        super().__init__(message)
        self.debug_details = debug_details


class UncertainOperationError(ProviderError):
    """A paid provider operation may have completed despite a lost response."""


class IncompleteAudioError(ProviderError):
    """An audio stream ended before the provider signalled completion."""


class AudioError(AudiobookError):
    """Audio data could not be validated, assembled, or published."""


class StaleArtifactError(AudiobookError):
    """A cached artifact does not match the current source or settings."""


@dataclass(frozen=True)
class RunSettings:
    """Validated command options and credentials for one run."""

    input_dir: Path
    output_dir: Path
    language: str | None
    voice: str
    model: str
    speed: Decimal
    force: bool
    dry_run: bool
    sample: bool
    no_cache: bool
    debug: bool = False
    openai_api_key: str | None = field(default=None, repr=False)
    elevenlabs_api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SourceIdentity:
    """Absolute source path and SHA-256 of its original bytes."""

    path: Path
    content_sha256: str


@dataclass(frozen=True)
class SourcePosition:
    """A Unicode code-point offset and one-based line/character coordinates."""

    offset: int
    line: int
    character: int


@dataclass(frozen=True)
class SourceDiagnostic:
    """A source-positioned message produced while preparing Markdown."""

    code: str
    message: str
    position: SourcePosition
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class PreparedChapter:
    """Original Markdown and its validated, source-positioned speech view."""

    source: SourceIdentity
    original_markdown: str
    narration: str
    insertion_positions: tuple[int, ...]
    diagnostics: tuple[SourceDiagnostic, ...]


@dataclass(frozen=True)
class Insertion:
    """A vocal direction inserted at a boundary in the unchanged original."""

    anchor: int
    tag: str


@dataclass(frozen=True)
class InsertionRecord:
    """Validated insertions and the prompt/source provenance that produced them."""

    source_sha256: str
    prompt_version: str
    insertions: tuple[Insertion, ...]
    provider_response_id: str | None = None


@dataclass(frozen=True)
class AnnotationIdentity:
    """Inputs that must match before an annotated chapter can be reused."""

    source: SourceIdentity
    model_id: str
    reasoning_effort: str
    prompt_version: str


@dataclass(frozen=True)
class AnnotatedChapter:
    """A source-preserving annotated copy and the narration derived from it."""

    prepared: PreparedChapter
    markdown: str
    narration: str
    insertion_record: InsertionRecord


@dataclass(frozen=True)
class ModelCapabilities:
    """Verified limits and estimate data for one explicit speech model ID."""

    model_id: str
    max_characters: int
    supported_languages: frozenset[str]
    cost_per_1000_characters: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class SpeechChunk:
    """One ordered, complete passage submitted to speech synthesis."""

    index: int
    text: str
    character_count: int
    estimated_cost: Decimal


@dataclass(frozen=True)
class ChunkPlan:
    """An ordered plan and estimate for one model's narration text."""

    model_id: str
    chunks: tuple[SpeechChunk, ...]
    submitted_characters: int
    cost_per_1000_characters: Decimal
    estimated_cost: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class AudioFormat:
    """Containerless audio format metadata returned by a provider."""

    sample_rate_hz: int
    sample_width_bytes: int
    channels: int
    encoding: str


@dataclass(frozen=True)
class RawAudio:
    """Complete raw PCM and the format needed to wrap it without conversion."""

    pcm: bytes
    format: AudioFormat


@dataclass(frozen=True)
class AudioIdentity:
    """Settings that must match before raw synthesized audio can be reused."""

    submitted_text_sha256: str
    model_id: str
    voice_id: str
    language: str
    generation_settings_sha256: str


@dataclass(frozen=True)
class FinalOutputIdentity:
    """Raw-audio compatibility plus output-only processing settings."""

    audio: AudioIdentity
    speed: Decimal


@dataclass(frozen=True)
class AssemblyResult:
    """Published output paths after successful WAV and MP3 assembly."""

    chunk_wav_paths: tuple[Path, ...]
    mp3_path: Path


@dataclass(frozen=True)
class ChapterCompletion:
    """Compatibility evidence for a completely rendered source chapter."""

    source: SourceIdentity
    final_output: FinalOutputIdentity
    mp3_sha256: str


ArtifactState = Literal["absent", "valid", "stale", "incomplete"]


@dataclass(frozen=True)
class ArtifactResult[ArtifactValue]:
    """A cache lookup that distinguishes absent, valid, stale, and partial data."""

    state: ArtifactState
    value: ArtifactValue | None = None
    reason: str | None = None
