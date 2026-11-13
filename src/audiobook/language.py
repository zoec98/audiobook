"""Choose one supported narration language for a collection of chapters."""

import re
from collections.abc import Collection, Iterable

from py3langid import rank

from audiobook.contracts import AnnotatedChapter, ConfigurationError, PreparedChapter

_CHAPTER_LIMIT = 2_000
_COLLECTION_LIMIT = 20_000
_CHAPTER_CUE = re.compile(r"(?m)^\[chapter\][ \t]?")


def choose_language(
    chapters: Iterable[PreparedChapter | AnnotatedChapter],
    override: str | None,
    supported_languages: Collection[str],
) -> str:
    """Return an explicit supported language or detect one from chapter prose."""
    if override is not None:
        if override not in supported_languages:
            raise _unsupported_language(override, supported_languages)
        return override

    sample = _sample_text(chapters)
    if not any(character.isalpha() for character in sample):
        raise _language_override_advice(supported_languages)

    candidates = rank(sample)
    if not candidates:
        raise _language_override_advice(supported_languages)
    language = candidates[0][0]
    if language not in supported_languages:
        raise _unsupported_language(language, supported_languages, detected=True)
    return language


def _unsupported_language(
    language: str,
    supported_languages: Collection[str],
    *,
    detected: bool = False,
) -> ConfigurationError:
    supported = ", ".join(sorted(supported_languages)) or "none"
    description = "Detected language" if detected else "Language"
    return ConfigurationError(
        f"{description} {language!r} is not supported by the selected speech model. "
        f"Choose a supported code with --language: {supported}."
    )


def _language_override_advice(
    supported_languages: Collection[str],
) -> ConfigurationError:
    supported = ", ".join(sorted(supported_languages)) or "none"
    return ConfigurationError(
        "Could not detect a language from the chapter narration. "
        f"Supply --language with a supported code: {supported}."
    )


def _sample_text(chapters: Iterable[PreparedChapter | AnnotatedChapter]) -> str:
    parts: list[str] = []
    sample_length = 0
    for chapter in chapters:
        if sample_length >= _COLLECTION_LIMIT:
            break
        prepared = (
            chapter.prepared if isinstance(chapter, AnnotatedChapter) else chapter
        )
        prose = _CHAPTER_CUE.sub("", prepared.narration)
        if not prose.strip():
            continue
        separator_length = 1 if parts else 0
        available = _COLLECTION_LIMIT - sample_length - separator_length
        if available <= 0:
            break
        passage = prose[: min(_CHAPTER_LIMIT, available)]
        if not passage:
            continue
        if parts:
            parts.append("\n")
            sample_length += 1
        parts.append(passage)
        sample_length += len(passage)
    return "".join(parts)
