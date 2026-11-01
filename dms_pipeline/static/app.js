"use strict";

const form = document.querySelector("#analysis-form");
const fileInput = document.querySelector("#workbook-file");
const dropZone = document.querySelector("#drop-zone");
const fileName = document.querySelector("#file-name");
const fileMeta = document.querySelector("#file-meta");
const sheetSelect = document.querySelector("#sheet-name");
const headerRowInput = document.querySelector("#header-row");
const inspectButton = document.querySelector("#inspect-button");
const analyzeButton = document.querySelector("#analyze-button");
const columnSettings = document.querySelector("#column-settings");
const globalError = document.querySelector("#global-error");
const emptyResults = document.querySelector("#empty-results");
const resultsContent = document.querySelector("#results-content");
const resultsStatus = document.querySelector("#results-status");
const resultsSubtitle = document.querySelector("#results-subtitle");
const columnSelects = [
  document.querySelector("#service-code-column"),
  document.querySelector("#diagnosis-column"),
  document.querySelector("#amount-column"),
];

function showError(message) {
  globalError.textContent = message;
  globalError.hidden = false;
}

function clearError() {
  globalError.textContent = "";
  globalError.hidden = true;
}

function currentFile() {
  return fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
}

function makeUploadForm() {
  const file = currentFile();
  if (!file) {
    throw new Error("Сначала выберите файл Excel.");
  }
  const body = new FormData();
  body.append("file", file, file.name);
  body.append("header_row", headerRowInput.value || "1");
  body.append("sheet_name", sheetSelect.value || "");
  return body;
}

async function postForm(url, body) {
  const response = await fetch(url, { method: "POST", body });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("Сервер вернул неожиданный ответ.");
  }
  if (!response.ok) {
    throw new Error(payload.error || "Не удалось выполнить запрос.");
  }
  return payload;
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.dataset.originalLabel ??= button.textContent.trim();
  button.textContent = busy ? label : button.dataset.originalLabel;
}

function updateFileState() {
  clearError();
  const file = currentFile();
  populateSheets([], null);
  fileName.textContent = file ? file.name : "Выберите файл или перетащите сюда";
  fileMeta.textContent = file
    ? `Выбран файл · ${(file.size / (1024 * 1024)).toFixed(2)} МБ`
    : "Сначала выберите файл, затем нажмите «Считать столбцы».";
  inspectButton.disabled = !file;
  columnSettings.hidden = true;
  resultsContent.hidden = true;
  emptyResults.hidden = false;
  resultsStatus.textContent = "Ожидание";
  resultsStatus.className = "status-chip status-idle";
  resultsSubtitle.textContent = "Настройте поля слева и запустите проверку";
}

function setOptions(select, columns) {
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Не выбрано";
  select.replaceChildren(placeholder);
  for (const column of columns) {
    const option = document.createElement("option");
    option.value = column;
    option.textContent = column;
    select.append(option);
  }
}

function populateSheets(sheetNames, selectedName) {
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent =
    sheetNames.length > 1 ? "Выберите лист" : "Определился автоматически";
  sheetSelect.replaceChildren(placeholder);
  for (const name of sheetNames) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    sheetSelect.append(option);
  }
  sheetSelect.disabled = sheetNames.length <= 1;
  if (selectedName) {
    sheetSelect.value = selectedName;
  }
}

async function inspectWorkbook() {
  clearError();
  setBusy(inspectButton, true, "Читаю заголовки…");
  try {
    const payload = await postForm("/api/inspect", makeUploadForm());
    populateSheets(payload.sheet_names || [], payload.sheet_name);
    if (payload.needs_sheet_selection) {
      columnSettings.hidden = true;
      fileMeta.textContent = "В книге несколько листов. Выберите нужный лист и повторите.";
      sheetSelect.disabled = false;
      return;
    }
    for (const select of columnSelects) {
      setOptions(select, payload.columns);
    }
    columnSettings.hidden = false;
    const sheetLabel = payload.sheet_name ? `Лист «${payload.sheet_name}»` : "Лист выбран";
    fileMeta.textContent = `${sheetLabel} · ${payload.columns.length} столбцов · ${payload.row_count} строк`;
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(inspectButton, false);
    inspectButton.disabled = !currentFile();
  }
}

