import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  analyticsRows,
  applyFailure,
  buildSearchBody,
  claimImpression,
  classifyHttpFailure,
  costView,
  createDialogState,
  impressionPayload,
  networkFailure,
  newClientId,
  periodBounds,
  responseView,
  safeProductUrl,
  widgetBoard,
  WIDGET_ROW_SIZE,
  suggestionQuery,
  traceSummary,
} from "../../src/smart_site_search/static/js/model.js";

const fixtures = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures", "contracts");
const read = (name) => JSON.parse(readFileSync(join(fixtures, name), "utf8"));

test("дополнения привязаны к первому товару и не появляются без карточек", () => {
  const response = read("search-functional.json");
  const empty = responseView(response);
  assert.deepEqual(empty.companions, []);
  assert.equal(empty.companionAnchorId, null);
  const view = responseView({
    ...response,
    companions: [{
      card_id: "group:4069",
      name: "Dr.Web Desktop Security Suite",
      vendor_names: ["Доктор Веб"],
      kind: "software",
      class_id: "endpoint_protection",
      role_label: "Антивирус",
      url: "https://example.org/cards/drweb",
      anchor_card_id: response.items[0].card_id,
      selected_offer_id: "должно игнорироваться",
      price_label: "1 ₽",
    }],
  });
  assert.equal(view.companionAnchorId, response.items[0].card_id);
  assert.equal(view.companions.length, 1);
  assert.equal(view.companions[0].roleLabel, "Антивирус");
  assert.equal(view.companions[0].url, "https://example.org/cards/drweb");
  assert.equal(view.companions[0].offerId, undefined);
  assert.equal(view.companions[0].priceLabel, undefined);
  const noPrimary = responseView({
    ...response,
    items: [],
    companions: [{
      card_id: "group:4069", name: "Dr.Web", vendor_names: [], kind: "software",
      role_label: "Антивирус", anchor_card_id: "group:demo",
    }],
  });
  assert.deepEqual(noPrimary.companions, []);
});

test("три группы, неизвестная цена и предупреждение о нехватке карточек", () => {
  const view = responseView(read("search-functional.json"));
  assert.deepEqual(view.groups.map((group) => group.id), ["primary_full", "primary_partial", "related"]);
  const partial = view.groups[1].items[0];
  assert.equal(partial.priceLabel, "Цена по запросу");
  assert.equal(partial.priceMissing, true);
  assert.equal(partial.batchTotal, null);
  assert.equal(partial.availabilityState, "unknown");
  assert.deepEqual(partial.unconfirmed, ["fixture-term"]);
  const related = view.groups[2].items[0];
  assert.equal(related.group, "related");
  assert.match(related.compatibility, /не проверена/);
  assert.equal(view.insufficient, true);
  assert.equal(view.warnings[0], "Найдено три обоснованных позиции.");
  assert.equal(safeProductUrl(view.groups[0].items[0].url), "https://example.org/products/demo-1");
});

