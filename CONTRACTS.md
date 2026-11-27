# Контракты компонентов MVP

Версия схемы `1.0` · 27.11.2026. Спецификация для реализации, не существующий API. Решения и ограничения источников — в [ARCHITECTURE.md](ARCHITECTURE.md). Контракты сверены с `RESEARCH_REPORT.md`, включая примеры офферов, пределы поиска и состав событий.

## 1. Общие правила

Единственный владелец общих контрактов при реализации — оркестратор. Сначала создать Pydantic-модели в `src/smart_site_search/contracts/`, выгрузить OpenAPI и fixtures, затем подключать исполнителей. Внутренние интерфейсы ниже — обычные Python-функции, не отдельные сетевые сервисы.

JSON: UTF-8, snake_case; время RFC3339 UTC, интерфейс показывает Europe/Moscow. Денежные суммы — целые minor units, валюта ISO (`RUB`, `USD`). Стоимость LLM — decimal string в основной единице валюты, чтобы не потерять доли цента. Все исходные id каталога — строки. `null` означает неизвестно/неприменимо согласно полю, `[]` — известный пустой список; эти значения не заменяются нулём.

Запрещены неописанные поля на входе. Все массивы ограничены: запрос 2 000 символов, фильтры 20, термины 12, экспертные компоненты 4, result items 12, card candidates 200. Внутренние типы преобразуются в JSON Schema; неоднозначные union при необходимости заменяются tagged objects. Лимиты — настройки MVP, фиксируются в trace.

Обозначения ниже: `T?` — nullable; `T[]` — массив; `enum(a|b)` — строка из списка. Все поля обязательны, кроме явно помеченных «опционально»; nullable поле передаётся как null. Кодовые блоки описывают типы, не являются реализацией продукта.

```text
CardId = "group:<source_group_id>" | "offer:<source_offer_id>"
SourceRef = {
  source: enum(feed|category|taxonomy_rule|reviewed_enrichment|external_doc|llm_hypothesis|mock),
  catalog_version: string?, entity_id: string, field: string,
  raw_index: integer?, artifact_version: string?, note: string?,
  document_url: string?, checked_at: string?
}
Money = { amount_minor: integer>=0, currency: string }
Scalar = string | integer | boolean
Knowledge = enum(known|unknown|conflicting)
Attribute = {
  key: string, knowledge: Knowledge, values: Scalar[],
  unit: string?, min_value: integer?, max_value: integer?,
  source_refs: SourceRef[]
}
```

Произвольная строка note не доказывает факт. SourceRef проверяется репозиторием по entity/field/version; `llm_hypothesis` не становится источником цены, наличия или технической характеристики. Диапазон и дискретные значения применяются по типу поля из реестра.

## 2. Каталог

```text
Card = {
  card_id: CardId, kind: enum(software|hardware), name: string,
  vendor_ids: string[], class_id: string,
  roles: {role_id:string, source_refs:SourceRef[], verified:boolean}[],
  shelf_ids: string[], description: string?, image_url: string?,
  card_url: string?, card_url_source: SourceRef?,
  offer_count: integer, available_count: integer,
  aggregate_price_min: Money?, aggregate_price_max: Money?,
  facet_values: Attribute[], catalog_version: string
}
Offer = {
  offer_id: string, card_id: CardId, name: string, vendor_code: string?,
  available: boolean?, price: Money?,
  price_state: enum(known|on_request|unknown),
  url: string?, image_url: string?,
  attrs: Attribute[], sales_notes_raw: string?,
  sale_unit: enum(license|device|pack|unknown),
  pack_size: integer?, min_order_quantity: integer?, max_order_quantity: integer?,
  raw_params: {name:string,value:string,index:integer}[],
  source_refs: SourceRef[], catalog_version: string
}
```

У hardware ровно один Offer. У software — один или более. Цена 0 из фида становится `price=null, price_state=on_request`; исходное значение остаётся в источнике. `aggregate_price_*` — информационные агрегаты, их нельзя присвоить цене подходящего оффера. Имена и URL не генерируются LLM.

Реестр `taxonomy/fields.json` задаёт для каждого поля тип, единицу, допустимые операции и области применения. Обязательный стартовый набор:

