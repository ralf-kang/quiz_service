// === storage keys ===
const LS_USER    = "quiz_user_id";
const LS_TYPES   = "quiz_known_user_ids";
const LS_STYLES  = "quiz_known_styles";
const LS_OPTS    = "quiz_gen_options";   // 직전 출제 옵션 (n, mcq, short, selectedStyles, extra)
const LS_TIMING  = "quiz_gen_timing";    // 직전 출제 소요시간 추정 학습 (지수이동평균)

// 기본 시간 가정 (모드별, 문항당 초). 실측이 누적되면 이걸로 갱신.
const GEN_TIMING_DEFAULT = {
  base_sec: 8,             // 그라운드 (네트워크/JSON 검증 등 고정 비용)
  per_question_sec: {
    "cli":    18,          // claude CLI 패스스루 (느림)
    "api":    7,           // Anthropic API 직접
    "기출":   28,          // 기출 스타일은 지문 생성 때문에 더 김 (보너스)
  },
};

// 난이도별 시간 가산 (1500자/2200자 지문 생성 + 재시도 가능성 고려)
const DIFFICULTY_MULTIPLIER = {
  "하":   0.9,
  "중":   1.0,
  "상":   2.2,
  "최상": 3.0,
};

function loadTiming() {
  try {
    const t = JSON.parse(localStorage.getItem(LS_TIMING) || "null");
    if (t && typeof t === "object") return Object.assign({}, GEN_TIMING_DEFAULT, t);
  } catch (_) {}
  return { ...GEN_TIMING_DEFAULT };
}
function saveTimingSample(mode, n, isPastexam, elapsedSec) {
  // 이동평균: new = 0.6*old + 0.4*sample
  const t = loadTiming();
  const perQ = Math.max(1, (elapsedSec - t.base_sec) / Math.max(1, n));
  const key = isPastexam ? "기출" : mode;
  const old = t.per_question_sec[key] ?? GEN_TIMING_DEFAULT.per_question_sec[key] ?? 10;
  t.per_question_sec[key] = Math.round((old * 0.6 + perQ * 0.4) * 10) / 10;
  localStorage.setItem(LS_TIMING, JSON.stringify(t));
}
function estimateGenSec(n, isPastexam, difficulty) {
  const t = loadTiming();
  const cliMode = state.healthMeta?.loopback_debug && state.healthMeta?.claude_cli_present && !state.authConfigured;
  const mode = cliMode ? "cli" : "api";
  const perQ = isPastexam
    ? (t.per_question_sec["기출"] ?? GEN_TIMING_DEFAULT.per_question_sec["기출"])
    : (t.per_question_sec[mode] ?? GEN_TIMING_DEFAULT.per_question_sec[mode]);
  const mult = DIFFICULTY_MULTIPLIER[difficulty] ?? 1.0;
  return { sec: Math.round((t.base_sec + perQ * n) * mult), mode, isPastexam, difficulty };
}

// === 카운트다운 — 클릭한 버튼 자체에 인라인 표시 ===
let _genTimer = null;
function startGenProgress(buttonEl, baseLabel, etaSec) {
  if (!buttonEl) return null;
  buttonEl.classList.add("is-counting");
  buttonEl.classList.remove("overrun");
  buttonEl.style.setProperty("--gen-pct", "0%");
  buttonEl._origLabel = buttonEl.textContent;
  const render = (remain, overrunSec) => {
    if (overrunSec > 0) {
      buttonEl.classList.add("overrun");
      buttonEl.innerHTML =
        `<span class="label-inline"><span class="mini-spinner"></span> ${escapeHTML(baseLabel)} · 예상 +${overrunSec}초 초과</span>`;
    } else {
      buttonEl.innerHTML =
        `<span class="label-inline"><span class="mini-spinner"></span> ${escapeHTML(baseLabel)} · ${remain}초 남음</span>`;
    }
  };
  render(etaSec, 0);

  const t0 = Date.now();
  if (_genTimer) clearInterval(_genTimer);
  _genTimer = setInterval(() => {
    const elapsed = (Date.now() - t0) / 1000;
    const remain  = etaSec - elapsed;
    const pct = Math.max(0, Math.min(100, (elapsed / etaSec) * 100));
    buttonEl.style.setProperty("--gen-pct", pct + "%");
    if (remain > 0) render(Math.ceil(remain), 0);
    else {
      buttonEl.style.setProperty("--gen-pct", "100%");
      render(0, Math.ceil(-remain));
    }
  }, 300);
  return { t0, buttonEl };
}
function stopGenProgress(handle, mode, n, isPastexam, success) {
  if (_genTimer) { clearInterval(_genTimer); _genTimer = null; }
  if (handle && handle.buttonEl) {
    const b = handle.buttonEl;
    b.classList.remove("is-counting", "overrun");
    b.style.removeProperty("--gen-pct");
    if (b._origLabel != null) {
      b.textContent = b._origLabel;
      b._origLabel = null;
    }
  }
  if (success && handle && handle.t0) {
    const elapsed = (Date.now() - handle.t0) / 1000;
    saveTimingSample(mode, n, isPastexam, elapsed);
  }
}

function loadGenOptions() {
  try {
    const o = JSON.parse(localStorage.getItem(LS_OPTS) || "null");
    if (!o || typeof o !== "object") return null;
    return o;
  } catch (_) { return null; }
}
function saveGenOptions() {
  const opts = {
    n:     parseInt(document.getElementById("n")?.value, 10) || 5,
    mcq:   parseInt(document.getElementById("mcq")?.value, 10) || 0,
    short: parseInt(document.getElementById("short")?.value, 10) || 0,
    difficulty: document.getElementById("difficulty")?.value || "중",
    styles: Array.from(state.selectedStyles),
    extra: document.getElementById("extra")?.value || "",
  };
  localStorage.setItem(LS_OPTS, JSON.stringify(opts));
}

// 기본 스타일 프리셋 — 사용자 정의 추가시 누적되어 다음에도 노출
const DEFAULT_STYLES = [
  { value: "기출",       label: "기출" },
  { value: "암기",       label: "암기" },
  { value: "계산",       label: "계산" },
  { value: "사례형",     label: "사례형" },
  { value: "약술형",     label: "약술형" },
  { value: "빈칸채우기", label: "빈칸채우기" },
  { value: "비교형",     label: "비교형" },
];

function loadKnownUsers() {
  try {
    const arr = JSON.parse(localStorage.getItem(LS_TYPES) || "[]");
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch (_) { return []; }
}
function rememberUser(uid) {
  if (!uid) return;
  const list = loadKnownUsers().filter((x) => x !== uid);
  list.unshift(uid);
  localStorage.setItem(LS_TYPES, JSON.stringify(list.slice(0, 50)));
}

function loadKnownStyles() {
  try {
    const arr = JSON.parse(localStorage.getItem(LS_STYLES) || "[]");
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch (_) { return []; }
}
function rememberStyle(s) {
  if (!s) return;
  const list = loadKnownStyles().filter((x) => x !== s);
  list.unshift(s);
  localStorage.setItem(LS_STYLES, JSON.stringify(list.slice(0, 30)));
}

// === state ===
const state = {
  userId: localStorage.getItem(LS_USER) || "",
  loggedIn: false,
  testType: null,           // 선택된 테스트 타입
  documentId: null,         // 출제 대상 문서
  sessionId: null,
  questions: [],
  answered: new Set(),
  authConfigured: false,
  knownUsers: loadKnownUsers(),
  knownTestTypes: [],       // 서버에서 받아온 현재 사용자의 test type 목록
  tree: [],
  treeOpen: { tt: new Set(), doc: new Set() },
  selectedStyles: new Set(["기출"]),    // 다중 선택된 스타일
  customStyles: loadKnownStyles(),       // 사용자 정의 스타일 (LocalStorage 누적)
  selectedDocIds: new Set(),             // 출제 범위에 포함된 문서 id (체크된 것)
  // === redesigned UI state ===
  historyGroupOpen: new Set(),           // 펼쳐진 test_type 그룹
  historyFilter: "",                     // 검색 필터
  wrongSort: "recent",                   // "recent" | "mastery"
  wrongCache: { items: [], topics: [] },
  scopeCache: null,                      // 마지막 scope-dashboard 응답 (커버리지 위젯용)
};

// 직전 출제 옵션 복원 (페이지 로드 / 테스트 타입 전환 무관 — 항상 유지)
(() => {
  const o = loadGenOptions();
  if (!o) return;
  if (Array.isArray(o.styles) && o.styles.length) state.selectedStyles = new Set(o.styles);
})();

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  return fetch(path, init).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = (typeof data.detail === "object" ? data.detail.message : data.detail) || `HTTP ${r.status}`;
      const err = new Error(msg);
      err.status = r.status;
      err.payload = data;
      throw err;
    }
    return data;
  });
}

function stars(level) {
  const n = Math.max(0, Math.min(5, level | 0));
  return "★".repeat(n) + "☆".repeat(5 - n);
}

