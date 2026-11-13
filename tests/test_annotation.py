import json
from pathlib import Path

import httpx
import pytest

from audiobook.annotation import annotate_chapter
from audiobook.contracts import (
    VOCAL_DIRECTION_TAGS,
    AnnotatedChapter,
    Insertion,
    PreparedChapter,
    ProviderError,
)
from audiobook.markdown import prepare_chapter


def _responses_result(
    result_text: str | None = None,
    *,
    status: str = "completed",
    response_id: str = "resp-test",
    content: list[dict[str, object]] | None = None,
) -> httpx.Response:
    if content is None:
        content = (
            []
            if result_text is None
            else [{"type": "output_text", "text": result_text}]
        )
    return httpx.Response(
        200,
        json={
            "id": response_id,
            "status": status,
            "output": [{"type": "message", "role": "assistant", "content": content}],
        },
    )


def _annotate_response(
    prepared: PreparedChapter, response: httpx.Response
) -> AnnotatedChapter:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: response),
        headers={"Authorization": "Bearer test-key"},
    ) as provider:
        return annotate_chapter(prepared, provider)


def test_annotation_inserts_one_direction_without_changing_source(
    tmp_path: Path,
) -> None:
    source_text = "Mara smiled."
    source = tmp_path / "1-mara.md"
    source.write_text(source_text, encoding="utf-8")
    prepared = prepare_chapter(source)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp-test-1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"insertions": [{"anchor": 12, "tag": "[warmly]"}]}
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer test-key"},
    ) as provider:
        annotated = annotate_chapter(prepared, provider)

    assert source.read_text(encoding="utf-8") == source_text
    assert annotated.markdown == "Mara smiled. [warmly]"
    assert annotated.narration == "Mara smiled. [warmly]"
    assert annotated.insertion_record.insertions == (
        Insertion(anchor=12, tag=" [warmly]"),
    )
    assert annotated.insertion_record.source_sha256 == prepared.source.content_sha256
    assert annotated.insertion_record.prompt_version
    assert annotated.insertion_record.provider_response_id == "resp-test-1"

    request = requests[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.url == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["input"] == source_text
    assert "Return only sparse vocal directions" in body["instructions"]
    assert "Do not add sound effects" in body["instructions"]
    assert "Return exactly one JSON object" in body["instructions"]
    output_format = body["text"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    assert output_format["schema"]["required"] == ["insertions"]
    assert output_format["schema"]["additionalProperties"] is False
    tag_schema = output_format["schema"]["properties"]["insertions"]["items"][
        "properties"
    ]["tag"]
    assert tag_schema == {"type": "string", "enum": sorted(VOCAL_DIRECTION_TAGS)}
    anchor_schema = output_format["schema"]["properties"]["insertions"]["items"][
        "properties"
    ]["anchor"]
    assert anchor_schema == {
        "type": "integer",
        "enum": list(prepared.insertion_positions),
    }


def test_large_anchor_lists_are_constrained_in_separate_requests(tmp_path: Path):
    source = tmp_path / "1-long.md"
    source.write_text("Mara smiled. " * 1200, encoding="utf-8")
    prepared = prepare_chapter(source)
    batches = []

    def respond(request):
        body = json.loads(request.content)
        properties = body["text"]["format"]["schema"]["properties"]["insertions"][
            "items"
        ]["properties"]
        anchors = properties["anchor"]["enum"]
        assert len(anchors) + len(properties["tag"]["enum"]) <= 1000
        assert body["input"] == prepared.original_markdown
        batches.append(anchors)
        return _responses_result(
            json.dumps({"insertions": [{"anchor": anchors[0], "tag": "[warmly]"}]}),
            response_id=f"resp-batch-{len(batches)}",
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
        annotated = annotate_chapter(prepared, provider)
    assert len(batches) == 2
    assert [anchor for batch in batches for anchor in batch] == list(
        prepared.insertion_positions
    )
    assert [item.anchor for item in annotated.insertion_record.insertions] == [
        batch[0] for batch in batches
    ]
    assert annotated.markdown.replace("[warmly] ", "") == prepared.original_markdown


def test_invalid_anchor_reports_chapter_and_rejected_value(tmp_path: Path):
    source = tmp_path / "1-mara.md"
    source.write_text("Mara smiled.", encoding="utf-8")
    with pytest.raises(ProviderError, match="invalid annotation anchor") as error:
        _annotate_response(
            prepare_chapter(source),
            _responses_result(
                json.dumps({"insertions": [{"anchor": 7, "tag": "[warmly]"}]})
            ),
        )
    assert "1-mara.md" in str(error.value)
    assert "7" in (error.value.debug_details or "")


def test_disallowed_direction_includes_bounded_debug_details(tmp_path: Path) -> None:
    source = tmp_path / "1-mara.md"
    source.write_text("Mara smiled.", encoding="utf-8")
    prepared = prepare_chapter(source)
    tag = "[door slams]\n" + "x" * 2000
    response = _responses_result(
        json.dumps({"insertions": [{"anchor": 12, "tag": tag}]})
    )
    with pytest.raises(ProviderError, match="disallowed vocal direction") as error:
        _annotate_response(prepared, response)
    details = error.value.debug_details
    assert details is not None
    assert "[door slams]" in details
    assert "anchor=12" in details
    assert "\\n" in details
    assert "\n" not in details
    assert len(details) <= 300


def test_annotation_uses_unicode_source_offsets_when_text_repeats(
    tmp_path: Path,
) -> None:
    source_text = "🦊 smiled. 🦊 smiled."
    source = tmp_path / "2-repeated.md"
    source.write_text(source_text, encoding="utf-8")
    prepared = prepare_chapter(source)
    assert prepared.insertion_positions == (9, 19)

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp-unicode",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"insertions": [{"anchor": 9, "tag": "[warmly]"}]}
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
        annotated = annotate_chapter(prepared, provider)

    assert annotated.markdown == "🦊 smiled. [warmly] 🦊 smiled."
    assert annotated.narration == "🦊 smiled. [warmly] 🦊 smiled."
    assert annotated.insertion_record.insertions == (
        Insertion(anchor=9, tag=" [warmly]"),
    )


def test_annotation_preserves_bom_crlf_and_existing_brackets(tmp_path: Path) -> None:
    source_bytes = b"\xef\xbb\xbfA [old cue] sentence.\r\n"
    source = tmp_path / "3-source-bytes.md"
    source.write_bytes(source_bytes)
    prepared = prepare_chapter(source)
    assert prepared.original_markdown == "\ufeffA [old cue] sentence.\r\n"
    assert prepared.insertion_positions == (22,)
    assert [diagnostic.code for diagnostic in prepared.diagnostics] == [
        "existing_bracketed_construct"
    ]

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp-crlf",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "insertions": [
                                            {"anchor": 22, "tag": "[whispering]"}
                                        ]
                                    }
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as provider:
        annotated = annotate_chapter(prepared, provider)

    assert source.read_bytes() == source_bytes
    assert annotated.markdown == "\ufeffA [old cue] sentence. [whispering]\r\n"
    assert annotated.narration == "\ufeffA [old cue] sentence. [whispering]\r\n"
    assert annotated.insertion_record.source_sha256 == prepared.source.content_sha256
    assert annotated.insertion_record.insertions == (
        Insertion(anchor=22, tag=" [whispering]"),
    )


@pytest.mark.parametrize(
    ("source_text", "result_text"),
    [
        ("Mara smiled.", "{"),
        (
            "Mara smiled.",
            json.dumps({"insertions": [{"anchor": 12, "tag": "[door slams]"}]}),
        ),
        (
            "Mara smiled.",
            json.dumps({"insertions": [{"anchor": 0, "tag": "[warmly]"}]}),
        ),
        (
            "---\ntitle: Tale\n---\nMara smiled.",
            json.dumps({"insertions": [{"anchor": 4, "tag": "[warmly]"}]}),
        ),
        (
            "Mara smiled.",
            json.dumps(
                {
                    "insertions": [{"anchor": 12, "tag": "[warmly]"}],
                    "replacement": "Mara laughed.",
                }
            ),
        ),
        (
            "Mara smiled. Then she left.",
            json.dumps(
                {
                    "insertions": [
                        {"anchor": 27, "tag": "[sadly]"},
                        {"anchor": 12, "tag": "[warmly]"},
                    ]
                }
            ),
        ),
        (
            "Mara smiled. Then she left.",
            json.dumps(
                {
                    "insertions": [
                        {"anchor": 12, "tag": "[warmly]"},
                        {"anchor": 12, "tag": "[sadly]"},
                    ]
                }
            ),
        ),
    ],
    ids=[
        "malformed-json",
        "environmental-effect",
        "non-anchor-location",
        "front-matter-location",
        "attempted-replacement",
        "out-of-order-anchors",
        "duplicate-anchors",
    ],
)
def test_annotation_rejects_invalid_model_insertions(
    tmp_path: Path, source_text: str, result_text: str
) -> None:
    source = tmp_path / "invalid-output.md"
    source.write_text(source_text, encoding="utf-8")
    prepared = prepare_chapter(source)

    with pytest.raises(ProviderError):
        _annotate_response(prepared, _responses_result(result_text))


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (
            "incomplete",
            [{"type": "output_text", "text": '{"insertions": []}'}],
        ),
        ("completed", [{"type": "refusal", "refusal": "Cannot annotate."}]),
    ],
    ids=["incomplete", "refusal"],
)
def test_annotation_rejects_incomplete_and_refused_responses(
    tmp_path: Path, status: str, content: list[dict[str, object]]
) -> None:
    source = tmp_path / "provider-status.md"
    source.write_text("Mara smiled.", encoding="utf-8")
    prepared = prepare_chapter(source)

    with pytest.raises(ProviderError):
        _annotate_response(
            prepared,
            _responses_result(status=status, content=content),
        )


def test_annotation_rejects_unrequested_response_items_with_valid_text(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unexpected-item.md"
    source.write_text("Mara smiled.", encoding="utf-8")
    prepared = prepare_chapter(source)
    response = httpx.Response(
        200,
        json={
            "id": "resp-unexpected-item",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "replace_chapter",
                    "arguments": "{}",
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"insertions": []}',
                        }
                    ],
                },
            ],
        },
    )

    with pytest.raises(ProviderError):
        _annotate_response(prepared, response)
