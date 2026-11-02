# Конфигурация, сопоставление и правила

## Конфигурация YAML

`config/sample.yml` намеренно не содержит доменных предположений:

```yaml
workbook:
  sheet_name: null
  header_row: 1
  required_columns: []
  skip_empty_rows: true

mapping:
  source_to_target: {}
  required_targets: []

rules:
  service_code_field: null
  service_codes: null
  diagnosis_field: null
  diagnosis_codes: null
  amount_field: null
  maximum_amount: null
  checks: []
```

Пример ниже — только синтетический формат конфигурации. Имена колонок,
коды и пределы нельзя использовать в реальной проверке без подтверждения
владельца данных и политики:

```yaml
workbook:
  sheet_name: "Synthetic cases"
  header_row: 1
  required_columns:
    - "Service code"
    - "Diagnosis code"
    - "Amount"
  skip_empty_rows: true

mapping:
  source_to_target:
    "Service code": service_code
    "Diagnosis code": diagnosis
    "Amount": amount
  required_targets:
    - service_code
    - diagnosis
    - amount

rules:
  service_code_field: service_code
  service_codes:
    - "SVC-001"
    - "SVC-002"
  diagnosis_field: diagnosis
  diagnosis_codes:
    - "DX-001"
    - "DX-002"
  amount_field: amount
  maximum_amount: 1000
```

Загрузить и применить конфигурацию из Python:

```python
from dms_pipeline.config import load_config
from dms_pipeline.audit import create_audit_event
from dms_pipeline.ingestion import read_workbook
from dms_pipeline.mapping import map_row
from dms_pipeline.rules import build_rules

config = load_config("config/example.yml")
workbook = read_workbook("synthetic.xlsx", config.workbook)
engine = build_rules(config.rules)
mapped_records = tuple(
    map_row(source_row.values, config.mapping)
    for source_row in workbook.rows
)
results_by_row = engine.evaluate_records(mapped_records)
for source_row, results in zip(workbook.rows, results_by_row):
    event = create_audit_event(
        workbook.sha256,
        source_row.row_number,
        results,
    )
```

Этот фрагмент показывает соединение модулей, но не добавляет файлового журнала
или решения о покрытии. Для каждого результата аудита вызывайте
`create_audit_event(workbook.sha256, source_row.row_number, results)`.

## Поддерживаемые параметры

### `workbook`

| Ключ | Тип | Значение |
|---|---|---|
| `sheet_name` | строка или `null` | Имя листа. Если листов больше одного, выберите лист явно. |
| `header_row` | целое число от 1 | Номер строки заголовков в исходном Excel (нумерация с 1). |
| `required_columns` | список строк | Заголовки, которые обязательно должны присутствовать. |
| `skip_empty_rows` | `true`/`false` | Пропускать ли строки, где все ячейки пустые или содержат только пробелы. |

Все заголовки листа должны быть непустым текстом; дубликаты без учёта регистра
ошибка. `required_columns` сравниваются с точным текстом заголовков с учётом
регистра.

### `mapping`

`source_to_target` — отображение точных названий исходных колонок на
стандартизованные имена. Имя целевого поля должно встречаться только один раз.
`required_targets` должны быть среди целевых имён отображения. Обязательное
значение считается отсутствующим, если оно `None` или пустая строка. Значения
в mapping не приводятся к другому типу и не очищаются автоматически.

### `rules`

| Ключ | Результат настройки |
|---|---|
| `service_code_field` | Создаёт проверку заполненности кода услуги. |
| `service_codes` | Включает точное сравнение с этим справочником, если он задан и не пуст. |
| `diagnosis_field` | Создаёт проверку заполненности диагноза. |
| `diagnosis_codes` | Включает точное сравнение диагноза с заданным непустым справочником. |
| `amount_field` | Создаёт проверку числового типа суммы. |
| `maximum_amount` | Включает проверку верхнего лимита, когда поле суммы и лимит заданы. |
| `checks` | Список расширенных явных правил для отдельных записей или всего набора. |

