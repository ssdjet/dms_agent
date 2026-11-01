"""Tests for the local web interface without real or persistent claim data."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from dms_pipeline.web import create_app


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Claims"
    sheet.append(["Code", "Diagnosis", "Amount"])
    sheet.append(["SYNTHETIC-CODE-1", "SYNTHETIC-DX-1", 25])
    sheet.append(["SYNTHETIC-CODE-2", "SYNTHETIC-DX-2", 150])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_homepage_serves_local_assets_with_security_headers() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Результаты проверки".encode() in response.data
    assert "text/html" in response.content_type
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_inspect_returns_headers_and_counts_without_row_values() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/inspect",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
            "header_row": "1",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["columns"] == ["Code", "Diagnosis", "Amount"]
    assert payload["row_count"] == 2
    assert len(payload["source_sha256"]) == 64
    assert "SYNTHETIC-CODE-1" not in response.get_data(as_text=True)
    assert "SYNTHETIC-DX-2" not in response.get_data(as_text=True)


def test_analyze_returns_rule_counts_without_source_values() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/analyze",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
            "header_row": "1",
            "service_code_column": "Code",
            "diagnosis_column": "Diagnosis",
            "amount_column": "Amount",
            "service_codes": "SYNTHETIC-CODE-1",
            "diagnosis_codes": "SYNTHETIC-DX-1",
            "maximum_amount": "100",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["rows_checked"] == 2
    assert payload["summary"] == {"pass": 1, "fail": 1, "not_evaluated": 0}
    assert payload["findings_total"] == 1
    assert payload["findings"][0]["row_number"] == 3
    assert payload["findings"][0]["status"] == "fail"
    assert {rule["rule_id"] for rule in payload["rules"]} == {
        "service_code_present",
        "service_code_reference",
        "diagnosis_present",
        "diagnosis_reference",
        "amount_numeric",
        "amount_maximum",
    }
    response_text = response.get_data(as_text=True)
    assert "SYNTHETIC-CODE-1" not in response_text
    assert "SYNTHETIC-CODE-2" not in response_text
    assert "SYNTHETIC-DX-1" not in response_text
    assert "SYNTHETIC-DX-2" not in response_text
    assert "150" not in response_text


def test_analyze_without_reference_values_marks_them_not_evaluated() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/analyze",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
            "service_code_column": "Code",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"] == {"pass": 0, "fail": 0, "not_evaluated": 2}
    assert payload["findings_total"] == 2
    reference_rule = next(
        rule for rule in payload["rules"] if rule["rule_id"] == "service_code_reference"
    )
    assert reference_rule["not_evaluated"] == 2


def test_inspect_requires_sheet_selection_for_multi_sheet_workbook() -> None:
    workbook = Workbook()
    workbook.active.title = "Claims"
    workbook.active.append(["Code"])
    workbook.create_sheet("Other")
    buffer = BytesIO()
    workbook.save(buffer)
    client = create_app().test_client()

    response = client.post(
        "/api/inspect",
        data={"file": (BytesIO(buffer.getvalue()), "multi.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["needs_sheet_selection"] is True
    assert payload["sheet_names"] == ["Claims", "Other"]
    assert payload["columns"] == []


def test_analyze_rejects_repeated_mapping_columns_and_bad_amount() -> None:
    client = create_app().test_client()
    repeated = client.post(
        "/api/analyze",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
            "service_code_column": "Code",
            "amount_column": "Code",
        },
        content_type="multipart/form-data",
    )
    bad_amount = client.post(
        "/api/analyze",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
            "amount_column": "Amount",
            "maximum_amount": "not a limit",
        },
        content_type="multipart/form-data",
    )

    assert repeated.status_code == 400
    assert bad_amount.status_code == 400
    assert "разные столбцы" in repeated.get_json()["error"]
    assert "Лимит должен быть числом" in bad_amount.get_json()["error"]


def test_inspect_rejects_unsupported_or_missing_files() -> None:
    client = create_app().test_client()
    missing = client.post("/api/inspect", data={}, content_type="multipart/form-data")
    unsupported = client.post(
        "/api/inspect",
        data={"file": (BytesIO(b"data"), "synthetic.csv")},
        content_type="multipart/form-data",
    )

    assert missing.status_code == 400
    assert unsupported.status_code == 400
    assert "Выберите файл" in missing.get_json()["error"]
    assert ".xls и .xlsx" in unsupported.get_json()["error"]


def test_uploaded_workbook_is_removed_after_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import dms_pipeline.web as web

    created_directories: list[str] = []
    original_directory = web.tempfile.TemporaryDirectory

    def tracking_directory(*args, **kwargs):
        directory = original_directory(*args, dir=tmp_path, **kwargs)
        created_directories.append(directory.name)
        return directory

    monkeypatch.setattr(web.tempfile, "TemporaryDirectory", tracking_directory)

    response = create_app().test_client().post(
        "/api/inspect",
        data={
            "file": (BytesIO(_workbook_bytes()), "synthetic.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert created_directories
    assert all(not Path(directory).exists() for directory in created_directories)
