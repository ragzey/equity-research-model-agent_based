const form = document.querySelector("#run-form");
const runBtn = document.querySelector("#run-btn");
const runState = document.querySelector("#run-state");
const empty = document.querySelector("#empty");
const workspace = document.querySelector("#workspace");
const logEl = document.querySelector("#log");

const KEY_STORE = "equityDesk.openaiKey";
const MODEL_STORE = "equityDesk.openaiModel";
const PROVIDER_STORE = "equityDesk.llmProvider";
const OPENAI_DEFAULT = "gpt-4o-mini";
const GEMINI_DEFAULT = "gemini-2.5-flash";
let envHasOpenAI = false;
let envHasGemini = false;

function $(id) {
  return document.querySelector(id);
}

function fmtUsd(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return (
    "$" +
    number.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

function fmtPct(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${(number * 100).toFixed(2)}%`;
}

function fmtSignedPct(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const sign = number > 0 ? "+" : "";
  return `${sign}${(number * 100).toFixed(1)}%`;
}

function setState(label) {
  runState.textContent = label;
}

function showError(message) {
  empty.hidden = true;
  workspace.hidden = false;
  $("#identity").innerHTML = `<p class="error">${escapeHtml(message)}</p>`;
  $("#cover").innerHTML = "";
  $("#metrics").innerHTML = "";
  $("#key-data").innerHTML = "";
  $("#charts").innerHTML = "";
  $("#panel-memo").innerHTML = "";
  $("#panel-assumptions").innerHTML = "";
  $("#panel-sources").innerHTML = "";
  $("#panel-desk").innerHTML = "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-on", tab.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    const on = panel.dataset.panel === name;
    panel.hidden = !on;
    panel.classList.toggle("is-on", on);
  });
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

function envHasLlm() {
  return envHasOpenAI || envHasGemini;
}

function inferredProvider(key, selected) {
  if (selected && selected !== "auto") return selected;
  if ((key || "").startsWith("AIza") || (key || "").startsWith("AQ.")) return "gemini";
  if ((key || "").startsWith("sk-")) return "openai";
  if (envHasGemini && !envHasOpenAI) return "gemini";
  return "openai";
}

function syncModelPlaceholder() {
  const provider = inferredProvider(
    $("#openai-key").value.trim(),
    $("#llm-provider") ? $("#llm-provider").value : "auto"
  );
  const modelEl = $("#openai-model");
  if (!modelEl) return;
  if (provider === "gemini") {
    modelEl.placeholder = GEMINI_DEFAULT;
    if (!modelEl.value || modelEl.value === OPENAI_DEFAULT) {
      modelEl.value = GEMINI_DEFAULT;
    }
  } else {
    modelEl.placeholder = OPENAI_DEFAULT;
    if (!modelEl.value || modelEl.value === GEMINI_DEFAULT) {
      modelEl.value = OPENAI_DEFAULT;
    }
  }
}

async function loadMeta() {
  const response = await fetch("/api/meta");
  const data = await response.json();
  const env = data.env || {};
  envHasOpenAI = Boolean(env.openai);
  envHasGemini = Boolean(env.gemini);
  $("#env").innerHTML = `
    <dt>OpenAI</dt><dd>${env.openai ? "key in .env" : "off"}</dd>
    <dt>Gemini</dt><dd>${env.gemini ? "key in .env" : "off"}</dd>
    <dt>Finnhub</dt><dd>${env.finnhub ? "on" : "off — Damodaran Kd"}</dd>
    <dt>SEC UA</dt><dd>${env.sec_user_agent_ok ? "set" : "placeholder"}</dd>
  `;
  const storedKey = sessionStorage.getItem(KEY_STORE);
  const storedModel = sessionStorage.getItem(MODEL_STORE);
  const storedProvider = sessionStorage.getItem(PROVIDER_STORE);
  if (storedProvider && $("#llm-provider")) {
    $("#llm-provider").value = storedProvider;
  }
  if (storedKey && !$("#openai-key").value) {
    $("#openai-key").value = storedKey;
  }
  if (storedModel && !$("#openai-model").value) {
    $("#openai-model").value = storedModel;
  } else if (!storedModel && !$("#openai-model").value) {
    $("#openai-model").value = env.gemini && !env.openai ? GEMINI_DEFAULT : OPENAI_DEFAULT;
  }
  syncModelPlaceholder();
  const recent = $("#recent");
  recent.innerHTML = "";
  (data.reports || []).forEach((item) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${item.ticker}  ${item.memo_name.replace("_memo.md", "")}`;
    button.addEventListener("click", () => openReport(item.memo_name));
    li.appendChild(button);
    recent.appendChild(li);
  });
}