Результат по каждому правилу:

- **`pass`** — проверка выполнена и условие соблюдено.
- **`fail`** — проверка выполнена и условие нарушено.
- **`not_evaluated`** — для сравнения не настроен непустой справочник или лимит.

Если поле кода услуги/диагноза включено, движок создаёт и правило справочника.
Когда сам список не задан, правило возвращает `not_evaluated`. Если список
пустой, он также не означает «запретить всё» — такое правило не оценивается.
Сравнения точные, чувствительны к регистру и не нормализуют пробелы.

Числовая проверка принимает `int`, `float` и `Decimal`, исключая `bool`.
Верхний предел включён: значение, равное `maximum_amount`, проходит; выше
предела — не проходит. Значение должно быть конечным числом. Отрицательные
пределы не запрещены автоматически — допустимость задаётся политикой владельца.

Итоговый статус строки:

1. `fail`, если хотя бы одно правило вернуло `fail`;
2. иначе `not_evaluated`, если правил нет или хотя бы одно правило не было
   оценено;
3. иначе `pass`.

Таким образом, `not_evaluated` не считается ни `pass`, ни отказом в покрытии.

### Расширенные `rules.checks`

Каждое правило — YAML mapping с обязательными `id`, `kind` и, в зависимости от
вида, `fields`. `id` должен состоять из строчных латинских букв, цифр и `_`.
Идентификаторы уникальны и не должны повторять идентификаторы встроенных правил.
Неизвестные ключи и неподдерживаемые виды отклоняются при загрузке конфигурации.

| `kind` | Параметры | Семантика |
|---|---|---|
| `required` | `fields: [field]` | Значение не пустое. |
| `value_type` | `fields`, `expected_type: text/numeric/date` | Проверяет тип; `bool` не считается числом. |
| `pattern` | `fields`, `pattern` | Регулярное выражение применяется целиком (`fullmatch`). |
| `numeric_range` | `fields`, `minimum`, `maximum` | Включительные границы; задайте хотя бы одну. |
| `date_range` | `fields`, ISO `minimum`/`maximum`, необязательный `date_format` | Включительный диапазон. Без формата текстовые даты должны быть ISO. |
| `date_order` | `fields: [start, end]`, необязательный `strict` | Проверяет `start <= end`, либо `<`, если `strict: true`. |
| `allowed_values` / `denied_values` | `fields`, необязательный `values` | Сравнение точное и чувствительное к регистру; отсутствие/пустота списка — `not_evaluated`. |
| `allowed_pairs` | `fields: [left, right]`, `pairs: [[a, b], ...]` | Явный справочник допустимых сочетаний, например услуги и диагноза. |
| `program_services` | `fields: [program, service]`, `catalog` | Для программы задаёт явный список разрешённых услуг. |
| `active_period` | `fields: [key, date]`, `periods` | Сверяет дату со включительным периодом для ключа программы/договора. |
| `price_catalog` | `fields: [service, amount]`, `catalog` | Точное числовое сравнение с заданной ценой; тарифы не угадываются. |
| `unique` | `fields: [field, ...]` | Уникальность комбинации по всей книге; все одинаковые записи помечаются `fail`. Отсутствие значения отдельно проверяется `required`. |
| `reference_integrity` | `fields: [foreign, local]` | Первое поле каждой строки должно присутствовать среди значений второго поля в той же книге. |
| `sequence` | `fields: [group_field, ..., date]`, необязательный `strict` | Проверяет возрастание дат в исходном порядке строк внутри группы. |
| `count_limit` | `group_by`, `maximum` | Явно ограничивает число строк на группу; пустой `group_by` означает весь набор. |
| `frequency_limit` | `fields: [value, date]`, `group_by`, `period`, `maximum` | Явно ограничивает частоту значения по группе и дню/месяцу/году. Это порог, не статистический baseline. |
| `policy_dependent` | `policy` | Всегда `not_evaluated` с безопасным пояснением из фиксированного списка. |

Пример только синтетических значений:

