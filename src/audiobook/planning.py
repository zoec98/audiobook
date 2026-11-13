"""Plan speech chunks and estimate their provider usage."""

from __future__ import annotations

import re
from decimal import Decimal

from audiobook.contracts import (
    VOCAL_DIRECTION_TAGS,
    ChunkPlan,
    ConfigurationError,
    ModelCapabilities,
    SourceValidationError,
    SpeechChunk,
)

_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n+")
_SENTENCE_BREAK = re.compile(r"[.!?][\"'’”)]*(?:[ \t]+|\r?\n(?![ \t]*\r?\n))")
_TERMINAL_DIRECTIONS = re.compile(
    r"(?:(?:"
    + "|".join(re.escape(tag) for tag in sorted(VOCAL_DIRECTION_TAGS))
    + r")\s*)+\Z"
)


def plan_chapter(narration: str, model_capabilities: ModelCapabilities) -> ChunkPlan:
    """Pack paragraphs, then sentences, within the model's limit.

    Counts use Python ``len`` (Unicode code points) and include whitespace and
    direction tags exactly as submitted. Directions at the end of the chapter
    are omitted: they have no following speech and can cause provider rejection.
    """
    maximum = model_capabilities.max_characters
    rate = model_capabilities.cost_per_1000_characters
    if (
        maximum <= 0
        or not model_capabilities.model_id
        or not rate.is_finite()
        or rate < 0
        or not model_capabilities.currency
    ):
        raise ConfigurationError("speech model capabilities are incomplete or invalid")

    narration = _TERMINAL_DIRECTIONS.sub("", narration)
    if not narration.strip():
        raise SourceValidationError("The narration has no speakable text")
    paragraphs = _paragraph_pieces(narration)
    texts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > maximum:
            if current:
                texts.append(current)
                current = ""
            sentences = _sentence_pieces(paragraph)
            for sentence in sentences:
                if len(sentence) > maximum:
                    raise SourceValidationError(
                        f"an unsplittable narration passage of {len(sentence)} "
                        f"characters exceeds the {maximum}-character limit for "
                        f"{model_capabilities.model_id}"
                    )
                if current and len(current) + len(sentence) > maximum:
                    texts.append(current)
                    current = ""
                current += sentence
            continue
        if current and len(current) + len(paragraph) > maximum:
            texts.append(current)
            current = ""
        current += paragraph
    if current:
        texts.append(current)

    chunks = tuple(
        SpeechChunk(
            index=index,
            text=text,
            character_count=len(text),
            estimated_cost=Decimal(len(text)) * rate / Decimal(1000),
        )
        for index, text in enumerate(texts)
    )
    submitted_characters = sum(chunk.character_count for chunk in chunks)
    estimated_cost = sum((chunk.estimated_cost for chunk in chunks), start=Decimal(0))
    return ChunkPlan(
        model_id=model_capabilities.model_id,
        chunks=chunks,
        submitted_characters=submitted_characters,
        cost_per_1000_characters=rate,
        estimated_cost=estimated_cost,
        currency=model_capabilities.currency,
    )


def _paragraph_pieces(narration: str) -> list[str]:
    """Keep paragraph separators, carrying directions into following speech."""
    pieces: list[str] = []
    start = 0
    for separator in _PARAGRAPH_BREAK.finditer(narration):
        end = separator.end()
        directions = _TERMINAL_DIRECTIONS.search(narration[start:end])
        if directions is not None:
            end = start + directions.start()
        if end > start:
            pieces.append(narration[start:end])
        start = end
    if start < len(narration):
        pieces.append(narration[start:])
    return pieces


def _sentence_pieces(narration: str) -> list[str]:
    """Split on sentence punctuation, keeping following whitespace in place."""
    pieces: list[str] = []
    start = 0
    for separator in _SENTENCE_BREAK.finditer(narration):
        pieces.append(narration[start : separator.end()])
        start = separator.end()
    if start < len(narration):
        pieces.append(narration[start:])
    return pieces
