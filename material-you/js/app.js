import { SEEDS, normalizeHex, applyScheme } from "./theme.js";

/* ==========================================================================
   Freenerf — Material You shell behaviour
   Screens, sessions, the composer demo, approval flow and the Theme Studio.
   The UI is a faithful web rendering of the app shell; tool results in the
   demo copy describe real outcomes, and every action here is local.
   ========================================================================== */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const escapeHtml = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);

const richText = (value = "") =>
  escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

/* ---- Persisted theme ---------------------------------------------------- */
const STORE_KEY = "freenerf.material-you.theme";
const THEME_DEFAULTS = {
  mode: "system",
  seed: "verdant",
  custom: "",
  contrast: "standard",
  amoled: false,
};

function loadTheme() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    return {
      mode: ["system", "light", "dark"].includes(raw.mode) ? raw.mode : THEME_DEFAULTS.mode,
      seed: SEEDS[raw.seed] ? raw.seed : THEME_DEFAULTS.seed,
      custom: normalizeHex(raw.custom) || "",
      contrast: ["standard", "medium", "high"].includes(raw.contrast)
        ? raw.contrast
        : THEME_DEFAULTS.contrast,
      amoled: Boolean(raw.amoled),
    };
  } catch {
    return { ...THEME_DEFAULTS };
  }
}

function saveTheme() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(theme));
  } catch {
    /* Storage can be unavailable in private windows; the theme still applies. */
  }
}

let theme = loadTheme();
let effectiveDark = false;

/* ---- Demo data ---------------------------------------------------------- */
function demoThread() {
  return [
    {
      type: "prompt",
      text: "The parser test fails on nested arrays. Find the cause and fix it, then run the suite.",
    },
    {
      type: "agent",
      text: "I'll reproduce the failure first, then read the parser around the array branch.",
    },
    {
      type: "tool",
      name: "run_tests",
      status: "done",
      duration: "4.2 s",
      summary: "npm test -- parser.spec.ts · 1 failing: nested arrays keep the outer index",
      detail:
        "FAIL src/parser.spec.ts\n  nested arrays\n    ✗ keeps the inner index (3 ms)\n      expected [ [1, 2] ] but received [ [1] ]\n\n1 failing, 41 passing",
    },
    {
      type: "tool",
      name: "read_file",
      status: "done",
      duration: "0.3 s",
      summary: "src/parser.ts · lines 41–96",
      detail:
        "41  function parseArray(input, i) {\n42    const value = []\n43    while (i < input.length) {\n44      value.push(parseValue(input, i))\n45    }\n46    return { value, i }\n47  }",
    },
    { type: "agent", text: "The array branch reuses the parent index:" },
    {
      type: "code",
      code:
        "// src/parser.ts · 68\nwhile (i < input.length) {\n  value.push(parseValue(input, i)); // i never advances\n}",
    },
    {
      type: "tool",
      name: "edit_file",
      status: "done",
      duration: "0.2 s",
      summary: "src/parser.ts · 1 line changed, backup saved",
      detail: "- value.push(parseValue(input, i))\n+ value.push(parseValue(input, i++))",
    },
    {
      type: "tool",
      name: "run_shell",
      status: "waiting",
      duration: "waiting",
      summary: "git pull --rebase · waiting for approval",
      detail: "git pull --rebase",
      approvable: true,
    },
    { type: "status", tone: "running", text: "Verifying with the test suite…" },
  ];
}

function shortThread(prompt, intro, tool, outcome) {
  return [
    { type: "prompt", text: prompt },
    { type: "agent", text: intro },
    tool,
    { type: "status", tone: "done", text: outcome },
  ];
}

