# SPDX-License-Identifier: Apache-2.0
"""Rule 7: the optional veraPDF pass-through.

veraPDF is the reference PDF/A validator. Plannotation never links it -- it is GPL, and
the licence policy admits a GPL tool only as an external CLI -- so this module shells
out to it, parses its JSON report and turns the verdict into one finding.

Detection runs the program
--------------------------
Presence on ``PATH`` is not evidence. The ``verapdf`` command is a shell wrapper around
a Java application: on a machine with no usable JRE it sits on ``PATH``, starts, prints
a stack trace and fails on everything. So :func:`find_verapdf` runs ``verapdf
--version`` and requires it to succeed before the binary is used for anything.

Absence is an error, not a skip
-------------------------------
The test suite skips when veraPDF is missing, because a missing external tool must
never turn CI red. The validator does the opposite and raises
:class:`plannotation.errors.ExternalToolError`, because the caller asked for the pass by
passing ``--verapdf``: a run that quietly omitted a check the caller requested and then
reported no errors would have said something false. The two behaviours are right for
their two callers, and the difference is deliberate.

The flavour is left to the file
-------------------------------
``--flavour 0`` is veraPDF's auto-detection, so the document's own XMP decides what it
is held to. A document that declares no PDF/A flavour is not held to one, which is
correct: Plannotation does not require a plannotated PDF to be a PDF/A, and nothing
here should invent a conformance target the document never claimed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

from plannotation.errors import ExternalToolError
from plannotation.validate.codes import finding

if TYPE_CHECKING:
    from pathlib import Path

    from plannotation.validate.report import Finding

__all__ = [
    "VERAPDF_BINARY_ENV",
    "VERAPDF_TIMEOUT",
    "VeraPdfReport",
    "check_verapdf",
    "find_verapdf",
    "parse_verapdf_report",
    "run_verapdf",
]

#: Environment variable naming a veraPDF that is not on ``PATH``.
VERAPDF_BINARY_ENV: Final = "PLANNOTATION_VERAPDF"

#: How long veraPDF may take on one document, in seconds.
VERAPDF_TIMEOUT: Final = 300.0

#: How long the ``--version`` probe may take, in seconds.
_PROBE_TIMEOUT: Final = 60.0


@dataclass(frozen=True)
class VeraPdfReport:
    """What veraPDF said about one file.

    Attributes:
        compliant: Whether the file satisfies the flavour it was checked against.
        failed_rules: A stable identifier per failing rule.
        passed_checks: How many assertions passed.
        failed_checks: How many failed.
    """

    compliant: bool
    failed_rules: frozenset[str]
    passed_checks: int
    failed_checks: int

    def describe(self) -> str:
        """Summarise the report in one line.

        Returns:
            A human-readable summary, naming the failing rules when there are any.
        """
        head = f"compliant={self.compliant} passed={self.passed_checks} failed={self.failed_checks}"
        if not self.failed_rules:
            return head
        return head + "; rules: " + ", ".join(sorted(self.failed_rules))


def _walk_mappings(node: object) -> list[dict[str, Any]]:
    """Collect every mapping in a nested JSON structure.

    Args:
        node: The parsed JSON.

    Returns:
        Every mapping in the tree, outermost first.
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk_mappings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_mappings(value))
    return found


