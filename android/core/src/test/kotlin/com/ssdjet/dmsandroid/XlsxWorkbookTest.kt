package com.ssdjet.dmsandroid

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class XlsxWorkbookTest {
    @Test
    fun readsSyntheticWorkbookAndSkipsEmptyRows() {
        withWorkbook { file ->
            val names = ExcelWorkbook.sheetNames(file)
            val headers = ExcelWorkbook.inspectHeaders(file, names.single(), 1)
            val data = ExcelWorkbook.read(file, names.single(), 1)

            assertEquals(listOf("Code", "Amount"), headers)
            assertEquals(listOf(2, 4), data.rows.map { it.sourceRowNumber })
            assertEquals("SYN-01", data.rows[0].values["Code"])
            assertEquals(25.0, data.rows[0].values["Amount"])
            assertEquals(31.0, data.rows[1].values["Amount"])
        }
    }

    @Test
    fun rejectsDuplicateHeadersWithoutReturningCellValues() {
        withWorkbook(duplicateHeaders = true) { file ->
            val error = assertThrows(WorkbookFormatException::class.java) {
                XlsxWorkbook.inspectHeaders(file, "Synthetic", 1)
            }
            assertEquals("В книге есть повторяющиеся заголовки.", error.message)
        }
    }

    @Test
    fun readsOnlyCachedFormulaValues() {
        withWorkbook(cachedFormula = true) { file ->
            val data = XlsxWorkbook.read(file, "Synthetic", 1)
            assertEquals(25.0, data.rows.first().values["Amount"])
        }
    }

    private fun withWorkbook(
        duplicateHeaders: Boolean = false,
        cachedFormula: Boolean = false,
        test: (File) -> Unit,
    ) {
        val file = Files.createTempFile("synthetic-dms-", ".xlsx").toFile()
        try {
            ZipOutputStream(FileOutputStream(file)).use { zip ->
                zipEntry(
                    zip,
                    "xl/workbook.xml",
                    """<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>""",
                )
                zipEntry(
                    zip,
                    "xl/_rels/workbook.xml.rels",
                    """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
                )
                val secondHeader = if (duplicateHeaders) {
                    """<c r="B1" t="inlineStr"><is><t>code</t></is></c>"""
                } else {
                    """<c r="B1" t="inlineStr"><is><t>Amount</t></is></c>"""
                }
                val formulaCell = if (cachedFormula) {
                    """<c r="B2"><f>20+5</f><v>25</v></c>"""
                } else {
                    """<c r="B2"><v>25</v></c>"""
                }
                zipEntry(
                    zip,
                    "xl/worksheets/sheet1.xml",
                    """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
                        <row r="1"><c r="A1" t="inlineStr"><is><t>Code</t></is></c>$secondHeader</row>
                        <row r="2"><c r="A2" t="inlineStr"><is><t>SYN-01</t></is></c>$formulaCell</row>
                        <row r="3"><c r="A3" t="inlineStr"><is><t xml:space="preserve">   </t></is></c></row>
                        <row r="4"><c r="A4" t="inlineStr"><is><t>SYN-02</t></is></c><c r="B4"><v>31</v></c></row>
                    </sheetData></worksheet>""".trimIndent(),
                )
            }
            test(file)
        } finally {
            file.delete()
        }
    }

    private fun zipEntry(zip: ZipOutputStream, path: String, content: String) {
        zip.putNextEntry(ZipEntry(path))
        zip.write(content.toByteArray(Charsets.UTF_8))
        zip.closeEntry()
    }
}