function ratingClass(rating) {
  const value = String(rating || "").toLowerCase();
  if (value === "buy") return "is-buy";
  if (value === "sell") return "is-sell";
  if (value === "hold") return "is-hold";
  return "is-na";
}

let lastPack = null;

function renderCharts(pack) {
  lastPack = pack;
  const charts = $("#charts");
  const points = pack.valuation_points || [];
  const history = pack.price_history || {};
  const historyPoints = history.points || [];
  if (!points.length && historyPoints.length < 2) {
    charts.innerHTML = "";
    return;
  }
  const valuationFigure = points.length
    ? `<figure>
        <figcaption>Exhibit 1 · Valuation versus the market</figcaption>
        <canvas id="chart-valuation"></canvas>
        <p class="chart-note">DCF range from the WACC/g grid. Dashed line is the last price. ${escapeHtml(pack.pt_method || "")}</p>
      </figure>`
    : "";
  const marketFigure =
    historyPoints.length >= 2
      ? `<figure>
        <figcaption>Exhibit 2 · 12-month indexed price versus ${escapeHtml(history.benchmark_label || history.benchmark || "the market")}</figcaption>
        <canvas id="chart-market"></canvas>
        <p class="chart-note">Weekly adjusted closes rebased to 100 at ${escapeHtml(history.start || "the start of the window")}. Source: Yahoo Finance.</p>
      </figure>`
      : "";
  charts.innerHTML = valuationFigure + marketFigure;
  requestAnimationFrame(() => {
    if (window.ResearchCharts && points.length) {
      window.ResearchCharts.drawValuationField($("#chart-valuation"), points);
    }
    if (window.ResearchCharts && historyPoints.length >= 2) {
      window.ResearchCharts.drawIndexedPerformance($("#chart-market"), history);
    }
  });
}

