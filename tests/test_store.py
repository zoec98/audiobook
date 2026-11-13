from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from audiobook.contracts import (
    AnnotatedChapter,
    AnnotationIdentity,
    AudioError,
    AudioFormat,
    AudioIdentity,
    ChapterCompletion,
    FinalOutputIdentity,
    Insertion,
    InsertionRecord,
    PreparedChapter,
    RawAudio,
    SourceIdentity,
    StaleArtifactError,
)
from audiobook.store import WorkStore


def test_annotation_round_trip_keeps_source_and_prompt_provenance(
    tmp_path: Path,
) -> None:
    original = "Hello, friend."
    source = SourceIdentity(
        path=tmp_path / "1-hello.md",
        content_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    identity = AnnotationIdentity(
        source=source,
        model_id="gpt-6-luna",
        reasoning_effort="medium",
        prompt_version="direction-v1",
    )
    prepared = PreparedChapter(
        source=source,
        original_markdown=original,
        narration="Hello, friend.",
        insertion_positions=(7,),
        diagnostics=(),
    )
    annotation = AnnotatedChapter(
        prepared=prepared,
        markdown="Hello, [warmly]friend.",
        narration="Hello, warmly friend.",
        insertion_record=InsertionRecord(
            source_sha256=source.content_sha256,
            prompt_version="direction-v1",
            insertions=(Insertion(anchor=7, tag="[warmly]"),),
            provider_response_id="resp-17",
        ),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")

    store.save_annotation(identity, annotation)
    result = store.load_annotation(identity)

    assert result.state == "valid"
    assert result.value == annotation
    assert result.value.prepared.original_markdown == original
    assert result.value.insertion_record.provider_response_id == "resp-17"


def test_same_filename_in_different_directories_has_separate_annotations(
    tmp_path: Path,
) -> None:
    original = "Hello, friend."
    content_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    first_source = SourceIdentity(tmp_path / "first" / "1-hello.md", content_hash)
    second_source = SourceIdentity(tmp_path / "second" / "1-hello.md", content_hash)
    root = tmp_path / "audiobook_tmp"

    def make_annotation(source: SourceIdentity, tag: str, response_id: str):
        identity = AnnotationIdentity(source, "gpt-6-luna", "medium", "direction-v1")
        prepared = PreparedChapter(
            source=source,
            original_markdown=original,
            narration=original,
            insertion_positions=(7,),
            diagnostics=(),
        )
        annotation = AnnotatedChapter(
            prepared=prepared,
            markdown=f"Hello, {tag}friend.",
            narration=f"Hello, {tag[1:-1]} friend.",
            insertion_record=InsertionRecord(
                source_sha256=content_hash,
                prompt_version="direction-v1",
                insertions=(Insertion(anchor=7, tag=tag),),
                provider_response_id=response_id,
            ),
        )
        return identity, annotation

    first_identity, first_annotation = make_annotation(
        first_source, "[warmly]", "resp-first"
    )
    second_identity, second_annotation = make_annotation(
        second_source, "[brightly]", "resp-second"
    )
    first_store = WorkStore(first_source, root=root)
    second_store = WorkStore(second_source, root=root)

    first_store.save_annotation(first_identity, first_annotation)
    second_store.save_annotation(second_identity, second_annotation)

    assert first_store.load_annotation(first_identity).value == first_annotation
    assert second_store.load_annotation(second_identity).value == second_annotation


def test_annotation_from_an_old_prompt_is_reported_stale(tmp_path: Path) -> None:
    original = "Hello, friend."
    source = SourceIdentity(
        path=tmp_path / "1-hello.md",
        content_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    identity = AnnotationIdentity(source, "gpt-6-luna", "medium", "direction-v1")
    prepared = PreparedChapter(source, original, original, (7,), ())
    annotation = AnnotatedChapter(
        prepared,
        "Hello, [warmly]friend.",
        "Hello, warmly friend.",
        InsertionRecord(
            source.content_sha256,
            "direction-v1",
            (Insertion(7, "[warmly]"),),
        ),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_annotation(identity, annotation)

    result = store.load_annotation(
        AnnotationIdentity(source, "gpt-6-luna", "medium", "direction-v2")
    )

    assert result.state == "stale"


def test_invalid_annotation_cannot_replace_a_valid_cached_copy(tmp_path: Path) -> None:
    original = "Hi."
    source = SourceIdentity(
        tmp_path / "1-hello.md",
        hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    identity = AnnotationIdentity(source, "gpt-6-luna", "medium", "direction-v1")
    valid = AnnotatedChapter(
        PreparedChapter(source, original, original, (0,), ()),
        "[calm]Hi.",
        "calm Hi.",
        InsertionRecord(
            source.content_sha256,
            "direction-v1",
            (Insertion(0, "[calm]"),),
        ),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_annotation(identity, valid)

    with pytest.raises(StaleArtifactError, match="exact-preservation"):
        store.save_annotation(identity, replace(valid, markdown="[calm]Changed."))

    assert store.load_annotation(identity).value == valid


def test_loaded_annotation_with_rewritten_source_text_is_incomplete(
    tmp_path: Path,
) -> None:
    original = "Hi."
    source = SourceIdentity(
        tmp_path / "1-hello.md",
        hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    identity = AnnotationIdentity(source, "gpt-6-luna", "medium", "direction-v1")
    annotation = AnnotatedChapter(
        PreparedChapter(source, original, original, (0,), ()),
        "[calm]Hi.",
        "calm Hi.",
        InsertionRecord(
            source.content_sha256,
            "direction-v1",
            (Insertion(0, "[calm]"),),
        ),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_annotation(identity, annotation)
    record = next((tmp_path / "audiobook_tmp").rglob("annotation.json"))
    document = json.loads(record.read_text(encoding="utf-8"))
    document["chapter"]["markdown"] = "[calm]Rewritten."
    record.write_text(json.dumps(document), encoding="utf-8")

    result = store.load_annotation(identity)

    assert result.state == "incomplete"
    assert result.value is None


def test_chunk_round_trip_keeps_pcm_and_format_metadata(tmp_path: Path) -> None:
    original = "Hello, friend."
    source = SourceIdentity(
        path=tmp_path / "1-hello.md",
        content_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    identity = AudioIdentity(
        submitted_text_sha256="a" * 64,
        model_id="eleven_v4_turbo",
        voice_id="voice-42",
        language="en",
        generation_settings_sha256="b" * 64,
    )
    audio = RawAudio(
        pcm=b"\x01\x02\x03\x04",
        format=AudioFormat(
            sample_rate_hz=24_000,
            sample_width_bytes=2,
            channels=1,
            encoding="pcm_s16le",
        ),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")

    store.save_chunk(identity, audio)
    result = store.load_chunk(identity)

    assert result.state == "valid"
    assert result.value == audio


def test_completion_round_trip_records_the_source_and_output_identity(
    tmp_path: Path,
) -> None:
    original = "Hello, friend."
    source = SourceIdentity(
        path=tmp_path / "1-hello.md",
        content_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )
    audio_identity = AudioIdentity(
        submitted_text_sha256="a" * 64,
        model_id="eleven_v4_turbo",
        voice_id="voice-42",
        language="en",
        generation_settings_sha256="b" * 64,
    )
    output_identity = FinalOutputIdentity(audio=audio_identity, speed=Decimal(1))
    completion = ChapterCompletion(
        source=source,
        final_output=output_identity,
        mp3_sha256="c" * 64,
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")

    store.save_completion(output_identity, completion)
    result = store.load_completion(output_identity)

    assert result.state == "valid"
    assert result.value == completion


def test_partial_pcm_artifact_is_not_returned_as_a_complete_chunk(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(tmp_path / "1-hello.md", "d" * 64)
    identity = AudioIdentity("a" * 64, "eleven_v4", "voice-42", "en", "b" * 64)
    audio = RawAudio(
        b"\x01\x02\x03\x04",
        AudioFormat(24_000, 2, 1, "pcm_s16le"),
    )
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_chunk(identity, audio)
    pcm_file = next((tmp_path / "audiobook_tmp").rglob("*.pcm"))
    pcm_file.write_bytes(b"\x01\x02")

    result = store.load_chunk(identity)

    assert result.state == "incomplete"
    assert result.value is None


def test_synthesis_settings_invalidate_audio_but_speed_only_rebuilds_output(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(tmp_path / "1-hello.md", "d" * 64)
    identity = AudioIdentity("a" * 64, "eleven_v4_turbo", "voice-42", "en", "b" * 64)
    audio = RawAudio(b"\x01\x02", AudioFormat(24_000, 2, 1, "pcm_s16le"))
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_chunk(identity, audio)

    for changed_identity in (
        replace(identity, model_id="eleven_v4"),
        replace(identity, voice_id="voice-99"),
        replace(identity, language="de"),
        replace(identity, generation_settings_sha256="c" * 64),
    ):
        assert store.load_chunk(changed_identity).state == "stale"
    assert (
        store.load_chunk(replace(identity, submitted_text_sha256="f" * 64)).state
        == "absent"
    )

    completed_at_normal_speed = FinalOutputIdentity(identity, Decimal(1))
    completion = ChapterCompletion(source, completed_at_normal_speed, "e" * 64)
    store.save_completion(completed_at_normal_speed, completion)
    completed_at_new_speed = FinalOutputIdentity(identity, Decimal("1.25"))

    assert store.load_chunk(completed_at_new_speed.audio).value == audio
    assert store.load_completion(completed_at_new_speed).state == "stale"
    with pytest.raises(StaleArtifactError):
        store.save_completion(completed_at_new_speed, completion)
    assert store.load_completion(completed_at_normal_speed).state == "valid"


def test_invalid_refresh_does_not_replace_the_previous_complete_chunk(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(tmp_path / "1-hello.md", "d" * 64)
    identity = AudioIdentity("a" * 64, "eleven_v4_turbo", "voice-42", "en", "b" * 64)
    complete_audio = RawAudio(b"\x01\x02", AudioFormat(24_000, 2, 1, "pcm_s16le"))
    invalid_audio = RawAudio(b"\x01", AudioFormat(24_000, 2, 1, "pcm_s16le"))
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_chunk(identity, complete_audio)

    with pytest.raises(AudioError, match="invalid PCM"):
        store.save_chunk(identity, invalid_audio)

    assert store.load_chunk(identity).value == complete_audio


def test_invalid_chunk_text_digest_is_rejected_before_path_lookup(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(tmp_path / "1-hello.md", "d" * 64)
    identity = AudioIdentity("../outside", "eleven_v4", "voice-42", "en", "b" * 64)
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")

    with pytest.raises(StaleArtifactError, match="text digest"):
        store.load_chunk(identity)


def test_completion_from_changed_source_is_reported_stale(tmp_path: Path) -> None:
    source_path = tmp_path / "1-hello.md"
    old_source = SourceIdentity(source_path, "d" * 64)
    new_source = SourceIdentity(source_path, "e" * 64)
    audio_identity = AudioIdentity("a" * 64, "eleven_v4", "voice-42", "en", "b" * 64)
    output_identity = FinalOutputIdentity(audio_identity, Decimal(1))
    old_completion = ChapterCompletion(old_source, output_identity, "f" * 64)
    old_store = WorkStore(old_source, root=tmp_path / "audiobook_tmp")
    old_store.save_completion(output_identity, old_completion)

    result = WorkStore(new_source, root=tmp_path / "audiobook_tmp").load_completion(
        output_identity
    )

    assert result.state == "stale"


def test_corrupt_completion_record_is_not_returned_as_valid(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(tmp_path / "1-hello.md", "d" * 64)
    audio_identity = AudioIdentity("a" * 64, "eleven_v4", "voice-42", "en", "b" * 64)
    output_identity = FinalOutputIdentity(audio_identity, Decimal(1))
    completion = ChapterCompletion(source, output_identity, "f" * 64)
    store = WorkStore(source, root=tmp_path / "audiobook_tmp")
    store.save_completion(output_identity, completion)
    record = next((tmp_path / "audiobook_tmp").rglob("completion.json"))
    record.write_text('{"version":', encoding="utf-8")

    result = store.load_completion(output_identity)

    assert result.state == "incomplete"
    assert result.value is None
