/** Правила интерфейса, не зависящие от страницы. Каталог здесь не считается. */

export const GROUP_ORDER = ["primary_full", "primary_partial", "related"];
export const WIDGET_ROW_SIZE = 4;

export const GROUP_LABELS = {
  primary_full: "Точное соответствие",
  primary_partial: "Частичное соответствие",
  related: "Связанные товары",
};

const BUDGET_SECTION_LABELS = {
  within: "В бюджете, с оговорками",
  over: "Дороже бюджета, проверенные параметры совпадают",
  unconfirmed: "Другие частичные совпадения",
};

const STAGE_LABELS = {
  request_received: "Запрос принят",
  interpretation_completed: "Интерпретация",
  context_applied: "Контекст",
  retrieval_completed: "Поиск кандидатов",
  offer_verification_completed: "Проверка предложения",
  ranking_completed: "Ранжирование",
  related_selected: "Связанный товар",
  expert_plan_completed: "Экспертный план",
  llm_call_started: "Вызов модели",
  llm_call_completed: "Ответ модели",
  llm_cache_hit: "Ответ из кэша",
  response_completed: "Ответ собран",
  search_issue: "Ограничение поиска",
  technical_error: "Техническая ошибка",
  request_interrupted: "Запрос прерван",
};

export function createDialogState() {
  return {
    sessionId: null,
    expectedRevision: 0,
    selection: null,
    impressed: new Set(),
    pendingBody: null,
  };
}

export function newClientId(cryptoObj = globalThis.crypto) {
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  if (!cryptoObj || typeof cryptoObj.getRandomValues !== "function") {
    throw new Error("crypto недоступен");
  }
  const bytes = new Uint8Array(16);
  cryptoObj.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function buildSearchBody({ query, state, clientRequestId, limit = 8 }) {
  const text = String(query ?? "").trim();
  const sessionId = state.sessionId;
  const selection = sessionId ? state.selection : null;
  return {
    schema_version: "1.0",
    client_request_id: clientRequestId,
    session_id: sessionId,
    expected_revision: sessionId ? state.expectedRevision : 0,
    query: text,
    reference_request_id: selection ? selection.referenceRequestId : null,
    selected_card_id: selection ? selection.cardId : null,
    limit,
  };
}

export function rememberSuccess(state, response) {
  state.sessionId = response.session_id;
  state.expectedRevision = response.revision;
  state.selection = null;
  state.pendingBody = null;
  return state;
}

export function classifyHttpFailure(status, body) {
  const error = body && body.error ? body.error : {};
  const message = typeof error.message === "string" && error.message
    ? error.message
    : "Сервер не принял запрос.";
  const revision = body && Number.isInteger(body.revision) ? body.revision : null;
  if (status === 409) {
    return {
      message,
      retrySameBody: false,
      offerNewSession: false,
      revision,
    };
  }
  if (status === 410) {
    return {
      message,
      retrySameBody: false,
      offerNewSession: true,
      revision: null,
    };
  }
  if (status === 422) {
    return {
      message,
      retrySameBody: false,
      offerNewSession: false,
      revision: null,
    };
  }
  if (status === 503) {
    return {
      message,
      retrySameBody: error.retryable !== false,
      offerNewSession: false,
      revision: null,
    };
  }
  return {
    message,
    retrySameBody: false,
    offerNewSession: false,
    revision: null,
  };
}

export function networkFailure() {
  return {
    message: "Нет ответа от сервера. Повтор отправит тот же запрос.",
    retrySameBody: true,
    offerNewSession: false,
    revision: null,
  };
}

export function applyFailure(state, failure) {
  if (failure.revision !== null) state.expectedRevision = failure.revision;
  if (!failure.retrySameBody) state.pendingBody = null;
  return state;
}

export function safeProductUrl(value) {
  if (typeof value !== "string" || value.trim() === "") return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  return url.href;
}

export function safeTracePath(value) {
  if (typeof value !== "string") return null;
  if (!value.startsWith("/api/v1/trace/")) return null;
  if (value.includes("\\") || value.includes("..") || value.includes("?")) return null;
  return value;
}

export function safeImageUrl(value) {
  return safeProductUrl(value);
}

export function formatMoney(money) {
  if (!money || typeof money.amount_minor !== "number" || typeof money.currency !== "string") {
    return null;
  }
  const formatted = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(money.amount_minor / 100);
  return `${formatted} ${money.currency}`;
}

function budgetMoney(amount, currency) {
  if (!Number.isFinite(amount) || typeof currency !== "string" || !/^[A-Z]{3}$/.test(currency)) {
    return null;
  }
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount / 100);
}

