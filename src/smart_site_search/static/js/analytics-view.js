import { analyticsRows } from "./model.js";

const NUMBER = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const PERCENT = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });
const TIMESTAMP = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" });
const DATE = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" });

const MODES = {
  direct: "Прямой",
  functional: "Функциональный",
  expert: "Экспертный",
};
const OUTCOMES = {
  full: "Полное совпадение",
  partial: "Частичное совпадение",
  none: "Нет совпадения",
  undetermined: "Исход не определён",
  error: "Ошибка",
  pending: "Нет итогового ответа",
};
const NEEDS = {
  server_hardware: "Серверы",
  operating_system: "Операционные системы",
  backup_restore: "Резервное копирование",
  endpoint_security: "Защита устройств",
  office_software: "Офисные приложения",
  gpu: "Видеокарты",
  ups: "Источники бесперебойного питания",
  laptop: "Ноутбуки",
  storage: "Системы хранения",
  nas: "Сетевые хранилища",
  workstation: "Рабочие станции",
  network_hardware: "Сетевое оборудование",
  remote_access: "Удалённый доступ",
  directory_service: "Службы каталогов",
};
const ISSUES = {
  no_matching_offer: "Нет подтверждённого совпадения по условиям",
  missing_attributes: "Не хватает характеристик",
  candidate_limit: "Ограничена выборка кандидатов",
  need_unmapped: "Потребность не сопоставлена с каталогом",
  llm_unavailable: "Недоступна внешняя интерпретация",
  empty_results: "Пустая выдача",
  no_results: "Пустая выдача",
  no_candidates: "В проверенной выборке нет кандидатов",
  technical_error: "Техническая ошибка",
  request_interrupted: "Запрос прерван",
  repeated_problem: "Проблема повторилась",
  budget_over: "Есть вариант дороже бюджета",
  budget_unknown: "Стоимость не подтверждена",
  budget_unconfirmed: "Стоимость не подтверждена",
  budget_within_alternative: "Вариант в бюджете с отличиями",
  negative_feedback: "Отрицательная оценка",
};
const METRICS = {
  topics: "Темы диалогов",
  observing_topics: "Темы с открытым окном наблюдения",
  first_full_rate: "Полное совпадение с первого ответа",
  recovery_rate: "Восстановление после неудачного старта",
  recovered_preserved: "Восстановились при исходных условиях",
  recovered_changed: "Восстановились после изменения условий",
  clarification_followup_rate: "Продолжили после уточнения",
  clarification_full_rate: "Полное совпадение после уточнения",
  repeated_problem_rate: "Проблема повторилась",
  helpful_rate: "Полезно по оценкам клиентов",
  offer_click_topics: "Темы с переходом к офферу",
  alternative_click_topics: "Темы с переходом к альтернативе",
  budget_topics: "Темы с бюджетом",
  budget_full_topics: "Полное совпадение в бюджете",
  budget_over_topics: "Есть вариант дороже бюджета",
  budget_unknown_topics: "Стоимость не подтверждена",
  budget_within_topics: "Вариант в бюджете с отличиями",
  primary_full_rate: "Полные совпадения",
  primary_partial_rate: "Частичные совпадения",
  primary_none_rate: "Нет совпадения",
  primary_undetermined_rate: "Исход не определён",
  empty_rate: "Пустая выдача",
  shown_rate: "Показы блока ответа",
  related_only_rate: "Только связанные товары",
  ctr: "CTR по текущим событиям",
  negative_rate: "Отрицательные оценки",
  degraded_rate: "Ответы в режиме деградации",
  errors_rate: "Ошибки запроса",
  trace_complete_rate: "Полнота трассы",
  p50_ms: "Медианная задержка, мс",
  p95_ms: "95-й процентиль задержки, мс",
  order_conversion: "Конверсия в заказ",
  exit_after_failure: "Уход после неудачного поиска",
  lost_orders: "Оценка потерянных заказов",
};
const FILTERS = {
  price_minor: "Бюджет",
  total_price_minor: "Бюджет закупки",
  users: "Пользователи",
  seats: "Рабочие места",
  devices: "Устройства",
  quantity: "Количество",
  organization_type: "Тип организации",
  use_case: "Назначение",
  license_type: "Тип лицензии",
  term_months: "Срок действия",
  product_form: "Форма продукта",
  os: "Операционная система",
  availability: "Наличие",
};
const GROUPS = {
  primary_full: "Полное совпадение",
  primary_partial: "Частичное совпадение",
  related: "Связанный товар",
};
const BUDGET_STATES = {
  within: "В бюджете",
  over: "Выше бюджета",
  unconfirmed: "Бюджет или условия не подтверждены",
};

