# SPDX-License-Identifier: Apache-2.0
"""Run one condition against one model, answering from the cache where it can.

A response is cached under the condition, the model, the question and everything
else that shapes the request, so a second run of the same command makes no API call
at all and reproduces the first one's table exactly. Only a question with no cached
response needs the network; the API client is built on the first such question, so
a fully cached run needs neither credentials nor the ``bench`` extra.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from planlabel.bench.page import PageInput, load_page
from planlabel.bench.pricing import cost
from planlabel.bench.prompt import PROMPT_VERSION, Condition, build_request
from planlabel.bench.score import score
from planlabel.errors import BenchError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from planlabel.bench.questions import Answer, Question

logger = logging.getLogger(__name__)

#: Retries the SDK makes on a rate limit, an overload or a dropped connection.
MAX_RETRIES: Final = 4

#: The only variable read from ``.env``.
API_KEY_VARIABLE: Final = "ANTHROPIC_API_KEY"

#: Stop reasons after which the response holds no complete answer.
_NO_ANSWER: Final = frozenset({"refusal", "max_tokens"})


@dataclass(frozen=True)
class Usage:
    """Token counts for one request, as the API reports them.

    Attributes:
        input_tokens: Input tokens neither written to nor read from the cache.
        output_tokens: Output tokens, thinking included.
        cache_creation_input_tokens: Input tokens written to the prompt cache.
        cache_read_input_tokens: Input tokens served from the prompt cache.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class Reply:
    """What the benchmark keeps of one response.

    Attributes:
        text: The response's text blocks, joined.
        stop_reason: Why generation stopped.
        model: The model that answered.
        usage: Its token counts.
        request_id: The API's request id, for reporting a problem upstream.
    """

    text: str
    stop_reason: str | None
    model: str
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None

    def to_json(self) -> dict[str, object]:
        """Return the reply as the cache stores it.

        Returns:
            A JSON-ready mapping.
        """
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Reply:
        """Load a cached reply.

        Args:
            data: What :meth:`to_json` wrote.

        Returns:
            The reply.
        """
        return cls(
            text=str(data["text"]),
            stop_reason=data.get("stop_reason"),
            model=str(data["model"]),
            usage=Usage(**data.get("usage", {})),
            request_id=data.get("request_id"),
        )


@dataclass(frozen=True)
class RunConfig:
    """What to run.

    Attributes:
        condition: ``plain`` or ``labelled``.
        model: The model id.
        n: Ask at most this many questions, in file order; None asks them all.
        effort: An ``output_config.effort`` level, or None for the model's default.
        cache_dir: Where responses are cached.
        base_dir: What a question's document path is relative to.
    """

    condition: Condition
    model: str
    n: int | None = None
    effort: str | None = None
    cache_dir: Path = Path("bench/cache")
    base_dir: Path = Path()


@dataclass(frozen=True)
class Record:
    """One question, answered and scored.

    Attributes:
        id: The question's id.
        sheet: The sheet it is about.
        category: What kind of fact it asks for.
        condition: The condition it ran under.
        model: The model asked.
        expected: The key.
        given: The model's answer, or None when it gave none.
        correct: Whether the answer matched the key.
        requires_label: Whether only the label carries the answer.
        stop_reason: Why generation stopped.
        usage: The request's token counts.
        cost_usd: Its estimated cost, or None when the model's price is unknown.
        latency_s: Wall-clock seconds the request took.
        cached: Whether the response came from the cache on this run.
    """

    id: str
    sheet: str
    category: str
    condition: str
    model: str
    expected: Answer
    given: str | None
    correct: bool
    requires_label: bool
    stop_reason: str | None
    usage: Usage
    cost_usd: float | None
    latency_s: float
    cached: bool

    def to_json(self) -> dict[str, object]:
        """Return the record as one line of a run log holds it.

        Returns:
            A JSON-ready mapping.
        """
        data = asdict(self)
        if isinstance(self.expected, tuple):
            data["expected"] = list(self.expected)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Record:
        """Load one line of a run log.

        Args:
            data: What :meth:`to_json` wrote.

        Returns:
            The record.
        """
        expected = data["expected"]
        return cls(
            **{
                **data,
                "expected": tuple(expected) if isinstance(expected, list) else expected,
                "usage": Usage(**data["usage"]),
            }
        )


