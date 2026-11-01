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
from dms_pipeline.ingestion import read_workbook
from dms_pipeline.mapping import map_row
from dms_pipeline.rules import build_rules

config = load_config("config/example.yml")
workbook = read_workbook("synthetic.xlsx", config.workbook)
engine = build_rules(config.rules)

for source_row in workbook.rows:
    mapped = map_row(source_row.values, config.mapping)
    results = engine.evaluate(mapped)
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
