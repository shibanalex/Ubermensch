/* Аня (Übermensch) — Obsidian как часть интерфейса Ани.
 *
 * Чат и панель целей ходят в локальное ядро (http://127.0.0.1:8765) с токеном из папки
 * данных. Решения кнопками — его решения: «Утвердить план», «Согласен на эксперимент»,
 * «Да/Нет» на подтверждения. Без сборки: обычный CommonJS, API Obsidian — require('obsidian').
 */
const {
  ItemView, MarkdownRenderer, Modal, Notice, Plugin, PluginSettingTab, Setting, requestUrl, setIcon,
} = require("obsidian");

const CHAT_VIEW = "anya-chat";
const DASHBOARD_VIEW = "anya-dashboard";
const SECTIONS = ["goals", "proposals", "consent", "tasks", "hh", "effectiveness"];
const DEFAULTS = {
  apiUrl: "http://127.0.0.1:8765",
  tokenFile: "~/Projects/Ubermensch/secrets/api_token",
  refreshSeconds: 60,
  attachNote: false,
};
const STATUS = { done: "сделано", unverified: "не подтверждено", blocked: "застряла",
                 needs_user: "ждёт тебя", failed: "не вышло" };

function expandHome(path) {
  return path.startsWith("~/") ? require("os").homedir() + path.slice(1) : path;
}

function el(parent, tag, options = {}) {
  return parent.createEl(tag, options);
}

class Api {
  constructor(plugin) { this.plugin = plugin; this.token = null; }

  readToken() {
    try {
      this.token = require("fs").readFileSync(expandHome(this.plugin.settings.tokenFile), "utf8").trim();
    } catch (e) {
      this.token = null;
    }
    return this.token;
  }

