# SPDX-License-Identifier: Apache-2.0
"""Audit installed dependency licences against the PlanLabel licence policy.

The policy is the project's legal position, not a preference, so it is checked
mechanically rather than trusted:

* Permissive licences (MIT, BSD, Apache-2.0, ISC, PSF, Zlib, 0BSD, ...) and
  MPL-2.0 are allowed without comment.
* LGPL is allowed only as an unmodified dependency, and every such dependency
  must be named in :data:`ACKNOWLEDGED_WEAK_COPYLEFT` so that adding a new one is
  a deliberate act rather than an accident.
* GPL is allowed only for tools invoked as external command-line programs, never
  as an installed Python distribution.
* AGPL is forbidden outright, as is PyMuPDF under any licence.

Reads distribution metadata from the environment it runs in, so it audits what is
actually installed rather than what ``pyproject.toml`` hoped for.

Usage:
    python tools/audit_licenses.py                     # report, exit 1 on violation
    python tools/audit_licenses.py --write FILE.md     # also rewrite the report file
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

#: Substrings that mark a licence as acceptable with no further thought.
PERMISSIVE = (
    "mit",
    "bsd",
    "apache",
    "isc",
    "psf",
    "python software foundation",
    "zlib",
    "0bsd",
    "unlicense",
    "cc0",
    "public domain",
    "mpl",
    "mozilla public license",
)

#: Distributions permitted to be weak copyleft, each with the reason.
ACKNOWLEDGED_WEAK_COPYLEFT = {
    "cairosvg": "LGPL-3.0-or-later; unmodified dependency, imported lazily, svg extra only",
    "ifcopenshell": "LGPL-3.0-or-later; unmodified dependency, ifc extra only",
    "shapely": "BSD-3-Clause itself, but vendors GEOS (LGPL-2.1) as a prebuilt library",
}

#: Forbidden outright, whatever the metadata claims.
BANNED_DISTRIBUTIONS = {"pymupdf", "fitz", "pymupdf4llm"}

#: GPL text bundled under the GCC Runtime Library Exception is not contamination.
#: pikepdf and numpy both ship it for libstdc++/libgcc inside musllinux and some
#: manylinux wheels; the exception explicitly permits redistribution with
#: independent works. Recorded here so a future audit does not re-panic.
GCC_RUNTIME_EXCEPTION = {"pikepdf", "numpy"}


class Finding(NamedTuple):
    """One audited distribution."""

    name: str
    version: str
    license_text: str
    verdict: str
    note: str


def _license_of(dist: metadata.Distribution) -> str:
    """Extract the best available licence string for a distribution.

    Args:
        dist: An installed distribution.

    Returns:
        The PEP 639 licence expression if present, else the legacy ``License``
        field, else the licence classifiers, else ``"UNKNOWN"``.
    """
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression).strip()
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return " OR ".join(classifiers)
    legacy = meta.get("License")
    if legacy and len(str(legacy)) < 200:  # noqa: PLR2004 -- some wheels inline the whole text
        return str(legacy).strip()
    return "UNKNOWN"


#: Distributions whose copyleft status has already been considered and accepted,
#: mapped to the verdict and the reason. Built once rather than per call.
_GCC_NOTE = "bundled libstdc++/libgcc under the GCC Runtime Library Exception"

_PRE_JUDGED: dict[str, tuple[str, str]] = {
    **{name: ("allowed", why) for name, why in ACKNOWLEDGED_WEAK_COPYLEFT.items()},
    **dict.fromkeys(GCC_RUNTIME_EXCEPTION, ("ok", _GCC_NOTE)),
}


def classify(name: str, license_text: str) -> tuple[str, str]:
    """Judge one distribution against the policy.

    Args:
        name: Normalised distribution name.
        license_text: Licence expression or classifier text.

    Returns:
        A ``(verdict, note)`` pair. Verdict is one of ``ok`` (plainly fine),
        ``allowed`` (copyleft, accepted deliberately), ``REVIEW`` (a human must
        look) or ``VIOLATION`` (the policy is broken).
    """
    lowered = license_text.lower()

    # Hard bans first, so nothing below can excuse them.
    if name in BANNED_DISTRIBUTIONS or "agpl" in lowered or "affero" in lowered:
        reason = (
            "banned outright by the licence policy"
            if name in BANNED_DISTRIBUTIONS
            else "AGPL is forbidden"
        )
        return "VIOLATION", reason

    if name in _PRE_JUDGED:
        return _PRE_JUDGED[name]

    # An unacknowledged LGPL dependency is not a violation, but adding one must
    # be a deliberate act: it has to be named in ACKNOWLEDGED_WEAK_COPYLEFT.
    if re.search(r"\blgpl\b", lowered):
        return "REVIEW", "LGPL not in the acknowledged list -- add it deliberately or drop it"
    if re.search(r"\bgpl\b", lowered):
        return "VIOLATION", "GPL is permitted only as an external CLI, never as a dependency"
    if any(token in lowered for token in PERMISSIVE):
        return "ok", ""

    return "REVIEW", (
        "no licence metadata -- check the project page by hand"
        if lowered in {"unknown", ""}
        else "licence not recognised by the policy"
    )


def audit() -> list[Finding]:
    """Audit every distribution installed in the current environment.

    Returns:
        Findings sorted by distribution name.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    for dist in metadata.distributions():
        raw = dist.metadata.get("Name")
        if not raw:
            continue
        name = re.sub(r"[-_.]+", "-", str(raw)).lower()
        if name in seen:
            continue
        seen.add(name)
        license_text = _license_of(dist)
        verdict, note = classify(name, license_text)
        findings.append(Finding(name, dist.version, license_text, verdict, note))
    return sorted(findings, key=lambda f: f.name)


