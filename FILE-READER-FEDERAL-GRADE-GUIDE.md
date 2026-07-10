# File Reader — federal-grade guide for extension + backend

Статус: draft implementation guide
Дата: 2026-07-08
Аудитория: разработчик экстеншина и backend, который будет это внедрять шаг за шагом
Цель: довести системный File Reader до federal-grade уровня для агентного использования Webbee/Imperal

---

## 0. Зачем вообще всё это нужно

Сейчас типичная проблема файлового ридера не в том, что он «не умеет читать файлы», а в том, что он отдаёт агенту слишком сырой результат.

Для человека сырой текст иногда ещё терпим.
Для системного AI-агента — нет.

Если агент получает:
- сырой текст без структуры,
- шумные куски,
- повторяющиеся заголовки и футеры,
- таблицы как кашу текста,
- большой blob без quality flags,
- отсутствие preview/full режимов,

то происходят сразу 4 плохие вещи:

1. **Жрутся токены** на мусор.
2. **Падает точность** ответов.
3. **Растёт hallucination pressure** — модели приходится додумывать структуру.
4. **Нельзя строить системные сценарии** поверх такого инструмента уверенно.

Federal-grade File Reader должен быть не просто экстрактором текста, а **контекстным документным интерфейсом для агента**.

---

## 1. Definition of done: что значит federal-grade

Файл-ридер считается federal-grade, если он умеет:

1. **Стабильно принимать файл** и классифицировать его тип.
2. **Делать extraction pipeline** отдельно от LLM-facing presentation.
3. **Отдавать агенту не только текст, но и структуру**.
4. **Помечать качество extraction-а** и уровень шума.
5. **Экономить токены** через preview-first архитектуру.
6. **Поддерживать typed mode для таблиц**, а не только plain text.
7. **Давать явные warning/diagnostic flags**, а не прятать проблемы.
8. **Иметь predictable API contract**, пригодный для системного использования.
9. **Уметь частичное чтение**, chunking и целевой retrieval.
10. **Не заставлять модель вручную санировать мусор**, который backend мог убрать сам.

---

## 2. Главные принципы архитектуры

### 2.1. Самый важный принцип
**Raw extraction != agent response**

Это нужно развести в архитектуре намертво.

Плохо:
- backend вытащил текст,
- этот текст почти как есть отдали агенту.

Хорошо:
- backend вытащил raw content,
- потом прогнал через normalization,
- потом выделил структуру,
- потом посчитал quality/noise,
- потом собрал компактный и typed ответ для агента.

### 2.2. Preview-first
По умолчанию агент должен получать **минимально достаточный и структурный** ответ.

Полный текст — только если реально нужен.

### 2.3. Typed beats plain text
Если документ табличный, агенту надо отдавать таблицу как таблицу.

Если документ презентация, агенту надо отдавать слайды как слайды.

Если документ docx, агенту надо отдавать headings/lists/tables/sections, а не один сплошной текстовый blob.

### 2.4. Backend обязан сам бороться с мусором
Не надо перекладывать на модель:
- dedupe boilerplate,
- suppression футеров,
- repeated slide chrome,
- пустые артефакты,
- page numbers,
- декоративную шелуху.

LLM — не мусороперерабатывающий завод.

---

## 3. Что менять именно в экстеншине

Под «экстеншином» здесь имеется в виду:
- tool contract,
- surface API,
- what functions expose to the agent,
- naming,
- режимы вызова,
- shape of returned payloads.

### 3.1. Разделить инструменты по уровням доступа

Сейчас типичная ошибка — один read endpoint пытается быть всем сразу.

Нужно минимум 4 уровня функций:

#### A. `file_overview`
Назначение:
- дешёвый первый взгляд на файл
- понять, стоит ли читать глубже

Должна возвращать:
- `file_id`
- `name`
- `mime_type`
- `source_type` (upload/google_drive/etc)
- `size_bytes`
- `created_at`
- `indexing_status`
- `detected_kind` (`document`, `spreadsheet`, `presentation`, `pdf`, `image`, `archive`, `unknown`)
- `language`
- `page_count` / `slide_count` / `sheet_count` / `row_count` если известно
- `has_tables`
- `has_images`
- `has_ocr`
- `estimated_tokens_clean`
- `quality_summary`
- `warnings[]`
- `short_preview`