```yaml
rules:
  checks:
    - id: service_code_reference
      kind: allowed_values
      fields: [service_code]
      values: [SYN-SVC-01, SYN-SVC-02]
    - id: service_diagnosis_pair
      kind: allowed_pairs
      fields: [service_code, diagnosis]
      pairs:
        - [SYN-SVC-01, SYN-DX-01]
    - id: service_program_membership
      kind: program_services
      fields: [program, service_code]
      catalog:
        SYN-PLAN: [SYN-SVC-01]
    - id: contract_active
      kind: active_period
      fields: [contract_id, service_date]
      periods:
        SYN-CONTRACT: [2025-01-01, 2025-12-31]
    - id: services_per_visit
      kind: count_limit
      group_by: [visit_id]
      maximum: 5
    - id: medical_indications
      kind: policy_dependent
      policy: medical_indications
```

Поддерживаемые ключи `policy`: `program_membership`, `medical_indications`,
`medical_necessity`, `quantity_norms`, `price_catalog`, `contract_period`,
`repeat_visit`, `anesthesia`, `coverage_exclusions`, `additional_services`,
`denial_justification`, `statistical_baseline`. Эти декларации нужны, чтобы
результат явно фиксировал отсутствие правил, а не означал прохождение проверки.
Нельзя менять фиксированные пояснения на сообщения с данными записей.

#### Как правила покрывают десять групп событий

| Группа событий | Реализуемое правило при явной конфигурации | Что остаётся `not_evaluated` без источника |
|---|---|---|
| Программа страхования | `program_services`, `active_period` | Условия участия и периоды, не внесённые в catalog. |
| Медицинские данные | `required`, `allowed_values`, `allowed_pairs` | Клинические показания и медицинская необходимость. |
| Финансы | `numeric_range`, `count_limit`, `price_catalog` | Нормативы количества и тарифы, пока их не предоставили. |
| Время | `date_range`, `date_order`, `sequence`, `active_period` | Срок договора/программы без явных дат. |
| Целостность данных | `required`, `unique`, `reference_integrity`, `pattern` | Нельзя определить обязательность неизвестного поля автоматически. |
| Особые условия | `count_limit`, `frequency_limit` | Обоснованность повторного приёма и анестезии. |
| Исключения и ограничения | `denied_values`, `allowed_values` | Неуказанные исключения и критерии отказа. |
| Статистика | `frequency_limit`, `numeric_range` с заданными порогами | Автоматическое обучение baseline и аномалии без согласованного порога. |
| Форматы | `value_type`, `pattern`, числовые/датовые проверки | Справочник кодов без заданных значений. |
| Системные параметры | `unique`, `reference_integrity`, `pattern` | Смысл идентификаторов без правил источника. |

Сетевые справочники, автоматически выводимые пороги и клинические эвристики
не используются. Пакетные проверки требуют `RuleEngine.evaluate_records()`;
`evaluate(record)` для такой проверки возвращает `not_evaluated`, так как одной
строки недостаточно. Веб-форма пока поддерживает только встроенные проверки
кода услуги, диагноза и суммы; расширенные `checks` предназначены для YAML/API
интеграции через Python-пакет.

Пустые справочники/периоды не интерпретируются как запрет всего: проверка
возвращает `not_evaluated`. Отсутствующий ключ в непустом настроенном каталоге
считается `fail`. Отсутствие значения поля для проверки уникальности следует
проверять отдельным правилом `required`.

## Связь конфига и интерфейса

Веб-интерфейс пока получает настройки из формы, а не загружает YAML:

- пользователь выбирает колонки для стандартных целей `service_code`,
  `diagnosis` и `amount`;
- строки кодов вводятся по одной на строку;
- лимит передаётся как десятичное число; интерфейс принимает точку или запятую
  как десятичный разделитель;
- эти настройки применяются только к одному запросу.

YAML — стабильная точка входа для пакетного или интегрированного использования;
в интерфейсе пока не реализовано сохранение конфигураций между запусками.