// === init ===
window.addEventListener("DOMContentLoaded", async () => {
  $("#user-id").value = state.userId;
  refreshUserOptions();

  $("#login-btn").addEventListener("click", login);
  $("#logout-btn").addEventListener("click", logout);
  $("#test-type-select").addEventListener("change", onTestTypeSelectChange);
  $("#select-test-type").addEventListener("click", selectTestType);
  $("#upload-btn").addEventListener("click", uploadFiles);
  $("#doc-check-all")?.addEventListener("click", () => setAllDocsChecked(true));
  $("#doc-uncheck-all")?.addEventListener("click", () => setAllDocsChecked(false));
  $("#generate-btn").addEventListener("click", generate);
  $("#style-custom").addEventListener("keydown", onStyleCustomKeydown);
  $("#style-custom-saved")?.addEventListener("change", onStyleCustomSavedChange);
  refreshCustomStyleSelect();

  // 직전 옵션 값 복원
  const savedOpts = loadGenOptions();
  if (savedOpts) {
    if (Number.isFinite(savedOpts.n))     $("#n").value     = savedOpts.n;
    if (Number.isFinite(savedOpts.mcq))   $("#mcq").value   = savedOpts.mcq;
    if (Number.isFinite(savedOpts.short)) $("#short").value = savedOpts.short;
    if (typeof savedOpts.difficulty === "string" && $("#difficulty"))
      $("#difficulty").value = savedOpts.difficulty;
    if (typeof savedOpts.extra === "string") $("#extra").value = savedOpts.extra;
  }
  // 사용자 변경 시 즉시 저장
  ["n", "mcq", "short", "difficulty", "extra"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", saveGenOptions);
    document.getElementById(id)?.addEventListener("input", saveGenOptions);
  });

  renderStyleChips();
  $("#refresh-tree").addEventListener("click", refreshAll);
  $("#refresh-dashboard").addEventListener("click", refreshDashboard);
  $("#refresh-scope")?.addEventListener("click", () => refreshScopeDashboard(true));
  $("#practice-btn").addEventListener("click", practice);
  $("#reset-user").addEventListener("click", resetUser);
  // 히스토리 검색
  $("#history-search")?.addEventListener("input", (ev) => {
    state.historyFilter = ev.target.value.trim().toLowerCase();
    renderHistoryPanel();
  });
  // 약점 노트 정렬 토글
  $$(".wrong-toolbar .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.wrongSort = btn.dataset.sort || "recent";
      $$(".wrong-toolbar .seg-btn").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderWrongNotes();
    });
  });

  $("#claude-login").addEventListener("click", onClaudeLoginClick);
  $("#auth-auto").addEventListener("click", tryAutoLogin);
  $("#auth-submit").addEventListener("click", submitAuth);
  $("#auth-logout").addEventListener("click", logoutAuth);
  $$("#auth-modal [data-close]").forEach((el) => el.addEventListener("click", closeAuthModal));

  // 모바일 사이드바 드로어
  setupMobileNav();

  await refreshAuthStatus();
  // 로그인 상태 복원은 명시 [로그인] 버튼으로 (자동 로그인하지 않음)
});

// === 모바일 사이드바 (드로어) ===
function toggleMobileNav(force) {
  const open = typeof force === "boolean" ? force : !document.body.classList.contains("nav-open");
  document.body.classList.toggle("nav-open", open);
  const bd = document.getElementById("sidebar-backdrop");
  if (bd) bd.hidden = !open;
  const btn = document.getElementById("nav-toggle");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
}

function setupMobileNav() {
  const btn = document.getElementById("nav-toggle");
  const bd = document.getElementById("sidebar-backdrop");
  if (!btn || !bd) return;
  btn.addEventListener("click", () => toggleMobileNav());
  bd.addEventListener("click", () => toggleMobileNav(false));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && document.body.classList.contains("nav-open")) {
      toggleMobileNav(false);
    }
  });
  // 데스크톱 폭으로 회복되면 드로어 상태 자동 정리
  if (typeof window.matchMedia === "function") {
    const mq = window.matchMedia("(min-width: 769px)");
    const onChange = (e) => { if (e.matches) toggleMobileNav(false); };
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
    else if (typeof mq.addListener === "function") mq.addListener(onChange);
  }
}

// === user login ===
function login() {
  const uid = $("#user-id").value.trim();
  if (!uid) { alert("사용자 ID 를 입력하세요."); return; }
  state.userId = uid;
  state.loggedIn = true;
  localStorage.setItem(LS_USER, uid);
  rememberUser(uid);
  state.knownUsers = loadKnownUsers();
  refreshUserOptions();
  renderLoginState();
  // 게이트 1 활성화 — 테스트 타입은 사용자 선택 후 결정
  setStepEnabled(1, true);
  refreshAll();
}

function logout() {
  state.loggedIn = false;
  state.testType = null;
  state.documentId = null;
  state.sessionId = null;
  state.questions = [];
  state.tree = [];
  state.wrongCache = { items: [], topics: [] };
  state.scopeCache = null;
  $("#history-recent").innerHTML = "";
  $("#history-groups").innerHTML = "";
  $("#dashboard-hint").textContent = "로그인 후 학습 현황이 표시됩니다.";
  resetWidgets();
  $("#wrong-list").innerHTML = "";
  $("#wrong-summary").innerHTML = "";
  $("#topics").innerHTML = "";
  $("#questions").innerHTML = "";
  $("#board-empty").style.display = "block";
  $("#session-banner").hidden = true;
  $("#active-test-type").hidden = true;
  setStepEnabled(1, false);
  setStepEnabled(2, false);
  setStepEnabled(3, false);
  $("#reset-user").disabled = true;
  $("#practice-btn").disabled = true;
  renderLoginState();
  // 로그아웃 후 다시 빠른 선택 칩이 보이도록
  renderKnownUsersQuick(state.knownUsers);
}

function renderLoginState() {
  const el = $("#login-state");
  if (state.loggedIn) {
    el.textContent = `로그인됨: ${state.userId}`;
    el.classList.add("connected");
    $("#login-btn").hidden = true;
    $("#logout-btn").hidden = false;
    $("#user-id").disabled = true;
    $("#reset-user").disabled = false;
  } else {
    el.textContent = "미로그인";
    el.classList.remove("connected");
    $("#login-btn").hidden = false;
    $("#logout-btn").hidden = true;
    $("#user-id").disabled = false;
    $("#reset-user").disabled = true;
  }
}

function setStepEnabled(n, enabled) {
  const sec = $(`#gate-${n}`);
  if (!sec) return;
  sec.classList.toggle("disabled", !enabled);
  sec.querySelectorAll("input, button, select, textarea").forEach((el) => {
    if (el.type === "hidden") return;          // hidden #style 등 보호
    el.disabled = !enabled;
  });
}

async function refreshUserOptions() {
  const local = loadKnownUsers();
  let server = [];
  try { server = (await api("/api/users")).map((r) => r.id); } catch (_) {}
  const merged = Array.from(new Set([...local, ...server]));
  state.knownUsers = merged;
  const dl = $("#user-id-options");
  dl.innerHTML = "";
  merged.forEach((t) => {
    const o = document.createElement("option");
    o.value = t;
    dl.appendChild(o);
  });
  renderKnownUsersQuick(merged);
}

// 헤더 #user-id 아래 "기존 ID" 빠른 선택 칩 렌더.
// list: string[] (서버+로컬 merge 결과)
function renderKnownUsersQuick(list) {
  const box = document.getElementById("known-users-quick");
  if (!box) return;
  // 로그인 상태이거나 비어 있으면 숨김
  if (state.loggedIn || !Array.isArray(list) || list.length === 0) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = "";

  const label = document.createElement("span");
  label.className = "quick-users-label";
  label.textContent = "기존 ID:";
  box.appendChild(label);

  list.forEach((uid) => {
    if (typeof uid !== "string" || !uid) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip-user";
    btn.dataset.uid = uid;
    btn.textContent = uid;
    btn.addEventListener("click", () => {
      const input = document.getElementById("user-id");
      if (input) input.value = uid;
      // 즉시 로그인 (login() 가 #user-id 값을 읽음)
      try { login(); } catch (e) { console.warn("quick login failed", e); }
      // 로그인 후 칩 영역 숨김 (login() 도 결국 renderKnownUsersQuick 을 호출하지만 보수적으로 즉시 처리)
      box.hidden = true;
    });
    box.appendChild(btn);
  });
}

// === test type 콤보박스 ===
async function refreshTestTypeOptions() {
  if (!state.loggedIn) return;
  try {
    const rows = await api(`/api/quiz/test-types?user_id=${encodeURIComponent(state.userId)}`);
    state.knownTestTypes = rows.map((r) => ({
      name: r.test_type,
      docs: r.document_count,
      last: r.last_uploaded_at,
    }));
  } catch (_) {
    state.knownTestTypes = [];
  }
  renderTestTypeSelect();
}