#### B. `read_file_preview`
Назначение:
- получить сжатое, полезное, структурное представление
- ответить на вопрос «что это вообще?» без слива токенов

Должна возвращать:
- `summary`
- `document_purpose_guess`
- `section_index[]`
- `representative_excerpts[]`
- `table_list[]`
- `entities[]` (если есть)
- `contact_fields_detected[]`
- `quality`
- `warnings`
- `truncation_info`

#### C. `read_file_full`
Назначение:
- полное чтение только когда это уже реально нужно

Должна возвращать:
- `content_chunks[]`
- `sections[]`
- `tables[]`
- `appendices[]`
- `quality`
- `warnings`
- `raw_availability`
- `token_estimate`

#### D. Targeted/typed tools
Нужны отдельные функции для специальных случаев:
- `list_sheets(file_id)`
- `describe_sheet(file_id, sheet_name)`
- `sample_sheet_rows(file_id, sheet_name, limit, offset)`
- `search_in_file(file_id, query)`
- `read_section(file_id, section_id)`
- `read_slide(file_id, slide_no)`
- `read_table(file_id, table_id)`

Это критично. Нельзя заставлять агента читать весь документ, чтобы извлечь одну таблицу.

---

### 3.2. Изменить контракты ответа экстеншина

Каждый ответ инструмента должен быть:
- предсказуемым,
- компактным,
- типизированным,
- с quality envelope,
- с warnings,
- с usage-aware полями.

Минимальная форма ответа должна быть такой:

```json
{
  "file": {},
  "summary": {},
  "structure": {},
  "quality": {},
  "warnings": [],
  "data": {}
}
```

Не надо отдавать просто `text: "..."` без всего остального.

---

### 3.3. Сделать сильное различие между clean mode и raw/debug mode

Инструмент должен по умолчанию работать в **clean mode**.

`raw/debug mode` нужен только:
- для разработчика,
- для диагностики extraction bugs,
- для сравнения качества pipeline.

#### Требование
Raw mode не должен быть default ни в одном пользовательском tool path.

Если оставить raw default, агент будет тонуть в мусоре всегда.

---

### 3.4. Добавить quality envelope на уровне tool contract

Каждый ответ чтения должен содержать как минимум:

```json
{
  "quality": {
    "text_quality": 0.0,
    "structure_quality": 0.0,
    "noise_score": 0.0,
    "coherence_score": 0.0,
    "is_partial": false,
    "is_truncated": false,
    "ocr_used": false,
    "ocr_confidence": null,
    "has_repeated_headers": false,
    "has_repeated_footers": false,
    "has_suspicious_artifacts": false,
    "dominant_language": "ru",
    "language_mixture": []
  }
}
```

#### Зачем это нужно
Чтобы агент могла:
- не доверять плохому extraction-у как чистой истине,
- предупреждать пользователя,
- принимать решение, делать ли deep read,
- экономить токены,
- строить retrieval только по хорошим чанкам.

---

### 3.5. Внедрить budget-aware responses

Инструменты должны уметь параметр вроде:
- `budget = small | medium | large`

#### Как это трактовать
- `small` → metadata + summary + 1-3 excerpts
- `medium` → preview + sections + sample tables
- `large` → full structured response

Это лучше, чем тупо слать максимум контента всегда.

---

### 3.6. Добавить tool-level warnings и explicit diagnostics

Вместо абстрактного «не удалось прочитать нормально» нужны машинно-понятные предупреждения.

Примеры:
- `INDEXING_PENDING`
- `OCR_REQUIRED`
- `LOW_TEXT_CONFIDENCE`
- `HIGH_BOILERPLATE_RATIO`
- `REPEATED_HEADERS_DETECTED`
- `TABLE_HEAVY_DOCUMENT`
- `PARTIAL_EXTRACTION`
- `UNSUPPORTED_EMBEDDED_OBJECTS`
- `LANGUAGE_MIXED`
- `SUSPICIOUS_LOW_COHERENCE_SEGMENTS`

Каждое предупреждение должно содержать:
- `code`
- `severity`
- `message`
- `impact`
- `recommended_next_step`

---

## 4. Что менять именно на backend

Теперь самое важное: backend.

Если extension contract улучшить, а backend оставить сырым, пользы будет мало.

### 4.1. Разделить pipeline на отдельные фазы

Нужен pipeline как минимум из 6 этапов:

1. **ingest**
2. **type detection**
3. **raw extraction**
4. **normalization & cleanup**
5. **structure reconstruction**
6. **agent presentation build**

