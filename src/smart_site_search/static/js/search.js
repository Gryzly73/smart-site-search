import {
  applyFailure,
  buildSearchBody,
  claimImpression,
  classifyHttpFailure,
  clientEvent,
  createDialogState,
  impressionPayload,
  networkFailure,
  newClientId,
  rememberSuccess,
  responseView,
  suggestionQuery,
  traceSummary,
} from "./model.js";

const state = createDialogState();
const thread = document.querySelector("#thread");
const form = document.querySelector("#composer");
const query = document.querySelector("#query");
const send = document.querySelector("#send");
const status = document.querySelector("#status");
const selection = document.querySelector("#selection");
const newSession = document.querySelector("#new-session");
let controller = null;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuery(query.value);
});

query.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

newSession.addEventListener("click", () => resetSession());

function submitQuery(text) {
  const trimmed = text.trim();
  if (!trimmed || send.disabled) return;
  const body = buildSearchBody({
    query: trimmed,
    state,
    clientRequestId: newClientId(),
  });
  state.pendingBody = body;
  appendUser(trimmed);
  query.value = "";
  postSearch(body);
}

function postSearch(body) {
  controller?.abort();
  controller = new AbortController();
  setBusy(true, "Ищем в каталоге…");
  fetch("/api/v1/search", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  }).then(async (response) => {
    const payload = await readJson(response);
    if (!response.ok) {
      showFailure(response.status, payload);
      return;
    }
    rememberSuccess(state, payload);
    renderSelection();
    appendResponse(payload);
    setBusy(false, "Ответ получен. Можно продолжить диалог.");
  }).catch((error) => {
    if (error.name === "AbortError") return;
    showFailure(0, null);
  });
}

function showFailure(statusCode, payload) {
  const failure = statusCode ? classifyHttpFailure(statusCode, payload) : networkFailure();
  applyFailure(state, failure);
  const note = document.createElement("p");
  note.className = "notice danger";
  note.textContent = failure.message;
  thread.append(note);
  if (failure.retrySameBody && state.pendingBody) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary";
    retry.textContent = "Повторить тот же запрос";
    retry.addEventListener("click", () => postSearch(state.pendingBody));
    note.after(retry);
  }
  if (failure.offerNewSession) {
    const restart = document.createElement("button");
    restart.type = "button";
    restart.textContent = "Начать новую сессию";
    restart.addEventListener("click", () => resetSession());
    note.after(restart);
  }
  renderSelection();
  setBusy(false, failure.message);
}

function appendUser(text) {
  const turn = document.createElement("article");
  turn.className = "turn user";
  const title = document.createElement("h2");
  title.textContent = "Запрос";
  const body = document.createElement("p");
  body.textContent = text;
  turn.append(title, body);
  thread.append(turn);
}

function appendResponse(payload) {
  const view = responseView(payload);
  const turn = document.createElement("article");
  turn.className = "turn assistant";
  turn.append(responseHead(view));
  for (const warning of view.warnings) turn.append(notice(warning, "warn"));
  if (view.insufficient && !view.warnings.some((warning) => /меньше четыр[её]х/.test(warning))) {
    turn.append(notice("Релевантных карточек меньше четырёх.", "warn"));
  }
  if (view.degraded && !view.warnings.some((warning) => /интерпретаци[яи]/i.test(warning))) {
    turn.append(notice("Ответ получен в ограниченном режиме.", "warn"));
  }
  if (view.syntheticCatalog) turn.append(notice("Каталог в этом ответе помечен как синтетический.", "warn"));
  if (view.filters.length) {
    const filters = document.createElement("p");
    filters.className = "fine";
    filters.textContent = `Фильтры: ${view.filters.join("; ")}`;
    turn.append(filters);
  }
  turn.append(renderGroups(view));
  turn.append(renderComponents(view));
  turn.append(renderAssumptions(view.assumptions));
  if (view.clarificationQuestion) {
    const question = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = "Уточнение. ";
    question.append(strong, document.createTextNode(view.clarificationQuestion));
    turn.append(question);
  }
  turn.append(renderSuggestions(view.suggestions));
  turn.append(renderTrace(view));
  turn.append(renderFeedback(view));
  thread.append(turn);
  thread.scrollTop = thread.scrollHeight;
  watchImpression(turn, view);
  query.placeholder = view.clarificationQuestion
    ? "Ответ на уточнение"
    : "Опишите, что нужно найти";
  query.focus();
}

