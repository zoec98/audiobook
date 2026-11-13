"""Lossless chunk WAV assembly and atomic chapter MP3 publishing."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from audiobook.contracts import AssemblyResult, AudioError, AudioFormat, RawAudio

_PCM_WIDTHS = {
    "pcm_u8": 1,
    "pcm_s16le": 2,
    "pcm_s24le": 3,
    "pcm_s32le": 4,
}
_TEMPO_MIN = Decimal("0.5")
_TEMPO_MAX = Decimal(2)
_MAX_TEMPO_FILTERS = 32


def assemble_chapter(
    audio: Sequence[RawAudio], destination: Path, speed: Decimal
) -> AssemblyResult:
    """Wrap ordered PCM chunks as WAVs and atomically publish one MP3 chapter."""
    if not audio:
        raise AudioError("A chapter needs at least one complete audio chunk")

    speed_value = _validated_speed(speed)
    audio_format = audio[0].format
    for chunk in audio:
        _validate_chunk(chunk)
        if chunk.format != audio_format:
            raise AudioError("All chapter chunks must have matching PCM formats")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioError("ffmpeg is required to encode the chapter MP3")

    destination = Path(destination)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AudioError(
            f"Could not create the chapter output directory: {error}"
        ) from error
    retained_paths: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.stem}-assemble-", dir=destination.parent
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        temporary_chunks: list[Path] = []
        for index, chunk in enumerate(audio):
            temporary_path = temporary_root / f"chunk-{index:04d}.wav"
            _write_wav(temporary_path, chunk)
            temporary_chunks.append(temporary_path)
            retained_paths.append(_retained_wav_path(destination, index, chunk))

        joined_wav = temporary_root / "chapter.wav"
        _write_joined_wav(joined_wav, audio, audio_format)
        temporary_mp3 = temporary_root / "chapter.mp3"
        _encode_mp3(ffmpeg, joined_wav, temporary_mp3, speed_value)

        try:
            for temporary_path, retained_path in zip(
                temporary_chunks, retained_paths, strict=True
            ):
                os.replace(temporary_path, retained_path)
            os.replace(temporary_mp3, destination)
        except OSError as error:
            raise AudioError(f"Could not publish chapter audio: {error}") from error

    return AssemblyResult(chunk_wav_paths=tuple(retained_paths), mp3_path=destination)


def _validated_speed(speed: Decimal) -> Decimal:
    try:
        result = speed if isinstance(speed, Decimal) else Decimal(str(speed))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise AudioError("Playback speed must be a finite positive number") from error
    if not result.is_finite() or result <= 0:
        raise AudioError("Playback speed must be a finite positive number")
    return result


def _validate_chunk(chunk: RawAudio) -> None:
    audio_format = chunk.format
    integer_metadata = (
        audio_format.sample_rate_hz,
        audio_format.sample_width_bytes,
        audio_format.channels,
    )
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in integer_metadata
    ):
        raise AudioError("PCM sample rate, width, and channel count must be integers")
    if not isinstance(audio_format.encoding, str):
        raise AudioError("PCM encoding metadata must be a string")
    expected_width = _PCM_WIDTHS.get(audio_format.encoding)
    if expected_width is None or audio_format.sample_width_bytes != expected_width:
        raise AudioError(
            "Unsupported PCM encoding or sample width: "
            f"{audio_format.encoding} with {audio_format.sample_width_bytes} bytes"
        )
    if (
        not 0 < audio_format.sample_rate_hz <= 0xFFFFFFFF
        or not 0 < audio_format.channels <= 0xFFFF
        or not isinstance(chunk.pcm, bytes)
        or not chunk.pcm
    ):
        raise AudioError("A chapter chunk has invalid PCM metadata or empty audio")
    frame_width = audio_format.sample_width_bytes * audio_format.channels
    if len(chunk.pcm) % frame_width:
        raise AudioError("A chapter chunk contains an incomplete PCM frame")


def _retained_wav_path(destination: Path, index: int, chunk: RawAudio) -> Path:
    digest = hashlib.sha256(
        chunk.pcm
        + b"\0"
        + str(chunk.format.sample_rate_hz).encode("ascii")
        + b"\0"
        + str(chunk.format.sample_width_bytes).encode("ascii")
        + b"\0"
        + str(chunk.format.channels).encode("ascii")
        + b"\0"
        + chunk.format.encoding.encode("ascii")
    ).hexdigest()[:16]
    return destination.with_name(f"{destination.stem}.chunk-{index:04d}-{digest}.wav")


def _write_wav(path: Path, chunk: RawAudio) -> None:
    try:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(chunk.format.channels)
            output.setsampwidth(chunk.format.sample_width_bytes)
            output.setframerate(chunk.format.sample_rate_hz)
            output.writeframes(chunk.pcm)
    except (OSError, OverflowError, struct.error, wave.Error) as error:
        raise AudioError(f"Could not write a valid chunk WAV: {error}") from error


def _write_joined_wav(
    path: Path, audio: Sequence[RawAudio], audio_format: AudioFormat
) -> None:
    try:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(audio_format.channels)
            output.setsampwidth(audio_format.sample_width_bytes)
            output.setframerate(audio_format.sample_rate_hz)
            for chunk in audio:
                output.writeframesraw(chunk.pcm)
    except (OSError, OverflowError, struct.error, wave.Error) as error:
        raise AudioError(f"Could not write a valid combined WAV: {error}") from error


def _encode_mp3(
    ffmpeg: str, source_wav: Path, destination_mp3: Path, speed: Decimal
) -> None:
    filters = ",".join(f"atempo={part}" for part in _tempo_parts(speed))
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source_wav),
        "-filter:a",
        filters,
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        "-f",
        "mp3",
        str(destination_mp3),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise AudioError(
            f"Could not run ffmpeg to encode the chapter: {error}"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffmpeg exited unsuccessfully"
        raise AudioError(f"ffmpeg could not encode the chapter MP3: {detail}")
    if not destination_mp3.is_file() or destination_mp3.stat().st_size == 0:
        raise AudioError("ffmpeg did not produce a playable chapter MP3")


def _tempo_parts(speed: Decimal) -> tuple[str, ...]:
    remaining = speed
    parts: list[Decimal] = []
    for _ in range(_MAX_TEMPO_FILTERS):
        if remaining > _TEMPO_MAX:
            parts.append(_TEMPO_MAX)
            remaining /= _TEMPO_MAX
        elif remaining < _TEMPO_MIN:
            parts.append(_TEMPO_MIN)
            remaining /= _TEMPO_MIN
        else:
            parts.append(remaining)
            return tuple(format(part.normalize(), "f") for part in parts)
    raise AudioError(
        "Playback speed is outside ffmpeg's supported pitch-preserving range"
    )
