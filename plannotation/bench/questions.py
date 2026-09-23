# SPDX-License-Identifier: Apache-2.0
"""The benchmark's questions: merged from the samples' ground truth, and loaded back.

``bench/questions.jsonl`` is the committed question set. It is built by merging each
``samples/<name>/groundtruth.jsonl`` and pointing every question at the plannotated
PDF it is about, so the file says everything a run needs except the PDFs themselves,
which ``plannotation samples build`` regenerates byte for byte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: The PDF each sample directory's questions are asked about.
SAMPLE_DOCUMENT = "sheet.plannotated.pdf"

#: Scalar or list: what one question expects.
Answer = str | int | float | tuple[str, ...]


@dataclass(frozen=True)
class Question:
    """One question with its answer key.

    Attributes:
        id: Stable identifier, ``<sheet>-<nn>``; the response cache is keyed on it.
        sheet: The sheet number the question is about.
        category: What kind of fact it asks for, such as ``dimension``.
        question: The text put to the model.
        answer: The expected answer, taken from the model the sheet was drawn from.
        document: The PDF to show, relative to the directory the run starts in.
        page: The one-based page of that PDF.
        unit: The unit a numeric answer is in, when it has one.
        requires_plannotation: True when only the plannotation carries the answer, such
            as an IFC GlobalId. Such questions are reported apart, since the plain
            condition cannot be expected to get them right.
    """

    id: str
    sheet: str
    category: str
    question: str
    answer: Answer
    document: str
    page: int = 1
    unit: str | None = None
    requires_plannotation: bool = False

    def to_json(self) -> dict[str, object]:
        """Return the question as one line of ``questions.jsonl`` would hold it.

        Returns:
            A JSON-ready mapping, with absent optional fields left out.
        """
        record: dict[str, object] = {
            "id": self.id,
            "sheet": self.sheet,
            "category": self.category,
            "question": self.question,
            "answer": list(self.answer) if isinstance(self.answer, tuple) else self.answer,
            "document": self.document,
            "page": self.page,
        }
        if self.unit is not None:
            record["unit"] = self.unit
        if self.requires_plannotation:
            record["requiresPlannotation"] = True
        return record

    @classmethod
    def from_json(cls, record: dict[str, object]) -> Question:
        """Load one line of ``questions.jsonl``.

        Args:
            record: The decoded line.

        Returns:
            The question.

        Raises:
            ValueError: If a required field is missing or has the wrong type.
        """
        try:
            answer = _answer(record["answer"])
            page = _page(record.get("page", 1))
            unit = record.get("unit")
            return cls(
                id=str(record["id"]),
                sheet=str(record["sheet"]),
                category=str(record["category"]),
                question=str(record["question"]),
                answer=answer,
                document=str(record["document"]),
                page=page,
                unit=None if unit is None else str(unit),
                requires_plannotation=bool(record.get("requiresPlannotation", False)),
            )
        except (KeyError, TypeError) as error:
            msg = f"not a benchmark question: {error}"
            raise ValueError(msg) from error


def _page(value: object) -> int:
    """Check a page number's type.

    Args:
        value: The decoded ``page`` field.

    Returns:
        The page number.

    Raises:
        TypeError: If it is not an integer.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    msg = f"page must be an integer, not {value!r}"
    raise TypeError(msg)


def _answer(value: object) -> Answer:
    """Check an answer's type, turning a list into a tuple so it stays immutable.

    Args:
        value: The decoded ``answer`` field.

    Returns:
        The answer.

    Raises:
        TypeError: If it is not a string, a number or a list of strings.
    """
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return value
    msg = f"an answer is a string, a number or a list of strings, not {value!r}"
    raise TypeError(msg)


def merge_ground_truth(samples_dir: Path, *, relative_to: Path) -> list[Question]:
    """Merge every sample's ground truth into one question set.

    Samples are taken in name order and questions in file order, so the merged set,
    and the id each question gets, is the same on every machine.

    Args:
        samples_dir: The directory ``plannotation samples build`` wrote.
        relative_to: The directory the benchmark runs from; document paths are made
            relative to it so the question file does not record where it was built.

    Returns:
        The questions.

    Raises:
        FileNotFoundError: If no sample directory has a ``groundtruth.jsonl``.
    """
    questions: list[Question] = []
    sources = sorted(samples_dir.glob("*/groundtruth.jsonl"))
    if not sources:
        msg = f"no */groundtruth.jsonl under {samples_dir}; run `plannotation samples build`"
        raise FileNotFoundError(msg)
    for source in sources:
        document = (source.parent / SAMPLE_DOCUMENT).resolve()
        try:
            location = document.relative_to(relative_to.resolve()).as_posix()
        except ValueError:
            location = document.as_posix()
        numbers: dict[str, int] = {}
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            sheet = str(record["sheet"])
            numbers[sheet] = numbers.get(sheet, 0) + 1
            record["id"] = f"{sheet}-{numbers[sheet]:02d}"
            record["document"] = location
            questions.append(Question.from_json(record))
    return questions


def write_questions(questions: list[Question], path: Path) -> None:
    """Write a question set as JSON Lines, one sorted-key object per line.

    Args:
        questions: The questions.
        path: Where to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(question.to_json(), sort_keys=True, ensure_ascii=False) for question in questions
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_questions(path: Path) -> list[Question]:
    """Read a question set.

    Args:
        path: A JSON Lines file of questions.

    Returns:
        The questions, in file order.

    Raises:
        ValueError: If a line is not a question, or two share an id.
    """
    questions: list[Question] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            question = Question.from_json(json.loads(line))
        except (json.JSONDecodeError, ValueError) as error:
            msg = f"{path}:{number}: {error}"
            raise ValueError(msg) from error
        if question.id in seen:
            msg = f"{path}:{number}: duplicate question id {question.id!r}"
            raise ValueError(msg)
        seen.add(question.id)
        questions.append(question)
    return questions