function renderTestTypeSelect() {
  const sel = $("#test-type-select");
  const cur = state.testType;
  sel.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.disabled = true;
  placeholder.textContent = "— 선택하세요 —";
  if (!cur) placeholder.selected = true;
  sel.appendChild(placeholder);

  if (state.knownTestTypes.length > 0) {
    const grp = document.createElement("optgroup");
    grp.label = "📂 과거 테스트 타입";
    state.knownTestTypes.forEach((tt) => {
      const o = document.createElement("option");
      o.value = tt.name;
      const when = tt.last ? tt.last.replace("T", " ").slice(0, 10) : "";
      o.textContent = `${tt.name}  (${tt.docs}문서${when ? ", " + when : ""})`;
      if (cur === tt.name) o.selected = true;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }

  const newOpt = document.createElement("option");
  newOpt.value = "__new__";
  newOpt.textContent = "＋ 새 테스트 타입 만들기…";
  sel.appendChild(newOpt);
}

function onTestTypeSelectChange(ev) {
  const v = ev.target.value;
  if (v === "__new__") {
    $("#test-type-new-row").hidden = false;
    $("#test-type-new").focus();
  } else {
    $("#test-type-new-row").hidden = true;
  }
}

function selectTestType() {
  if (!state.loggedIn) { alert("먼저 로그인하세요."); return; }
  let tt;
  const selVal = $("#test-type-select").value;
  if (selVal === "__new__") {
    tt = $("#test-type-new").value.trim();
    if (!tt) { alert("새 테스트 타입 이름을 입력하세요."); return; }
  } else {
    tt = (selVal || "").trim();
    if (!tt) { alert("테스트 타입을 선택하세요."); return; }
  }
  state.testType = tt;
  $("#active-test-type").hidden = false;
  $("#active-test-type").innerHTML = `🎯 활성 테스트 타입: <strong>${escapeHTML(tt)}</strong>`;
  $("#test-type-new-row").hidden = true;
  $("#test-type-new").value = "";
  setStepEnabled(2, true);
  populateActiveDoc();
  refreshTestTypeOptions();
  refreshScopeDashboard(false);
}

function populateActiveDoc() {
  // 출제 범위 체크리스트 렌더 + 디폴트 전체 체크
  const node = state.tree.find((n) => n.test_type === state.testType);
  const docs = node ? node.documents : [];
  const root = $("#doc-checklist");
  root.innerHTML = "";

  if (docs.length === 0) {
    root.innerHTML = `<div class="hint" style="font-size:11px;color:#9ca3af">— 업로드 후 표시됩니다 —</div>`;
    state.selectedDocIds = new Set();
    state.documentId = null;
    setStepEnabled(3, false);
    $("#doc-check-all").disabled = true;
    $("#doc-uncheck-all").disabled = true;
    updateDocChecklistSummary(0, 0);
    return;
  }

  // 새로 등장한 문서는 자동 체크 (사용자가 명시적으로 해제하지 않은 한)
  const known = state.selectedDocIds;
  const newSet = new Set();
  docs.forEach((d) => {
    if (known.size === 0 || known.has(d.id)) newSet.add(d.id);
    // 새 문서 (이전 known 에 없던) 는 디폴트 체크
    else if (!known.has(d.id) && !state._knownDocIds?.has?.(d.id)) newSet.add(d.id);
  });
  state.selectedDocIds = newSet;
  state._knownDocIds = new Set(docs.map((d) => d.id));

  docs.forEach((d) => {
    const row = document.createElement("label");
    row.className = "doc-row" + (state.selectedDocIds.has(d.id) ? "" : " unchecked");
    const when = d.uploaded_at ? d.uploaded_at.replace("T", " ").slice(5, 16) : "";
    row.innerHTML = `
      <input type="checkbox" data-id="${d.id}" ${state.selectedDocIds.has(d.id) ? "checked" : ""} />
      <span class="doc-name">📄 ${escapeHTML(d.filename)}</span>
      <span class="doc-meta">${d.sessions.length}회차 · ${escapeHTML(when)}</span>
    `;
    row.querySelector("input").addEventListener("change", (ev) => {
      const id = parseInt(ev.target.dataset.id, 10);
      if (ev.target.checked) state.selectedDocIds.add(id);
      else state.selectedDocIds.delete(id);
      row.classList.toggle("unchecked", !ev.target.checked);
      onSelectedDocsChanged();
    });
    root.appendChild(row);
  });

  $("#doc-check-all").disabled = false;
  $("#doc-uncheck-all").disabled = false;
  state.documentId = state.selectedDocIds.size ? Array.from(state.selectedDocIds)[0] : null;
  setStepEnabled(3, state.selectedDocIds.size > 0);
  updateDocChecklistSummary(state.selectedDocIds.size, docs.length);
  $("#practice-btn").disabled = false;
}

function setAllDocsChecked(checked) {
  $$("#doc-checklist input[type='checkbox']").forEach((cb) => {
    const id = parseInt(cb.dataset.id, 10);
    cb.checked = checked;
    if (checked) state.selectedDocIds.add(id);
    else state.selectedDocIds.delete(id);
    cb.closest(".doc-row")?.classList.toggle("unchecked", !checked);
  });
  onSelectedDocsChanged();
}

function onSelectedDocsChanged() {
  const total = $$("#doc-checklist input[type='checkbox']").length;
  updateDocChecklistSummary(state.selectedDocIds.size, total);
  state.documentId = state.selectedDocIds.size ? Array.from(state.selectedDocIds)[0] : null;
  setStepEnabled(3, state.selectedDocIds.size > 0);
}

function updateDocChecklistSummary(checked, total) {
  const el = $("#doc-checklist-summary");
  if (!el) return;
  el.textContent = total ? `${checked}/${total} 포함됨` : "";
}

// === 스타일 칩 (다중 선택) ===
function allStyleOptions() {
  const seen = new Set();
  const out = [];
  for (const s of DEFAULT_STYLES) {
    if (!seen.has(s.value)) { seen.add(s.value); out.push({ ...s, custom: false }); }
  }
  for (const v of state.customStyles) {
    if (!seen.has(v)) { seen.add(v); out.push({ value: v, label: v, custom: true }); }
  }
  return out;
}

function renderStyleChips() {
  const root = $("#style-chips");
  if (!root) return;
  root.innerHTML = "";
  allStyleOptions().forEach((s) => {
    const chip = document.createElement("span");
    chip.className = "style-chip" + (s.custom ? " custom" : "")
                    + (state.selectedStyles.has(s.value) ? " active" : "");
    chip.dataset.value = s.value;
    chip.innerHTML = escapeHTML(s.label) +
      (s.custom ? ` <span class="x" title="제거">×</span>` : "");
    chip.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("x")) {
        state.customStyles = state.customStyles.filter((v) => v !== s.value);
        state.selectedStyles.delete(s.value);
        localStorage.setItem(LS_STYLES, JSON.stringify(state.customStyles));
        renderStyleChips();
        refreshCustomStyleSelect();
        return;
      }
      if (state.selectedStyles.has(s.value)) state.selectedStyles.delete(s.value);
      else state.selectedStyles.add(s.value);
      // 최소 1개는 유지
      if (state.selectedStyles.size === 0) state.selectedStyles.add("기출");
      syncStyleHidden();
      renderStyleChips();
    });
    root.appendChild(chip);
  });
  syncStyleHidden();
}

function syncStyleHidden() {
  const arr = Array.from(state.selectedStyles);
  $("#style").value = arr.join(", ");
  saveGenOptions();
}

function onStyleCustomKeydown(ev) {
  if (ev.key !== "Enter") return;
  ev.preventDefault();
  const v = ev.target.value.trim();
  if (!v) return;
  rememberStyle(v);
  state.customStyles = loadKnownStyles();
  state.selectedStyles.add(v);
  ev.target.value = "";
  renderStyleChips();
  refreshCustomStyleSelect();
}

function onStyleCustomSavedChange(ev) {
  const v = ev.target.value;
  if (!v) return;
  state.selectedStyles.add(v);
  // 다시 선택 가능하도록 placeholder 로 복귀
  ev.target.value = "";
  renderStyleChips();
}

function refreshCustomStyleSelect() {
  const sel = $("#style-custom-saved");
  if (!sel) return;
  // 기존 옵션 비우고 placeholder + 저장된 사용자 정의 스타일들 추가
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.disabled = true;
  ph.selected = true;
  ph.textContent = state.customStyles.length
    ? "＋ 저장된 사용자 정의 스타일…"
    : "(저장된 사용자 정의 없음 — 우측에 입력 후 Enter)";
  sel.appendChild(ph);
  state.customStyles.forEach((s) => {
    const o = document.createElement("option");
    o.value = s;
    o.textContent = s;
    sel.appendChild(o);
  });
}

// === auth (Claude) ===
async function refreshAuthStatus() {
  try {
    const s = await api("/api/auth/claude");
    state.authConfigured = !!s.configured;
    renderAuthBadge(s);
    const h = await api("/health");
    state.healthMeta = h;
    $("#llm-status").textContent = h.llm_configured
      ? "LLM 연결됨"
      : "LLM 미연결 — [Claude 로그인] 필요";
  } catch (_) {
    $("#llm-status").textContent = "서버 연결 실패";
  }
}

function renderAuthBadge(s) {
  const btn = $("#claude-login");
  const label = $("#claude-label");
  if (s.configured) {
    btn.classList.add("connected");
    const kind = s.token_kind === "oauth" ? "OAuth" :
                 s.token_kind === "api-key" ? "API Key" : "Token";
    const plan = s.plan_aware ? " · 플랜 토큰" : "";
    label.textContent = `Claude 연결됨 (${kind}${plan})`;
    $("#auth-logout").hidden = false;
  } else {
    btn.classList.remove("connected");
    label.textContent = "Claude 로그인";
    $("#auth-logout").hidden = true;
  }
}

function openAuthModal() {
  $("#auth-error").hidden = true;
  $("#auth-error").textContent = "";
  $("#api-key").value = "";
  $("#auth-modal").hidden = false;
  setTimeout(() => $("#api-key").focus(), 0);
}
function closeAuthModal() { $("#auth-modal").hidden = true; }

