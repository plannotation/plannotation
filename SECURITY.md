# Security policy

## Reporting a vulnerability

Please do not open a public issue. Report it through
[private vulnerability reporting](https://github.com/plannotation/plannotation/security/advisories/new);
only the maintainers see the report, and the fix is coordinated there.

## Supported versions

Plannotation is at 0.1, a draft, and nothing has been released yet. Fixes land on
`main`.

## Threat model

Plannotations are **data, never executable**. A conforming reader validates a
plannotation against the schema before trusting any value in it, and treats every
PDF and SVG it reads as untrusted input. The security considerations are normative
and live in [section 9 of the specification](spec/SPEC.md#9-security-considerations).