let sessions = [
  {
    id: "s1",
    title: "Fix the failing parser test",
    time: "2m ago",
    status: "open",
    activity: "Responding",
    messages: demoThread(),
  },
  {
    id: "s2",
    title: "Add rate limiting to the upload route",
    time: "18m ago",
    status: "open",
    activity: "Completed",
    messages: shortThread(
      "Add rate limiting to the upload route.",
      "I'll find the upload route and the middleware chain it uses.",
      {
        type: "tool",
        name: "edit_file",
        status: "done",
        duration: "0.4 s",
        summary: "src/routes/upload.ts · added a sliding-window limit",
        detail: "+ const limit = rateLimit({ windowMs: 60_000, max: 12 })",
      },
      "Completed · 12 requests/minute enforced",
    ),
  },
  {
    id: "s3",
    title: "Explain the project structure",
    time: "3h ago",
    status: "open",
    activity: "Completed",
    messages: [
      { type: "prompt", text: "Explain the project structure." },
      {
        type: "agent",
        text: "Three layers: the Python TUI in `src/freenerf/`, the Rust agent core in `native/`, and the Compose client in `android/`.",
      },
      {
        type: "code",
        code:
          "src/freenerf/    Textual app, providers, tools\nnative/          Rust crates, agent loop, JNI\nandroid/         Compose UI, theme, storage\nscripts/         Toolchain + native builds",
      },
    ],
  },
  {
    id: "s4",
    title: "Write a README for this project",
    time: "yesterday",
    status: "closed",
    messages: [
      { type: "prompt", text: "Write a README for this project." },
      {
        type: "agent",
        text: "I drafted the README from the actual sources: install, interface, slash commands, tasks and permissions.",
      },
    ],
  },
  {
    id: "s5",
    title: "Migrate the settings store to JSON",
    time: "Nov 21",
    status: "closed",
    messages: [
      { type: "prompt", text: "Migrate the settings store to JSON." },
      {
        type: "agent",
        text: "The legacy config is imported once, then written as versioned JSON with the keys masked in every read path.",
      },
    ],
  },
];

const state = {
  screen: "chat",
  filter: "all",
  query: "",
  running: false,
  settingsTab: "general",
};

let activeId = "s1";

/* ---- Element references ------------------------------------------------- */
const chatTitle = $("#chat-title");
const chatSub = $("#chat-sub-text");
const chatBar = $("#chat-bar");
const chatScroll = $("#chat-scroll");
const thread = $("#chat-thread");
const promptEl = $("#prompt");
const sendBtn = $("#send-btn");
const stopBtn = $("#stop-btn");
const chatMenuBtn = $("#chat-menu-btn");

const sessionsBar = $("#sessions-bar");
const sessionsScroll = $("#sessions-scroll");
const sessionList = $("#session-list");
const sessionsCount = $("#sessions-count");
const sessionsEmpty = $("#sessions-empty");
const searchToggle = $("#search-toggle");
const sessionsSearch = $("#sessions-search");
const sessionSearch = $("#session-search");
const newSessionBtn = $("#new-session");

const railTheme = $("#rail-theme");
const amoledSwitch = $("#amoled-switch");
const customHex = $("#custom-hex");
const applyHexBtn = $("#apply-hex");
const accentStatus = $("#accent-status");
const modelNote = $("#model-note");

const sessionMenu = $("#session-menu");
const approvalDialog = $("#approval-dialog");
const renameDialog = $("#rename-dialog");
const renameForm = $("#rename-form");
const renameInput = $("#rename-input");
const snackbar = $("#snackbar");

let pendingTool = null;
let renameTarget = null;
let runToken = 0;
let snackbarTimer = 0;

const activeSession = () => sessions.find((session) => session.id === activeId) || null;

/* ---- Chat rendering ----------------------------------------------------- */
const EMPTY_MARKUP = `
  <div class="empty-chat">
    <div class="empty-chat__hero" aria-hidden="true"><span class="ms">forum</span></div>
    <h2 class="md-headline-small">New session</h2>
    <p class="md-body-medium">Describe the task. The agent reads, edits and runs tools inside your workspace.</p>
    <div class="suggestions">
      <button class="suggestion" type="button" data-suggestion="Summarize this workspace">
        <span class="ms" aria-hidden="true">notes</span>
        <span class="suggestion__label">Summarize this workspace</span>
        <span class="ms suggestion__arrow" aria-hidden="true">arrow_forward</span>
      </button>
      <button class="suggestion" type="button" data-suggestion="Find and fix a failing test">
        <span class="ms" aria-hidden="true">task_alt</span>
        <span class="suggestion__label">Find and fix a failing test</span>
        <span class="ms suggestion__arrow" aria-hidden="true">arrow_forward</span>
      </button>
      <button class="suggestion" type="button" data-suggestion="Explain the project structure">
        <span class="ms" aria-hidden="true">folder_open</span>
        <span class="suggestion__label">Explain the project structure</span>
        <span class="ms suggestion__arrow" aria-hidden="true">arrow_forward</span>
      </button>
      <button class="suggestion" type="button" data-suggestion="Write a README for this project">
        <span class="ms" aria-hidden="true">description</span>
        <span class="suggestion__label">Write a README for this project</span>
        <span class="ms suggestion__arrow" aria-hidden="true">arrow_forward</span>
      </button>
    </div>
  </div>`;