test("варианты бюджета показывают условия, превышение и альтернативу в той же карточке", () => {
  const response = read("search-functional.json");
  const partial = response.items[1];
  const difference = { amount_minor: 1323600, currency: "RUB" };
  const view = responseView({
    ...response,
    applied_filters: [
      { filter_id: "price", key: "price_minor", op: "lte", values: [9000000], currency: "RUB" },
      { filter_id: "organization", key: "organization_type", source_text: "коммерческому юрлицу" },
      { filter_id: "platform", key: "platform", source_text: "платформа виртуализации" },
      { filter_id: "vram", key: "vram_gb", source_text: "от 24 ГБ", unit: "GB" },
    ],
    items: [
      {
        ...partial,
        budget_status: "within",
        selected_offer_name: "Лицензия для образовательных организаций",
        unmet_conditions: [{ filter_id: "organization", status: "mismatch" },
          { filter_id: "vram", status: "mismatch", actual_values: [16] }],
        budget_alternative: {
          offer_id: "commercial",
          name: "Лицензия для коммерческих организаций",
          price_label: "103 236 ₽",
          available: true,
          budget_status: "over",
          budget_difference: difference,
          unmet_conditions: [{ filter_id: "price", status: "mismatch" }],
          url: "https://example.org/offers/commercial",
        },
      },
      { ...partial, card_id: "group:over", budget_status: "over", budget_difference: difference },
      { ...partial, card_id: "group:unknown", budget_status: "unconfirmed",
        unmet_conditions: [{ filter_id: "platform", status: "unknown" }] },
    ],
  });
  assert.match(view.filters[0], /^Бюджет до 90\s000\s₽$/);
  const [within, over, unconfirmed] = view.groups[0].sections;
  assert.equal(within.title, "В бюджете, с оговорками");
  assert.equal(over.title, "Дороже бюджета, проверенные параметры совпадают");
  assert.equal(unconfirmed.title, "Другие частичные совпадения");
  const [first, second, third] = view.groups[0].items;
  assert.deepEqual(first.mismatched, ["коммерческому юрлицу", "от 24 ГБ (в предложении 16 ГБ)"]);
  assert.equal(first.offerName, "Лицензия для образовательных организаций");
  assert.equal(first.budgetAlternative.offerId, "commercial");
  assert.equal(first.budgetAlternative.url, "https://example.org/offers/commercial");
  assert.match(first.budgetAlternative.budgetLabel, /^На 13\s236\s₽ выше бюджета$/);
  assert.equal(second.budgetLabel, first.budgetAlternative.budgetLabel);
  assert.equal(third.budgetLabel, "");
  assert.deepEqual(third.unconfirmed, ["платформа виртуализации"]);
  assert.deepEqual(impressionPayload(view).visible_items.map((item) => [item.group, item.position]), [
    ["primary_partial", 1], ["primary_partial", 2], ["primary_partial", 3],
  ]);
});

test("экспертный missing остаётся отдельным компонентом без карточки", () => {
  const view = responseView(read("search-expert.json"));
  const missing = view.components.find((component) => component.status === "missing");
  assert.equal(missing.id, "virtualization");
  assert.equal(missing.missingReason, "Уточните гипервизор");
  assert.deepEqual(missing.fullCardIds, []);
  assert.equal(view.needsClarification, true);
  assert.deepEqual(view.assumptions, ["Для копий потребуется отдельное хранилище."]);
});

test("degraded не превращает неизвестную стоимость в полный ноль", () => {
  const view = responseView(read("search-degraded.json"));
  assert.equal(view.degraded, true);
  assert.equal(view.groups.length, 0);
  assert.equal(view.usage.complete, false);
  assert.match(view.usage.text, /Учёт неполный/);
  assert.equal(costView({ cost_known: "0.25", currency: "USD", cost_complete: true }).complete, true);
});

test("конфликт revision обновляет ожидаемую ревизию и не просит повтор того же тела", () => {
  const failure = classifyHttpFailure(409, read("error-revision.json"));
  assert.equal(failure.revision, 2);
  assert.equal(failure.retrySameBody, false);
  assert.match(failure.message, /уже обновился/);
  const state = createDialogState();
  state.sessionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  state.expectedRevision = 1;
  state.pendingBody = { client_request_id: "same" };
  applyFailure(state, failure);
  assert.equal(state.expectedRevision, 2);
  assert.equal(state.pendingBody, null);
});

