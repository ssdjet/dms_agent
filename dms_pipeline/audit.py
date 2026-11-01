"""Deterministic audit event generation and serialization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .rules import RuleResult, RuleStatus

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuditStatus(str, Enum):
    """Aggregate status for a processed row."""

    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class AuditFinding:
    """A rule identifier and result, intentionally excluding source values."""

    rule_id: str
    status: AuditStatus


@dataclass(frozen=True)
class AuditEvent:
    """Stable audit data sufficient to identify a row and its rule outcomes."""

    source_sha256: str
    source_row_number: int
    status: AuditStatus
    findings: tuple[AuditFinding, ...]


def create_audit_event(
    source_sha256: str,
    source_row_number: int,
    results: Sequence[RuleResult],
) -> AuditEvent:
    """Create one deterministic event without copying record values.

    A row fails if any rule fails. It is not evaluated if no rules ran or at
    least one rule is unevaluated and none failed; otherwise it passes.
    """
    if not _SHA256_PATTERN.fullmatch(source_sha256):
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
    if source_row_number < 1:
        raise ValueError("source_row_number must be at least 1")

    if any(result.status == RuleStatus.FAIL for result in results):
        status = AuditStatus.FAIL
    elif not results or any(
        result.status == RuleStatus.NOT_EVALUATED for result in results
    ):
        status = AuditStatus.NOT_EVALUATED
    else:
        status = AuditStatus.PASS

    return AuditEvent(
        source_sha256=source_sha256,
        source_row_number=source_row_number,
        status=status,
        findings=tuple(
            AuditFinding(result.rule_id, AuditStatus(result.status.value))
            for result in results
        ),
    )


def serialize_audit_event(event: AuditEvent) -> str:
    """Serialize an audit event as stable compact JSON with no source values."""
    payload = {
        "findings": [
            {"rule_id": finding.rule_id, "status": finding.status.value}
            for finding in event.findings
        ],
        "source_row_number": event.source_row_number,
        "source_sha256": event.source_sha256,
        "status": event.status.value,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
