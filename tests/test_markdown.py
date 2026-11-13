from pathlib import Path

from audiobook.markdown import prepare_chapter


def test_prepare_chapter_turns_headings_into_chapter_cues(tmp_path: Path) -> None:
    source = "# Morning\n## Evening\n###### Last light\nA calm beginning.\n"
    chapter = tmp_path / "1-morning.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.original_markdown == source
    assert prepared.narration == (
        "[chapter] Morning\n[chapter] Evening\n"
        "[chapter] Last light\nA calm beginning.\n"
    )
    assert prepared.diagnostics == ()


def test_prepare_chapter_speaks_emphasis_and_link_labels_only(tmp_path: Path) -> None:
    source = "A *quiet* [morning](https://example.test/day) arrives.\n"
    chapter = tmp_path / "2-link.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "A quiet morning arrives.\n"
    assert prepared.original_markdown == source


def test_prepare_chapter_omits_image_captions_but_keeps_ordinary_emphasis(
    tmp_path: Path,
) -> None:
    source = (
        "![shore](shore.jpg) *A same-line caption.*\n"
        "![waves](waves.jpg)\n"
        "\n"
        "*A next-line caption.*\n"
        "Ordinary *emphasis* remains.\n"
    )
    chapter = tmp_path / "3-images.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "Ordinary emphasis remains.\n"
    assert prepared.original_markdown == source


def test_prepare_chapter_reports_original_speech_brackets_after_projection(
    tmp_path: Path,
) -> None:
    source = (
        "Él dijo [risas]. Then [a link](https://example.test).\n"
        "![cover](cover.png) *[caption].*\n"
    )
    chapter = tmp_path / "4-brackets.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "Él dijo [risas]. Then a link.\n"
    assert len(prepared.diagnostics) == 1
    diagnostic = prepared.diagnostics[0]
    assert diagnostic.code == "existing_bracketed_construct"
    assert diagnostic.position.offset == 8
    assert diagnostic.position.line == 1
    assert diagnostic.position.character == 9


def test_prepare_chapter_skips_a_closed_leading_front_matter_block(
    tmp_path: Path,
) -> None:
    source = "---\ntitle: Dawn\n---\nThe day begins.\n"
    chapter = tmp_path / "5-front-matter.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "The day begins.\n"
    assert prepared.original_markdown == source
    assert prepared.diagnostics == ()
    assert prepared.insertion_positions == (35,)


def test_prepare_chapter_keeps_text_after_unclosed_front_matter(tmp_path: Path) -> None:
    source = "---\ntitle: Dawn\nThe tail must survive.\n"
    chapter = tmp_path / "6-unclosed-front-matter.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "title: Dawn\nThe tail must survive.\n"
    assert prepared.original_markdown == source
    assert len(prepared.diagnostics) == 1
    diagnostic = prepared.diagnostics[0]
    assert diagnostic.code == "unclosed_front_matter"
    assert diagnostic.position.offset == 0
    assert diagnostic.position.line == 1
    assert diagnostic.position.character == 1


def test_prepare_chapter_reports_code_blocks_without_dropping_their_text(
    tmp_path: Path,
) -> None:
    source = "Before.\n```python\nprint('kept')\n```\nAfter.\n"
    chapter = tmp_path / "7-code.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == source
    assert prepared.insertion_positions == (7, 42)
    assert len(prepared.diagnostics) == 1
    diagnostic = prepared.diagnostics[0]
    assert diagnostic.code == "unsupported_code_block"
    assert diagnostic.position.offset == 8
    assert diagnostic.position.line == 2
    assert diagnostic.position.character == 1


def test_prepare_chapter_diagnoses_unknown_block_markup_and_keeps_it(
    tmp_path: Path,
) -> None:
    source = "A pause.\n> This is a quote.\n"
    chapter = tmp_path / "8-quote.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == source
    assert len(prepared.diagnostics) == 1
    diagnostic = prepared.diagnostics[0]
    assert diagnostic.code == "unsupported_markdown_structure"
    assert diagnostic.position.offset == 9
    assert diagnostic.position.line == 2
    assert diagnostic.position.character == 1


