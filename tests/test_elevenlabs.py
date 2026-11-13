from __future__ import annotations

import base64
import json
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from websockets.sync.server import serve

from audiobook.contracts import (
    IncompleteAudioError,
    ProviderError,
    UncertainOperationError,
)
from audiobook.elevenlabs import resolve_voice, synthesize_chunk

DEFAULT_DAHLIA_ID = "JaagUurP1dmW3WscoJ79"


def _voice(
    name: str,
    voice_id: str,
    model_ids: list[str],
    *,
    live_moderated: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "voice_id": voice_id,
        "sharing": {"live_moderation_enabled": live_moderated},
        "high_quality_base_model_ids": model_ids,
    }


def test_default_voice_is_resolved_across_account_pages() -> None:
    pages = {
        None: {
            "voices": [_voice("Other Voice", "voice-other", ["eleven_v4_turbo"])],
            "has_more": True,
            "next_page_token": "page-two",
        },
        "page-two": {
            "voices": [
                _voice(
                    "Dahlia - Vintage Romantic Hollywood Star",
                    DEFAULT_DAHLIA_ID,
                    ["eleven_v4", "eleven_v4_turbo"],
                )
            ],
            "has_more": False,
            "next_page_token": None,
        },
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v2/voices"
        assert request.url.params["include_live_moderated"] == "false"
        page_token = request.url.params.get("next_page_token")
        return httpx.Response(200, json=pages[page_token])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        voice_id = resolve_voice(None, "test-key", client=client)

    assert voice_id == DEFAULT_DAHLIA_ID


def test_exact_voice_name_resolves_to_its_account_id() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["include_live_moderated"] == "false"
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice("Studio Narrator", "voice-studio", ["eleven_v4_turbo"])
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        voice_id = resolve_voice("Studio Narrator", "test-key", client=client)

    assert voice_id == "voice-studio"


def test_explicit_id_lookup_still_rejects_live_moderated_voice() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["include_live_moderated"] == "false"
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice(
                        "Moderated Voice",
                        "voice-moderated",
                        ["eleven_v4_turbo"],
                        live_moderated=True,
                    )
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="live moderation"),
    ):
        resolve_voice("voice-moderated", "test-key", client=client)


def test_duplicate_exact_voice_names_require_an_id() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice("Twin Voice", "voice-twin-a", ["eleven_v4_turbo"]),
                    _voice("Twin Voice", "voice-twin-b", ["eleven_v4_turbo"]),
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="ambiguous.*voice ID"),
    ):
        resolve_voice("Twin Voice", "test-key", client=client)


def test_voice_must_support_the_selected_model() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice("Turbo Only", "voice-turbo-only", ["eleven_v4_turbo"])
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="not eligible for model 'eleven_v4'"),
    ):
        resolve_voice(
            "voice-turbo-only",
            "test-key",
            model_id="eleven_v4",
            client=client,
        )


def test_missing_voice_does_not_fall_back_to_another_account_voice() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice("A Different Voice", "voice-different", ["eleven_v4_turbo"])
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="'missing-voice'.*not found"),
    ):
        resolve_voice("missing-voice", "test-key", client=client)


def test_voice_record_without_an_id_reports_provider_error() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "name": "Voice Without ID",
                        "sharing": {"live_moderation_enabled": False},
                        "high_quality_base_model_ids": ["eleven_v4_turbo"],
                    }
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="missing a valid voice ID"),
    ):
        resolve_voice("Voice Without ID", "test-key", client=client)


def test_standard_v4_returns_native_pcm_with_explicit_format_metadata() -> None:
    pcm = b"\x01\x00\x02\x00"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/text-to-speech/voice-v4"
        assert request.url.params["output_format"] == "pcm_24000"
        assert request.headers["xi-api-key"] == "test-key"
        assert json.loads(request.read()) == {
            "text": "The wind crossed the quiet meadow.",
            "model_id": "eleven_v4",
            "language_code": "en",
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=pcm,
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        audio = synthesize_chunk(
            "The wind crossed the quiet meadow.",
            "voice-v4",
            "eleven_v4",
            "en",
            "test-key",
            client=client,
        )

    assert audio.pcm == pcm
    assert audio.format.sample_rate_hz == 24_000
    assert audio.format.sample_width_bytes == 2
    assert audio.format.channels == 1
    assert audio.format.encoding == "pcm_s16le"


def test_standard_v4_rejection_is_reported_without_retry() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"detail": "invalid language"})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="rejected.*HTTP 400"),
    ):
        synthesize_chunk(
            "The wind crossed the quiet meadow.",
            "voice-v4",
            "eleven_v4",
            "en",
            "test-key",
            client=client,
        )

    assert len(requests) == 1


