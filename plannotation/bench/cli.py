# SPDX-License-Identifier: Apache-2.0
"""``plannotation-bench``: build the question set, run a condition, write the report.

::

    plannotation-bench questions                    # merge samples/*/groundtruth.jsonl
    plannotation-bench run --condition plain       --model claude-opus-5 --n 100
    plannotation-bench run --condition plannotated --model claude-opus-5 --n 100
    plannotation-bench report --model claude-opus-5 # -> bench/results/<date>-<model>.md
    plannotation-bench readme --model claude-opus-5 # the table between the README's markers

``run`` reads ``ANTHROPIC_API_KEY`` from the environment or from ``.env``, and needs
it only for questions that are not already in the response cache; ``--dry-run`` says
how many those are without asking any. Exit codes: 0 done, 2 could not run.

Like :mod:`plannotation.cli`, this module is allowed to write to standard output;
everything it calls reports through :mod:`logging`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import typer
from rich.console import Console
from rich.logging import RichHandler

from plannotation.bench.prompt import CONDITIONS, Condition
from plannotation.bench.questions import load_questions, merge_ground_truth, write_questions
from plannotation.bench.report import render_readme_section, render_report, tally, update_readme
from plannotation.bench.runner import RunConfig, pending, read_records, run, write_records
from plannotation.errors import PlannotationError

if TYPE_CHECKING:
    from plannotation.bench.runner import Record

#: The default model: the current Opus.
DEFAULT_MODEL: Final = "claude-opus-5"

QUESTIONS: Final = Path("bench/questions.jsonl")
RESULTS: Final = Path("bench/results")
CACHE: Final = Path("bench/cache")

app = typer.Typer(
    name="plannotation-bench",
    help="Measure what a plannotation is worth to a model reading a drawing.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
errors = Console(stderr=True)


def _fail(message: str) -> typer.Exit:
    """Report why the command cannot go on.

    Args:
        message: What went wrong and what to do about it.

    Returns:
        The exit to raise, with status 2.
    """
    errors.print(f"[red]error[/red] {message}", markup=True, highlight=False)
    return typer.Exit(code=2)


def _log_file(results: Path, model: str, condition: str) -> Path:
    """Return where one run's log is written.

    Args:
        results: The results directory.
        model: The model.
        condition: The condition.

    Returns:
        The log's path.
    """
    return results / f"{model}-{condition}.jsonl"


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log each request.")] = False,
) -> None:
    """Measure what a plannotation is worth to a model reading a drawing."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=errors, show_path=False)],
    )


@app.command("questions")
def questions_command(
    samples: Annotated[
        Path, typer.Option("--samples", help="Where `plannotation samples build` wrote.")
    ] = Path("samples"),
    out: Annotated[Path, typer.Option("--out", "-o", help="The question file.")] = QUESTIONS,
) -> None:
    """Merge every sample's ground truth into the question set.

    Raises:
        typer.Exit: With 2 when there is no ground truth to merge.
    """
    try:
        merged = merge_ground_truth(samples, relative_to=Path.cwd())
    except (FileNotFoundError, ValueError) as error:
        raise _fail(str(error)) from error
    write_questions(merged, out)
    console.print(f"{len(merged)} questions written to {out}")


