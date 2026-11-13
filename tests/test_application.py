from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from websockets.sync.server import serve

from audiobook import elevenlabs
from audiobook.cli import main

_DEFAULT_DAHLIA_ID = "JaagUurP1dmW3WscoJ79"


def _install_provider_transport(monkeypatch, respond) -> None:
    real_client = httpx.Client

    def client_factory(*args, **kwargs) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)


def _completed_annotation() -> dict[str, object]:
    return {
        "id": "resp-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"insertions": []}'}],
            }
        ],
    }


def _pcm_tone(frequency: int) -> bytes:
    sample_rate = 24_000
    return b"".join(
        struct.pack(
            "<h", round(6_000 * math.sin(2 * math.pi * frequency * frame / sample_rate))
        )
        for frame in range(sample_rate // 3)
    )


def test_dry_run_keeps_annotation_and_plan_without_speech_generation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    source = input_dir / "1-opening.md"
    original = "We began at sunrise."
    source.write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == "api.openai.com"
        assert request.url.path == "/v1/responses"
        body = json.loads(request.read())
        assert body["model"] == "gpt-6-luna"
        assert body["reasoning"] == {"effort": "medium"}
        assert body["input"] == original
        return httpx.Response(
            200,
            json={
                "id": "resp-dry-run",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": '{"insertions": []}'}
                        ],
                    }
                ],
            },
        )

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        ["--input-dir", str(input_dir), "--output-dir", str(output_dir), "--dry-run"]
    )

    assert exit_code == 0
    assert source.read_text(encoding="utf-8") == original
    assert len(requests) == 1
    assert output_dir.exists() is False
    report = capsys.readouterr()
    assert "Language: en" in report.out
    assert "1-opening.md" in report.out
    assert "20 characters" in report.out
    assert "$0.0008" in report.out
    assert "0.04 per 1,000 characters" in report.out
    assert report.err == ""

    plan_files = list((tmp_path / "audiobook_tmp" / "plans").glob("*.json"))
    assert len(plan_files) == 1
    plan = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert plan["model_id"] == "eleven_v4_turbo"
    assert [chunk["text"] for chunk in plan["chunks"]] == [original]
    assert plan["estimated_cost"] == "0.0008"


def test_ineligible_voice_stops_the_run_before_any_speech_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    (input_dir / "1-opening.md").write_text("We began at sunrise.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    request_paths: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        request_paths.append((request.method, request.url.path))
        if request.url.host == "api.openai.com":
            return httpx.Response(
                200,
                json={
                    "id": "resp-voice-test",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": '{"insertions": []}'}
                            ],
                        }
                    ],
                },
            )
        assert request.url.path == "/v2/voices"
        assert request.url.params["include_live_moderated"] == "false"
        assert request.headers["xi-api-key"] == "test-elevenlabs-key"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "name": "Dahlia - Vintage Romantic Hollywood Star",
                        "voice_id": _DEFAULT_DAHLIA_ID,
                        "sharing": {"live_moderation_enabled": True},
                        "high_quality_base_model_ids": ["eleven_v4"],
                    }
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            "finished",
            "--model",
            "eleven_v4",
        ]
    )

    assert exit_code == 1
    assert request_paths == [("POST", "/v1/responses"), ("GET", "/v2/voices")]
    error = capsys.readouterr().err
    assert "live moderation" in error
    assert "test-elevenlabs-key" not in error
    assert not (tmp_path / "finished" / "1-opening.mp3").exists()


