package com.ssdjet.dmsandroid

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.math.BigDecimal

class BuiltInValidationTest {
    @Test
    fun unsetReferencesAndMaximumRemainNotEvaluated() {
        val summary = BuiltInValidation.analyze(
            table(
                listOf(
                    row("SVC-01", "DX-01", 25.0),
                    row("SVC-02", "DX-02", 30.0),
                ),
            ),
            ColumnSelection("Code", "Diagnosis", "Amount"),
        )

        assertEquals(0, summary.pass)
        assertEquals(0, summary.fail)
        assertEquals(2, summary.notEvaluated)
        assertEquals(2, summary.rules[1].notEvaluated)
        assertEquals(2, summary.rules.last().notEvaluated)
    }

    @Test
    fun explicitReferencesAndInclusiveMaximumAreAppliedExactly() {
        val summary = BuiltInValidation.analyze(
            table(
                listOf(
                    row("SVC-01", "DX-01", 30.0),
                    row("SVC-02", "DX-01", 31.0),
                ),
            ),
            ColumnSelection("Code", "Diagnosis", "Amount"),
            ValidationOptions(
                serviceCodes = setOf("SVC-01"),
                diagnosisCodes = setOf("DX-01"),
                maximumAmount = BigDecimal("30"),
            ),
        )

        assertEquals(1, summary.pass)
        assertEquals(1, summary.fail)
        assertEquals(0, summary.notEvaluated)
        assertEquals(1, summary.rules.first { it.ruleId == "service_code_reference" }.fail)
    }

    @Test
    fun failureTakesPrecedenceAndResultsContainNoSourceValues() {
        val summary = BuiltInValidation.analyze(
            table(listOf(row("SENSITIVE-CODE", "DX-01", "not numeric"))),
            ColumnSelection("Code", null, "Amount"),
            ValidationOptions(serviceCodes = setOf("APPROVED")),
        )

        assertEquals(1, summary.fail)
        assertEquals(0, summary.notEvaluated)
        assertFalse(summary.toString().contains("SENSITIVE-CODE"))
    }

    @Test
    fun requiresDistinctSelectedColumns() {
        val error = runCatching {
            BuiltInValidation.analyze(
                table(emptyList()),
                ColumnSelection("Code", "Code", null),
            )
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    private fun table(rows: List<WorkbookRow>) =
        WorkbookTable(listOf("Code", "Diagnosis", "Amount"), rows)

    private fun row(code: Any?, diagnosis: Any?, amount: Any?) = WorkbookRow(
        sourceRowNumber = 2,
        values = mapOf("Code" to code, "Diagnosis" to diagnosis, "Amount" to amount),
    )
}