| Ключ | Тип и область |
|---|---|
| `vendor_id`, `card_id`, `kind`, `class_id`, `role_id` | Строка/множество, карточка |
| `domestic_hint`, `saas` | boolean, карточка с provenance |
| `registry_number`, `certificate` | Строка, факт оффера; не выводить из domestic_hint |
| `delivery_type`, `customer_type`, `organization_type`, `license_type`, `edition`, `platform`, `architecture` | Enum/строка, software offer; legal entity и commercial organization — разные условия |
| `term_months`, `users`, `devices`, `nodes`, `cores` | Целое/интервал, software offer; независимые измерения |
| `perpetual`, `available` | boolean, соответствующий оффер |
| `ram_gib`, `vram_gb`, `storage_gb`, `form_factor`, `socket` | Число/строка, hardware offer; RAM и память GPU не взаимозаменяемы |
| `price_minor`, `total_price_minor` | Целое, обязательно currency; второе только при известной единице закупки |

Неизвестный системе атрибут не отбрасывается: он помещается в `unresolved_constraints`, вызывает оговорку и исключает full до разрешения. Техническая неизвестность фильтра не означает отсутствие свойства у товара. Для `external_doc` обязательны document_url и checked_at; материал подтверждает только приведённое утверждение и не подменяет цену/наличие фида.

## 3. Вход API и состояние сессии

`POST /api/v1/search`:

```json
{
  "schema_version": "1.0",
  "client_request_id": "54d102ea-27f1-491b-b0c4-5aef8d83bf9a",
  "session_id": null,
  "expected_revision": 0,
  "query": "Astra Linux Special Edition",
  "reference_request_id": null,
  "selected_card_id": null,
  "limit": 8
}
```

`session_id=null` создаёт сессию, `expected_revision=0` обязательно. `limit` — от 4 до 12, по умолчанию 8; это предел карточек, а не требование заполнять список нерелевантными товарами. Существующая сессия — opaque UUID, хранится браузером; нет реального профиля клиента по умолчанию. Для просроченной сессии — 410 с предложением начать новую. Selected card должен присутствовать в указанном предыдущем ответе этой же сессии; иначе 422.

Повторный `(session_id, client_request_id)` с тем же payload возвращает сохранённый ответ, без нового вызова LLM и аналитического запроса. Для создания сессии client_request_id уникален в таблице requests и также позволяет вернуть прежнюю сессию. Иное тело с тем же id → 409. Один выполняющийся запрос на сессию; второй → 409 `session_busy`, не очередь. Revision проверяется перед началом и при фиксации. Сбой между стартом и завершением остаётся `request_interrupted`; повтор не маскируется под новую бесплатную попытку. После перезапуска незавершённый request требует нового client_request_id, прежние расходы сохраняются.

```text
SearchSession = {
  session_id: string, revision: integer, topic_id: string,
  goal: string?, mode: enum(direct|functional|expert)?,
  catalog_version: string, taxonomy_version: string,
  query_state: QueryState?,
  recent_results: {request_id:string, display_order:CardId[]}[],
  seen_card_ids: CardId[], expert_plan: ExpertPlan?,
  last_turns: {query:string, response_summary:string}[],
  profile: {segment:string?, is_mock:boolean}?,
  updated_at: string, expires_at: string
}
```

Смена версии каталога инвалидирует recent_results/seen и карточные ссылки, требует повторного поиска; возвращается предупреждение. Смена таксономии также проверяет валидность сохранённых role/class/filter ids. Последовательность выдачи сохраняется сервером, а не восстанавливается из ранга.

## 4. Интерпретация и изменение фильтров