def render(findings: list[Finding]) -> str:
    """Render the findings as a Markdown report.

    Args:
        findings: Audited distributions.

    Returns:
        The full Markdown document.
    """
    flagged = [f for f in findings if f.verdict in {"REVIEW", "VIOLATION"}]
    weak = [f for f in findings if f.verdict == "allowed"]
    lines = [
        "# Third-party licences",
        "",
        "Generated by `make licenses`. Do not edit by hand.",
        "",
        "PlanLabel is Apache-2.0. Its dependency policy allows OSI-approved permissive",
        "licences and MPL-2.0; LGPL only as an unmodified dependency; GPL only for tools",
        "invoked as external command-line programs; and AGPL not at all.",
        "",
        f"Audited **{len(findings)}** installed distributions.",
        "",
        "## Weak copyleft, used as unmodified dependencies",
        "",
    ]
    if weak:
        lines += [
            "| Distribution | Version | Licence | Why it is allowed |",
            "| --- | --- | --- | --- |",
        ]
        lines += [f"| `{f.name}` | {f.version} | {f.license_text} | {f.note} |" for f in weak]
    else:
        lines.append("_None installed._")
    lines += ["", "## Needing review", ""]
    if flagged:
        lines += [
            "| Distribution | Version | Licence | Verdict | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{f.name}` | {f.version} | {f.license_text} | **{f.verdict}** | {f.note} |"
            for f in flagged
        ]
    else:
        lines.append("_None. Every installed distribution satisfies the policy._")
    lines += [
        "",
        "## Full inventory",
        "",
        "| Distribution | Version | Licence |",
        "| --- | --- | --- |",
    ]
    lines += [f"| `{f.name}` | {f.version} | {f.license_text} |" for f in findings]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the audit.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when the policy holds, 1 when anything is flagged.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", metavar="FILE", help="write the Markdown report to FILE")
    args = parser.parse_args(argv)

    findings = audit()
    flagged = [f for f in findings if f.verdict in {"REVIEW", "VIOLATION"}]

    for f in findings:
        if f.verdict in {"REVIEW", "VIOLATION", "allowed"}:
            print(f"{f.verdict:>9}  {f.name} {f.version} -- {f.license_text}   {f.note}")

    if args.write:
        Path(args.write).write_text(render(findings), encoding="utf-8")
        print(f"\nwrote {args.write}")

    print(f"\n{len(findings)} distributions audited, {len(flagged)} flagged.")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
