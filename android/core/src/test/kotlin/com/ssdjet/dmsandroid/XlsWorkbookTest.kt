package com.ssdjet.dmsandroid

import jxl.Workbook
import jxl.write.Label
import jxl.write.Number
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File
import java.nio.file.Files

class XlsWorkbookTest {
    @Test
    fun readsSyntheticXlsThroughSharedWorkbookApi() {
        withWorkbook { file ->
            val names = ExcelWorkbook.sheetNames(file)
            val headers = ExcelWorkbook.inspectHeaders(file, names.single(), 1)
            val table = ExcelWorkbook.read(file, names.single(), 1)

            assertEquals(listOf("Code", "Amount"), headers)
            assertEquals(listOf(2, 4), table.rows.map { it.sourceRowNumber })
            assertEquals("SYN-01", table.rows[0].values["Code"])
            assertEquals(25.0, table.rows[0].values["Amount"])
            assertEquals(31.0, table.rows[1].values["Amount"])
            val summary = BuiltInValidation.analyze(
                table,
                ColumnSelection(serviceCode = "Code", amount = "Amount"),
            )
            assertEquals(2, summary.rowsChecked)
            assertEquals(0, summary.fail)
            assertEquals(2, summary.notEvaluated)
        }
    }

    @Test
    fun rejectsDuplicateXlsHeaders() {
        withWorkbook(duplicateHeaders = true) { file ->
            val error = assertThrows(WorkbookFormatException::class.java) {
                ExcelWorkbook.inspectHeaders(file, "Synthetic", 1)
            }
            assertEquals("В книге есть повторяющиеся заголовки.", error.message)
        }
    }

    @Test
    fun rejectsInvalidHeaderRow() {
        withWorkbook { file ->
            val error = assertThrows(WorkbookFormatException::class.java) {
                ExcelWorkbook.inspectHeaders(file, "Synthetic", 0)
            }
            assertEquals("Номер строки заголовков должен быть больше нуля.", error.message)
        }
    }

    @Test
    fun rejectsUnsupportedOrCorruptWorkbookWithoutEchoingContents() {
        val file = Files.createTempFile("synthetic-dms-invalid-", ".xls").toFile()
        try {
            file.writeText("synthetic-not-an-excel-workbook")
            val error = assertThrows(WorkbookFormatException::class.java) {
                ExcelWorkbook.sheetNames(file)
            }
            assertEquals("Формат файла не поддерживается или книга повреждена.", error.message)
        } finally {
            file.delete()
        }
    }

    private fun withWorkbook(
        duplicateHeaders: Boolean = false,
        test: (File) -> Unit,
    ) {
        val file = Files.createTempFile("synthetic-dms-", ".xls").toFile()
        try {
            val workbook = Workbook.createWorkbook(file)
            try {
                val sheet = workbook.createSheet("Synthetic", 0)
                sheet.addCell(Label(0, 0, "Code"))
                sheet.addCell(Label(1, 0, if (duplicateHeaders) "code" else "Amount"))
                sheet.addCell(Label(0, 1, "SYN-01"))
                sheet.addCell(Number(1, 1, 25.0))
                sheet.addCell(Label(0, 3, "SYN-02"))
                sheet.addCell(Number(1, 3, 31.0))
                workbook.write()
            } finally {
                workbook.close()
            }
            test(file)
        } finally {
            file.delete()
        }
    }
}