```text
Filter = {
  filter_id: string,
  key: string, op: enum(eq|in|gte|lte|between),
  values: Scalar[], unit: string?, currency: string?,
  strength: enum(require|prefer|exclude),
  scope: enum(topic|session|component), component_id: string?,
  source_turn_id: string, source_text: string
}
FilterChange = {
  action: enum(set|remove|clear_scope),
  scope: enum(topic|session|component), component_id: string?,
  key: string?, filter: Filter?
}
Interpretation = {
  mode: enum(direct|functional|expert),
  operation: enum(new_topic|refine|more|reference),
  goal: string,
  entities: {text:string, type:enum(product|vendor|sku),
             usage:enum(target|reference|excluded), resolved_id:string?}[],
  class_ids: string[], role_ids: string[], search_terms: string[],
  filter_changes: FilterChange[],
  reference: {request_id:string, card_id:CardId?, ordinal:integer?}?,
  unresolved_constraints: string[],
  ambiguity: enum(none|needs_clarification),
  clarification_question: string?,
  interpretation_source: enum(rules|llm|cache|degraded_rules)
}
QueryState = {
  goal: string, mode: enum(direct|functional|expert),
  entities: {text:string,type:enum(product|vendor|sku),
             usage:enum(target|reference|excluded),resolved_id:string?}[],
  class_ids: string[], role_ids: string[], search_terms: string[],
  filters: Filter[], excluded_card_ids: CardId[], excluded_vendor_ids: string[],
  unresolved_constraints: string[], reference_card_id: CardId?,
  omit_seen: boolean
}
```

В LLM-ответе серверные `filter_id`, `source_turn_id`, `interpretation_source` и resolved_id не считаются доверенными: адаптер назначает id/источник сам, а resolved_id проверяет по каталогу. Модели можно передать нормализованные lookup-варианты, но не полный каталог.

Семантика фильтров:

- `eq/gte/lte` требуют одно значение, `between` — два упорядоченных, `in` — непустое множество. `exclude` означает запрет удовлетворяющего предикату значения, а не переименование `require`.
- Строки сравниваются после нормализации по реестру; in — принадлежность допустимому набору. Numeric gte/lte/between сравнивают один и тот же физический показатель в совместимых единицах. Для подтверждённого лицензионного диапазона число запроса должно входить в диапазон; для ёмкости пакета действует правило покрытия и единицы закупки, а партия проверяется отдельно. Не выводить ёмкость лицензии из sales_notes.
- `set` требует Filter; заменяет фильтр с тем же `(scope,component_id,key)`, остальные сохраняет. Несколько запрещённых вендоров объединяются в одном `in`-фильтре с `strength=exclude`.
- `remove` требует key и не требует filter; отсутствие такого ключа — идемпотентный no-op. `clear_scope` очищает только указанную область, key/filter=null.
- На `new_topic` reducer сам очищает topic/component фильтры, seen и ссылки до применения новых изменений. Session scope выставляется только при явном «во всех следующих запросах» или через UI, не по догадке модели.
- `more` сохраняет потребность и устанавливает omit_seen=true. Reference ordinal — от 1, по display_order конкретного ответа. Неоднозначная ссылка не выбирает произвольную карточку.
- Явные ограничения сохраняются даже если не дали результатов. Relaxation — свойство выдачи partial, не изменение пользовательского состояния.
- Target product/sku после разрешения id даёт обязательное условие идентичности; другой продукт не full даже при совпадающем классе. Reference-сущность в «аналог VMware» задаёт контекст функции и не становится require vendor/card. Excluded-сущность преобразуется в exclude. Неразрешённое точное имя не разрешает автоматически подменить его товаром того же класса.

Пример операции для «теперь на два года» в существующей software-теме:

```json
{
  "action": "set",
  "scope": "topic",
  "component_id": null,
  "key": "term_months",
  "filter": {
    "filter_id": "f-term-2",
    "key": "term_months",
    "op": "eq",
    "values": [24],
    "unit": "month",
    "currency": null,
    "strength": "require",
    "scope": "topic",
    "component_id": null,
    "source_turn_id": "request-2",
    "source_text": "на два года"
  }
}
```

Это пример контракта, не заявление о наличии соответствующей лицензии Astra.

## 5. Retrieval и проверка офферов