async function onClaudeLoginClick() {
  if (state.authConfigured) { openAuthModal(); return; }
  try {
    const s = await api("/api/auth/claude/login", { method: "POST" });
    state.authConfigured = !!s.configured;
    renderAuthBadge(s);
    if (!s.configured) { openAuthModal(); showAuthError("자동 로그인 실패. 토큰을 직접 입력해 주세요."); }
  } catch (e) {
    openAuthModal();
    showAuthError("자동 로그인 실패: " + e.message);
  }
}
async function tryAutoLogin() {
  try {
    const s = await api("/api/auth/claude/login", { method: "POST" });
    state.authConfigured = !!s.configured;
    renderAuthBadge(s);
    if (s.configured) closeAuthModal();
    else showAuthError("자동 로그인 실패 — 토큰을 직접 입력해 주세요.");
  } catch (e) { showAuthError(e.message); }
}
async function submitAuth() {
  const key = $("#api-key").value.trim();
  if (!key) { showAuthError("토큰을 입력하세요."); return; }
  try {
    const s = await api("/api/auth/claude", { method: "POST", body: JSON.stringify({ api_key: key }) });
    state.authConfigured = !!s.configured;
    renderAuthBadge(s);
    closeAuthModal();
  } catch (e) { showAuthError(e.message); }
}
async function logoutAuth() {
  try {
    const s = await api("/api/auth/claude", { method: "DELETE" });
    state.authConfigured = !!s.configured;
    renderAuthBadge(s);
  } catch (e) { showAuthError(e.message); }
}
function showAuthError(msg) {
  const el = $("#auth-error");
  el.textContent = msg;
  el.hidden = false;
}

// === upload (다중 파일) ===
async function uploadFiles() {
  if (!state.loggedIn) { alert("먼저 로그인하세요."); return; }
  if (!state.testType) { alert("먼저 테스트 타입을 선택하세요."); return; }
  const files = Array.from($("#file").files || []);
  if (!files.length) { alert("파일을 1개 이상 선택하세요."); return; }

  const btn = $("#upload-btn");
  const prog = $("#upload-progress");
  btn.disabled = true;
  prog.hidden = false;

  const successes = [];
  const skipped = [];
  const failures = [];
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    prog.textContent = `📤 ${i + 1}/${files.length} 업로드 중… (${f.name})`;
    btn.textContent = `업로드 중 (${i + 1}/${files.length})…`;
    const fd = new FormData();
    fd.append("user_id", state.userId);
    fd.append("test_type", state.testType);
    fd.append("file", f);
    try {
      const data = await fetch("/api/upload", { method: "POST", body: fd }).then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { const e = new Error("upload failed"); e.status = r.status; e.payload = d; throw e; }
        return d;
      });
      successes.push(data.filename);
      // 새 문서는 디폴트 체크
      state.selectedDocIds.add(data.document_id);
    } catch (e) {
      if (e.status === 409 && e.payload && typeof e.payload.detail === "object") {
        const d = e.payload.detail;
        skipped.push(`${f.name} (이미 존재 — ${d.existing_filename})`);
        // 이미 존재하는 문서도 체크리스트에 포함
        if (d.existing_document_id) state.selectedDocIds.add(d.existing_document_id);
      } else {
        failures.push(`${f.name}: ${e.message || "알 수 없음"}`);
      }
    }
  }

  prog.hidden = true;
  prog.textContent = "";
  btn.disabled = false;
  btn.textContent = "업로드";
  $("#file").value = "";

  const lines = [];
  if (successes.length) lines.push(`✅ 성공 ${successes.length}개:\n  ${successes.join("\n  ")}`);
  if (skipped.length)   lines.push(`⏭ 중복 스킵 ${skipped.length}개:\n  ${skipped.join("\n  ")}`);
  if (failures.length)  lines.push(`❌ 실패 ${failures.length}개:\n  ${failures.join("\n  ")}`);
  if (lines.length) alert(lines.join("\n\n"));

  await refreshAll();
}

// === generate ===
async function generate() {
  if (!state.loggedIn) { alert("먼저 로그인하세요."); return; }
  if (!state.testType) { alert("먼저 테스트 타입을 선택하세요."); return; }
  const docIds = Array.from(state.selectedDocIds || []);
  if (!docIds.length) { alert("출제 범위에 포함할 강의노트를 1개 이상 체크하세요."); return; }
  const styleStr = Array.from(state.selectedStyles).join(", ") || "기출";
  const n = parseInt($("#n").value, 10) || 5;
  const isPastexam = /(^|,\s*)기출(\s*,|$)/.test(styleStr);
  const difficulty = $("#difficulty")?.value || "중";
  const extraWithDifficulty = `난이도: ${difficulty}` +
    ($("#extra").value ? `\n${$("#extra").value}` : "");
  const payload = {
    user_id: state.userId,
    document_ids: docIds,
    n: n,
    type_mix: { mcq: parseInt($("#mcq").value, 10) || 0, short: parseInt($("#short").value, 10) || 0 },
    style: styleStr,
    extra_instructions: extraWithDifficulty,
  };

  const est = estimateGenSec(n, isPastexam, difficulty);
  const diffNote = difficulty === "상" ? " · 50문장+ 지문" :
                   difficulty === "최상" ? " · 70문장+ 지문 + 다단계 추론" : "";
  const docNote = docIds.length > 1 ? ` · 노트 ${docIds.length}권` : "";
  const handle = startGenProgress(
    $("#generate-btn"),
    `출제 중 (${n}문항 · ${difficulty}${diffNote}${docNote})`,
    est.sec
  );
  let ok = false;
  try {
    const data = await api("/api/quiz/generate", { method: "POST", body: JSON.stringify(payload) });
    state.sessionId = data.session_id || null;
    state.answered = new Set();
    state.questions = data.questions;
    renderSessionBanner(false);
    renderQuestions(data.questions);
    refreshAll();
    ok = true;
  } catch (e) {
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("출제 실패: " + e.message);
  } finally {
    stopGenProgress(handle, est.mode, n, isPastexam, ok);
  }
}

function renderSessionBanner(historyMode) {
  const banner = $("#session-banner");
  if (!state.sessionId) { banner.hidden = true; return; }
  const sess = state._lastSessionSummary;
  const title = sess?.title || `회차 #${state.sessionId}`;
  const score = sess
    ? ` · ${sess.n_correct}/${sess.n_attempted} 정답 (전체 ${sess.n_questions}문항)`
    : "";
  banner.innerHTML = `
    <span><strong>📂 ${escapeHTML(title)}</strong>${escapeHTML(score)}${historyMode ? " · 이력 보기" : ""}</span>
    <button id="banner-close">닫기</button>
  `;
  banner.hidden = false;
  $("#banner-close").addEventListener("click", () => {
    state.sessionId = null;
    state.questions = [];
    state.answered.clear();
    state._lastSessionSummary = null;
    $("#questions").innerHTML = "";
    $("#board-empty").style.display = "block";
    banner.hidden = true;
  });
}

function renderQuestions(qs) {
  $("#board-empty").style.display = qs.length ? "none" : "block";
  const root = $("#questions");
  root.innerHTML = "";

  // group_id 기준으로 인접 문제들을 묶어 렌더 (passage 한 번만 표시)
  let i = 0;
  let qNum = 1;
  while (i < qs.length) {
    const q = qs[i];
    const gid = q.group_id;
    if (gid) {
      // 같은 group_id 인 연속 문제들을 한 그룹으로
      const group = [q];
      let j = i + 1;
      while (j < qs.length && qs[j].group_id === gid) {
        group.push(qs[j]);
        j++;
      }
      const wrapper = document.createElement("div");
      wrapper.className = "q-group";
      // 그룹 첫 문항 또는 그룹 내 어느 문항에든 passage 가 있으면 그것을 사용
      const passage = group.find((x) => x.passage)?.passage;
      if (passage) {
        const pb = document.createElement("div");
        pb.className = "passage-block";
        pb.innerHTML = `<div class="passage-label">📖 지문</div>${escapeHTML(passage)}`;
        wrapper.appendChild(pb);
      }
      group.forEach((gq) => {
        wrapper.appendChild(renderCard(gq, qNum));
        qNum++;
      });
      root.appendChild(wrapper);
      i = j;
    } else {
      root.appendChild(renderCard(q, qNum));
      qNum++;
      i++;
    }
  }
}

function renderCard(q, idx) {
  const card = document.createElement("div");
  card.className = "q-card";
  card.dataset.qid = q.id;
  if (q.attempted) card.classList.add("attempted");
  if (q.excluded) card.classList.add("excluded");

  const tagHtml = q.topic_tag ? `<span class="topic-tag">${escapeHTML(q.topic_tag)}</span>` : "";
  const stem = document.createElement("div");
  stem.className = "stem";
  stem.innerHTML = `Q${idx}. ${escapeHTML(q.stem)}${tagHtml}`;
  card.appendChild(stem);

  const disabled = q.attempted === true;

  if (q.type === "mcq") {
    (q.choices || []).forEach((c, i) => {
      const lab = document.createElement("label");
      lab.className = "choice" + (disabled ? " disabled" : "");
      const checked = disabled && typeof q.user_answer === "number" && q.user_answer === i ? "checked" : "";
      const dis = disabled ? "disabled" : "";
      lab.innerHTML = `<input type="radio" name="q${q.id}" value="${i}" ${checked} ${dis}/> ${escapeHTML(c)}`;
      card.appendChild(lab);
    });
  } else {
    const ta = document.createElement("textarea");
    ta.placeholder = "주관식 답안을 입력하세요";
    ta.dataset.role = "short-answer";
    if (disabled) { ta.disabled = true; ta.value = q.user_answer ? String(q.user_answer) : ""; }
    card.appendChild(ta);
  }

  const actions = document.createElement("div");
  actions.className = "actions";
  const submitDisabled = disabled || q.excluded;
  actions.innerHTML = `
    <button class="submit" ${submitDisabled ? "disabled" : ""}>${disabled ? "제출됨" : "제출"}</button>
    <button class="explain" ${disabled && !q.excluded ? "" : "disabled"}>🔍 해설 보기</button>
    <button class="verify" title="강의노트 + 공식 자료로 정답 재검증">⚖️ 정답 검증</button>
    <button class="similar" ${disabled && !q.excluded ? "" : "disabled"}>＋ 비슷한 문제</button>
    <button class="exclude ${q.excluded ? "on" : ""}" title="이 문제를 다음 출제·채점에서 제외">
      ${q.excluded ? "✓ 제외됨" : "🚫 제외"}
    </button>
  `;
  card.appendChild(actions);

  if (disabled && !q.excluded) {
    state.answered.add(q.id);
    const badge = document.createElement("div");
    badge.className = "submitted-badge";
    badge.textContent = "✅ 제출됨 — 해설을 보려면 [🔍 해설 보기]";
    card.appendChild(badge);
  }
  if (q.excluded) {
    const badge = document.createElement("div");
    badge.className = "excluded-badge";
    badge.textContent = "🚫 제외됨 — 다음 출제·채점에서 빠집니다. [✓ 제외됨] 다시 누르면 해제";
    card.appendChild(badge);
  }

  actions.querySelector(".submit").addEventListener("click", () => submitAnswer(card, q));
  actions.querySelector(".explain").addEventListener("click", () => fetchExplain(card, q));
  actions.querySelector(".similar").addEventListener("click", (ev) => similar(q.id, ev));
  actions.querySelector(".exclude").addEventListener("click", () => toggleExclude(card, q));
  actions.querySelector(".verify").addEventListener("click", (ev) => verifyQuestion(card, q, ev));

  return card;
}

