package com.ssdjet.dmsandroid

import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.nio.file.Paths
import java.time.LocalDate
import java.util.Locale
import java.util.zip.ZipFile
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory

class WorkbookFormatException(message: String) : Exception(message)

data class WorkbookTable(
    val headers: List<String>,
    val rows: List<WorkbookRow>,
)

data class WorkbookRow(
    val sourceRowNumber: Int,
    val values: Map<String, Any?>,
)

object XlsxWorkbook : WorkbookParser {
    private const val WORKBOOK_PATH = "xl/workbook.xml"
    private const val RELATIONSHIPS_PATH = "xl/_rels/workbook.xml.rels"
    private const val SHARED_STRINGS_PATH = "xl/sharedStrings.xml"
    private const val STYLES_PATH = "xl/styles.xml"
    private const val MAX_XML_PART_BYTES = 16 * 1024 * 1024
    private const val MAX_WORKSHEET_ROWS = 100_000
    private const val MAX_WORKSHEET_CELLS = 500_000
    private const val MAIN_RELATIONSHIP_NS =
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    private val excelEpoch = LocalDate.of(1899, 12, 30)

    override fun sheetNames(file: File): List<String> = open(file) { zip ->
        workbookInfo(zip).map { it.first }
    }

    override fun inspectHeaders(file: File, sheetName: String, headerRow: Int): List<String> {
        validateHeaderRow(headerRow)
        return open(file) { zip ->
            val sheet = findSheet(zip, sheetName)
            val sharedStrings = readSharedStrings(zip)
            val styles = readDateStyles(zip)
            readHeaders(zip, sheet.second, headerRow, sharedStrings, styles)
        }
    }

    override fun read(
        file: File,
        sheetName: String,
        headerRow: Int,
        skipEmptyRows: Boolean,
    ): WorkbookTable {
        validateHeaderRow(headerRow)
        return open(file) { zip ->
            val sheet = findSheet(zip, sheetName)
            val sharedStrings = readSharedStrings(zip)
            val styles = readDateStyles(zip)
            val document = zip.getEntry(sheet.second)?.let { entry ->
                zip.getInputStream(entry).use(::parseXml)
            } ?: throw WorkbookFormatException("Не удалось прочитать лист.")
            val worksheetRows = document.getElementsByTagNameNS("*", "row")
            var maximumColumn = 0
            var totalCells = 0
            val rows = ArrayList<Pair<Int, Map<Int, Any?>>>()
            for (rowIndex in 0 until worksheetRows.length) {
                val rowElement = worksheetRows.item(rowIndex) as? Element ?: continue
                val rowNumber = rowElement.getAttribute("r").toIntOrNull()
                    ?: (rowIndex + 1)
                val cells = rowElement.getElementsByTagNameNS("*", "c")
                val values = HashMap<Int, Any?>()
                for (cellIndex in 0 until cells.length) {
                    val cell = cells.item(cellIndex) as? Element ?: continue
                    val column = columnIndex(cell.getAttribute("r"))
                    if (column < 0) {
                        throw WorkbookFormatException("Не удалось прочитать ячейку.")
                    }
                    maximumColumn = maxOf(maximumColumn, column + 1)
                    values[column] = readCell(cell, sharedStrings, styles)
                }
                if (rows.size >= MAX_WORKSHEET_ROWS ||
                    totalCells + values.size > MAX_WORKSHEET_CELLS
                ) {
                    throw WorkbookFormatException("Книга превышает лимит строк или ячеек.")
                }
                totalCells += values.size
                rows += rowNumber to values
            }
            val headers = makeHeaders(rows, maximumColumn, headerRow)
            val resultRows = ArrayList<WorkbookRow>()
            for ((rowNumber, cells) in rows) {
                if (rowNumber <= headerRow) continue
                val values = headers.indices.map { cells[it] }.toList()
                if (skipEmptyRows && values.all(::isEmpty)) continue
                resultRows += WorkbookRow(
                    sourceRowNumber = rowNumber,
                    values = headers.indices.associate { index ->
                        headers[index] to values[index]
                    },
                )
            }
            WorkbookTable(headers, resultRows)
        }
    }

    private fun <T> open(file: File, operation: (ZipFile) -> T): T {
        if (!file.isFile) throw WorkbookFormatException("Не удалось открыть книгу.")
        return try {
            ZipFile(file).use(operation)
        } catch (error: WorkbookFormatException) {
            throw error
        } catch (_: Exception) {
            throw WorkbookFormatException("Не удалось прочитать книгу .xlsx.")
        }
    }

    private fun validateHeaderRow(headerRow: Int) {
        if (headerRow < 1) {
            throw WorkbookFormatException("Номер строки заголовков должен быть больше нуля.")
        }
    }

