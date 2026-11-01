"""Configurable, deterministic readers for Excel workbooks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeAlias
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .errors import WorkbookReadError, WorkbookValidationError

CellValue: TypeAlias = (
    str | int | float | bool | date | datetime | time | timedelta | None
)


@dataclass(frozen=True)
class WorkbookReadConfig:
    """Options defining how a workbook's tabular data is located and validated.

    ``header_row`` uses Excel's one-based row numbering. If ``sheet_name`` is not
    supplied, the workbook must contain exactly one worksheet.
    """

    sheet_name: str | None = None
    header_row: int = 1
    required_columns: tuple[str, ...] = ()
    skip_empty_rows: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.header_row, bool)
            or not isinstance(self.header_row, int)
            or self.header_row < 1
        ):
            raise WorkbookValidationError("header_row must be at least 1")
        if self.sheet_name is not None and (
            not isinstance(self.sheet_name, str) or not self.sheet_name.strip()
        ):
            raise WorkbookValidationError("sheet_name must be non-empty text or null")
        if any(
            not isinstance(column, str) or not column.strip()
            for column in self.required_columns
        ):
            raise WorkbookValidationError("required column names cannot be empty")
        if not isinstance(self.skip_empty_rows, bool):
            raise WorkbookValidationError("skip_empty_rows must be a boolean")


@dataclass(frozen=True)
class WorkbookRow:
    """A source row with its original one-based worksheet row number."""

    row_number: int
    values: dict[str, CellValue]


@dataclass(frozen=True)
class WorkbookData:
    """Workbook rows and source identity, without assumptions about claim fields."""

    sha256: str
    sheet_name: str
    headers: tuple[str, ...]
    rows: tuple[WorkbookRow, ...]


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest for a file's exact bytes.

    Args:
        path: Workbook path.
        chunk_size: Number of bytes read per iteration.

    Raises:
        WorkbookReadError: If the file cannot be read or ``chunk_size`` is invalid.
    """
    file_path = Path(path)
    if chunk_size < 1:
        raise WorkbookReadError("chunk_size must be greater than zero")

    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as file_stream:
            for chunk in iter(lambda: file_stream.read(chunk_size), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WorkbookReadError(f"Unable to read workbook file: {file_path}") from exc
    return digest.hexdigest()


def read_workbook(
    path: str | Path,
    config: WorkbookReadConfig | None = None,
) -> WorkbookData:
    """Read an Excel workbook, preserving source row numbers and hashing its bytes.

    `.xlsx` files are read in read-only mode. Legacy `.xls` files use ``xlrd``;
    that library loads the workbook into memory because its API is not streaming.
    The selected worksheet and header row must be explicit when the workbook
    cannot be unambiguously identified from the supplied configuration.

    Args:
        path: Path to an `.xlsx` or `.xls` workbook.
        config: Worksheet and header validation options.

    Raises:
        WorkbookReadError: If the file is absent, malformed, or unsupported.
        WorkbookValidationError: If worksheet headers or rows are invalid.
    """
    file_path = Path(path)
    reader_config = config or WorkbookReadConfig()
    suffix = file_path.suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise WorkbookReadError(
            f"Unsupported workbook format {suffix or '(no extension)'}; "
            "expected .xlsx or .xls"
        )

    source_hash = sha256_file(file_path)
    if suffix == ".xlsx":
        rows, sheet_name, headers = _read_xlsx(file_path, reader_config)
    else:
        rows, sheet_name, headers = _read_xls(file_path, reader_config)
    return WorkbookData(
        sha256=source_hash,
        sheet_name=sheet_name,
        headers=headers,
        rows=tuple(rows),
    )


def workbook_sheet_names(path: str | Path) -> tuple[str, ...]:
    """Return worksheet names without reading any cell contents."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        try:
            workbook = load_workbook(file_path, read_only=True, data_only=True)
        except (OSError, InvalidFileException, BadZipFile, ValueError) as exc:
            raise WorkbookReadError(
                f"Unable to open .xlsx workbook: {file_path}"
            ) from exc
        try:
            return tuple(workbook.sheetnames)
        finally:
            workbook.close()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise WorkbookReadError(
                "Reading .xls workbooks requires the xlrd dependency"
            ) from exc
        try:
            workbook = xlrd.open_workbook(file_path, on_demand=True)
        except (OSError, xlrd.XLRDError, ValueError) as exc:
            raise WorkbookReadError(
                f"Unable to open .xls workbook: {file_path}"
            ) from exc
        try:
            return tuple(workbook.sheet_names())
        finally:
            workbook.release_resources()
    raise WorkbookReadError(
        f"Unsupported workbook format {suffix or '(no extension)'}; "
        "expected .xlsx or .xls"
    )


def _read_xlsx(
    path: Path,
    config: WorkbookReadConfig,
) -> tuple[list[WorkbookRow], str, tuple[str, ...]]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (OSError, InvalidFileException, BadZipFile, ValueError) as exc:
        raise WorkbookReadError(f"Unable to open .xlsx workbook: {path}") from exc

    try:
        sheet = _select_sheet(workbook.sheetnames, config.sheet_name)
        worksheet = workbook[sheet]
        header_values = next(
            worksheet.iter_rows(
                min_row=config.header_row,
                max_row=config.header_row,
                values_only=True,
            ),
            (),
        )
        headers = _validate_headers(header_values, config.required_columns)
        result: list[WorkbookRow] = []
        for row_number, cells in enumerate(
            worksheet.iter_rows(
                min_row=config.header_row + 1,
                max_col=len(headers),
                values_only=True,
            ),
            start=config.header_row + 1,
        ):
            values = tuple(cells)
            if config.skip_empty_rows and _is_empty_row(values):
                continue
            result.append(
                WorkbookRow(
                    row_number=row_number,
                    values={
                        header: values[index] if index < len(values) else None
                        for index, header in enumerate(headers)
                    },
                )
            )
        return result, sheet, headers
    except WorkbookValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkbookReadError(f"Unable to read .xlsx workbook: {path}") from exc
    finally:
        workbook.close()


def _read_xls(
    path: Path,
    config: WorkbookReadConfig,
) -> tuple[list[WorkbookRow], str, tuple[str, ...]]:
    try:
        import xlrd
    except ImportError as exc:
        raise WorkbookReadError(
            "Reading .xls workbooks requires the xlrd dependency"
        ) from exc

    try:
        workbook = xlrd.open_workbook(path, on_demand=True)
    except (OSError, xlrd.XLRDError, ValueError) as exc:
        raise WorkbookReadError(f"Unable to open .xls workbook: {path}") from exc

    try:
        sheet = _select_sheet(workbook.sheet_names(), config.sheet_name)
        worksheet = workbook.sheet_by_name(sheet)
        header_index = config.header_row - 1
        if header_index >= worksheet.nrows:
            header_values: tuple[CellValue, ...] = ()
        else:
            header_values = tuple(
                _xls_cell_value(worksheet.cell(header_index, column), workbook.datemode)
                for column in range(worksheet.ncols)
            )
        headers = _validate_headers(header_values, config.required_columns)

        result: list[WorkbookRow] = []
        for row_index in range(header_index + 1, worksheet.nrows):
            cells = tuple(
                _xls_cell_value(worksheet.cell(row_index, column), workbook.datemode)
                for column in range(len(headers))
            )
            if config.skip_empty_rows and _is_empty_row(cells):
                continue
            result.append(
                WorkbookRow(
                    row_number=row_index + 1,
                    values=dict(zip(headers, cells)),
                )
            )
        return result, sheet, headers
    except WorkbookValidationError:
        raise
    except (OSError, xlrd.XLRDError, ValueError) as exc:
        raise WorkbookReadError(f"Unable to read .xls workbook: {path}") from exc
    finally:
        workbook.release_resources()


def _select_sheet(sheet_names: list[str], requested_name: str | None) -> str:
    if not sheet_names:
        raise WorkbookValidationError("workbook contains no worksheets")
    if requested_name is not None:
        if requested_name not in sheet_names:
            raise WorkbookValidationError(
                f"configured worksheet does not exist: {requested_name}"
            )
        return requested_name
    if len(sheet_names) != 1:
        raise WorkbookValidationError(
            "sheet_name must be configured when a workbook has multiple worksheets"
        )
    return sheet_names[0]


def _validate_headers(
    values: tuple[CellValue, ...],
    required_columns: tuple[str, ...],
) -> tuple[str, ...]:
    if not values:
        raise WorkbookValidationError("configured header row is empty or out of range")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise WorkbookValidationError(
            "every header cell must contain a non-empty text column name"
        )

    headers = tuple(value.strip() for value in values if isinstance(value, str))
    if len({header.casefold() for header in headers}) != len(headers):
        raise WorkbookValidationError("worksheet contains duplicate column names")
    missing_columns = sorted(set(required_columns).difference(headers))
    if missing_columns:
        raise WorkbookValidationError(
            f"worksheet is missing required columns: {', '.join(missing_columns)}"
        )
    return headers


def _is_empty_row(values: tuple[CellValue, ...]) -> bool:
    """Return whether every cell is empty or whitespace-only."""
    return all(
        value is None or (isinstance(value, str) and not value.strip())
        for value in values
    )


def _xls_cell_value(cell: Any, datemode: int) -> CellValue:
    """Convert xlrd cell objects to standard Python values."""
    import xlrd

    cell_type = cell.ctype
    value = cell.value
    if cell_type in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell_type == xlrd.XL_CELL_BOOLEAN:
        return bool(value)
    if cell_type == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(value, datemode)
    if cell_type == xlrd.XL_CELL_ERROR:
        raise WorkbookValidationError("worksheet contains an Excel error cell")
    return value
