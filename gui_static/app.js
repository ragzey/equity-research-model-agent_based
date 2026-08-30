const form = document.querySelector("#run-form");
const runBtn = document.querySelector("#run-btn");
const runState = document.querySelector("#run-state");
const empty = document.querySelector("#empty");
const workspace = document.querySelector("#workspace");
const logEl = document.querySelector("#log");

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
  $("#metrics").innerHTML = "";
  $("#panel-memo").innerHTML = "";
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

async function loadMeta() {
  const response = await fetch("/api/meta");
  const data = await response.json();
  const env = data.env || {};
  $("#env").innerHTML = `
    <dt>OpenAI</dt><dd>${env.openai ? "on" : "off — deterministic desk"}</dd>
    <dt>Finnhub</dt><dd>${env.finnhub ? "on" : "off — Damodaran Kd"}</dd>
    <dt>SEC UA</dt><dd>${env.sec_user_agent_ok ? "set" : "placeholder"}</dd>
  `;
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

function renderSummary(summary) {
  empty.hidden = true;
  workspace.hidden = false;
  const verified = summary.verified
    ? `<span class="chip ok">Verified</span>`
    : `<span class="chip warn">Not verified</span>`;
  const method = escapeHtml(summary.valuation_method || "n/a");
  const firm = escapeHtml(summary.firm_type || "Unclassified");
  const desk = escapeHtml(summary.desk_mode || "n/a");
  $("#identity").innerHTML = `
    <div class="ticker">${escapeHtml(summary.ticker || "")}</div>
    <span class="chip">${firm}</span>
    <span class="chip">${method}</span>
    ${verified}
    <span class="chip">desk ${desk}</span>
  `;

  const showValue = summary.valuation_method !== "unsupported_financial";
  $("#metrics").innerHTML = `
    <div class="metric"><span>Price</span><strong>${fmtUsd(summary.share_price)}</strong></div>
    <div class="metric"><span>Model value</span><strong>${showValue ? fmtUsd(summary.display_value) : "—"}</strong></div>
    <div class="metric"><span>WACC</span><strong>${showValue ? fmtPct(summary.wacc) : "—"}</strong></div>
    <div class="metric"><span>vs price</span><strong>${showValue ? fmtSignedPct(summary.gap) : "—"}</strong></div>
  `;

  const downloads = [];
  if (summary.memo_name) {
    downloads.push(`<a class="dl" href="/api/files/${encodeURIComponent(summary.memo_name)}">Markdown</a>`);
  }
  if (summary.has_pdf && summary.pdf_name) {
    downloads.push(`<a class="dl" href="/api/files/${encodeURIComponent(summary.pdf_name)}">PDF</a>`);
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

loadMeta().catch(() => {
  $("#env").innerHTML = "<dt>Status</dt><dd>Could not read environment.</dd>";
});