function budgetFilterLabel(filter) {
  if (filter.key !== "price_minor" && filter.key !== "total_price_minor") return null;
  const amount = budgetMoney(filter.values?.[0], filter.currency);
  if (!amount) return null;
  if (filter.op === "lte") return `${filter.key === "total_price_minor" ? "Общий бюджет" : "Бюджет"} до ${amount}`;
  if (filter.op === "gte") return `Цена от ${amount}`;
  if (filter.op === "eq") return `Цена ${amount}`;
  if (filter.op === "between") {
    const upper = budgetMoney(filter.values?.[1], filter.currency);
    if (upper) return `Цена от ${amount} до ${upper}`;
  }
  return null;
}

function conditionLabels(checks, filters) {
  const label = (check) => {
    const filter = filters.get(check.filter_id);
    if (!filter) return check.filter_id;
    const money = budgetFilterLabel(filter);
    if (money) return money;
    const requested = filter.source_text || check.filter_id;
    const actual = check.actual_values ?? [];
    if (check.status !== "mismatch" || !actual.length || !actual.every(Number.isFinite)) return requested;
    const units = { GB: " ГБ", month: " мес.", months: " мес." };
    const values = actual.map((value) => value.toLocaleString("ru-RU")).join(", ");
    return `${requested} (в предложении ${values}${units[filter.unit] ?? ""})`;
  };
  return {
    unconfirmed: checks.filter((check) => check?.status === "unknown").map(label).filter(Boolean),
    mismatched: checks.filter((check) => check?.status === "mismatch").map(label).filter(Boolean),
  };
}

function budgetLabel(item) {
  if (item.budget_status === "within") return "В пределах бюджета";
  if (item.budget_status !== "over") return "";
  const difference = item.budget_difference;
  const amount = budgetMoney(difference?.amount_minor, difference?.currency);
  return amount ? `На ${amount} выше бюджета` : "Выше бюджета";
}

function alternativeView(offer, filters) {
  if (!offer) return null;
  const availability = offer.available === true ? "В наличии по снимку"
    : offer.available === false ? "Нет в наличии по снимку" : "Наличие не подтверждено";
  return {
    offerId: offer.offer_id,
    name: offer.name || "Другое предложение",
    priceLabel: offer.price_label || formatMoney(offer.price),
    availability,
    budgetLabel: budgetLabel(offer),
    budgetStatus: offer.budget_status,
    reason: offer.reason ?? "",
    url: safeProductUrl(offer.url),
    ...conditionLabels(offer.unmet_conditions ?? [], filters),
  };
}

export function cardView(item, position, filters = new Map()) {
  const vendors = Array.isArray(item.vendor_names) ? item.vendor_names.filter(Boolean) : [];
  const unmet = Array.isArray(item.unmet_conditions) ? item.unmet_conditions : [];
  const url = safeProductUrl(item.url);
  return {
    position,
    cardId: item.card_id,
    offerId: item.selected_offer_id,
    offerName: item.selected_offer_name === item.name ? null : item.selected_offer_name,
    group: item.group,
    name: item.name ?? "",
    vendors: vendors.length ? vendors.join(", ") : "Вендор не указан",
    kind: item.kind === "hardware" ? "Оборудование" : "Программа",
    priceLabel: item.price_label ?? "",
    priceMissing: item.price == null,
    batchTotal: item.quantity_total == null ? null : formatMoney(item.quantity_total),
    availabilityLabel: item.availability_label ?? "",
    availabilityState: item.available === true ? "yes" : item.available === false ? "no" : "unknown",
    url,
    urlRejected: Boolean(item.url) && !url,
    reason: item.reason ?? "",
    caveats: Array.isArray(item.caveats) ? item.caveats : [],
    ...conditionLabels(unmet, filters),
    budgetStatus: item.budget_status ?? null,
    budgetLabel: budgetLabel(item),
    budgetAlternative: alternativeView(item.budget_alternative, filters),
    compatibility: item.compatibility_status === "evidence_available"
      ? "Есть ссылка на сведения о совместимости. Вывод о совместимости не добавляется."
      : "Совместимость не проверена.",
    businessMock: Boolean(item.business_factors_mock),
    imageUrl: safeImageUrl(item.image_url),
  };
}