function renderMessage(message) {
  if (message.type === "prompt") {
    const block = document.createElement("div");
    block.className = "prompt-block";
    block.innerHTML = `<span class="prompt-block__label">PROMPT</span><p>${richText(message.text)}</p>`;
    return block;
  }

  if (message.type === "agent") {
    const text = document.createElement("div");
    text.className = "agent-text";
    text.innerHTML = richText(message.text);
    return text;
  }

  if (message.type === "code") {
    const pre = document.createElement("pre");
    pre.className = "code-block";
    const code = document.createElement("code");
    code.textContent = message.code;
    pre.append(code);
    return pre;
  }

  if (message.type === "status") {
    const pill = document.createElement("div");
    pill.className = "status-pill";
    if (message.tone === "done") pill.classList.add("status-pill--done");
    if (message.tone === "error") pill.classList.add("status-pill--error");
    const marker =
      message.tone === "running"
        ? '<span class="spinner" aria-hidden="true"></span>'
        : `<span class="ms" aria-hidden="true">${
            message.tone === "done" ? "check" : message.tone === "error" ? "error" : "sync"
          }</span>`;
    pill.innerHTML = `${marker}<span>${escapeHtml(message.text)}</span>`;
    return pill;
  }

  if (message.type === "tool") return renderTool(message);
  return document.createElement("div");
}

function renderTool(message) {
  const card = document.createElement("article");
  card.className = `tool-card tool-card--${message.status}`;

  const head = document.createElement("button");
  head.type = "button";
  head.className = "tool-card__head";
  const expanded = message.status === "waiting";
  head.setAttribute("aria-expanded", String(expanded));

  const tile =
    message.status === "running"
      ? '<span class="spinner" aria-hidden="true"></span>'
      : `<span class="ms" aria-hidden="true">${
          message.status === "done"
            ? "check"
            : message.status === "failed"
              ? "error"
              : message.status === "waiting"
                ? "schedule"
                : "sync"
        }</span>`;

  head.innerHTML = `
    <span class="tool-card__tile">${tile}</span>
    <span class="tool-card__name">${escapeHtml(message.name)}</span>
    <span class="tool-card__time">${escapeHtml(message.duration || "")}</span>
    <span class="ms tool-card__chevron" aria-hidden="true">expand_more</span>`;

  const body = document.createElement("div");
  body.className = "tool-card__body";
  body.hidden = !expanded;
  body.innerHTML = `
    <p class="tool-card__summary">${richText(message.summary || "")}</p>
    ${message.detail ? `<pre class="tool-card__detail">${escapeHtml(message.detail)}</pre>` : ""}
    ${message.approvable ? '<button class="tool-card__review" type="button">Review request</button>' : ""}`;

  const review = body.querySelector(".tool-card__review");
  if (review) {
    review.addEventListener("click", (event) => {
      event.stopPropagation();
      pendingTool = message;
      approvalDialog.returnValue = "";
      approvalDialog.showModal();
    });
  }

  head.addEventListener("click", () => {
    const open = head.getAttribute("aria-expanded") === "true";
    head.setAttribute("aria-expanded", String(!open));
    body.hidden = open;
  });

  card.append(head, body);
  return card;
}