#### Важно
Эти этапы должны быть не “мысленно”, а реально отражены в коде и данных.

Иначе потом невозможно:
- дебажить качество,
- мерить improvement,
- сравнивать стратегии,
- понимать, где возникает мусор.

---

### 4.2. Ingest layer: что должна делать первая стадия

На ingest этапе backend должен:
- присвоить стабильный `file_id`
- вычислить `content_hash`
- определить `mime_type`
- определить `source`
- снять базовые metadata
- попытаться понять container-type (pdf/docx/xlsx/pptx/txt/html/image/...)
- поставить документ в очередь индексирования/обработки

#### Надо хранить
- `size_bytes`
- `upload_time`
- `source_connector`
- `original_filename`
- `detected_extension`
- `detected_mime`
- `processing_status`

---

### 4.3. Raw extraction layer: не прятать, но и не отдавать напрямую

Raw extraction должен сохраняться отдельно как debug artifact.

Например:
- `raw_text`
- `raw_tables`
- `raw_slides`
- `raw_ocr_blocks`
- `raw_html`

Но эти поля не должны идти в агентский ответ по умолчанию.

#### Почему
Потому что raw extraction почти всегда:
- шумный,
- неровный,
- форматозависимый,
- не оптимален по токенам.

---

### 4.4. Normalization layer: здесь начинается реальная магия

Эта стадия должна делать системную чистку.

#### Обязательные действия

##### 1. Unicode normalization
- привести пробелы, переносы, кавычки, спецсимволы к стабильному виду

##### 2. Whitespace cleanup
- убрать бессмысленные множественные пробелы
- почистить рваные пустые строки

##### 3. Header/footer dedupe
- найти повторяющиеся строки на множестве страниц/слайдов
- вынести их в boilerplate
- убрать из основного контента

##### 4. Page number suppression
- убрать отдельные page numbers / slide numbers, если они не содержательные

##### 5. Decorative noise suppression
- линии, маркеры, одиночные служебные символы, мусорный chrome

##### 6. Merge broken paragraphs
- склеить параграфы, если extraction разорвал их не по смыслу

##### 7. Split merged junk
- наоборот, разделить слипшиеся блоки, если extraction всё смешал

##### 8. Duplicate block removal
- удалить одинаковые блоки, которые повторяются многократно

##### 9. Language detection per segment
- определить dominant language документа
- отметить сегменты с другим языком

##### 10. Suspicious segment marking
- отметить странные куски типа:
  - низкая связность,
  - бытовая фраза внутри научного отчёта,
  - хаотичный junk,
  - OCR-мешанина

---

### 4.5. Structure reconstruction layer

Это обязательный слой. Без него агент будет постоянно страдать.

#### Для DOCX / rich text docs
Нужно уметь выделять:
- title
- headings hierarchy
- paragraphs
- lists
- tables
- captions
- appendix sections
- references/bibliography if identifiable

#### Для PPTX
Нужно выделять:
- slide number
- slide title
- bullet hierarchy
- notes
- master-template repeated text
- text density per slide
- possible “title only / image only / agenda slide” classes

#### Для PDF
Нужно пытаться восстановить:
- page sections
- headings
- paragraphs
- tables
- repeated boilerplate
- columns if detectable

#### Для spreadsheets
Нужно выделять:
- workbook metadata
- list of sheets
- header row per sheet
- row count
- column count
- inferred column types
- sparse columns
- candidate email/url/phone columns
- sample rows
- maybe unique cardinality on key fields

#### Для plain text / markdown / html
Нужно выделять:
- headings
- paragraphs
- lists
- links
- code blocks if any

---

### 4.6. Quality scoring layer

Federal-grade backend обязан уметь оценивать качество extraction-а численно.

#### Минимальные score-поля
- `text_quality`
- `structure_quality`
- `noise_score`
- `coherence_score`
- `boilerplate_ratio`
- `duplication_ratio`
- `ocr_confidence_avg`
- `table_parse_confidence`
- `language_consistency_score`

#### Как это использовать
- high noise → не слать full text по default
- low structure → предупреждать агента
- low OCR confidence → советовать человеку manual verification
- high boilerplate ratio → aggressive clean mode

---

### 4.7. Chunking layer: резать по структуре, а не тупо по символам

Плохой подход:
- каждые 3000–5000 символов новый chunk

