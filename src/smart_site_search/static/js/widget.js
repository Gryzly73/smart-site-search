import {
  WIDGET_ROW_SIZE,
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
  widgetBoard,
} from "./model.js";

const state = createDialogState();
const launcher = document.querySelector("#launcher");
const panel = document.querySelector("#panel");
const close = document.querySelector("#close");
const clear = document.querySelector("#clear");
const operator = document.querySelector("#operator");
const log = document.querySelector("#log");
const form = document.querySelector("#composer");
const query = document.querySelector("#query");
const send = document.querySelector("#send");
const boards = new WeakMap();
const welcome = log.querySelector(".welcome");
let controller = null;

launcher.addEventListener("click", () => setOpen(true));
close.addEventListener("click", () => setOpen(false));
clear.addEventListener("click", () => resetDialog());
operator.addEventListener("click", () => {
  operator.disabled = true;
  if (operator.nextElementSibling?.classList.contains("operator-note")) return;
  const note = document.createElement("p");
  note.className = "fine operator-note";
  note.textContent = "Оператор в этой проверке не подключается. Диалог с поиском продолжается.";
  operator.after(note);
});
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = query.value.trim();
  if (!text || send.disabled) return;
  appendUser(text);
  query.value = "";
  const body = buildSearchBody({ query: text, state, clientRequestId: newClientId() });
  state.pendingBody = body;
  postSearch(body);
});

query.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function setOpen(open) {
  panel.hidden = !open;
  launcher.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) query.focus();
}

function appendUser(text) {
  const bubble = document.createElement("article");
  bubble.className = "bubble user";
  const line = document.createElement("p");
  line.textContent = text;
  bubble.append(line);
  log.append(bubble);
  syncClear();
  log.scrollTop = log.scrollHeight;
}

function resetDialog() {
  controller?.abort();
  state.sessionId = null;
  state.expectedRevision = 0;
  state.selection = null;
  state.pendingBody = null;
  state.impressed = new Set();
  log.replaceChildren(welcome);
  operator.disabled = false;
  operator.parentElement?.querySelector(".operator-note")?.remove();
  syncClear();
  setBusy(false);
  query.focus();
}

function syncClear() {
  clear.hidden = ![...log.children].some((node) => node !== welcome);
}

setOpen(true);

function postSearch(body) {
  controller?.abort();
  controller = new AbortController();
  setBusy(true);
  fetch("/api/v1/search", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  }).then(async (response) => {
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      showFailure(response.status, payload);
      return;
    }
    rememberSuccess(state, payload);
    appendAssistant(responseView(payload));
    setBusy(false);
  }).catch((error) => {
    if (error.name === "AbortError") return;
    showFailure(0, null);
  });
}

function showFailure(statusCode, payload) {
  const failure = statusCode ? classifyHttpFailure(statusCode, payload) : networkFailure();
  applyFailure(state, failure);
  const bubble = document.createElement("article");
  bubble.className = "bubble assistant";
  const line = document.createElement("p");
  line.textContent = failure.message;
  bubble.append(line);
  log.append(bubble);
  syncClear();
  setBusy(false);
}

function appendAssistant(view) {
  const bubble = document.createElement("article");
  bubble.className = "bubble assistant";
  boards.set(bubble, { view, shown: WIDGET_ROW_SIZE });
  paint(bubble);
  log.append(bubble);
  syncClear();
  log.scrollTop = log.scrollHeight;
  watchImpression(bubble, view);
}

function paint(bubble) {
  const stored = boards.get(bubble);
  const board = widgetBoard(stored.view, stored.shown);
  bubble.replaceChildren();
  if (stored.view.goal) {
    const goal = document.createElement("p");
    goal.textContent = stored.view.goal;
    bubble.append(goal);
  }
  if (stored.view.clarificationQuestion) {
    const question = document.createElement("p");
    question.textContent = stored.view.clarificationQuestion;
    bubble.append(question);
  }
  if (board.cards.length) bubble.append(cardRow(board.cards));
  if (board.companions.length) {
    const heading = document.createElement("p");
    heading.className = "fine";
    heading.textContent = "Также дополнительно можем вам предложить...";
    bubble.append(heading, cardRow(board.companions));
  }
  if (board.remaining > 0) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    more.textContent = "Показать ещё";
    more.addEventListener("click", () => {
      stored.shown = board.nextCount;
      paint(bubble);
    });
    bubble.append(more);
  }
}

function cardRow(cards) {
  const row = document.createElement("div");
  row.className = "row";
  row.role = "list";
  for (const card of cards) row.append(productCard(card));
  return row;
}

function productCard(card) {
  const node = document.createElement(card.url ? "a" : "div");
  node.className = `product ${card.group === "primary_full" ? "full" : card.group === "primary_partial" ? "partial" : ""}`;
  node.role = "listitem";
  if (card.url) {
    node.href = card.url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
  }
  const photo = document.createElement("div");
  photo.className = "photo";
  if (card.imageUrl) {
    const image = document.createElement("img");
    image.src = card.imageUrl;
    image.alt = "";
    photo.append(image);
  } else {
    const mark = document.createElement("div");
    mark.className = "mark";
    photo.append(mark);
  }
  const body = document.createElement("div");
  body.className = "body";
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = card.name;
  name.title = card.name;
  body.append(name);
  if (card.priceLabel) {
    const price = document.createElement("div");
    price.className = "price";
    price.textContent = card.priceLabel;
    body.append(price);
  }
  if (card.status) {
    const status = document.createElement("div");
    status.className = "status";
    status.textContent = card.status;
    body.append(status);
  }
  if (card.url) {
    const look = document.createElement("span");
    look.className = "look";
    look.textContent = "Посмотреть";
    body.append(look);
  }
  node.append(photo, body);
  return node;
}

function watchImpression(bubble, view) {
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
  observer.observe(bubble);
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
  }).catch(() => {});
}

function setBusy(busy) {
  send.disabled = busy;
  query.disabled = busy;
}