async function verifyQuestion(card, q, event) {
  const btn = event?.target?.closest("button.verify");
  // 검증 호출은 LLM 비용이 들 수 있어 인라인 카운트다운(약 30초 가정)
  const handle = btn ? startGenProgress(btn, "정답 검증 중", 30) : null;
  try {
    const data = await api(`/api/quiz/verify/${q.id}`, {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId }),
    });
    renderVerifyPanel(card, q, data);
  } catch (e) {
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("검증 실패: " + e.message);
  } finally {
    stopGenProgress(handle, "api", 1, false, true);
  }
}

function renderVerifyPanel(card, q, d) {
  const old = card.querySelector(".verify-panel");
  if (old) old.remove();
  const div = document.createElement("div");
  const verdict = d.verdict || "warning";
  div.className = "verify-panel " + verdict;
  const verdictLabel = { ok: "✅ 정답 일치", warning: "⚠️ 주의", conflict: "❌ 충돌" }[verdict] || "⚠️ 검토 필요";

  const noteV = d.note_view || {};
  const offV  = d.official_view || {};
  const noteCons = noteV.consistent === true ? "✓ 일치" : noteV.consistent === false ? "✗ 불일치" : "?";
  const offCons  = offV.consistent  === true ? "✓ 일치" : offV.consistent  === false ? "✗ 불일치" : "?";

  let suggested = "";
  if (d.suggested_answer != null && JSON.stringify(d.suggested_answer) !== JSON.stringify(d.stored_answer)) {
    let asText = d.suggested_answer;
    if (typeof asText === "number" && q.choices && q.choices[asText] != null) {
      asText = `${asText + 1}. ${q.choices[asText]}`;
    }
    suggested = `<div class="row"><span class="tag">제안 정답</span><span><strong>${escapeHTML(String(asText))}</strong></span></div>`;
  }

  div.innerHTML = `
    <h5>${verdictLabel} <span class="conf">신뢰도 ${Math.round((d.confidence || 0) * 100)}%</span></h5>
    ${d.summary ? `<div>${escapeHTML(d.summary)}</div>` : ""}
    <div class="row"><span class="tag">📘 강의노트</span><span>${escapeHTML(noteCons + " · " + (noteV.explanation || ""))}</span></div>
    <div class="row"><span class="tag">🌐 공식 자료</span><span>${escapeHTML(offCons + " · " + (offV.explanation || "") + (offV.source_hint ? " (출처: " + offV.source_hint + ")" : ""))}</span></div>
    ${suggested}
  `;
  card.appendChild(div);
}

async function toggleExclude(card, q) {
  const next = !q.excluded;
  const btn = card.querySelector(".exclude");
  btn.disabled = true;
  try {
    const data = await api(`/api/quiz/exclude/${q.id}`, {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId, excluded: next }),
    });
    q.excluded = !!data.excluded;
    // 카드 리렌더 (간단히 교체)
    const fresh = renderCard(q, parseInt(card.querySelector(".stem").textContent.match(/Q(\d+)/)?.[1] || "1", 10));
    card.replaceWith(fresh);
    refreshWrong();
  } catch (e) {
    btn.disabled = false;
    alert("제외 토글 실패: " + e.message);
  }
}

async function submitAnswer(card, q) {
  let userAnswer;
  if (q.type === "mcq") {
    const sel = card.querySelector(`input[name="q${q.id}"]:checked`);
    if (!sel) { alert("보기를 선택하세요."); return; }
    userAnswer = parseInt(sel.value, 10);
  } else {
    userAnswer = card.querySelector("textarea[data-role='short-answer']").value.trim();
    if (!userAnswer) { alert("답안을 입력하세요."); return; }
  }
  const submitBtn = card.querySelector(".submit");
  submitBtn.disabled = true;
  submitBtn.textContent = "제출 중…";
  try {
    await api("/api/quiz/answer", {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId, question_id: q.id, user_answer: userAnswer }),
    });
    submitBtn.textContent = "제출됨";
    card.classList.add("attempted");
    card.querySelectorAll("input[type=radio]").forEach((el) => (el.disabled = true));
    card.querySelectorAll("label.choice").forEach((el) => el.classList.add("disabled"));
    const ta = card.querySelector("textarea[data-role='short-answer']");
    if (ta) ta.disabled = true;
    card.querySelector(".explain").disabled = false;
    card.querySelector(".similar").disabled = false;
    state.answered.add(q.id);
    if (!card.querySelector(".submitted-badge")) {
      const badge = document.createElement("div");
      badge.className = "submitted-badge";
      badge.textContent = "✅ 제출됨 — 해설을 보려면 [🔍 해설 보기]";
      card.appendChild(badge);
    }
    refreshDashboard();
    refreshTree();
    refreshWrong();
  } catch (e) {
    submitBtn.disabled = false;
    submitBtn.textContent = "제출";
    alert("제출 실패: " + e.message);
  }
}

async function fetchExplain(card, q) {
  const btn = card.querySelector(".explain");
  btn.disabled = true;
  btn.textContent = "해설 불러오는 중…";
  try {
    const data = await api(`/api/quiz/explain/${q.id}?user_id=${encodeURIComponent(state.userId)}`);
    renderFeedback(card, q, data);
    btn.textContent = "🔍 해설 다시 보기";
    btn.disabled = false;
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "🔍 해설 보기";
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("해설 조회 실패: " + e.message);
  }
}

function renderFeedback(card, q, data) {
  const old = card.querySelector(".feedback");
  if (old) old.remove();
  const fb = document.createElement("div");
  fb.className = "feedback" + (data.correct ? "" : " wrong");
  let answerStr = data.correct_answer;
  if (q.type === "mcq" && q.choices) answerStr = `${data.correct_answer + 1}. ${q.choices[data.correct_answer]}`;
  const refs = (data.references || [])
    .map((r) => `<blockquote>📖 <span class="ref-loc">doc#${r.document_id} · chunk#${r.chunk_id}</span><br>“${escapeHTML(r.snippet)}”</blockquote>`)
    .join("");
  fb.innerHTML = `
    <h4>${data.correct ? "✅ 정답입니다" : "❌ 오답입니다"} — 체득 단계 ${stars(data.mastery_level)}</h4>
    <div><strong>정답:</strong> ${escapeHTML(String(answerStr))}</div>
    ${data.rationale ? `<div style="margin-top:6px"><strong>요약 해설:</strong> ${escapeHTML(data.rationale)}</div>` : ""}
    <div class="references">${refs ? `<strong>📖 인용 근거</strong>${refs}` : ""}</div>
  `;
  card.appendChild(fb);
}

async function similar(questionId, event) {
  const styleStr = Array.from(state.selectedStyles).join(", ") || "기출";
  const isPastexam = /(^|,\s*)기출(\s*,|$)/.test(styleStr);
  const difficulty = $("#difficulty")?.value || "중";
  const est = estimateGenSec(1, isPastexam, difficulty);
  // similar 호출은 카드 안 [+ 비슷한 문제] 버튼에서 옴 — 그 버튼을 찾아 인라인 카운트다운
  const trigger = (event && event.target && event.target.closest("button.similar")) || null;
  const handle = trigger ? startGenProgress(trigger, "비슷한 문제 출제 중", est.sec) : null;
  let ok = false;
  try {
    const data = await api("/api/quiz/similar", {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId, question_id: questionId }),
    });
    state.questions = state.questions.concat(data.questions);
    const root = $("#questions");
    data.questions.forEach((q, i) =>
      root.appendChild(renderCard(q, state.questions.length - data.questions.length + i + 1))
    );
    ok = true;
  } catch (e) {
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("비슷한 문제 출제 실패: " + e.message);
  } finally {
    stopGenProgress(handle, est.mode, 1, isPastexam, ok);
  }
}

// === history panel (좌측 사이드바: 카드 리스트 + 그룹 헤더) ===
async function refreshTree() {
  if (!state.loggedIn) return;
  try {
    state.tree = await api(`/api/quiz/tree?user_id=${encodeURIComponent(state.userId)}`);
    renderHistoryPanel();
  } catch (e) { console.warn("tree", e); }
}