Хороший подход:
- heading-aware
- section-aware
- slide-aware
- table-aware
- paragraph-aware

#### Каждый chunk должен иметь:
- `chunk_id`
- `section_path`
- `content_type` (`paragraph`, `table`, `list`, `slide_notes`, `header`, etc)
- `token_estimate`
- `quality`
- `source_span`
- `importance_score`
- `retrieval_text`
- `display_text`

#### Почему это важно
Потому что retrieval и direct read — это не одно и то же.
Иногда для поиска нужен один текст, для показа — другой, более очищенный.

---

### 4.8. Retrieval/index layer

Если backend индексирует файлы для поиска, retrieval должен быть quality-aware.

#### Нельзя ранжировать только по embedding similarity
Нужно учитывать ещё:
- section title boost
- file title boost
- table/header weight
- anomaly penalty
- noise penalty
- duplicate penalty
- chunk recency / file recency if relevant

#### Иначе что происходит
В top results вылазят:
- шумные куски,
- повторяющийся boilerplate,
- случайные обрывки,
- менее полезные сегменты, чем реально нужные.

---

### 4.9. Typed spreadsheet backend

Это отдельный must-have.

Spreadsheet нельзя считать просто как текстовый документ.

#### Backend для sheet должен уметь:
- `list_sheets(file_id)`
- `describe_sheet(file_id, sheet)`
- `get_sheet_schema(file_id, sheet)`
- `sample_rows(file_id, sheet, limit, offset)`
- `search_rows(file_id, sheet, query)`
- `profile_columns(file_id, sheet)`
- `count_rows(file_id, sheet)`
- `detect_contact_columns(file_id, sheet)`

#### Что должен вернуть profile_columns
Например:
- column name
- inferred type
- null ratio
- unique ratio
- example values
- maybe regex class: email/phone/url/date

Это огромная экономия токенов и качество ответа для задач типа lead tables.

---

### 4.10. Observability and metrics

Если ты хочешь реально довести это до federal-grade, нужно мерить систему.

#### Обязательные метрики
На backend по документу:
- ingest success rate
- extraction success rate
- partial extraction rate
- OCR invocation rate
- average raw chars
- average cleaned chars
- compression ratio
- average token estimate raw
- average token estimate clean
- average noise score
- average structure score
- duplicate suppression gain
- preview-to-full ratio
- search hit usefulness proxy

#### Обязательные метрики по tool usage
- какие инструменты чаще вызываются
- сколько раз нужен full read после preview
- сколько раз search решает задачу без full read
- какие форматы самые шумные
- где чаще всего warning `PARTIAL_EXTRACTION`

---

### 4.11. Error contract

Backend не должен отвечать мутным “failed”.

Нужен строгий error taxonomy.

#### Примеры кодов
- `UNSUPPORTED_FILE_TYPE`
- `FILE_TOO_LARGE`
- `EXTRACTION_TIMEOUT`
- `OCR_FAILED`
- `INDEXING_IN_PROGRESS`
- `NO_TEXT_EXTRACTED`
- `PARTIAL_EXTRACTION_ONLY`
- `SHEET_PARSE_FAILED`
- `EMBEDDED_OBJECT_NOT_SUPPORTED`
- `REMOTE_FETCH_BLOCKED`

#### Каждая ошибка должна нести
- `code`
- `message`
- `recoverable` boolean
- `stage` (`ingest`, `extract`, `normalize`, `index`, `serve`)
- `recommended_next_step`

---

## 5. Что именно жрёт токены зря и как это лечить

Вот список waste-sources и точечные методы лечения.

### 5.1. Повторяющиеся headers/footers
Проблема:
- документы с колонтитулами гонят одинаковые строки в каждый page chunk

Лечение:
- page-frequency analysis
- repetition threshold
- mark as boilerplate
- exclude from main display text

### 5.2. Плоская выдача таблиц
Проблема:
- 200 строк sheet-а как линейный text dump

Лечение:
- typed row/column JSON
- sampled rows
- schema-first view
- row search endpoint

### 5.3. Слайдовые артефакты
Проблема:
- master template text, footer, slide number, decorative labels

Лечение:
- slide-level boilerplate detection
- repeated slide text suppression
- title/body separation

### 5.4. OCR junk
Проблема:
- мусорные символы и плохая сцепка слов

Лечение:
- OCR confidence threshold
- low-confidence zones flagged
- line repair heuristics
- no full dump by default if OCR quality low