function renderThread() {
  const session = activeSession();
  thread.innerHTML = "";

  if (!session || session.messages.length === 0) {
    thread.classList.add("is-empty");
    thread.innerHTML = EMPTY_MARKUP;
    return;
  }

  thread.classList.remove("is-empty");
  session.messages.forEach((message) => thread.append(renderMessage(message)));
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function updateChatHeader() {
  const session = activeSession();
  chatTitle.textContent = session ? session.title : "New session";
  chatSub.textContent = session
    ? `${session.activity || (session.status === "closed" ? "Closed" : "Ready")} · projects/api`
    : "projects/api · workspace";
}

function pushMessage(session, message) {
  session.messages.push(message);
  if (session.id === activeId) renderThread();
}

/* ---- Sessions ----------------------------------------------------------- */
function renderSessions() {
  const query = state.query.trim().toLowerCase();
  const filtered = sessions.filter((session) => {
    const statusMatches = state.filter === "all" || session.status === state.filter;
    const queryMatches = !query || session.title.toLowerCase().includes(query);
    return statusMatches && queryMatches;
  });

  sessionList.innerHTML = "";

  filtered.forEach((session) => {
    const card = document.createElement("div");
    card.className = "session-card";

    const main = document.createElement("button");
    main.type = "button";
    main.className = "session-card__main";
    main.setAttribute("aria-label", `Open session: ${session.title}`);
    main.innerHTML = `
      <span class="session-card__tile"><span class="ms" aria-hidden="true">forum</span></span>
      <span class="session-card__text">
        <span class="session-card__title">${escapeHtml(session.title)}</span>
        <span class="session-card__sub${session.status === "closed" ? " is-closed" : ""}">
          ${session.status === "closed" ? "Closed · " : ""}${escapeHtml(session.time)}${
            session.id === activeId ? " · Active" : ""
          }
        </span>
      </span>`;
    main.addEventListener("click", () => openSession(session.id));

    const more = document.createElement("button");
    more.type = "button";
    more.className = "md-icon-btn session-card__more";
    more.setAttribute("aria-label", `Actions for ${session.title}`);
    more.setAttribute("aria-haspopup", "menu");
    more.innerHTML = '<span class="ms" aria-hidden="true">more_vert</span>';
    more.addEventListener("click", () => openSessionMenu(more, session));

    card.append(main, more);
    sessionList.append(card);
  });

  sessionsEmpty.hidden = filtered.length > 0;
  sessionsEmpty.textContent =
    sessions.length === 0
      ? "No sessions yet. Describe a task in Chat to start one."
      : "No sessions match this filter.";

  sessionsCount.textContent =
    state.filter === "all" && !query
      ? `${sessions.length} session${sessions.length === 1 ? "" : "s"}`
      : `${filtered.length} of ${sessions.length} sessions`;
}

function openSession(id) {
  activeId = id;
  updateChatHeader();
  renderSessions();
  go("chat");
}

function newSession() {
  const session = {
    id: `s-${Date.now()}`,
    title: "New session",
    time: "now",
    status: "open",
    activity: "",
    messages: [],
  };
  sessions.unshift(session);
  activeId = session.id;
  updateChatHeader();
  renderSessions();
  go("chat");
  renderThread();
  promptEl.focus();
}

/* ---- Session menu ------------------------------------------------------- */
let menuAnchor = null;

function closeMenu() {
  if (sessionMenu.hidden) return;
  sessionMenu.hidden = true;
  sessionMenu.innerHTML = "";
  if (menuAnchor) menuAnchor.setAttribute("aria-expanded", "false");
  menuAnchor = null;
}

function menuItem(icon, label, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "md-menu__item";
  if (options.danger) button.classList.add("md-menu__item--danger");
  button.setAttribute("role", "menuitem");
  button.innerHTML = `<span class="ms" aria-hidden="true">${icon}</span>${escapeHtml(label)}`;
  button.addEventListener("click", options.onSelect);
  return button;
}

function openSessionMenu(anchor, session) {
  closeMenu();
  menuAnchor = anchor;
  sessionMenu.innerHTML = "";
  sessionMenu.append(
    menuItem("edit", "Rename session", { onSelect: () => openRename(session) }),
    menuItem(
      session.status === "closed" ? "unarchive" : "archive",
      session.status === "closed" ? "Reopen session" : "Close session",
      {
        onSelect: () => {
          session.status = session.status === "closed" ? "open" : "closed";
          closeMenu();
          renderSessions();
          updateChatHeader();
          showSnackbar(
            session.status === "closed"
              ? "Session closed. It stays in history."
              : "Session reopened.",
          );
        },
      },
    ),
  );

  const divider = document.createElement("div");
  divider.className = "md-menu__divider";
  sessionMenu.append(
    divider,
    menuItem("delete", "Delete session", {
      danger: true,
      onSelect: () => {
        closeMenu();
        deleteSession(session);
      },
    }),
  );

  sessionMenu.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const width = sessionMenu.offsetWidth;
  const height = sessionMenu.offsetHeight;
  const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
  let top = rect.bottom + 4;
  if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
  sessionMenu.style.left = `${left}px`;
  sessionMenu.style.top = `${top}px`;
  menuAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");

  const first = sessionMenu.querySelector(".md-menu__item");
  if (first) first.focus({ preventScroll: true });
}

function deleteSession(session) {
  const index = sessions.indexOf(session);
  if (index < 0) return;
  sessions.splice(index, 1);
  if (activeId === session.id) {
    activeId = sessions[0]?.id ?? null;
    updateChatHeader();
    renderThread();
  }
  renderSessions();
  showSnackbar("Session deleted", "Undo", () => {
    sessions.splice(Math.min(index, sessions.length), 0, session);
    renderSessions();
  });
}

/* ---- Rename dialog ------------------------------------------------------ */
function openRename(session) {
  closeMenu();
  renameTarget = session;
  renameInput.value = session.title;
  renameDialog.returnValue = "";
  renameDialog.showModal();
  renameInput.focus();
  renameInput.select();
}

renameForm.addEventListener("submit", (event) => {
  renameDialog.returnValue = event.submitter?.value || "save";
});

renameDialog.addEventListener("close", () => {
  if (renameDialog.returnValue !== "save" || !renameTarget) {
    renameTarget = null;
    return;
  }
  const title = renameInput.value.trim();
  if (title) {
    renameTarget.title = title;
    if (renameTarget.id === activeId) updateChatHeader();
    renderSessions();
    showSnackbar("Session renamed");
  }
  renameTarget = null;
});

/* ---- Approval dialog ---------------------------------------------------- */
approvalDialog.addEventListener("close", () => {
  const answer = approvalDialog.returnValue;
  if (!pendingTool || (answer !== "allow" && answer !== "deny")) {
    pendingTool = null;
    return;
  }

  const allowed = answer === "allow";
  pendingTool.status = allowed ? "done" : "failed";
  pendingTool.duration = allowed ? "0.7 s" : "denied";
  pendingTool.summary = allowed
    ? "git pull --rebase · already up to date"
    : "git pull --rebase · denied in the approval dialog";
  pendingTool.detail = allowed
    ? "Current branch main is up to date.\nFast-forwarded 0 commits."
    : "The command was not run. The agent will choose another approach.";
  pendingTool.approvable = false;
  pendingTool = null;

  renderThread();
  showSnackbar(
    allowed ? "run_shell approved · command completed" : "Denied · the agent will try another approach",
  );
});

/* ---- Theme Studio ------------------------------------------------------- */
const seedLabel = (seed) => seed.charAt(0).toUpperCase() + seed.slice(1);

function syncThemeControls() {
  $$("[data-mode]").forEach((button) =>
    button.setAttribute("aria-pressed", String(theme.mode === button.dataset.mode)),
  );
  $$("[data-contrast]").forEach((button) =>
    button.setAttribute("aria-pressed", String(theme.contrast === button.dataset.contrast)),
  );
  amoledSwitch.checked = theme.amoled;

  $$(".accent-swatch").forEach((swatch) => {
    const selected = !theme.custom && theme.seed === swatch.dataset.seed;
    swatch.setAttribute("aria-pressed", String(selected));
  });

  customHex.value = theme.custom || "";
  customHex.removeAttribute("aria-invalid");
  accentStatus.classList.remove("is-error");
  accentStatus.textContent = theme.custom
    ? `Custom seed ${theme.custom} · ${effectiveDark ? "dark" : "light"} palette across every role.`
    : `${seedLabel(theme.seed)} seed · ${effectiveDark ? "dark" : "light"} palette · ${theme.contrast} contrast.`;

  railTheme.innerHTML = `<span class="ms" aria-hidden="true">${effectiveDark ? "light_mode" : "dark_mode"}</span>`;
  railTheme.setAttribute("aria-label", effectiveDark ? "Switch to light theme" : "Switch to dark theme");
}

function applyTheme() {
  effectiveDark = applyScheme(theme).dark;
  syncThemeControls();
}

function setTheme(patch, announce) {
  theme = { ...theme, ...patch };
  saveTheme();
  applyTheme();
  if (announce) showSnackbar(announce);
}

$$("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => setTheme({ mode: button.dataset.mode }));
});