function label(labels, key) {
  return Object.hasOwn(labels, key) ? labels[key] : String(key ?? "").replaceAll("_", " ");
}

export function issueLabel(code) {
  return label(ISSUES, code);
}

export function metricValue(metric) {
  if (metric.value === null || metric.value === undefined) return "Нет данных";
  return metric.denominator > 0 ? PERCENT.format(metric.value) : NUMBER.format(metric.value);
}

function fraction(metric) {
  if (metric.numerator == null || metric.denominator == null) return "";
  return `${NUMBER.format(metric.numerator)} из ${NUMBER.format(metric.denominator)}`;
}

export function formatMoney(money) {
  if (!money || !Number.isFinite(money.amount_minor) || !/^[A-Z]{3}$/.test(money.currency)) return "";
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: money.currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(money.amount_minor / 100);
}

export function formatBudget(budget) {
  const values = budget.values.map((amount_minor) => formatMoney({ amount_minor, currency: budget.currency }));
  const amount = values.filter(Boolean).join(" — ");
  if (!amount) return "";
  const operator = { lte: "до ", gte: "от ", lt: "менее ", gt: "свыше " };
  return `${label(FILTERS, budget.key)} ${operator[budget.op] ?? ""}${amount}`;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function note(text) {
  return element("p", text, "fine");
}

function button(text, action, className = "secondary") {
  const node = element("button", text, className);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function table(headers, rows) {
  const wrapper = element("div", null, "analytics-table");
  const grid = element("table");
  const head = grid.createTHead().insertRow();
  for (const text of headers) {
    const node = element("th", text);
    node.scope = "col";
    head.append(node);
  }
  const body = grid.createTBody();
  for (const cells of rows) {
    const row = body.insertRow();
    for (const value of cells) {
      const cell = row.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    }
  }
  wrapper.append(grid);
  return wrapper;
}

function metricStatus(metric) {
  const labels = { measured: "Измерено", mock: "Макет", unavailable: "Нет данных", scenario: "Сценарий" };
  return element("span", labels[metric.availability] ?? "Нет данных", `tag ${metric.availability ?? "unavailable"}`);
}

function metricsTable(metrics) {
  if (!metrics.length) return note("Метрик за период нет.");
  return table(["Показатель", "Значение", "Выборка", "Статус", "Определение"], metrics.map((metric) => [
    metric.id.startsWith("median_over_budget_")
      ? `Медиана превышения, ${metric.id.slice("median_over_budget_".length).toUpperCase()}`
      : label(METRICS, metric.id),
    metricValue(metric), fraction(metric) || "—", metricStatus(metric),
    [metric.definition, ...(metric.limitations ?? [])].filter(Boolean).join(" "),
  ]));
}

function disclosure(title, ...content) {
  const details = element("details", null, "analytics-disclosure");
  details.append(element("summary", title), ...content);
  return details;
}

function legacyOverview(cohort, mode) {
  const subhead = mode === "all" ? "Аналитика по запросам" : "Аналитика по запросам · все режимы";
  const content = [
    metricsTable(cohort.metrics),
    element("h3", "Спрос"),
    cohort.demand.length ? table(["Потребность", "Запросы", "Сессии"], cohort.demand.map((item) => [
      label(NEEDS, item.goal_or_role), item.requests, item.sessions,
    ])) : note("Нет сведений о спросе за период."),
    element("h3", "Пробелы"),
    cohort.gaps.length ? table(["Причина", "Запросы", "Основание", "Что проверить"], cohort.gaps.map((gap) => [
      issueLabel(gap.code), gap.count, gap.evidence_status, gap.recommendation,
    ])) : note("Пробелы в запросах за период не зафиксированы."),
    element("h3", "Экономика"),
    cohort.costs.length ? table(["Режим", "Запросы", "Вызовы", "Известная стоимость", "Неизвестные вызовы"],
      cohort.costs.map((cost) => [
        label(MODES, cost.mode), cost.requests, cost.calls, `${cost.known_cost} ${cost.currency}`, cost.unknown_calls,
      ])) : note("Расходы за период не зафиксированы."),
  ];
  return disclosure(subhead, ...content);
}

function trendTable(trend) {
  const peak = trend.reduce((max, day) => Math.max(max, day.topics), 1);
  return table(["День, UTC", "Темы", "С первого ответа", "Восстановились"], trend.map((day) => {
    const activity = element("div", null, "analytics-activity");
    const meter = element("meter");
    meter.min = 0;
    meter.max = peak;
    meter.value = day.topics;
    meter.setAttribute("aria-hidden", "true");
    activity.append(element("span", NUMBER.format(day.topics)), meter);
    return [DATE.format(new Date(day.date)), activity, day.first_full, day.recovered];
  }));
}

function overview(section, cohort, dialogues, mode) {
  if (dialogues) {
    const metrics = dialogues.metrics.map((entry) => ({ id: entry.metric_id, ...entry.metric }));
    const headlineIds = ["topics", "first_full_rate", "recovery_rate", "helpful_rate"];
    const cards = element("div", null, "analytics-kpis");
    for (const id of headlineIds) {
      const metric = metrics.find((entry) => entry.id === id);
      if (!metric) continue;
      const card = element("div", null, "analytics-kpi");
      card.append(
        element("span", label(METRICS, id), "analytics-kpi-label"),
        element("strong", metricValue(metric)),
      );
      const sample = fraction(metric);
      if (sample) card.append(element("span", sample, "fine"));
      cards.append(card);
    }
    section.append(cards, note(`Окно наблюдения — ${dialogues.observation_minutes} мин с первого запроса темы.`));
    if (dialogues.unattributed_requests > 0) {
      section.append(note(`Запросы без определённой темы — ${NUMBER.format(dialogues.unattributed_requests)}.`));
    }
    if (dialogues.trend.length) {
      section.append(element("h3", "Темы по дням"), trendTable(dialogues.trend));
    }
    if (metrics.length) section.append(disclosure("Все показатели и методика", metricsTable(metrics)));
  } else {
    section.append(note("Сведения о темах недоступны в ответе сервера."));
  }
  section.append(legacyOverview(cohort, mode));
}

function dateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Дата неизвестна" : `${TIMESTAMP.format(date)} UTC`;
}

function outcome(key) {
  return element("span", label(OUTCOMES, key), `analytics-outcome analytics-outcome-${key}`);
}

function info(list, title, value) {
  if (!value) return;
  list.append(element("dt", title), element("dd", value));
}

function offersTable(offers) {
  return table(["Предложение", "Цена", "Группа и бюджет", "Действие"], offers.map((offer) => {
    const name = element("div", offer.offer_name || offer.card_name);
    if (offer.offer_name && offer.card_name && offer.offer_name !== offer.card_name) {
      name.append(element("div", offer.card_name, "fine"));
    }
    if (offer.alternative) name.append(element("div", "Альтернативный оффер", "fine"));
    const difference = formatMoney(offer.budget_difference);
    const budget = difference ? `Превышение на ${difference}` : label(BUDGET_STATES, offer.budget_status);
    return [name, formatMoney(offer.price) || "Не указана",
      [label(GROUPS, offer.group), budget].filter(Boolean).join(" · "),
      offer.clicked ? "Переход зафиксирован" : "—"];
  }));
}

function turnView(turn, position) {
  const entry = element("li", null, "analytics-turn");
  const line = element("div", null, "analytics-turn-heading");
  line.append(element("strong", `Запрос ${position}`), outcome(turn.outcome),
    element("span", dateTime(turn.started_at), "fine"));
  entry.append(line);
  const fields = element("dl", null, "analytics-facts");
  const filterNames = (keys) => keys.map((key) => label(FILTERS, key)).join(", ");
  info(fields, "Режим", label(MODES, turn.mode));
  info(fields, "Уточнение", turn.clarification ? "В ответе был уточняющий вопрос" : "");
  info(fields, "Условия", filterNames(turn.filter_keys));
  info(fields, "Добавили", filterNames(turn.filter_changes.added));
  info(fields, "Изменили", filterNames(turn.filter_changes.replaced));
  info(fields, "Убрали", filterNames(turn.filter_changes.removed));
  info(fields, "Лимит цены", turn.budgets.map(formatBudget).filter(Boolean).join(" · "));
  info(fields, "События", turn.impression_recorded ? "Зарегистрирован показ блока выдачи" : "");
  info(fields, "Оценка", turn.feedback === "useful" ? "Полезно" : turn.feedback === "not_useful" ? "Не полезно" : "");
  info(fields, "Проблемы", turn.issues.map(issueLabel).join(", "));
  entry.append(fields);
  if (turn.offers.length) entry.append(offersTable(turn.offers));
  return entry;
}

function topicView(topic) {
  const needs = (topic.role_ids.length ? topic.role_ids : topic.class_ids).map((key) => label(NEEDS, key));
  const firstCard = topic.turns.find((turn) => turn.offers.length)?.offers[0].card_name;
  const title = needs.join(", ") || firstCard || `Тема ${topic.topic_id.slice(0, 8)}`;
  const details = element("details", null, "analytics-topic");
  const summary = element("summary");
  const summaryBody = element("span", null, "analytics-topic-overview");
  const main = element("span", null, "analytics-topic-title");
  main.append(element("strong", title), element("span",
    [dateTime(topic.started_at), label(MODES, topic.mode), `${topic.turns.length} запр.`].join(" · "), "fine"));
  const badges = element("span", null, "analytics-topic-badges");
  badges.append(outcome(topic.first_outcome));
  if (topic.last_outcome !== topic.first_outcome) badges.append(element("span", "→"), outcome(topic.last_outcome));
  if (topic.observing) badges.append(element("span", "Окно ещё открыто", "tag"));
  summaryBody.append(main, badges);
  summary.append(summaryBody);
  details.append(summary);
  const body = element("div", null, "analytics-topic-body");
  details.append(body);
  details.addEventListener("toggle", () => {
    if (!details.open || body.childElementCount) return;
    const descriptions = {
      preserved: "Полное совпадение при сохранении исходных обязательных условий.",
      changed: "Полное совпадение после изменения обязательных условий.",
    };
    if (topic.recovery) body.append(note(descriptions[topic.recovery] ?? ""));
    if (topic.issues.length) body.append(note(topic.issues.map(issueLabel).join(" · ")));
    const timeline = element("ol", null, "analytics-timeline");
    topic.turns.forEach((turn, i) => timeline.append(turnView(turn, i + 1)));
    body.append(timeline);
  });
  return details;
}

function problemView(section, dialogues, openIssue) {
  if (!dialogues.problems.length) {
    section.append(note("Наблюдаемые проблемы в темах за период не зафиксированы."));
    return;
  }
  section.append(table(["Причина", "Темы", "Что проверить"], dialogues.problems.map((problem) => [
    button(issueLabel(problem.code), () => openIssue(problem.code), "analytics-link"),
    NUMBER.format(problem.count), problem.recommendation,
  ])));
}

function pageControls(pages, movePage) {
  const total = Math.max(...pages.map((page) => page.filtered_total));
  const { offset, limit } = pages[0];
  if (offset === 0 && total <= limit) return null;
  const nav = element("nav", null, "analytics-pagination");
  nav.setAttribute("aria-label", "Страницы списка тем");
  const previous = button("Назад", () => movePage(Math.max(0, offset - limit)));
  previous.disabled = offset === 0;
  const next = button("Дальше", () => movePage(offset + limit));
  next.disabled = offset + limit >= total;
  nav.append(previous, element("span", `Страница ${Math.floor(offset / limit) + 1}`), next);
  return nav;
}

export function renderReport(container, payload, options) {
  const { view, mode, issue, openIssue, movePage } = options;
  const rows = analyticsRows(payload);
  if (!rows.length) {
    container.replaceChildren(note("Сервер вернул отчёт без наборов данных."));
    return;
  }
  const fragment = document.createDocumentFragment();
  if (view === "dialogues" && issue) {
    const selected = element("div", null, "analytics-issue");
    selected.append(element("strong", issueLabel(issue)), button("Сбросить причину", () => openIssue("")));
    fragment.append(selected);
  }
  const pages = [];
  rows.forEach((cohort, index) => {
    const dialogues = payload.cohorts[index].dialogues;
    const section = element("section", null, "analytics-section panel");
    section.append(element("h2", cohort.title));
    if (view === "overview") overview(section, cohort, dialogues, mode);
    else if (!dialogues) section.append(note("Сведения о темах недоступны в ответе сервера."));
    else if (view === "problems") problemView(section, dialogues, openIssue);
    else {
      pages.push(dialogues);
      const count = dialogues.topics.length;
      section.append(note(count ? `Темы ${dialogues.offset + 1}–${dialogues.offset + count} из ${dialogues.filtered_total}.`
        : dialogues.filtered_total ? "На этой странице тем нет." : "Темы с выбранными параметрами не найдены."));
      for (const topic of dialogues.topics) section.append(topicView(topic));
    }
    fragment.append(section);
  });
  if (pages.length) {
    const pagination = pageControls(pages, movePage);
    if (pagination) fragment.append(pagination);
  }
  container.replaceChildren(fragment);
}
