"""Prepare a Markdown chapter for narration."""

import re
from hashlib import sha256
from pathlib import Path

from audiobook.contracts import (
    PreparedChapter,
    SourceDiagnostic,
    SourceIdentity,
    SourcePosition,
)

_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*)|[ \t]*)$")
_IMAGE_AT_END = re.compile(r"!\[[^\]]*\]\([^)]*\)[ \t]*$")
_FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_UNSUPPORTED_LIST_OR_QUOTE = re.compile(r"^ {0,3}(>+|[-*+][ \t]+|\d+[.)][ \t]+)")
_UNSUPPORTED_HTML = re.compile(r"^ {0,3}(</?[A-Za-z][^>]*>)")
_UNSUPPORTED_INDENTED_CODE = re.compile(r"^( {4,})(?=\S)")
_UNSUPPORTED_TABLE = re.compile(r"^ {0,3}(\|)")
_THEMATIC_BREAK = re.compile(
    r"^ {0,3}((?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)


def prepare_chapter(source: Path) -> PreparedChapter:
    """Return an exact source copy, speech projection, diagnostics, and anchors.

    Anchor offsets are Python character boundaries in the unchanged source.
    They follow headings and spoken sentence or passage ends; generated
    ``[chapter]`` cues have no source anchor.
    """
    path = source.expanduser().resolve()
    source_bytes = path.read_bytes()
    original = source_bytes.decode("utf-8")
    narration_lines: list[str] = []

    lines = original.splitlines(keepends=True)
    front_matter_lines, unclosed_front_matter = _leading_front_matter(lines)
    caption_lines = _following_caption_lines(lines)
    source_offset = 0
    diagnostics: list[SourceDiagnostic] = []
    insertion_positions: set[int] = set()
    fence_character: str | None = None
    fence_length = 0
    if unclosed_front_matter:
        diagnostics.append(
            SourceDiagnostic(
                code="unclosed_front_matter",
                message="leading front matter is missing its closing `---`",
                position=SourcePosition(offset=0, line=1, character=1),
            )
        )
    for line_index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if line_index < front_matter_lines or line_index in caption_lines:
            source_offset += len(line)
            continue
        if fence_character is not None:
            diagnostics.extend(
                _bracket_diagnostics(
                    [
                        (character, source_offset + index, source_offset + index + 1)
                        for index, character in enumerate(content)
                    ],
                    original,
                )
            )
            narration_lines.append(line)
            if re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                content,
            ):
                fence_character = None
                fence_length = 0
            source_offset += len(line)
            continue
        fence_match = _FENCE_START.match(content)
        if fence_match is not None:
            marker = fence_match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            diagnostics.append(
                _source_diagnostic(
                    original,
                    source_offset + fence_match.start(1),
                    "unsupported_code_block",
                    "code blocks are not supported for narration",
                )
            )
            diagnostics.extend(
                _bracket_diagnostics(
                    [
                        (character, source_offset + index, source_offset + index + 1)
                        for index, character in enumerate(content)
                    ],
                    original,
                )
            )
            narration_lines.append(line)
            source_offset += len(line)
            continue
        if _THEMATIC_BREAK.fullmatch(content) is not None:
            if ending:
                narration_lines.append(ending)
            source_offset += len(line)
            continue
        unsupported_offset = _unsupported_block_offset(content)
        if unsupported_offset is not None:
            diagnostics.append(
                _source_diagnostic(
                    original,
                    source_offset + unsupported_offset,
                    "unsupported_markdown_structure",
                    "unsupported block Markdown is retained in forced narration",
                )
            )
        match = _HEADING.fullmatch(content)
        if match is None:
            projected_items = _project_inline(content, source_offset)
            diagnostics.extend(_bracket_diagnostics(projected_items, original))
            inline_code = _inline_code_diagnostics(projected_items, original)
            diagnostics.extend(inline_code)
            projected = "".join(char for char, _, _ in projected_items)
            if projected or not content.strip():
                narration_lines.append(projected + ending)
            if unsupported_offset is None and not inline_code:
                insertion_positions.update(_sentence_boundaries(projected_items))
            source_offset += len(line)
            continue
        heading = match.group(1) or ""
        heading_start = match.start(1) if match.group(1) is not None else len(content)
        heading = re.sub(r"[ \t]+#+[ \t]*$", "", heading)
        heading_items = _project_inline(heading, source_offset + heading_start)
        diagnostics.extend(_bracket_diagnostics(heading_items, original))
        inline_code = _inline_code_diagnostics(heading_items, original)
        diagnostics.extend(inline_code)
        heading_text = "".join(char for char, _, _ in heading_items)
        narration_lines.append(f"[chapter] {heading_text}{ending}")
        if heading_text and not inline_code:
            insertion_positions.add(source_offset + heading_start + len(heading))
        source_offset += len(line)

    narration = "".join(narration_lines).lstrip("\r\n")
    return PreparedChapter(
        source=SourceIdentity(
            path=path, content_sha256=sha256(source_bytes).hexdigest()
        ),
        original_markdown=original,
        narration=narration,
        insertion_positions=tuple(sorted(insertion_positions)),
        diagnostics=tuple(diagnostics),
    )