$$("[data-contrast]").forEach((button) => {
  button.addEventListener("click", () =>
    setTheme({ contrast: button.dataset.contrast }, `${button.dataset.contrast} contrast`),
  );
});

$$(".accent-swatch").forEach((swatch) => {
  swatch.addEventListener("click", () => {
    setTheme({ seed: swatch.dataset.seed, custom: "" }, `${swatch.getAttribute("aria-label")} accent`);
  });
});

amoledSwitch.addEventListener("change", () => setTheme({ amoled: amoledSwitch.checked }));

function applyCustomAccent() {
  const value = normalizeHex(customHex.value);
  if (!value) {
    accentStatus.textContent = "Enter a six-digit hex colour, for example #4F6B52.";
    accentStatus.classList.add("is-error");
    customHex.setAttribute("aria-invalid", "true");
    customHex.focus();
    return;
  }
  customHex.removeAttribute("aria-invalid");
  setTheme({ custom: value }, `Accent set to ${value}`);
}

applyHexBtn.addEventListener("click", applyCustomAccent);
customHex.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    applyCustomAccent();
  }
});

railTheme.addEventListener("click", () => {
  setTheme({ mode: effectiveDark ? "light" : "dark" });
});

const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const onSystemTheme = () => {
  if (theme.mode === "system") applyTheme();
};
if (systemTheme.addEventListener) systemTheme.addEventListener("change", onSystemTheme);
else systemTheme.addListener(onSystemTheme);

