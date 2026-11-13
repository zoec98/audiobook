"""ElevenLabs account voice lookup and native PCM synthesis adapters."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

from audiobook.contracts import (
    AudioFormat,
    ConfigurationError,
    IncompleteAudioError,
    ProviderError,
    RawAudio,
    UncertainOperationError,
)

DEFAULT_VOICE_ID = "JaagUurP1dmW3WscoJ79"
DEFAULT_MODEL_ID = "eleven_v4_turbo"
SUPPORTED_MODEL_IDS = frozenset({"eleven_v4_turbo", "eleven_v4"})
VOICE_LIST_URL = "https://api.elevenlabs.io/v2/voices"
REST_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TURBO_WS_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
PCM_24000_FORMAT = AudioFormat(
    sample_rate_hz=24_000,
    sample_width_bytes=2,
    channels=1,
    encoding="pcm_s16le",
)
_VOICE_LOOKUP_ATTEMPTS = 3
_VOICE_LOOKUP_TIMEOUT_SECONDS = 10.0
_MAX_PROVIDER_ERROR_DETAIL_LENGTH = 1000


def resolve_voice(
    selector: str | None,
    api_key: str,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    client: httpx.Client | None = None,
) -> str:
    """Resolve one exact account voice that is eligible for the chosen model."""
    if model_id not in SUPPORTED_MODEL_IDS:
        raise ConfigurationError(
            f"Unsupported ElevenLabs model {model_id!r}; choose one of "
            "eleven_v4_turbo or eleven_v4"
        )
    if not api_key:
        raise ConfigurationError("ELEVENLABS_API_KEY is required")

    if client is not None:
        voices = _load_account_voices(client, api_key)
    else:
        with httpx.Client(timeout=30.0) as owned_client:
            voices = _load_account_voices(owned_client, api_key)

    selected = selector if selector is not None else DEFAULT_VOICE_ID
    matches = [
        voice
        for voice in voices
        if voice.get("voice_id") == selected or voice.get("name") == selected
    ]
    if not matches:
        raise ProviderError(f"ElevenLabs voice {selected!r} was not found")
    if len(matches) > 1:
        raise ProviderError(
            f"ElevenLabs voice {selected!r} is ambiguous; select its exact voice ID"
        )
    voice = matches[0]
    sharing = voice.get("sharing")
    if (
        not isinstance(sharing, Mapping)
        or sharing.get("live_moderation_enabled") is not False
    ):
        raise ProviderError(
            f"ElevenLabs voice {selected!r} is not verified as eligible without live moderation"
        )

    model_ids = voice.get("high_quality_base_model_ids")
    if not isinstance(model_ids, list) or model_id not in model_ids:
        raise ProviderError(
            f"ElevenLabs voice {selected!r} is not eligible for model {model_id!r}"
        )
    voice_id = voice.get("voice_id")
    if not isinstance(voice_id, str) or not voice_id:
        raise ProviderError(
            f"ElevenLabs voice {selected!r} is missing a valid voice ID"
        )
    return voice_id


def _load_account_voices(client: httpx.Client, api_key: str) -> list[Mapping[str, Any]]:
    headers = {"xi-api-key": api_key}
    page_token: str | None = None
    seen_tokens: set[str] = set()
    voices: list[Mapping[str, Any]] = []

    while True:
        params: dict[str, str | int] = {
            "include_live_moderated": "false",
            "page_size": 100,
        }
        if page_token is not None:
            params["next_page_token"] = page_token
        payload: Any
        for attempt in range(_VOICE_LOOKUP_ATTEMPTS):
            try:
                response = client.get(
                    VOICE_LIST_URL,
                    params=params,
                    headers=headers,
                    timeout=_VOICE_LOOKUP_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if (
                    status == 429 or status >= 500
                ) and attempt + 1 < _VOICE_LOOKUP_ATTEMPTS:
                    continue
                status_text = f" (HTTP {status})" if status is not None else ""
                raise ProviderError(
                    f"ElevenLabs voice lookup failed{status_text}"
                ) from error
            except httpx.RequestError as error:
                if attempt + 1 < _VOICE_LOOKUP_ATTEMPTS:
                    continue
                raise ProviderError("ElevenLabs voice lookup failed") from error
            except httpx.HTTPError as error:
                raise ProviderError("ElevenLabs voice lookup failed") from error
            except ValueError as error:
                raise ProviderError(
                    "ElevenLabs voice lookup returned invalid JSON"
                ) from error
            break

        if not isinstance(payload, Mapping):
            raise ProviderError("ElevenLabs voice lookup returned an invalid response")
        page_voices = payload.get("voices")
        has_more = payload.get("has_more")
        if not isinstance(page_voices, list) or not isinstance(has_more, bool):
            raise ProviderError("ElevenLabs voice lookup returned an invalid page")
        if any(not isinstance(voice, Mapping) for voice in page_voices):
            raise ProviderError(
                "ElevenLabs voice lookup returned an invalid voice record"
            )
        voices.extend(page_voices)

        if not has_more:
            return voices
        next_token = payload.get("next_page_token")
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token in seen_tokens
        ):
            raise ProviderError(
                "ElevenLabs voice lookup returned an invalid page token"
            )
        seen_tokens.add(next_token)
        page_token = next_token


def synthesize_chunk(
    text: str,
    voice_id: str,
    model_id: str,
    language_code: str,
    api_key: str,
    *,
    client: httpx.Client | None = None,
    websocket_url: str = TURBO_WS_URL,
) -> RawAudio:
    """Synthesize one passage and return complete native PCM."""
    if model_id not in SUPPORTED_MODEL_IDS:
        raise ConfigurationError(
            f"Unsupported ElevenLabs model {model_id!r}; choose one of "
            "eleven_v4_turbo or eleven_v4"
        )
    if not api_key:
        raise ConfigurationError("ELEVENLABS_API_KEY is required")
    if not text.strip():
        raise ConfigurationError("Speech text must not be empty")

    if model_id == "eleven_v4_turbo":
        return _synthesize_turbo(text, voice_id, language_code, api_key, websocket_url)

    url = REST_TTS_URL.format(voice_id=quote(voice_id, safe=""))
    headers = {"xi-api-key": api_key}
    params = {"output_format": "pcm_24000"}
    request = {
        "text": text,
        "model_id": model_id,
        "language_code": language_code,
    }
    if client is not None:
        response = _post_rest_audio(client, url, headers, params, request)
    else:
        with httpx.Client(timeout=60.0) as owned_client:
            response = _post_rest_audio(owned_client, url, headers, params, request)

    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "audio/pcm":
        raise UncertainOperationError(
            "ElevenLabs v4 completed with an unexpected audio format; the paid "
            "request outcome is uncertain, so do not retry it blindly"
        )
    if not response.content or response.content.startswith(b"RIFF"):
        raise UncertainOperationError(
            "ElevenLabs v4 completed without usable native PCM; the paid request "
            "outcome is uncertain, so do not retry it blindly"
        )
    return RawAudio(pcm=response.content, format=PCM_24000_FORMAT)


def _post_rest_audio(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    request: dict[str, str],
) -> httpx.Response:
    try:
        response = client.post(url, headers=headers, params=params, json=request)
    except httpx.RequestError as error:
        raise UncertainOperationError(
            "The ElevenLabs v4 request outcome is uncertain; do not retry it blindly"
        ) from error
    if 500 <= response.status_code:
        raise UncertainOperationError(
            f"ElevenLabs v4 returned HTTP {response.status_code}; the paid request outcome is uncertain"
        )
    if response.is_error:
        raise ProviderError(
            f"ElevenLabs v4 rejected the request (HTTP {response.status_code})",
            debug_details=_provider_error_details(_response_error_payload(response)),
        )
    return response


def _response_error_payload(response: httpx.Response) -> object:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return response.text


def _provider_error_details(error: object) -> str | None:
    """Format a bounded, one-line provider error detail for optional debug output."""
    if isinstance(error, Mapping):
        nested_error = error.get("error")
        if isinstance(nested_error, Mapping):
            combined = dict(nested_error)
            for name, value in error.items():
                if name != "error" and name not in combined:
                    combined[name] = value
            error = combined

        nested_detail = error.get("detail")
        if isinstance(nested_detail, Mapping):
            combined = dict(nested_detail)
            for name, value in error.items():
                if name != "detail" and name not in combined:
                    combined[name] = value
            error = combined

        if isinstance(error, Mapping):
            fields = []
            for name in (
                "type",
                "code",
                "status",
                "message",
                "request_id",
                "param",
                "detail",
                "error",
            ):
                value = error.get(name)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    value_text = str(value).strip()
                    if value_text:
                        fields.append(f"{name}={value_text}")
                elif name in {"detail", "error"} and isinstance(value, (Mapping, list)):
                    fields.append(
                        f"{name}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                    )
            if fields:
                detail = "; ".join(fields)
            else:
                detail = json.dumps(error, ensure_ascii=False, sort_keys=True)
        else:
            detail = str(error)
    elif isinstance(error, str):
        detail = error
    else:
        detail = json.dumps(error, ensure_ascii=False, sort_keys=True)

    detail = " ".join(
        "".join(
            character if character.isprintable() else " " for character in detail
        ).split()
    )
    if not detail:
        return None
    if len(detail) > _MAX_PROVIDER_ERROR_DETAIL_LENGTH:
        detail = f"{detail[: _MAX_PROVIDER_ERROR_DETAIL_LENGTH - 3].rstrip()}..."
    return detail


def _synthesize_turbo(
    text: str,
    voice_id: str,
    language_code: str,
    api_key: str,
    websocket_url: str,
) -> RawAudio:
    parts = urlsplit(websocket_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "model_id": "eleven_v4_turbo",
            "output_format": "pcm_24000",
            "language_code": language_code,
        }
    )
    uri = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    try:
        connection = websocket_connect(
            uri,
            additional_headers={"xi-api-key": api_key},
            open_timeout=10,
            close_timeout=10,
        )
    except (OSError, TimeoutError, WebSocketException) as error:
        raise ProviderError(
            "Unable to connect to the ElevenLabs Turbo WebSocket"
        ) from error

    submitted = False
    audio_parts: list[bytes] = []
    final_audio_for_turn = False
    final_session = False
    try:
        with connection as websocket:
            websocket.send(json.dumps({"voices": [voice_id]}))
            submitted = True
            websocket.send(
                json.dumps({"inputs": [{"text": text, "voice_id": voice_id}]})
            )
            websocket.send(json.dumps({"close_socket": True}))

            while not final_session:
                try:
                    frame = websocket.recv(timeout=60)
                except (OSError, TimeoutError, WebSocketException) as error:
                    raise UncertainOperationError(
                        "The ElevenLabs Turbo stream ended before completion; "
                        "the request outcome is uncertain, so do not retry it blindly"
                    ) from error
                try:
                    payload = json.loads(frame)
                except (TypeError, json.JSONDecodeError) as error:
                    raise UncertainOperationError(
                        "ElevenLabs Turbo returned a malformed message after "
                        "submission; the paid request outcome is uncertain"
                    ) from error
                if not isinstance(payload, Mapping):
                    raise UncertainOperationError(
                        "ElevenLabs Turbo returned a malformed message after "
                        "submission; the paid request outcome is uncertain"
                    )
                if "error" in payload:
                    raise ProviderError(
                        "ElevenLabs Turbo rejected the synthesis request",
                        debug_details=_provider_error_details(payload),
                    )
                encoded_audio = payload.get("audio")
                if encoded_audio is not None:
                    if not isinstance(encoded_audio, str):
                        raise UncertainOperationError(
                            "ElevenLabs Turbo returned malformed audio after "
                            "submission; the paid request outcome is uncertain"
                        )
                    if encoded_audio:
                        try:
                            audio_part = base64.b64decode(encoded_audio, validate=True)
                        except (binascii.Error, ValueError) as error:
                            raise UncertainOperationError(
                                "ElevenLabs Turbo returned malformed audio after "
                                "submission; the paid request outcome is uncertain"
                            ) from error
                        if not audio_part:
                            raise UncertainOperationError(
                                "ElevenLabs Turbo returned an empty audio frame after "
                                "submission; the paid request outcome is uncertain"
                            )
                        audio_parts.append(audio_part)

                if payload.get("is_final_audio_for_turn") is True:
                    final_audio_for_turn = True
                if payload.get("is_final") is True:
                    final_session = True
    except (OSError, TimeoutError, WebSocketException) as error:
        if submitted:
            raise UncertainOperationError(
                "The ElevenLabs Turbo request outcome is uncertain; do not retry it blindly"
            ) from error
        raise ProviderError("Unable to send the ElevenLabs Turbo request") from error

    if not final_audio_for_turn or not final_session or not audio_parts:
        raise IncompleteAudioError(
            "ElevenLabs Turbo returned an incomplete audio stream"
        )
    return RawAudio(pcm=b"".join(audio_parts), format=PCM_24000_FORMAT)
