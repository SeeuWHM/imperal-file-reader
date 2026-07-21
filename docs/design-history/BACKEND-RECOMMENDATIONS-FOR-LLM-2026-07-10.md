# File Reader backend recommendations for LLM handoff

Ниже — список рекомендаций для backend file-reader по форматированию и сокращённости ответа, собранный как отдельный handoff-файл для другой LLM.

---

## 1) Делать два профиля ответа: `lean` и `full`
### Проблема
Сейчас backend payload хочется использовать сразу:
- и для панели,
- и для агента,
- и для отладки.

Из-за этого happy-path распухает.

### Рекомендация
Добавить query-параметр или header, например:

- `view=lean`
- `view=full`

Или:
- `X-Response-Profile: lean`

### Что должно быть в `lean`
Только поля, которые реально нужны для агентного чтения.

### Что должно быть в `full`
Всё остальное:
- отладка,
- quality internals,
- status details,
- pipeline hints.

### Почему это важно
Я почти всегда лучше работаю с `lean`.  
Панель или диагностика пусть просят `full`.

---

## 2) Для happy-path не отдавать debug/diagnostic поля
### Проблема
Если всё ок, диагностические поля не помогают, а только жрут токены.

### Рекомендация
На успешном ответе **не отдавать**:
- `diagnosis`
- `diagnosis_json`
- внутренние stage-trace
- pipeline-specific service internals
- лишние reason/debug blobs

Отдавать их только:
- при ошибке,
- при anomaly,
- при `view=full`,
- при `debug=1`.

### Почему
На happy-path мне нужен **результат**, а не медицинская карта пайплайна.

---

## 3) Канонизировать одно имя на одну сущность
### Проблема
Сейчас концептуально часто дублируются:
- `id` и `file_id`
- `title` и `filename`
- `text`, `body`, иногда `raw_body`
- `preview`, `excerpt`, `snippet`

### Рекомендация
Для backend contract сделать жёсткие канонические поля:

### Для файла:
- `file_id`
- `filename`

### Для текста:
- `text`

### Для превью:
- `preview`

### Для search-hit:
- `snippet`

### Для overview:
- `preview`

### Убрать дубли
Не возвращать одновременно:
- `id` + `file_id`
- `title` + `filename`
- `body` + `text`
- `preview` + `excerpt` если это одно и то же

### Почему
Дубли — это токены и риск путаницы для модели.

---

## 4) `GET /v1/documents/{id}/text` должен быть максимально сухим
Это главный read endpoint, и он должен быть самым “чистым”.

### Идеальный lean-ответ
```json
{
  "document_id": 123,
  "status": "ok",
  "text": "...",
  "offset": 0,
  "returned_chars": 3800,
  "total_chars": 22140,
  "has_more": true,
  "extraction_method": "pdf_text",
  "image_ai_used": false,
  "ocr_used": false,
  "is_partial": false
}
```

### Что лучше не тащить в standard happy-path
- pipeline stage
- chunk_count
- service diagnostics
- preview alongside full text
- duplicate size/name if caller already knows document
- giant metadata blobs

### Почему
Если я уже читаю `/text`, мне нужен **текст + окно + truth**, а не всё на свете.

---

## 5) Разделить “read window” и “document metadata” жёстче
### Проблема
Часть полей у `/text` по сути относится не к read-window, а к документу вообще.

### Рекомендация
`GET /v1/documents/{id}/text` пусть возвращает **только read-window contract**:
- `text`
- `offset`
- `returned_chars`
- `total_chars`
- `has_more`
- maybe truth fields

А document-level stuff:
- filename
- mime_type
- size_bytes
- created_at
- chunk_count
- preview
- status summary

оставить в:
- `GET /v1/documents/{id}`

### Почему
Меньше токенов и яснее роль endpoint.

---

## 6) `GET /v1/documents/{id}` должен быть cheap overview, не полутекстовый монстр
### Рекомендация
Этот endpoint должен быть **самым дешёвым** способом понять:
- что за документ,
- в каком он состоянии,
- стоит ли читать дальше.

### Идеальный lean-ответ
```json
{
  "document_id": 123,
  "filename": "invoice.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 842193,
  "status": "processed",
  "preview": "First 300–700 chars of clean text...",
  "extraction_method": "pdf_text",
  "image_ai_used": false,
  "ocr_used": false,
  "is_partial": false,
  "text_quality": 0.94,
  "noise_score": 0.06
}
```