/* ---- Navigation --------------------------------------------------------- */
function go(screen) {
  if (!["chat", "sessions", "settings"].includes(screen)) screen = "chat";
  state.screen = screen;

  $$(".screen").forEach((element) => {
    element.hidden = element.id !== `screen-${screen}`;
  });

  $$("[data-screen]").forEach((button) => {
    if (button.dataset.screen === screen) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  if (screen === "sessions") {
    renderSessions();
    sessionsScroll.scrollTop = 0;
    sessionsBar.classList.remove("is-collapsed");
  }
  if (screen === "chat") renderThread();

  const current = $(`#screen-${screen}`);
  current.classList.remove("is-entering");
  void current.offsetWidth;
  current.classList.add("is-entering");

  if (location.hash !== `#${screen}`) history.replaceState(null, "", `#${screen}`);
}

$$("[data-screen]").forEach((button) => {
  button.addEventListener("click", () => go(button.dataset.screen));
});

$$("[data-go]").forEach((button) => {
  button.addEventListener("click", () => go(button.dataset.go));
});

/* ---- Settings tabs ------------------------------------------------------ */
const tabs = $$(".md-tab");

function showTab(name) {
  state.settingsTab = name;
  tabs.forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $$(".settings-panel").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const index = tabs.indexOf(tab);
    let next = null;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    showTab(next.dataset.tab);
    next.focus();
  });
});

$("#settings-about").addEventListener("click", () => {
  showTab("about");
  $("#settings-scroll").scrollTo({ top: 0 });
});

/* ---- Composer ----------------------------------------------------------- */
function autosize() {
  promptEl.style.height = "auto";
  promptEl.style.height = `${Math.min(promptEl.scrollHeight, 160)}px`;
}