// 트리(test_type→doc→session) 를 평탄화하여 세션 목록으로 변환
function flattenSessions() {
  const out = [];
  (state.tree || []).forEach((tt) => {
    (tt.documents || []).forEach((doc) => {
      (doc.sessions || []).forEach((s) => {
        out.push({
          id: s.id,
          title: s.title || "",
          created_at: s.created_at,
          n_questions: s.n_questions,
          n_attempted: s.n_attempted,
          n_correct: s.n_correct,
          test_type: tt.test_type,
          doc_id: doc.id,
          doc_filename: doc.filename,
        });
      });
    });
  });
  // 새로운 순으로 정렬
  out.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  return out;
}

// 세션 카드 — 한 줄 요약 [ 84% · 4/26 · 20문항 · 기출 ]
function makeSessionCard(s) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "session-card" + (state.sessionId === s.id ? " active" : "");
  const when = s.created_at ? s.created_at.replace("T", " ").slice(5, 16) : "";
  const pct = s.n_attempted > 0 ? Math.round((s.n_correct / s.n_attempted) * 100) : null;
  const scoreCls = pct == null ? "" : pct >= 80 ? "good" : pct >= 50 ? "mid" : "low";
  const scoreLabel = pct == null
    ? `<span class="score">미응시</span>`
    : `<span class="score ${scoreCls}">${pct}%</span>`;
  // 제목에 스타일 힌트 추출 (예: "… · 기출")
  const styleHint = (s.title || "").split("·").slice(-1)[0]?.trim() || "";
  const isStyle = styleHint && styleHint.length <= 8 && !/\d/.test(styleHint);
  const stylePill = isStyle ? `<span class="meta-pill style">${escapeHTML(styleHint)}</span>` : "";
  btn.innerHTML = `
    <div class="row1">
      ${scoreLabel}
      <span class="meta-pill">${s.n_correct}/${s.n_attempted || 0}</span>
      <span class="meta-pill">${s.n_questions}문항</span>
      ${stylePill}
    </div>
    <div class="row2">${escapeHTML(when)}${s.doc_filename ? " · " + escapeHTML(s.doc_filename) : ""}</div>
  `;
  btn.addEventListener("click", () => loadSession(s.id));
  return btn;
}

function matchesFilter(s, q) {
  if (!q) return true;
  const hay = [
    s.title || "",
    s.doc_filename || "",
    s.test_type || "",
    s.created_at || "",
    s.n_correct != null && s.n_attempted
      ? `${Math.round((s.n_correct / s.n_attempted) * 100)}%`
      : "",
  ].join(" ").toLowerCase();
  return hay.includes(q);
}

function renderHistoryPanel() {
  const recentRoot = $("#history-recent");
  const groupRoot = $("#history-groups");
  if (!recentRoot || !groupRoot) return;
  recentRoot.innerHTML = "";
  groupRoot.innerHTML = "";

  if (!state.tree || state.tree.length === 0) {
    groupRoot.innerHTML = '<div class="history-empty">아직 등록된 데이터가 없습니다.</div>';
    return;
  }

  const all = flattenSessions();
  const q = state.historyFilter;
  const filtered = all.filter((s) => matchesFilter(s, q));

  // 최근 5개 (필터 미적용 — 빠른 진입용)
  if (!q && all.length > 0) {
    const head = document.createElement("div");
    head.className = "group-title";
    head.textContent = `최근 세션 ${Math.min(5, all.length)}개`;
    recentRoot.appendChild(head);
    all.slice(0, 5).forEach((s) => recentRoot.appendChild(makeSessionCard(s)));
  }

  // test_type 별 collapsible 섹션
  const groups = new Map();
  filtered.forEach((s) => {
    if (!groups.has(s.test_type)) groups.set(s.test_type, []);
    groups.get(s.test_type).push(s);
  });

  if (groups.size === 0) {
    groupRoot.innerHTML = '<div class="history-empty">검색 결과가 없습니다.</div>';
    return;
  }

  groups.forEach((sessions, tt) => {
    // 평균 점수
    const scored = sessions.filter((s) => s.n_attempted > 0);
    const avg = scored.length
      ? Math.round(
          (scored.reduce(
            (a, s) => a + s.n_correct / s.n_attempted, 0
          ) / scored.length) * 100
        )
      : null;
    const avgCls = avg == null ? "" : avg >= 80 ? "" : avg >= 50 ? "mid" : "low";
    const open = q !== "" || state.historyGroupOpen.has(tt) ||
                 state.testType === tt;
    const det = document.createElement("details");
    det.className = "history-group";
    if (open) det.open = true;
    det.innerHTML = `
      <summary class="group-head">
        <span class="caret">▶</span>
        <span class="label">📂 ${escapeHTML(tt)}</span>
        ${avg != null ? `<span class="badge score ${avgCls}">평균 ${avg}%</span>` : ""}
        <span class="badge">${sessions.length}회</span>
      </summary>
      <div class="group-body"></div>
    `;
    const body = det.querySelector(".group-body");
    sessions.forEach((s) => body.appendChild(makeSessionCard(s)));
    det.addEventListener("toggle", () => {
      if (det.open) state.historyGroupOpen.add(tt);
      else state.historyGroupOpen.delete(tt);
    });
    // 그룹 헤더 클릭 시 (caret 이외) 활성 테스트 타입 전환은 사용자 명시적 액션으로만
    det.querySelector("summary.group-head").addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      state.testType = tt;
      $("#active-test-type").hidden = false;
      $("#active-test-type").innerHTML = `🎯 활성 테스트 타입: <strong>${escapeHTML(tt)}</strong>`;
      setStepEnabled(2, true);
      populateActiveDoc();
      refreshScopeDashboard(false);
    });
    groupRoot.appendChild(det);
  });
}

// 호환성 — 기존 코드 일부에서 renderTree() 호출 가능 (selectTestType 로컬 등)
function renderTree() { renderHistoryPanel(); }

async function loadSession(sid) {
  try {
    const data = await api(`/api/quiz/sessions/${sid}?user_id=${encodeURIComponent(state.userId)}`);
    state.sessionId = sid;
    state._lastSessionSummary = data.session;
    state.questions = data.questions.map((q) => ({
      id: q.id, type: q.type, stem: q.stem, choices: q.choices,
      topic_tag: q.topic_tag, attempted: q.attempted, user_answer: q.user_answer,
      excluded: !!q.excluded,
      passage: q.passage || null, group_id: q.group_id || null,
    }));
    state.answered = new Set(data.questions.filter((q) => q.attempted).map((q) => q.id));
    renderSessionBanner(true);
    renderQuestions(state.questions);
    renderTree();
  } catch (e) { alert("회차 조회 실패: " + e.message); }
}

// === dashboard (위젯 그리드 — 한 정보 = 한 위젯) ===
function resetWidgets() {
  $("#w-sessions-big").textContent = "—";
  $("#w-sessions-sub").textContent = "로그인 필요";
  $("#w-sessions-spark").innerHTML = "";
  $("#w-score-big").textContent = "—";
  $("#w-score-big").className = "widget-big";
  $("#w-score-sub").textContent = "직전 세션 대비 변화";
  $("#w-mastery-bar").innerHTML = "";
  $("#w-mastery-sub").textContent = "5단계 분포";
  $("#w-weak-list").innerHTML = '<li class="widget-empty">데이터 없음</li>';
  $("#w-coverage-arc")?.setAttribute("stroke-dasharray", "0 999");
  $("#w-coverage-text").textContent = "—";
  $("#w-coverage-sub").textContent = "테스트 타입 선택";
  $("#w-recent-line").innerHTML = "";
  $("#w-recent-sub").textContent = "최근 5세션 점수";
}

async function refreshDashboard() {
  if (!state.loggedIn) {
    resetWidgets();
    $("#dashboard-hint").textContent = "로그인 후 학습 현황이 표시됩니다.";
    return;
  }
  try {
    const [dash, sessions] = await Promise.all([
      api(`/api/quiz/dashboard?user_id=${encodeURIComponent(state.userId)}`),
      api(`/api/quiz/sessions?user_id=${encodeURIComponent(state.userId)}`).catch(() => []),
    ]);
    renderDashboardWidgets(dash || [], sessions || []);
  } catch (e) {
    console.warn("dashboard", e);
    $("#dashboard-hint").textContent = "대시보드 로드 실패: " + e.message;
  }
}