    private fun workbookInfo(zip: ZipFile): List<Pair<String, String>> {
        val workbook = readXml(zip, WORKBOOK_PATH)
        val relationships = readXml(zip, RELATIONSHIPS_PATH)
        val targets = HashMap<String, String>()
        val relationshipNodes = relationships.getElementsByTagNameNS("*", "Relationship")
        for (index in 0 until relationshipNodes.length) {
            val relationship = relationshipNodes.item(index) as? Element ?: continue
            targets[relationship.getAttribute("Id")] =
                resolveWorkbookTarget(relationship.getAttribute("Target"))
        }
        val sheets = workbook.getElementsByTagNameNS("*", "sheet")
        return (0 until sheets.length).mapNotNull { index ->
            val element = sheets.item(index) as? Element ?: return@mapNotNull null
            val name = element.getAttribute("name")
            val relationshipId = element.getAttributeNS(MAIN_RELATIONSHIP_NS, "id")
            val target = targets[relationshipId]
            if (name.isBlank() || target.isNullOrBlank()) {
                throw WorkbookFormatException("Не удалось прочитать список листов.")
            }
            name to target
        }.also {
            if (it.isEmpty()) throw WorkbookFormatException("В книге нет листов.")
        }
    }

    private fun findSheet(zip: ZipFile, requestedName: String): Pair<String, String> {
        return workbookInfo(zip).firstOrNull { it.first == requestedName }
            ?: throw WorkbookFormatException("Выбранный лист отсутствует в книге.")
    }

    private fun resolveWorkbookTarget(target: String): String {
        if (target.isBlank()) throw WorkbookFormatException("Не удалось прочитать лист.")
        val candidate = if (target.startsWith("/")) {
            target.removePrefix("/")
        } else {
            "xl/$target"
        }
        val normalized = Paths.get(candidate).normalize().toString().replace('\\', '/')
        if (normalized.startsWith("../") || normalized == "..") {
            throw WorkbookFormatException("Некорректная ссылка на лист.")
        }
        return normalized
    }

    private fun readHeaders(
        zip: ZipFile,
        sheetPath: String,
        headerRow: Int,
        sharedStrings: List<String>,
        dateStyles: Set<Int>,
    ): List<String> {
        val document = zip.getEntry(sheetPath)?.let { entry ->
            zip.getInputStream(entry).use(::parseXml)
        } ?: throw WorkbookFormatException("Не удалось прочитать лист.")
        val rows = document.getElementsByTagNameNS("*", "row")
        var maximumColumn = 0
        var headerCells: Map<Int, Any?>? = null
        for (index in 0 until rows.length) {
            val row = rows.item(index) as? Element ?: continue
            val number = row.getAttribute("r").toIntOrNull() ?: (index + 1)
            val cells = row.getElementsByTagNameNS("*", "c")
            val values = HashMap<Int, Any?>()
            for (cellIndex in 0 until cells.length) {
                val cell = cells.item(cellIndex) as? Element ?: continue
                val column = columnIndex(cell.getAttribute("r"))
                if (column < 0) throw WorkbookFormatException("Некорректная строка заголовков.")
                maximumColumn = maxOf(maximumColumn, column + 1)
                if (number == headerRow) {
                    values[column] = readCell(cell, sharedStrings, dateStyles)
                }
            }
            if (number == headerRow) headerCells = values
        }
        return makeHeaders(
            listOf(headerRow to (headerCells ?: emptyMap())),
            maximumColumn,
            headerRow,
        )
    }

    private fun makeHeaders(
        rows: List<Pair<Int, Map<Int, Any?>>>,
        maximumColumn: Int,
        headerRow: Int,
    ): List<String> {
        if (maximumColumn == 0) {
            throw WorkbookFormatException("Строка заголовков пуста или вне диапазона.")
        }
        val values = rows.firstOrNull { it.first == headerRow }?.second
            ?: throw WorkbookFormatException("Строка заголовков пуста или вне диапазона.")
        val headers = (0 until maximumColumn).map { index ->
            (values[index] as? String)?.trim()?.takeIf(String::isNotEmpty)
                ?: throw WorkbookFormatException("Все заголовки должны быть непустым текстом.")
        }
        if (headers.map { it.lowercase(Locale.ROOT) }.toSet().size != headers.size) {
            throw WorkbookFormatException("В книге есть повторяющиеся заголовки.")
        }
        return headers
    }

