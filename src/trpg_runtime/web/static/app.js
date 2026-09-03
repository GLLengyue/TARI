/* TARI web console - vanilla JS single page app. */

const I18N = {
  zh: {
    library: "素材库", compose: "组合新战役", campaigns: "战役", settings: "设置",
    scenarios: "剧本", cards: "角色卡", worlds: "世界观", rules: "规则预设",
    use: "使用", entries: "条", noResources: "还没有素材，去上传吧。", warnings: "扫描警告",
    uploadCard: "上传角色卡 (PNG/JSON)", uploadWorld: "上传世界观 (JSON)", uploadScenario: "上传剧本 (YAML)",
    uploaded: "已上传", uploadFailed: "上传失败",
    scenario: "剧本（可选）", card: "角色卡（可选）", world: "世界观（可选）",
    ruleset: "规则预设", customRules: "自定义规则文本（非空时替换预设）", language: "语言",
    campaignId: "战役 ID（留空自动生成）", seed: "随机种子（可选）", playerName: "玩家名称",
    playerDesc: "玩家角色描述", preview: "组合预览", create: "创建战役并开始", creating: "创建中…",
    noPreview: "选择素材后这里会显示预览。", opening: "开场", scene: "场景",
    player: "玩家", actor: "角色", facts: "世界事实", storyFramework: "剧情框架",
    effectiveRules: "生效规则", diceNote: "v1 骰子引擎固定 2d6（PbtA 档位）",
    emptyCampaigns: "还没有战役，去组合一个吧。", play: "游玩", turnN: "第 {n} 回合",
    inputPlaceholder: "描述你的行动…（/quit 退出由窗口关闭代替）", send: "发送",
    thinking: "GM 思考中…", gm: "GM", actorLabel: "角色", playerLabel: "你",
    dice: "掷骰", reasoning: "GM 推理", gmView: "GM 视图", publicView: "公开视图",
    hiddenFacts: "隐藏事实", events: "事件日志", openingLabel: "开场",
    error: "回合失败", cached: "（已缓存结果）", factsEmpty: "暂无公开事实",
    storyPremise: "前提", requiredBeats: "必需节拍", optionalBeats: "可选节拍",
    forbidden: "禁止揭示", endings: "可能结局", languageName: "中文",
    save: "保存设置", saved: "已保存", keyStatus: "API Key", keyReady: "已配置",
    keyMissing: "未配置（请放入 .env）", configPath: "配置路径", dbPath: "数据文件",
    models: "模型", agents: "Agent 参数", modelId: "模型 ID", temperature: "温度",
    maxTokens: "最大输出", retries: "重试次数", gmAgent: "GM", actorAgent: "角色", auditorAgent: "审计",
    back: "返回",
  },
  en: {
    library: "Library", compose: "Compose", campaigns: "Campaigns", settings: "Settings",
    scenarios: "Scenarios", cards: "Cards", worlds: "Worlds", rules: "Rule presets",
    use: "Use", entries: "entries", noResources: "No resources yet. Upload some.",
    warnings: "Scan warnings", uploadCard: "Upload card (PNG/JSON)", uploadWorld: "Upload world (JSON)",
    uploadScenario: "Upload scenario (YAML)", uploaded: "Uploaded", uploadFailed: "Upload failed",
    scenario: "Scenario (optional)", card: "Card (optional)", world: "World (optional)",
    ruleset: "Ruleset", customRules: "Custom rules (replaces preset when non-empty)",
    language: "Language", campaignId: "Campaign ID (empty = auto)", seed: "Seed (optional)",
    playerName: "Player name", playerDesc: "Player description", preview: "Preview",
    create: "Create & play", creating: "Creating…", noPreview: "Preview appears here.",
    opening: "Opening", scene: "Scene", player: "Player", actor: "Actor",
    facts: "World facts", storyFramework: "Story framework", effectiveRules: "Effective rules",
    diceNote: "v1 dice engine is fixed 2d6 (PbtA bands)",
    emptyCampaigns: "No campaigns yet. Compose one.", play: "Play", turnN: "Turn {n}",
    inputPlaceholder: "Describe your action…", send: "Send",
    thinking: "GM thinking…", gm: "GM", actorLabel: "Actor", playerLabel: "You",
    dice: "Roll", reasoning: "GM reasoning", gmView: "GM view", publicView: "Public view",
    hiddenFacts: "Hidden facts", events: "Event log", openingLabel: "Opening",
    error: "Turn failed", cached: "(cached result)", factsEmpty: "No public facts yet",
    storyPremise: "Premise", requiredBeats: "Required beats", optionalBeats: "Optional beats",
    forbidden: "Forbidden revelations", endings: "Possible endings", languageName: "English",
    save: "Save settings", saved: "Saved", keyStatus: "API key", keyReady: "Configured",
    keyMissing: "Missing (set in .env)", configPath: "Config path", dbPath: "Database",
    models: "Models", agents: "Agent settings", modelId: "Model ID", temperature: "Temperature",
    maxTokens: "Max output", retries: "Retries", gmAgent: "GM", actorAgent: "Actor", auditorAgent: "Auditor",
    back: "Back",
  },
};

