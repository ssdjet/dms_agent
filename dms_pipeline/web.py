"""Local web interface for running configured checks on Excel workbooks.

Run with ``python -m dms_pipeline.web``. The server binds to loopback by
default; it does not persist uploaded files or return source-row values.
"""

from __future__ import annotations

import argparse
import tempfile
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .audit import AuditStatus, create_audit_event
from .errors import PipelineError, WorkbookValidationError
from .ingestion import WorkbookReadConfig, read_workbook, workbook_sheet_names
from .mapping import FieldMapping, map_row
from .rules import RuleResult, RuleStatus, RulesConfig, build_rules

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DISPLAYED_FINDINGS = 1000
STATIC_DIRECTORY = Path(__file__).parent / "static"

_RULE_LABELS = {
    "service_code_present": "Код услуги заполнен",
    "service_code_reference": "Код услуги в справочнике",
    "diagnosis_present": "Диагноз заполнен",
    "diagnosis_reference": "Диагноз в справочнике",
    "amount_numeric": "Сумма указана числом",
    "amount_maximum": "Сумма не превышает заданный лимит",
}


def create_app() -> Flask:
    """Create the local analysis application."""
    app = Flask(__name__, static_folder=str(STATIC_DIRECTORY))
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/")
    def index() -> Any:
        return send_from_directory(STATIC_DIRECTORY, "index.html")

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error: RequestEntityTooLarge) -> Any:
        return jsonify({"error": "Файл превышает лимит 20 МБ."}), 413

    @app.errorhandler(PipelineError)
    def pipeline_error(_error: PipelineError) -> Any:
        return (
            jsonify(
                {
                    "error": (
                        "Не удалось прочитать файл или заголовки. "
                        "Проверьте лист и строку заголовков."
                    )
                }
            ),
            400,
        )

    @app.errorhandler(BadRequest)
    def bad_request(error: BadRequest) -> Any:
        return jsonify({"error": error.description}), 400

    @app.after_request
    def add_security_headers(response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.post("/api/inspect")
    def inspect() -> Any:
        upload = _get_upload()
        header_row = _parse_header_row(request.form.get("header_row", "1"))
        selected_sheet = request.form.get("sheet_name", "").strip() or None

        with _temporary_upload(upload) as file_path:
            sheet_names = workbook_sheet_names(file_path)
            if not sheet_names:
                raise WorkbookValidationError("В книге нет листов.")
            if selected_sheet is not None and selected_sheet not in sheet_names:
                raise WorkbookValidationError("Выбранный лист отсутствует в книге.")
            if selected_sheet is None and len(sheet_names) > 1:
                return jsonify(
                    {
                        "sheet_names": sheet_names,
                        "needs_sheet_selection": True,
                        "columns": [],
                        "row_count": None,
                    }
                )

            data = read_workbook(
                file_path,
                WorkbookReadConfig(
                    sheet_name=selected_sheet or sheet_names[0],
                    header_row=header_row,
                ),
            )
        return jsonify(
            {
                "sheet_names": sheet_names,
                "sheet_name": data.sheet_name,
                "needs_sheet_selection": False,
                "columns": data.headers,
                "row_count": len(data.rows),
                "source_sha256": data.sha256,
            }
        )

    @app.post("/api/analyze")
    def analyze() -> Any:
        upload = _get_upload()
        header_row = _parse_header_row(request.form.get("header_row", "1"))
        selected_sheet = request.form.get("sheet_name", "").strip() or None

        with _temporary_upload(upload) as file_path:
            sheet_names = workbook_sheet_names(file_path)
            if not sheet_names:
                raise WorkbookValidationError("В книге нет листов.")
            if selected_sheet is None:
                if len(sheet_names) != 1:
                    raise WorkbookValidationError(
                        "Выберите лист книги перед запуском проверок."
                    )
                selected_sheet = sheet_names[0]
            data = read_workbook(
                file_path,
                WorkbookReadConfig(
                    sheet_name=selected_sheet,
                    header_row=header_row,
                ),
            )

            source_fields = {
                "service_code": _selected_column("service_code_column", data.headers),
                "diagnosis": _selected_column("diagnosis_column", data.headers),
                "amount": _selected_column("amount_column", data.headers),
            }
            if not any(source_fields.values()):
                raise BadRequest("Выберите хотя бы одно поле для проверки.")
            selected_values = [value for value in source_fields.values() if value]
            if len(set(selected_values)) != len(selected_values):
                raise BadRequest(
                    "Для разных полей выберите разные столбцы Excel."
                )

            mapping = FieldMapping(
                {
                    source_column: target
                    for target, source_column in source_fields.items()
                    if source_column
                }
            )
            service_codes = _parse_reference_values(
                request.form.get("service_codes", "")
            )
            diagnosis_codes = _parse_reference_values(
                request.form.get("diagnosis_codes", "")
            )
            maximum_amount = _parse_maximum_amount(
                request.form.get("maximum_amount", "")
            )
            rules_config = RulesConfig(
                service_code_field=(
                    "service_code" if source_fields["service_code"] else None
                ),
                service_codes=(
                    service_codes
                    if source_fields["service_code"] and service_codes
                    else None
                ),
                diagnosis_field=(
                    "diagnosis" if source_fields["diagnosis"] else None
                ),
                diagnosis_codes=(
                    diagnosis_codes
                    if source_fields["diagnosis"] and diagnosis_codes
                    else None
                ),
                amount_field="amount" if source_fields["amount"] else None,
                maximum_amount=(
                    maximum_amount
                    if source_fields["amount"] and maximum_amount is not None
                    else None
                ),
            )
            engine = build_rules(rules_config)

            row_findings: list[dict[str, Any]] = []
            status_counts: Counter[str] = Counter()
            rule_counts: dict[str, Counter[str]] = {}
            for row in data.rows:
                mapped = map_row(row.values, mapping)
                results = engine.evaluate(mapped)
                event = create_audit_event(data.sha256, row.row_number, results)
                status_counts[event.status.value] += 1
                for result in results:
                    rule_counts.setdefault(result.rule_id, Counter())[
                        result.status.value
                    ] += 1
                if event.status != AuditStatus.PASS:
                    row_findings.append(
                        {
                            "row_number": row.row_number,
                            "status": event.status.value,
                            "findings": [_safe_finding(result) for result in results],
                        }
                    )

        return jsonify(
            {
                "source_sha256": data.sha256,
                "sheet_name": data.sheet_name,
                "rows_checked": len(data.rows),
                "summary": {
                    "pass": status_counts[AuditStatus.PASS.value],
                    "fail": status_counts[AuditStatus.FAIL.value],
                    "not_evaluated": status_counts[AuditStatus.NOT_EVALUATED.value],
                },
                "rules": [
                    {
                        "rule_id": rule_id,
                        "label": _RULE_LABELS.get(rule_id, rule_id),
                        "pass": counts[RuleStatus.PASS.value],
                        "fail": counts[RuleStatus.FAIL.value],
                        "not_evaluated": counts[RuleStatus.NOT_EVALUATED.value],
                    }
                    for rule_id, counts in sorted(rule_counts.items())
                ],
                "findings": row_findings[:MAX_DISPLAYED_FINDINGS],
                "findings_total": len(row_findings),
                "findings_truncated": len(row_findings) > MAX_DISPLAYED_FINDINGS,
            }
        )

    return app


def _get_upload() -> FileStorage:
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise BadRequest("Выберите файл Excel.")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise BadRequest("Поддерживаются только файлы .xls и .xlsx.")
    return upload


@contextmanager
def _temporary_upload(upload: FileStorage) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="dms-upload-") as directory:
        file_path = (
            Path(directory) / f"upload{Path(upload.filename).suffix.lower()}"
        )
        upload.save(file_path)
        if file_path.stat().st_size == 0:
            raise BadRequest("Загруженный файл пуст.")
        yield file_path