```text
Candidate = {
  card_id: CardId, component_id: string?,
  channels: enum(exact|alias|fts|fuzzy|role)[],
  channel_ranks: {channel:string, rank:integer}[],
  lexical_score: number?, fuzzy_score: number?,
  matched_role_ids: string[], evidence: SourceRef[],
  relevance: enum(confirmed|hypothesis|insufficient)
}
CandidateBatch = {
  candidates: Candidate[], next_cursor: string?,
  examined_card_count: integer,
  exhausted: boolean, truncated: boolean,
  truncation_reason: enum(candidate_limit|deadline)?
}
ConditionCheck = {
  filter_id: string, status: enum(match|mismatch|unknown),
  reason_code: enum(value_match|value_mismatch|missing_attribute|conflicting_values|
                    unknown_price|unknown_quantity_basis|unsupported_condition),
  actual_values: Scalar[], source_refs: SourceRef[]
}
OfferCheck = {
  offer_id: string, card_id: CardId,
  conditions: ConditionCheck[],
  required_match: enum(full|partial|rejected),
  price: Money?, quantity_total: Money?,
  quantity_basis: string?, available: boolean?,
  url: string?, source_refs: SourceRef[]
}
VerifiedCandidate = {
  candidate: Candidate,
  match_level: enum(full|partial|rejected),
  selected_offer: OfferCheck?,
  alternate_offers: OfferCheck[],
  checks_complete_for_card: boolean,
  reason_codes: string[],
  rank_factors: {
    relevance_tier:integer, identity_rank:integer, preference_matches:integer,
    availability_rank:integer, price_minor:integer?,
    popularity:number?, margin:number?, business_factors_mock:boolean
  }
}
```

`lexical_score` разных каналов нельзя складывать напрямую: BM25 и fuzzy имеют разные шкалы. Первичная сборка — round-robin квоты по каналам; окончательный порядок — кортеж факторов из ARCHITECTURE.md. Порядок при равенстве стабилизируется card_id.

Инварианты проверки:

1. Все required-фильтры должны иметь `match` на **одном offer_id** для full. Источники карточки могут подтверждать карточные условия; лицензионные свойства всегда относятся к выбранному офферу.
2. `relevance=hypothesis` или непустые unresolved_constraints запрещают full, даже при подходящей цене. `insufficient` не попадает в выдачу.
3. Known-попадание под exclude → rejected. Unknown проверки exclude → partial с оговоркой. Исключения разрешённых card/vendor ids применяются до retrieval и никогда не ослабляются.
4. При unknown условии цена/наличие не подставляются из другой лицензии. Возможны partial с null ценой и полной причиной.
5. `checks_complete_for_card=false` запрещает вывод «подходящего оффера у карточки нет», но найденный полный свидетель уже может доказать full. Глобальная полнота определяется отдельно CandidateBatch.
6. При отсутствии full обычный отбор partial учитывает число mismatch обязательных условий, затем unknown, предпочтения и доступность. При заданном пределе цены выше частичных вариантов с непроверенными условиями идут предложения в бюджете с известными отличиями и предложения дороже бюджета с подтверждёнными остальными условиями. В одной карточке можно показать по одному предложению из обеих групп. Цена по запросу, несовпадение валют и неподтверждённая стоимость партии не доказывают соблюдение бюджета. Причины невыполненных required условий передаются UI.
7. Прямое имя подтверждает идентичность товара, но не лицензию. Например, `group:121231` не доказывает электронную поставку на два года для юрлица.

Интерфейсы:

```text
interpret(query, session_snapshot, taxonomy, deadline) -> Interpretation
reduce_context(session, interpretation, request_id) -> QueryState + ContextDelta
retrieve(query_state, cursor, card_budget, catalog) -> CandidateBatch
verify(candidate_batch, query_state, catalog, deadline) -> VerifiedCandidate[]
rank(verified_candidates, business_signals) -> VerifiedCandidate[]
```

`ContextDelta` содержит `added_filter_ids`, `replaced_filter_ids`, `removed_filter_ids`, `resolved_reference`, `topic_changed`; reducer не выполняет LLM-вызов. Repository принимает проверенные QueryState и id, не произвольный SQL от интерпретатора.

## 6. Экспертные компоненты и направленные связи

