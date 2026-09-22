# SPDX-License-Identifier: Apache-2.0
"""The request each condition sends: one template, and the label as a tool result.

Both conditions send the same system prompt, the same page picture, the same
extracted text, and the same question block. ``labelled`` differs only in that the
conversation also holds a ``get_page_label`` call and its result -- the label as
canonical JSON -- before the question, which is how an MCP host hands a label over.

The prompt cache breakpoint sits after the page material and before the question, so
every question about a sheet after the first reads the page from the cache.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from planlabel.bench.page import PageInput
    from planlabel.bench.questions import Question

#: The two conditions.
Condition = Literal["plain", "labelled"]
CONDITIONS: Final[tuple[Condition, ...]] = ("plain", "labelled")

#: Bumped whenever anything below changes what is sent, so cached answers to the old
#: prompt are never served for the new one.
PROMPT_VERSION: Final = "1"

#: Room for adaptive thinking as well as the short answer.
MAX_TOKENS: Final = 16000

#: The synthetic tool call's id. Any id is valid; a fixed one keeps requests stable.
LABEL_TOOL_USE_ID: Final = "toolu_planlabel_page_label"

SYSTEM_PROMPT: Final = (
    "You answer questions about one sheet of a set of construction drawings. You are "
    "shown the sheet rendered as an image, and the text a PDF reader extracts from it. "
    "Use everything you are given. Answer from the drawing, not from what drawings of "
    "this kind usually show."
)

ANSWER_INSTRUCTIONS: Final = (
    "Put the answer alone in the `answer` field: a number without its unit, a scale as "
    "1:n, a sheet number, a comma-separated list, an IFC class name, or a single word. "
    "If the sheet does not tell you, answer `unknown`."
)

LABEL_TOOL: Final[dict[str, object]] = {
    "name": "get_page_label",
    "description": (
        "Return the PlanLabel page label for a page of the open drawing: a JSON "
        "document naming the sheet, its viewports and how paper maps to model "
        "coordinates, the model elements drawn with their IFC class, GlobalId and "
        "mark, and the annotations -- grids, dimensions and what they measure, tags "
        "and what they show, callouts and the sheet they point to."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"page": {"type": "integer", "description": "One-based page number."}},
        "required": ["page"],
        "additionalProperties": False,
    },
}

ANSWER_FORMAT: Final[dict[str, object]] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
}

_CACHE: Final = {"type": "ephemeral"}


def question_block(question: Question) -> str:
    """Return the text that asks the question; identical under both conditions.

    Args:
        question: The question.

    Returns:
        The question and the answer instructions.
    """
    return f"Question: {question.question}\n\n{ANSWER_INSTRUCTIONS}"


def build_request(
    question: Question,
    page: PageInput,
    *,
    condition: Condition,
    model: str,
    effort: str | None = None,
) -> dict[str, object]:
    """Build the keyword arguments of one ``messages.create`` call.

    Args:
        question: What to ask.
        page: The page it is about.
        condition: ``plain`` or ``labelled``.
        model: The model id.
        effort: An ``output_config.effort`` level, or None for the model's default.

    Returns:
        The request, as plain JSON-ready data.

    Raises:
        ValueError: If the condition is ``labelled`` and the page has no label.
    """
    picture = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(page.png).decode("ascii"),
        },
    }
    extracted = f"Text extracted from the PDF page:\n\n{page.text}"
    asking = {"type": "text", "text": question_block(question)}
    messages: list[dict[str, object]]
    request: dict[str, object] = {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT}
    if condition == "plain":
        messages = [
            {
                "role": "user",
                "content": [
                    picture,
                    {"type": "text", "text": extracted, "cache_control": _CACHE},
                    asking,
                ],
            }
        ]
    else:
        if page.label is None:
            msg = f"{question.document} page {question.page} has no label to supply"
            raise ValueError(msg)
        request["tools"] = [LABEL_TOOL]
        messages = [
            {"role": "user", "content": [picture, {"type": "text", "text": extracted}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": LABEL_TOOL_USE_ID,
                        "name": LABEL_TOOL["name"],
                        "input": {"page": question.page},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": LABEL_TOOL_USE_ID,
                        "content": page.label,
                        "cache_control": _CACHE,
                    },
                    asking,
                ],
            },
        ]
    request["messages"] = messages
    output_config: dict[str, object] = {"format": ANSWER_FORMAT}
    if effort is not None:
        output_config["effort"] = effort
    request["output_config"] = output_config
    return request