function responseHead(view) {
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = view.goal || "Результат";
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = [modeLabel(view.mode), statusLabel(view.status)].filter(Boolean).join(" · ");
  header.append(title, meta);
  return header;
}

function renderGroups(view) {
  const root = document.createElement("div");
  root.className = "groups";
  if (!view.groups.length) {
    root.append(notice("Карточки не возвращены.", ""));
    return root;
  }
  let anchorId = view.companions?.length ? view.companionAnchorId : null;
  for (const group of view.groups) {
    for (const resultGroup of group.sections.length ? group.sections : [group]) {
      const section = document.createElement("section");
      section.className = "group";
      const title = document.createElement("h3");
      title.textContent = resultGroup.title;
      section.append(title);
      for (const item of resultGroup.items) {
        section.append(renderCard(item, view.requestId));
        if (anchorId && item.cardId === anchorId && item.group !== "related") {
          section.append(renderCompanions(view.companions, view.requestId));
          anchorId = null;
        }
      }
      root.append(section);
    }
  }
  return root;
}

function renderCard(item, requestId) {
  const card = document.createElement("article");
  card.className = `card ${item.group}`;
  card.tabIndex = -1;
  card.dataset.cardId = item.cardId;
  const header = document.createElement("header");
  const title = document.createElement("h4");
  title.textContent = item.name;
  const kind = document.createElement("span");
  kind.className = "meta";
  kind.textContent = item.kind;
  header.append(title, kind);
  card.append(header);
  if (item.imageUrl) {
    const image = document.createElement("img");
    image.className = "preview";
    image.src = item.imageUrl;
    image.alt = "";
    card.append(image);
  }
  const facts = document.createElement("dl");
  facts.className = "facts";
  addFact(facts, "Вендор", item.vendors);
  if (item.offerName) addFact(facts, "Предложение", item.offerName);
  addFact(facts, "Наличие", item.availabilityLabel);
  addFact(facts, "Цена выбранного предложения", item.priceLabel);
  if (item.priceMissing) addFact(facts, "Числовая цена", "не передана");
  if (item.batchTotal) addFact(facts, "Итог партии из ответа", item.batchTotal);
  addFact(facts, "Идентификатор предложения", item.offerId);
  card.append(facts);
  const chips = document.createElement("div");
  chips.className = "chips";
  chips.append(chip(availabilityChip(item), item.availabilityState === "unknown" ? "warn" : ""));
  if (item.businessMock) chips.append(chip("Коммерческие факторы — макет", "warn"));
  appendConditionChips(chips, item);
  card.append(chips);
  const reason = document.createElement("p");
  reason.textContent = item.reason;
  card.append(reason);
  if (item.caveats.length) {
    const list = document.createElement("ul");
    for (const caveat of item.caveats) {
      const line = document.createElement("li");
      line.textContent = caveat;
      list.append(line);
    }
    card.append(list);
  }
  const compatibility = document.createElement("p");
  compatibility.className = "fine";
  compatibility.textContent = item.compatibility;
  card.append(compatibility);
  const actions = document.createElement("div");
  actions.className = "card-actions";
  const choose = document.createElement("button");
  choose.type = "button";
  choose.className = "secondary";
  choose.textContent = "Продолжить с этой карточкой";
  choose.addEventListener("click", () => {
    state.selection = { referenceRequestId: requestId, cardId: item.cardId, name: item.name };
    renderSelection();
    query.focus();
  });
  actions.append(choose);
  if (item.url) {
    actions.append(resultLink(item.url, "Открыть ссылку из ответа", item, item.offerId, requestId));
  } else if (item.urlRejected) {
    const rejected = document.createElement("span");
    rejected.className = "fine";
    rejected.textContent = "Ссылка из ответа не показана: допустимы только http и https.";
    actions.append(rejected);
  }
  card.append(actions);
  if (item.budgetAlternative) card.append(renderBudgetAlternative(item, requestId));
  return card;
}