let lang = localStorage.getItem("tari_lang") || "zh";
const t = (key, vars) => {
  let s = (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* keep */ }
    throw new Error(detail);
  }
  return res.json();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function renderApp() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = hash.split("?");
  const params = new URLSearchParams(query);
  const route = path.split("/").filter(Boolean);
  const app = document.getElementById("app");
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === (route[0] || "library"));
  });
  document.getElementById("lang-toggle").textContent = lang === "zh" ? "EN" : "中文";
  if (route[0] === "compose") renderCompose(app, params);
  else if (route[0] === "campaigns") renderCampaigns(app);
  else if (route[0] === "play") renderPlay(app, decodeURIComponent(route[1] || ""));
  else if (route[0] === "settings") renderSettings(app);
  else renderLibrary(app);
}

document.getElementById("lang-toggle").addEventListener("click", () => {
  lang = lang === "zh" ? "en" : "zh";
  localStorage.setItem("tari_lang", lang);
  renderApp();
});
window.addEventListener("hashchange", renderApp);
renderApp();

/* ---------------- Library ---------------- */

async function renderLibrary(app) {
  app.innerHTML = `<div class="page-title">${esc(t("library"))}</div><div class="empty">${esc(t("noResources"))}</div>`;
  let data;
  try { data = await api("/api/resources"); } catch (e) { app.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }

  const upload = (label, kind) => `
    <label class="upload">${esc(label)}
      <input type="file" data-upload="${kind}" accept="${kind === "cards" ? ".png,.json" : kind === "worlds" ? ".json" : ".yaml,.yml"}">
    </label>`;

  const section = (title, items, kind) => `
    <h2>${esc(title)}</h2>
    ${items.length ? `<div class="grid">${items.map((r) => `
      <div class="card">
        ${kind === "cards" && r.avatar ? `<img class="avatar" src="/api/resources/avatar?resource_id=${encodeURIComponent(r.id)}" alt="">` : ""}
        <h3>${esc(r.title)}</h3>
        <div class="desc">${esc(r.description || "")}</div>
        <div>${(r.tags || []).slice(0, 5).map((x) => `<span class="tag">${esc(x)}</span>`).join("")}
          ${r.locale ? `<span class="tag">${esc(r.locale)}</span>` : ""}
          ${kind === "worlds" ? `<span class="tag">${r.entry_count ?? 0} ${esc(t("entries"))}</span>` : ""}
        </div>
        <button data-use="${kind}" data-id="${esc(r.id)}">${esc(t("use"))}</button>
      </div>`).join("")}</div>` : `<div class="empty">${esc(t("noResources"))}</div>`}`;

  app.innerHTML = `
    <div class="page-title">${esc(t("library"))}</div>
    <div class="panel">
      ${upload(t("uploadCard"), "cards")}
      ${upload(t("uploadWorld"), "worlds")}
      ${upload(t("uploadScenario"), "scenarios")}
      <div id="upload-status"></div>
    </div>
    ${section(t("scenarios"), data.scenarios, "scenarios")}
    ${section(t("cards"), data.cards, "cards")}
    ${section(t("worlds"), data.worlds, "worlds")}
    <h2>${esc(t("rules"))}</h2>
    <div class="grid">${data.rulesets.map((r) => `
      <div class="card"><h3>${esc(r.id)}</h3><div class="desc">${esc(r.text.slice(0, 220))}…</div>
      <button data-use="ruleset" data-id="${esc(r.id)}">${esc(t("use"))}</button></div>`).join("")}
    </div>
    ${data.warnings && data.warnings.length ? `<div class="warn">${esc(t("warnings"))}:<br>${data.warnings.map(esc).join("<br>")}</div>` : ""}`;

  document.querySelectorAll("[data-upload]").forEach((input) => {
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      const status = document.getElementById("upload-status");
      try {
        const res = await fetch(`/api/resources/${input.dataset.upload}`, { method: "POST", body: fd });
        if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || res.statusText); }
        status.textContent = `${t("uploaded")}: ${res.title || res.id}`;
        renderApp();
      } catch (e) { status.textContent = `${t("uploadFailed")}: ${e.message}`; }
    });
  });

  document.querySelectorAll("[data-use]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.dataset.use;
      const id = btn.dataset.id;
      const param = kind === "cards" ? "card" : kind === "worlds" ? "world" : kind === "scenarios" ? "scenario" : "ruleset";
      location.hash = `#/compose?${param}=${encodeURIComponent(id)}`;
    });
  });
}