### Чего не надо
- full text
- huge nested arrays
- chunk payloads
- index internals
- repeated derived fields

---

## 7) `POST /v1/search` — лучший кандидат на супер-lean контракт
### Сейчас
Семантический поиск уже самый удобный по идее.

### Рекомендация
Держать hit минимальным:

```json
{
  "query": "gin",
  "mode": "semantic",
  "total_matches": 6,
  "hits": [
    {
      "document_id": 123,
      "filename": "catalog.pdf",
      "seq": 14,
      "snippet": "....",
      "score": 0.92
    }
  ]
}
```

### Убрать из hit всё лишнее
Не надо на каждый hit:
- title
- kind
- url
- subtitle
- description
- full metadata объекта
- duplicate labels if filename+seq is enough

### Почему
Поиск должен быть дешёвым. Это мой основной triage-tool.

---

## 8) Везде передавать `returned_chars`, а не заставлять клиента вычислять
### Проблема
Иногда клиент может считать длину текста сам, но это лишняя работа и неоднозначность.

### Рекомендация
На text endpoints явно отдавать:
- `returned_chars`
- `total_chars`
- `has_more`

### Почему
Это даёт мне чёткое понимание:
- сколько уже прочитано,
- нужно ли дочитывать,
- насколько ответ уже полон.

---

## 9) Не отдавать пустые/null поля пачками
### Проблема
Куча полей вида:
- `"foo": null`
- `"bar": null`
- `"baz": null`

раздувает JSON без пользы.

### Рекомендация
Для `lean`-ответов:
- **omit null fields**
- **omit empty strings**
- **omit empty arrays**, если они не смыслообразующие

### Исключения
Оставлять только когда null сам по себе несёт смысл.

### Почему
Для LLM это прям ощутимая экономия.

---

## 10) Error-path и success-path должны быть разной плотности
### Проблема
Сейчас часто хочется один универсальный payload на всё.

### Рекомендация
**Success-path**
- минимальный
- короткий
- структурный

**Error-path**
- richer
- с explain/detail/code
- с retry hint если есть

### Пример
Успех:
```json
{"status":"ok","text":"...","has_more":true}
```

Ошибка:
```json
{
  "status": "error",
  "error_code": "document_not_ready",
  "message": "document is still processing",
  "retryable": true
}
```

### Почему
Ошибки редки, успехи часты. Значит оптимизировать надо успех.

---

## 11) Truth-поля оставить, но не раздувать
### Очень полезные поля
Эти поля мне реально нужны:
- `extraction_method`
- `image_ai_used`
- `ocr_used`
- `is_partial`

### Условно полезные
- `text_quality`
- `noise_score`

### Рекомендация
В `lean`:
- всегда оставить первые 4
- `text_quality` и `noise_score` — только в overview/preview или по `include_quality=1`

### Почему
Truth critical. Quality nice-to-have.

---

## 12) Нормализовать `status` до маленького фиксированного словаря
### Рекомендация
Использовать короткий стабильный набор:
- `pending`
- `processing`
- `ready`
- `error`
- `expired`

И не плодить близкие варианты.

### Почему
Чем меньше синонимов статусов, тем меньше когнитивный шум.

---

## 13) Preview должен быть отдельным cheap mode на backend
Сейчас extension строит preview комбинацией overview/read. Это ок, но backend может помочь лучше.

### Рекомендация
Либо:
- отдельный `GET /v1/documents/{id}/preview`
  
либо:
- `GET /v1/documents/{id}?view=preview`

### Ответ:
```json
{
  "document_id": 123,
  "status": "ready",
  "total_chars": 22140,
  "preview": "opening excerpt...",
  "secondary_preview": "excerpt from later...",
  "extraction_method": "pdf_text",
  "image_ai_used": false,
  "ocr_used": false
}
```

### Почему
Preview — это лучший способ экономить токены до full read.

---

## 14) Search должен возвращать snippets уже чистыми
### Проблема
Сейчас extension чистит snippets сама — это правильно, но лучше, чтобы backend уже отдавал:
- без `\x00`
- без мусорных переносов
- без гигантских дыр