def test_standard_v4_rejects_lossy_or_unknown_response_format() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg"},
            content=b"encoded-mp3-data",
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="expected audio/pcm"),
    ):
        synthesize_chunk(
            "The wind crossed the quiet meadow.",
            "voice-v4",
            "eleven_v4",
            "en",
            "test-key",
            client=client,
        )


def test_standard_v4_rejects_wav_container_when_pcm_was_requested() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/pcm"},
            content=b"RIFF\x04\x00\x00\x00WAVE",
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ProviderError, match="empty or containerized"),
    ):
        synthesize_chunk(
            "The wind crossed the quiet meadow.",
            "voice-v4",
            "eleven_v4",
            "en",
            "test-key",
            client=client,
        )


def test_turbo_stream_returns_complete_pcm_from_split_audio_frames() -> None:
    text = "The morning sun warmed the open field beside the river."
    voice_id = "voice-turbo"
    pcm_parts = (b"\x01\x00", b"\x02\x00\x03\x00")
    received: dict[str, object] = {}

    def peer(connection) -> None:
        received["path"] = connection.request.path
        received["headers"] = dict(connection.request.headers)
        received["messages"] = [
            json.loads(connection.recv(timeout=5)) for _ in range(3)
        ]
        for part in pcm_parts:
            encoded = base64.b64encode(part).decode("ascii")
            connection.send(json.dumps({"audio": encoded}))
        connection.send(json.dumps({"is_final_audio_for_turn": True}))
        connection.send(json.dumps({"is_final": True}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        audio = synthesize_chunk(
            text,
            voice_id,
            "eleven_v4_turbo",
            "en",
            "test-key",
            websocket_url=f"ws://127.0.0.1:{port}",
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    parsed_url = urlsplit(str(received["path"]))
    assert parse_qs(parsed_url.query) == {
        "model_id": ["eleven_v4_turbo"],
        "output_format": ["pcm_24000"],
        "language_code": ["en"],
    }
    headers = received["headers"]
    assert isinstance(headers, dict)
    assert headers["xi-api-key"] == "test-key"
    assert received["messages"] == [
        {"voices": [voice_id]},
        {"inputs": [{"text": text, "voice_id": voice_id}]},
        {"close_socket": True},
    ]
    assert audio.pcm == b"".join(pcm_parts)
    assert audio.format.sample_rate_hz == 24_000
    assert audio.format.sample_width_bytes == 2
    assert audio.format.channels == 1
    assert audio.format.encoding == "pcm_s16le"


def test_turbo_provider_error_is_reported_without_retry() -> None:
    connections: list[bool] = []

    def peer(connection) -> None:
        connections.append(True)
        for _ in range(3):
            connection.recv(timeout=5)
        connection.send(json.dumps({"error": {"message": "request rejected"}}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        with pytest.raises(ProviderError, match="rejected"):
            synthesize_chunk(
                "A short passage for one provider response.",
                "voice-turbo",
                "eleven_v4_turbo",
                "en",
                "test-key",
                websocket_url=f"ws://127.0.0.1:{port}",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert connections == [True]


def test_turbo_rejects_malformed_base64_audio_frame() -> None:
    def peer(connection) -> None:
        for _ in range(3):
            connection.recv(timeout=5)
        connection.send(json.dumps({"audio": "SGVsbG8$"}))
        connection.send(json.dumps({"is_final_audio_for_turn": True}))
        connection.send(json.dumps({"is_final": True}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        with pytest.raises(ProviderError, match="malformed audio data"):
            synthesize_chunk(
                "A short passage for one provider response.",
                "voice-turbo",
                "eleven_v4_turbo",
                "en",
                "test-key",
                websocket_url=f"ws://127.0.0.1:{port}",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_turbo_disconnect_after_submission_is_uncertain_and_not_retried() -> None:
    connections: list[bool] = []

    def peer(connection) -> None:
        connections.append(True)
        for _ in range(3):
            connection.recv(timeout=5)
        connection.close(code=1011, reason="stream interrupted")

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        with pytest.raises(UncertainOperationError, match="outcome is uncertain"):
            synthesize_chunk(
                "A short passage for one provider response.",
                "voice-turbo",
                "eleven_v4_turbo",
                "en",
                "test-key",
                websocket_url=f"ws://127.0.0.1:{port}",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert connections == [True]


def test_turbo_final_session_without_audio_is_incomplete() -> None:
    def peer(connection) -> None:
        for _ in range(3):
            connection.recv(timeout=5)
        connection.send(json.dumps({"is_final": True}))

    server = serve(peer, "127.0.0.1", 0, compression=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        with pytest.raises(IncompleteAudioError, match="incomplete audio stream"):
            synthesize_chunk(
                "A short passage for one provider response.",
                "voice-turbo",
                "eleven_v4_turbo",
                "en",
                "test-key",
                websocket_url=f"ws://127.0.0.1:{port}",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)