  async call(path, { method = "GET", body } = {}) {
    if (!this.token && !this.readToken()) throw new Error("Нет токена ядра: проверь путь в настройках плагина");
    const send = () => requestUrl({
      url: this.plugin.settings.apiUrl + path, method, throw: false,
      headers: { Authorization: "Bearer " + this.token, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let response = await send();
    if (response.status === 401 && this.readToken()) response = await send();  // токен пересоздан
    const data = response.text ? JSON.parse(response.text) : {};
    if (response.status >= 400 || data.error) throw new Error(data.error || `Ядро ответило ${response.status}`);
    return data;
  }

  async chat(text, note) {
    const run = await this.call("/api/runs/chat", {
      method: "POST", body: { text, run_id: crypto.randomUUID(), channel: "obsidian", note: note || "" },
    });
    for (let waited = 0; waited < 15 * 60; waited += 2) {
      await sleep(2000);
      const state = await this.call(`/api/runs/${run.id}`);
      if (["completed", "failed", "interrupted", "cancelled"].includes(state.status)) return state;
    }
    throw new Error("Аня думает дольше 15 минут — ответ придёт в историю");
  }
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function noteText(app) {
  const file = app.workspace.getActiveFile();
  if (!file) return Promise.resolve("");
  return app.vault.cachedRead(file).then((text) => `# ${file.path}\n${text.slice(0, 18000)}`);
}

/* ---------- панель: секции, общие для вида и блоков ```anya``` ---------- */

class Sections {
  constructor(plugin, root, refresh) { this.plugin = plugin; this.root = root; this.refresh = refresh; }

  async render(names = SECTIONS) {
    this.root.empty();
    let data;
    try {
      data = await this.plugin.api.call("/api/dashboard");
    } catch (e) {
      el(this.root, "div", { cls: "anya-error", text: "Аня не на связи: " + e.message });
      return;
    }
    for (const name of names) {
      const method = this["section_" + name];
      if (method) method.call(this, data);
    }
  }

  box(title, icon) {
    const box = el(this.root, "div", { cls: "anya-box" });
    const head = el(box, "div", { cls: "anya-box-title" });
    setIcon(el(head, "span", { cls: "anya-icon" }), icon);
    el(head, "span", { text: title });
    return box;
  }

  button(parent, text, action, cls = "") {
    const b = el(parent, "button", { text, cls: "anya-button " + cls });
    b.onclick = async () => {
      b.disabled = true;
      try { await action(); await this.refresh(); } catch (e) { new Notice("Аня: " + e.message); b.disabled = false; }
    };
    return b;
  }

  section_goals(data) {
    const box = this.box("Цели", "target");
    for (const goal of data.goals) {
      const row = el(box, "div", { cls: "anya-goal" });
      const top = el(row, "div", { cls: "anya-goal-top" });
      el(top, "span", { cls: "anya-goal-title", text: goal.title });
      if (goal.deadline) el(top, "span", { cls: "anya-muted", text: " · до " + goal.deadline });
      if (goal.plan) {
        const percent = Math.max(0, Math.min(100, goal.plan.progress_percent || 0));
        const bar = el(row, "div", { cls: "anya-bar" });
        el(bar, "div", { cls: "anya-bar-fill" }).style.width = percent + "%";
        el(row, "div", { cls: "anya-muted",
          text: `${goal.plan.metric}: ${goal.plan.current} → ${goal.plan.target} ${goal.plan.unit} · ${goal.plan.weekly_minutes} мин/нед` });
        el(row, "div", { text: "Следующий шаг: " + goal.plan.next_step });
      } else {
        el(row, "div", { cls: "anya-muted", text: "Нет измеримого плана — Аня над целью не работает" });
      }
      if (goal.last_cycle) {
        const c = goal.last_cycle;
        el(row, "div", { cls: "anya-cycle anya-" + c.status,
          text: `${c.created_at.slice(0, 10)} · ${STATUS[c.status] || c.status}: ${c.action || c.ask_user || ""}`.slice(0, 300) });
        if (c.blocker) el(row, "div", { cls: "anya-muted", text: "Мешало: " + c.blocker });
      }
      if (goal.hypothesis) el(row, "div", { cls: "anya-muted", text: "Гипотеза: " + goal.hypothesis });
      const actions = el(row, "div", { cls: "anya-actions" });
      this.button(actions, "Обсудить", async () => this.plugin.openChat(`Давай про цель «${goal.title}»: `));
    }
  }

  section_proposals(data) {
    if (!data.plan_proposals.length) return;
    const box = this.box("Аня предлагает планы", "clipboard-check");
    for (const item of data.plan_proposals) {
      const p = item.proposal;
      const row = el(box, "div", { cls: "anya-goal" });
      el(row, "div", { cls: "anya-goal-title", text: item.title });
      el(row, "div", { text: `${p.metric}: ${p.baseline ?? "?"} → ${p.target} ${p.unit} · ${p.weekly_minutes} мин/нед` });
      el(row, "div", { cls: "anya-muted", text: "Первый шаг: " + p.next_step });
      if (item.ask_user) el(row, "div", { cls: "anya-ask", text: "Аня спрашивает: " + item.ask_user });
      const actions = el(row, "div", { cls: "anya-actions" });
      this.button(actions, "Утвердить…", async () => new PlanModal(this.plugin, item, () => this.refresh()).open(), "mod-cta");
    }
  }

  section_consent(data) {
    if (!data.consent_needed.length) return;
    const box = this.box("Эксперименты ждут твоего согласия", "flask-conical");
    for (const e of data.consent_needed) {
      const row = el(box, "div", { cls: "anya-goal" });
      el(row, "div", { cls: "anya-goal-title", text: e.hypothesis });
      el(row, "div", { text: "Что меняем: " + e.change });
      el(row, "div", { cls: "anya-muted", text: `Замер: ${e.metric} · успех: ${e.success} · стоп: ${e.stop} · пересмотр ${e.review_on}` });
      if (e.sensitive) el(row, "div", { cls: "anya-ask", text: "Касается здоровья, сна, питания или тренировок" });
      const actions = el(row, "div", { cls: "anya-actions" });
      const decide = (accept) => this.plugin.api.call(`/api/experiments/${e.id}/decide`, {
        method: "POST", body: { accept, consent: accept ? "Согласен (кнопка в Obsidian)" : "Отказ (кнопка в Obsidian)" } });
      this.button(actions, "Согласен", () => decide(true), "mod-cta");
      this.button(actions, "Отказаться", () => decide(false));
    }
  }

  section_tasks(data) {
    const box = this.box("Задачи", "check-square");
    if (data.approvals.length) {
      for (const a of data.approvals) {
        const row = el(box, "div", { cls: "anya-approval" });
        el(row, "div", { cls: "anya-goal-title", text: "Подтверди: " + a.summary });
        if (a.details) el(row, "pre", { cls: "anya-details", text: a.details.slice(0, 1500) });
        const actions = el(row, "div", { cls: "anya-actions" });
        const decide = (approve) => this.plugin.api.call(`/api/actions/${a.id}`, { method: "POST", body: { approve } });
        this.button(actions, "Да", () => decide(true), "mod-cta");
        this.button(actions, "Нет", () => decide(false));
      }
    }
    for (const task of data.tasks) {
      const row = el(box, "label", { cls: "anya-task" });
      const check = el(row, "input", { type: "checkbox" });
      check.onchange = async () => {
        try {
          await this.plugin.api.call(`/api/tasks/${task.id}`, { method: "POST", body: { status: "done" } });
          await this.refresh();
        } catch (e) { new Notice("Аня: " + e.message); check.checked = false; }
      };
      el(row, "span", { text: task.title + (task.due ? " · " + task.due : "") });
    }
    for (const item of data.agent_tasks) {
      el(box, "div", { cls: "anya-muted", text: `Поручение Ани: ${item.title} (${item.status})` });
    }
    if (!data.tasks.length && !data.agent_tasks.length && !data.approvals.length) {
      el(box, "div", { cls: "anya-muted", text: "Пусто — можно выдохнуть" });
    }
  }

  section_hh(data) {
    const box = this.box("Отклики hh", "briefcase");
    const hh = data.hh;
    el(box, "div", { text: hh.available
      ? `${hh.live ? "Откликается сама" : "Только письма, без отправки"} · до ${hh.daily_limit} в день · последняя сессия: ${hh.last_session_day || "ещё не было"}`
      : "Бот недоступен: " + hh.problem });
    for (const g of data.funnel) {
      el(box, "div", { cls: "anya-muted",
        text: `Воронка: откликов ${g.applied}, ответов ${g.reply} (${Math.round((g.reply_rate || 0) * 100)}%), приглашений ${g.interview}, отказов ${g.rejected}` });
    }
    if (hh.templates.length) {
      const table = el(box, "table", { cls: "anya-table" });
      const head = el(table, "tr");
      for (const h of ["Письмо", "Отправлено", "Просмотрено", "Приглашений", "Отказов"]) el(head, "th", { text: h });
      for (const t of hh.templates) {
        const tr = el(table, "tr");
        for (const v of [t.template, t.sent, t.viewed, t.invited, t.rejected]) el(tr, "td", { text: String(v) });
      }
    }
  }

  section_effectiveness(data) {
    const e = data.effectiveness;
    const box = this.box("Как работает Аня (7 дней)", "activity");
    const steps = Object.entries(e.goal_steps).map(([k, v]) => `${STATUS[k] || k}: ${v}`).join(", ") || "шагов не было";
    el(box, "div", { text: "Шаги по целям — " + steps });
    if (e.verified_step_share !== null) {
      el(box, "div", { text: `Подтверждено журналом: ${Math.round(e.verified_step_share * 100)}%` });
    }
    if (e.repeated_blockers.length) el(box, "div", { cls: "anya-ask", text: "Повторяется: " + e.repeated_blockers.join("; ") });
    el(box, "div", { cls: "anya-muted", text: e.note });
  }
}

class PlanModal extends Modal {
  constructor(plugin, item, done) { super(plugin.app); this.plugin = plugin; this.item = item; this.done = done; }

  onOpen() {
    const p = { ...this.item.proposal };
    this.titleEl.setText("План: " + this.item.title);
    const fields = [["metric", "Показатель"], ["unit", "Единица"], ["baseline", "Сейчас (число)"],
                    ["target", "Цель (число)"], ["weekly_minutes", "Минут в неделю"],
                    ["next_step", "Первый шаг"], ["evidence", "Основание"]];
    for (const [key, label] of fields) {
      new Setting(this.contentEl).setName(label).addText((text) => text
        .setValue(p[key] === null || p[key] === undefined ? "" : String(p[key]))
        .onChange((value) => { p[key] = value; }));
    }
    new Setting(this.contentEl).addButton((b) => b.setButtonText("Утвердить").setCta().onClick(async () => {
      const body = { metric: p.metric, unit: p.unit, baseline: Number(p.baseline), target: Number(p.target),
                     weekly_minutes: parseInt(p.weekly_minutes, 10), next_step: p.next_step,
                     evidence: `${p.evidence} · утверждено владельцем в Obsidian` };
      if (String(p.baseline).trim() === "" || Number.isNaN(body.baseline) || Number.isNaN(body.target)) {
        new Notice("Укажи текущее значение и цель числами");
        return;
      }
      try {
        await this.plugin.api.call(`/api/goals/${this.item.goal_id}/plan`, { method: "POST", body });
        new Notice("План утверждён — Аня возьмётся за первый шаг");
        this.close();
        this.done();
      } catch (e) { new Notice("Аня: " + e.message); }
    }));
  }

  onClose() { this.contentEl.empty(); }
}

/* ---------- виды ---------- */

class DashboardView extends ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; }
  getViewType() { return DASHBOARD_VIEW; }
  getDisplayText() { return "Аня: цели"; }
  getIcon() { return "target"; }

  async onOpen() {
    const head = el(this.contentEl, "div", { cls: "anya-head" });
    el(head, "span", { cls: "anya-head-title", text: "Аня" });
    const refresh = el(head, "button", { cls: "anya-button" });
    setIcon(refresh, "refresh-cw");
    refresh.onclick = () => this.sections.render();
    this.sections = new Sections(this.plugin, el(this.contentEl, "div"), () => this.sections.render());
    await this.sections.render();
    this.timer = window.setInterval(() => this.sections.render(), this.plugin.settings.refreshSeconds * 1000);
    this.registerInterval(this.timer);
  }
}

class ChatView extends ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; this.busy = false; }
  getViewType() { return CHAT_VIEW; }
  getDisplayText() { return "Аня: чат"; }
  getIcon() { return "bot"; }

