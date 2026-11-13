from decimal import Decimal

import pytest

from audiobook.contracts import ModelCapabilities, SourceValidationError
from audiobook.planning import plan_chapter


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[softly] A sentence. [firmly]\n", "[softly] A sentence. "),
        ("A sentence. [firmly] [softly]\n", "A sentence. "),
        ("A sentence. [Appendix]\n", "A sentence. [Appendix]\n"),
    ],
)
def test_terminal_directions_are_omitted_from_submitted_text(text, expected):
    from audiobook.application import _model_capabilities

    plan = plan_chapter(text, _model_capabilities("eleven_v4_turbo"))
    assert "".join(c.text for c in plan.chunks) == expected
    assert plan.submitted_characters == len(expected)
    assert plan.estimated_cost == Decimal(len(expected)) * Decimal("0.00004")


def test_direction_at_paragraph_end_moves_with_following_speech():
    capabilities = ModelCapabilities(
        model_id="eleven_v4_turbo",
        max_characters=28,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.04"),
    )
    narration = "First paragraph. [softly]\n\nSecond paragraph.\n"
    plan = plan_chapter(narration, capabilities)
    assert [c.text for c in plan.chunks] == [
        "First paragraph. ",
        "[softly]\n\nSecond paragraph.\n",
    ]
    assert "".join(c.text for c in plan.chunks) == narration


@pytest.mark.parametrize("model", ["eleven_v4", "eleven_v4_turbo"])
def test_application_uses_800_character_budget_including_tags(model):
    from audiobook.application import _model_capabilities

    capabilities = _model_capabilities(model)
    assert capabilities.max_characters == 800
    narration = ("[softly] " + "A full sentence. " * 30 + "\n\n") * 8
    plan = plan_chapter(narration, capabilities)
    assert len(plan.chunks) > 1
    assert all(c.character_count <= 800 for c in plan.chunks)
    assert "".join(c.text for c in plan.chunks) == narration


def test_tag_only_narration_is_rejected_before_paid_work():
    from audiobook.application import _model_capabilities

    with pytest.raises(SourceValidationError, match="no speakable text"):
        plan_chapter("[softly]\n", _model_capabilities("eleven_v4_turbo"))


def test_plan_chapter_keeps_paragraphs_in_order_without_losing_separators() -> None:
    narration = "First paragraph.\n\nSecond paragraph.\n"
    capabilities = ModelCapabilities(
        model_id="eleven_v4_turbo",
        max_characters=18,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.04"),
    )

    plan = plan_chapter(narration, capabilities)

    assert [chunk.text for chunk in plan.chunks] == [
        "First paragraph.\n\n",
        "Second paragraph.\n",
    ]
    assert [chunk.character_count for chunk in plan.chunks] == [18, 18]
    assert [chunk.index for chunk in plan.chunks] == [0, 1]
    assert [chunk.estimated_cost for chunk in plan.chunks] == [
        Decimal("0.00072"),
        Decimal("0.00072"),
    ]
    assert "".join(chunk.text for chunk in plan.chunks) == narration
    assert plan.submitted_characters == 36
    assert plan.estimated_cost == Decimal("0.00144")


def test_plan_chapter_falls_back_to_sentence_boundaries() -> None:
    narration = "First sentence. Second sentence.\n"
    capabilities = ModelCapabilities(
        model_id="eleven_v4_turbo",
        max_characters=17,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.04"),
    )

    plan = plan_chapter(narration, capabilities)

    assert [chunk.text for chunk in plan.chunks] == [
        "First sentence. ",
        "Second sentence.\n",
    ]
    assert [chunk.character_count for chunk in plan.chunks] == [16, 17]
    assert "".join(chunk.text for chunk in plan.chunks) == narration
    assert plan.submitted_characters == 33


def test_plan_chapter_accepts_a_passage_at_the_exact_model_limit() -> None:
    narration = "1234567890"
    capabilities = ModelCapabilities(
        model_id="eleven_v4",
        max_characters=10,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.08"),
    )

    plan = plan_chapter(narration, capabilities)

    assert [chunk.text for chunk in plan.chunks] == ["1234567890"]
    assert [chunk.character_count for chunk in plan.chunks] == [10]
    assert plan.estimated_cost == Decimal("0.0008")


@pytest.mark.parametrize(
    ("model_id", "rate", "expected_cost"),
    [
        ("eleven_v4_turbo", Decimal("0.04"), Decimal("0.04")),
        ("eleven_v4", Decimal("0.08"), Decimal("0.08")),
    ],
)
def test_plan_chapter_uses_the_selected_models_published_rate(
    model_id: str, rate: Decimal, expected_cost: Decimal
) -> None:
    narration = "x" * 1000
    capabilities = ModelCapabilities(
        model_id=model_id,
        max_characters=1000,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=rate,
    )

    plan = plan_chapter(narration, capabilities)

    assert plan.submitted_characters == 1000
    assert plan.cost_per_1000_characters == rate
    assert plan.estimated_cost == expected_cost


def test_plan_chapter_reports_an_unsplittable_oversized_passage() -> None:
    narration = "Unsplittable passage."
    capabilities = ModelCapabilities(
        model_id="eleven_v4_turbo",
        max_characters=5,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.04"),
    )

    with pytest.raises(SourceValidationError, match="unsplittable.*5-character limit"):
        plan_chapter(narration, capabilities)


def test_plan_chapter_keeps_a_direction_tag_with_its_following_sentence() -> None:
    narration = "Intro sentence. [whispering] A short line.\n"
    capabilities = ModelCapabilities(
        model_id="eleven_v4_turbo",
        max_characters=27,
        supported_languages=frozenset({"en"}),
        cost_per_1000_characters=Decimal("0.04"),
    )

    plan = plan_chapter(narration, capabilities)

    assert [chunk.text for chunk in plan.chunks] == [
        "Intro sentence. ",
        "[whispering] A short line.\n",
    ]
    assert [chunk.character_count for chunk in plan.chunks] == [16, 27]
    assert "".join(chunk.text for chunk in plan.chunks) == narration