function renderSources(pack) {
  const rows = pack.sources || [];
  if (!rows.length) {
    $("#panel-sources").innerHTML = "<p class='hint'>No source register on this note.</p>";
    return;
  }
  const body = rows
    .map((row) => {
      const url = String(row.url || "").trim();
      const source = url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.source || url)}</a>`
        : escapeHtml(row.source || "");
      return `
      <tr>
        <td>${escapeHtml(row.item || "")}</td>
        <td>${escapeHtml(row.detail || "")}</td>
        <td>${source}</td>
        <td>${escapeHtml(row.used_for || "")}</td>
      </tr>`;
    })
    .join("");
  $("#panel-sources").innerHTML = `
    <table class="assumptions">
      <thead>
        <tr>
          <th>Input</th>
          <th>What was used</th>
          <th>Source</th>
          <th>Used for</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
    <p class="chart-note">Ledger citations only. The desk does not invent URLs or references.</p>
  `;
}

function renderAssumptions(pack) {
  const rows = pack.assumptions || [];
  if (!rows.length) {
    $("#panel-assumptions").innerHTML = "<p class='hint'>No assumption register on this note.</p>";
    return;
  }
  const body = rows
    .map(
      (row) => `
      <tr>
        <td>${escapeHtml(row.item || "")}</td>
        <td>${escapeHtml(row.value || "")}</td>
        <td>${escapeHtml(row.justification || "")}</td>
        <td>${escapeHtml(row.source || "")}</td>
      </tr>`
    )
    .join("");
  $("#panel-assumptions").innerHTML = `
    <table class="assumptions">
      <thead>
        <tr>
          <th>Assumption</th>
          <th>Value</th>
          <th>Justification</th>
          <th>Source</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderSummary(summary) {
  empty.hidden = true;
  workspace.hidden = false;
  const pack = summary.report_pack || {};
  const verified = summary.verified
    ? `<span class="chip ok">Verified</span>`
    : `<span class="chip warn">Not verified</span>`;
  const method = escapeHtml(summary.valuation_method || "n/a");
  const firm = escapeHtml(summary.firm_type || "Unclassified");
  const desk = escapeHtml(summary.desk_mode || "n/a");
  const name = escapeHtml(pack.company_name || summary.company_name || summary.ticker || "");
  const industry = escapeHtml(pack.industry || summary.industry || "");
  const country = escapeHtml(pack.country || summary.country || "");
  const peers = (summary.peer_selection && summary.peer_selection.selected) || [];
  const peerMode = (summary.peer_selection && summary.peer_selection.mode) || "auto";
  const peerChip = peers.length
    ? `<span class="chip">peers ${escapeHtml(peers.join(" "))} (${escapeHtml(peerMode)})</span>`
    : "";
  $("#identity").innerHTML = `
    <div class="ticker">${name}</div>
    <span class="chip">${escapeHtml(summary.ticker || "")}</span>
    ${industry ? `<span class="chip">${industry}</span>` : ""}
    ${country ? `<span class="chip">${country}</span>` : ""}
    <span class="chip">${firm}</span>
    <span class="chip">${method}</span>
    ${verified}
    <span class="chip">desk ${desk}</span>
    ${peerChip}
  `;

  const rating = pack.model_rating || summary.model_rating;
  const ratingLabel = rating ? String(rating).toUpperCase() : "N/A";
  const showValue = summary.valuation_method !== "unsupported_financial";
  $("#cover").innerHTML = showValue
    ? `
    <div class="rating ${ratingClass(rating)}">${escapeHtml(ratingLabel)}</div>
    <div class="cover-cell"><span>12-month PT</span><strong>${fmtUsd(pack.price_target_12m)}</strong></div>
    <div class="cover-cell"><span>Share price</span><strong>${fmtUsd(pack.share_price != null ? pack.share_price : summary.share_price)}</strong></div>
    <div class="cover-cell"><span>Upside to PT</span><strong>${fmtSignedPct(pack.upside_to_pt)}</strong></div>
    <div class="cover-cell"><span>Fair value</span><strong>${fmtUsd(pack.fair_value)}</strong></div>
    <p class="disclaimer">${escapeHtml(pack.model_rating_note || "Model output only; not an investment recommendation.")}</p>
  `
    : `<p class="disclaimer">Financial-services firms are out of scope for this FCFF model.</p>`;

  $("#metrics").innerHTML = showValue
    ? `
    <div class="metric"><span>DCF</span><strong>${fmtUsd(pack.dcf_value != null ? pack.dcf_value : summary.display_value)}</strong></div>
    <div class="metric"><span>Relative EV/EBITDA</span><strong>${fmtUsd(pack.relative_value)}</strong></div>
    <div class="metric"><span>WACC</span><strong>${fmtPct(summary.wacc)}</strong></div>
    <div class="metric"><span>vs price (FV)</span><strong>${fmtSignedPct(pack.upside_to_fair_value != null ? pack.upside_to_fair_value : summary.gap)}</strong></div>
  `
    : "";

  const keyRows = pack.key_data || [];
  $("#key-data").innerHTML = keyRows
    .map(
      (row) =>
        `<div class="key-row"><span>${escapeHtml(row.label || "")}</span><strong>${escapeHtml(row.value || "")}</strong></div>`
    )
    .join("");

  renderCharts(pack);
  renderAssumptions(pack);
  renderSources(pack);

  const downloads = [];
  if (summary.memo_name) {
    downloads.push(`<a class="dl" href="/api/files/${encodeURIComponent(summary.memo_name)}">Markdown</a>`);
  }
  if (summary.has_pdf && summary.pdf_name) {
    const stem = summary.pdf_download_name || summary.ticker || "";
    let href = `/api/files/${encodeURIComponent(summary.pdf_name)}`;
    if (stem) href += `?as=${encodeURIComponent(stem)}`;
    downloads.push(`<a class="dl" href="${href}">PDF</a>`);
  }
  $("#downloads").innerHTML = downloads.join("");
  $("#panel-memo").innerHTML = `<article class="memo">${summary.memo_html || "<p>No memo.</p>"}</article>`;

  const decisions = (summary.decisions || [])
    .map(
      (row) => `
      <div class="decision">
        <div class="who">${escapeHtml(row.key || "")} · ${escapeHtml(row.action || "")}</div>
        <div>${escapeHtml(row.reason || "")}</div>
      </div>`
    )
    .join("");
  const handoffs = (summary.handoffs || [])
    .map(
      (row) => `
      <div class="handoff">
        <div class="who">${escapeHtml(row.from_agent || "")} → ${escapeHtml(row.to_agent || "")} · ${escapeHtml(row.kind || "")}</div>
        <div>${escapeHtml(row.body || "")}</div>
      </div>`
    )
    .join("");
  $("#panel-desk").innerHTML = `
    <h2>Accept / reject</h2>
    ${decisions || "<p class='hint'>No reviewer decisions on this memo.</p>"}
    <h2>Handoffs</h2>
    ${handoffs || "<p class='hint'>No desk handoffs recorded.</p>"}
  `;
}

async function openReport(name) {
  setState("Loading memo");
  const response = await fetch(`/api/reports/${encodeURIComponent(name)}`);
  if (!response.ok) {
    showError("Could not open that memo.");
    setState("Idle");
    return;
  }
  const summary = await response.json();
  renderSummary(summary);
  switchTab("memo");
  setState("Idle");
}

async function pollJob(jobId) {
  let seen = 0;
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    const logs = job.logs || [];
    if (logs.length > seen) {
      logEl.textContent = logs.join("\n");
      logEl.scrollTop = logEl.scrollHeight;
      seen = logs.length;
    }
    if (job.status === "done") {
      renderSummary(job.summary);
      switchTab("memo");
      setState("Done");
      runBtn.disabled = false;
      await loadMeta();
      return;
    }
    if (job.status === "error") {
      showError(job.error || "Run failed.");
      switchTab("log");
      setState("Failed");
      runBtn.disabled = false;
      return;
    }
    setState(job.status === "queued" ? "Queued" : "Running");
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const openaiKey = $("#openai-key").value.trim();
  const openaiModel = $("#openai-model").value.trim();
  const llmProvider = $("#llm-provider") ? $("#llm-provider").value : "auto";
  if (!openaiKey && !envHasLlm()) {
    showError(
      "Paste an OpenAI or Gemini API key. Competitive, Qualitative, the reviewer, and the writer must call the model."
    );
    setState("Idle");
    return;
  }
  if (openaiKey) {
    sessionStorage.setItem(KEY_STORE, openaiKey);
  }
  if (openaiModel) {
    sessionStorage.setItem(MODEL_STORE, openaiModel);
  }
  sessionStorage.setItem(PROVIDER_STORE, llmProvider);
  runBtn.disabled = true;
  empty.hidden = true;
  workspace.hidden = false;
  switchTab("log");
  logEl.textContent = "Starting run…";
  setState("Starting");
  const response = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ticker: $("#ticker").value,
      target_year: $("#year").value,
      peers: $("#peers").value,
      bonds: $("#bonds").value,
      openai_api_key: openaiKey,
      openai_model: openaiModel,
      llm_provider: llmProvider,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    showError(data.error || "Could not start a run.");
    runBtn.disabled = false;
    setState("Idle");
    return;
  }
  await pollJob(data.job_id);
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

window.addEventListener("resize", () => {
  if (!lastPack || !window.ResearchCharts) return;
  const points = lastPack.valuation_points || [];
  const history = lastPack.price_history || {};
  const valuation = $("#chart-valuation");
  const market = $("#chart-market");
  if (valuation && points.length) {
    window.ResearchCharts.drawValuationField(valuation, points);
  }
  if (market && (history.points || []).length >= 2) {
    window.ResearchCharts.drawIndexedPerformance(market, history);
  }
});

if ($("#llm-provider")) {
  $("#llm-provider").addEventListener("change", syncModelPlaceholder);
}
if ($("#openai-key")) {
  $("#openai-key").addEventListener("input", syncModelPlaceholder);
}

loadMeta().catch(() => {
  $("#env").innerHTML = "<dt>Status</dt><dd>Could not read environment.</dd>";
});