function renderCompanions(companions, requestId) {
  const block = document.createElement("section");
  block.className = "companions";
  const title = document.createElement("h3");
  title.textContent = "Также дополнительно можем вам предложить...";
  block.append(title);
  for (const item of companions) block.append(renderCompanion(item, requestId));
  return block;
}

function renderCompanion(item, requestId) {
  const card = document.createElement("article");
  card.className = "card companion";
  card.dataset.cardId = item.cardId;
  const header = document.createElement("header");
  const title = document.createElement("h4");
  title.textContent = item.name;
  const kind = document.createElement("span");
  kind.className = "meta";
  kind.textContent = item.kind;
  header.append(title, kind);
  card.append(header);
  if (item.imageUrl) {
    const image = document.createElement("img");
    image.className = "preview";
    image.src = item.imageUrl;
    image.alt = "";
    card.append(image);
  }
  const facts = document.createElement("dl");
  facts.className = "facts";
  addFact(facts, "Вендор", item.vendors);
  addFact(facts, "Роль", item.roleLabel);
  card.append(facts);
  const actions = document.createElement("div");
  actions.className = "card-actions";
  const choose = document.createElement("button");
  choose.type = "button";
  choose.className = "secondary";
  choose.textContent = "Продолжить с этой карточкой";
  choose.addEventListener("click", () => {
    state.selection = { referenceRequestId: requestId, cardId: item.cardId, name: item.name };
    renderSelection();
    query.focus();
  });
  actions.append(choose);
  if (item.url) {
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Открыть карточку";
    actions.append(link);
  }
  card.append(actions);
  return card;
}

function appendConditionChips(chips, offer) {
  if (offer.budgetLabel) chips.append(chip(offer.budgetLabel, offer.budgetStatus === "over" ? "warn" : ""));
  for (const label of offer.unconfirmed) chips.append(chip(`Не подтверждено: ${label}`, "warn"));
  for (const label of offer.mismatched) chips.append(chip(`Не выполнено: ${label}`, "bad"));
}

function renderBudgetAlternative(item, requestId) {
  const offer = item.budgetAlternative;
  const section = document.createElement("section");
  section.className = "budget-alternative";
  const title = document.createElement("h5");
  title.textContent = offer.name;
  const facts = document.createElement("dl");
  facts.className = "facts";
  addFact(facts, "Цена", offer.priceLabel);
  addFact(facts, "Наличие", offer.availability);
  const chips = document.createElement("div");
  chips.className = "chips";
  appendConditionChips(chips, offer);
  section.append(title, facts, chips);
  if (offer.reason) {
    const reason = document.createElement("p");
    reason.textContent = offer.reason;
    section.append(reason);
  }
  if (offer.url) section.append(resultLink(offer.url, "Открыть это предложение", item, offer.offerId, requestId));
  return section;
}

function resultLink(url, label, item, offerId, requestId) {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  link.addEventListener("click", () => {
    sendEvent("result_click", {
      request_id: requestId,
      card_id: item.cardId,
      offer_id: offerId,
      group: item.group,
      position: item.position,
    }, requestId);
  });
  return link;
}

