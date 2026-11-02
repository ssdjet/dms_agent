"""Focused tests for deterministic, configurable pipeline primitives."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt
from openpyxl import Workbook

from dms_pipeline.checks import CheckKind, ValidationCheck
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


def _write_legacy_workbook(
    path: Path,
    sheet_names: tuple[str, ...] = ("Claims",),
) -> None:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet(sheet_names[0])
    sheet.write(0, 0, "Code")
    sheet.write(0, 1, "Amount")
    sheet.write(1, 0, "SVC-01")
    sheet.write(1, 1, 25.5)
    sheet.write(2, 0, "")
    sheet.write(3, 0, "SVC-02")
    sheet.write(3, 1, 40)
    for sheet_name in sheet_names[1:]:
        workbook.add_sheet(sheet_name)
    workbook.save(str(path))


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


def test_read_legacy_xls_skips_empty_rows_and_preserves_source_row_numbers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic.xls"
    _write_legacy_workbook(source)

    result = read_workbook(source, WorkbookReadConfig(required_columns=("Code",)))

    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.sheet_name == "Claims"
    assert result.headers == ("Code", "Amount")
    assert [row.row_number for row in result.rows] == [2, 4]
    assert result.rows[0].values == {"Code": "SVC-01", "Amount": 25.5}
    assert result.rows[1].values == {"Code": "SVC-02", "Amount": 40.0}


def test_read_legacy_xls_requires_explicit_sheet_for_ambiguous_workbook(
    tmp_path: Path,
) -> None:
    source = tmp_path / "multiple.xls"
    _write_legacy_workbook(source, ("Claims", "Other"))

    with pytest.raises(WorkbookValidationError, match="sheet_name"):
        read_workbook(source)


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


def test_read_workbook_rejects_duplicate_headers_case_insensitively(
    tmp_path: Path,
) -> None:
    source = tmp_path / "duplicate_headers.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Code", "code"])
    workbook.save(source)

    with pytest.raises(WorkbookValidationError, match="duplicate"):
        read_workbook(source)


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


def test_synthetic_workbook_pipeline_maps_validates_and_audits_without_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic.xlsx"
    _write_workbook(source)
    workbook = read_workbook(source)
    field_mapping = FieldMapping(
        {"Code": "service_code", "Diagnosis": "diagnosis", "Amount": "amount"},
        required_targets=("service_code", "diagnosis", "amount"),
    )
    mapped = map_rows(workbook.rows, field_mapping)[0]
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
    event = create_audit_event(
        workbook.sha256,
        workbook.rows[0].row_number,
        engine.evaluate(mapped),
    )
    audit_json = serialize_audit_event(event)

    assert event.status is AuditStatus.PASS
    assert "SVC-01" not in audit_json
    assert "DX-01" not in audit_json
    assert "25.5" not in audit_json


def test_sample_yaml_loads_without_inventing_claim_schema() -> None:
    config = load_config(Path(__file__).parents[1] / "config" / "sample.yml")

    assert config.workbook.sheet_name is None
    assert config.mapping.source_to_target == {}
    assert config.rules.service_codes is None
    assert config.rules.checks == ()
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


def test_configured_checks_cover_policy_catalogs_and_deferred_criteria(
    tmp_path: Path,
) -> None:
    source = tmp_path / "checks.yml"
    source.write_text(
        """\
workbook: {}
mapping:
  source_to_target: {}
rules:
  checks:
    - id: service_program_allow
      kind: program_services
      fields: [program, service]
      catalog:
        PLAN-A: [SVC-01]
    - id: service_diagnosis_pair
      kind: allowed_pairs
      fields: [service, diagnosis]
      pairs:
        - [SVC-01, DX-01]
    - id: program_active_period
      kind: active_period
      fields: [program, service_date]
      periods:
        PLAN-A: [2025-01-01, 2025-12-31]
    - id: price_catalog
      kind: price_catalog
      fields: [service, amount]
      catalog:
        SVC-01: 25.00
    - id: medical_indications
      kind: policy_dependent
      policy: medical_indications