### 5.5. Длинные blobs без sections
Проблема:
- агент тратит токены на реконструкцию структуры

Лечение:
- explicit section index
- section chunks
- headings hierarchy

### 5.6. Дублированные блоки
Проблема:
- одинаковый текст идёт несколько раз

Лечение:
- block hashing
- near-duplicate detection
- dedupe in clean mode

### 5.7. Малоинформативные первые килобайты
Проблема:
- начало документа часто титул/служебка/содержание

Лечение:
- representative excerpt selection вместо naive prefix dump

---

## 6. Как должен выглядеть идеальный agent-facing ответ

Ниже пример хорошего ответа для spreadsheet.

```json
{
  "file": {
    "id": "file_123",
    "name": "Молдавские компании",
    "type": "spreadsheet",
    "mime_type": "application/vnd.google-apps.spreadsheet",
    "source_type": "google_drive"
  },
  "summary": {
    "kind": "lead_table",
    "title": "Молдавские компании",
    "short_description": "Spreadsheet with Moldovan companies and outreach fields"
  },
  "structure": {
    "sheets": [
      {
        "name": "Sheet1",
        "rows": 187,
        "columns": [
          {"name": "Название", "type": "text"},
          {"name": "Link", "type": "url"},
          {"name": "Email", "type": "email"},
          {"name": "Оценка", "type": "text"},
          {"name": "Hosting", "type": "text"},
          {"name": "Отправил", "type": "text"},
          {"name": "Статус", "type": "text"}
        ]
      }
    ]
  },
  "quality": {
    "text_quality": 0.96,
    "structure_quality": 0.99,
    "noise_score": 0.03,
    "is_truncated": false,
    "ocr_used": false
  },
  "warnings": [],
  "data": {
    "sample_rows": [
      {
        "Название": "Example SRL",
        "Link": "https://example.md",
        "Email": "hello@example.md"
      }
    ]
  }
}
```

Аналогичный contract нужен для:
- docx
- pdf
- pptx
- html
- txt

---

## 7. Пошаговый implementation plan для «тупенького Claude»

Ниже специальный прямолинейный план без романтики.

### Phase 1 — зафиксировать контракт

#### Task 1.1
Описать новый JSON contract для:
- `file_overview`
- `read_file_preview`
- `read_file_full`
- `search_in_file`
- spreadsheet typed endpoints

#### Task 1.2
Запретить неструктурные ответы как default.

#### Task 1.3
Добавить в каждый ответ разделы:
- `file`
- `summary`
- `structure`
- `quality`
- `warnings`
- `data`

**Definition of done:** все новые и обновлённые endpoints возвращают унифицированный envelope.

---

### Phase 2 — cleanup pipeline

#### Task 2.1
Сделать normalization module.

#### Task 2.2
Внедрить:
- whitespace cleanup
- repeated line detection
- header/footer suppression
- page-number suppression
- duplicate block removal

#### Task 2.3
Сделать флаги:
- `has_repeated_headers`
- `has_repeated_footers`
- `duplication_ratio`
- `boilerplate_ratio`

**Definition of done:** clean output visibly короче и чище raw output на типовых шумных документах.

---

### Phase 3 — structure reconstruction

#### Task 3.1
Для docx/pdf/html/txt добавить section model.

#### Task 3.2
Для pptx добавить slide model.

#### Task 3.3
Для xlsx/google sheets добавить sheet model.

#### Task 3.4
Научиться отдельно хранить:
- sections
- tables
- slides
- sample rows

**Definition of done:** модель документа восстанавливается без чтения гигантского text blob.

---

### Phase 4 — quality scoring

#### Task 4.1
Ввести quality calculators.

#### Task 4.2
Научиться считать:
- `text_quality`
- `structure_quality`
- `noise_score`
- `coherence_score`
- `ocr_confidence_avg`

#### Task 4.3
Добавить warnings generator.

**Definition of done:** любой file read response содержит machine-usable quality state.

---

### Phase 5 — preview/full split

#### Task 5.1
Сделать дешёвый preview endpoint.

#### Task 5.2
Ограничить full endpoint так, чтобы он:
- поддерживал sections/chunks
- не отдавал всё подряд без нужды

#### Task 5.3
Добавить budget parameter.

**Definition of done:** большинство пользовательских задач можно решить через overview+preview без full dump.

---