function renderDashboardWidgets(items, sessions) {
  const hint = $("#dashboard-hint");
  if ((!items || items.length === 0) && (!sessions || sessions.length === 0)) {
    resetWidgets();
    hint.textContent = "테스트 타입을 만들고 강의노트를 업로드하면 통계가 표시됩니다.";
    return;
  }
  hint.textContent = `${(items || []).length}개 테스트 타입 · ${(sessions || []).length}세션`;

  // ---- W1 총 세션 + 7일 sparkline ----
  const totalSessions = (sessions || []).length;
  $("#w-sessions-big").textContent = totalSessions;
  // 최근 7일 일별 카운트
  const days = []; // [{label, n}]
  const today = new Date();
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ key, n: 0, label: `${d.getMonth() + 1}/${d.getDate()}` });
  }
  (sessions || []).forEach((s) => {
    if (!s.created_at) return;
    const k = s.created_at.slice(0, 10);
    const idx = days.findIndex((x) => x.key === k);
    if (idx >= 0) days[idx].n += 1;
  });
  const last7 = days.reduce((a, x) => a + x.n, 0);
  $("#w-sessions-sub").textContent = `최근 7일 ${last7}세션`;
  const maxN = Math.max(1, ...days.map((d) => d.n));
  $("#w-sessions-spark").innerHTML = days
    .map((d, i) => {
      const h = d.n === 0 ? 2 : Math.round((d.n / maxN) * 22);
      const cls = d.n === 0 ? "zero" : i === days.length - 1 ? "today" : "";
      return `<span class="bar ${cls}" style="height:${h}px" title="${d.label}: ${d.n}세션"></span>`;
    })
    .join("");

  // ---- W2 평균 점수 + 직전 세션 대비 ----
  const scored = (sessions || []).filter((s) => s.n_attempted > 0);
  const avgRate = scored.length
    ? scored.reduce((a, s) => a + s.n_correct / s.n_attempted, 0) / scored.length
    : null;
  const avgPct = avgRate == null ? null : Math.round(avgRate * 100);
  $("#w-score-big").textContent = avgPct == null ? "—" : `${avgPct}%`;
  // delta = 가장 최근 응시 세션 vs 그 이전 세션
  if (scored.length >= 2) {
    const a = Math.round((scored[0].n_correct / scored[0].n_attempted) * 100);
    const b = Math.round((scored[1].n_correct / scored[1].n_attempted) * 100);
    const d = a - b;
    const cls = d > 0 ? "delta-up" : d < 0 ? "delta-down" : "delta-flat";
    const sign = d > 0 ? "▲ +" : d < 0 ? "▼ " : "= ";
    $("#w-score-sub").innerHTML =
      `직전 ${a}% <span class="${cls}">${sign}${d > 0 ? d : d}%p</span>`;
    $("#w-score-big").className = "widget-big" + (d > 0 ? " up" : d < 0 ? " down" : "");
  } else if (scored.length === 1) {
    $("#w-score-sub").textContent = "응시 세션 1개 — 비교 불가";
    $("#w-score-big").className = "widget-big";
  } else {
    $("#w-score-sub").textContent = "아직 응시 없음";
    $("#w-score-big").className = "widget-big";
  }

  // ---- W3 마스터리 5단계 분포 (wrongCache.topics) ----
  const topics = state.wrongCache?.topics || [];
  const buckets = [0, 0, 0, 0, 0]; // m1..m5
  topics.forEach((t) => {
    const lv = Math.max(1, Math.min(5, parseInt(t.level || 0, 10) || 1));
    buckets[lv - 1] += 1;
  });
  const masTotal = buckets.reduce((a, b) => a + b, 0);
  const bar = $("#w-mastery-bar");
  if (masTotal === 0) {
    bar.innerHTML = `<span class="m1" style="flex:1; background:#e5e7eb; color:#9ca3af">데이터 없음</span>`;
    $("#w-mastery-sub").textContent = "토픽 마스터리가 누적되면 표시";
  } else {
    bar.innerHTML = buckets
      .map((n, i) => {
        const flex = n === 0 ? 0 : n;
        return `<span class="m${i + 1}" style="flex:${flex}" title="Lv${i + 1}: ${n}개">${n > 0 ? n : ""}</span>`;
      })
      .join("");
    const mastered = buckets[4];
    $("#w-mastery-sub").textContent = `${masTotal}개 토픽 · 마스터됨 ${mastered}`;
  }

  // ---- W4 약점 Top 3 ----
  const allWeak = [];
  (items || []).forEach((it) => {
    (it.weak_topics || []).forEach((w) =>
      allWeak.push({
        topic: w.topic_tag,
        level: w.level,
        wrong: w.wrong_count || 0,
        tt: it.test_type,
      })
    );
  });
  allWeak.sort((a, b) => (a.level - b.level) || (b.wrong - a.wrong));
  const top3 = [];
  const seen = new Set();
  for (const w of allWeak) {
    if (seen.has(w.topic)) continue;
    seen.add(w.topic);
    top3.push(w);
    if (top3.length >= 3) break;
  }
  const weakUl = $("#w-weak-list");
  if (top3.length === 0) {
    weakUl.innerHTML = '<li class="widget-empty">약점 없음 · 좋아요!</li>';
  } else {
    weakUl.innerHTML = top3
      .map(
        (w) =>
          `<li>${escapeHTML(w.topic)} <small>Lv${w.level} · 오답${w.wrong}회</small></li>`
      )
      .join("");
  }

  // ---- W5 범위 커버리지 (scopeCache 가 있을 때만) ----
  const sc = state.scopeCache;
  if (sc && sc.coverage_pct != null) {
    const pct = Math.max(0, Math.min(100, Math.round(sc.coverage_pct)));
    const arc = $("#w-coverage-arc");
    const C = 2 * Math.PI * 24;             // 약 150.8
    const filled = (pct / 100) * C;
    arc.setAttribute("stroke-dasharray", `${filled.toFixed(2)} ${(C - filled).toFixed(2)}`);
    arc.setAttribute("stroke", pct >= 80 ? "#16a34a" : pct >= 50 ? "#eab308" : "#dc2626");
    $("#w-coverage-text").textContent = `${pct}%`;
    $("#w-coverage-sub").textContent =
      `${state.testType || ""} · 시험범위 ${sc.scope_total || 0}개`;
  } else {
    $("#w-coverage-arc")?.setAttribute("stroke-dasharray", "0 999");
    $("#w-coverage-text").textContent = "—";
    $("#w-coverage-sub").textContent = state.testType
      ? "범위 분석 대기 중"
      : "테스트 타입 선택 시 분석";
  }

  // ---- W6 최근 5세션 점수 미니 라인 ----
  const recent = (sessions || []).slice(0, 5).reverse();
  const line = $("#w-recent-line");
  if (recent.length === 0) {
    line.innerHTML = '<div class="widget-empty" style="font-size:11px;color:#9ca3af">데이터 없음</div>';
    $("#w-recent-sub").textContent = "최근 5세션 점수";
  } else {
    line.innerHTML = recent
      .map((s) => {
        const has = s.n_attempted > 0;
        const pct = has ? Math.round((s.n_correct / s.n_attempted) * 100) : 0;
        const h = has ? Math.max(2, Math.round((pct / 100) * 32)) : 2;
        const cls = !has ? "empty" : pct >= 80 ? "good" : pct >= 50 ? "mid" : "low";
        const when = s.created_at ? s.created_at.slice(5, 10) : "";
        const tip = has ? `${when} · ${pct}%` : `${when} · 미응시`;
        return `<span class="pt ${cls}" style="height:${h}px" data-tip="${escapeHTML(tip)}"></span>`;
      })
      .join("");
    $("#w-recent-sub").textContent =
      `오래된순 → 최근 (${recent.length}세션)`;
  }
}

// === scope dashboard (시험 범위 분석) ===
async function refreshScopeDashboard(forceRefresh) {
  if (!state.loggedIn || !state.testType) {
    $("#scope-dashboard").hidden = true;
    return;
  }
  $("#scope-dashboard").hidden = false;
  const summary = $("#scope-summary");
  summary.textContent = forceRefresh ? "재분석 중… (LLM 호출, 시간 소요)" : "분석 중…";
  const grid = $("#scope-grid");
  grid.innerHTML = "";
  try {
    const url = `/api/quiz/scope-dashboard?user_id=${encodeURIComponent(state.userId)}`
      + `&test_type=${encodeURIComponent(state.testType)}`
      + (forceRefresh ? "&refresh=true" : "");
    const data = await api(url);
    renderScopeDashboard(data);
  } catch (e) {
    summary.textContent = "분석 실패: " + e.message;
  }
}

function renderScopeDashboard(d) {
  state.scopeCache = d;
  // 커버리지 위젯 즉시 반영 (대시보드 다시 풀로드는 안 함)
  try { refreshDashboard(); } catch (_) {}
  const summary = $("#scope-summary");
  const cov = d.coverage_pct == null ? "-" : `${d.coverage_pct}%`;
  const tot = d.totals || {};
  const wr = tot.wrong_rate == null ? "-" : `${Math.round(tot.wrong_rate * 100)}%`;
  const srcLabel = { file: "📁 파일", cache: "💾 캐시", llm: "🤖 LLM", empty: "❓ 없음" }[d.scope_source] || d.scope_source;
  summary.textContent =
    `[${srcLabel}] 시험범위 ${d.scope_total}개 · 노트 ${d.note_total}개 · 커버리지 ${cov} · ` +
    `풀이 ${tot.attempted || 0}/${tot.asked || 0} · 오답률 ${wr}`;

  const grid = $("#scope-grid");
  grid.innerHTML = "";

  grid.appendChild(renderScopeCol("covered", "✅ 커버됨", "노트가 다루는 시험범위", d.covered, true));
  grid.appendChild(renderScopeCol("missing", "❗ 갭", "시험범위지만 노트에 없음", d.missing, true));
  grid.appendChild(renderScopeCol("extra",   "➕ 추가",  "노트에 있지만 시험범위 밖", d.extra, false));
}

function renderScopeCol(kind, title, desc, items, withSection) {
  const col = document.createElement("div");
  col.className = "scope-col " + kind;
  col.innerHTML = `<h4>${title} <span class="count">${items.length}</span></h4>
                   <div class="hint" style="font-size:10px;color:#9ca3af">${escapeHTML(desc)}</div>
                   <div class="scrolly"></div>`;
  const inner = col.querySelector(".scrolly");
  if (!items.length) {
    inner.innerHTML = `<div class="empty-note">— 항목 없음 —</div>`;
    return col;
  }
  items.forEach((it) => {
    const row = document.createElement("div");
    row.className = "topic-row";
    const stats = it.stats || {};
    let statHtml = "";
    if (stats.asked && stats.asked > 0) {
      const wr = stats.wrong_rate == null ? "" : `오답${Math.round(stats.wrong_rate * 100)}%`;
      const cls = stats.wrong_rate >= 0.5 ? "warn" : (stats.asked > 0 ? "ok" : "zero");
      statHtml = `<span class="stat ${cls}">${stats.asked}회·${wr || "응시0"}</span>`;
    } else {
      statHtml = `<span class="stat zero">미출제</span>`;
    }
    const name = it.name || "";
    const sub  = withSection && it.section ? `<small style="color:#9ca3af;font-size:10px"> · ${escapeHTML(it.section)}</small>` : "";
    row.innerHTML = `<span class="name">${escapeHTML(name)}${sub}</span>${statHtml}`;
    inner.appendChild(row);
  });
  return col;
}

