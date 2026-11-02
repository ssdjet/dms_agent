package com.ssdjet.dmsandroid

import java.io.File
import java.io.InputStream

object ExcelWorkbook {
    private const val MAX_WORKBOOK_BYTES = 20L * 1024 * 1024
    private val xlsSignature = byteArrayOf(
        0xD0.toByte(), 0xCF.toByte(), 0x11, 0xE0.toByte(),
        0xA1.toByte(), 0xB1.toByte(), 0x1A, 0xE1.toByte(),
    )
    private val xlsxSignature = byteArrayOf(0x50, 0x4B, 0x03, 0x04)

    fun sheetNames(file: File): List<String> = withParser(file) { parser ->
        parser.sheetNames(file)
    }

    fun inspectHeaders(file: File, sheetName: String, headerRow: Int): List<String> =
        withParser(file) { parser -> parser.inspectHeaders(file, sheetName, headerRow) }

    fun read(
        file: File,
        sheetName: String,
        headerRow: Int,
        skipEmptyRows: Boolean = true,
    ): WorkbookTable = withParser(file) { parser ->
        parser.read(file, sheetName, headerRow, skipEmptyRows)
    }

    private fun <T> withParser(file: File, operation: (WorkbookParser) -> T): T {
        if (!file.isFile) throw WorkbookFormatException("Не удалось открыть книгу.")
        if (file.length() > MAX_WORKBOOK_BYTES) {
            throw WorkbookFormatException("Книга превышает размерный лимит.")
        }
        val signature = ByteArray(xlsSignature.size)
        val bytesRead = file.inputStream().use { input -> readSignature(input, signature) }
        val parser = when {
            bytesRead >= xlsSignature.size && signature.contentEquals(xlsSignature) ->
                XlsWorkbook
            bytesRead >= xlsxSignature.size &&
                signature.copyOf(xlsxSignature.size).contentEquals(xlsxSignature) ->
                XlsxWorkbook
            else -> throw WorkbookFormatException(
                "Формат файла не поддерживается или книга повреждена.",
            )
        }
        return operation(parser)
    }

    private fun readSignature(input: InputStream, destination: ByteArray): Int {
        var total = 0
        while (total < destination.size) {
            val read = input.read(destination, total, destination.size - total)
            if (read < 0) break
            if (read == 0) {
                val next = input.read()
                if (next < 0) break
                destination[total++] = next.toByte()
                continue
            }
            total += read
        }
        return total
    }
}

internal interface WorkbookParser {
    fun sheetNames(file: File): List<String>

    fun inspectHeaders(file: File, sheetName: String, headerRow: Int): List<String>

    fun read(
        file: File,
        sheetName: String,
        headerRow: Int,
        skipEmptyRows: Boolean = true,
    ): WorkbookTable
}