### Рекомендация
Нормализация search snippet на backend:
- newline normalization
- strip null bytes
- collapse absurd blank runs
- trim edges

### Почему
Чем чище snippet, тем меньше я трачу токенов на шум.

---

## 15) Не возвращать full preview и full text одновременно
### Проблема
Если endpoint отдаёт и `preview`, и `text`, и ещё metadata — это раздутие.

### Рекомендация
Принцип:
- overview endpoint → `preview`
- text endpoint → `text`
- search endpoint → `snippet`

Не смешивать.

### Почему
Одно назначение — один payload.

---

## 16) Для exact-in-file search добавить backend endpoint вместо fulltext pull
### Важный момент
Сейчас exact mode в extension может читать весь документ и grep-ить локально.

### Рекомендация
Добавить backend endpoint вроде:
- `POST /v1/search/exact`
или
- `GET /v1/documents/{id}/grep?q=...`

### Ответ:
```json
{
  "document_id": 123,
  "query": "gin",
  "matches": [
    {"line": 81, "snippet": "..."},
    {"line": 144, "snippet": "..."}
  ],
  "total_matches": 2
}
```

### Почему
Это огромная экономия:
- не надо тянуть мегатекст ради пары строк
- меньше latency
- меньше токенов
- меньше риска выбить контекст

---

## 17) Добавить backend-side snippet length control
### Рекомендация
Для search/preview:
- `snippet_chars=300`
- `preview_chars=700`

### Почему
Пусть клиент не режет “после факта”, а backend сразу шлёт нужный размер.

---

## 18) Уточнить semantics для `is_partial`
### Проблема
`is_partial` может значить:
- extraction incomplete?
- text window partial?
- OCR partial?
- content truncated?

### Рекомендация
Разделить, если это не одно и то же:
- `is_partial_extraction`
- `is_truncated_window`

или хотя бы документировать жёстко одно значение.

### Почему
Для меня это важно: “неполный документ” и “ты просто читаешь не весь window” — очень разные вещи.

---

## 19) `GET /v1/documents` list endpoint сделать ultra-cheap
### Рекомендация
Список документов должен отдавать только:
- `document_id`
- `filename`
- `status`
- `mime_type`
- `size_bytes`
- maybe `updated_at`

Без preview, без chunk stats, без quality.

### Почему
List нужен для навигации, не для чтения.

---

## 20) Везде, где можно, использовать короткие имена enum-значений
### Не надо
- `full extracted text was empty at /v1/documents/{id}/text`

### Лучше
- `warning: "preview_only"`

А уже message можно короткий.

### Почему
Коды дешёвые, длинные prose-предложения дорогие.

---

## 21) Сократить prose в `message`
### Проблема
Иногда message-поля слишком разговорные и длинные.

### Рекомендация
Message делать короткими и фактическими:
- `"document still processing"`
- `"document expired; re-upload required"`
- `"preview used because full text was empty"`

### Почему
Я прекрасно читаю короткие технические формулировки.

---

## 22) Timestamp/retention/debug поля держать вне read/search path
### Рекомендация
Если нужны:
- `created_at`
- `updated_at`
- `expires_at`
- `ttl_days`
- `purge_reason`
- storage metadata

то только в list/detail/full/debug, но не в `search` и не в standard `text`.

### Почему
Это не помогает понять содержимое документа.

---

## 23) Если backend уже знает “text absent but preview present” — пусть сам делает fallback честно
### Проблема
Сейчас extension сама вынуждена ловить кейс:
- `/text` пуст
- `/documents/{id}` preview есть

### Рекомендация
На backend сделать единый честный контракт:
- либо `/text` всегда гарантирует usable text
- либо возвращает чёткий код/flag:
  - `warning: preview_only`
  - `fallback_text: ...`

### Почему
Меньше orchestration complexity на клиенте и меньше повторных запросов.

---

## 24) Добавить `recommended_next_action`
Это не обязательно, но очень полезно.

### Пример
Для `overview`:
- `"recommended_next_action": "read_preview"`
или
- `"recommended_next_action": "read_text"`

Для `pending`:
- `"recommended_next_action": "retry_later"`

### Почему
Это помогает агенту принимать следующий шаг без лишней догадки.