/* ---------------- Compose ---------------- */

let composeData = null;
const draft = {
  scenario_id: "", card_id: "", world_id: "", ruleset_id: "pbta-minimal",
  custom_rules: "", lang: "zh", campaign_id: "", seed: "", player_name: "", player_description: "",
};

async function renderCompose(app, params) {
  if (!composeData) {
    try { composeData = await api("/api/resources"); } catch (e) {
      app.innerHTML = `<div class="error">${esc(e.message)}</div>`; return;
    }
  }
  for (const [key, value] of params) {
    if (key in draft) draft[key] = value;
  }
  const options = (list, selected, placeholder) =>
    `<option value="">${esc(placeholder)}</option>` +
    list.map((r) => `<option value="${esc(r.id)}" ${r.id === selected ? "selected" : ""}>${esc(r.title)}</option>`).join("");

  app.innerHTML = `
    <div class="page-title">${esc(t("compose"))}</div>
    <div class="two-col">
      <div class="panel">
        <label>${esc(t("scenario"))}</label>
        <select id="c-scenario" style="width:100%">${options(composeData.scenarios, draft.scenario_id, "—")}</select>
        <label>${esc(t("card"))}</label>
        <select id="c-card" style="width:100%">${options(composeData.cards, draft.card_id, "—")}</select>
        <label>${esc(t("world"))}</label>
        <select id="c-world" style="width:100%">${options(composeData.worlds, draft.world_id, "—")}</select>
        <label>${esc(t("ruleset"))}</label>
        <select id="c-ruleset" style="width:100%">${composeData.rulesets.map((r) => `<option value="${esc(r.id)}" ${r.id === draft.ruleset_id ? "selected" : ""}>${esc(r.id)}</option>`).join("")}</select>
        <label>${esc(t("customRules"))}</label>
        <textarea id="c-custom">${esc(draft.custom_rules)}</textarea>
        <label>${esc(t("language"))}</label>
        <select id="c-lang"><option value="zh" ${draft.lang === "zh" ? "selected" : ""}>中文</option><option value="en" ${draft.lang === "en" ? "selected" : ""}>English</option></select>
        <div class="row">
          <div style="flex:1"><label>${esc(t("campaignId"))}</label><input id="c-id" style="width:100%" value="${esc(draft.campaign_id)}"></div>
          <div style="flex:1"><label>${esc(t("seed"))}</label><input id="c-seed" style="width:100%" value="${esc(draft.seed)}"></div>
        </div>
        <label>${esc(t("playerName"))}</label><input id="c-pname" style="width:100%" value="${esc(draft.player_name)}">
        <label>${esc(t("playerDesc"))}</label><textarea id="c-pdesc">${esc(draft.player_description)}</textarea>
        <div style="margin-top:14px"><button id="c-create" class="primary">${esc(t("create"))}</button></div>
        <div id="c-error" class="error"></div>
      </div>
      <div class="panel">
        <h2>${esc(t("preview"))}</h2>
        <div id="preview"><div class="empty">${esc(t("noPreview"))}</div></div>
      </div>
    </div>`;

  const bind = (id, key) => document.getElementById(id).addEventListener("change", (e) => { draft[key] = e.target.value; updatePreview(); });
  bind("c-scenario", "scenario_id");
  bind("c-card", "card_id");
  bind("c-world", "world_id");
  bind("c-ruleset", "ruleset_id");
  bind("c-lang", "lang");
  document.getElementById("c-custom").addEventListener("input", (e) => { draft.custom_rules = e.target.value; updatePreview(); });
  document.getElementById("c-id").addEventListener("input", (e) => { draft.campaign_id = e.target.value; });
  document.getElementById("c-seed").addEventListener("input", (e) => { draft.seed = e.target.value; });
  document.getElementById("c-pname").addEventListener("input", (e) => { draft.player_name = e.target.value; });
  document.getElementById("c-pdesc").addEventListener("input", (e) => { draft.player_description = e.target.value; });
  document.getElementById("c-create").addEventListener("click", createCampaign);
  updatePreview();
}

