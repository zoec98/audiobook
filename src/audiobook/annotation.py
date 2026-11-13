"""Exact-preserving vocal directions for prepared Markdown chapters."""

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from audiobook.annotation_prompt_v1 import PROMPT, PROMPT_VERSION
from audiobook.contracts import (
    AnnotatedChapter,
    Insertion,
    InsertionRecord,
    PreparedChapter,
    ProviderError,
)
from audiobook.markdown import prepare_chapter

_RESPONSES_URL = "https://api.openai.com/v1/responses"
_ALLOWED_TAGS = frozenset(
    {
        "[calmly]",
        "[warmly]",
        "[softly]",
        "[firmly]",
        "[curiously]",
        "[hesitantly]",
        "[sadly]",
        "[brightly]",
        "[whispering]",
        "[sighing]",
    }
)
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "insertions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchor": {"type": "integer"},
                    "tag": {"type": "string"},
                },
                "required": ["anchor", "tag"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["insertions"],
    "additionalProperties": False,
}


def annotate_chapter(
    prepared_chapter: PreparedChapter, provider: httpx.Client
) -> AnnotatedChapter:
    """Request and apply only validated directions at original source anchors."""
    request_body = {
        "model": "gpt-6-luna",
        "reasoning": {"effort": "medium"},
        "instructions": (
            f"{PROMPT}\n\nLegal original source offsets: "
            f"{json.dumps(prepared_chapter.insertion_positions)}"
        ),
        "input": prepared_chapter.original_markdown,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "chapter_insertions",
                "strict": True,
                "schema": _OUTPUT_SCHEMA,
            }
        },
    }
    try:
        response = provider.post(_RESPONSES_URL, json=request_body)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError("OpenAI annotation request failed") from error

    response_id, insertion_values = _read_response(payload)
    insertions = _validate_insertions(prepared_chapter, insertion_values)
    record = InsertionRecord(
        source_sha256=prepared_chapter.source.content_sha256,
        prompt_version=PROMPT_VERSION,
        insertions=insertions,
        provider_response_id=response_id,
    )
    annotated_markdown = _splice(prepared_chapter.original_markdown, insertions)
    annotated_narration = _project_narration(annotated_markdown)
    return AnnotatedChapter(
        prepared=prepared_chapter,
        markdown=annotated_markdown,
        narration=annotated_narration,
        insertion_record=record,
    )


def _read_response(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ProviderError("OpenAI returned an incomplete annotation response")
    response_id = payload.get("id")
    if not isinstance(response_id, str) or not response_id:
        raise ProviderError("OpenAI annotation response has no response ID")

    output = payload.get("output")
    if not isinstance(output, list):
        raise ProviderError("OpenAI annotation response has no structured output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("OpenAI annotation response has malformed output")
        if item.get("type") == "refusal":
            raise ProviderError("OpenAI refused the annotation request")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ProviderError("OpenAI annotation response has an unsupported output")
        content = item.get("content")
        if not isinstance(content, list):
            raise ProviderError("OpenAI annotation response has malformed content")
        for part in content:
            if not isinstance(part, dict):
                raise ProviderError("OpenAI annotation response has malformed content")
            if part.get("type") == "refusal":
                raise ProviderError("OpenAI refused the annotation request")
            if part.get("type") == "output_text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise ProviderError("OpenAI annotation output is not text")
                texts.append(text)
            else:
                raise ProviderError(
                    "OpenAI annotation response has unsupported content"
                )
    if len(texts) != 1:
        raise ProviderError("OpenAI annotation response must contain one output")

    try:
        result = json.loads(texts[0])
    except json.JSONDecodeError as error:
        raise ProviderError("OpenAI returned malformed annotation JSON") from error
    if not isinstance(result, dict) or set(result) != {"insertions"}:
        raise ProviderError("OpenAI annotation output contains unsupported fields")
    insertions = result["insertions"]
    if not isinstance(insertions, list):
        raise ProviderError("OpenAI annotation insertions must be a list")
    if any(
        not isinstance(item, dict) or set(item) != {"anchor", "tag"}
        for item in insertions
    ):
        raise ProviderError("OpenAI annotation contains a malformed insertion")
    return response_id, insertions


def _validate_insertions(
    prepared: PreparedChapter, values: list[dict[str, Any]]
) -> tuple[Insertion, ...]:
    allowed_positions = set(prepared.insertion_positions)
    previous_anchor = -1
    insertions: list[Insertion] = []
    for value in values:
        anchor = value["anchor"]
        tag = value["tag"]
        if type(anchor) is not int or anchor not in allowed_positions:
            raise ProviderError("OpenAI returned an invalid annotation anchor")
        if anchor <= previous_anchor:
            raise ProviderError("OpenAI annotation anchors are not in source order")
        if not isinstance(tag, str) or tag not in _ALLOWED_TAGS:
            raise ProviderError("OpenAI returned a disallowed vocal direction")
        previous_anchor = anchor
        insertions.append(
            Insertion(
                anchor=anchor, tag=_format_tag(prepared.original_markdown, anchor, tag)
            )
        )
    return tuple(insertions)


def _format_tag(source: str, anchor: int, tag: str) -> str:
    """Put boundary spacing inside the insertion record itself."""
    before = source[anchor - 1 : anchor]
    after = source[anchor : anchor + 1]
    prefix = " " if before and not before.isspace() else ""
    suffix = " " if after and not after.isspace() else ""
    return f"{prefix}{tag}{suffix}"


def _splice(source: str, insertions: tuple[Insertion, ...]) -> str:
    pieces: list[str] = []
    cursor = 0
    for insertion in insertions:
        pieces.append(source[cursor : insertion.anchor])
        pieces.append(insertion.tag)
        cursor = insertion.anchor
    pieces.append(source[cursor:])
    return "".join(pieces)


def _project_narration(markdown: str) -> str:
    """Run the public Markdown projection over the exact annotated copy."""
    with tempfile.TemporaryDirectory(prefix="audiobook-annotation-") as directory:
        source = Path(directory) / "annotated.md"
        source.write_bytes(markdown.encode("utf-8"))
        return prepare_chapter(source).narration
