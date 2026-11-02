package com.ssdjet.dmsandroid

import jxl.Cell
import jxl.CellType
import jxl.DateCell
import jxl.NumberCell
import jxl.Workbook
import jxl.WorkbookSettings
import jxl.BooleanCell
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

internal object XlsWorkbook : WorkbookParser {
    private const val MAX_WORKSHEET_ROWS = 100_000
    private const val MAX_WORKSHEET_CELLS = 500_000

    override fun sheetNames(file: File): List<String> = open(file) { workbook ->
        workbook.sheetNames.toList().also {
            if (it.isEmpty()) throw WorkbookFormatException("В книге нет листов.")
        }
    }

    override fun inspectHeaders(
        file: File,
        sheetName: String,
        headerRow: Int,
    ): List<String> {
        validateHeaderRow(headerRow)
        return open(file) { workbook ->
            val sheet = findSheet(workbook, sheetName)
            validateSheetSize(sheet)
            makeHeaders(sheet, headerRow)
        }
    }

    override fun read(
        file: File,
        sheetName: String,
        headerRow: Int,
        skipEmptyRows: Boolean,
    ): WorkbookTable {
        validateHeaderRow(headerRow)
        return open(file) { workbook ->
            val sheet = findSheet(workbook, sheetName)
            validateSheetSize(sheet)
            val headers = makeHeaders(sheet, headerRow)
            val rows = ArrayList<WorkbookRow>()
            for (rowIndex in headerRow until sheet.rows) {
                val values = headers.indices.map { column ->
                    readCell(sheet.getCell(column, rowIndex))
                }
                if (skipEmptyRows && values.all(::isEmpty)) continue
                rows += WorkbookRow(
                    sourceRowNumber = rowIndex + 1,
                    values = headers.indices.associate { index ->
                        headers[index] to values[index]
                    },
                )
            }
            WorkbookTable(headers, rows)
        }
    }

    private fun makeHeaders(sheet: jxl.Sheet, headerRow: Int): List<String> {
        val index = headerRow - 1
        if (index >= sheet.rows || sheet.columns == 0) {
            throw WorkbookFormatException("Строка заголовков пуста или вне диапазона.")
        }
        val headers = (0 until sheet.columns).map { column ->
            val cell = sheet.getCell(column, index)
            if (cell.type != CellType.LABEL && cell.type != CellType.STRING_FORMULA) {
                throw WorkbookFormatException("Все заголовки должны быть непустым текстом.")
            }
            cell.contents.trim().takeIf(String::isNotEmpty)
                ?: throw WorkbookFormatException("Все заголовки должны быть непустым текстом.")
        }
        if (headers.map { it.lowercase(Locale.ROOT) }.toSet().size != headers.size) {
            throw WorkbookFormatException("В книге есть повторяющиеся заголовки.")
        }
        return headers
    }

    private fun readCell(cell: Cell): Any? = when (cell.type) {
        CellType.EMPTY -> null
        CellType.LABEL, CellType.STRING_FORMULA -> cell.contents
        CellType.NUMBER, CellType.NUMBER_FORMULA ->
            (cell as? NumberCell)?.value
                ?: throw WorkbookFormatException("Некорректное числовое значение в книге.")
        CellType.BOOLEAN, CellType.BOOLEAN_FORMULA ->
            (cell as? BooleanCell)?.value
                ?: throw WorkbookFormatException("Некорректное логическое значение в книге.")
        CellType.DATE, CellType.DATE_FORMULA ->
            (cell as? DateCell)?.date?.toInstant()?.let(::toLocalDate)
                ?: throw WorkbookFormatException("Некорректная дата в книге.")
        CellType.ERROR, CellType.FORMULA_ERROR ->
            throw WorkbookFormatException("Книга содержит ячейку с ошибкой.")
        else -> cell.contents.ifBlank { null }
    }

    private fun toLocalDate(instant: Instant): LocalDate =
        instant.atZone(ZoneId.systemDefault()).toLocalDate()

    private fun findSheet(workbook: Workbook, requestedName: String): jxl.Sheet =
        workbook.getSheet(requestedName)
            ?: throw WorkbookFormatException("Выбранный лист отсутствует в книге.")

    private fun <T> open(file: File, operation: (Workbook) -> T): T {
        if (!file.isFile) throw WorkbookFormatException("Не удалось открыть книгу.")
        val workbook = try {
            val settings = WorkbookSettings().apply { setSuppressWarnings(true) }
            Workbook.getWorkbook(file, settings)
        } catch (_: Exception) {
            throw WorkbookFormatException("Не удалось прочитать книгу .xls.")
        }
        return try {
            operation(workbook)
        } catch (error: WorkbookFormatException) {
            throw error
        } catch (_: Exception) {
            throw WorkbookFormatException("Не удалось прочитать книгу .xls.")
        } finally {
            workbook.close()
        }
    }

    private fun validateHeaderRow(headerRow: Int) {
        if (headerRow < 1) {
            throw WorkbookFormatException("Номер строки заголовков должен быть больше нуля.")
        }
    }

    private fun validateSheetSize(sheet: jxl.Sheet) {
        if (sheet.rows > MAX_WORKSHEET_ROWS ||
            sheet.columns.toLong() * sheet.rows.toLong() > MAX_WORKSHEET_CELLS
        ) {
            throw WorkbookFormatException("Книга превышает лимит строк или ячеек.")
        }
    }

    private fun isEmpty(value: Any?): Boolean =
        value == null || (value is String && value.isBlank())
}