@app.command("run")
def run_command(
    condition: Annotated[str, typer.Option("--condition", "-c", help="plain or plannotated.")],
    model: Annotated[str, typer.Option("--model", "-m", help="The model id.")] = DEFAULT_MODEL,
    n: Annotated[
        int | None, typer.Option("--n", min=1, help="Ask at most this many questions.")
    ] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="output_config.effort; the model's default if unset."),
    ] = None,
    questions: Annotated[Path, typer.Option("--questions", help="The question file.")] = QUESTIONS,
    cache: Annotated[Path, typer.Option("--cache", help="The response cache.")] = CACHE,
    results: Annotated[Path, typer.Option("--results", help="Where run logs go.")] = RESULTS,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Count what would be asked; ask nothing.")
    ] = False,
) -> None:
    """Ask the questions under one condition and log the scored answers.

    Raises:
        typer.Exit: With 2 when the run cannot start or the API fails part-way.
            Answers already obtained stay cached, so re-running resumes.
    """
    if condition not in CONDITIONS:
        msg = f"--condition must be one of {', '.join(CONDITIONS)}, not {condition!r}"
        raise _fail(msg)
    chosen: Condition = "plain" if condition == "plain" else "plannotated"
    config = RunConfig(condition=chosen, model=model, n=n, effort=effort, cache_dir=cache)
    try:
        question_set = load_questions(questions)
        if dry_run:
            waiting = pending(question_set, config)
            asked = len(question_set) if n is None else min(n, len(question_set))
            console.print(
                f"{chosen} x {model}: {asked} questions, {asked - len(waiting)} cached, "
                f"{len(waiting)} would be sent to the API"
            )
            return
        records = run(question_set, config)
    except (PlannotationError, FileNotFoundError, ValueError) as error:
        raise _fail(str(error)) from error
    log = _log_file(results, model, chosen)
    write_records(records, log)
    fresh = [record for record in records if not record.cached]
    spent = sum(record.cost_usd or 0.0 for record in fresh)
    console.print(
        f"{chosen} x {model}: {tally(records)} correct; "
        f"{len(fresh)} asked, {len(records) - len(fresh)} from cache, "
        f"about ${spent:.2f} spent. Log: {log}"
    )


@app.command("report")
def report_command(
    model: Annotated[str, typer.Option("--model", "-m", help="The model id.")] = DEFAULT_MODEL,
    date: Annotated[
        str | None, typer.Option("--date", help="The report's date; today (UTC) if unset.")
    ] = None,
    results: Annotated[Path, typer.Option("--results", help="Where run logs are.")] = RESULTS,
) -> None:
    """Write the report for a model, bench/results/<date>-<model>.md, from its run logs.

    Raises:
        typer.Exit: With 2 when neither condition has been run.
    """
    plain, plannotated = _load_runs(results, model)
    day = date or datetime.now(UTC).date().isoformat()
    path = results / f"{day}-{model}.md"
    path.write_text(render_report(model, day, plain, plannotated), encoding="utf-8")
    console.print(f"report written to {path}")


@app.command("readme")
def readme_command(
    model: Annotated[str, typer.Option("--model", "-m", help="The model id.")] = DEFAULT_MODEL,
    date: Annotated[
        str | None, typer.Option("--date", help="The report to link; today (UTC) if unset.")
    ] = None,
    results: Annotated[Path, typer.Option("--results", help="Where run logs are.")] = RESULTS,
    readme: Annotated[Path, typer.Option("--readme", help="The README to update.")] = Path(
        "README.md"
    ),
) -> None:
    """Write the benchmark table into the README, between its BENCHMARK markers.

    Raises:
        typer.Exit: With 2 when there is nothing to report or no markers to write between.
    """
    plain, plannotated = _load_runs(results, model)
    day = date or datetime.now(UTC).date().isoformat()
    report = results / f"{day}-{model}.md"
    try:
        link = report.relative_to(readme.parent).as_posix()
    except ValueError:
        link = report.as_posix()
    try:
        changed = update_readme(readme, render_readme_section(model, link, plain, plannotated))
    except ValueError as error:
        raise _fail(str(error)) from error
    console.print(f"{readme} {'updated' if changed else 'already up to date'}")


def _load_runs(results: Path, model: str) -> tuple[list[Record], list[Record]]:
    """Read both conditions' logs for a model.

    Args:
        results: Where the logs are.
        model: The model.

    Returns:
        The plain and plannotated records; either may be empty.

    Raises:
        typer.Exit: With 2 when neither log exists.
    """
    logs = [_log_file(results, model, condition) for condition in CONDITIONS]
    if not any(log.is_file() for log in logs):
        msg = f"no run logs for {model} in {results}; run `plannotation-bench run` first"
        raise _fail(msg)
    plain, plannotated = (read_records(log) if log.is_file() else [] for log in logs)
    return plain, plannotated
