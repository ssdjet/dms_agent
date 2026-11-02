"""Composable, policy-neutral validation rules for mapped records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from .ingestion import CellValue

if TYPE_CHECKING:
    from .checks import ValidationCheck


class RuleStatus(str, Enum):
    """Outcome of evaluating one configured rule."""

    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class RuleResult:
    """Privacy-conscious result containing no source field values."""

    rule_id: str
    status: RuleStatus
    message: str


class Rule(Protocol):
    """Contract implemented by deterministic record-level rules."""

    rule_id: str

    def evaluate(self, record: Mapping[str, CellValue]) -> RuleResult:
        """Evaluate a record and return a stable result."""


@dataclass(frozen=True)
class PresenceRule:
    """Check that a configured field exists and contains a non-empty value."""

    rule_id: str
    field_name: str

    def evaluate(self, record: Mapping[str, CellValue]) -> RuleResult:
        if self.field_name not in record:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is missing from the mapped record",
            )
        value = record[self.field_name]
        if value is None or (isinstance(value, str) and not value.strip()):
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is empty",
            )
        return RuleResult(self.rule_id, RuleStatus.PASS, "configured field is present")


@dataclass(frozen=True)
class AllowedValuesRule:
    """Check a field against an explicitly supplied approved value set."""

    rule_id: str
    field_name: str
    allowed_values: frozenset[str] | None

    def evaluate(self, record: Mapping[str, CellValue]) -> RuleResult:
        if not self.allowed_values:
            return RuleResult(
                self.rule_id,
                RuleStatus.NOT_EVALUATED,
                "no reference values are configured",
            )
        if self.field_name not in record:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is missing from the mapped record",
            )
        value = record[self.field_name]
        if not isinstance(value, str):
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is not text",
            )
        if value not in self.allowed_values:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "value is not in the configured reference set",
            )
        return RuleResult(
            self.rule_id,
            RuleStatus.PASS,
            "value is in the configured reference set",
        )


@dataclass(frozen=True)
class NumericValueRule:
    """Check that a configured value is numeric, without imposing a limit."""

    rule_id: str
    field_name: str

    def evaluate(self, record: Mapping[str, CellValue]) -> RuleResult:
        value = record.get(self.field_name)
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is not numeric",
            )
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is not a finite number",
            )
        if not number.is_finite():
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is not a finite number",
            )
        return RuleResult(self.rule_id, RuleStatus.PASS, "configured field is numeric")


@dataclass(frozen=True)
class MaximumValueRule:
    """Check a numeric field against an explicitly configured maximum."""

    rule_id: str
    field_name: str
    maximum: Decimal | int | float | None

    def evaluate(self, record: Mapping[str, CellValue]) -> RuleResult:
        if self.maximum is None:
            return RuleResult(
                self.rule_id,
                RuleStatus.NOT_EVALUATED,
                "no maximum is configured",
            )
        if self.field_name not in record:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is missing from the mapped record",
            )
        value = record[self.field_name]
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured field is not numeric",
            )
        try:
            amount = Decimal(str(value))
            maximum = Decimal(str(self.maximum))
        except InvalidOperation:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured value or maximum is not numeric",
            )
        if not amount.is_finite() or not maximum.is_finite():
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "configured value or maximum is not finite",
            )
        if amount > maximum:
            return RuleResult(
                self.rule_id,
                RuleStatus.FAIL,
                "value exceeds the configured maximum",
            )
        return RuleResult(
            self.rule_id,
            RuleStatus.PASS,
            "value is within the configured maximum",
        )


@dataclass(frozen=True)
class RuleEngine:
    """Apply configured rules in order and preserve deterministic result ordering."""

    rules: tuple[Rule, ...]
    checks: tuple[ValidationCheck, ...] = ()

    def evaluate(self, record: Mapping[str, CellValue]) -> tuple[RuleResult, ...]:
        """Evaluate row-level rules; dataset checks require ``evaluate_records``."""
        return (
            *(rule.evaluate(record) for rule in self.rules),
            *(check.evaluate_record(record) for check in self.checks),
        )

    def evaluate_records(
        self,
        records: tuple[Mapping[str, CellValue], ...],
    ) -> tuple[tuple[RuleResult, ...], ...]:
        """Evaluate all configured checks with full-workbook context available."""
        base_results = [
            tuple(rule.evaluate(record) for rule in self.rules)
            for record in records
        ]
        check_results = [
            check.evaluate_records(records)
            for check in self.checks
        ]
        return tuple(
            (
                *base_results[index],
                *(results[index] for results in check_results),
            )
            for index in range(len(records))
        )


@dataclass(frozen=True)
class RulesConfig:
    """Optional rule inputs; unset reference data and limits never imply coverage."""

    service_code_field: str | None = None
    service_codes: tuple[str, ...] | None = None
    diagnosis_field: str | None = None
    diagnosis_codes: tuple[str, ...] | None = None
    amount_field: str | None = None
    maximum_amount: Decimal | int | float | None = None
    checks: tuple[ValidationCheck, ...] = ()

    def __post_init__(self) -> None:
        fields = (
            self.service_code_field,
            self.diagnosis_field,
            self.amount_field,
        )
        if any(
            field is not None
            and (not isinstance(field, str) or not field.strip())
            for field in fields
        ):
            raise ValueError("configured field names cannot be empty")
        for values in (self.service_codes, self.diagnosis_codes):
            if values is not None and any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError("configured reference values cannot be empty")
        if self.service_codes is not None and not self.service_code_field:
            raise ValueError(
                "service_code_field is required when service_codes are set"
            )
        if self.diagnosis_codes is not None and not self.diagnosis_field:
            raise ValueError(
                "diagnosis_field is required when diagnosis_codes are set"
            )
        if self.maximum_amount is not None and not self.amount_field:
            raise ValueError("amount_field is required when maximum_amount is set")
        if self.maximum_amount is not None:
            if isinstance(self.maximum_amount, bool) or not isinstance(
                self.maximum_amount, (Decimal, int, float)
            ):
                raise ValueError("maximum_amount must be numeric")
            try:
                maximum = Decimal(str(self.maximum_amount))
            except InvalidOperation as exc:
                raise ValueError("maximum_amount must be finite") from exc
            if not maximum.is_finite():
                raise ValueError("maximum_amount must be finite")
        generated_ids = set()
        if self.service_code_field:
            generated_ids.update(
                {"service_code_present", "service_code_reference"}
            )
        if self.diagnosis_field:
            generated_ids.update({"diagnosis_present", "diagnosis_reference"})
        if self.amount_field:
            generated_ids.update({"amount_numeric", "amount_maximum"})
        check_ids = tuple(check.rule_id for check in self.checks)
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("configured check ids must be unique")
        if generated_ids.intersection(check_ids):
            raise ValueError("configured check ids conflict with built-in rule ids")


def build_rules(config: RulesConfig) -> RuleEngine:
    """Build only rules enabled by explicit field and reference/limit settings."""
    rules: list[Rule] = []
    if config.service_code_field:
        rules.append(PresenceRule("service_code_present", config.service_code_field))
        rules.append(
            AllowedValuesRule(
                "service_code_reference",
                config.service_code_field,
                (
                    frozenset(config.service_codes)
                    if config.service_codes is not None
                    else None
                ),
            )
        )
    if config.diagnosis_field:
        rules.append(PresenceRule("diagnosis_present", config.diagnosis_field))
        rules.append(
            AllowedValuesRule(
                "diagnosis_reference",
                config.diagnosis_field,
                (
                    frozenset(config.diagnosis_codes)
                    if config.diagnosis_codes is not None
                    else None
                ),
            )
        )
    if config.amount_field:
        rules.append(NumericValueRule("amount_numeric", config.amount_field))
        rules.append(
            MaximumValueRule(
                "amount_maximum",
                config.amount_field,
                config.maximum_amount,
            )
        )
    return RuleEngine(tuple(rules), config.checks)


def evaluate_rules(
    engine: RuleEngine,
    record: Mapping[str, CellValue],
) -> tuple[RuleResult, ...]:
    """Convenience function for the pure evaluation operation."""
    return engine.evaluate(record)