test("сетевой повтор сохраняет тело, новая реплика получает новый id", () => {
  const state = createDialogState();
  const firstId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const body = buildSearchBody({ query: "  astra linux  ", state, clientRequestId: firstId });
  assert.equal(body.session_id, null);
  assert.equal(body.expected_revision, 0);
  assert.equal(body.query, "astra linux");
  state.pendingBody = body;
  const failure = networkFailure();
  applyFailure(state, failure);
  assert.equal(state.pendingBody.client_request_id, firstId);
  state.sessionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  state.expectedRevision = 1;
  state.selection = {
    referenceRequestId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    cardId: "group:demo-1",
  };
  const next = buildSearchBody({
    query: "для юрлица",
    state,
    clientRequestId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  });
  assert.notEqual(next.client_request_id, firstId);
  assert.equal(next.reference_request_id, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  assert.equal(next.selected_card_id, "group:demo-1");
  assert.equal(next.expected_revision, 1);
});

test("показ одного ответа отправляется один раз и содержит три группы", () => {
  const state = createDialogState();
  const view = responseView(read("search-functional.json"));
  assert.equal(claimImpression(state, view.requestId), true);
  assert.equal(claimImpression(state, view.requestId), false);
  const payload = impressionPayload(view);
  assert.deepEqual(payload.visible_items.map((item) => item.group), [
    "primary_full", "primary_partial", "related",
  ]);
  assert.deepEqual(payload.visible_items.map((item) => item.position), [1, 2, 3]);
});

test("небезопасная ссылка не попадает в карточку", () => {
  assert.equal(safeProductUrl("javascript:alert(1)"), null);
  assert.equal(safeProductUrl("data:text/html,hi"), null);
  assert.equal(safeProductUrl("https://user:pass@example.org/a"), null);
  const view = responseView({
    ...read("search-functional.json"),
    items: [{
      ...read("search-functional.json").items[0],
      name: "<script>alert(1)</script>",
      url: "javascript:alert(1)",
      budget_alternative: { offer_id: "unsafe", url: "javascript:alert(1)" },
    }],
  });
  assert.equal(view.groups[0].items[0].name, "<script>alert(1)</script>");
  assert.equal(view.groups[0].items[0].url, null);
  assert.equal(view.groups[0].items[0].urlRejected, true);
  assert.equal(view.groups[0].items[0].budgetAlternative.url, null);
});

test("предложение без текста фильтра становится явной репликой", () => {
  assert.equal(suggestionQuery({ label: "Уточнить", action: "query", query: "для linux" }), "для linux");
  assert.equal(
    suggestionQuery({ label: "Снять срок", action: "remove_filter", filter_id: "term" }),
    "убери фильтр term",
  );
});

test("аналитика сохраняет пустое значение и помечает макет", () => {
  const rows = analyticsRows(read("analytics.json"));
  assert.equal(rows[0].metrics[0].value, null);
  assert.equal(rows[0].metrics[0].availability, "unavailable");
  assert.equal(rows[1].metrics[0].availability, "mock");
  assert.equal(rows[1].metrics[0].value, 0.5);
  assert.equal(periodBounds("2026-11-28", "2026-11-27").error.length > 0, true);
  assert.deepEqual(periodBounds("2026-11-27", "2026-11-28"), {
    from: "2026-11-27T00:00:00Z",
    to: "2026-11-28T00:00:00Z",
  });
});

test("идентификатор запроса строится и на localhost без randomUUID", () => {
  const bytes = Uint8Array.from({ length: 16 }, (_, index) => index);
  const id = newClientId({ getRandomValues: (target) => target.set(bytes) });
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const native = newClientId({ randomUUID: () => "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" });
  assert.equal(native, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
});

test("виджет показывает четыре основных товара и сопутствующие под первой строкой", () => {
  const base = read("search-functional.json");
  const partial = base.items.find((item) => item.group === "primary_partial");
  const extra = [2, 3, 4, 5, 6].map((number) => ({
    ...partial,
    card_id: `group:${number}`,
    name: `Товар ${number}`,
  }));
  const view = responseView({
    ...base,
    items: [base.items[0], ...extra, base.items.find((item) => item.group === "related")],
    companions: [{
      card_id: "group:4069",
      name: "Dr.Web",
      vendor_names: [],
      kind: "software",
      role_label: "Антивирус",
      anchor_card_id: base.items[0].card_id,
    }],
  });
  const first = widgetBoard(view);
  assert.equal(WIDGET_ROW_SIZE, 4);
  assert.equal(first.cards.length, 4);
  assert.equal(first.remaining, 2);
  assert.equal(first.cards[0].status, "Подходит");
  assert.equal(first.cards[1].status, "Частично");
  assert.equal(first.cards[0].priceLabel.length > 0, true);
  assert.deepEqual(first.companions.map((card) => card.status), ["Антивирус"]);
  assert.equal(first.companions[0].priceLabel, "");
  const opened = widgetBoard(view, first.nextCount);
  assert.equal(opened.cards.length, 6);
  assert.equal(opened.remaining, 0);
  assert.equal(opened.companions.length, 1);
  const quiet = widgetBoard(responseView({ ...base, items: [], companions: [] }));
  assert.deepEqual(quiet.cards, []);
  assert.deepEqual(quiet.companions, []);
});

test("страницы содержат поле запроса, диалог и период аналитики", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "smart_site_search", "static");
  const search = readFileSync(join(root, "index.html"), "utf8");
  const analytics = readFileSync(join(root, "analytics.html"), "utf8");
  assert.match(search, /id="query"/);
  assert.match(search, /id="thread"/);
  assert.match(search, /Новая сессия/);
  assert.match(search, /\/static\/js\/search\.js/);
  assert.match(analytics, /id="from"/);
  assert.match(analytics, /id="cohort"/);
  assert.match(analytics, /не подставляются/);
  const widget = readFileSync(join(root, "widget.html"), "utf8");
  assert.match(widget, /id="launcher"/);
  assert.match(widget, /Очистить историю/);
  assert.match(widget, /\/static\/js\/widget\.js/);
  const widgetCss = readFileSync(join(root, "css", "widget.css"), "utf8");
  assert.match(widgetCss, /height: min\(80rem, calc\(100vh - 2rem\)\)/);
  assert.match(widgetCss, /#e8bf8c 0%, #91694a 100%/);
  assert.ok(widget.indexOf('id="operator"') < widget.indexOf('id="clear"'));
  assert.ok(widget.indexOf('id="operator"') < widget.indexOf('id="log"'));
  assert.match(widgetCss, /\.panel-head[\s\S]*#e8bf8c 0%, #91694a 100%/);
  assert.doesNotMatch(widget, /id="thread"/);
});

test("трасса показывает источник интерпретации и этапы по порядку", () => {
  const summary = traceSummary(read("trace.json"), { cost_known: "0", currency: "USD", cost_complete: false });
  assert.deepEqual(summary.stages.map((stage) => stage.seq), [1, 2, 3]);
  assert.equal(summary.interpretationSource, "degraded_rules");
  assert.equal(summary.usage.complete, false);
  assert.doesNotMatch(summary.usage.text, /НДС/);
});

test("трасса называет вызов, токены и рублёвую стоимость с НДС", () => {
  const summary = traceSummary([
    { seq: 1, event_type: "llm_call_started", payload: { purpose: "interpret" } },
    { seq: 2, event_type: "llm_call_completed", payload: {
      purpose: "interpret", input_tokens: 2287, cached_input_tokens: 12, output_tokens: 107,
    } },
    { seq: 3, event_type: "llm_call_started", payload: { purpose: "expert_plan" } },
    { seq: 4, event_type: "retrieval_completed", payload: {} },
    { seq: 5, event_type: "offer_verification_completed", payload: {} },
  ], { cost_known: "4.16", currency: "RUB", cost_complete: true });
  assert.equal(summary.stages[0].label, "Вызов модели: интерпретация");
  assert.equal(summary.stages[1].label, "Ответ модели: интерпретация, 2287 входных, 12 из кэша, 107 выходных");
  assert.equal(summary.stages[2].label, "Вызов модели: план");
  assert.equal(summary.stages[3].label, "Поиск кандидатов");
  assert.equal(summary.stages[4].label, "Проверка предложения");
  assert.equal(summary.usage.text, "Стоимость 4.16 RUB с НДС.");
});