def test_standard_v4_renders_chapters_in_numeric_order_with_native_wavs(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    second = input_dir / "2-second.md"
    first = input_dir / "1-first.md"
    first.write_text("The first chapter begins.", encoding="utf-8")
    second.write_text("The second chapter follows.", encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    requests: list[tuple[str, str]] = []
    annotations: list[str] = []
    speech_requests: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.host == "api.openai.com":
            body = json.loads(request.read())
            assert request.headers["authorization"] == "Bearer test-openai-key"
            annotations.append(body["input"])
            return httpx.Response(200, json=_completed_annotation())
        assert request.url.host == "api.elevenlabs.io"
        assert request.headers["xi-api-key"] == "test-elevenlabs-key"
        assert request.headers.get("authorization") is None
        if request.method == "GET":
            assert request.url.path == "/v2/voices"
            assert request.url.params["include_live_moderated"] == "false"
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": [
                                "eleven_v4_turbo",
                                "eleven_v4",
                            ],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )
        assert request.url.path == f"/v1/text-to-speech/{_DEFAULT_DAHLIA_ID}"
        assert request.url.params["output_format"] == "pcm_24000"
        body = json.loads(request.read())
        assert body["model_id"] == "eleven_v4"
        assert body["language_code"] == "en"
        speech_requests.append(body)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(210 + 20 * len(speech_requests)),
        )

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "eleven_v4",
            "--language",
            "en",
        ]
    )

    assert exit_code == 0
    assert annotations == ["The first chapter begins.", "The second chapter follows."]
    assert [request["text"] for request in speech_requests] == annotations
    assert requests == [
        ("POST", "/v1/responses"),
        ("POST", "/v1/responses"),
        ("GET", "/v2/voices"),
        ("POST", f"/v1/text-to-speech/{_DEFAULT_DAHLIA_ID}"),
        ("POST", f"/v1/text-to-speech/{_DEFAULT_DAHLIA_ID}"),
    ]
    assert (output_dir / "1-first.mp3").is_file()
    assert (output_dir / "2-second.mp3").is_file()
    assert len(list((tmp_path / "audiobook_tmp" / "wav").glob("**/*.wav"))) == 2
    report = capsys.readouterr().out
    assert "$0.0020 USD at $0.08 per 1,000 characters." in report
    assert "$0.0022 USD at $0.08 per 1,000 characters." in report
    assert first.read_text(encoding="utf-8") == "The first chapter begins."
    assert second.read_text(encoding="utf-8") == "The second chapter follows."