    private fun readSharedStrings(zip: ZipFile): List<String> {
        val entry = zip.getEntry(SHARED_STRINGS_PATH) ?: return emptyList()
        val document = zip.getInputStream(entry).use(::parseXml)
        val items = document.getElementsByTagNameNS("*", "si")
        return (0 until items.length).map { index ->
            val item = items.item(index) as Element
            val textNodes = item.getElementsByTagNameNS("*", "t")
            buildString {
                for (textIndex in 0 until textNodes.length) {
                    append(textNodes.item(textIndex).textContent.orEmpty())
                }
            }
        }
    }

    private fun readDateStyles(zip: ZipFile): Set<Int> {
        val entry = zip.getEntry(STYLES_PATH) ?: return emptySet()
        val document = zip.getInputStream(entry).use(::parseXml)
        val customFormats = HashMap<Int, String>()
        val formatNodes = document.getElementsByTagNameNS("*", "numFmt")
        for (index in 0 until formatNodes.length) {
            val format = formatNodes.item(index) as? Element ?: continue
            val id = format.getAttribute("numFmtId").toIntOrNull() ?: continue
            customFormats[id] = format.getAttribute("formatCode")
        }
        val cellFormats = document.getElementsByTagNameNS("*", "cellXfs")
        if (cellFormats.length == 0) return emptySet()
        val formats = (cellFormats.item(0) as Element).getElementsByTagNameNS("*", "xf")
        return (0 until formats.length).mapNotNull { index ->
            val format = formats.item(index) as? Element ?: return@mapNotNull null
            val numberFormat = format.getAttribute("numFmtId").toIntOrNull() ?: return@mapNotNull null
            val builtInDate = numberFormat in 14..22 ||
                numberFormat in 27..36 ||
                numberFormat in 45..47 ||
                numberFormat in 50..58
            val customDate = customFormats[numberFormat]
                ?.replace(Regex("\"[^\"]*\""), "")
                ?.replace(Regex("\\\\."), "")
                ?.replace(Regex("\\[[^]]*]"), "")
                ?.contains(Regex("[ymd]", RegexOption.IGNORE_CASE)) == true
            if (builtInDate || customDate) index else null
        }.toSet()
    }

    private fun readCell(
        cell: Element,
        sharedStrings: List<String>,
        dateStyles: Set<Int>,
    ): Any? {
        val type = cell.getAttribute("t")
        val style = cell.getAttribute("s").toIntOrNull()
        val value = cell.getElementsByTagNameNS("*", "v").item(0)?.textContent
        return when (type) {
            "inlineStr" -> cell.getElementsByTagNameNS("*", "is").item(0)
                ?.let { element ->
                    (element as Element).getElementsByTagNameNS("*", "t").let { nodes ->
                        buildString {
                            for (index in 0 until nodes.length) append(nodes.item(index).textContent.orEmpty())
                        }
                    }
                }
            "s" -> value?.toIntOrNull()?.let { sharedStrings.getOrNull(it) }
                ?: throw WorkbookFormatException("Некорректное значение в книге.")
            "b" -> when (value) {
                "0" -> false
                "1" -> true
                else -> null
            }
            "str" -> value
            "e" -> throw WorkbookFormatException("Книга содержит ячейку с ошибкой.")
            else -> {
                if (value.isNullOrBlank()) null
                else {
                    val number = value.toDoubleOrNull()
                        ?: throw WorkbookFormatException("Некорректное числовое значение в книге.")
                    if (style in dateStyles && number.isFinite()) {
                        excelEpoch.plusDays(number.toLong())
                    } else {
                        number
                    }
                }
            }
        }
    }

    private fun columnIndex(reference: String): Int {
        var index = 0
        var hasLetters = false
        for (character in reference) {
            if (!character.isLetter()) break
            val upper = character.uppercaseChar()
            if (upper !in 'A'..'Z') return -1
            hasLetters = true
            index = index * 26 + (upper - 'A' + 1)
        }
        return if (hasLetters) index - 1 else -1
    }

    private fun isEmpty(value: Any?): Boolean =
        value == null || (value is String && value.isBlank())

    private fun readXml(zip: ZipFile, path: String): Document {
        val entry = zip.getEntry(path)
            ?: throw WorkbookFormatException("В книге отсутствует обязательный файл.")
        return zip.getInputStream(entry).use(::parseXml)
    }

    private fun parseXml(input: InputStream): Document {
        val xml = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        var total = 0
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            total += count
            if (total > MAX_XML_PART_BYTES) {
                throw WorkbookFormatException("Часть книги превышает допустимый размер.")
            }
            xml.write(buffer, 0, count)
        }
        val factory = DocumentBuilderFactory.newInstance()
        factory.isNamespaceAware = true
        factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
        factory.setFeature("http://xml.org/sax/features/external-general-entities", false)
        factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false)
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "")
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
        return factory.newDocumentBuilder().parse(ByteArrayInputStream(xml.toByteArray()))
    }
}