def run(
    questions: Sequence[Question],
    config: RunConfig,
    *,
    transport: Callable[[dict[str, object]], Reply] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> list[Record]:
    """Ask every question under one condition, and score the answers.

    Args:
        questions: The question set.
        config: The condition, model and paths.
        transport: Sends a request. When None, :func:`anthropic_transport` is built
            the first time a question is not in the cache.
        clock: Measures latency; injectable so tests are deterministic.

    Returns:
        One record per question asked, in question order.
    """
    records: list[Record] = []
    sender = transport
    for question, request in requests(questions, config):
        path = cache_path(config, question, request)
        cached = _read_cache(path)
        if cached is None:
            if sender is None:
                sender = anthropic_transport()
            started = clock()
            reply = sender(request)
            latency = clock() - started
            _write_cache(path, reply, latency)
            logger.info("%s %s asked in %.1f s", config.condition, question.id, latency)
        else:
            reply, latency = cached
        records.append(_record(question, config, reply, latency, cached=cached is not None))
    return records


def pending(questions: Sequence[Question], config: RunConfig) -> list[str]:
    """Return the questions a run would send to the API, because none is cached.

    Args:
        questions: The question set.
        config: The run.

    Returns:
        Their ids, in question order.
    """
    return [
        question.id
        for question, request in requests(questions, config)
        if not cache_path(config, question, request).is_file()
    ]


def requests(
    questions: Sequence[Question], config: RunConfig
) -> Iterator[tuple[Question, dict[str, object]]]:
    """Build the request for every question a run asks, preparing each page once.

    Args:
        questions: The question set.
        config: The run; ``n`` limits how many questions are taken, in order.

    Yields:
        Each question and the request that asks it.
    """
    selected = questions if config.n is None else questions[: config.n]
    pages: dict[tuple[str, int], PageInput] = {}
    for question in selected:
        location = (question.document, question.page)
        if location not in pages:
            pages[location] = load_page(config.base_dir / question.document, question.page)
        yield (
            question,
            build_request(
                question,
                pages[location],
                condition=config.condition,
                model=config.model,
                effort=config.effort,
            ),
        )


def cache_path(config: RunConfig, question: Question, request: dict[str, object]) -> Path:
    """Return where one response is cached.

    The key is the condition, the model and the question -- and, so that a changed
    prompt or a rebuilt sample never serves a stale answer, a digest of the whole
    request that was sent.

    Args:
        config: The run.
        question: The question.
        request: The request built for it.

    Returns:
        The cache file's path.
    """
    key = {
        "condition": config.condition,
        "model": config.model,
        "question": question.id,
        "prompt": PROMPT_VERSION,
        "request": hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    model = re.sub(r"[^A-Za-z0-9._-]", "_", config.model)
    return config.cache_dir / config.condition / model / f"{question.id}-{digest}.json"


def _read_cache(path: Path) -> tuple[Reply, float] | None:
    """Read a cached response, if there is one.

    Args:
        path: The cache file.

    Returns:
        The reply and the latency it was obtained in, or None.
    """
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Reply.from_json(data["reply"]), float(data["latency_s"])


def _write_cache(path: Path, reply: Reply, latency: float) -> None:
    """Cache a response.

    Args:
        path: The cache file.
        reply: The reply.
        latency: How long it took.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reply": reply.to_json(), "latency_s": round(latency, 3)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def answer_from(reply: Reply) -> str | None:
    """Pull the answer out of a reply.

    The request asks for ``{"answer": "..."}``; text that is not that JSON is taken
    as the answer as it stands, so a model that ignores the format is scored on what
    it said rather than failed on how.

    Args:
        reply: The reply.

    Returns:
        The answer, or None when the response holds none.
    """
    if reply.stop_reason in _NO_ANSWER:
        return None
    text = reply.text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text or None
    if isinstance(data, dict) and isinstance(data.get("answer"), str | int | float):
        return str(data["answer"])
    return text or None


def _record(
    question: Question, config: RunConfig, reply: Reply, latency: float, *, cached: bool
) -> Record:
    """Score one reply.

    Args:
        question: The question.
        config: The run.
        reply: The reply.
        latency: How long it took.
        cached: Whether it came from the cache.

    Returns:
        The record.
    """
    given = answer_from(reply)
    return Record(
        id=question.id,
        sheet=question.sheet,
        category=question.category,
        condition=config.condition,
        model=config.model,
        expected=question.answer,
        given=given,
        correct=score(question.answer, given, unit=question.unit),
        requires_label=question.requires_label,
        stop_reason=reply.stop_reason,
        usage=reply.usage,
        cost_usd=cost(config.model, reply.usage),
        latency_s=round(latency, 3),
        cached=cached,
    )


def write_records(records: Sequence[Record], path: Path) -> None:
    """Write a run log, one record per line.

    Args:
        records: The records.
        path: Where to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record.to_json(), sort_keys=True) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_records(path: Path) -> list[Record]:
    """Read a run log.

    Args:
        path: What :func:`write_records` wrote.

    Returns:
        The records.
    """
    return [
        Record.from_json(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_api_key(env_file: Path) -> str | None:
    """Find the API key: the environment first, then ``.env``.

    Only ``ANTHROPIC_API_KEY`` is read from the file, and nothing is exported, so the
    key reaches the client and nowhere else.

    Args:
        env_file: The ``.env`` file to fall back on.

    Returns:
        The key, or None -- in which case the SDK resolves credentials itself, from an
        ``ant auth login`` profile for instance.
    """
    key = os.environ.get(API_KEY_VARIABLE)
    if key:
        return key
    if not env_file.is_file():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == API_KEY_VARIABLE:
            return value.strip().strip("'\"") or None
    return None


def anthropic_transport(
    *, api_key: str | None = None, env_file: Path = Path(".env")
) -> Callable[[dict[str, object]], Reply]:
    """Build the transport that calls the Claude API.

    Args:
        api_key: The key to use. When None, :func:`load_api_key` looks for one.
        env_file: The ``.env`` file :func:`load_api_key` falls back on.

    Returns:
        A function that sends one request and returns its reply.

    Raises:
        BenchError: If the ``bench`` extra is not installed.
    """
    try:
        anthropic = importlib.import_module("anthropic")
    except ImportError as error:
        msg = "the benchmark calls the Claude API: install it with pip install 'planlabel[bench]'"
        raise BenchError(msg) from error
    key = api_key or load_api_key(env_file)
    options: dict[str, Any] = {"max_retries": MAX_RETRIES}
    if key:
        options["api_key"] = key
    try:
        client = anthropic.Anthropic(**options)
    except anthropic.AnthropicError as error:
        msg = f"could not set up the API client: {error}; put {API_KEY_VARIABLE} in .env"
        raise BenchError(msg) from error

    def send(request: dict[str, object]) -> Reply:
        try:
            message = client.messages.create(**request)
        except anthropic.AuthenticationError as error:
            msg = f"the API rejected the credentials; check {API_KEY_VARIABLE} in .env"
            raise BenchError(msg) from error
        except anthropic.APIStatusError as error:
            msg = f"the API answered {error.status_code}: {error.message}"
            raise BenchError(msg) from error
        except anthropic.APIConnectionError as error:
            msg = f"could not reach the API: {error}"
            raise BenchError(msg) from error
        except anthropic.AnthropicError as error:
            msg = f"the request failed: {error}"
            raise BenchError(msg) from error
        return reply_from_message(message)

    return send


def reply_from_message(message: object) -> Reply:
    """Keep what the benchmark needs of an SDK ``Message``.

    Args:
        message: The SDK's response object.

    Returns:
        The reply.
    """
    blocks = getattr(message, "content", None) or []
    text = "".join(str(block.text) for block in blocks if getattr(block, "type", None) == "text")
    usage = getattr(message, "usage", None)

    def count(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    stop_reason = getattr(message, "stop_reason", None)
    request_id = getattr(message, "_request_id", None)
    return Reply(
        text=text,
        stop_reason=None if stop_reason is None else str(stop_reason),
        model=str(getattr(message, "model", "")),
        usage=Usage(
            input_tokens=count("input_tokens"),
            output_tokens=count("output_tokens"),
            cache_creation_input_tokens=count("cache_creation_input_tokens"),
            cache_read_input_tokens=count("cache_read_input_tokens"),
        ),
        request_id=None if request_id is None else str(request_id),
    )