  async onOpen() {
    this.contentEl.addClass("anya-chat");
    this.log = el(this.contentEl, "div", { cls: "anya-log" });
    const form = el(this.contentEl, "div", { cls: "anya-form" });
    this.input = el(form, "textarea", { cls: "anya-input", attr: { placeholder: "Напиши Ане… (Ctrl+Enter — отправить)", rows: 3 } });
    const row = el(form, "div", { cls: "anya-actions" });
    const label = el(row, "label", { cls: "anya-muted" });
    this.attach = el(label, "input", { type: "checkbox" });
    this.attach.checked = this.plugin.settings.attachNote;
    label.appendText(" приложить открытую заметку");
    const send = el(row, "button", { cls: "anya-button mod-cta", text: "Отправить" });
    send.onclick = () => this.send();
    this.input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) this.send(); });
    await this.renderHistory();
    this.registerInterval(window.setInterval(() => { if (!this.busy) this.renderHistory(); },
                                             this.plugin.settings.refreshSeconds * 1000));
  }

  prefill(text) { this.input.value = text; this.input.focus(); }

  async renderHistory() {
    let state;
    try { state = await this.plugin.api.call("/api/state"); } catch (e) {
      this.log.empty();
      el(this.log, "div", { cls: "anya-error", text: "Аня не на связи: " + e.message });
      return;
    }
    this.log.empty();
    for (const m of state.messages.slice(-30)) await this.bubble(m.role, m.text, m.channel, m.ts);
    for (const a of state.approvals) {
      const box = el(this.log, "div", { cls: "anya-approval" });
      el(box, "div", { cls: "anya-goal-title", text: "Подтверди: " + a.summary });
      const actions = el(box, "div", { cls: "anya-actions" });
      for (const [text, approve] of [["Да", true], ["Нет", false]]) {
        el(actions, "button", { cls: "anya-button" + (approve ? " mod-cta" : ""), text }).onclick = async () => {
          await this.plugin.api.call(`/api/actions/${a.id}`, { method: "POST", body: { approve } });
          await this.renderHistory();
        };
      }
    }
    this.log.scrollTop = this.log.scrollHeight;
  }

  async bubble(role, text, channel, ts) {
    const box = el(this.log, "div", { cls: "anya-msg anya-" + (role === "user" ? "user" : "anya") });
    el(box, "div", { cls: "anya-meta", text: `${role === "user" ? "Ты" : "Аня"}${channel ? " · " + channel : ""}${ts ? " · " + String(ts).slice(11, 16) : ""}` });
    await MarkdownRenderer.render(this.app, text || "", el(box, "div"), "", this);
  }

  async send() {
    const text = this.input.value.trim();
    if (!text || this.busy) return;
    this.busy = true;
    this.input.value = "";
    await this.bubble("user", text, "obsidian");
    const waiting = el(this.log, "div", { cls: "anya-muted anya-thinking", text: "Аня думает…" });
    this.log.scrollTop = this.log.scrollHeight;
    try {
      const note = this.attach.checked ? await noteText(this.app) : "";
      const run = await this.plugin.api.chat(text, note);
      waiting.remove();
      if (run.status !== "completed") new Notice(`Аня: ${run.error || run.status}`);
    } catch (e) {
      waiting.setText("Не вышло: " + e.message);
    } finally {
      this.busy = false;
      await this.renderHistory();
    }
  }
}

