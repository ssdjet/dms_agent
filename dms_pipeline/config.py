"""YAML loading for explicit workbook, mapping, and rule configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .checks import CheckKind, ValidationCheck
from .errors import ConfigurationError, PipelineError
from .ingestion import WorkbookReadConfig
from .mapping import FieldMapping
from .rules import RulesConfig


@dataclass(frozen=True)
class PipelineConfig:
    """Validated configuration consumed by the pipeline's independent modules."""

    workbook: WorkbookReadConfig
    mapping: FieldMapping
    rules: RulesConfig


def load_config(path: str | Path) -> PipelineConfig:
    """Load and validate pipeline settings from a YAML file.

    The YAML document must contain ``workbook``, ``mapping``, and ``rules``
    sections. Missing source-specific details should remain unset until inspected.
    """
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as file_stream:
            raw: Any = yaml.safe_load(file_stream)
    except OSError as exc:
        raise ConfigurationError(
            f"Unable to read configuration: {config_path}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Invalid YAML configuration: {config_path}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a mapping")
    _require_known_keys(raw, {"workbook", "mapping", "rules"}, "configuration")

    workbook_raw = _mapping_section(raw, "workbook")
    _require_known_keys(
        workbook_raw,
        {"sheet_name", "header_row", "required_columns", "skip_empty_rows"},
        "workbook",
    )
    sheet_name = workbook_raw.get("sheet_name")
    if sheet_name is not None and not isinstance(sheet_name, str):
        raise ConfigurationError("workbook.sheet_name must be text or null")
    required_columns = _string_tuple(
        workbook_raw.get("required_columns", ()),
        "workbook.required_columns",
    )
    try:
        workbook = WorkbookReadConfig(
            sheet_name=sheet_name,
            header_row=_positive_integer(
                workbook_raw.get("header_row", 1), "workbook.header_row"
            ),
            required_columns=required_columns,
            skip_empty_rows=_boolean(
                workbook_raw.get("skip_empty_rows", True),
                "workbook.skip_empty_rows",
            ),
        )
    except (ValueError, PipelineError) as exc:
        raise ConfigurationError(str(exc)) from exc

    mapping_raw = _mapping_section(raw, "mapping")
    _require_known_keys(
        mapping_raw,
        {"source_to_target", "required_targets"},
        "mapping",
    )
    source_to_target = _mapping_section(mapping_raw, "source_to_target")
    if any(
        not isinstance(source, str) or not isinstance(target, str)
        for source, target in source_to_target.items()
    ):
        raise ConfigurationError("mapping.source_to_target must contain text pairs")
    required_targets = _string_tuple(
        mapping_raw.get("required_targets", ()),
        "mapping.required_targets",
    )
    try:
        mapping = FieldMapping(source_to_target, required_targets)
    except (ValueError, PipelineError) as exc:
        raise ConfigurationError(str(exc)) from exc

    rules_raw = _mapping_section(raw, "rules")
    allowed_rule_keys = {
        "service_code_field",
        "service_codes",
        "diagnosis_field",
        "diagnosis_codes",
        "amount_field",
        "maximum_amount",
        "checks",
    }
    _require_known_keys(rules_raw, allowed_rule_keys, "rules")
    try:
        rules = RulesConfig(
            service_code_field=_optional_string(
                rules_raw.get("service_code_field"), "rules.service_code_field"
            ),
            service_codes=_optional_string_tuple(
                rules_raw.get("service_codes"), "rules.service_codes"
            ),
            diagnosis_field=_optional_string(
                rules_raw.get("diagnosis_field"), "rules.diagnosis_field"
            ),
            diagnosis_codes=_optional_string_tuple(
                rules_raw.get("diagnosis_codes"), "rules.diagnosis_codes"
            ),
            amount_field=_optional_string(
                rules_raw.get("amount_field"), "rules.amount_field"
            ),
            maximum_amount=_optional_decimal(
                rules_raw.get("maximum_amount"), "rules.maximum_amount"
            ),
            checks=_parse_checks(rules_raw.get("checks", ())),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    return PipelineConfig(workbook, mapping, rules)


def _mapping_section(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a mapping")
    return value


def _require_known_keys(
    value: dict[str, Any],
    known_keys: set[str],
    section: str,
) -> None:
    if any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{section} setting names must be text")
    unknown_keys = set(value).difference(known_keys)
    if unknown_keys:
        raise ConfigurationError(
            f"unknown {section} setting(s): {', '.join(sorted(unknown_keys))}"
        )


def _string_tuple(value: Any, setting: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ConfigurationError(f"{setting} must be a list of text values")
    return tuple(value)


def _optional_string_tuple(value: Any, setting: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, setting)


def _optional_string(value: Any, setting: str) -> str | None:
    if value is not None and (
        not isinstance(value, str) or not value.strip()
    ):
        raise ConfigurationError(f"{setting} must be text or null")
    return value


def _positive_integer(value: Any, setting: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{setting} must be a positive integer")
    return value


def _boolean(value: Any, setting: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{setting} must be true or false")
    return value


def _optional_decimal(value: Any, setting: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ConfigurationError(f"{setting} must be a number or null")
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigurationError(f"{setting} must be a finite number") from exc
    if not decimal_value.is_finite():
        raise ConfigurationError(f"{setting} must be a finite number")
    return decimal_value


def _parse_checks(value: Any) -> tuple[ValidationCheck, ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigurationError("rules.checks must be a list")
    checks: list[ValidationCheck] = []
    known_keys = {
        "id",
        "kind",
        "fields",
        "values",
        "pairs",
        "catalog",
        "periods",
        "minimum",
        "maximum",
        "pattern",
        "expected_type",
        "date_format",
        "group_by",
        "period",
        "strict",
        "policy",
    }
    kind_options = {
        CheckKind.REQUIRED: set(),
        CheckKind.VALUE_TYPE: {"expected_type"},
        CheckKind.PATTERN: {"pattern"},
        CheckKind.NUMERIC_RANGE: {"minimum", "maximum"},
        CheckKind.DATE_RANGE: {"minimum", "maximum", "date_format"},
        CheckKind.DATE_ORDER: {"date_format", "strict"},
        CheckKind.ALLOWED_VALUES: {"values"},
        CheckKind.DENIED_VALUES: {"values"},
        CheckKind.ALLOWED_PAIRS: {"pairs"},
        CheckKind.PROGRAM_SERVICES: {"catalog"},
        CheckKind.ACTIVE_PERIOD: {"periods", "date_format"},
        CheckKind.PRICE_CATALOG: {"catalog"},
        CheckKind.UNIQUE: set(),
        CheckKind.REFERENCE_INTEGRITY: set(),
        CheckKind.SEQUENCE: {"date_format", "strict"},
        CheckKind.COUNT_LIMIT: {"group_by", "maximum"},
        CheckKind.FREQUENCY_LIMIT: {
            "date_format",
            "group_by",
            "maximum",
            "period",
        },
        CheckKind.POLICY_DEPENDENT: {"policy"},
    }
    for index, raw_check in enumerate(value):
        setting = f"rules.checks[{index}]"
        if not isinstance(raw_check, dict):
            raise ConfigurationError(f"{setting} must be a mapping")
        _require_known_keys(raw_check, known_keys, setting)
        rule_id = raw_check.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ConfigurationError(f"{setting}.id must be non-empty text")
        try:
            kind = CheckKind(raw_check.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{setting}.kind is not supported") from exc
        unused_keys = set(raw_check).difference({"id", "kind", "fields"}).difference(
            kind_options[kind]
        )
        if unused_keys:
            raise ConfigurationError(
                f"unsupported {kind.value} setting(s): {', '.join(sorted(unused_keys))}"
            )

        fields = _string_tuple(raw_check.get("fields", ()), f"{setting}.fields")
        group_by = _string_tuple(raw_check.get("group_by", ()), f"{setting}.group_by")
        values = _optional_string_tuple(
            raw_check.get("values"), f"{setting}.values"
        )
        pairs = _parse_pairs(raw_check.get("pairs", ()), f"{setting}.pairs")
        catalog = _parse_catalog(
            raw_check.get("catalog"),
            f"{setting}.catalog",
            kind,
        )
        periods = _parse_periods(raw_check.get("periods"), f"{setting}.periods")

        if kind in {CheckKind.NUMERIC_RANGE, CheckKind.COUNT_LIMIT, CheckKind.FREQUENCY_LIMIT}:
            minimum = _optional_decimal(
                raw_check.get("minimum"), f"{setting}.minimum"
            )
            maximum = _optional_decimal(
                raw_check.get("maximum"), f"{setting}.maximum"
            )
        elif kind is CheckKind.DATE_RANGE:
            minimum = _optional_date(raw_check.get("minimum"), f"{setting}.minimum")
            maximum = _optional_date(raw_check.get("maximum"), f"{setting}.maximum")
        else:
            minimum = None
            maximum = None
        if kind in {CheckKind.COUNT_LIMIT, CheckKind.FREQUENCY_LIMIT}:
            if maximum is not None and maximum != maximum.to_integral_value():
                raise ConfigurationError(f"{setting}.maximum must be an integer")
            if maximum is not None and maximum < 0:
                raise ConfigurationError(f"{setting}.maximum cannot be negative")

        pattern = _optional_string(raw_check.get("pattern"), f"{setting}.pattern")
        expected_type = _optional_string(
            raw_check.get("expected_type"), f"{setting}.expected_type"
        )
        date_format = _optional_string(
            raw_check.get("date_format"), f"{setting}.date_format"
        )
        period = _optional_string(raw_check.get("period"), f"{setting}.period")
        policy = _optional_string(raw_check.get("policy"), f"{setting}.policy")
        strict = _boolean(raw_check.get("strict", False), f"{setting}.strict")

        try:
            checks.append(
                ValidationCheck(
                    rule_id=rule_id,
                    kind=kind,
                    fields=fields,
                    reference_values=values,
                    allowed_pairs=pairs,
                    catalog=catalog,
                    periods=periods,
                    minimum=minimum,
                    maximum=maximum,
                    pattern=pattern,
                    expected_type=expected_type,
                    date_format=date_format,
                    group_by=group_by,
                    period=period,
                    strict=strict,
                    policy=policy,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{setting}: {exc}") from exc
    return tuple(checks)


def _parse_pairs(
    value: Any,
    setting: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        raise ConfigurationError(f"{setting} must be a list of two-item text lists")
    pairs: list[tuple[str, str]] = []
    for pair in value:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or any(not isinstance(item, str) for item in pair)
        ):
            raise ConfigurationError(f"{setting} must contain two-item text lists")
        pairs.append((pair[0], pair[1]))
    return tuple(pairs)


def _parse_catalog(
    value: Any,
    setting: str,
    kind: CheckKind,
) -> tuple[tuple[str, tuple[str, ...] | Decimal], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip() for key in value
    ):
        raise ConfigurationError(f"{setting} must be a mapping with text keys")
    entries: list[tuple[str, tuple[str, ...] | Decimal]] = []
    for key, entry in value.items():
        if kind is CheckKind.PROGRAM_SERVICES:
            entries.append((key, _string_tuple(entry, f"{setting}.{key}")))
        elif kind is CheckKind.PRICE_CATALOG:
            price = _optional_decimal(entry, f"{setting}.{key}")
            if price is None:
                raise ConfigurationError(f"{setting}.{key} must be numeric")
            entries.append((key, price))
        else:
            raise ConfigurationError(
                f"{setting} is only supported for program_services or price_catalog"
            )
    return tuple(entries)


def _parse_periods(
    value: Any,
    setting: str,
) -> tuple[tuple[str, date | None, date | None], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip() for key in value
    ):
        raise ConfigurationError(f"{setting} must be a mapping with text keys")
    periods: list[tuple[str, date | None, date | None]] = []
    for key, interval in value.items():
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ConfigurationError(
                f"{setting}.{key} must contain [start_date, end_date]"
            )
        periods.append(
            (
                key,
                _optional_date(interval[0], f"{setting}.{key}[0]"),
                _optional_date(interval[1], f"{setting}.{key}[1]"),
            )
        )
    return tuple(periods)


def _optional_date(value: Any, setting: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigurationError(f"{setting} must be an ISO date or null") from exc
    raise ConfigurationError(f"{setting} must be an ISO date or null")