```text
ExpertPlan = {
  plan_id: string, goal: string, scope_limited: boolean,
  assumptions: string[], clarification_question: string?,
  components: {
    component_id:string, label:string, role_ids:string[],
    required:boolean, search_terms:string[], filters:Filter[],
    rationale:string, unknowns:string[]
  }[],
  knowledge_source: enum(external_llm|cached_external_llm),
  model_call_id: string
}
ComponentResult = {
  component_id: string, status: enum(covered|partial|missing),
  label: string?, rationale: string?, unknowns: string[] (optional),
  full_card_ids: CardId[], partial_card_ids: CardId[],
  missing_reason: string?, compatibility_status: enum(not_verified|evidence_available),
  compatibility_evidence: SourceRef[]
}
Relation = {
  relation_id: string,
  source: {type:enum(class|role),id:string},
  target: {type:enum(class|role),id:string},
  relation_type: enum(complements|supports),
  reason: string, propagated_filter_keys: string[],
  source_refs: SourceRef[], version: string
}
RelatedResult = {
  verified: VerifiedCandidate, relation_id: string,
  anchor_card_id: CardId?, anchor_role_id: string?,
  reason: string, compatibility_status: enum(not_verified|evidence_available),
  compatibility_evidence: SourceRef[]
}
```

У экспертного плана нет полей для цены, оффера и придуманного card_id. `plan_id` и component ids назначаются сервером. `model_call_id` при cache hit указывает исходный сохранённый вызов, а событие текущего запроса фиксирует cache hit. Missing-компонент сохраняется в ответе, не удаляется ради красивого списка.

Если подтверждающих материалов совместимости нет, всегда `not_verified`. Наличие ребра Relation не является compatibility_evidence. Для связанного товара обязательны relation_id и хотя бы один anchor; anchors проверяются в текущем каталоге/потребности. Один товар может закрывать несколько экспертных ролей: card_id выводится один раз, component_ids содержит все подтверждённые роли.

```text
plan_expert(interpretation, query_state, taxonomy, deadline) -> ExpertPlan
search_components(plan, catalog, total_budget=200) -> ComponentResult[] + VerifiedCandidate[]
find_related(primary, query_state, relations, remaining_limit) -> RelatedResult[]
```

## 7. Ответ API

```text
ResultItem = {
  card_id: CardId, name: string, vendor_names: string[], kind: enum(software|hardware),
  class_id: string, image_url: string?,
  group: enum(primary_full|primary_partial|related),
  match_level: enum(full|partial), component_ids: string[],
  selected_offer_id: string, price: Money?, quantity_total: Money?,
  price_label: string, available: boolean?, availability_label: string,
  url: string?, url_kind: enum(card|offer|none),
  matched_conditions: ConditionCheck[], unmet_conditions: ConditionCheck[],
  reason: string, caveats: string[], source_refs: SourceRef[],
  relation_id: string?, compatibility_status: enum(not_verified|evidence_available),
  business_factors_mock: boolean,
  selected_offer_name: string?, budget_status: BudgetStatus?, budget_difference: Money?,
  budget_alternative: BudgetAlternative?
}
BudgetStatus = enum(within|over|unconfirmed)
BudgetAlternative = {
  offer_id: string, name: string, price: Money?, price_label: string,
  available: boolean?, url: string?, budget_status: BudgetStatus, budget_difference: Money?,
  matched_conditions: ConditionCheck[], unmet_conditions: ConditionCheck[], reason: string
}
SearchResponse = {
  schema_version: "1.0", request_id: string, client_request_id: string,
  session_id: string, revision: integer, catalog_version: string,
  status: enum(ok|degraded|needs_clarification), mode: enum(direct|functional|expert),
  interpreted_goal: string,
  applied_filters: Filter[], context_delta: ContextDelta,
  items: ResultItem[], components: ComponentResult[],
  expert_assumptions: string[],
  primary_outcome: enum(full|partial|none|undetermined),
  counts: {primary_full:integer,primary_partial:integer,related:integer},
  insufficient_relevant_items: boolean,
  search_truncated: boolean, catalog_absence_confirmed: boolean,
  warnings: string[], suggestions: Suggestion[], clarification_question: string?,
  trace_url: string,
  usage: {llm_attempts:integer,cache_hits:integer,cost_known:string,
          currency:string,cost_complete:boolean,latency_ms:integer},
  provenance: {catalog_data:enum(provided|synthetic_fixture),
               profile_mock:boolean,business_signals_mock:boolean,replay:boolean}
}
Suggestion = {
  label:string, action:enum(query|remove_filter|new_topic),
  query:string?, filter_id:string?
}
```