const updatePreview = debounce(async () => {
  const box = document.getElementById("preview");
  if (!box) return;
  const body = { ...draft, seed: draft.seed ? Number(draft.seed) : null };
  try {
    const p = await api("/api/resources/preview", { method: "POST", body: JSON.stringify(body) });
    box.innerHTML = `
      <h3>${esc(p.title)} <span class="tag">${esc(p.locale)}</span></h3>
      ${p.actor && p.actor.avatar_url ? `<img src="${esc(p.actor.avatar_url)}" style="width:100%;max-height:180px;object-fit:cover;border-radius:8px">` : ""}
      <label>${esc(t("opening"))}</label><div class="preview">${esc(p.opening)}</div>
      <label>${esc(t("scene"))}</label><div class="preview">${esc(p.scene.title)}${p.scene.location ? ` · ${esc(p.scene.location)}` : ""}</div>
      <label>${esc(t("actor"))}</label><div class="preview">${esc(p.actor.name)} — ${esc(p.actor.description.slice(0, 220))}…</div>
      <label>${esc(t("facts"))} (${p.scene.public_facts.length})</label>
      <ul>${p.scene.public_facts.slice(0, 8).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
      <label>${esc(t("storyFramework"))}</label><div class="preview">${esc(p.story_framework.premise)}</div>
      <label>${esc(t("effectiveRules"))}</label><div class="preview">${esc(p.rules.effective_text)}</div>
      <div class="muted">${esc(p.rules.dice_note)}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}, 350);

async function createCampaign() {
  const btn = document.getElementById("c-create");
  btn.disabled = true;
  btn.textContent = t("creating");
  try {
    const body = { ...draft, seed: draft.seed ? Number(draft.seed) : null };
    const res = await api("/api/campaigns", { method: "POST", body: JSON.stringify(body) });
    location.hash = `#/play/${encodeURIComponent(res.campaign_id)}`;
  } catch (e) {
    const box = document.getElementById("c-error");
    box.textContent = e.message;
    btn.disabled = false;
    btn.textContent = t("create");
  }
}

/* ---------------- Campaigns ---------------- */

async function renderCampaigns(app) {
  app.innerHTML = `<div class="page-title">${esc(t("campaigns"))}</div><div class="empty">${esc(t("emptyCampaigns"))}</div>`;
  let data;
  try { data = await api("/api/campaigns"); } catch (e) { app.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  if (!data.campaigns.length) return;
  app.innerHTML = `<div class="page-title">${esc(t("campaigns"))}</div><div class="grid">` +
    data.campaigns.map((c) => `
      <div class="card">
        <h3>${esc(c.title)}</h3>
        <div><span class="tag">${esc(c.campaign_id)}</span><span class="tag">${esc(c.locale)}</span></div>
        <div class="muted">${esc(t("turnN", { n: c.turn_number }))} · ${esc(c.status)}</div>
        <button data-play="${esc(c.campaign_id)}">${esc(t("play"))}</button>
      </div>`).join("") + `</div>`;
  document.querySelectorAll("[data-play]").forEach((b) => b.addEventListener("click", () => {
    location.hash = `#/play/${encodeURIComponent(b.dataset.play)}`;
  }));
}

/* ---------------- Play ---------------- */

let playState = null;
let playGM = false;
let playLocked = false;

async function renderPlay(app, id) {
  if (!id) { app.innerHTML = `<div class="empty">${esc(t("emptyCampaigns"))}</div>`; return; }
  app.innerHTML = `<div class="page-title">${esc(id)}</div><div class="empty">…</div>`;
  try { playState = await api(`/api/campaigns/${encodeURIComponent(id)}/state`); }
  catch (e) { app.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  playGM = false;
  playLocked = false;
  drawPlay(app, id);
}

function drawPlay(app, id) {
  const s = playState;
  app.innerHTML = `
    <div class="page-title">${esc(s.title)} <span class="tag">${esc(s.locale)}</span>
      <button id="gm-toggle" style="float:right">${esc(t("gmView"))}</button>
    </div>
    <div class="two-col">
      <div style="min-width:0">
        <div class="panel" id="opening-panel"><h2>${esc(t("openingLabel"))}</h2><div class="preview">${esc(s.opening)}</div></div>
        <div id="chat" class="chat"></div>
        <div id="stage" class="stage"></div>
        <div class="composer-input">
          <input id="chat-input" placeholder="${esc(t("inputPlaceholder"))}" autocomplete="off">
          <button id="chat-send" class="primary">${esc(t("send"))}</button>
        </div>
      </div>
      <div class="sidebar" id="sidebar">${publicSidebar(s)}</div>
    </div>`;

  (s.transcript || []).forEach((item) => {
    if (item.type === "player") addMessage("player", item.text);
    else if (item.type === "gm") addMessage("gm", item.text);
    else if (item.type === "actor") addMessage("actor", item.text);
    else if (item.type === "dice") {
      addDice({ rolls: item.rolls, total: item.total, outcome: item.outcome });
    }
  });

  const input = document.getElementById("chat-input");
  const send = () => {
    const text = input.value.trim();
    if (!text || playLocked) return;
    input.value = "";
    addMessage("player", text);
    playLocked = true;
    sendTurn(id, text);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  document.getElementById("chat-send").addEventListener("click", send);
  document.getElementById("gm-toggle").addEventListener("click", toggleGM(id));
}

function publicSidebar(s) {
  return `
    <section><h3>${esc(t("facts"))}</h3>
      ${s.scene.public_facts.length ? `<ul>${s.scene.public_facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : `<div class="muted">${esc(t("factsEmpty"))}</div>`}
    </section>
    <section><h3>${esc(t("actor"))}: ${esc(s.actor.name)}</h3>
      <div class="preview">${esc(s.actor.description.slice(0, 400))}</div>
      ${s.actor.goals.length ? `<ul>${s.actor.goals.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
    </section>`;
}

async function toggleGM(id) {
  playGM = !playGM;
  try {
    const gm = await api(`/api/campaigns/${encodeURIComponent(id)}/state?view=gm`);
    const btn = document.getElementById("gm-toggle");
    if (!btn) return;
    btn.textContent = t(playGM ? "publicView" : "gmView");
    const sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    if (playGM) {
      const extra = `
        <section><h3>${esc(t("hiddenFacts"))}</h3><ul>${gm.scene.hidden_facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></section>
        <section><h3>${esc(t("storyFramework"))}</h3>
          <div class="muted">${esc(t("storyPremise"))}: ${esc(gm.story_framework.premise)}</div>
          ${gm.story_framework.required_beats.length ? `<div class="muted">${esc(t("requiredBeats"))}:</div><ul>${gm.story_framework.required_beats.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
          ${gm.story_framework.forbidden_revelations.length ? `<div class="muted">${esc(t("forbidden"))}:</div><ul>${gm.story_framework.forbidden_revelations.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
        </section>
        <section><h3>${esc(t("events"))}</h3><div class="preview" style="font-size:12px">${esc(gm.events.slice(-25).map((e) => `#${e.seq} t${e.turn} ${e.type}`).join("\n"))}</div></section>`;
      sidebar.insertAdjacentHTML("beforeend", extra);
    } else {
      sidebar.innerHTML = publicSidebar(playState);
    }
  } catch (e) { /* keep current view */ }
}

function addMessage(kind, text) {
  const chat = document.getElementById("chat");
  if (!chat) return;
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  const who = { player: t("playerLabel"), gm: t("gm"), actor: t("actorLabel") }[kind] || kind;
  el.innerHTML = `<div class="who">${esc(who)}</div><div class="body"></div>`;
  el.querySelector(".body").textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function addDice(roll) {
  const chat = document.getElementById("chat");
  if (!chat) return;
  const el = document.createElement("div");
  el.className = "dice";
  el.textContent = `${t("dice")}: ${roll.rolls.join(" + ")} = ${roll.total} → ${roll.outcome}`;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}

async function sendTurn(id, text) {
  const stage = document.getElementById("stage");
  const input = document.getElementById("chat-input");
  stage.textContent = t("thinking");
  const requestId = crypto.randomUUID();
  console.debug("[TARI] turn request", { campaign: id, requestId, text });
  try {
    const res = await fetch(`/api/campaigns/${encodeURIComponent(id)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        player_input: text,
        request_id: requestId,
        stream: false,
      }),
    });
    console.debug("[TARI] turn response", {
      status: res.status,
      contentType: res.headers.get("content-type"),
    });
    const raw = await res.text();
    console.debug("[TARI] turn response body", raw.slice(0, 2000));
    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      throw new Error(
        `服务器返回了非 JSON 响应 (HTTP ${res.status}): ${raw.slice(0, 300)}`
      );
    }
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (data.roll) addDice(data.roll);
    if (data.gm_narration) addMessage("gm", data.gm_narration);
    if (data.actor_text) addMessage("actor", data.actor_text);
    if (data.gm_wrap_narration) addMessage("gm", data.gm_wrap_narration);
    stage.textContent = data.cached ? t("cached") : "";
  } catch (e) {
    console.error("[TARI] turn failed", e);
    stage.textContent = `${t("error")}: ${e.message}`;
  } finally {
    playLocked = false;
    if (input) input.disabled = false;
    if (stage && stage.textContent && !stage.textContent.startsWith(t("error"))) {
      setTimeout(() => { if (stage) stage.textContent = ""; }, 2500);
    }
    try {
      playState = await api(`/api/campaigns/${encodeURIComponent(id)}/state`);
      const sidebar = document.getElementById("sidebar");
      if (sidebar && !playGM) sidebar.innerHTML = publicSidebar(playState);
    } catch (_) { /* ignore */ }
  }
}

/* ---------------- Settings ---------------- */

async function renderSettings(app) {
  app.innerHTML = `<div class="page-title">${esc(t("settings"))}</div><div class="empty">…</div>`;
  let data;
  try { data = await api("/api/settings"); } catch (e) { app.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const agents = data.config.agents;
  const agentNames = { gm: t("gmAgent"), actor: t("actorAgent"), auditor: t("auditorAgent") };
  const fields = Object.entries(agents).map(([name, a]) => {
    const modelKey = a.model;
    const m = data.config.models[modelKey] || { model_id: "" };
    return `
      <section class="panel">
        <h3>${esc(agentNames[name] || name)}</h3>
        <label>${esc(t("modelId"))}</label>
        <input data-agent="${name}" data-field="model" value="${esc(m.model_id)}" style="width:100%">
        <div class="row">
          <div style="flex:1"><label>${esc(t("temperature"))}</label><input type="number" step="0.05" min="0" max="2" data-agent="${name}" data-field="temperature" value="${a.temperature}" style="width:100%"></div>
          <div style="flex:1"><label>${esc(t("maxTokens"))}</label><input type="number" min="1" data-agent="${name}" data-field="max_output_tokens" value="${a.max_output_tokens}" style="width:100%"></div>
          <div style="flex:1"><label>${esc(t("retries"))}</label><input type="number" min="0" max="5" data-agent="${name}" data-field="retries" value="${a.retries}" style="width:100%"></div>
        </div>
      </section>`;
  }).join("");
  app.innerHTML = `
    <div class="page-title">${esc(t("settings"))}</div>
    <div class="panel"><h3>${esc(t("keyStatus"))}</h3>
      <span class="${data.key_status.configured ? "ok" : "error"}">${esc(data.key_status.configured ? t("keyReady") : t("keyMissing"))}</span>
      <div class="muted">${esc(t("configPath"))}: ${esc(data.config_path)}<br>${esc(t("dbPath"))}: ${esc(data.db_path)}</div>
    </div>
    ${fields}
    <div style="margin-top:14px"><button id="settings-save" class="primary">${esc(t("save"))}</button> <span id="settings-status" class="ok"></span></div>`;

  document.getElementById("settings-save").addEventListener("click", async () => {
    const models = {};
    const agentsOut = {};
    for (const el of document.querySelectorAll("[data-agent]")) {
      const name = el.dataset.agent;
      const field = el.dataset.field;
      if (field === "model") {
        const modelKey = data.config.agents[name].model;
        models[modelKey] = { model_id: el.value.trim() };
      } else {
        agentsOut[name] = agentsOut[name] || { ...data.config.agents[name] };
        agentsOut[name][field] = field === "temperature" ? Number(el.value) : Number(el.value);
      }
    }
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ models, agents: agentsOut }) });
      document.getElementById("settings-status").textContent = t("saved");
    } catch (e) {
      document.getElementById("settings-status").textContent = e.message;
    }
  });
}