export function companionView(item) {
  const vendors = Array.isArray(item.vendor_names) ? item.vendor_names.filter(Boolean) : [];
  return {
    cardId: item.card_id,
    name: item.name ?? "",
    vendors: vendors.length ? vendors.join(", ") : "Вендор не указан",
    kind: item.kind === "hardware" ? "Оборудование" : "Программа",
    roleLabel: item.role_label ?? "",
    url: safeProductUrl(item.url),
    imageUrl: safeImageUrl(item.image_url),
  };
}

function widgetFace(item, status) {
  return {
    cardId: item.cardId,
    name: item.name ?? "",
    imageUrl: item.imageUrl || null,
    priceLabel: item.priceLabel || "",
    status,
    url: item.url || null,
    offerId: item.offerId || null,
    group: item.group || "",
    position: item.position ?? null,
  };
}

export function widgetBoard(view, shownCount = WIDGET_ROW_SIZE) {
  const groups = Array.isArray(view?.groups) ? view.groups : [];
  const primary = groups
    .filter((group) => group.id === "primary_full" || group.id === "primary_partial")
    .flatMap((group) => group.items);
  const visibleCount = Math.max(0, shownCount);
  const visible = primary.slice(0, visibleCount);
  const companions = Array.isArray(view?.companions) ? view.companions : [];
  const related = groups.find((group) => group.id === "related")?.items ?? [];
  const extra = companions.length
    ? companions.map((item) => widgetFace(item, item.roleLabel || ""))
    : related.map((item) => widgetFace(item, ""));
  return {
    cards: visible.map((item) => widgetFace(
      item,
      item.group === "primary_full" ? "Подходит" : "Частично",
    )),
    remaining: Math.max(0, primary.length - visible.length),
    nextCount: Math.min(primary.length, visibleCount + WIDGET_ROW_SIZE),
    companions: extra,
  };
}

export function orderedItems(items) {
  const source = Array.isArray(items) ? items : [];
  const ordered = [];
  for (const group of GROUP_ORDER) {
    for (const item of source) {
      if (item && item.group === group) ordered.push(item);
    }
  }
  return ordered;
}

function budgetSections(items) {
  if (!items.some((item) => item.budgetStatus)) return [];
  const sections = [];
  for (const item of items) {
    const title = BUDGET_SECTION_LABELS[item.budgetStatus] || GROUP_LABELS.primary_partial;
    if (sections.at(-1)?.title !== title) sections.push({ title, items: [] });
    sections.at(-1).items.push(item);
  }
  return sections;
}

export function responseView(response) {
  const filters = response.applied_filters ?? [];
  const filtersById = new Map(filters.map((filter) => [filter.filter_id, filter]));
  const items = orderedItems(response.items).map((item, index) => cardView(item, index + 1, filtersById));
  const firstPrimary = items.find((item) => item.group === "primary_full" || item.group === "primary_partial");
  const companions = firstPrimary && Array.isArray(response.companions) && response.companions.length
    ? response.companions.map(companionView)
    : [];
  const groups = GROUP_ORDER.map((id) => {
    const groupItems = items.filter((item) => item.group === id);
    return { id, title: GROUP_LABELS[id], items: groupItems,
      sections: id === "primary_partial" ? budgetSections(groupItems) : [] };
  }).filter((group) => group.items.length > 0);
  return {
    requestId: response.request_id,
    sessionId: response.session_id,
    revision: response.revision,
    status: response.status,
    degraded: response.status === "degraded",
    needsClarification: response.status === "needs_clarification",
    mode: response.mode ?? "",
    goal: response.interpreted_goal ?? "",
    warnings: Array.isArray(response.warnings) ? response.warnings : [],
    insufficient: Boolean(response.insufficient_relevant_items),
    groups,
    companions,
    companionAnchorId: companions.length ? firstPrimary.cardId : null,
    components: (response.components ?? []).map((component) => ({
      id: component.component_id,
      label: component.label ?? component.component_id,
      rationale: component.rationale ?? "",
      unknowns: Array.isArray(component.unknowns) ? component.unknowns : [],
      status: component.status,
      missingReason: component.missing_reason ?? "",
      fullCardIds: component.full_card_ids ?? [],
      partialCardIds: component.partial_card_ids ?? [],
      compatibility: component.compatibility_status,
    })),
    assumptions: response.expert_assumptions ?? [],
    suggestions: response.suggestions ?? [],
    clarificationQuestion: response.clarification_question ?? "",
    traceUrl: safeTracePath(response.trace_url),
    usage: costView(response.usage),
    syntheticCatalog: response.provenance?.catalog_data === "synthetic_fixture",
    filters: filters.map((filter) => (
      budgetFilterLabel(filter) || `${filter.key} ${filter.op} ${(filter.values ?? []).join(", ")}`
    )),
  };
}