function renderComponents(view) {
  if (!view.components.length) return document.createDocumentFragment();
  const section = document.createElement("section");
  section.className = "components";
  const title = document.createElement("h3");
  title.textContent = "Компоненты решения";
  section.append(title);
  for (const component of view.components) {
    const block = document.createElement("div");
    block.className = "component";
    const name = document.createElement("strong");
    name.textContent = `${component.label}: ${componentStatus(component.status)}`;
    block.append(name);
    if (component.rationale) {
      const rationale = document.createElement("p");
      rationale.textContent = component.rationale;
      block.append(rationale);
    }
    if (component.missingReason) {
      const reason = document.createElement("p");
      reason.textContent = component.missingReason;
      block.append(reason);
    }
    if (component.unknowns.length) {
      const unknown = document.createElement("p");
      unknown.textContent = `Уточнить: ${component.unknowns.slice(0, 2).join("; ")}`;
      block.append(unknown);
    }
    const ids = [...component.fullCardIds, ...component.partialCardIds];
    if (ids.length) {
      const nav = document.createElement("p");
      nav.append(document.createTextNode("Позиции: "));
      ids.forEach((cardId, index) => {
        if (index) nav.append(document.createTextNode(", "));
        const jump = document.createElement("button");
        jump.type = "button";
        jump.className = "linkish";
        jump.textContent = cardId;
        jump.addEventListener("click", () => {
          const target = thread.querySelector(`[data-card-id="${cssEscape(cardId)}"]`);
          target?.focus();
          target?.scrollIntoView({ block: "nearest" });
        });
        nav.append(jump);
      });
      block.append(nav);
    }
    section.append(block);
  }
  return section;
}

function renderAssumptions(assumptions) {
  if (!assumptions.length) return document.createDocumentFragment();
  const section = document.createElement("section");
  section.className = "assumptions";
  const title = document.createElement("h3");
  title.textContent = "Допущения";
  const list = document.createElement("ul");
  for (const assumption of assumptions) {
    const line = document.createElement("li");
    line.textContent = assumption;
    list.append(line);
  }
  section.append(title, list);
  return section;
}

function renderSuggestions(suggestions) {
  if (!suggestions.length) return document.createDocumentFragment();
  const row = document.createElement("div");
  row.className = "choice-row";
  for (const suggestion of suggestions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = suggestion.label || "Предложение";
    button.addEventListener("click", () => submitQuery(suggestionQuery(suggestion)));
    row.append(button);
  }
  return row;
}

function renderTrace(view) {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Короткая трасса";
  const body = document.createElement("div");
  details.append(summary, body);
  details.addEventListener("toggle", () => {
    if (!details.open || body.dataset.loaded === "yes") return;
    body.dataset.loaded = "yes";
    fillTrace(body, view);
  });
  return details;
}

function fillTrace(body, view) {
  const cost = document.createElement("p");
  cost.textContent = view.usage.text;
  body.append(cost);
  if (!view.traceUrl) {
    const missing = document.createElement("p");
    missing.textContent = "Адрес трассы не принят.";
    body.append(missing);
    return;
  }
  fetch(view.traceUrl, { headers: { accept: "application/json" } })
    .then(async (response) => {
      if (!response.ok) throw new Error("trace");
      return response.json();
    })
    .then((events) => {
      const summary = traceSummary(events, null);
      if (summary.interpretationSource) {
        const source = document.createElement("p");
        source.textContent = `Источник интерпретации: ${summary.interpretationSource}`;
        body.append(source);
      }
      const list = document.createElement("ol");
      list.className = "stages";
      for (const stage of summary.stages) {
        const line = document.createElement("li");
        line.textContent = stage.label;
        list.append(line);
      }
      body.append(list);
    })
    .catch(() => {
      const failed = document.createElement("p");
      failed.textContent = "Трассу не удалось прочитать.";
      body.append(failed);
    });
}

