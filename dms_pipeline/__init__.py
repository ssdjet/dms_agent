"""Foundational components for a configurable DMS claims-verification pipeline."""

from .audit import (
    AuditEvent,
    AuditFinding,
    AuditStatus,
    create_audit_event,
    serialize_audit_event,
)
from .config import PipelineConfig, load_config
from .ingestion import (
    CellValue,
    WorkbookData,
    WorkbookReadConfig,
    WorkbookRow,
    read_workbook,
    sha256_file,
)
from .mapping import FieldMapping, map_row, map_rows
from .rules import (
    AllowedValuesRule,
    MaximumValueRule,
    NumericValueRule,
    PresenceRule,
    RuleEngine,
    RuleResult,
    RuleStatus,
    RulesConfig,
    build_rules,
)

__all__ = [
    "AllowedValuesRule",
    "AuditEvent",
    "AuditFinding",
    "AuditStatus",
    "CellValue",
    "FieldMapping",
    "MaximumValueRule",
    "NumericValueRule",
    "PipelineConfig",
    "PresenceRule",
    "RuleEngine",
    "RuleResult",
    "RuleStatus",
    "RulesConfig",
    "WorkbookData",
    "WorkbookReadConfig",
    "WorkbookRow",
    "build_rules",
    "create_audit_event",
    "load_config",
    "map_row",
    "map_rows",
    "read_workbook",
    "serialize_audit_event",
    "sha256_file",
]
