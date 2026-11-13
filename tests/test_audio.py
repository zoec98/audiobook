import math
import shutil
import struct
import subprocess
import wave
from decimal import Decimal
from itertools import pairwise

import pytest

from audiobook.audio import assemble_chapter
from audiobook.contracts import AudioError, AudioFormat, RawAudio


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_assemble_chapter_retains_pcm_chunks_with_their_declared_format(
    tmp_path,
) -> None:
    audio_format = AudioFormat(32_000, 2, 2, "pcm_s16le")
    first_pcm = struct.pack("<hh", 700, -700) * 12_000
    second_pcm = struct.pack("<hh", -300, 300) * 12_000
    destination = tmp_path / "audio & books" / "chapter, 1!.mp3"

    result = assemble_chapter(
        (RawAudio(first_pcm, audio_format), RawAudio(second_pcm, audio_format)),
        destination,
        Decimal(1),
    )

    assert result.mp3_path == destination
    assert destination.is_file()
    assert len(result.chunk_wav_paths) == 2
    for path, expected_pcm in zip(
        result.chunk_wav_paths, (first_pcm, second_pcm), strict=True
    ):
        with wave.open(str(path), "rb") as chunk:
            assert chunk.getframerate() == 32_000
            assert chunk.getnchannels() == 2
            assert chunk.getsampwidth() == 2
            assert chunk.readframes(chunk.getnframes()) == expected_pcm


def test_assemble_chapter_rejects_unsupported_pcm_encoding(tmp_path) -> None:
    audio = RawAudio(b"\x01\x00", AudioFormat(24_000, 2, 1, "pcm_s16be"))
    destination = tmp_path / "chapter.mp3"

    with pytest.raises(AudioError, match="Unsupported PCM encoding"):
        assemble_chapter((audio,), destination, Decimal(1))

    assert not destination.exists()


def test_assemble_chapter_rejects_mixed_chunk_formats(tmp_path) -> None:
    chunks = (
        RawAudio(b"\x01\x00", AudioFormat(24_000, 2, 1, "pcm_s16le")),
        RawAudio(b"\x01\x00", AudioFormat(48_000, 2, 1, "pcm_s16le")),
    )

    with pytest.raises(AudioError, match="matching PCM formats"):
        assemble_chapter(chunks, tmp_path / "chapter.mp3", Decimal(1))


@pytest.mark.parametrize(
    "speed", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")]
)
def test_assemble_chapter_rejects_invalid_speed(speed, tmp_path) -> None:
    audio = RawAudio(b"\x01\x00", AudioFormat(24_000, 2, 1, "pcm_s16le"))

    with pytest.raises(AudioError, match="finite positive"):
        assemble_chapter((audio,), tmp_path / "chapter.mp3", speed)


def test_assemble_chapter_rejects_incomplete_pcm_frames(tmp_path) -> None:
    audio = RawAudio(b"\x01", AudioFormat(24_000, 2, 1, "pcm_s16le"))

    with pytest.raises(AudioError, match="incomplete PCM frame"):
        assemble_chapter((audio,), tmp_path / "chapter.mp3", Decimal(1))


def test_assemble_chapter_requires_ffmpeg_with_an_actionable_error(
    tmp_path, monkeypatch
) -> None:
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    audio = RawAudio(b"\x01\x00", AudioFormat(24_000, 2, 1, "pcm_s16le"))

    with pytest.raises(AudioError, match="ffmpeg is required"):
        assemble_chapter((audio,), tmp_path / "chapter.mp3", Decimal(1))


def test_failed_ffmpeg_preserves_previous_mp3_and_reusable_chunk_wav(
    tmp_path, monkeypatch
) -> None:
    real_ffmpeg = shutil.which("ffmpeg")
    if real_ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    audio = RawAudio(
        struct.pack("<h", 300) * 12_000,
        AudioFormat(24_000, 2, 1, "pcm_s16le"),
    )
    destination = tmp_path / "chapter.mp3"
    first = assemble_chapter((audio,), destination, Decimal(1))
    destination.write_bytes(b"previous mp3 remains intact")
    previous_chunk = first.chunk_wav_paths[0].read_bytes()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_ffmpeg.write_text("#!/bin/sh\nexit 1\n")
    fake_ffmpeg.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(AudioError, match="ffmpeg could not encode"):
        assemble_chapter((audio,), destination, Decimal(1))

    assert destination.read_bytes() == b"previous mp3 remains intact"
    assert first.chunk_wav_paths[0].read_bytes() == previous_chunk


def _tone_pcm(frequency_hz: int, duration_seconds: int, sample_rate_hz: int) -> bytes:
    samples = (
        int(12_000 * math.sin(2 * math.pi * frequency_hz * frame / sample_rate_hz))
        for frame in range(duration_seconds * sample_rate_hz)
    )
    return b"".join(struct.pack("<h", sample) for sample in samples)


def _decode_mp3(ffmpeg: str, path, output_path) -> list[int]:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "24000",
            str(output_path),
        ],
        capture_output=True,
        check=True,
    )
    decoded = output_path.read_bytes()
    return [sample[0] for sample in struct.iter_unpack("<h", decoded)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_tempo_changes_mp3_duration_while_preserving_tone_pitch(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    audio = RawAudio(_tone_pcm(440, 2, 24_000), AudioFormat(24_000, 2, 1, "pcm_s16le"))
    normal_path = tmp_path / "normal.mp3"
    faster_path = tmp_path / "faster.mp3"

    assemble_chapter((audio,), normal_path, Decimal(1))
    assemble_chapter((audio,), faster_path, Decimal("1.5"))

    normal_samples = _decode_mp3(ffmpeg, normal_path, tmp_path / "normal.pcm")
    faster_samples = _decode_mp3(ffmpeg, faster_path, tmp_path / "faster.pcm")
    normal_duration = len(normal_samples) / 24_000
    faster_duration = len(faster_samples) / 24_000
    faster_segment = faster_samples[7_200:16_800]
    zero_crossings = sum(
        (left <= 0 < right) or (left > 0 >= right)
        for left, right in pairwise(faster_segment)
    )
    faster_frequency = zero_crossings * 24_000 / (2 * len(faster_segment))

    assert faster_duration == pytest.approx(normal_duration / 1.5, abs=0.12)
    assert faster_duration < normal_duration
    assert faster_frequency == pytest.approx(440, abs=12)