/* ---------- плагин ---------- */

module.exports = class AnyaPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    this.api = new Api(this);
    this.registerView(CHAT_VIEW, (leaf) => new ChatView(leaf, this));
    this.registerView(DASHBOARD_VIEW, (leaf) => new DashboardView(leaf, this));
    this.addRibbonIcon("bot", "Аня: чат", () => this.openChat());
    this.addRibbonIcon("target", "Аня: цели", () => this.activate(DASHBOARD_VIEW, "right"));

    this.addCommand({ id: "open-chat", name: "Открыть чат", callback: () => this.openChat() });
    this.addCommand({ id: "open-dashboard", name: "Открыть цели и планы", callback: () => this.activate(DASHBOARD_VIEW, "right") });
    this.addCommand({
      id: "ask-selection", name: "Спросить Аню о выделенном",
      editorCallback: (editor) => {
        const selected = editor.getSelection();
        this.openChat(selected ? `О фрагменте:\n> ${selected.split("\n").join("\n> ")}\n\n` : "");
      },
    });
    this.addCommand({
      id: "selection-to-tasks", name: "Выделенное — в задачи Ани",
      editorCallback: async (editor) => {
        const lines = editor.getSelection().split("\n").map((l) => l.replace(/^\s*(?:[-*]\s*)?(?:\[[ x]\]\s*)?/, "").trim()).filter(Boolean);
        if (!lines.length) { new Notice("Выдели строки — каждая станет задачей"); return; }
        try {
          for (const title of lines) await this.api.call("/api/tasks", { method: "POST", body: { title } });
          new Notice(`Аня: задач добавлено — ${lines.length}`);
        } catch (e) { new Notice("Аня: " + e.message); }
      },
    });

    // ```anya goals tasks``` — живая панель прямо в заметке
    this.registerMarkdownCodeBlockProcessor("anya", async (source, root) => {
      const names = source.split(/\s+/).map((s) => s.trim()).filter((s) => SECTIONS.includes(s));
      const sections = new Sections(this, root.createDiv({ cls: "anya-embed" }), () => sections.render(names.length ? names : SECTIONS));
      await sections.render(names.length ? names : SECTIONS);
    });

    this.status = this.addStatusBarItem();
    this.updateStatus();
    this.registerInterval(window.setInterval(() => this.updateStatus(), this.settings.refreshSeconds * 1000));
    this.addSettingTab(new AnyaSettings(this));
  }

  async updateStatus() {
    try {
      const d = await this.api.call("/api/dashboard");
      const busy = d.anya.thinking ? "думает" : d.anya.goal ? "над целью" : d.anya.task ? "поручение" : d.anya.hh ? "откликается" : "";
      const waiting = d.plan_proposals.length + d.consent_needed.length + d.approvals.length;
      this.status.setText(`🤖 Аня${busy ? " · " + busy : ""}${waiting ? " · ждёт тебя: " + waiting : ""}`);
    } catch (e) {
      this.status.setText("🤖 Аня не на связи");
    }
  }

  async activate(type, side) {
    let leaf = this.app.workspace.getLeavesOfType(type)[0];
    if (!leaf) {
      leaf = side === "right" ? this.app.workspace.getRightLeaf(false) : this.app.workspace.getLeaf("tab");
      await leaf.setViewState({ type, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    return leaf;
  }

  async openChat(prefill = "") {
    const leaf = await this.activate(CHAT_VIEW, "right");
    if (prefill && leaf.view && leaf.view.prefill) leaf.view.prefill(prefill);
  }

  async saveSettings() { await this.saveData(this.settings); this.api.token = null; }
};

class AnyaSettings extends PluginSettingTab {
  constructor(plugin) { super(plugin.app, plugin); this.plugin = plugin; }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    const s = this.plugin.settings;
    const text = (name, desc, key) => new Setting(containerEl).setName(name).setDesc(desc)
      .addText((t) => t.setValue(String(s[key])).onChange(async (v) => { s[key] = v; await this.plugin.saveSettings(); }));
    text("Адрес ядра", "Локальный API Übermensch", "apiUrl");
    text("Файл токена", "secrets/api_token в папке данных Ани", "tokenFile");
    new Setting(containerEl).setName("Обновлять, секунд").addText((t) => t.setValue(String(s.refreshSeconds))
      .onChange(async (v) => { s.refreshSeconds = Math.max(15, parseInt(v, 10) || 60); await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("Прикладывать открытую заметку по умолчанию").addToggle((t) => t
      .setValue(s.attachNote).onChange(async (v) => { s.attachNote = v; await this.plugin.saveSettings(); }));
  }
}
