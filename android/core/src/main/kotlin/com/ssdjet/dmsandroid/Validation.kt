package com.ssdjet.dmsandroid

import java.math.BigDecimal

enum class RuleStatus {
    PASS,
    FAIL,
    NOT_EVALUATED,
}

data class RuleCount(
    val ruleId: String,
    val pass: Int,
    val fail: Int,
    val notEvaluated: Int,
)

data class AnalysisSummary(
    val rowsChecked: Int,
    val pass: Int,
    val fail: Int,
    val notEvaluated: Int,
    val rules: List<RuleCount>,
)

data class ColumnSelection(
    val serviceCode: String? = null,
    val diagnosis: String? = null,
    val amount: String? = null,
)

data class ValidationOptions(
    val serviceCodes: Set<String> = emptySet(),
    val diagnosisCodes: Set<String> = emptySet(),
    val maximumAmount: BigDecimal? = null,
)

object BuiltInValidation {
    fun analyze(
        table: WorkbookTable,
        columns: ColumnSelection,
        options: ValidationOptions = ValidationOptions(),
    ): AnalysisSummary {
        val selected = listOfNotNull(columns.serviceCode, columns.diagnosis, columns.amount)
        require(selected.isNotEmpty()) { "Выберите хотя бы одно поле для проверки." }
        require(selected.size == selected.toSet().size) {
            "Для разных полей выберите разные столбцы."
        }
        require(selected.all { it in table.headers }) { "Выбранный столбец отсутствует." }

        val ruleIds = buildList {
            if (columns.serviceCode != null) {
                add("service_code_present")
                add("service_code_reference")
            }
            if (columns.diagnosis != null) {
                add("diagnosis_present")
                add("diagnosis_reference")
            }
            if (columns.amount != null) {
                add("amount_numeric")
                add("amount_maximum")
            }
        }
        val ruleCounts = ruleIds.associateWith { IntArray(RuleStatus.entries.size) }
        var pass = 0
        var fail = 0
        var notEvaluated = 0

        for (row in table.rows) {
            val results = ArrayList<RuleStatus>(ruleIds.size)
            columns.serviceCode?.let { field ->
                val value = row.values[field]
                results += if (present(value)) RuleStatus.PASS else RuleStatus.FAIL
                results += when {
                    options.serviceCodes.isEmpty() -> RuleStatus.NOT_EVALUATED
                    value !is String -> RuleStatus.FAIL
                    value in options.serviceCodes -> RuleStatus.PASS
                    else -> RuleStatus.FAIL
                }
            }
            columns.diagnosis?.let { field ->
                val value = row.values[field]
                results += if (present(value)) RuleStatus.PASS else RuleStatus.FAIL
                results += when {
                    options.diagnosisCodes.isEmpty() -> RuleStatus.NOT_EVALUATED
                    value !is String -> RuleStatus.FAIL
                    value in options.diagnosisCodes -> RuleStatus.PASS
                    else -> RuleStatus.FAIL
                }
            }
            columns.amount?.let { field ->
                val value = row.values[field]
                val amount = asDecimal(value)
                results += if (amount == null) RuleStatus.FAIL else RuleStatus.PASS
                results += when {
                    options.maximumAmount == null -> RuleStatus.NOT_EVALUATED
                    amount == null -> RuleStatus.FAIL
                    amount <= options.maximumAmount -> RuleStatus.PASS
                    else -> RuleStatus.FAIL
                }
            }

            for ((index, status) in results.withIndex()) {
                val counts = ruleCounts.getValue(ruleIds[index])
                counts[status.ordinal]++
            }
            when {
                RuleStatus.FAIL in results -> fail++
                results.isEmpty() || RuleStatus.NOT_EVALUATED in results -> notEvaluated++
                else -> pass++
            }
        }

        return AnalysisSummary(
            rowsChecked = table.rows.size,
            pass = pass,
            fail = fail,
            notEvaluated = notEvaluated,
            rules = ruleIds.map { id ->
                val counts = ruleCounts.getValue(id)
                RuleCount(id, counts[0], counts[1], counts[2])
            },
        )
    }

    private fun present(value: Any?): Boolean =
        value != null && !(value is String && value.isBlank())

    private fun asDecimal(value: Any?): BigDecimal? {
        if (value !is Number) return null
        val number = value.toDouble()
        if (!number.isFinite()) return null
        return runCatching { BigDecimal(value.toString()) }.getOrNull()
    }
}