def _following_caption_lines(lines: list[str]) -> set[int]:
    """Find italic-only lines associated with a preceding image line."""
    captions: set[int] = set()
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        if _IMAGE_AT_END.search(content) is None:
            continue
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index < len(lines) and _is_italic_caption(lines[next_index].strip()):
            captions.add(next_index)
    return captions


def _unsupported_block_offset(content: str) -> int | None:
    """Locate a common block construct outside the supported prose subset."""
    for pattern in (
        _UNSUPPORTED_LIST_OR_QUOTE,
        _UNSUPPORTED_HTML,
        _UNSUPPORTED_INDENTED_CODE,
        _UNSUPPORTED_TABLE,
    ):
        match = pattern.match(content)
        if match is not None:
            return match.start(1)
    return None


def _leading_front_matter(lines: list[str]) -> tuple[int, bool]:
    """Return the number of leading front-matter lines to exclude and its state."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return 0, False
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index + 1, False
    return 1, True


def _is_italic_caption(text: str) -> bool:
    """Return whether text consists of one single-delimiter italic span."""
    if _THEMATIC_BREAK.fullmatch(text) is not None:
        return False
    if len(text) < 3:
        return False
    return text[0] == text[-1] and text[0] in "*_" and text[1] != text[0]


def _bracket_diagnostics(
    projected: list[tuple[str, int, int]], original: str
) -> list[SourceDiagnostic]:
    """Find existing square-bracket speech constructs in retained narration."""
    diagnostics: list[SourceDiagnostic] = []
    opening_positions: list[int] = []
    reported: set[int] = set()
    for character, source_offset, _ in projected:
        if character == "[":
            if not opening_positions:
                opening_positions.append(source_offset)
        elif character == "]":
            if opening_positions:
                opening = opening_positions.pop()
                if opening not in reported:
                    diagnostics.append(_bracket_diagnostic(original, opening))
                    reported.add(opening)
            else:
                diagnostics.append(_bracket_diagnostic(original, source_offset))
    for opening in opening_positions:
        if opening not in reported:
            diagnostics.append(_bracket_diagnostic(original, opening))
    return diagnostics


def _inline_code_diagnostics(
    projected: list[tuple[str, int, int]], original: str
) -> list[SourceDiagnostic]:
    """Report retained backtick spans while leaving their source text intact."""
    diagnostics: list[SourceDiagnostic] = []
    index = 0
    while index < len(projected):
        if projected[index][0] != "`":
            index += 1
            continue
        opening = index
        while index < len(projected) and projected[index][0] == "`":
            index += 1
        marker_length = index - opening
        closing_end: int | None = None
        search = index
        while search < len(projected):
            if projected[search][0] != "`":
                search += 1
                continue
            run_end = search
            while run_end < len(projected) and projected[run_end][0] == "`":
                run_end += 1
            if run_end - search == marker_length:
                closing_end = run_end
                break
            search = run_end
        diagnostics.append(
            _source_diagnostic(
                original,
                projected[opening][1],
                "unsupported_inline_code",
                "inline code is not supported for narration",
            )
        )
        index = closing_end if closing_end is not None else index
    return diagnostics


def _sentence_boundaries(projected: list[tuple[str, int, int]]) -> set[int]:
    """Return safe source offsets after sentences and final prose passages."""
    boundaries: set[int] = set()
    index = 0
    while index < len(projected):
        if projected[index][0] not in ".!?":
            index += 1
            continue
        end = index
        while end + 1 < len(projected) and projected[end + 1][0] in ".!?":
            end += 1
        following = projected[end + 1][0] if end + 1 < len(projected) else None
        if following is None or following.isspace():
            boundaries.add(projected[end][2])
        index = end + 1
    if projected:
        boundaries.add(projected[-1][2])
    return boundaries


def _bracket_diagnostic(original: str, offset: int) -> SourceDiagnostic:
    return _source_diagnostic(
        original,
        offset,
        "existing_bracketed_construct",
        "existing bracketed speech construct is not supported",
    )


def _source_diagnostic(
    original: str, offset: int, code: str, message: str
) -> SourceDiagnostic:
    prefix = original[:offset]
    line_number = prefix.count("\n") + 1
    line_start = prefix.rfind("\n")
    return SourceDiagnostic(
        code=code,
        message=message,
        position=SourcePosition(
            offset=offset,
            line=line_number,
            character=offset - line_start,
        ),
    )


def _project_inline(text: str, source_offset: int) -> list[tuple[str, int, int]]:
    """Remove supported inline Markdown syntax while retaining source positions."""
    projected: list[tuple[str, int, int]] = []
    cursor = 0

    while cursor < len(text):
        image_start = text.startswith("![", cursor)
        link_start = text.startswith("[", cursor)
        if image_start or link_start:
            opening = cursor + 1 if image_start else cursor
            label_end = _find_label_end(text, opening)
            if label_end is not None and text[label_end + 1 : label_end + 2] == "(":
                link_end = _find_parenthesis_end(text, label_end + 1)
                if link_end is not None:
                    if link_start:
                        label = _project_inline(
                            text[opening + 1 : label_end],
                            source_offset + opening + 1,
                        )
                        projected.extend(
                            (character, start, source_offset + link_end + 1)
                            for character, start, _ in label
                        )
                    elif _is_italic_caption(text[link_end + 1 :].strip()):
                        cursor = len(text)
                        continue
                    cursor = link_end + 1
                    if image_start and (not projected or projected[-1][0] in " \t"):
                        while cursor < len(text) and text[cursor] in " \t":
                            cursor += 1
                    continue

        delimiter = next(
            (
                marker
                for marker in ("**", "__", "*", "_")
                if text.startswith(marker, cursor)
            ),
            None,
        )
        if delimiter is not None:
            closing = text.find(delimiter, cursor + len(delimiter))
            if closing > cursor + len(delimiter) and delimiter.startswith("_"):
                before = text[cursor - 1] if cursor > 0 else ""
                after_index = closing + len(delimiter)
                after = text[after_index] if after_index < len(text) else ""
                if (before and before.isalnum()) or (after and after.isalnum()):
                    delimiter = None
            if delimiter is not None and closing > cursor + len(delimiter):
                emphasis = _project_inline(
                    text[cursor + len(delimiter) : closing],
                    source_offset + cursor + len(delimiter),
                )
                projected.extend(
                    (character, start, source_offset + closing + len(delimiter))
                    for character, start, _ in emphasis
                )
                cursor = closing + len(delimiter)
                continue

        projected.append(
            (text[cursor], source_offset + cursor, source_offset + cursor + 1)
        )
        cursor += 1

    return projected


def _find_label_end(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_parenthesis_end(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None