// === wrong notes (오답 카드) ===
async function refreshWrong() {
  if (!state.loggedIn) return;
  try {
    const data = await api(`/api/wrong/list?user_id=${encodeURIComponent(state.userId)}`);
    state.wrongCache = {
      items: Array.isArray(data.items) ? data.items : [],
      topics: Array.isArray(data.topics) ? data.topics : [],
    };
    renderWrongNotes();
    // 마스터리 위젯이 wrongCache.topics 를 사용하므로 위젯 재계산
    try { refreshDashboard(); } catch (_) {}
  } catch (e) { console.warn("wrong", e); }
}

function renderWrongNotes() {
  const items = state.wrongCache?.items || [];
  const topics = state.wrongCache?.topics || [];

  // 상단 요약 위젯
  const totalWrong = items.length;
  const masteredTopics = topics.filter((t) => (t.level || 0) >= 5).length;
  const inProgress = topics.filter((t) => (t.level || 0) > 0 && (t.level || 0) < 5).length;
  $("#wrong-summary").innerHTML = `
    <div class="ws-cell total"><b>${totalWrong}</b>오답 누적</div>
    <div class="ws-cell master"><b>${masteredTopics}</b>마스터됨</div>
    <div class="ws-cell in-progress"><b>${inProgress}</b>진행중</div>
  `;

  // 토픽 마스터리 (기존 #topics 자리 — 압축 표시)
  const topicsBox = $("#topics");
  topicsBox.innerHTML = "";
  topics.slice(0, 5).forEach((t) => {
    const div = document.createElement("div");
    div.className = "topic";
    div.innerHTML = `<span>${escapeHTML(t.topic_tag)}</span>
      <span class="stars">${stars(t.level)} (${t.wrong_count})</span>`;
    topicsBox.appendChild(div);
  });

  // 정렬
  const sorted = items.slice();
  if (state.wrongSort === "mastery") {
    sorted.sort((a, b) => (a.level - b.level) || (b.wrong_count - a.wrong_count));
  }
  // (기본은 백엔드가 last_at desc 로 줌 — recent)

  // 카드
  const root = $("#wrong-list");
  root.innerHTML = "";
  if (sorted.length === 0) {
    root.innerHTML = `<div class="hint" style="font-size:11px;color:#9ca3af;padding:6px 0">오답이 누적되면 여기에 카드로 표시됩니다.</div>`;
    return;
  }
  sorted.forEach((it) => {
    const card = document.createElement("div");
    const lv = Math.max(1, Math.min(5, parseInt(it.level || 1, 10) || 1));
    card.className = "wrong-card" + (lv >= 5 ? " mastered" : "");
    const preview = (it.stem || "").slice(0, 120);
    const more = it.stem && it.stem.length > 120 ? "…" : "";
    const lastWrongDate = it.last_wrong_at
      ? it.last_wrong_at.slice(5, 10)
      : "최근";
    card.innerHTML = `
      <div class="stem-preview">${escapeHTML(preview)}${more}</div>
      <div class="meta-row">
        <span class="chip mastery m${lv}">마스터리 ${lv}/5</span>
        ${it.topic_tag ? `<span class="chip topic">${escapeHTML(it.topic_tag)}</span>` : ""}
        <span class="chip">${it.type === "mcq" ? "객관식" : "주관식"}</span>
        <span class="chip">오답 ${it.wrong_count}회</span>
        <span class="chip">${escapeHTML(lastWrongDate)}</span>
      </div>
      <div class="actions">
        <button class="replay" title="해당 문제와 동일 토픽으로 즉시 재출제">↻ 다시 풀기</button>
        <button class="similar" title="유사 문제 1개 출제 (LLM)">＋ 유사 문제</button>
        <button class="explain" title="저장된 정답·해설 보기">📖 해설</button>
        <button class="delete-mini" title="이 오답 노트에서 제거">🗑</button>
      </div>
    `;
    card.querySelector(".replay").addEventListener("click", (ev) =>
      replayWrongQuestion(it, ev)
    );
    card.querySelector(".similar").addEventListener("click", (ev) =>
      similar(it.question_id, ev)
    );
    card.querySelector(".explain").addEventListener("click", (ev) =>
      explainWrongQuestion(it, ev)
    );
    card.querySelector(".delete-mini").addEventListener("click", () =>
      deleteWrong(it.question_id)
    );
    root.appendChild(card);
  });
}

// "다시 풀기" — 백엔드에 단일-question 재출제 엔드포인트가 없으므로
// /api/quiz/similar 로 동일 토픽·동일 원본 기반의 유사 문제 1개를 즉시 출제.
// (백엔드에 GET /api/quiz/questions/{id} 또는 재시도 엔드포인트가 추가되면 그쪽으로 교체 권장)
async function replayWrongQuestion(it, event) {
  if (!state.loggedIn) return;
  const trigger = event?.target?.closest("button.replay") || null;
  // similar() 는 인라인 카운트다운을 자체 처리 — 그대로 위임
  return similar(it.question_id, { target: trigger });
}

// 오답 카드의 "📖 해설" — board 영역에 단일 카드 렌더 후 explain 호출
async function explainWrongQuestion(it, event) {
  const btn = event?.target?.closest("button.explain");
  if (btn) { btn.disabled = true; btn.textContent = "조회 중…"; }
  try {
    const data = await api(
      `/api/quiz/explain/${it.question_id}?user_id=${encodeURIComponent(state.userId)}`
    );
    // 보드에 임시 카드 한 장 띄우고 그 안에 feedback 렌더
    const root = $("#questions");
    $("#board-empty").style.display = "none";
    $("#session-banner").hidden = true;
    root.innerHTML = "";
    const card = document.createElement("div");
    card.className = "q-card attempted";
    card.innerHTML = `
      <div class="stem">📌 ${escapeHTML(it.stem)}${
        it.topic_tag ? `<span class="topic-tag">${escapeHTML(it.topic_tag)}</span>` : ""
      }</div>
      <div class="hint" style="font-size:11px;color:#6b7280">약점 노트에서 열린 단일 카드 — 정답·해설 표시</div>
    `;
    root.appendChild(card);
    // 문제 유형/보기 정보가 wrong-list 응답에 없어 q.choices 가 없음 → 정답 라벨만 텍스트로
    renderFeedback(card, { type: it.type, choices: null }, data);
  } catch (e) {
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("해설 조회 실패: " + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "📖 해설"; }
  }
}

async function practice() {
  if (!state.loggedIn) { alert("먼저 로그인하세요."); return; }
  const n = 3;
  const styleStr = Array.from(state.selectedStyles).join(", ") || "기출";
  const isPastexam = /(^|,\s*)기출(\s*,|$)/.test(styleStr);
  const difficulty = $("#difficulty")?.value || "중";
  const est = estimateGenSec(n, isPastexam, difficulty);
  const handle = startGenProgress($("#practice-btn"), `약점 강화 출제 중 (${n}문항)`, est.sec);
  let ok = false;
  try {
    const data = await api("/api/wrong/practice", {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId, n }),
    });
    state.sessionId = data.session_id || null;
    state.questions = data.questions;
    state.answered = new Set();
    renderSessionBanner(false);
    renderQuestions(data.questions);
    refreshAll();
    ok = true;
  } catch (e) {
    if (e.status === 503) { alert("LLM 미연결: " + e.message); openAuthModal(); }
    else alert("약점 강화 출제 실패: " + e.message);
  } finally {
    stopGenProgress(handle, est.mode, n, isPastexam, ok);
  }
}

async function deleteWrong(qid) {
  if (!confirm("이 문제를 약점 노트에서 제거하고 데이터를 삭제할까요?")) return;
  try {
    await api(`/api/wrong/${qid}?user_id=${encodeURIComponent(state.userId)}`, { method: "DELETE" });
    refreshAll();
  } catch (e) { alert("삭제 실패: " + e.message); }
}

async function resetUser() {
  if (!state.loggedIn) return;
  if (!confirm(`정말 사용자 '${state.userId}' 의 모든 데이터(문서/회차/문제/약점)를 삭제할까요?`)) return;
  try {
    await api(`/api/user/${encodeURIComponent(state.userId)}`, { method: "DELETE" });
    const remaining = loadKnownUsers().filter((t) => t !== state.userId);
    localStorage.setItem(LS_TYPES, JSON.stringify(remaining));
    logout();
    alert("삭제 완료.");
  } catch (e) { alert("삭제 실패: " + e.message); }
}

// === aggregate refresh ===
async function refreshAll() {
  if (!state.loggedIn) return;
  await Promise.allSettled([
    refreshTestTypeOptions(),
    refreshTree(),
    refreshDashboard(),
    refreshWrong(),
  ]);
  if (state.testType) {
    populateActiveDoc();
    refreshScopeDashboard(false);
  }
}

// === util ===
function escapeHTML(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// === 배포 버전 위젯 ===
(function initAppVersion() {
  fetch("/api/version").then(r => r.json()).then(v => {
    const el = document.getElementById("app-version");
    if (!el) return;
    const d = (v.updated || "").slice(0, 10);
    el.textContent = "v" + (v.version || "dev") + (d ? " · " + d : "");
  }).catch(() => {});
}());