function syncComposer() {
  sendBtn.hidden = state.running;
  stopBtn.hidden = !state.running;
}

function send() {
  const value = promptEl.value.trim();
  if (!value) return;
  if (state.running) {
    showSnackbar("Wait for this run to finish, or stop it first.");
    return;
  }

  let session = activeSession();
  if (!session) {
    newSession();
    session = activeSession();
  }

  if (session.messages.length === 0) {
    session.title = value.length > 72 ? `${value.slice(0, 69)}…` : value;
    session.time = "now";
    updateChatHeader();
  }
  if (session.status === "closed") session.status = "open";

  session.messages.push({ type: "prompt", text: value });
  promptEl.value = "";
  autosize();
  renderThread();
  renderSessions();
  simulate(session);
}

async function simulate(session) {
  const token = ++runToken;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  state.running = true;
  syncComposer();
  session.activity = "Thinking";
  if (session.id === activeId) updateChatHeader();

  await wait(450);
  if (token !== runToken) return;

  pushMessage(session, {
    type: "agent",
    text: "I'll read the workspace and its checks before changing anything.",
  });

  await wait(650);
  if (token !== runToken) return;

  const read = {
    type: "tool",
    name: "read_file",
    status: "running",
    duration: "…",
    summary: "Scanning projects/api",
    detail: "",
  };
  pushMessage(session, read);

  await wait(850);
  if (token !== runToken) return;
  read.status = "done";
  read.duration = "0.4 s";
  read.summary = "projects/api · 18 files, 3 packages";
  read.detail = "projects/api\n  src/\n  tests/\n  package.json";
  if (session.id === activeId) renderThread();

  const tests = {
    type: "tool",
    name: "run_tests",
    status: "running",
    duration: "…",
    summary: "npm test",
    detail: "",
  };
  pushMessage(session, tests);

  await wait(1100);
  if (token !== runToken) return;
  tests.status = "done";
  tests.duration = "3.8 s";
  tests.summary = "npm test · 42 passing, 0 failing";
  tests.detail = "Test Suites: 6 passed, 6 total\nTests:       42 passed, 42 total\nTime:        3.8 s";
  if (session.id === activeId) renderThread();

  pushMessage(session, {
    type: "agent",
    text: "The workspace is healthy: 42 tests pass and nothing is left open. `src/parser.ts` keeps the index fix, and no files outside the workspace were touched.",
  });
  pushMessage(session, { type: "status", tone: "done", text: "Completed · verified with the test suite" });

  session.activity = "Completed";
  state.running = false;
  syncComposer();
  if (session.id === activeId) updateChatHeader();
  showSnackbar("Task completed · 42 tests passed");
}

function stopRun() {
  if (!state.running) return;
  runToken += 1;
  state.running = false;
  const session = activeSession();
  if (session) {
    session.messages.forEach((message) => {
      if (message.type === "tool" && message.status === "running") {
        message.status = "failed";
        message.duration = "cancelled";
        message.summary = `${message.summary || "Tool call"} · cancelled`;
      }
    });
    pushMessage(session, { type: "status", tone: "error", text: "Cancelled · you stopped the run" });
    session.activity = "Cancelled";
    if (session.id === activeId) updateChatHeader();
  }
  syncComposer();
  showSnackbar("Run cancelled");
}

sendBtn.addEventListener("click", send);
stopBtn.addEventListener("click", stopRun);
promptEl.addEventListener("input", autosize);
promptEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

thread.addEventListener("click", (event) => {
  const suggestion = event.target.closest("[data-suggestion]");
  if (!suggestion) return;
  promptEl.value = suggestion.dataset.suggestion;
  autosize();
  promptEl.focus();
});

$("#attach-btn").addEventListener("click", () => {
  showSnackbar("Attachments are imported through the workspace mirror.");
});

chatMenuBtn.addEventListener("click", () => {
  const session = activeSession();
  if (!session) {
    showSnackbar("No session yet — send a prompt to start one.");
    return;
  }
  openSessionMenu(chatMenuBtn, session);
});

/* ---- Sessions controls -------------------------------------------------- */
newSessionBtn.addEventListener("click", newSession);