`budget_difference` — превышение записанного лимита в той же валюте. `budget_alternative` — проверенный оффер того же товара с противоположным бюджетным исходом. Он отображается внутри карточки. Событие перехода по ссылке использует ID выбранного оффера и позицию карточки.

Внутренние rejected-кандидаты доступны в trace, но не в items. Любой displayed item имеет реальный selected_offer_id, даже если цена неизвестна. Лицензионные альтернативы одной карточки не увеличивают счётчик уникальных товаров.

`primary_outcome`: для обычного поиска full, если есть хотя бы одна primary_full; иначе partial при primary_partial; иначе none, если потребность понятна и поиск выполнен; иначе undetermined. Для expert full только при covered всех required компонентов, partial — при покрытии части или только partial-кандидатах; none — нет результатов для required-компонентов при состоявшемся поиске; undetermined — план не получен/невалиден. `status=degraded` — независимое техническое состояние; оно не превращает full в none, но unresolved ограничения исключают full.

`catalog_absence_confirmed=true` разрешено только при доказанно исчерпанной проверке релевантной области и отсутствии гипотетической интерпретации. По умолчанию false; стандартная формулировка — «в проверенной выборке не нашли». `insufficient_relevant_items` рассчитывается по уникальным items < 4 для любого штатного товарного ответа. Не создавать related без обоснования для обнуления этого флага.

Дополнительные endpoints:

| Метод и путь | Контракт |
|---|---|
| `GET /healthz` | `{status:"alive"}` без внешних вызовов |
| `GET /readyz` | `{ready:boolean,catalog_version:string?,storage_ok:boolean,llm_state:string}`; LLM offline не делает готовый lexical fallback неготовым |
| `GET /api/v1/trace/{request_id}` | Упорядоченные EventEnvelope[]; доступ участникам демо, тексты обезличены |
| `POST /api/v1/events` | ClientEvent; 202 `{event_id,accepted:true}`; повтор event_id идемпотентен |
| `GET /api/v1/analytics?from=...&to=...&cohort=real|mock|all` | AnalyticsReport; диапазон UTC `[from,to)`; all возвращает раздельные cohorts |

У аналитики диалогов есть необязательные параметры `mode=all|direct|functional|expert`, `issue`, `offset` и `limit` (по умолчанию 25, максимум 50). `mode` сужает отчёт по темам. `issue` сужает только список тем, чтобы из частоты проблемы можно было перейти к её случаям. `offset` и `limit` применяются к списку каждой когорты. Существующие показатели по отдельным запросам по-прежнему рассчитываются по периоду и когорте.

Ошибки HTTP: 422 неверный ввод, 409 конфликт revision/idempotency, 410 истёкшая сессия, 503 каталог/обязательное хранилище недоступно. LLM fallback возвращается как обычный SearchResponse со status=degraded, а не 500. `ErrorResponse={schema_version,request_id,error:{code,message,retryable},session_id,revision}`; stack trace и секреты в API не возвращаются.

## 8. События первого инкремента

```text
EventEnvelope = {
  schema_version:"1.0", event_id:string, event_type:string,
  request_id:string?, session_id:string?, seq:integer,
  occurred_at:string, received_at:string,
  catalog_version:string?, app_version:string,
  provenance:{catalog_data:enum(provided|synthetic_fixture),
              behavior_mock:boolean,profile_mock:boolean,
              business_signals_mock:boolean,replay:boolean},
  payload: object
}
```

Сервер назначает seq монотонно внутри request. Времена клиентских действий не используются как единственное основание атрибуции. События импорта имеют request_id=null, отдельный run_id в payload. `provided` означает выданный каталог, а не проверенные текущие остатки магазина. Поведение реального тестировщика на предоставленном каталоге не помечается автоматически mock; синтетические клики имеют behavior_mock=true.

Обязательные типы payload:

| event_type | Минимальные поля payload |
|---|---|
| `request_received` | client_request_id, query_redacted, normalized_query, expected_revision, request_kind |
| `interpretation_completed` | mode, goal, role_ids, class_ids, unresolved_constraints, interpretation_source, duration_ms |
| `context_applied` | ContextDelta, effective_filters, topic_id |
| `retrieval_completed` | stage, channels, candidate_ids, new_count, total_count, exhausted, truncated, duration_ms |
| `offer_verification_completed` | card_id, component_id, selected_offer_id, match_level, conditions, checks_complete_for_card, duration_ms |
| `ranking_completed` | ordered_card_ids, rank_factors, business_signals_mock |
| `related_selected` | card_id, relation_id, anchor_card_id/role_id, propagated_filter_ids |
| `expert_plan_completed` | plan_id, component_ids, assumptions, scope_limited, model_call_id |
| `llm_call_started` | call_id, provider, model, purpose, attempt, timeout_ms, request_fingerprint |
| `llm_call_completed` | LlmCallUsage ниже |
| `llm_cache_hit` | purpose, cache_key_hash, original_call_id; новый call_id не создаётся |
| `response_completed` | mode, primary_outcome, counts, displayed_card_ids, displayed_offer_ids, component_statuses, search_truncated, insufficient_relevant_items, latency_ms, cost_known, cost_complete |
| `search_issue` | code, scope, evidence_status, affected_filter_ids, candidate_count |
| `technical_error` | stage, code, retryable, duration_ms, sanitized_message |
| `request_interrupted` | last_stage, known_call_ids, reason |
| `results_impression` | request_id, visible_items[{card_id,group,position}] |
| `result_click` | request_id, card_id, offer_id, group, position |
| `feedback` | request_id, rating enum(useful|not_useful), reason? |
| `mock_order` / `order` | order_id, attributed_request_id, card_ids, total?, attribution_method |

`search_issue.code`: `no_candidates`, `no_matching_offer`, `missing_attributes`, `need_unmapped`, `ambiguous_reference`, `candidate_limit`, `deadline`, `llm_unavailable`, `no_more_results`. `evidence_status=observed|hypothesis`: отсутствие кандидатов обычно не доказывает пробел ассортимента. Не писать `catalog_gap_confirmed` на основании слабого retrieval.

```text
LlmCallUsage = {
  call_id:string, provider_request_id:string?, provider:string, model:string,
  purpose:enum(interpret|expert_plan|offline_enrichment), attempt:integer,
  status:enum(success|timeout|provider_error|invalid_output|refused),
  input_tokens:integer?, cached_input_tokens:integer?, output_tokens:integer?,
  reasoning_tokens:integer?, usage_source:enum(provider|unavailable),
  pricing_version:string?, cost:string?, currency:string,
  cost_status:enum(calculated|unknown), latency_ms:integer,
  error_code:string?
}
```

`cached_input_tokens` — подмножество input; `reasoning_tokens` — детализация output, не дополнительное слагаемое. Provider error может стоить денег: без usage cost=null. Затраты по всем попыткам суммируются, включая невалидный результат и экспертный вызов. Локальный cache hit не является вызовом.

`ClientEvent={event_id,session_id,request_id,occurred_at,event_type,payload}`. Разрешены только impression/click/feedback и mock_order в DEMO_MODE. Сервер сам проставляет provenance, проверяет принадлежность request сессии и наличие card/offer/group/position в сохранённом ответе. Клиент не может выставить себе provider usage, успешный поиск или real order. Реальные order-события принимаются только от будущего доверенного адаптера; в MVP он не заявлен.

## 9. Аналитика и минимальные границы модулей

```text
Metric = {
  value:number?, numerator:integer?, denominator:integer?,
  availability:enum(measured|mock|unavailable|scenario),
  definition:string, limitations:string[]
}
AnalyticsReport = {
  schema_version:"1.0", from:string, to:string, generated_at:string,
  cohorts:{
    name:enum(real|mock),
    metrics:{metric_id:string,metric:Metric}[],
    demand:{goal_or_role:string,requests:integer,sessions:integer}[],
    gaps:{code:string,count:integer,evidence_status:string,recommendation:string}[],
    costs_by_mode:{mode:string,requests:integer,calls:integer,
                    known_cost:string,unknown_calls:integer,currency:string}[],
    dialogues:DialogueReport?
  }[]
}
```

