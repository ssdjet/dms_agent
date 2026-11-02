"""Explicitly configured record- and dataset-level validation checks."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping, Sequence

from .ingestion import CellValue
from .rules import RuleResult, RuleStatus


class CheckKind(str, Enum):
    """Supported technical checks; none infer clinical or coverage policy."""

    REQUIRED = "required"
    VALUE_TYPE = "value_type"
    PATTERN = "pattern"
    NUMERIC_RANGE = "numeric_range"
    DATE_RANGE = "date_range"
    DATE_ORDER = "date_order"
    ALLOWED_VALUES = "allowed_values"
    DENIED_VALUES = "denied_values"
    ALLOWED_PAIRS = "allowed_pairs"
    PROGRAM_SERVICES = "program_services"
    ACTIVE_PERIOD = "active_period"
    PRICE_CATALOG = "price_catalog"
    UNIQUE = "unique"
    REFERENCE_INTEGRITY = "reference_integrity"
    SEQUENCE = "sequence"
    COUNT_LIMIT = "count_limit"
    FREQUENCY_LIMIT = "frequency_limit"
    POLICY_DEPENDENT = "policy_dependent"


_POLICY_REASONS = {
    "program_membership": "program membership criteria are not configured",
    "medical_indications": "medical indication criteria are not configured",
    "medical_necessity": "medical necessity criteria are not configured",
    "quantity_norms": "approved quantity norms are not configured",
    "price_catalog": "an approved price catalog is not configured",
    "contract_period": "contract period data is not configured",
    "repeat_visit": "repeat-visit criteria are not configured",
    "anesthesia": "anesthesia criteria are not configured",
    "coverage_exclusions": "coverage exclusions are not configured",
    "additional_services": "additional-service criteria are not configured",
    "denial_justification": "denial justification criteria are not configured",
    "statistical_baseline": "an approved statistical baseline is not configured",
}

_DATASET_KINDS = frozenset(
    {
        CheckKind.UNIQUE,
        CheckKind.REFERENCE_INTEGRITY,
        CheckKind.SEQUENCE,
        CheckKind.COUNT_LIMIT,
        CheckKind.FREQUENCY_LIMIT,
    }
)


@dataclass(frozen=True)
class ValidationCheck:
    """A validated rule specification parsed from trusted YAML configuration.

    Fields and reference catalogs contain configuration only. Evaluation
    results are static and never include source values.
    """

    rule_id: str
    kind: CheckKind
    fields: tuple[str, ...] = ()
    reference_values: tuple[str, ...] | None = None
    allowed_pairs: tuple[tuple[str, str], ...] = ()
    catalog: tuple[tuple[str, tuple[str, ...] | Decimal], ...] = ()
    periods: tuple[tuple[str, date | None, date | None], ...] = ()
    minimum: Decimal | date | None = None
    maximum: Decimal | date | None = None
    pattern: str | None = None
    expected_type: str | None = None
    date_format: str | None = None
    group_by: tuple[str, ...] = ()
    period: str | None = None
    strict: bool = False
    policy: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CheckKind):
            raise ValueError("check kind is unsupported")
        if not isinstance(self.rule_id, str) or re.fullmatch(
            r"[a-z][a-z0-9_]*", self.rule_id
        ) is None:
            raise ValueError("check id must use lowercase letters, digits, and underscores")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("check fields must be unique")
        if any(not isinstance(field, str) or not field.strip() for field in self.fields):
            raise ValueError("check fields cannot be empty")
        if any(not isinstance(field, str) or not field.strip() for field in self.group_by):
            raise ValueError("group_by fields cannot be empty")
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by fields must be unique")
        if self.reference_values is not None and any(
            not isinstance(value, str) or not value.strip()
            for value in self.reference_values
        ):
            raise ValueError("reference values cannot be empty")
        if any(
            len(pair) != 2
            or any(not isinstance(value, str) or not value.strip() for value in pair)
            for pair in self.allowed_pairs
        ):
            raise ValueError("allowed pairs must contain two non-empty text values")
        catalog_keys = tuple(key for key, _ in self.catalog)
        if len(set(catalog_keys)) != len(catalog_keys) or any(
            not isinstance(key, str) or not key.strip() for key in catalog_keys
        ):
            raise ValueError("catalog keys must be unique non-empty text")
        if self.kind is CheckKind.PROGRAM_SERVICES and any(
            not isinstance(services, tuple)
            or any(not isinstance(value, str) or not value.strip() for value in services)
            for _, services in self.catalog
        ):
            raise ValueError("program services must be lists of non-empty text")
        if self.kind is CheckKind.PRICE_CATALOG and any(
            not isinstance(price, Decimal) or not price.is_finite()
            for _, price in self.catalog
        ):
            raise ValueError("catalog prices must be finite numbers")
        period_keys = tuple(key for key, _, _ in self.periods)
        if len(set(period_keys)) != len(period_keys) or any(
            not isinstance(key, str) or not key.strip() for key in period_keys
        ):
            raise ValueError("period keys must be unique non-empty text")
        if any(
            (start is not None and (not isinstance(start, date) or isinstance(start, datetime)))
            or (end is not None and (not isinstance(end, date) or isinstance(end, datetime)))
            for _, start, end in self.periods
        ):
            raise ValueError("period boundaries must be dates or null")
        for bound in (self.minimum, self.maximum):
            if isinstance(bound, Decimal) and not bound.is_finite():
                raise ValueError("numeric bounds must be finite")
        required_counts: dict[CheckKind, tuple[int, int | None]] = {
            CheckKind.REQUIRED: (1, 1),
            CheckKind.VALUE_TYPE: (1, 1),
            CheckKind.PATTERN: (1, 1),
            CheckKind.NUMERIC_RANGE: (1, 1),
            CheckKind.DATE_RANGE: (1, 1),
            CheckKind.DATE_ORDER: (2, 2),
            CheckKind.ALLOWED_VALUES: (1, 1),
            CheckKind.DENIED_VALUES: (1, 1),
            CheckKind.ALLOWED_PAIRS: (2, 2),
            CheckKind.PROGRAM_SERVICES: (2, 2),
            CheckKind.ACTIVE_PERIOD: (2, 2),
            CheckKind.PRICE_CATALOG: (2, 2),
            CheckKind.UNIQUE: (1, None),
            CheckKind.REFERENCE_INTEGRITY: (2, 2),
            CheckKind.SEQUENCE: (2, None),
            CheckKind.COUNT_LIMIT: (0, 0),
            CheckKind.FREQUENCY_LIMIT: (2, 2),
            CheckKind.POLICY_DEPENDENT: (0, 0),
        }
        lower, upper = required_counts[self.kind]
        if len(self.fields) < lower or (
            upper is not None and len(self.fields) > upper
        ):
            raise ValueError(f"{self.kind.value} check has an invalid number of fields")
        if self.kind in {CheckKind.COUNT_LIMIT, CheckKind.FREQUENCY_LIMIT}:
            if not isinstance(self.maximum, Decimal):
                raise ValueError(f"{self.kind.value} check requires a numeric maximum")
        if self.kind is CheckKind.NUMERIC_RANGE:
            if self.minimum is None and self.maximum is None:
                raise ValueError("numeric_range requires a minimum or maximum")
            if self.minimum is not None and not isinstance(self.minimum, Decimal):
                raise ValueError("numeric_range minimum must be numeric")
            if self.maximum is not None and not isinstance(self.maximum, Decimal):
                raise ValueError("numeric_range maximum must be numeric")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("numeric_range minimum cannot exceed maximum")
        if self.kind is CheckKind.DATE_RANGE:
            if self.minimum is None and self.maximum is None:
                raise ValueError("date_range requires a minimum or maximum")
            if self.minimum is not None and (
                not isinstance(self.minimum, date) or isinstance(self.minimum, datetime)
            ):
                raise ValueError("date_range minimum must be a date")
            if self.maximum is not None and (
                not isinstance(self.maximum, date) or isinstance(self.maximum, datetime)
            ):
                raise ValueError("date_range maximum must be a date")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("date_range minimum cannot exceed maximum")
        if self.kind in {
            CheckKind.COUNT_LIMIT,
            CheckKind.FREQUENCY_LIMIT,
        }:
            if self.maximum < 0:
                raise ValueError(f"{self.kind.value} maximum cannot be negative")
            if self.maximum != self.maximum.to_integral_value():
                raise ValueError(f"{self.kind.value} maximum must be an integer")
        if self.kind is CheckKind.VALUE_TYPE and self.expected_type not in {
            "text",
            "numeric",
            "date",
        }:
            raise ValueError("value_type must be text, numeric, or date")
        if self.kind is CheckKind.PATTERN:
            if not isinstance(self.pattern, str) or not self.pattern:
                raise ValueError("pattern check requires a regular expression")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("pattern check contains an invalid expression") from exc
        if self.kind is CheckKind.FREQUENCY_LIMIT and self.period not in {
            "day",
            "month",
            "year",
        }:
            raise ValueError("frequency_limit period must be day, month, or year")
        if self.kind is CheckKind.POLICY_DEPENDENT and self.policy not in _POLICY_REASONS:
            raise ValueError("policy_dependent check requires a supported policy key")
        if not isinstance(self.strict, bool):
            raise ValueError("strict must be boolean")
        if self.kind is CheckKind.ACTIVE_PERIOD and any(
            start is not None and end is not None and start > end
            for _, start, end in self.periods
        ):
            raise ValueError("active_period start cannot exceed end")

    @property
    def is_dataset_check(self) -> bool:
        """Whether the check needs all records from one workbook."""
        return self.kind in _DATASET_KINDS

    def evaluate_record(self, record: Mapping[str, CellValue]) -> RuleResult:
        """Evaluate a record-level check or report missing dataset context."""
        if self.is_dataset_check:
            return self._result(
                RuleStatus.NOT_EVALUATED,
                "check requires the full dataset; use evaluate_records",
            )
        return self._evaluate_one(record)

    def evaluate_records(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        """Evaluate this check for each row in a dataset, preserving row order."""
        if not self.is_dataset_check:
            return tuple(self._evaluate_one(record) for record in records)
        if self.kind is CheckKind.UNIQUE:
            return self._evaluate_unique(records)
        if self.kind is CheckKind.REFERENCE_INTEGRITY:
            return self._evaluate_references(records)
        if self.kind is CheckKind.SEQUENCE:
            return self._evaluate_sequence(records)
        if self.kind is CheckKind.COUNT_LIMIT:
            return self._evaluate_count_limit(records)
        if self.kind is CheckKind.FREQUENCY_LIMIT:
            return self._evaluate_frequency_limit(records)
        raise AssertionError(f"unhandled dataset check: {self.kind}")

    def _evaluate_one(self, record: Mapping[str, CellValue]) -> RuleResult:
        if self.kind is CheckKind.POLICY_DEPENDENT:
            return self._result(
                RuleStatus.NOT_EVALUATED,
                _POLICY_REASONS[self.policy or ""],
            )
        field = self.fields[0]
        if self.kind is CheckKind.REQUIRED:
            value = record.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                return self._result(RuleStatus.FAIL, "required field is empty")
            return self._result(RuleStatus.PASS, "required field is present")
        if self.kind is CheckKind.VALUE_TYPE:
            value = record.get(field)
            valid = {
                "text": lambda item: isinstance(item, str),
                "numeric": _is_numeric,
                "date": lambda item: isinstance(item, (date, datetime)),
            }[self.expected_type or "text"](value)
            return self._result(
                RuleStatus.PASS if valid else RuleStatus.FAIL,
                "value has the configured type" if valid else "value has the wrong type",
            )
        if self.kind is CheckKind.PATTERN:
            value = record.get(field)
            valid = (
                isinstance(value, str)
                and re.fullmatch(self.pattern or "", value) is not None
            )
            return self._result(
                RuleStatus.PASS if valid else RuleStatus.FAIL,
                "value matches the configured format"
                if valid
                else "value does not match the configured format",
            )
        if self.kind is CheckKind.NUMERIC_RANGE:
            value = record.get(field)
            number = _as_decimal(value)
            if number is None:
                return self._result(RuleStatus.FAIL, "value is not a finite number")
            lower = self.minimum
            upper = self.maximum
            valid = (
                (lower is None or number >= lower)
                and (upper is None or number <= upper)
            )
            return self._result(
                RuleStatus.PASS if valid else RuleStatus.FAIL,
                "value is within the configured range"
                if valid
                else "value is outside the configured range",
            )
        if self.kind is CheckKind.DATE_RANGE:
            value = _as_date(record.get(field), self.date_format)
            if value is None:
                return self._result(RuleStatus.FAIL, "value is not a valid date")
            valid = (
                (self.minimum is None or value >= self.minimum)
                and (self.maximum is None or value <= self.maximum)
            )
            return self._result(
                RuleStatus.PASS if valid else RuleStatus.FAIL,
                "date is within the configured range"
                if valid
                else "date is outside the configured range",
            )
        if self.kind is CheckKind.DATE_ORDER:
            start = _as_date(record.get(self.fields[0]), self.date_format)
            end = _as_date(record.get(self.fields[1]), self.date_format)
            if start is None or end is None:
                return self._result(
                    RuleStatus.FAIL,
                    "date-order fields are not valid dates",
                )
            valid = start < end if self.strict else start <= end
            return self._result(
                RuleStatus.PASS if valid else RuleStatus.FAIL,
                "date order is valid" if valid else "date order is invalid",
            )
        if self.kind in {CheckKind.ALLOWED_VALUES, CheckKind.DENIED_VALUES}:
            values = self.reference_values
            if not values:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "reference values are not configured",
                )
            value = record.get(field)
            if not isinstance(value, str):
                return self._result(RuleStatus.FAIL, "configured field is not text")
            is_allowed = value in values
            passed = (
                is_allowed
                if self.kind is CheckKind.ALLOWED_VALUES
                else not is_allowed
            )
            return self._result(
                RuleStatus.PASS if passed else RuleStatus.FAIL,
                "value satisfies the configured reference check"
                if passed
                else "value violates the configured reference check",
            )
        if self.kind is CheckKind.ALLOWED_PAIRS:
            if not self.allowed_pairs:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "approved field pairs are not configured",
                )
            pair = (record.get(self.fields[0]), record.get(self.fields[1]))
            if any(not isinstance(value, str) for value in pair):
                return self._result(
                    RuleStatus.FAIL,
                    "paired fields must contain text",
                )
            passed = pair in self.allowed_pairs
            return self._result(
                RuleStatus.PASS if passed else RuleStatus.FAIL,
                "field pair is configured" if passed else "field pair is not configured",
            )
        if self.kind is CheckKind.PROGRAM_SERVICES:
            program, service = (record.get(field) for field in self.fields)
            catalog = dict(self.catalog)
            if not catalog:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "program service catalogs are not configured",
                )
            if not isinstance(program, str) or not isinstance(service, str):
                return self._result(
                    RuleStatus.FAIL,
                    "program and service fields must contain text",
                )
            approved = catalog.get(program)
            if approved == ():
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "no service reference values are configured for this program",
                )
            passed = approved is not None and service in approved
            return self._result(
                RuleStatus.PASS if passed else RuleStatus.FAIL,
                "service is configured for the program"
                if passed
                else "service is not configured for the program",
            )
        if self.kind is CheckKind.ACTIVE_PERIOD:
            key = record.get(self.fields[0])
            service_date = _as_date(record.get(self.fields[1]), self.date_format)
            periods = {name: (start, end) for name, start, end in self.periods}
            if not periods:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "effective periods are not configured",
                )
            if not isinstance(key, str) or service_date is None:
                return self._result(RuleStatus.FAIL, "period key or date is invalid")
            if key not in periods:
                return self._result(RuleStatus.FAIL, "period key is not configured")
            start, end = periods[key]
            if start is not None and end is not None and start > end:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "configured effective period is invalid",
                )
            passed = (start is None or service_date >= start) and (
                end is None or service_date <= end
            )
            return self._result(
                RuleStatus.PASS if passed else RuleStatus.FAIL,
                "date is within the configured effective period"
                if passed
                else "date is outside the configured effective period",
            )
        if self.kind is CheckKind.PRICE_CATALOG:
            service, amount = (record.get(field) for field in self.fields)
            catalog = dict(self.catalog)
            if not catalog:
                return self._result(
                    RuleStatus.NOT_EVALUATED,
                    "an approved price catalog is not configured",
                )
            if not isinstance(service, str):
                return self._result(RuleStatus.FAIL, "service field is not text")
            price = catalog.get(service)
            amount_value = _as_decimal(amount)
            if price is None or amount_value is None:
                return self._result(
                    RuleStatus.FAIL,
                    "service price is missing or not numeric",
                )
            passed = amount_value == price
            return self._result(
                RuleStatus.PASS if passed else RuleStatus.FAIL,
                "price matches the configured catalog"
                if passed
                else "price does not match the configured catalog",
            )
        raise AssertionError(f"unhandled record check: {self.kind}")

    def _evaluate_unique(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        keys = [tuple(record.get(field) for field in self.fields) for record in records]
        counts = Counter(keys)
        return tuple(
            self._result(
                RuleStatus.FAIL if count > 1 else RuleStatus.PASS,
                "key is not unique" if count > 1 else "key is unique",
            )
            for key in keys
            for count in (counts[key],)
        )

    def _evaluate_references(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        reference_values = {
            record.get(self.fields[1])
            for record in records
            if _present(record.get(self.fields[1]))
        }
        if not reference_values:
            return tuple(
                self._result(
                    RuleStatus.NOT_EVALUATED,
                    "reference records are not available in the dataset",
                )
                for _ in records
            )
        return tuple(
            self._result(
                RuleStatus.PASS
                if _present(record.get(self.fields[0]))
                and record.get(self.fields[0]) in reference_values
                else RuleStatus.FAIL,
                "reference is present"
                if record.get(self.fields[0]) in reference_values
                else "reference is missing",
            )
            for record in records
        )

    def _evaluate_sequence(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        previous: dict[tuple[CellValue, ...], date] = {}
        results: list[RuleResult] = []
        group_fields = self.fields[:-1]
        date_field = self.fields[-1]
        for record in records:
            group = tuple(record.get(field) for field in group_fields)
            value = _as_date(record.get(date_field), self.date_format)
            if not all(_present(item) for item in group) or value is None:
                results.append(
                    self._result(RuleStatus.FAIL, "sequence key or date is invalid")
                )
                continue
            last = previous.get(group)
            passed = last is None or (last < value if self.strict else last <= value)
            if passed:
                previous[group] = value
            results.append(
                self._result(
                    RuleStatus.PASS if passed else RuleStatus.FAIL,
                    "sequence is ordered" if passed else "sequence is out of order",
                )
            )
        return tuple(results)

    def _evaluate_count_limit(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        groups = [
            tuple(record.get(field) for field in self.group_by)
            for record in records
        ]
        counts = Counter(groups)
        return tuple(
            self._result(
                RuleStatus.FAIL
                if any(not _present(value) for value in key)
                or counts[key] > int(self.maximum or 0)
                else RuleStatus.PASS,
                "group key is incomplete"
                if any(not _present(value) for value in key)
                else "group exceeds the configured count limit"
                if counts[key] > int(self.maximum or 0)
                else "group is within the configured count limit",
            )
            for key in groups
        )

    def _evaluate_frequency_limit(
        self,
        records: Sequence[Mapping[str, CellValue]],
    ) -> tuple[RuleResult, ...]:
        group_fields = self.group_by
        value_field, date_field = self.fields[:2]
        keys: list[tuple[CellValue, ...] | None] = []
        for record in records:
            value = record.get(value_field)
            when = _as_date(record.get(date_field), self.date_format)
            group = tuple(record.get(field) for field in group_fields)
            if not isinstance(value, str) or when is None or not all(
                _present(item) for item in group
            ):
                keys.append(None)
                continue
            bucket: object = when
            if self.period == "month":
                bucket = (when.year, when.month)
            elif self.period == "year":
                bucket = when.year
            keys.append((*group, bucket, value))
        counts = Counter(key for key in keys if key is not None)
        return tuple(
            self._result(
                RuleStatus.FAIL
                if key is None or counts[key] > int(self.maximum or 0)
                else RuleStatus.PASS,
                "frequency inputs are invalid"
                if key is None
                else "frequency exceeds the configured limit"
                if counts[key] > int(self.maximum or 0)
                else "frequency is within the configured limit",
            )
            for key in keys
        )

    def _result(self, status: RuleStatus, message: str) -> RuleResult:
        return RuleResult(self.rule_id, status, message)


def _present(value: CellValue) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _is_numeric(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float, Decimal))
        and _as_decimal(value) is not None
    )


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _as_date(value: object, date_format: str | None = None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if date_format:
            return datetime.strptime(value, date_format).date()
        return date.fromisoformat(value)
    except ValueError:
        return None