$$("[data-filter]").forEach((chip) => {
  chip.addEventListener("click", () => {
    state.filter = chip.dataset.filter;
    $$("[data-filter]").forEach((other) =>
      other.setAttribute("aria-pressed", String(other.dataset.filter === state.filter)),
    );
    renderSessions();
  });
});

searchToggle.addEventListener("click", () => {
  const open = sessionsSearch.hidden;
  sessionsSearch.hidden = !open;
  searchToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    sessionSearch.focus();
  } else {
    sessionSearch.value = "";
    state.query = "";
    renderSessions();
  }
});

sessionSearch.addEventListener("input", () => {
  state.query = sessionSearch.value;
  renderSessions();
});

sessionsScroll.addEventListener(
  "scroll",
  () => {
    sessionsBar.classList.toggle("is-collapsed", sessionsScroll.scrollTop > 16);
  },
  { passive: true },
);

chatScroll.addEventListener(
  "scroll",
  () => {
    chatBar.classList.toggle("is-scrolled", chatScroll.scrollTop > 2);
  },
  { passive: true },
);

sessionsScroll.addEventListener("scroll", closeMenu, { passive: true });
chatScroll.addEventListener("scroll", closeMenu, { passive: true });
$("#settings-scroll").addEventListener("scroll", closeMenu, { passive: true });

/* ---- Providers / models / permissions ---------------------------------- */
$$("[data-add-key]").forEach((chip) => {
  chip.addEventListener("click", () => {
    showSnackbar(`${chip.dataset.addKey}: keys are entered once and kept in the Android Keystore.`);
  });
});

$$('input[name="model"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    modelNote.textContent = `Current model: ${radio.value}. The catalog loads live from the active provider.`;
    showSnackbar(`Model set to ${radio.value}`);
  });
});

$$('input[name="permission"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    showSnackbar(`Permission mode set to ${radio.value}`);
  });
});

/* ---- Snackbar ----------------------------------------------------------- */
function hideSnackbar() {
  snackbar.classList.remove("is-visible");
  window.setTimeout(() => {
    if (!snackbar.classList.contains("is-visible")) snackbar.hidden = true;
  }, 250);
}

function showSnackbar(message, actionLabel, onAction) {
  window.clearTimeout(snackbarTimer);
  snackbar.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = message;
  snackbar.append(text);

  if (actionLabel) {
    const action = document.createElement("button");
    action.type = "button";
    action.className = "md-snackbar__action";
    action.textContent = actionLabel;
    action.addEventListener("click", () => {
      hideSnackbar();
      if (typeof onAction === "function") onAction();
    });
    snackbar.append(action);
  }

  snackbar.hidden = false;
  requestAnimationFrame(() => snackbar.classList.add("is-visible"));
  snackbarTimer = window.setTimeout(hideSnackbar, 6000);
}

/* ---- Menu dismissal ----------------------------------------------------- */
document.addEventListener("pointerdown", (event) => {
  if (sessionMenu.hidden) return;
  if (sessionMenu.contains(event.target)) return;
  closeMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenu();
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    go("chat");
    promptEl.focus();
  }
});

window.addEventListener("resize", closeMenu);

/* ---- Ripple ------------------------------------------------------------- */
document.addEventListener("pointerdown", (event) => {
  const target = event.target.closest(
    ".md-filled-btn, .md-tonal-btn, .md-outlined-btn, .md-text-btn, .md-fab, .md-icon-btn, .md-chip, .md-nav-item, .md-nav-bar-item, .session-card__main, .suggestion, .md-segmented__item, .md-tab",
  );
  if (!target || target.disabled) return;

  const rect = target.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height);
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.width = `${size}px`;
  ripple.style.height = `${size}px`;
  ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
  ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
  target.append(ripple);
  window.setTimeout(() => ripple.remove(), 600);
});

/* ---- Start -------------------------------------------------------------- */
renderSessions();
applyTheme();
showTab("general");
updateChatHeader();
syncComposer();
autosize();

const initial = location.hash.replace("#", "");
go(["chat", "sessions", "settings"].includes(initial) ? initial : "chat");