""",
        encoding="utf-8",
    )

    config = load_config(source)
    engine = build_rules(config.rules)
    passing = engine.evaluate(
        {
            "program": "PLAN-A",
            "service": "SVC-01",
            "diagnosis": "DX-01",
            "service_date": "2025-05-01",
            "amount": 25,
        }
    )
    failing = engine.evaluate(
        {
            "program": "PLAN-A",
            "service": "SVC-02",
            "diagnosis": "DX-99",
            "service_date": "2026-01-01",
            "amount": 99,
        }
    )

    assert [result.status for result in passing] == [
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.NOT_EVALUATED,
    ]
    assert [result.status for result in failing] == [
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.NOT_EVALUATED,
    ]
    assert passing[-1].message == "medical indication criteria are not configured"
    assert "PLAN-A" not in passing[-1].message


def test_dataset_checks_detect_duplicates_links_order_and_explicit_limits() -> None:
    checks = (
        ValidationCheck("visit_id_unique", CheckKind.UNIQUE, ("visit_id",)),
        ValidationCheck(
            "provider_reference",
            CheckKind.REFERENCE_INTEGRITY,
            ("provider_ref", "provider_id"),
        ),
        ValidationCheck(
            "visit_sequence",
            CheckKind.SEQUENCE,
            ("patient_id", "visit_date"),
        ),
        ValidationCheck(
            "services_per_visit",
            CheckKind.COUNT_LIMIT,
            group_by=("visit_id",),
            maximum=Decimal("1"),
        ),
        ValidationCheck(
            "service_frequency",
            CheckKind.FREQUENCY_LIMIT,
            ("service_code", "visit_date"),
            maximum=Decimal("1"),
            group_by=("patient_id",),
            period="month",
        ),
    )
    records = (
        {
            "visit_id": "V-1",
            "provider_ref": "P-1",
            "provider_id": "P-1",
            "patient_id": "C-1",
            "visit_date": "2025-01-01",
            "service_code": "S-1",
        },
        {
            "visit_id": "V-1",
            "provider_ref": "UNKNOWN",
            "provider_id": "P-1",
            "patient_id": "C-1",
            "visit_date": "2024-12-31",
            "service_code": "S-1",
        },
        {
            "visit_id": "V-2",
            "provider_ref": "P-1",
            "provider_id": "P-1",
            "patient_id": "C-1",
            "visit_date": "2025-01-05",
            "service_code": "S-1",
        },
    )

    results = RuleEngine((), checks).evaluate_records(records)

    assert [result.status for result in results[0]] == [
        RuleStatus.FAIL,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
    ]
    assert [result.status for result in results[1]] == [
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.PASS,
    ]
    assert [result.status for result in results[2]] == [
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.PASS,
        RuleStatus.FAIL,
    ]
    assert checks[2].evaluate_record(records[0]).status is RuleStatus.NOT_EVALUATED


def test_unconfigured_reference_and_policy_checks_are_not_evaluated() -> None:
    checks = (
        ValidationCheck(
            "service_allowlist",
            CheckKind.ALLOWED_VALUES,
            ("service_code",),
        ),
        ValidationCheck(
            "coverage_exclusions",
            CheckKind.DENIED_VALUES,
            ("service_code",),
        ),
        ValidationCheck(
            "program_membership",
            CheckKind.PROGRAM_SERVICES,
            ("program", "service_code"),
        ),
        ValidationCheck(
            "repeat_visit_justification",
            CheckKind.POLICY_DEPENDENT,
            policy="repeat_visit",
        ),
    )
    results = RuleEngine((), checks).evaluate({"service_code": "SYNTHETIC"})

    assert all(result.status is RuleStatus.NOT_EVALUATED for result in results)
    assert all("SYNTHETIC" not in result.message for result in results)


def test_configured_allowed_and_denied_references_are_exact() -> None:
    engine = RuleEngine(
        (),
        (
            ValidationCheck(
                "service_allowlist",
                CheckKind.ALLOWED_VALUES,
                ("service_code",),
                reference_values=("SYN-SVC-01",),
            ),
            ValidationCheck(
                "service_exclusions",
                CheckKind.DENIED_VALUES,
                ("service_code",),
                reference_values=("SYN-SVC-X",),
            ),
        ),
    )

    allowed = engine.evaluate({"service_code": "SYN-SVC-01"})
    excluded = engine.evaluate({"service_code": "SYN-SVC-X"})

    assert [result.status for result in allowed] == [
        RuleStatus.PASS,
        RuleStatus.PASS,
    ]
    assert [result.status for result in excluded] == [
        RuleStatus.FAIL,
        RuleStatus.FAIL,
    ]


def test_configured_audit_messages_and_json_exclude_record_values() -> None:
    sensitive_value = "SYNTHETIC-SENSITIVE-CODE"
    engine = RuleEngine(
        (),
        (
            ValidationCheck(
                "service_code_format",
                CheckKind.PATTERN,
                ("service_code",),
                pattern=r"SVC-[0-9]+",
            ),
        ),
    )
    result = engine.evaluate({"service_code": sensitive_value})[0]
    event = create_audit_event("d" * 64, 2, (result,))

    assert result.status is RuleStatus.FAIL
    assert sensitive_value not in result.message
    assert sensitive_value not in serialize_audit_event(event)


def test_configured_format_range_and_date_order_checks() -> None:
    checks = (
        ValidationCheck(
            "service_code_format",
            CheckKind.PATTERN,
            ("service_code",),
            pattern=r"SYN-[0-9]{2}",
        ),
        ValidationCheck(
            "quantity_range",
            CheckKind.NUMERIC_RANGE,
            ("quantity",),
            minimum=Decimal("1"),
            maximum=Decimal("5"),
        ),
        ValidationCheck(
            "minimum_quantity",
            CheckKind.NUMERIC_RANGE,
            ("quantity",),
            minimum=Decimal("1"),
        ),
        ValidationCheck(
            "service_date_range",
            CheckKind.DATE_RANGE,
            ("service_date",),
            minimum=date(2025, 1, 1),
            maximum=date(2025, 12, 31),
        ),
        ValidationCheck(
            "visit_date_order",
            CheckKind.DATE_ORDER,
            ("visit_start", "visit_end"),
        ),
        ValidationCheck(
            "service_type",
            CheckKind.VALUE_TYPE,
            ("service_code",),
            expected_type="text",
        ),
    )
    engine = RuleEngine((), checks)

    passed = engine.evaluate(
        {
            "service_code": "SYN-01",
            "quantity": 3,
            "service_date": "2025-03-01",
            "visit_start": "2025-03-01",
            "visit_end": "2025-03-01",
        }
    )
    failed = engine.evaluate(
        {
            "service_code": "SYN-ABC",
            "quantity": True,
            "service_date": "2026-03-01",
            "visit_start": "2025-03-02",
            "visit_end": "2025-03-01",
        }
    )

    assert [result.status for result in passed] == [RuleStatus.PASS] * 6
    assert [result.status for result in failed] == [
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.FAIL,
        RuleStatus.PASS,
    ]


def test_config_rejects_malformed_configured_checks(tmp_path: Path) -> None:
    source = tmp_path / "invalid-checks.yml"
    source.write_text(
        """\
workbook: {}
mapping:
  source_to_target: {}
rules:
  checks:
    - id: invalid
      kind: numeric_range
      fields: [amount]
      minimum: 10
      maximum: 1
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="minimum cannot exceed maximum"):
        load_config(source)