### Phase 6 — retrieval and search hardening

#### Task 6.1
Индексировать чанки, а не только whole file text.

#### Task 6.2
Добавить ranking с quality weighting.

#### Task 6.3
Научить search возвращать:
- chunk id
- section title
- file title
- excerpt
- quality summary

**Definition of done:** поиск перестаёт вытаскивать мусорные куски в топе.

---

### Phase 7 — spreadsheet first-class support

#### Task 7.1
Реализовать typed sheet endpoints.

#### Task 7.2
Добавить profile_columns и sample_rows.

#### Task 7.3
Добавить auto-detection для:
- email columns
- phone columns
- url columns
- date columns

**Definition of done:** таблицы больше не требуют чтения как plain text, если вопрос табличный.

---

### Phase 8 — observability and regression set

#### Task 8.1
Собрать golden corpus файлов:
- чистый docx
- шумный docx
- pptx со slide boilerplate
- pdf с таблицей
- sheet с лидами
- OCR-скан плохого качества
- mixed-language file

#### Task 8.2
Для каждого файла фиксировать:
- raw chars
- clean chars
- token estimate raw/clean
- warnings
- summary quality

#### Task 8.3
Писать regression tests на contract и cleanup.

**Definition of done:** улучшения можно проверять числами, а не на глазок.

---

## 8. Приоритеты: что делать в первую очередь

Если ресурсов мало, порядок такой.

### P0 — обязательно
1. Единый response envelope
2. Preview/full split
3. Quality envelope
4. Cleanup pipeline
5. Structure-aware chunking
6. Typed spreadsheet support

### P1 — очень желательно
7. Quality-aware retrieval
8. Warnings taxonomy
9. Budget parameter
10. Representative excerpts
11. Error taxonomy

### P2 — nice but later
12. Advanced anomaly detection
13. OCR confidence zoning
14. Section importance scoring
15. Cross-file entity graph

---

## 9. Антипаттерны, которые надо запретить

Вот список вещей, которые в системном File Reader должны считаться архитектурными ошибками.

### Запрещённые антипаттерны
- Отдавать `text` blob без quality и structure
- Считать spreadsheet plain-text документом по умолчанию
- Возвращать raw extraction как default
- Не разделять overview/preview/full
- Резать документ только по длине строки/символов
- Не помечать partial extraction
- Не различать noise и content
- Не удалять repeated boilerplate
- Делать retrieval по whole file вместо chunks
- Не иметь typed diagnostics

---

## 10. Минимальный федеральный контракт качества

Чтобы можно было сказать «да, это уже federal-grade enough», минимально должны выполняться все пункты ниже:

1. У каждого read endpoint есть uniform envelope.
2. Есть preview endpoint, который заметно дешевле full.
3. Есть quality + warnings.
4. Есть clean mode по умолчанию.
5. Есть suppression repeated headers/footers.
6. Есть structure-aware chunking.
7. Sheets доступны typed способом.
8. Ошибки имеют taxonomy.
9. Retrieval работает по чанкам.
10. Есть metrics, показывающие gain по clean-vs-raw token volume.

---

## 11. Самый практичный короткий вывод

Если совсем по-простому, то вот что нужно сделать:

### В экстеншине
- переделать инструменты в `overview / preview / full / typed readers`
- везде добавить `quality`, `warnings`, `structure`
- не слать raw text по умолчанию
- добавить budget-aware и targeted reads

### На backend
- отделить raw extraction от agent presentation
- внедрить cleanup/dedupe/boilerplate suppression
- реконструировать структуру документов
- считать quality/noise/coherence
- сделать typed spreadsheet pipeline
- хранить chunk-level данные для retrieval
- ввести строгие ошибки и метрики

Если это сделать, File Reader превратится из «экстрактора текста» в **системный документный интерфейс федерального качества**, которым Webbee сможет пользоваться уверенно, дёшево по токенам и без постоянной санитарной магии поверх сырых ответов.

---

## 12. Следующий логичный артефакт после этого гайда

После этого документа логично сделать ещё 2 файла:

1. **API contract spec**
   - точные JSON схемы ответов
   - поля
   - enum values
   - примеры success/error payloads

2. **Implementation checklist**
   - буквально checkbox-лист по backend и extension
   - чтобы Claude мог закрывать задачи по одной без умничанья

Если захочешь — я следующим сообщением могу собрать и их тоже. 🐝
