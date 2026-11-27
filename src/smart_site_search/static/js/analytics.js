import { renderReport } from "./analytics-view.js";
import { periodBounds } from "./model.js";

const form = document.querySelector("#analytics-form");
const report = document.querySelector("#report");
const status = document.querySelector("#status");
const from = document.querySelector("#from");
const to = document.querySelector("#to");
const cohort = document.querySelector("#cohort");
const mode = document.querySelector("#mode");
const tabs = [...document.querySelectorAll(".analytics-nav [data-view]")];

const state = { view: "overview", issue: "", offset: 0, filters: null, payload: null, request: null };

const today = new Date();
const day = 24 * 60 * 60 * 1000;
from.value = new Date(today.getTime() - 6 * day).toISOString().slice(0, 10);
to.value = new Date(today.getTime() + day).toISOString().slice(0, 10);

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    selectView(tab.dataset.view);
    if (state.payload) paint();
    else if (state.filters && !state.request) loadReport();
  });
}
form.addEventListener("submit", (event) => {
  event.preventDefault();
  applyFilters();
});
applyFilters();

function applyFilters() {
  const bounds = periodBounds(from.value, to.value);
  if (bounds.error) {
    status.textContent = bounds.error;
    return;
  }
  state.filters = { from: bounds.from, to: bounds.to, cohort: cohort.value, mode: mode.value };
  state.offset = 0;
  state.issue = "";
  loadReport();
}

function selectView(view) {
  state.view = view;
  for (const tab of tabs) tab.setAttribute("aria-pressed", String(tab.dataset.view === view));
}

function paint() {
  if (!state.payload) return;
  renderReport(report, state.payload, {
    view: state.view,
    mode: state.filters.mode,
    issue: state.issue,
    openIssue: (issue) => {
      state.issue = issue;
      state.offset = 0;
      selectView("dialogues");
      loadReport(true);
    },
    movePage: (offset) => {
      state.offset = offset;
      loadReport(true);
    },
  });
}

async function loadReport(focusReport = false) {
  state.request?.abort();
  const request = new AbortController();
  state.request = request;
  state.payload = null;
  report.replaceChildren();
  report.setAttribute("aria-busy", "true");
  status.textContent = "Загружаем отчёт…";
  const params = new URLSearchParams({ ...state.filters, offset: state.offset });
  if (state.issue) params.set("issue", state.issue);
  let message;
  try {
    const response = await fetch(`/api/v1/analytics?${params}`, {
      headers: { accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
    const payload = await response.json().catch(() => null);
    if (state.request !== request) return;
    if (!response.ok || !payload) {
      message = payload?.error?.message || "Аналитика недоступна.";
    } else {
      state.payload = payload;
      paint();
      if (focusReport) report.focus();
    }
  } catch {
    if (state.request !== request) return;
    state.payload = null;
    message = "Не удалось загрузить отчёт.";
  } finally {
    if (state.request === request) {
      state.request = null;
      report.removeAttribute("aria-busy");
      status.textContent = message ?? "";
    }
  }
}
