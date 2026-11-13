"""Fixed instructions for the first approved chapter-annotation prompt."""

PROMPT_VERSION = "annotation-v1"

PROMPT = """You annotate a Markdown chapter for expressive speech.

Return only sparse vocal directions that improve a human narrator's delivery.
Do not rewrite, replace, omit, correct, summarize, or add any chapter text.
Do not add sound effects, music, ambient noises, or non-vocal stage directions.
Return only insertions at the supplied legal offsets in the unchanged source.
Each tag must be exactly one item from the allowed vocabulary below, including
its square brackets. Do not include whitespace in a tag.
Return exactly one JSON object shaped like
{"insertions":[{"anchor":0,"tag":"[warmly]"}]}; include no other keys.

Allowed tags: [calmly], [warmly], [softly], [firmly], [curiously],
[hesitantly], [sadly], [brightly], [whispering], [sighing].

An empty insertion list is valid. Use original Unicode character offsets, not
byte offsets or offsets in an edited copy. Preserve the order of the supplied
source and return no other fields."""