def test_sample_uses_default_turbo_and_publishes_only_a_sample_artifact(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    first_text = "A" * 600 + ". " + "B" * 600 + "."
    first_chunk = "A" * 600 + ". "
    first = input_dir / "1-first.md"
    second = input_dir / "2-second.md"
    first.write_text(first_text, encoding="utf-8")
    second.write_text(
        "This later chapter is not annotated or spoken.", encoding="utf-8"
    )
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    annotation_inputs: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            body = json.loads(request.read())
            annotation_inputs.append(body["input"])
            return httpx.Response(200, json=_completed_annotation())
        assert request.url.host == "api.elevenlabs.io"
        assert request.method == "GET"
        assert request.url.params["include_live_moderated"] == "false"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "name": "Dahlia - Vintage Romantic Hollywood Star",
                        "voice_id": _DEFAULT_DAHLIA_ID,
                        "sharing": {"live_moderation_enabled": False},
                        "high_quality_base_model_ids": ["eleven_v4_turbo"],
                    }
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    _install_provider_transport(monkeypatch, respond)

    received: dict[str, object] = {}
    pcm = _pcm_tone(260)

    def peer(connection) -> None:
        received["path"] = connection.request.path
        received["headers"] = dict(connection.request.headers)
        received["messages"] = [
            json.loads(connection.recv(timeout=5)) for _ in range(3)
        ]
        connection.send(json.dumps({"audio": base64.b64encode(pcm).decode("ascii")}))
        connection.send(json.dumps({"is_final_audio_for_turn": True}))
        connection.send(json.dumps({"is_final": True}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    monkeypatch.setattr(elevenlabs, "TURBO_WS_URL", f"ws://127.0.0.1:{port}")
    try:
        exit_code = main(
            [
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--sample",
                "--language",
                "en",
            ]
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert exit_code == 0
    assert annotation_inputs == [first_text]
    assert (output_dir / "1-first.sample.mp3").is_file()
    assert not (output_dir / "1-first.mp3").exists()
    assert not (output_dir / "2-second.mp3").exists()
    assert list((tmp_path / "audiobook_tmp" / "wav").glob("**/*.wav"))
    assert list((tmp_path / "audiobook_tmp").glob("**/*.pcm"))
    assert not list((tmp_path / "audiobook_tmp").glob("**/completion.json"))
    plan = json.loads(
        next((tmp_path / "audiobook_tmp" / "plans").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert [chunk["text"] for chunk in plan["chunks"]] == [
        first_chunk,
        "B" * 600 + ".",
    ]

    path = urlsplit(str(received["path"]))
    assert parse_qs(path.query) == {
        "model_id": ["eleven_v4_turbo"],
        "output_format": ["pcm_24000"],
        "language_code": ["en"],
    }
    assert received["messages"] == [
        {"voices": [_DEFAULT_DAHLIA_ID]},
        {"inputs": [{"text": first_chunk, "voice_id": _DEFAULT_DAHLIA_ID}]},
        {"close_socket": True},
    ]
    headers = received["headers"]
    assert isinstance(headers, dict)
    assert headers["xi-api-key"] == "test-elevenlabs-key"
    assert "test-elevenlabs-key" not in capsys.readouterr().err


def test_completed_chapter_reuse_refresh_and_smaller_chunk_budget(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    source = input_dir / "1-reuse.md"
    original = "A" * 600 + ". " + "B" * 600 + "."
    source.write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    monkeypatch.setattr("audiobook.application._NARRATION_CHUNK_LIMIT", 1600)

    annotations: list[str] = []
    voice_lookups = 0
    speech_requests: list[dict[str, str]] = []
    request_order: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            request_order.append("annotation")
            body = json.loads(request.read())
            annotations.append(body["input"])
            return httpx.Response(200, json=_completed_annotation())
        assert request.url.host == "api.elevenlabs.io"
        if request.method == "GET":
            nonlocal voice_lookups
            voice_lookups += 1
            request_order.append("voice lookup")
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )
        request_order.append("speech")
        body = json.loads(request.read())
        assert body["model_id"] == "eleven_v4"
        speech_requests.append(body)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(200 + 40 * len(speech_requests)),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]

    assert main(arguments) == 0
    completed = output_dir / "1-reuse.mp3"
    first_digest = hashlib.sha256(completed.read_bytes()).hexdigest()
    assert request_order == ["annotation", "voice lookup", "speech"]
    first_report = capsys.readouterr().out
    assert "created 1-reuse.mp3" in first_report

    assert main(arguments) == 0
    second_digest = hashlib.sha256(completed.read_bytes()).hexdigest()
    second_report = capsys.readouterr().out
    assert annotations == [original]
    assert len(speech_requests) == 1
    assert voice_lookups == 2
    assert second_digest == first_digest
    assert "using cached file 1-reuse.md" in second_report
    assert "using completed file 1-reuse.mp3" in second_report

    assert main([*arguments, "--no-cache"]) == 0
    third_digest = hashlib.sha256(completed.read_bytes()).hexdigest()
    third_report = capsys.readouterr().out
    assert annotations == [original, original]
    assert len(speech_requests) == 2
    assert voice_lookups == 3
    assert third_digest != second_digest
    assert "using cached file" not in third_report

    # A previously completed long request must not hide a new, shorter plan.
    monkeypatch.setattr("audiobook.application._NARRATION_CHUNK_LIMIT", 800)
    assert main(arguments) == 0
    fourth_report = capsys.readouterr().out
    assert "using completed file" not in fourth_report
    assert annotations == [original, original]
    assert len(speech_requests) == 4
    assert all(len(request["text"]) <= 800 for request in speech_requests[-2:])
    assert "".join(request["text"] for request in speech_requests[-2:]) == original
    assert "created 1-reuse.mp3" in third_report


def test_all_sources_are_validated_before_any_provider_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    (input_dir / "1-valid.md").write_text(
        "The first chapter is valid.", encoding="utf-8"
    )
    invalid = input_dir / "2-invalid.md"
    invalid.write_text("The second chapter has [risas].\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("provider calls must follow source validation")

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(["--input-dir", str(input_dir), "--language", "en"])

    assert exit_code == 1
    assert requests == []
    error = capsys.readouterr().err
    assert "2-invalid.md:1:24" in error
    assert "--force" in error
    assert (input_dir / "1-valid.md").read_text(encoding="utf-8") == (
        "The first chapter is valid."
    )
    assert invalid.read_text(encoding="utf-8") == "The second chapter has [risas].\n"


def test_source_diagnostic_shows_the_offending_line_and_caret(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    (input_dir / "1-offending.md").write_text(
        "A sentence.\n> This block is unsupported.\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("provider calls must follow source validation")

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(["--input-dir", str(input_dir), "--language", "en"])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert (
        "1-offending.md:2:1: error: unsupported block Markdown is retained in forced narration\n"
        "  2 | > This block is unsupported.\n"
        "    | ^\n"
    ) in error
    assert requests == []


def test_force_reports_source_diagnostics_and_keeps_questionable_prose(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    source = input_dir / "1-forced.md"
    original = "He saw [risas] near the door."
    source.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    annotation_inputs: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openai.com"
        annotation_inputs.append(json.loads(request.read())["input"])
        return httpx.Response(200, json=_completed_annotation())

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--dry-run",
            "--force",
            "--language",
            "en",
        ]
    )

    assert exit_code == 0
    report = capsys.readouterr()
    assert "1-forced.md:1:8" in report.err
    assert f"  1 | He saw [risas] near the door.\n    | {' ' * 7}^\n" in report.err
    assert annotation_inputs == [original]
    assert source.read_text(encoding="utf-8") == original


def test_dry_run_sample_plans_only_the_first_chapter_without_elevenlabs_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    first = input_dir / "1-first.md"
    first_text = "The first chapter is prepared."
    first.write_text(first_text, encoding="utf-8")
    (input_dir / "2-later.md").write_text(
        "This later chapter is not annotated.", encoding="utf-8"
    )
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    annotation_inputs: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openai.com"
        annotation_inputs.append(json.loads(request.read())["input"])
        return httpx.Response(200, json=_completed_annotation())

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--sample",
            "--language",
            "en",
        ]
    )

    assert exit_code == 0
    assert annotation_inputs == [first_text]
    assert len(list((tmp_path / "audiobook_tmp" / "plans").glob("*.json"))) == 1
    assert not output_dir.exists()
    assert not list((tmp_path / "audiobook_tmp").glob("**/*.pcm"))
    assert "1-first.md" in capsys.readouterr().out
    plan = json.loads(
        next((tmp_path / "audiobook_tmp" / "plans").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert plan["chunks"][0]["text"] == first_text


def test_stale_annotation_requires_an_explicit_no_cache_refresh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    source = input_dir / "1-changed.md"
    source.write_text("The original chapter is here.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    annotations: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openai.com"
        annotations.append(json.loads(request.read())["input"])
        return httpx.Response(200, json=_completed_annotation())

    _install_provider_transport(monkeypatch, respond)
    arguments = ["--input-dir", str(input_dir), "--dry-run", "--language", "en"]

    assert main(arguments) == 0
    capsys.readouterr()
    source.write_text("The changed chapter is here.", encoding="utf-8")

    assert main([*arguments, "--force"]) == 1
    error = capsys.readouterr().err
    assert "--no-cache" in error
    assert annotations == ["The original chapter is here."]

    assert main([*arguments, "--no-cache"]) == 0
    assert annotations == [
        "The original chapter is here.",
        "The changed chapter is here.",
    ]


def test_missing_ffmpeg_is_reported_before_provider_requests(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    (input_dir / "1-chapter.md").write_text(
        "The chapter is ready to render.", encoding="utf-8"
    )
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completed_annotation())

    _install_provider_transport(monkeypatch, respond)

    exit_code = main(
        ["--input-dir", str(input_dir), "--output-dir", "finished", "--language", "en"]
    )

    assert exit_code == 1
    assert requests == []
    assert "ffmpeg is required" in capsys.readouterr().err


def test_malformed_cached_pcm_requires_explicit_refresh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    first_text = "A" * 600 + ". "
    second_text = "B" * 600 + "."
    source = input_dir / "1-resume.md"
    source.write_text(first_text + second_text, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    rejected_second_chunk = False
    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal rejected_second_chunk
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        body = json.loads(request.read())
        speech_submissions.append(body["text"])
        if body["text"] == second_text and not rejected_second_chunk:
            rejected_second_chunk = True
            return httpx.Response(400, json={"detail": "invalid request"})
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(240),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]

    assert main(arguments) == 1
    capsys.readouterr()
    assert speech_submissions == [first_text, second_text]
    assert not (output_dir / "1-resume.mp3").exists()
    assert not list((tmp_path / "audiobook_tmp").glob("**/completion.json"))

    pcm_paths = list((tmp_path / "audiobook_tmp").glob("**/*.pcm"))
    assert len(pcm_paths) == 1
    pcm_paths[0].write_bytes(b"invalid pcm data")

    assert main([*arguments, "--force"]) == 1
    error = capsys.readouterr().err
    assert "cached audio" in error.lower()
    assert "--no-cache" in error
    assert speech_submissions == [first_text, second_text]


def test_uncertain_paid_synthesis_is_not_retried_without_no_cache(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The provider may have spoken before the response was lost."
    (input_dir / "1-uncertain.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        submitted_text = json.loads(request.read())["text"]
        speech_submissions.append(submitted_text)
        if len(speech_submissions) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(230),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]

    assert main(arguments) == 1
    assert "uncertain" in capsys.readouterr().err.lower()
    assert speech_submissions == [original]
    assert not (output_dir / "1-uncertain.mp3").exists()

    assert main(arguments) == 1
    error = capsys.readouterr().err
    assert "uncertain" in error.lower()
    assert "--no-cache" in error
    assert speech_submissions == [original]

    assert main([*arguments, "--no-cache"]) == 0
    assert speech_submissions == [original, original]
    assert (output_dir / "1-uncertain.mp3").is_file()


def test_invalid_paid_pcm_is_persisted_as_uncertain_before_a_retry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The provider returned an incomplete PCM frame."
    (input_dir / "1-invalid-pcm.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        speech_submissions.append(json.loads(request.read())["text"])
        response_pcm = b"\x01" if len(speech_submissions) == 1 else _pcm_tone(230)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=response_pcm,
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]

    assert main(arguments) == 1
    first_error = capsys.readouterr().err
    assert "pcm" in first_error.lower()
    assert speech_submissions == [original]

    assert main(arguments) == 1
    retry_error = capsys.readouterr().err
    assert "uncertain" in retry_error.lower()
    assert "--no-cache" in retry_error
    assert speech_submissions == [original]

    assert main([*arguments, "--no-cache"]) == 0
    assert speech_submissions == [original, original]
    assert (output_dir / "1-invalid-pcm.mp3").is_file()


def test_repeated_run_reuses_the_completed_chunk_after_a_later_rejection(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    first_text = "A" * 600 + ". "
    second_text = "B" * 600 + "."
    (input_dir / "1-resume.md").write_text(first_text + second_text, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    rejected_second_chunk = False
    voice_requests: list[httpx.Request] = []
    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal rejected_second_chunk
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            voice_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {
                                "live_moderation_enabled": len(voice_requests) == 2
                            },
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        submitted_text = json.loads(request.read())["text"]
        speech_submissions.append(submitted_text)
        if submitted_text == second_text and not rejected_second_chunk:
            rejected_second_chunk = True
            return httpx.Response(400, json={"detail": "invalid request"})
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(240),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]

    assert main(arguments) == 1
    first_error = capsys.readouterr().err
    assert "HTTP 400" in first_error
    assert speech_submissions == [first_text, second_text]
    assert not (output_dir / "1-resume.mp3").exists()
    assert not list((tmp_path / "audiobook_tmp").glob("**/completion.json"))

    assert main(arguments) == 1
    expired_voice_error = capsys.readouterr().err
    assert "live moderation" in expired_voice_error
    assert len(voice_requests) == 2
    assert speech_submissions == [first_text, second_text]

    assert main(arguments) == 0
    report = capsys.readouterr().out
    assert len(voice_requests) == 3
    assert speech_submissions == [first_text, second_text, second_text]
    assert "using cached audio chunk 1" in report
    assert (output_dir / "1-resume.mp3").is_file()
    assert len(list((tmp_path / "audiobook_tmp").glob("**/*.pcm"))) == 2
    assert len(list((tmp_path / "audiobook_tmp").glob("**/completion.json"))) == 1


def test_speed_change_reuses_pcm_but_synthesis_setting_changes_fail_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The same PCM can be played at a different speed."
    (input_dir / "1-speed.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": [
                                "eleven_v4",
                                "eleven_v4_turbo",
                            ],
                        },
                        {
                            "name": "Alternate Voice",
                            "voice_id": "voice-alternate",
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": [
                                "eleven_v4",
                                "eleven_v4_turbo",
                            ],
                        },
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        speech_submissions.append(json.loads(request.read())["text"])
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(240),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]
    output = output_dir / "1-speed.mp3"

    assert main(arguments) == 0
    original_output = hashlib.sha256(output.read_bytes()).hexdigest()
    assert speech_submissions == [original]
    capsys.readouterr()

    assert main([*arguments, "--speed", "1.5"]) == 0
    faster_output = hashlib.sha256(output.read_bytes()).hexdigest()
    speed_report = capsys.readouterr().out
    assert faster_output != original_output
    assert speech_submissions == [original]
    assert "using cached audio chunk 1" in speed_report

    for changed_settings in (
        ["--model", "eleven_v4_turbo"],
        ["--language", "fr"],
        ["--voice", "Alternate Voice"],
    ):
        assert main([*arguments, *changed_settings]) == 1
        error = capsys.readouterr().err
        assert "cached audio" in error.lower()
        assert "--no-cache" in error
        assert speech_submissions == [original]


def test_failed_no_cache_assembly_preserves_the_previous_completion(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The older chapter stays published if refreshing fails."
    (input_dir / "1-refresh.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )

        speech_submissions.append(json.loads(request.read())["text"])
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(240),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]
    destination = output_dir / "1-refresh.mp3"

    assert main(arguments) == 0
    old_mp3 = destination.read_bytes()
    completion_path = next((tmp_path / "audiobook_tmp").glob("**/completion.json"))
    old_completion = completion_path.read_bytes()
    assert speech_submissions == [original]
    capsys.readouterr()

    fake_bin = tmp_path / "failing-bin"
    fake_bin.mkdir()
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_ffmpeg.write_text("#!/bin/sh\nprintf 'assembly failed' >&2\nexit 17\n")
    fake_ffmpeg.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    assert main([*arguments, "--no-cache"]) == 1
    error = capsys.readouterr().err
    assert "ffmpeg could not encode" in error
    assert "assembly failed" in error
    assert destination.read_bytes() == old_mp3
    assert completion_path.read_bytes() == old_completion
    assert len(list((tmp_path / "audiobook_tmp").glob("**/completion.json"))) == 1
    assert speech_submissions == [original, original]
    assert not list(output_dir.glob(".*.pending.mp3"))


def test_completion_write_failure_preserves_previous_mp3(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The previous chapter survives a completion-record failure."
    (input_dir / "1-transaction.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    speech_submissions: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "Dahlia - Vintage Romantic Hollywood Star",
                            "voice_id": _DEFAULT_DAHLIA_ID,
                            "sharing": {"live_moderation_enabled": False},
                            "high_quality_base_model_ids": ["eleven_v4"],
                        }
                    ],
                    "has_more": False,
                    "next_page_token": None,
                },
            )
        speech_submissions.append(json.loads(request.read())["text"])
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=_pcm_tone(230 + 10 * len(speech_submissions)),
        )

    _install_provider_transport(monkeypatch, respond)
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--model",
        "eleven_v4",
        "--language",
        "en",
    ]
    destination = output_dir / "1-transaction.mp3"

    assert main(arguments) == 0
    old_mp3 = destination.read_bytes()
    completion_path = next((tmp_path / "audiobook_tmp").glob("**/completion.json"))
    old_completion = completion_path.read_bytes()
    assert speech_submissions == [original]
    capsys.readouterr()

    real_replace = os.replace

    def fail_completion_replace(source, target) -> None:
        if Path(target).name == "completion.json":
            raise OSError("simulated completion storage failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_completion_replace)

    exit_code = main([*arguments, "--no-cache"])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "completion" in error.lower()
    assert destination.read_bytes() == old_mp3
    assert completion_path.read_bytes() == old_completion
    assert speech_submissions == [original, original]
    assert not list(output_dir.glob(".*.pending.mp3"))


def test_interrupted_turbo_stream_requires_no_cache_before_a_fresh_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    input_dir = tmp_path / "chapters"
    input_dir.mkdir()
    original = "The stream may stop after sending only part of the chapter."
    (input_dir / "1-interrupted.md").write_text(original, encoding="utf-8")
    output_dir = tmp_path / "finished"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json=_completed_annotation())
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "name": "Dahlia - Vintage Romantic Hollywood Star",
                        "voice_id": _DEFAULT_DAHLIA_ID,
                        "sharing": {"live_moderation_enabled": False},
                        "high_quality_base_model_ids": ["eleven_v4_turbo"],
                    }
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    _install_provider_transport(monkeypatch, respond)
    streams: list[list[dict[str, object]]] = []
    partial_pcm = _pcm_tone(200)
    complete_pcm = _pcm_tone(260)

    def peer(connection) -> None:
        messages = [json.loads(connection.recv(timeout=5)) for _ in range(3)]
        streams.append(messages)
        if len(streams) == 1:
            connection.send(
                json.dumps({"audio": base64.b64encode(partial_pcm).decode("ascii")})
            )
            connection.close()
            return

        connection.send(
            json.dumps({"audio": base64.b64encode(complete_pcm).decode("ascii")})
        )
        connection.send(json.dumps({"is_final_audio_for_turn": True}))
        connection.send(json.dumps({"is_final": True}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    monkeypatch.setattr(elevenlabs, "TURBO_WS_URL", f"ws://127.0.0.1:{port}")
    arguments = [
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--language",
        "en",
    ]
    try:
        assert main(arguments) == 1
        first_error = capsys.readouterr().err
        assert (
            "incomplete audio" in first_error.lower()
            or "uncertain" in first_error.lower()
        )
        assert len(streams) == 1
        assert not (output_dir / "1-interrupted.mp3").exists()

        assert main(arguments) == 1
        retry_error = capsys.readouterr().err
        assert "uncertain" in retry_error.lower()
        assert "--no-cache" in retry_error
        assert len(streams) == 1

        assert main([*arguments, "--no-cache"]) == 0
        assert len(streams) == 2
        assert (output_dir / "1-interrupted.mp3").is_file()
        assert len(list((tmp_path / "audiobook_tmp").glob("**/*.pcm"))) == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