export function costView(usage) {
  if (!usage) {
    return { complete: false, text: "Стоимость не передана." };
  }
  const known = `${usage.cost_known ?? ""} ${usage.currency ?? ""}`.trim();
  if (usage.cost_complete) {
    return { complete: true, text: `Стоимость ${known}.` };
  }
  return {
    complete: false,
    text: `Известная часть ${known}. Учёт неполный: неизвестные вызовы не заменяются нулём.`,
  };
}

export function claimImpression(state, requestId) {
  if (!requestId || state.impressed.has(requestId)) return false;
  state.impressed.add(requestId);
  return true;
}

export function clientEvent({ eventId, sessionId, requestId, eventType, payload, occurredAt }) {
  return {
    event_id: eventId,
    session_id: sessionId,
    request_id: requestId,
    occurred_at: occurredAt,
    event_type: eventType,
    payload,
  };
}

export function impressionPayload(view) {
  return {
    request_id: view.requestId,
    visible_items: view.groups.flatMap((group) => group.items.map((item) => ({
      card_id: item.cardId,
      group: item.group,
      position: item.position,
    }))),
  };
}

export function traceSummary(events, usage) {
  const list = Array.isArray(events) ? [...events] : [];
  list.sort((left, right) => (left.seq ?? 0) - (right.seq ?? 0));
  const interpretation = list.find((event) => event.event_type === "interpretation_completed");
  return {
    stages: list.map((event) => ({
      seq: event.seq,
      label: STAGE_LABELS[event.event_type] || event.event_type || "Этап",
    })),
    interpretationSource: interpretation?.payload?.interpretation_source ?? "",
    usage: costView(usage),
  };
}

export function suggestionQuery(suggestion) {
  if (suggestion.query) return suggestion.query;
  if (suggestion.action === "remove_filter" && suggestion.filter_id) {
    return `убери фильтр ${suggestion.filter_id}`;
  }
  return suggestion.label || "";
}

export function analyticsRows(report) {
  return (report.cohorts ?? []).map((cohort) => ({
    name: cohort.name,
    title: cohort.name === "real" ? "Реальные действия демо" : "Макет и синтетика",
    metrics: (cohort.metrics ?? []).map((entry) => ({
      id: entry.metric_id,
      availability: entry.metric?.availability ?? "unavailable",
      value: entry.metric?.value ?? null,
      numerator: entry.metric?.numerator ?? null,
      denominator: entry.metric?.denominator ?? null,
      definition: entry.metric?.definition ?? "",
      limitations: entry.metric?.limitations ?? [],
    })),
    demand: cohort.demand ?? [],
    gaps: cohort.gaps ?? [],
    costs: cohort.costs_by_mode ?? [],
  }));
}

export function periodBounds(fromDate, toDate) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fromDate) || !/^\d{4}-\d{2}-\d{2}$/.test(toDate)) {
    return { error: "Укажите обе даты." };
  }
  if (fromDate >= toDate) {
    return { error: "Начало периода должно быть раньше конца. Конец в интервал не входит." };
  }
  return {
    from: `${fromDate}T00:00:00Z`,
    to: `${toDate}T00:00:00Z`,
  };
}