def _parse_header_row(value: str) -> int:
    try:
        header_row = int(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest("Номер строки заголовков должен быть целым числом.") from exc
    if header_row < 1:
        raise BadRequest("Номер строки заголовков должен быть не меньше 1.")
    return header_row


def _selected_column(field_name: str, headers: tuple[str, ...]) -> str | None:
    selected = request.form.get(field_name, "").strip()
    if not selected:
        return None
    if selected not in headers:
        raise BadRequest("Выбранный столбец отсутствует в книге.")
    return selected


def _parse_reference_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.splitlines() if item.strip())


def _parse_maximum_amount(value: str) -> Decimal | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned.replace(",", "."))
    except InvalidOperation as exc:
        raise BadRequest("Лимит должен быть числом.") from exc
    if not amount.is_finite():
        raise BadRequest("Лимит должен быть конечным числом.")
    return amount


def _safe_finding(result: RuleResult) -> dict[str, str]:
    return {
        "rule_id": result.rule_id,
        "label": _RULE_LABELS.get(result.rule_id, result.rule_id),
        "status": result.status.value,
        "message": result.message,
    }


def main() -> None:
    """Start the local-only development server."""
    parser = argparse.ArgumentParser(description="Локальный интерфейс проверки DMS.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Сервер принимает файлы только на loopback-интерфейсе.")
    create_app().run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
