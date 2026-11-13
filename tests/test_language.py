from pathlib import Path

import pytest

from audiobook.contracts import (
    AnnotatedChapter,
    ConfigurationError,
    InsertionRecord,
    PreparedChapter,
    SourceIdentity,
)
from audiobook.language import choose_language
from audiobook.markdown import prepare_chapter


def _chapter(narration: str) -> PreparedChapter:
    return PreparedChapter(
        source=SourceIdentity(path=Path("chapter.md"), content_sha256="source"),
        original_markdown=narration,
        narration=narration,
        insertion_positions=(),
        diagnostics=(),
    )


def _annotated(chapter: PreparedChapter, narration: str) -> AnnotatedChapter:
    return AnnotatedChapter(
        prepared=chapter,
        markdown=chapter.original_markdown,
        narration=narration,
        insertion_record=InsertionRecord(
            source_sha256=chapter.source.content_sha256,
            prompt_version="v1",
            insertions=(),
        ),
    )


def test_explicit_supported_language_overrides_the_chapter_language() -> None:
    chapter = _chapter(
        "Dies ist ein deutscher Absatz über die Sprache und ein kleines Buch."
    )

    assert choose_language((chapter,), "en", frozenset({"de", "en"})) == "en"


def test_detects_english_from_chapter_prose() -> None:
    chapter = _chapter(
        "The old house stood at the edge of the village. Every morning the baker "
        "opened the blue door before sunrise, and neighbors gathered to share "
        "stories while the warm bread cooled on the wooden table."
    )

    assert choose_language((chapter,), None, frozenset({"en", "de", "fr"})) == "en"


@pytest.mark.parametrize(
    ("narration", "expected"),
    (
        (
            (
                "Am frühen Morgen verließ Anna das kleine Haus am Waldrand. Sie ging "
                "langsam den schmalen Weg hinunter, während die Sonne zwischen den "
                "Bäumen aufging. Im Dorf öffnete der Bäcker bereits seine Tür, und "
                "der Duft von frischem Brot zog über den Platz. Anna grüßte die "
                "Nachbarn und erzählte ihnen, dass sie am Nachmittag ihre Schwester "
                "besuchen wollte. Gemeinsam sprachen sie über das Wetter, die Ernte "
                "und die Reise, die sie im kommenden Sommer planten. Niemand hatte "
                "es eilig, denn der Tag begann ruhig und klar."
            ),
            "de",
        ),
        (
            (
                "Au petit matin, Claire quitta la maison située au bord de la rivière. "
                "Elle suivit le chemin qui traversait le vieux jardin, puis s'arrêta "
                "devant la boulangerie du village. Le boulanger ouvrit la porte et lui "
                "souhaita une bonne journée. Ils parlèrent du temps, des nouvelles du "
                "quartier et du voyage que Claire préparait pour le mois prochain. "
                "Les enfants jouaient déjà sur la place, tandis que les premiers "
                "rayons du soleil éclairaient les toits. Elle acheta un pain chaud "
                "avant de rentrer chez elle."
            ),
            "fr",
        ),
    ),
)
def test_detects_german_and_french_prose(narration: str, expected: str) -> None:
    chapters = (_chapter(narration), _chapter(narration))

    assert choose_language(chapters, None, frozenset({"en", "de", "fr"})) == expected


def test_unsupported_detected_language_advises_explicit_override() -> None:
    chapter = _chapter(
        "Le matin, elle ouvrit la fenêtre et regarda la rivière couler doucement "
        "entre les arbres. La lumière du soleil éclairait les maisons du village."
    )

    with pytest.raises(ConfigurationError) as error:
        choose_language((chapter,), None, frozenset({"en", "de"}))

    assert "--language" in str(error.value)
    assert "fr" in str(error.value)


def test_empty_or_nonlinguistic_chapters_advises_explicit_override() -> None:
    chapter = _chapter(" 12345 !!! \n")

    with pytest.raises(ConfigurationError) as error:
        choose_language((chapter,), None, frozenset({"en", "de"}))

    assert "--language" in str(error.value)


def test_unsupported_explicit_override_lists_supported_codes() -> None:
    with pytest.raises(ConfigurationError) as error:
        choose_language((), "fr", frozenset({"de", "en"}))

    assert "--language" in str(error.value)
    assert "de, en" in str(error.value)


def test_detection_uses_original_projection_without_front_matter_or_directions(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "1-chapter.md"
    source.write_text(
        "---\nsummary: This English text must not be sampled.\n---\n"
        "# Morgen\nDas Mädchen geht am Fluss entlang und betrachtet die alten Häuser.\n",
        encoding="utf-8",
    )
    prepared = prepare_chapter(source)
    annotated = _annotated(
        prepared,
        "[excited] The English direction must not be sampled.\n"
        "[whispering] Neither should this one.",
    )
    detected_text: list[str] = []

    def classify(text: str) -> list[tuple[str, int]]:
        detected_text.append(text)
        return [("de", 0)]

    monkeypatch.setattr("audiobook.language.rank", classify)

    language = choose_language((annotated,), None, frozenset({"de", "en"}))

    assert language == "de"
    assert detected_text == [
        "Morgen\nDas Mädchen geht am Fluss entlang und betrachtet die alten Häuser.\n"
    ]


def test_detection_samples_chapters_in_order_with_per_chapter_and_total_limits(
    monkeypatch,
) -> None:
    chapters = tuple(_chapter(letter * 2_100) for letter in "abcdefghijkl")
    detected_text: list[str] = []

    def classify(text: str) -> list[tuple[str, int]]:
        detected_text.append(text)
        return [("en", 0)]

    monkeypatch.setattr("audiobook.language.rank", classify)

    choose_language(chapters, None, frozenset({"en"}))

    sample = detected_text[0]
    parts = sample.splitlines()
    assert len(sample) <= 20_000
    assert parts == [letter * 2_000 for letter in "abcdefghi"] + ["j" * 1_991]
