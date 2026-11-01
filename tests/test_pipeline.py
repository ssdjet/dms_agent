"""Focused tests for deterministic, configurable pipeline primitives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from dms_pipeline.audit import (
    AuditStatus,
    create_audit_event,
    serialize_audit_event,
)
from dms_pipeline.config import load_config
from dms_pipeline.errors import (
    ConfigurationError,
    MappingError,
    WorkbookReadError,
    WorkbookValidationError,
)
from dms_pipeline.ingestion import WorkbookReadConfig, read_workbook, sha256_file
from dms_pipeline.mapping import FieldMapping, map_row, map_rows
from dms_pipeline.rules import (
    AllowedValuesRule,
    MaximumValueRule,
    PresenceRule,
    RuleEngine,
    RuleStatus,
    RulesConfig,
    build_rules,
)


def _write_workbook(path: Path, sheet_names: tuple[str, ...] = ("Claims",)) -> None:
    workbook = Workbook()
    workbook.active.title = sheet_names[0]
    for sheet_name in sheet_names[1:]:
        workbook.create_sheet(sheet_name)
    sheet = workbook[sheet_names[0]]
    sheet.append(["Code", "Diagnosis", "Amount"])
    sheet.append(["SVC-01", "DX-01", 25.5])
    workbook.save(path)


def test_read_workbook_calculates_source_hash_and_preserves_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic.xlsx"
    _write_workbook(source)

    result = read_workbook(source, WorkbookReadConfig(required_columns=("Code",)))

    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.sha256 == sha256_file(source)
    assert result.sheet_name == "Claims"
    assert result.headers == ("Code", "Diagnosis", "Amount")
    assert result.rows[0].row_number == 2
    assert result.rows[0].values == {
        "Code": "SVC-01",
        "Diagnosis": "DX-01",
        "Amount": 25.5,
    }


def test_read_workbook_requires_explicit_sheet_for_ambiguous_workbook(
    tmp_path: Path,
) -> None:
    source = tmp_path / "multiple.xlsx"
    _write_workbook(source, ("Claims", "Other"))

    with pytest.raises(WorkbookValidationError, match="sheet_name"):
        read_workbook(source)


def test_read_workbook_rejects_missing_required_columns(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.xlsx"
    _write_workbook(source)

    with pytest.raises(WorkbookValidationError, match="missing required columns"):
        read_workbook(source, WorkbookReadConfig(required_columns=("Unknown",)))


def test_read_workbook_rejects_unsupported_format(tmp_path: Path) -> None:
    with pytest.raises(WorkbookReadError, match="Unsupported workbook format"):
        read_workbook(tmp_path / "synthetic.csv")


def test_workbook_config_rejects_invalid_direct_values() -> None:
    with pytest.raises(WorkbookValidationError, match="header_row"):
        WorkbookReadConfig(header_row=True)
    with pytest.raises(WorkbookValidationError, match="required column"):
        WorkbookReadConfig(required_columns=(1,))  # type: ignore[arg-type]


def test_field_mapping_is_explicit_and_preserves_values() -> None:
    field_mapping = FieldMapping(
        {"Code": "service_code", "Amount": "amount"},
        required_targets=("service_code",),
    )
    source_row = {"Code": "SVC-01", "Amount": 25.5, "Unmapped": "not copied"}

    assert map_row(source_row, field_mapping) == {
        "service_code": "SVC-01",
        "amount": 25.5,
    }
    assert map_rows([source_row], field_mapping) == (
        {"service_code": "SVC-01", "amount": 25.5},
    )


def test_field_mapping_rejects_missing_columns_or_required_values() -> None:
    field_mapping = FieldMapping(
        {"Code": "service_code"},
        required_targets=("service_code",),
    )

    with pytest.raises(MappingError, match="missing mapped columns"):
        map_row({}, field_mapping)
    with pytest.raises(MappingError, match="missing required values"):
        map_row({"Code": None}, field_mapping)


def test_rules_only_apply_explicit_reference_sets_and_limits() -> None:
    engine = build_rules(
        RulesConfig(
            service_code_field="service_code",
            service_codes=("SVC-01",),
            diagnosis_field="diagnosis",
            diagnosis_codes=("DX-01",),
            amount_field="amount",
            maximum_amount=30,
        )
    )

    passing = engine.evaluate(
        {"service_code": "SVC-01", "diagnosis": "DX-01", "amount": 25.5}
    )
    failing = engine.evaluate(
        {"service_code": "SVC-02", "diagnosis": "DX-01", "amount": 31}
    )

    assert all(result.status is RuleStatus.PASS for result in passing)
    assert [result.status for result in failing] == [
        RuleStatus.PASS,
        RuleStatus.FAIL,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.FAIL,
    ]


def test_unconfigured_reference_and_limit_are_not_evaluated() -> None:
    engine = build_rules(
        RulesConfig(
            service_code_field="service_code",
            amount_field="amount",
        )
    )

    results = engine.evaluate({"service_code": "SVC-01", "amount": 1})

    assert [result.status for result in results] == [
        RuleStatus.PASS,
        RuleStatus.NOT_EVALUATED,
        RuleStatus.PASS,
        RuleStatus.NOT_EVALUATED,
    ]


def test_rule_results_do_not_include_record_values() -> None:
    rule = AllowedValuesRule(
        "service_code_reference",
        "service_code",
        frozenset({"SVC-01"}),
    )

    result = rule.evaluate({"service_code": "SENSITIVE-VALUE"})

    assert result.status is RuleStatus.FAIL
    assert "SENSITIVE-VALUE" not in result.message


def test_audit_serialization_is_deterministic_and_excludes_record_values() -> None:
    engine = RuleEngine((PresenceRule("service_code_present", "service_code"),))
    results = engine.evaluate({"service_code": "SYNTHETIC-CODE"})
    event = create_audit_event("a" * 64, 12, results)
    serialized = serialize_audit_event(event)

    assert event.status is AuditStatus.PASS
    assert serialized == serialize_audit_event(event)
    assert json.loads(serialized) == {
        "findings": [{"rule_id": "service_code_present", "status": "pass"}],
        "source_row_number": 12,
        "source_sha256": "a" * 64,
        "status": "pass",
    }
    assert "SYNTHETIC-CODE" not in serialized


def test_audit_aggregate_status_for_failed_or_unevaluated_rules() -> None:
    failed_event = create_audit_event(
        "b" * 64,
        2,
        (PresenceRule("service", "code").evaluate({"code": ""}),),
    )
    unevaluated_event = create_audit_event("c" * 64, 3, ())

    assert failed_event.status is AuditStatus.FAIL
    assert unevaluated_event.status is AuditStatus.NOT_EVALUATED


def test_sample_yaml_loads_without_inventing_claim_schema() -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "sample.yml")

    assert config.workbook.sheet_name is None
    assert config.mapping.source_to_target == {}
    assert config.rules.service_codes is None
    assert build_rules(config.rules).rules == ()


def test_config_rejects_unknown_settings(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yml"
    source.write_text(
        "workbook:\n  guessed_column: value\nmapping:\n  source_to_target: {}\n"
        "rules: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unknown workbook"):
        load_config(source)


def test_config_wraps_invalid_workbook_and_mapping_settings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.yml"
    source.write_text(
        "workbook:\n  header_row: 0\nmapping:\n  source_to_target: {}\n"
        "rules: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="header_row"):
        load_config(source)


def test_standalone_numeric_and_limit_rules_handle_invalid_values() -> None:
    numeric = RuleEngine((MaximumValueRule("limit", "amount", 100),))

    assert numeric.evaluate({"amount": True})[0].status is RuleStatus.FAIL
    assert numeric.evaluate({"amount": 101})[0].status is RuleStatus.FAIL