Нулевой знаменатель → value=null. Cohort real означает несинтетические действия демо-пользователей, а не production-трафик. Mixed/replay/business-mock запросы относятся к mock; каталог provided сам по себе не делает cohort mock. Данные synthetic_fixture всегда mock. Все метрики обязаны раскрывать определение и ограничения.

`DialogueReport` содержит метрики, дневную динамику, проблемы с числом тем и страницу историй. В выборку входят темы, начатые в выбранном периоде по `context_applied.topic_changed`. Первые 30 минут после начала темы образуют окно наблюдения. Уточнения могут попасть в это окно и после конца выбранного календарного периода. Свежие темы отображаются с признаком продолжающегося наблюдения. Тема без записанного `context_applied` не восстанавливается по соседним запросам.

История показывает исходы, виды условий и их изменения, распознанные бюджеты, офферы из ответов и зарегистрированные действия. Идентификатор темы в отчёте псевдонимизирован. Текст реплики, формулировка вопроса, исходные идентификаторы запросов и сессий не входят в историю. Запись о показе относится к ответу. Она не подтверждает видимость отдельного оффера, а отсутствие клика не доказывает уход клиента.

| Модуль | Принимает | Возвращает / ответственность |
|---|---|---|
| catalog | Локальный feed + справочники | Snapshot; Card/Offer lookup; индексированные выборки |
| interpretation | Реплика + компактная сессия + taxonomy | Interpretation / ExpertPlan; usage через telemetry |
| session | Сессия + Interpretation | QueryState, ContextDelta; deterministic reducer |
| search | QueryState, snapshot | Candidates, OfferChecks, ранжированные VerifiedCandidates |
| response | Проверенные кандидаты, компоненты, связи | SearchResponse; шаблонные объяснения и suggestions |
| telemetry | EventEnvelope / LlmCallUsage | Durable append; идемпотентность; stdout mirror |
| analytics | Диапазон, cohort | AnalyticsReport по сохранённым событиям |
| api/service | HTTP и общий deadline | Координация, idempotency, фиксация сессии/ответа |
| static | SearchResponse / AnalyticsReport | UI, показ/клик/feedback; без вычисления фактов каталога |

Пограничные fixtures для независимой работы: один full software; partial с отсутствующим term; конфликт одноимённого param; аппаратная позиция; экспертный missing-компонент; related без основного full; unknown цены; LLM timeout; duplicate request. Синтетические fixtures помечены и не подменяют каталог в live-демо. Реальные идентификаторы для приёмки перечислены в IMPLEMENTATION_PLAN.md с указанием фактического источника.

## 10. Интерфейсы первой интеграции

- Модели Pydantic в `src/smart_site_search/contracts/`: `catalog.py` (карточки, офферы, источники, связи), `intent.py` (интерпретация, фильтры, сессия, план), `search.py` (кандидаты и проверка), `api.py`, `events.py`, `analytics.py`.
- P1 экспортирует `CatalogRepository(path)` из `smart_site_search.catalog`. Методы: свойства `catalog_version`, `taxonomy_version`; `get_card(card_id)`, `get_offer(offer_id)` возвращают модель или None; `get_offers(card_id)` возвращает список Offer; `lookup(query, limit=8)` возвращает list[Candidate]; `retrieve(query_state, cursor=None, card_budget=24)` возвращает CandidateBatch; `vendor_name(vendor_id)` возвращает имя; `relations()` возвращает list[Relation]. Предельный бюджет задаёт координатор. Индексный снимок доступен только для чтения из приложения.
- P2 экспортирует `Interpreter` из `smart_site_search.interpretation` с синхронными `interpret(query, session_snapshot, taxonomy, deadline)` и `plan_expert(interpretation, query_state, taxonomy, deadline)`; `reduce_context(session, interpretation, request_id)` из `smart_site_search.session` возвращает `(QueryState, ContextDelta)`. Deadline — абсолютное значение `time.monotonic()`. Taxonomy для интерпретации — компактный dict с `classes`, `roles`, `fields` (справочники по id). Наблюдатель вызовов `emit(event_type, payload)` привязан к одному запросу. Недоступность LLM даёт degraded_rules для интерпретации и None для экспертного плана.
- Общие примеры HTTP в `tests/fixtures/contracts/`. Они синтетические и не требуют локальной БД.
