package com.ssdjet.dmsandroid

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import java.io.File
import java.io.FileOutputStream
import java.math.BigDecimal

class MainActivity : Activity() {
    private lateinit var root: LinearLayout
    private lateinit var workbookLabel: TextView
    private lateinit var message: TextView
    private lateinit var sheetPicker: Spinner
    private lateinit var headerRowInput: EditText
    private lateinit var inspectButton: Button
    private lateinit var mappingSection: LinearLayout
    private lateinit var serviceColumn: Spinner
    private lateinit var diagnosisColumn: Spinner
    private lateinit var amountColumn: Spinner
    private lateinit var serviceCodesInput: EditText
    private lateinit var diagnosisCodesInput: EditText
    private lateinit var maximumInput: EditText
    private lateinit var analyzeButton: Button
    private lateinit var results: TextView

    private var workbookFile: File? = null
    private var isBusy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        clearStaleWorkbookCopies()
        buildScreen()
    }

    override fun onDestroy() {
        workbookFile?.delete()
        workbookFile = null
        super.onDestroy()
    }

    @Deprecated("Uses the system document picker without broad storage permissions.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_OPEN_WORKBOOK || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        setBusy(true)
        clearWorkbook()
        message.text = "Читаю книгу локально…"
        Thread {
            var temporary: File? = null
            try {
                val copiedWorkbook = copyToPrivateCache(uri)
                temporary = copiedWorkbook
                val availableSheets = ExcelWorkbook.sheetNames(copiedWorkbook)
                runOnUiThread {
                    workbookFile = copiedWorkbook
                    temporary = null
                    workbookLabel.text = "Книга выбрана · листов: ${availableSheets.size}"
                    setSpinnerOptions(sheetPicker, availableSheets)
                    mappingSection.visibility = View.GONE
                    results.text = ""
                    headerRowInput.setText("1")
                    serviceCodesInput.setText("")
                    diagnosisCodesInput.setText("")
                    maximumInput.setText("")
                    message.text = "Выберите лист и укажите строку заголовков."
                    setBusy(false)
                }
            } catch (_: Exception) {
                temporary?.delete()
                runOnUiThread {
                    message.text = "Не удалось открыть книгу. Выберите файл .xls или .xlsx."
                    setBusy(false)
                }
            }
        }.start()
    }

    private fun buildScreen() {
        val scroll = ScrollView(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(24))
        }
        scroll.addView(root)
        setContentView(scroll)

        addText("Локальная проверка реестра", 24f, bold = true)
        addText(
            "Проверки выполняются только на устройстве. Это не решение о страховом покрытии.",
            15f,
        )
        addButton("Лицензия парсера .xls") { showXlsLicense() }
        addButton("Выбрать книгу .xls или .xlsx") { chooseWorkbook() }
        workbookLabel = addText("Книга не выбрана", 14f)
        addText("Лист", 16f, bold = true)
        sheetPicker = Spinner(this).also(root::addView)
        addText("Строка заголовков (начиная с 1)", 16f, bold = true)
        headerRowInput = addInput("1", number = true)
        inspectButton = addButton("Считать столбцы") { inspectWorkbook() }
        message = addText("", 14f)

        mappingSection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        root.addView(mappingSection)
        addTo(mappingSection, "Поля и правила", 20f, bold = true)
        addTo(mappingSection, "Код услуги", 16f, bold = true)
        serviceColumn = spinnerIn(mappingSection)
        serviceCodesInput = inputIn(
            mappingSection,
            "Справочник кодов услуг: по одному значению на строку",
            multiline = true,
        )
        addTo(mappingSection, "Диагноз", 16f, bold = true)
        diagnosisColumn = spinnerIn(mappingSection)
        diagnosisCodesInput = inputIn(
            mappingSection,
            "Справочник диагнозов: по одному значению на строку",
            multiline = true,
        )
        addTo(mappingSection, "Сумма", 16f, bold = true)
        amountColumn = spinnerIn(mappingSection)
        maximumInput = inputIn(mappingSection, "Максимум (необязательно)", multiline = false)
        addTo(
            mappingSection,
            "Пустой справочник или лимит означает «не оценено», а не одобрение.",
            14f,
        )
        analyzeButton = addButtonIn(mappingSection, "Запустить проверки") { analyzeWorkbook() }
        results = addTo(mappingSection, "", 15f)
    }

    private fun chooseWorkbook() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(
                Intent.EXTRA_MIME_TYPES,
                arrayOf(
                    "application/vnd.ms-excel",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        }
        startActivityForResult(intent, REQUEST_OPEN_WORKBOOK)
    }

    private fun showXlsLicense() {
        val license = assets.open("third_party_licenses/LGPL-2.1.txt")
            .bufferedReader()
            .use { it.readText() }
        val content = ScrollView(this).apply {
            addView(TextView(this@MainActivity).apply {
                text = license
                setPadding(dp(16), dp(12), dp(16), dp(12))
            })
        }
        AlertDialog.Builder(this)
            .setTitle("JExcelAPI 2.6.12 — LGPL")
            .setView(content)
            .setPositiveButton("Закрыть", null)
            .show()
    }

    private fun inspectWorkbook() {
        val file = workbookFile ?: return showMessage("Сначала выберите книгу.")
        val headerRow = headerRowInput.text.toString().toIntOrNull()
        if (headerRow == null || headerRow < 1) {
            return showMessage("Введите положительный номер строки заголовков.")
        }
        val selectedSheet = sheetPicker.selectedItem as? String
            ?: return showMessage("Выберите лист книги.")
        setBusy(true)
        message.text = "Читаю заголовки…"
        Thread {
            try {
                val foundHeaders = ExcelWorkbook.inspectHeaders(file, selectedSheet, headerRow)
                runOnUiThread {
                    val options = listOf(NO_COLUMN) + foundHeaders
                    listOf(serviceColumn, diagnosisColumn, amountColumn).forEach {
                        setSpinnerOptions(it, options)
                    }
                    mappingSection.visibility = View.VISIBLE
                    results.text = ""
                    message.text = "Найдено столбцов: ${foundHeaders.size}"
                    setBusy(false)
                }
            } catch (_: Exception) {
                runOnUiThread {
                    message.text = "Не удалось прочитать заголовки. Проверьте лист и строку."
                    setBusy(false)
                }
            }
        }.start()
    }

    private fun analyzeWorkbook() {
        val file = workbookFile ?: return showMessage("Сначала выберите книгу.")
        val selectedSheet = sheetPicker.selectedItem as? String
            ?: return showMessage("Выберите лист книги.")
        val headerRow = headerRowInput.text.toString().toIntOrNull()
            ?: return showMessage("Введите положительный номер строки заголовков.")
        val columns = ColumnSelection(
            serviceCode = selectedColumn(serviceColumn),
            diagnosis = selectedColumn(diagnosisColumn),
            amount = selectedColumn(amountColumn),
        )
        val maximumText = maximumInput.text.toString().trim()
        val maximum = if (maximumText.isEmpty()) {
            null
        } else {
            runCatching {
                BigDecimal(maximumText.replace(',', '.'))
            }.getOrNull() ?: return showMessage("Лимит должен быть конечным числом.")
        }
        val options = ValidationOptions(
            serviceCodes = parseLines(serviceCodesInput.text.toString()),
            diagnosisCodes = parseLines(diagnosisCodesInput.text.toString()),
            maximumAmount = maximum,
        )
        setBusy(true)
        results.text = "Выполняю проверки локально…"
        Thread {
            try {
                val table = ExcelWorkbook.read(file, selectedSheet, headerRow)
                val summary = BuiltInValidation.analyze(table, columns, options)
                val safeText = formatSummary(summary)
                runOnUiThread {
                    results.text = safeText
                    message.text = "Проверка завершена. Исходная книга не изменялась."
                    clearWorkbook()
                    mappingSection.visibility = View.VISIBLE
                    setBusy(false)
                }
            } catch (error: IllegalArgumentException) {
                runOnUiThread {
                    results.text = ""
                    message.text = error.message ?: "Проверьте выбранные поля."
                    setBusy(false)
                }
            } catch (_: Exception) {
                runOnUiThread {
                    results.text = ""
                    message.text = "Не удалось обработать книгу. Проверьте формат и настройки."
                    clearWorkbook()
                    mappingSection.visibility = View.GONE
                    setBusy(false)
                }
            }
        }.start()
    }

    private fun formatSummary(summary: AnalysisSummary): String = buildString {
        appendLine("Всего строк: ${summary.rowsChecked}")
        appendLine("Пройдены: ${summary.pass}")
        appendLine("Есть ошибки: ${summary.fail}")
        appendLine("Не оценены: ${summary.notEvaluated}")
        if (summary.rules.isNotEmpty()) {
            appendLine()
            appendLine("По правилам (прошло / ошибки / не оценено):")
            summary.rules.forEach { rule ->
                appendLine(
                    "${ruleLabel(rule.ruleId)}: ${rule.pass} / ${rule.fail} / ${rule.notEvaluated}",
                )
            }
        }
        appendLine()
        append("Покрытие, клинические показания и медицинская необходимость не оценивались.")
    }

    private fun ruleLabel(ruleId: String): String = when (ruleId) {
        "service_code_present" -> "Код услуги заполнен"
        "service_code_reference" -> "Код услуги в справочнике"
        "diagnosis_present" -> "Диагноз заполнен"
        "diagnosis_reference" -> "Диагноз в справочнике"
        "amount_numeric" -> "Сумма указана числом"
        "amount_maximum" -> "Сумма не превышает лимит"
        else -> "Техническая проверка"
    }

    private fun parseLines(text: String): Set<String> =
        text.lineSequence().map(String::trim).filter(String::isNotEmpty).toSet()

    private fun selectedColumn(spinner: Spinner): String? =
        (spinner.selectedItem as? String)?.takeUnless { it == NO_COLUMN }

    private fun copyToPrivateCache(uri: Uri): File {
        val destination = File.createTempFile("dms-workbook-", ".tmp", cacheDir)
        try {
            val input = contentResolver.openInputStream(uri)
                ?: throw WorkbookFormatException("Не удалось открыть файл.")
            input.use { source ->
                FileOutputStream(destination).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    var total = 0L
                    while (true) {
                        val read = source.read(buffer)
                        if (read < 0) break
                        total += read
                        if (total > MAX_FILE_BYTES) {
                            throw WorkbookFormatException("Книга превышает размерный лимит.")
                        }
                        output.write(buffer, 0, read)
                    }
                }
            }
            return destination
        } catch (error: Exception) {
            destination.delete()
            throw error
        }
    }

    private fun clearWorkbook() {
        workbookFile?.delete()
        workbookFile = null
        workbookLabel.text = "Книга не выбрана"
        setSpinnerOptions(sheetPicker, emptyList())
    }

    private fun clearStaleWorkbookCopies() {
        cacheDir.listFiles { file ->
            file.isFile && file.name.startsWith("dms-workbook-") &&
                (file.name.endsWith(".tmp") || file.name.endsWith(".xlsx"))
        }?.forEach(File::delete)
    }

    private fun setBusy(busy: Boolean) {
        isBusy = busy
        inspectButton.isEnabled = !busy && workbookFile != null
        analyzeButton.isEnabled = !busy && workbookFile != null
    }

    private fun showMessage(text: String) {
        message.text = text
    }

    private fun setSpinnerOptions(spinner: Spinner, values: List<String>) {
        spinner.isSaveEnabled = false
        spinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_item,
            values,
        ).also { adapter ->
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        }
    }

    private fun addText(text: String, size: Float, bold: Boolean = false): TextView =
        addTo(root, text, size, bold)

    private fun addTo(
        parent: LinearLayout,
        text: String,
        size: Float,
        bold: Boolean = false,
    ): TextView = TextView(this).apply {
        this.text = text
        textSize = size
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(8), 0, dp(4))
        parent.addView(this)
    }

    private fun addButton(text: String, action: () -> Unit): Button =
        addButtonIn(root, text, action)

    private fun addButtonIn(
        parent: LinearLayout,
        text: String,
        action: () -> Unit,
    ): Button = Button(this).apply {
        this.text = text
        setOnClickListener { if (!isBusy) action() }
        parent.addView(this)
    }

    private fun addInput(hint: String, number: Boolean = false): EditText =
        inputIn(root, hint, multiline = false, number = number)

    private fun inputIn(
        parent: LinearLayout,
        hint: String,
        multiline: Boolean,
        number: Boolean = false,
    ): EditText = EditText(this).apply {
        this.hint = hint
        if (number) inputType = android.text.InputType.TYPE_CLASS_NUMBER
        if (multiline) {
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            gravity = Gravity.TOP or Gravity.START
        }
        isSaveEnabled = false
        parent.addView(
            this,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
    }

    private fun spinnerIn(parent: LinearLayout): Spinner =
        Spinner(this).also(parent::addView)

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val REQUEST_OPEN_WORKBOOK = 41
        private const val MAX_FILE_BYTES = 20L * 1024 * 1024
        private const val NO_COLUMN = "— не проверять —"
    }
}