function renderFeedback(view) {
  const row = document.createElement("div");
  row.className = "choice-row";
  const useful = document.createElement("button");
  useful.type = "button";
  useful.className = "secondary";
  useful.textContent = "Полезно";
  const useless = document.createElement("button");
  useless.type = "button";
  useless.className = "secondary";
  useless.textContent = "Не полезно";
  const mark = (rating, button) => {
    sendEvent("feedback", { request_id: view.requestId, rating }, view.requestId);
    useful.disabled = true;
    useless.disabled = true;
    button.textContent = `${button.textContent} — отправлено`;
  };
  useful.addEventListener("click", () => mark("useful", useful));
  useless.addEventListener("click", () => mark("not_useful", useless));
  row.append(useful, useless);
  return row;
}

function watchImpression(turn, view) {
  const cards = view.groups.flatMap((group) => group.items);
  if (!cards.length) return;
  const send = () => {
    if (!claimImpression(state, view.requestId)) return;
    sendEvent("results_impression", impressionPayload(view), view.requestId);
  };
  if (typeof IntersectionObserver !== "function") {
    requestAnimationFrame(send);
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      observer.disconnect();
      send();
    }
  }, { threshold: 0.15 });
  observer.observe(turn);
}

function sendEvent(eventType, payload, requestId) {
  if (!state.sessionId || !requestId) return;
  const body = clientEvent({
    eventId: newClientId(),
    sessionId: state.sessionId,
    requestId,
    eventType,
    payload,
    occurredAt: new Date().toISOString(),
  });
  fetch("/api/v1/events", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {
    status.textContent = "Событие интерфейса не записалось.";
  });
}

function renderSelection() {
  if (!state.selection) {
    selection.hidden = true;
    selection.textContent = "";
    return;
  }
  selection.hidden = false;
  selection.textContent = "";
  selection.append(document.createTextNode(`Следующая реплика про карточку «${state.selection.name}». `));
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "secondary";
  clear.textContent = "Снять выбор";
  clear.addEventListener("click", () => {
    state.selection = null;
    renderSelection();
  });
  selection.append(clear);
}

function resetSession() {
  controller?.abort();
  state.sessionId = null;
  state.expectedRevision = 0;
  state.selection = null;
  state.pendingBody = null;
  state.impressed = new Set();
  thread.textContent = "";
  query.placeholder = "Опишите, что нужно найти";
  renderSelection();
  setBusy(false, "Новая сессия. Предыдущий диалог закрыт.");
  query.focus();
}

function setBusy(busy, text) {
  send.disabled = busy;
  query.disabled = busy;
  form.setAttribute("aria-busy", busy ? "true" : "false");
  status.textContent = text;
}

function notice(text, kind) {
  const node = document.createElement("p");
  node.className = kind ? `notice ${kind}` : "notice";
  node.textContent = text;
  return node;
}

function chip(text, kind) {
  const node = document.createElement("span");
  node.className = kind ? `chip ${kind}` : "chip";
  node.textContent = text;
  return node;
}

function addFact(list, name, value) {
  const term = document.createElement("dt");
  term.textContent = name;
  const detail = document.createElement("dd");
  detail.textContent = value;
  list.append(term, detail);
}

function availabilityChip(item) {
  if (item.availabilityState === "yes") return "В снимке есть";
  if (item.availabilityState === "no") return "В снимке нет";
  return "Наличие не подтверждено";
}

function componentStatus(status) {
  if (status === "covered") return "покрыто";
  if (status === "partial") return "частично";
  if (status === "missing") return "нет позиции";
  return status || "статус не указан";
}

function modeLabel(mode) {
  if (mode === "direct") return "Прямой поиск";
  if (mode === "functional") return "Поиск по задаче";
  if (mode === "expert") return "Экспертный план";
  return mode;
}

function statusLabel(status) {
  if (status === "ok") return "Ответ собран";
  if (status === "degraded") return "Ограниченный режим";
  if (status === "needs_clarification") return "Нужно уточнение";
  return status;
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
  return String(value).replace(/"/g, '\\"');
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}
