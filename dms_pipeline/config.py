"""YAML loading for explicit workbook, mapping, and rule configuration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

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
        )
    except ValueError as exc:
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
