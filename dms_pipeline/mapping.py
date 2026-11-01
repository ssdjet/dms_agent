"""Explicit source-to-standard field mapping utilities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .errors import MappingError
from .ingestion import CellValue, WorkbookRow


@dataclass(frozen=True)
class FieldMapping:
    """Mapping from source column names to caller-selected standard names."""

    source_to_target: Mapping[str, str]
    required_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not isinstance(source, str) or not source.strip()
            for source in self.source_to_target
        ):
            raise MappingError("source column names cannot be empty")
        targets = tuple(self.source_to_target.values())
        if any(not isinstance(target, str) or not target.strip() for target in targets):
            raise MappingError("target field names cannot be empty")
        if len(set(targets)) != len(targets):
            raise MappingError("target field names must be unique")
        if any(
            not isinstance(target, str) or not target.strip()
            for target in self.required_targets
        ):
            raise MappingError("required target names cannot be empty")
        unmapped_required = set(self.required_targets).difference(targets)
        if unmapped_required:
            raise MappingError(
                "required targets are not mapped: "
                + ", ".join(sorted(unmapped_required))
            )


def map_row(
    source_row: Mapping[str, CellValue],
    field_mapping: FieldMapping,
) -> dict[str, CellValue]:
    """Map one row without coercing, dropping, or otherwise changing its values.

    Raises:
        MappingError: If a configured source column is absent from the row.
    """
    missing_sources = set(field_mapping.source_to_target).difference(source_row)
    if missing_sources:
        raise MappingError(
            "source row is missing mapped columns: "
            + ", ".join(sorted(missing_sources))
        )

    mapped = {
        target: source_row[source]
        for source, target in field_mapping.source_to_target.items()
    }
    missing_required = [
        target
        for target in field_mapping.required_targets
        if target not in mapped
        or mapped[target] is None
        or (isinstance(mapped[target], str) and not mapped[target].strip())
    ]
    if missing_required:
        raise MappingError(
            "mapped row is missing required values for: "
            + ", ".join(sorted(missing_required))
        )
    return mapped


def map_rows(
    source_rows: Iterable[Mapping[str, CellValue] | WorkbookRow],
    field_mapping: FieldMapping,
) -> tuple[dict[str, CellValue], ...]:
    """Map a sequence of source rows, returning results in input order."""
    return tuple(
        map_row(row.values if isinstance(row, WorkbookRow) else row, field_mapping)
        for row in source_rows
    )