def parse_verapdf_report(text: str) -> VeraPdfReport:
    """Parse a veraPDF ``--format json`` report.

    The report is walked rather than indexed into, because veraPDF has moved keys
    between releases. Two details matter: the counts must be read from the one object
    that also carries ``isCompliant``, since an identically named key appears on every
    rule summary; and a rule's identity arrives in two shapes, nested under ``ruleId``
    and flat on the summary itself.

    Args:
        text: veraPDF's standard output.

    Returns:
        The parsed report.

    Raises:
        ExternalToolError: If the output is not JSON, or carries no validation result.
            A Java stack trace is what an unusable installation produces, and it is not
            a verdict.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = (
            f"veraPDF did not produce JSON: {text[:200]!r}. The CLI is a wrapper around "
            f"a Java application; this is what it prints with no usable JRE"
        )
        raise ExternalToolError(msg) from exc
    result = next(
        (node for node in _walk_mappings(document) if isinstance(node.get("isCompliant"), bool)),
        None,
    )
    if result is None:
        msg = "veraPDF's report carried no validation result"
        raise ExternalToolError(msg)

    def count(keys: tuple[str, str]) -> int:
        """Read the first of two spellings of a count.

        Args:
            keys: The two key names veraPDF has used for it.

        Returns:
            The count, or 0 when neither key is present.
        """
        return next((int(result[key]) for key in keys if isinstance(result.get(key), int)), 0)

    failed: set[str] = set()
    for node in _walk_mappings(result):
        status = node.get("status") or node.get("ruleStatus")
        if not isinstance(status, str) or status.upper() != "FAILED":
            continue
        source = node["ruleId"] if isinstance(node.get("ruleId"), dict) else node
        clause, test = source.get("clause"), source.get("testNumber")
        if clause is not None and test is not None:
            failed.add(f"{source.get('specification') or '?'} {clause}-{test}")
    return VeraPdfReport(
        compliant=bool(result["isCompliant"]),
        failed_rules=frozenset(failed),
        passed_checks=count(("passedChecks", "testAssertionCountPassed")),
        failed_checks=count(("failedChecks", "testAssertionCountFailed")),
    )


@lru_cache(maxsize=1)
def find_verapdf() -> str | None:
    """Find a veraPDF that can actually run.

    Returns:
        A runnable command, or None. ``PLANNOTATION_VERAPDF`` wins over ``PATH``, and the
        candidate is only returned once ``--version`` has succeeded.
    """
    candidate = os.environ.get(VERAPDF_BINARY_ENV) or shutil.which("verapdf")
    if not candidate:
        return None
    try:
        probe = subprocess.run(  # noqa: S603 - a path from the environment, run with no shell
            [candidate, "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return candidate if probe.returncode == 0 else None


def run_verapdf(binary: str, path: Path) -> VeraPdfReport:
    """Validate one file with veraPDF.

    Args:
        binary: The command :func:`find_verapdf` returned.
        path: The file to validate.

    Returns:
        The parsed report.

    Raises:
        ExternalToolError: If veraPDF cannot be run, times out, or says nothing a
            verdict can be read from.
    """
    try:
        finished = subprocess.run(  # noqa: S603 - a probed binary, run with no shell
            [binary, "--format", "json", "--flavour", "0", "--success", str(path)],
            capture_output=True,
            text=True,
            timeout=VERAPDF_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"veraPDF did not finish within {VERAPDF_TIMEOUT:g} s on {path}. A validator "
            f"bounds the wall-clock time it spends per operation"
        )
        raise ExternalToolError(msg) from exc
    except OSError as exc:
        msg = f"veraPDF could not be run: {exc}"
        raise ExternalToolError(msg) from exc
    return parse_verapdf_report(finished.stdout)


def check_verapdf(path: Path, *, source: str) -> list[Finding]:
    """Run the veraPDF pass over a document and report its verdict.

    Args:
        path: The PDF to check.
        source: Which document it is, for the finding.

    Returns:
        A single finding when veraPDF says the file does not conform, and nothing when
        it says it does.

    Raises:
        ExternalToolError: If veraPDF is absent or cannot run. The caller asked for the
            pass; reporting no errors without having made it would be untrue.
    """
    binary = find_verapdf()
    if binary is None:
        msg = (
            f"--verapdf was requested, but no runnable veraPDF was found. It is not "
            f"installed with Plannotation -- it is a GPL tool this project only ever shells "
            f"out to -- so install it from verapdf.org and put it on PATH, or set "
            f"{VERAPDF_BINARY_ENV} to its path. The probe runs `verapdf --version` and "
            f"requires it to succeed, because the CLI is a Java wrapper that can be "
            f"present and unusable"
        )
        raise ExternalToolError(msg)
    report = run_verapdf(binary, path)
    if report.compliant:
        return []
    return [
        finding(
            "PL-PDF-001",
            message=f"veraPDF reports this document as not conforming: {report.describe()}",
            path="",
            source=source,
        )
    ]
