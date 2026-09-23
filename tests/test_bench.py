# SPDX-License-Identifier: Apache-2.0
"""The benchmark harness, exercised end to end without ever reaching the API.

The design brief's rules for Phase 8 are what is tested: both conditions share one
prompt and differ only by the label supplied as a tool result; numbers are scored
within 1 % and everything else exactly; responses are cached on condition, model and
question so a re-run asks nothing; and no API key is needed to run what is cached.

A fake transport stands in for the API everywhere. Where the real SDK is exercised,
it talks to an in-process mock HTTP transport, so the request shapes are checked by
the SDK's own serialiser and still nothing leaves the machine.
"""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from typer.testing import CliRunner

import tests.pdf_fixtures as fx
from plannotation.bench import runner as runner_module
from plannotation.bench.cli import app
from plannotation.bench.page import PageInput, encode_png, load_page
from plannotation.bench.pricing import PRICES, cost
from plannotation.bench.prompt import (
    PLANNOTATION_TOOL_USE_ID,
    PROMPT_VERSION,
    build_request,
    question_block,
)
from plannotation.bench.questions import (
    Question,
    load_questions,
    merge_ground_truth,
    write_questions,
)
from plannotation.bench.report import (
    README_END,
    README_START,
    Tally,
    categories,
    delta,
    render_readme_section,
    render_report,
    update_readme,
)
from plannotation.bench.runner import (
    Record,
    Reply,
    RunConfig,
    Usage,
    answer_from,
    anthropic_transport,
    cache_path,
    load_api_key,
    pending,
    read_records,
    run,
    write_records,
)
from plannotation.bench.score import parse_number, score
from plannotation.errors import BenchError
from plannotation.pdf import embed
from plannotation.pdf.extract import page_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from plannotation.bench.prompt import Condition

MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def drawings(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a labelled and an unlabelled copy of the two-page drawing set.

    Args:
        tmp_path_factory: pytest's temporary directory factory.

    Returns:
        The directory holding ``plain.pdf`` and ``labelled.pdf``.
    """
    root = tmp_path_factory.mktemp("bench")
    plain = root / "plain.pdf"
    plain.write_bytes(fx.build_drawing_set())
    labels = fx.drawing_set_plannotations()
    embed.attach(plain, labels, embed.build_index(labels), root / "labelled.pdf", mod_date=MOD_DATE)
    return root


def question(**overrides: Any) -> Question:  # noqa: ANN401
    """Build a question about page 1 of the labelled drawing set.

    Args:
        **overrides: Fields to change.

    Returns:
        The question.
    """
    fields: dict[str, Any] = {
        "id": "A-101-01",
        "sheet": "A-101",
        "category": "dimension",
        "question": "What is the distance between grid A and grid B?",
        "answer": 8000.0,
        "document": "labelled.pdf",
        "unit": "mm",
    }
    return Question(**{**fields, **overrides})


QUESTIONS = (
    question(),
    question(
        id="A-101-02", category="grid", question="Which grid axes?", answer=("1", "A"), unit=None
    ),
    question(
        id="A-101-03",
        category="model",
        question="What is the GlobalId?",
        answer="0abc",
        unit=None,
        requires_plannotation=True,
    ),
)


class FakeTransport:
    """Answers every request with a fixed reply, and remembers what it was sent."""

    def __init__(self, text: str = '{"answer": "8000"}', stop_reason: str = "end_turn") -> None:
        """Set the reply.

        Args:
            text: The reply's text.
            stop_reason: Its stop reason.
        """
        self.text = text
        self.stop_reason = stop_reason
        self.requests: list[dict[str, object]] = []

    def __call__(self, request: dict[str, object]) -> Reply:
        """Answer one request.

        Args:
            request: The request.

        Returns:
            The fixed reply.
        """
        self.requests.append(request)
        return Reply(
            text=self.text,
            stop_reason=self.stop_reason,
            model=str(request["model"]),
            usage=Usage(input_tokens=100, output_tokens=10, cache_read_input_tokens=1000),
        )


def refuse(request: dict[str, object]) -> Reply:
    """Fail the test: the run was meant to be answered from the cache.

    Args:
        request: The request that should not have been sent.

    Raises:
        AssertionError: Always.
    """
    msg = f"a cached run sent a request: {request['model']}"
    raise AssertionError(msg)


def ticking() -> Callable[[], float]:
    """Return a clock that advances two seconds per reading.

    Returns:
        The clock.
    """
    readings = iter(range(0, 1000, 2))
    return lambda: float(next(readings))


def config(tmp: Path, root: Path, **overrides: Any) -> RunConfig:  # noqa: ANN401
    """Build a run configuration with the cache under ``tmp``.

    Args:
        tmp: Where the cache goes.
        root: What document paths are relative to.
        **overrides: Fields to change.

    Returns:
        The configuration.
    """
    fields: dict[str, Any] = {
        "condition": "labelled",
        "model": "claude-opus-5",
        "cache_dir": tmp / "cache",
        "base_dir": root,
    }
    return RunConfig(**{**fields, **overrides})


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
#: Every required field of a question line but the answer.
BASE = '"id": "x", "sheet": "s", "category": "c", "question": "q", "document": "d"'


class TestQuestions:
    """The question set: merged from ground truth, written, and read back."""

    @staticmethod
    def _samples(tmp_path: Path) -> Path:
        """Write two samples' ground truth.

        Args:
            tmp_path: Where to write.

        Returns:
            The samples directory.
        """
        samples = tmp_path / "samples"
        for name, sheet in (("b-plan", "B-1"), ("a-plan", "A-1")):
            (samples / name).mkdir(parents=True)
            lines = [
                {"sheet": sheet, "category": "count", "question": "How many?", "answer": 4},
                {
                    "sheet": sheet,
                    "category": "grid",
                    "question": "Which axes?",
                    "answer": ["1", "A"],
                },
                {
                    "sheet": sheet,
                    "category": "model",
                    "question": "GlobalId?",
                    "answer": "x",
                    "requiresPlannotation": True,
                },
            ]
            text = "\n".join(json.dumps(line) for line in lines) + "\n\n"
            (samples / name / "groundtruth.jsonl").write_text(text, encoding="utf-8")
        return samples

    def test_merging_numbers_questions_per_sheet_in_name_order(self, tmp_path: Path) -> None:
        """Ids are stable because the order they are given in is."""
        merged = merge_ground_truth(self._samples(tmp_path), relative_to=tmp_path)
        assert [q.id for q in merged] == [
            "A-1-01",
            "A-1-02",
            "A-1-03",
            "B-1-01",
            "B-1-02",
            "B-1-03",
        ]
        assert merged[0].document == "samples/a-plan/sheet.plannotated.pdf"
        assert merged[1].answer == ("1", "A")
        assert merged[2].requires_plannotation

    def test_a_document_outside_the_run_directory_is_recorded_absolute(
        self, tmp_path: Path
    ) -> None:
        """Rather than as a relative path that climbs out of it."""
        samples = self._samples(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        merged = merge_ground_truth(samples, relative_to=elsewhere)
        assert Path(merged[0].document).is_absolute()

    def test_merging_nothing_says_how_to_make_something(self, tmp_path: Path) -> None:
        """An empty question set would make a benchmark of nothing, silently."""
        with pytest.raises(FileNotFoundError, match="samples build"):
            merge_ground_truth(tmp_path, relative_to=tmp_path)

    def test_the_file_round_trips(self, tmp_path: Path) -> None:
        """What is written is what is read."""
        merged = merge_ground_truth(self._samples(tmp_path), relative_to=tmp_path)
        path = tmp_path / "bench" / "questions.jsonl"
        write_questions(merged, path)
        assert load_questions(path) == merged
        assert all(json.loads(line) for line in path.read_text("utf-8").splitlines())

    def test_optional_fields_are_left_out_when_absent(self) -> None:
        """Absent, not null, as everywhere else in Plannotation."""
        record = question(unit=None).to_json()
        assert "unit" not in record
        assert "requiresPlannotation" not in record

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            ("{not json", "1:"),
            ('{"id": "x"}', "not a benchmark question"),
            (f'{{{BASE}, "answer": {{"a": 1}}}}', "an answer is"),
            (f'{{{BASE}, "answer": true}}', "an answer is"),
            (f'{{{BASE}, "answer": 1, "page": "1"}}', "page must be an integer"),
        ],
    )
    def test_a_malformed_line_is_named(self, tmp_path: Path, line: str, message: str) -> None:
        """With its line number, so the file can be fixed."""
        path = tmp_path / "questions.jsonl"
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_questions(path)

    def test_a_duplicate_id_is_refused(self, tmp_path: Path) -> None:
        """Two questions under one id would share one cached answer."""
        path = tmp_path / "questions.jsonl"
        write_questions([question(), question()], path)
        with pytest.raises(ValueError, match="duplicate question id"):
            load_questions(path)

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "samples" / "groundtruth.jsonl").is_file(),
        reason="samples are not built; run make samples",
    )
    def test_the_committed_question_set_matches_the_samples(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A changed exporter must come with a regenerated ``bench/questions.jsonl``."""
        root = Path(__file__).parent.parent
        monkeypatch.chdir(root)
        merged = merge_ground_truth(root / "samples", relative_to=root)
        assert load_questions(root / "bench" / "questions.jsonl") == merged

    def test_the_committed_question_set_loads(self) -> None:
        """``bench/questions.jsonl`` is committed, so it must stay readable."""
        committed = Path(__file__).parent.parent / "bench" / "questions.jsonl"
        loaded = load_questions(committed)
        assert len(loaded) >= 30
        assert {q.category for q in loaded} >= {"count", "dimension", "grid", "tag", "model"}
        assert all(q.requires_plannotation == (q.category == "model") for q in loaded)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
class TestScore:
    """Exact for words and lists, within 1 % for numbers."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("8000", True),
            ("8,000", True),
            ("8 000", True),
            ("8'000", True),
            ("8.000,0", True),
            ("8,000.0", True),
            ("7950", True),
            ("8079", True),
            ("7900", False),
            ("8 m", True),
            ("800 cm", True),
            ("8000 mm", True),
            ("about 8000 millimetres", True),
            ("eight thousand", False),
            ("", False),
        ],
    )
    def test_a_length_is_read_as_it_is_written(self, given: str, *, expected: bool) -> None:
        """Separators and a unit are the ways people write the same number."""
        assert score(8000.0, given, unit="mm") is expected

    @pytest.mark.parametrize(
        ("given", "expected"), [("1:50", True), ("M 1 : 50", True), ("50", True), ("1:100", False)]
    )
    def test_a_scale_is_its_denominator(self, given: str, *, expected: bool) -> None:
        """The key stores 50 for 1:50."""
        assert score(50.0, given) is expected

    @pytest.mark.parametrize(("given", "expected"), [("0", True), ("1", False), ("none", False)])
    def test_zero_has_no_tolerance(self, given: str, *, expected: bool) -> None:
        """One per cent of nothing is nothing."""
        assert score(0, given) is expected

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("1, 2, A, B", True),
            ("A, B, 1 and 2", True),
            ("[A; B; 1; 2]", True),
            ("a b 1 2", True),
            ("A, B", False),
            ("A, B, 1, 2, 3", False),
        ],
    )
    def test_a_list_is_a_set(self, given: str, *, expected: bool) -> None:
        """Order and separators do not matter; membership does."""
        assert score(("1", "2", "A", "B"), given) is expected

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("IfcWall", True),
            ("ifcwall", True),
            ("`IfcWall`.", True),
            ('"IfcWall"', True),
            ("IfcWallStandardCase", False),
            ("a wall", False),
        ],
    )
    def test_a_word_is_matched_exactly(self, given: str, *, expected: bool) -> None:
        """Up to case and wrapping, and not an inch further."""
        assert score("IfcWall", given) is expected

    def test_no_answer_is_wrong(self) -> None:
        """A refusal or a cut-off answer is not right by default."""
        assert score("IfcWall", None) is False
        assert score(1.0, None) is False

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("8,5", 8.5),
            ("1,234,567", 1234567.0),
            ("1.234.567", 1234567.0),
            ("-3", -3.0),
            ("no number here", None),
        ],
    )
    def test_numbers_parse(self, text: str, value: float | None) -> None:
        """A lone comma before other than three digits is a decimal comma."""
        assert parse_number(text) == value

    def test_a_unit_is_left_alone_when_the_key_has_none(self) -> None:
        """Converting needs to know what to convert to."""
        assert parse_number("8 m") == 8.0
        assert parse_number("8 m", unit="mm") == 8000.0


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
class TestPricing:
    """Cost is an estimate from the table, or nothing."""

    def test_every_kind_of_token_is_priced(self) -> None:
        """Cache writes and reads are priced differently from plain input."""
        price = PRICES["claude-opus-5"]
        usage = Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)
        total = price.input + price.output + price.cache_write + price.cache_read
        assert cost("claude-opus-5", usage) == pytest.approx(total)
        assert price.cache_write == pytest.approx(price.input * 1.25)

    def test_an_unknown_model_has_no_cost(self) -> None:
        """Rather than a guessed one."""
        assert cost("some-other-model", Usage(10, 10)) is None


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------
PAGE = PageInput(png=b"\x89PNG", text="A B 1 2", label='{"page": {}}')


def sent(condition: Condition, effort: str | None = None) -> Any:  # noqa: ANN401
    """Build a request and put it through JSON, as the SDK will.

    Args:
        condition: The condition.
        effort: The effort level, if any.

    Returns:
        The request as decoded JSON, which also proves it serialises.
    """
    request = build_request(question(), PAGE, condition=condition, model="m", effort=effort)
    return json.loads(json.dumps(request))


class TestPrompt:
    """One template for both conditions; the label only as a tool result."""

    def test_plain_is_one_message_ending_in_the_question(self) -> None:
        """The cache breakpoint is on the page material, before the question."""
        request = sent("plain")
        assert len(request["messages"]) == 1
        content = request["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "text", "text"]
        assert content[1]["cache_control"] == {"type": "ephemeral"}
        assert content[2]["text"] == question_block(question())
        assert "tools" not in request

    def test_labelled_adds_a_tool_call_and_its_result_and_nothing_else(self) -> None:
        """Same picture, same text, same question; the label in between."""
        plain, labelled = sent("plain"), sent("labelled")
        user, call, result = labelled["messages"]
        assert user["content"][0] == plain["messages"][0]["content"][0]
        assert user["content"][1]["text"] == plain["messages"][0]["content"][1]["text"]
        tool_use = call["content"][0]
        assert call["role"] == "assistant"
        assert tool_use["type"] == "tool_use"
        assert result["content"][0]["tool_use_id"] == tool_use["id"] == PLANNOTATION_TOOL_USE_ID
        assert result["content"][0]["content"] == PAGE.label
        assert result["content"][1]["text"] == question_block(question())
        assert labelled["system"] == plain["system"]
        assert labelled["output_config"] == plain["output_config"]
        assert labelled["tools"][0]["name"] == tool_use["name"]

    def test_there_is_no_labelled_condition_without_a_label(self) -> None:
        """A tool result of nothing would measure nothing."""
        with pytest.raises(ValueError, match="no label"):
            build_request(question(), PageInput(b"", "", None), condition="labelled", model="m")

    def test_effort_is_sent_only_when_asked_for(self) -> None:
        """Otherwise the model's own default applies."""
        assert "effort" not in sent("plain")["output_config"]
        assert sent("plain", effort="high")["output_config"]["effort"] == "high"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
class TestPage:
    """The picture, the text and the label of one page."""

    def test_the_png_decodes_to_the_pixels_it_was_given(self) -> None:
        """Checked with an independent decoder."""
        from PIL import Image

        pixels = np.zeros((3, 4, 3), dtype=np.uint8)
        pixels[1, 2] = (255, 128, 0)
        decoded = np.asarray(Image.open(io.BytesIO(encode_png(pixels))).convert("RGB"))
        assert np.array_equal(decoded, pixels)

    def test_only_rgb_is_encoded(self) -> None:
        """A greyscale or RGBA array would be written with the wrong header."""
        with pytest.raises(ValueError, match="RGB"):
            encode_png(np.zeros((2, 2), dtype=np.uint8))
        with pytest.raises(ValueError, match="RGB"):
            encode_png(np.zeros((2, 2, 4), dtype=np.uint8))

    def test_a_labelled_page_brings_its_label(self, drawings: Path) -> None:
        """As canonical JSON, the form the label is attached in."""
        page = load_page(drawings / "labelled.pdf", 1, dpi=20)
        assert page.png.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.label is not None
        assert json.loads(page.label)["sheet"]["id"] == "A-101"
        assert page.text == page_text(drawings / "labelled.pdf", 0)

    def test_an_unlabelled_page_has_none(self, drawings: Path) -> None:
        """So the labelled condition can refuse it."""
        assert load_page(drawings / "plain.pdf", 1, dpi=20).label is None

    def test_a_missing_document_says_how_to_make_it(self, tmp_path: Path) -> None:
        """The samples are generated, not committed."""
        with pytest.raises(FileNotFoundError, match="samples build"):
            load_page(tmp_path / "absent.pdf", 1)

    def test_text_from_a_page_that_is_not_there(self, drawings: Path) -> None:
        """Names how many pages there are."""
        with pytest.raises(IndexError, match="2 page"):
            page_text(drawings / "plain.pdf", 5)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------
class TestRun:
    """Asking, caching, scoring."""

    def test_a_run_asks_scores_and_caches(self, tmp_path: Path, drawings: Path) -> None:
        """The first run sends every question; the second sends none."""
        fake = FakeTransport()
        settings = config(tmp_path, drawings)
        first = run(QUESTIONS, settings, transport=fake, clock=ticking())
        assert len(fake.requests) == len(QUESTIONS)
        assert [record.correct for record in first] == [True, False, False]
        assert first[0].latency_s == 2.0
        assert not any(record.cached for record in first)
        assert first[0].cost_usd == cost("claude-opus-5", first[0].usage)

        second = run(QUESTIONS, settings, transport=refuse)
        assert all(record.cached for record in second)
        assert [record.correct for record in second] == [record.correct for record in first]
        assert pending(QUESTIONS, settings) == []

    def test_each_page_is_prepared_once(self, tmp_path: Path, drawings: Path) -> None:
        """Every question about a sheet sends the identical picture."""
        fake = FakeTransport()
        run(QUESTIONS, config(tmp_path, drawings), transport=fake)
        pictures = {
            request["messages"][0]["content"][0]["source"]["data"]  # type: ignore[index]
            for request in fake.requests
        }
        assert len(pictures) == 1

    def test_n_limits_the_questions_in_order(self, tmp_path: Path, drawings: Path) -> None:
        """The first ``n``, so two runs of ``--n 2`` ask the same two."""
        records = run(QUESTIONS, config(tmp_path, drawings, n=2), transport=FakeTransport())
        assert [record.id for record in records] == ["A-101-01", "A-101-02"]

    def test_the_cache_key_separates_conditions_models_and_prompts(
        self, tmp_path: Path, drawings: Path
    ) -> None:
        """A changed question, condition or model never reads another's answer."""
        base = config(tmp_path, drawings)
        request: dict[str, object] = {"model": "m", "messages": []}
        paths = {
            cache_path(base, question(), request),
            cache_path(config(tmp_path, drawings, condition="plain"), question(), request),
            cache_path(config(tmp_path, drawings, model="other"), question(), request),
            cache_path(base, question(), {**request, "system": "changed"}),
        }
        assert len(paths) == 4
        assert all(path.is_relative_to(tmp_path / "cache") for path in paths)

    def test_the_api_client_is_built_only_when_needed(
        self, tmp_path: Path, drawings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fully cached run needs no credentials; an uncached one builds the client."""
        built: list[FakeTransport] = []

        def factory() -> FakeTransport:
            built.append(FakeTransport())
            return built[-1]

        monkeypatch.setattr(runner_module, "anthropic_transport", factory)
        settings = config(tmp_path, drawings, condition="plain")
        run(QUESTIONS, settings)
        assert len(built) == 1
        run(QUESTIONS, settings)
        assert len(built) == 1

    def test_the_log_round_trips(self, tmp_path: Path, drawings: Path) -> None:
        """A list answer comes back as the tuple it went in as."""
        records = run(QUESTIONS, config(tmp_path, drawings), transport=FakeTransport())
        log = tmp_path / "results" / "run.jsonl"
        write_records(records, log)
        assert read_records(log) == records

    @pytest.mark.parametrize(
        ("text", "stop_reason", "answer"),
        [
            ('{"answer": "IfcWall"}', "end_turn", "IfcWall"),
            ('{"answer": 4}', "end_turn", "4"),
            ("IfcWall", "end_turn", "IfcWall"),
            ('["a"]', "end_turn", '["a"]'),
            ("", "end_turn", None),
            ('{"answer": "IfcWall"}', "refusal", None),
            ('{"answer": "IfcW', "max_tokens", None),
        ],
    )
    def test_the_answer_is_pulled_out_of_the_reply(
        self, text: str, stop_reason: str, answer: str | None
    ) -> None:
        """Structured when it can be, as said when it cannot, nothing when cut off."""
        assert answer_from(Reply(text=text, stop_reason=stop_reason, model="m")) == answer


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
class TestApiKey:
    """The key comes from the environment or ``.env``, and from nowhere else."""

    def test_the_environment_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exported key is the one the user meant."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
        assert load_api_key(tmp_path / ".env") == "from-env"

    def test_dot_env_is_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With ``export``, quotes and comments, as such files are written."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text(
            "# comment\nOTHER=1\nexport ANTHROPIC_API_KEY='from-file'\n", encoding="utf-8"
        )
        assert load_api_key(env) == "from-file"

    def test_no_key_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """So the SDK can resolve a profile instead."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert load_api_key(tmp_path / ".env") is None
        (tmp_path / ".env").write_text("OTHER=1\nANTHROPIC_API_KEY=\n", encoding="utf-8")
        assert load_api_key(tmp_path / ".env") is None

    def test_no_key_is_committed(self) -> None:
        """The rule the gate names: no API key in the repository."""
        root = Path(__file__).parent.parent
        assert not (root / ".env").is_file() or ".env" in (root / ".gitignore").read_text("utf-8")


# ---------------------------------------------------------------------------
# The real SDK, against a mock HTTP transport
# ---------------------------------------------------------------------------
needs_sdk = pytest.mark.skipif(
    importlib.util.find_spec("anthropic") is None, reason="the bench extra is not installed"
)


@needs_sdk
class TestAnthropicTransport:
    """The SDK accepts the requests as built, and its errors become BenchError."""

    @staticmethod
    def _mock(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> list[Any]:
        """Point the SDK at an in-process HTTP handler that answers ``status``.

        Args:
            monkeypatch: pytest's monkeypatch.
            status: The HTTP status to answer.
            body: The JSON body to answer with.

        Returns:
            The list the handler appends each request's decoded body to.
        """
        import anthropic
        import httpx2

        seen: list[Any] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(json.loads(request.content))
            return httpx2.Response(status, headers={"request-id": "req_1"}, json=body)

        real = anthropic.Anthropic

        def build(**options: Any) -> anthropic.Anthropic:  # noqa: ANN401
            client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
            return real(**options, http_client=client)

        monkeypatch.setattr(anthropic, "Anthropic", build)
        monkeypatch.setattr(runner_module, "MAX_RETRIES", 0)
        return seen

    def test_a_request_goes_through_the_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both conditions' requests serialise, and the reply keeps what is needed."""
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": '{"answer": "8000"}'},
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 12,
                "output_tokens": 34,
                "cache_creation_input_tokens": 1500,
                "cache_read_input_tokens": None,
            },
        }
        seen = self._mock(monkeypatch, 200, message)
        send = anthropic_transport(api_key="sk-test")
        for condition in ("plain", "labelled"):
            reply = send(
                build_request(question(), PAGE, condition=condition, model="claude-opus-5")
            )
            assert reply.text == '{"answer": "8000"}'
            assert reply.usage == Usage(12, 34, 1500, 0)
            assert reply.request_id == "req_1"
        assert "tools" not in seen[0]
        assert seen[1]["messages"][2]["content"][0]["type"] == "tool_result"

    @pytest.mark.parametrize(("status", "message"), [(401, "credentials"), (400, "answered 400")])
    def test_an_api_error_is_a_bench_error(
        self, monkeypatch: pytest.MonkeyPatch, status: int, message: str
    ) -> None:
        """With what to do about it, and the cause chained."""
        error = {"type": "error", "error": {"type": "invalid_request_error", "message": "no"}}
        self._mock(monkeypatch, status, error)
        send = anthropic_transport(api_key="sk-test")
        with pytest.raises(BenchError, match=message):
            send(build_request(question(), PAGE, condition="plain", model="claude-opus-5"))

    def test_a_key_is_taken_from_dot_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When none is passed or exported."""
        import anthropic

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        captured: dict[str, Any] = {}

        def build(**options: Any) -> object:  # noqa: ANN401
            captured.update(options)
            return object()

        monkeypatch.setattr(anthropic, "Anthropic", build)
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-file\n", encoding="utf-8")
        anthropic_transport(env_file=tmp_path / ".env")
        assert captured["api_key"] == "sk-file"


def test_without_the_extra_the_message_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pip install 'plannotation[bench]'`` is the fix, so it is what is said."""

    def missing(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(BenchError, match=r"plannotation\[bench\]"):
        anthropic_transport()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def record(
    question_id: str,
    category: str,
    *,
    correct: bool,
    **overrides: Any,  # noqa: ANN401
) -> Record:
    """Build a record.

    Args:
        question_id: Its question id.
        category: Its category.
        correct: Whether it was answered right.
        **overrides: Other fields to change.

    Returns:
        The record.
    """
    fields: dict[str, Any] = {
        "id": question_id,
        "sheet": "A-101",
        "category": category,
        "condition": "plain",
        "model": "claude-opus-5",
        "expected": 8000.0,
        "given": "8000" if correct else "7000",
        "correct": correct,
        "requires_plannotation": False,
        "stop_reason": "end_turn",
        "usage": Usage(100, 10, 0, 1000),
        "cost_usd": 0.01,
        "latency_s": 1.5,
        "cached": False,
    }
    return Record(**{**fields, **overrides})


PLAIN = [
    record("A-101-01", "dimension", correct=False),
    record("A-101-02", "grid", correct=True, expected=("1", "A"), given="1 | A"),
    record(
        "A-101-03",
        "model",
        correct=False,
        expected="0abc",
        given=None,
        requires_plannotation=True,
        stop_reason="refusal",
    ),
    record("A-101-04", "zeta", correct=True, given="x" * 200),
]
PLANNOTATED = [
    record(
        r.id,
        r.category,
        correct=True,
        condition="labelled",
        requires_plannotation=r.requires_plannotation,
    )
    for r in PLAIN
]


class TestReport:
    """The table, the costs and every answer."""

    def test_tallies_and_differences(self) -> None:
        """Percentages, and percentage points between them."""
        assert str(Tally(3, 4)) == "3/4 (75%)"
        assert str(Tally(0, 0)) == "—"
        assert delta(Tally(1, 4), Tally(3, 4)) == "+50 pp"
        assert delta(Tally(0, 0), Tally(3, 4)) == "—"

    def test_categories_come_in_report_order_then_by_name(self) -> None:
        """Known categories first, so reports compare line by line."""
        assert categories(PLAIN) == ["dimension", "grid", "model", "zeta"]

    def test_the_report_holds_the_table_the_costs_and_the_answers(self) -> None:
        """Every number in it can be checked against what the model said."""
        text = render_report("claude-opus-5", "2026-09-22", PLAIN, PLANNOTATED)
        assert text.startswith("# Plannotation benchmark — `claude-opus-5`, 2026-09-22\n")
        assert "| model (label only) | 1 | 0/1 (0%) | 1/1 (100%) | +100 pp |" in text
        assert "| **Answerable from the drawing** | **3** | **2/3 (67%)** | **3/3 (100%)**" in text
        assert "| **All questions** | **4** | **2/4 (50%)** | **4/4 (100%)** | **+50 pp** |" in text
        assert "| `plain` | 4 | 400 | 0 | 4,000 | 40 | 0.04 | 1.5 |" in text
        assert "| A-101-03 | model | 0abc | ✗ _no answer_ | ✓ 8000 |" in text
        assert "✓ 1 \\| A" in text
        assert "x" * 77 + "..." in text
        assert f"Prompt version {PROMPT_VERSION}" in text

    def test_one_condition_alone_still_reports(self) -> None:
        """With dashes where the other would be."""
        unpriced = [record("A-101-01", "dimension", correct=True, cost_usd=None, expected=8000.5)]
        text = render_report("m", "2026-09-22", unpriced, [])
        assert "| `labelled` | 0 | — | — | — | — | — | — |" in text
        assert "| `plain` | 1 | 100 | 0 | 1,000 | 10 | — | 1.5 |" in text
        assert "| A-101-01 | dimension | 8000.5 | ✓ 8000 | — |" in text

    def test_the_readme_section_is_rewritten_between_its_markers(self, tmp_path: Path) -> None:
        """And only there; a second identical write changes nothing."""
        readme = tmp_path / "README.md"
        readme.write_text(f"# x\n\n{README_START}\nold\n{README_END}\n\ntail\n", encoding="utf-8")
        section = render_readme_section("claude-opus-5", "bench/results/r.md", PLAIN, PLANNOTATED)
        assert update_readme(readme, section) is True
        text = readme.read_text("utf-8")
        assert "old" not in text
        assert text.endswith(f"{README_END}\n\ntail\n")
        assert "| Answerable from the drawing | 2/3 (67%) | 3/3 (100%) | +33 pp |" in text
        assert "| Label only (IFC GlobalId) | 0/1 (0%) | 1/1 (100%) | +100 pp |" in text
        assert "[bench/results/r.md](bench/results/r.md)" in text
        assert update_readme(readme, section) is False

    def test_a_readme_without_markers_is_refused(self, tmp_path: Path) -> None:
        """Rather than appended to, which would duplicate the table on every run."""
        readme = tmp_path / "README.md"
        readme.write_text("# x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="BENCHMARK:START"):
            update_readme(readme, "table")


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
class TestCli:
    """``plannotation-bench``: questions, run, report, readme."""

    @staticmethod
    def _invoke(*args: str) -> Any:  # noqa: ANN401
        """Run the command line.

        Args:
            *args: Its arguments.

        Returns:
            The result.
        """
        return CliRunner().invoke(app, list(args))

    def test_the_whole_loop(
        self, tmp_path: Path, drawings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Questions, both runs, the report and the README, with a fake API."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "labelled.pdf").write_bytes((drawings / "labelled.pdf").read_bytes())
        write_questions(list(QUESTIONS), tmp_path / "bench" / "questions.jsonl")
        monkeypatch.setattr(runner_module, "anthropic_transport", FakeTransport)

        dry = self._invoke("run", "--condition", "plain", "--dry-run", "--n", "2")
        assert dry.exit_code == 0
        assert "2 questions, 0 cached, 2 would be sent" in dry.stdout

        for condition in ("plain", "labelled"):
            result = self._invoke("-v", "run", "--condition", condition)
            assert result.exit_code == 0, result.output
            assert "1/3" in result.stdout
        again = self._invoke("run", "--condition", "plain")
        assert "0 asked, 3 from cache" in again.stdout

        report = self._invoke("report", "--date", "2026-09-22")
        assert report.exit_code == 0
        written = tmp_path / "bench" / "results" / "2026-09-22-claude-opus-5.md"
        assert "| **All questions** | **3** |" in written.read_text("utf-8")

        (tmp_path / "README.md").write_text(f"{README_START}\n{README_END}\n", encoding="utf-8")
        readme = self._invoke("readme", "--date", "2026-09-22")
        assert readme.exit_code == 0
        assert "bench/results/2026-09-22-claude-opus-5.md" in (tmp_path / "README.md").read_text(
            "utf-8"
        )

    def test_the_question_set_is_merged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """From wherever the samples were built."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "samples" / "a").mkdir(parents=True)
        (tmp_path / "samples" / "a" / "groundtruth.jsonl").write_text(
            '{"sheet": "A-1", "category": "count", "question": "q", "answer": 1}\n',
            encoding="utf-8",
        )
        result = self._invoke("questions")
        assert result.exit_code == 0
        assert load_questions(tmp_path / "bench" / "questions.jsonl")[0].id == "A-1-01"
        assert self._invoke("questions", "--samples", "nowhere").exit_code == 2

    @pytest.mark.parametrize(
        "args",
        [
            ("run", "--condition", "sideways"),
            ("run", "--condition", "plain", "--questions", "absent.jsonl"),
            ("report",),
            ("readme",),
        ],
    )
    def test_what_cannot_run_exits_two(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
    ) -> None:
        """With a message on standard error saying why."""
        monkeypatch.chdir(tmp_path)
        assert self._invoke(*args).exit_code == 2

    def test_a_readme_without_markers_exits_two(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The runs exist; there is just nowhere to write the table."""
        monkeypatch.chdir(tmp_path)
        write_records(PLAIN, tmp_path / "bench" / "results" / "claude-opus-5-plain.jsonl")
        (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
        assert self._invoke("readme").exit_code == 2

    def test_an_api_failure_part_way_exits_two(
        self, tmp_path: Path, drawings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What was answered before it stays cached."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "labelled.pdf").write_bytes((drawings / "labelled.pdf").read_bytes())
        write_questions(list(QUESTIONS), tmp_path / "bench" / "questions.jsonl")

        def failing() -> Callable[[dict[str, object]], Reply]:
            def send(_request: dict[str, object]) -> Reply:
                msg = "the API answered 529: overloaded"
                raise BenchError(msg)

            return send

        monkeypatch.setattr(runner_module, "anthropic_transport", failing)
        result = self._invoke("run", "--condition", "plain")
        assert result.exit_code == 2
        assert "529" in result.output