def test_prepare_chapter_treats_common_thematic_breaks_as_silent_separators(
    tmp_path: Path,
) -> None:
    for index, separator in enumerate(("* * *", "---", "___", "  * * *")):
        source = f"A sentence.\n{separator}\nB follows.\n"
        chapter = tmp_path / f"{index + 1}-thematic-break.md"
        chapter.write_bytes(source.encode("utf-8"))

        prepared = prepare_chapter(chapter)

        assert prepared.original_markdown == source
        assert chapter.read_bytes() == source.encode("utf-8")
        assert prepared.narration == "A sentence.\n\nB follows.\n"
        assert prepared.diagnostics == ()


def test_prepare_chapter_exposes_only_original_heading_and_sentence_boundaries(
    tmp_path: Path,
) -> None:
    source = "# Dawn\nThe light arrives. Birds sing!\n"
    chapter = tmp_path / "9-anchors.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "[chapter] Dawn\nThe light arrives. Birds sing!\n"
    assert prepared.insertion_positions == (6, 25, 37)
    assert prepared.diagnostics == ()


def test_prepare_chapter_places_link_sentence_anchor_after_its_destination(
    tmp_path: Path,
) -> None:
    source = (
        "A [useful.](https://example.test/route.)\n![alt](img.png) *Caption.*\nNext.\n"
    )
    chapter = tmp_path / "10-anchor-link.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.insertion_positions == (40, 73)


def test_prepare_chapter_preserves_bom_and_crlf_source_text(tmp_path: Path) -> None:
    source_bytes = b"\xef\xbb\xbfA sentence.\r\n"
    chapter = tmp_path / "11-exact-source.md"
    chapter.write_bytes(source_bytes)

    prepared = prepare_chapter(chapter)

    assert chapter.read_bytes() == source_bytes
    assert prepared.original_markdown == "\ufeffA sentence.\r\n"
    assert prepared.narration == "\ufeffA sentence.\r\n"
    assert (
        prepared.source.content_sha256
        == "7fb780c81f6c2d41878948fd5d58f545fc3dd7186ba6d5731171c6729c30b0d3"
    )


def test_prepare_chapter_omits_inline_images_without_stranding_spaces(
    tmp_path: Path,
) -> None:
    source = "![cover](cover.png) The boat is here.\n"
    chapter = tmp_path / "12-inline-image.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "The boat is here.\n"


def test_prepare_chapter_diagnoses_inline_code_and_preserves_its_text(
    tmp_path: Path,
) -> None:
    source = "A `code` span.\n"
    chapter = tmp_path / "13-inline-code.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == source
    assert prepared.insertion_positions == ()
    assert len(prepared.diagnostics) == 1
    diagnostic = prepared.diagnostics[0]
    assert diagnostic.code == "unsupported_inline_code"
    assert diagnostic.position.offset == 2
    assert diagnostic.position.line == 1
    assert diagnostic.position.character == 3


def test_prepare_chapter_keeps_underscores_inside_words(tmp_path: Path) -> None:
    source = "Use snake_case_name in this example.\n"
    chapter = tmp_path / "14-underscores.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == source


def test_prepare_chapter_places_heading_anchor_before_closing_hash_syntax(
    tmp_path: Path,
) -> None:
    source = "# Dawn ###\n"
    chapter = tmp_path / "15-closing-heading.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == "[chapter] Dawn\n"
    assert prepared.insertion_positions == (6,)


def test_prepare_chapter_checks_brackets_in_retained_code_blocks(
    tmp_path: Path,
) -> None:
    source = "```\n[tag]\n```\n"
    chapter = tmp_path / "16-code-brackets.md"
    chapter.write_text(source, encoding="utf-8")

    prepared = prepare_chapter(chapter)

    assert prepared.narration == source
    assert [diagnostic.code for diagnostic in prepared.diagnostics] == [
        "unsupported_code_block",
        "existing_bracketed_construct",
    ]
    bracket_diagnostic = prepared.diagnostics[1]
    assert bracket_diagnostic.position.offset == 4
    assert bracket_diagnostic.position.line == 2
    assert bracket_diagnostic.position.character == 1