function textCell(text, className = "") {
  const cell = document.createElement("td");
  if (className) {
    cell.className = className;
  }
  cell.textContent = text;
  return cell;
}

function renderRules(rules) {
  const body = document.querySelector("#rules-body");
  body.replaceChildren();
  for (const rule of rules) {
    const row = document.createElement("tr");
    row.append(
      textCell(rule.label),
      textCell(String(rule.pass), "metric-cell"),
      textCell(String(rule.fail), "metric-cell"),
      textCell(String(rule.not_evaluated), "metric-cell"),
    );
    body.append(row);
  }
}

function renderFindings(payload) {
  const body = document.querySelector("#findings-body");
  const noFindings = document.querySelector("#no-findings");
  const table = body.closest(".table-wrap");
  body.replaceChildren();
  document.querySelector("#findings-count").textContent = String(
    payload.findings_total,
  );
  document.querySelector("#truncated-note").hidden = !payload.findings_truncated;
  noFindings.hidden = payload.findings.length > 0;
  table.hidden = payload.findings.length === 0;
  for (const item of payload.findings) {
    const row = document.createElement("tr");
    const status = document.createElement("span");
    status.className = `row-status ${item.status}`;
    status.textContent = item.status === "fail" ? "Сработка" : "Не оценено";
    row.append(
      textCell(String(item.row_number), "metric-cell"),
      textCell(""),
    );
    row.cells[1].append(status);
    const details = document.createElement("td");
    const list = document.createElement("div");
    list.className = "finding-row";
    for (const finding of item.findings) {
      if (finding.status === "pass") {
        continue;
      }
      const line = document.createElement("span");
      line.className = `finding-item ${finding.status}`;
      line.textContent = `${finding.label}: ${finding.message}`;
      list.append(line);
    }
    details.append(list);
    row.append(details);
    body.append(row);
  }
}

function renderResults(payload) {
  const summary = payload.summary;
  document.querySelector("#count-total").textContent = String(payload.rows_checked);
  document.querySelector("#count-pass").textContent = String(summary.pass);
  document.querySelector("#count-fail").textContent = String(summary.fail);
  document.querySelector("#count-unchecked").textContent = String(
    summary.not_evaluated,
  );
  renderRules(payload.rules);
  renderFindings(payload);
  emptyResults.hidden = true;
  resultsContent.hidden = false;
  const hasFailures = summary.fail > 0;
  const hasUnevaluated = summary.not_evaluated > 0;
  resultsStatus.textContent = hasFailures
    ? "Есть сработки"
    : hasUnevaluated
      ? "Есть неполные проверки"
      : "Готово";
  resultsStatus.className = `status-chip ${
    hasFailures ? "status-fail" : hasUnevaluated ? "status-warn" : "status-pass"
  }`;
  resultsSubtitle.textContent = `${payload.rows_checked} строк · лист «${payload.sheet_name}» · SHA-256 ${payload.source_sha256.slice(0, 12)}`;
}

async function analyzeWorkbook(event) {
  event.preventDefault();
  clearError();
  setBusy(analyzeButton, true, "Выполняю проверки…");
  try {
    const body = makeUploadForm();
    for (const select of columnSelects) {
      body.append(select.name, select.value);
    }
    body.append("service_codes", document.querySelector("#service-codes").value);
    body.append("diagnosis_codes", document.querySelector("#diagnosis-codes").value);
    body.append("maximum_amount", document.querySelector("#maximum-amount").value);
    const payload = await postForm("/api/analyze", body);
    renderResults(payload);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(analyzeButton, false, "");
  }
}

fileInput.addEventListener("change", updateFileState);
inspectButton.addEventListener("click", inspectWorkbook);
form.addEventListener("submit", analyzeWorkbook);
headerRowInput.addEventListener("change", () => {
  columnSettings.hidden = true;
});
sheetSelect.addEventListener("change", () => {
  columnSettings.hidden = true;
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("drag-over");
  const [file] = event.dataTransfer.files;
  if (!file) {
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  updateFileState();
});
