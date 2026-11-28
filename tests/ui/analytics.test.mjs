import assert from "node:assert/strict";
import test from "node:test";
import {
  formatBudget, metricValue, renderReport,
} from "../../src/smart_site_search/static/js/analytics-view.js";

class TestElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.valueText = "";
  }

  get textContent() { return this.valueText + this.children.map((child) => child.textContent).join(" "); }
  set textContent(value) { this.valueText = String(value); }
  get childElementCount() { return this.children.length; }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) { this.listeners[name] = listener; }

  makeChild(name) {
    const child = new TestElement(name);
    this.append(child);
    return child;
  }

  createTHead() { return this.makeChild("thead"); }
  createTBody() { return this.makeChild("tbody"); }
  insertRow() { return this.makeChild("tr"); }
  insertCell() { return this.makeChild("td"); }

  find(predicate) {
    if (predicate(this)) return this;
    for (const child of this.children) {
      const node = child.find(predicate);
      if (node) return node;
    }
    return null;
  }
}

function renderFixture(t) {
  const original = { document: globalThis.document, Node: globalThis.Node };
  globalThis.Node = TestElement;
  globalThis.document = {
    createElement: (name) => new TestElement(name),
    createDocumentFragment: () => new TestElement("fragment"),
  };
  t.after(() => Object.assign(globalThis, original));
  const budget = { key: "price_minor", op: "lte", values: [9_000_000], currency: "RUB" };
  const topic = {
    topic_id: "synthetic-example", started_at: "2026-11-27T10:00:00Z", mode: "functional",
    role_ids: ["backup_restore"], class_ids: [], first_outcome: "partial", last_outcome: "full",
    observing: false, recovery: "changed", issues: ["budget_over"],
    turns: [{
      started_at: "2026-11-27T10:00:00Z", mode: "functional", outcome: "partial",
      result_kind: "price_near", request_text: "Кибер Бэкап до 90 тысяч",
      answer_text: "Товара в вашем ценовом диапазоне нет, но можем предложить решения до 40% выше указанной цены.",
      clarification: true, filter_keys: ["price_minor"], filter_changes: { added: ["price_minor"], replaced: [], removed: [] },
      budgets: [budget], impression_recorded: true, feedback: "useful", issues: ["budget_over"],
      offers: [{
        card_name: "Резервное копирование", offer_name: "Альтернативная лицензия", group: "primary_partial",
        price: { amount_minor: 10_323_600, currency: "RUB" }, budget_status: "over",
        budget_difference: { amount_minor: 1_323_600, currency: "RUB" }, alternative: true, clicked: true,
      }],
    }],
  };
  const payload = {
    cohorts: [{
      name: "mock", metrics: [], demand: [], gaps: [], costs_by_mode: [],
      dialogues: {
        observation_minutes: 30, unattributed_requests: 0, topics_total: 27, filtered_total: 27, offset: 0, limit: 25,
        metrics: [{ metric_id: "topics", metric: {
          value: 27, numerator: null, denominator: null, availability: "mock", definition: "Темы", limitations: [],
        } }],
        trend: [{ date: "2026-11-27", topics: 27, first_full: 3, recovered: 1 }],
        problems: [{ code: "budget_over", count: 27, recommendation: "Проверить цены." }],
        topics: [topic],
      },
    }],
  };
  return { root: new TestElement("main"), payload };
}

test("доли различают нулевое значение и отсутствие измерения, бюджет учитывает валюту", () => {
  assert.match(metricValue({ value: 0, denominator: 12 }), /^0\s*%$/);
  assert.equal(metricValue({ value: null, denominator: 0 }), "Нет данных");
  assert.match(formatBudget({ key: "price_minor", op: "lte", values: [9_000_000], currency: "RUB" }),
    /^Бюджет до 90\s000\s₽$/);
});

test("аналитика показывает когорту и метрики, причина открывает список, тема раскрывает оффер и события", (t) => {
  const { root, payload } = renderFixture(t);
  let issue;
  let offset;
  const options = { mode: "all", issue: "", openIssue: (value) => { issue = value; }, movePage: (value) => { offset = value; } };
  renderReport(root, payload, { ...options, view: "overview" });
  assert.match(root.textContent, /Макет и синтетика/);
  assert.match(root.textContent, /Темы диалогов 27/);
  assert.match(root.textContent, /Окно наблюдения — 30 мин/);
  assert.match(root.textContent, /Аналитика по запросам/);

  renderReport(root, payload, { ...options, view: "problems" });
  root.find((node) => node.className === "analytics-link").listeners.click();
  assert.equal(issue, "budget_over");

  renderReport(root, payload, { ...options, issue, view: "dialogues" });
  assert.match(root.textContent, /Есть вариант дороже бюджета/);
  const details = root.find((node) => node.className === "analytics-topic");
  assert.doesNotMatch(details.textContent, /Альтернативный оффер/);
  details.open = true;
  details.listeners.toggle();
  assert.match(details.textContent, /Кибер Бэкап до 90 тысяч/);
  assert.match(details.textContent, /можем предложить решения до 40%/);
  assert.match(details.textContent, /Бюджет до 90\s000\s₽/);
  assert.match(details.textContent, /Превышение на 13\s236\s₽/);
  assert.match(details.textContent, /Альтернативный оффер/);
  assert.match(details.textContent, /Зарегистрирован показ блока выдачи/);
  assert.match(details.textContent, /Переход зафиксирован/);
  assert.match(details.textContent, /Полезно/);
  root.find((node) => node.tagName === "button" && node.textContent === "Дальше").listeners.click();
  assert.equal(offset, 25);
});

test("переход на вкладку повторяет запрос после ошибки первой загрузки", async (t) => {
  const { root, payload } = renderFixture(t);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const selectors = new Map(["#analytics-form", "#status", "#from", "#to", "#cohort", "#mode"]
    .map((id) => [id, new TestElement("input")]));
  selectors.set("#report", root);
  selectors.get("#cohort").value = "all";
  selectors.get("#mode").value = "all";
  const tabs = ["overview", "dialogues", "problems"].map((view) => {
    const tab = new TestElement("button");
    tab.dataset = { view };
    return tab;
  });
  document.querySelector = (selector) => selectors.get(selector);
  document.querySelectorAll = () => tabs;

  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(url);
    const ok = requests.length > 1;
    return { ok, json: async () => ok ? payload : null };
  };
  const tick = () => new Promise(setImmediate);

  await import("../../src/smart_site_search/static/js/analytics.js");
  await tick();
  assert.match(selectors.get("#status").textContent, /Аналитика недоступна/);

  tabs[1].listeners.click();
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(tabs[1].attributes["aria-pressed"], "true");
  assert.ok(root.find((node) => node.className === "analytics-topic"));

  tabs[2].listeners.click();
  assert.equal(requests.length, 2);
  assert.equal(tabs[2].attributes["aria-pressed"], "true");
  assert.ok(root.find((node) => node.className === "analytics-link"));
});
