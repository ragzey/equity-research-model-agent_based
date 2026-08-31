# Equity Research Model — Final write-up

**Repository:** [ragzey/equity-research-model-agent_based](https://github.com/ragzey/equity-research-model-agent_based)  
**Branch:** `main`  
**Date:** 31 August 2026  
**HEAD:** `3ced79d` (assumption auditor) on `main`

This note describes the project, how it was built, and what is on the live research desk. It is the source of truth for the current model. Older session logs (`PROJECT_LOG.md`, `WORK_LOG.md`, `IMPLEMENTATION_LOG.md`) keep chronology; 31 August is recorded there as well as here.

The CLI and GUI produce an **illustrative model result**, not an investment recommendation.

---

## What this project is

A local **multi-agent equity research desk**. A ticker goes in. The desk pulls statements, a 10-K, peers, and Yahoo Street fields, then writes an initiation-style memo (Markdown + PDF) and a GUI pack.

The design lock-in is simple:

| Layer | Who owns it |
| --- | --- |
| WACC, three-stage FCFF, operating P&L, labeled mix weights, 12-month price target, Street table, catalysts, bear/base/bull | **Python** |
| Peer keep/drop, 10-K reading, industry / products / operations / growth-path / mix **labels**, assumption picks, accept/reject / revert, memo prose | **LLM**, on a ledger, with Python clips |
| Invented tickers, DCF numbers, mix percentages, URLs, citations, Street quotes, or catalyst dates | **Not allowed** |

Runs are **ticker-only**. Peers and bond ISINs are optional. Banks, insurers, and brokers are detected from Yahoo’s sector label and **do not get an FCFF**. There is no fabricated bank model.

---

## What happened

The desk was built in layers, not as a single prompt.

1. **Ledger and data.** Shared LangGraph state. Yahoo statements (`yfinance`), SEC EDGAR Item 1A / Item 7, SQLite TTL cache.
2. **Cost of capital.** TRACE YTMs via Finnhub when ISINs exist; otherwise Damodaran synthetic rating from EBIT / interest coverage, plus live `^TNX`. After-tax Kd uses a 21% statutory shield. WACC is assembled in Python and stored in `discount_rate`.
3. **Quant.** Firm-type classifier, three-stage FCFF, post-quant arithmetic checks, 5×5 WACC / *g* sensitivity.
4. **Research agents.** Competitive (harvested or pinned comps only). Qualitative (filing quotes, not memory). Industry/macro (categorical demand/cycle packet). Operations (CCC, NWC, sales-to-capital). Assumption architect (menu labels, not free-typed rates). Reviewer (accept/reject only).
5. **Writer and memo auditor.** Frozen Python figures in the memo. The memo auditor may correct narrative and clip invented tickers; it **cannot rewrite WACC or DCF** and it does not re-decide growth labels.
6. **Local GUI.** Flask on `http://127.0.0.1:5050`. Cover, charts, assumption register, mix, assumption audit, memo, PDF download.
7. **Research close (`d489374`, 30 Aug).** Operating P&L on the same path as FCFF. Evidence-gated bear/base/bull. Dated catalysts. Model versus Street and a Python thesis spine. Then a full-repo hallucination check: shared URL detector (`www.` as well as `http(s)`), writer/qualitative citation drops, missing allow-lists no longer reopen `high` / `light`, Street mean PT force-aligned.
8. **Name → ticker and thin-data overlay (`c7ab2f9`).** Runs stay ticker-only, but a company name maps to a listed symbol. When Yahoo or 10-K quotes are thin, SEC companyfacts and ledger views fill statements so agents are not guessing from a blank page.
9. **Company/products + allowlisted web research (`9190f19`).** Industry was mixing **firm products** with **category demand**. Market size lives on IR/web, not in the 10-K. Python fetches allowlisted pages; the LLM may quote and copy those URLs, not mint them.
10. **Scale-up lifecycle (`3e0dbfe`).** Names with P/S ≥ 15 and CAGR ≥ 25% were valued as mature 2–7% / 3-year rails. Last year’s P&L is not the firm the market is pricing. Terminal margin is no longer pulled below the classifier floor.
11. **Growth-path agent (`0fc21b0`).** Scale-ups need an explicit horizon, STC fade from a build phase, and a terminal-margin path rather than last year’s print. Mature names get `not_applicable`.
12. **Labeled mix and stacked-recession cut (`91ce064`).** GUI sidecars of NBIS, TJX, TPR, and MNST all printed Sell. See [31 August research passes](#31-august-research-passes) for why. Mix is a **label** (`dcf_heavy` 90/10, `base` 70/30, `balanced` 55/45); Python overwrites forged floats. High-growth classification starts at 10% CAGR.
13. **Assumption auditor (`3ced79d`).** Second independent agent **before Quant**. It may only revert labels to the classifier baseline. The memo auditor stays last, on citations and frozen figures, so the two agents do not rubber-stamp the same accept/reject pass.

Unit tests: **218 passing** (`python -m unittest discover -s tests -q`).

---

## The finalized model

### Valuation identity

```text
Unlevered FCFF  = NOPAT − reinvestment
Fair value      = labeled mix of DCF and peer-median EV/EBITDA
                  (base 70/30; dcf_heavy 90/10; balanced 55/45;
                   100% DCF if there is no usable peer multiple)
12-month PT     = FV × (1 + cost of equity) − indicated DPS
Model band      = Buy if PT upside ≥ 15%, Sell if ≤ −15%, else Hold
```

The band is a **model convention**, labeled as such on the memo. It is not a house recommendation.

Terminal *g* is economy-linked (live 10-year Treasury), floored at 1.5%, and recapped so WACC − *g* stays usable. Stretch labels (`high` growth, `extend` horizon, `light` / `heavy` sales-to-capital, moat margin lift) require ledger evidence. A missing or empty allow-list stays on conservative labels.

### Operating P&L (then FCFF)

Each forecast year is built as:

Revenue → EBIT → interest held at last reported (absolute) → EBT → tax 21% → NI → model EPS.

Last-reported NI / EPS use **one fiscal period** (statement diluted EPS when present, otherwise NI / shares). Forecast EPS is model NI / shares, not Street EPS.

**FCFF stays unlevered:** NOPAT − reinvestment. Interest is a P&L line, not a WACC input from this path.

### 31 August research passes

These passes exist because live GUI runs were systematically **Sell**, for two different reasons.

**Scale-ups (NBIS).** DCF was about $177 versus ~$205 last price (−14%, Hold on DCF alone). A 70/30 blend with trailing EV/EBITDA ~$18 pulled fair value to ~$129 → Sell. Trailing EV/EBITDA is a poor descriptor when EBITDA is thin or the multiple is a leftover from last year’s P&L. Mix therefore offers `dcf_heavy` (90/10) when P/S ≥ 15, the firm is scale-up, or EBITDA is non-positive. Scale-ups cannot pick `balanced`. Stretch 80% sales growth still needs a **forward** consensus print above the 50% base cap — the desk does not reverse-engineer P/S into growth to match the tape, and it does not invent TAM.

**Mature compounders (TJX).** Industry tagged **downswing + negative inflection** from a Yahoo article about Walmart/Home Depot consumers while TJX’s ledger was 6.5% CAGR and category **in_line**. That unlocked **2% growth / 2 years / 1.5% *g* / heavy STC** — even the bull case sat far below the tape. Fixes:

- Cycle follows **peer/target trailing growth**, not a consumer snippet about another ticker.
- Negative inflection is cleared when cycle is mid/upswing and category is not `below_history`.
- Hostile macro needs **downswing AND (negative inflection OR below_history)**.
- Mature names stay on classifier-base growth, years, and perpetuity *g* unless demand is actually hostile.
- Heavy STC needs a lengthening CCC or heavy reinvestment, not one year of inventory.
- Distressed EV/EBITDA outliers (Kohl’s-type 5× in an otherwise mid-teens off-price set) are dropped from the peer median.
- Mature terminal margin holds **current** (no automatic 5% fade).

High-growth/scale-up names cannot pick `low` or `compress` unless the cycle is hostile or category is evidenced `below_history`. They get `extend` without a constructive industry packet. High-growth classification starts at **10%** trailing CAGR (was 15%).

**Two auditors, by design.** The assumption auditor runs after the reviewer and before Quant. It is independent of the accept/reject pass. It can only revert. The memo auditor cannot rewrite WACC/DCF or re-pick growth, years, or *g*.

### Operating scenarios

Bear / base / bull change only operating menu labels (growth rate and years, terminal margin, sales-to-capital, perpetuity *g*). **WACC, beta, size premium, and peer EV/EBITDA stay on the accepted base case.** The published rating uses the assumption-auditor-cleared base path (reviewer accept/reject, then revert-only audit). Bull and bear are the most optimistic and pessimistic combinations still inside the evidence-gated allow-list.

### Dated catalysts

Dates come from the ledger: Yahoo earnings / ex-dividend and the 10-K filing date. Keyword events are taken only from `qualitative_evidence`. A date must sit near the matched token and **on or after today**. Nothing is invented.

### Model versus Street

Yahoo `targetMeanPrice`, forward EPS, analyst count, and labeled **forward** revenue growth. Trailing `revenueGrowth` is shown only as trailing; it is **not** blended into the DCF and is **not** the Street growth cell.

The thesis spine is Python (“Street mean 12-month target is $X versus this model’s $Y”). The writer may add *why*. It may not restate or change dollar targets or EPS.

---

## Graph

```text
Aggregator
  → Competitive ∥ Qualitative
  → Industry/macro ∥ Company/products ∥ Operations
  → Growth-path → Valuation-mix
  → Router (financials stop here)
  → Architect (labels) → Reviewer (accept/reject)
  → Assumption auditor (revert only)
  → Quant (Python WACC + P&L + FCFF)
  → Post-quant (arithmetic; bounded retry)
  → Sensitivity (WACC/g grid + operating bear/base/bull)
  → Writer (memo + GUI pack)
  → Memo auditor (narrative only)
```

| Node | Role |
| --- | --- |
| Aggregator | Statements, 10-K, peers, Street snapshot, consensus, bonds, price history |
| Competitive | Keep harvested or operator-pinned tickers; peer matrix |
| Qualitative | Item 1A / Item 7; section-tagged evidence |
| Industry / macro | Category, pricing, cycle, rates, inflection — views, not DCF numbers. Cycle owned by peer/target trailing growth |
| Company / products | Item 1 products, mix, firm catalysts. Not category TAM |
| Operations | CCC, NWC, reinvestment; skipped for financials |
| Growth-path | Scale-up horizon, STC fade, margin path. `not_applicable` for mature names |
| Valuation-mix | Labeled DCF/relative weights from firm type, peer fit, industry. Overlay overwrites forged floats |
| Architect | Pick a label from a Python menu |
| Reviewer | Accept or reject. Cannot type a new rate |
| Assumption auditor | Independent second check of labels. May only revert to classifier baseline |
| Quant | CAPM, WACC, P&L, three-stage FCFF |
| Post-quant | Missing/non-finite outputs, terminal spread, FCFF durability |
| Sensitivity | 5×5 WACC/g plus operating scenarios |
| Writer | Frozen facts + prose. Sources from the ledger. Mix / growth-path / assumption-audit tables |
| Memo auditor | Per-agent memo check. Force-aligns PT, FV, last price, WACC, Street mean PT, rating. Does not re-decide growth labels |

LLM providers: OpenAI or Gemini. Temperature is omitted for GPT-5.x. **WACC and DCF never go through the LLM.**

---

## What the memo and GUI show

- Cover: last price, blended FV, 12-month PT, model band, Street mean PT when Yahoo supplies it  
- Investment thesis (Python spine + optional why)  
- Model versus Street table  
- Operating forecast (P&L + unlevered FCFF)  
- Operating scenarios  
- Dated catalysts  
- Assumption register (accepted labels, sources)
- Growth-path, valuation-mix, and assumption-audit tables
- WACC appendix, peer table, research-desk transcript, memo audit
- Sources and references built from the ledger (no invented URLs)

GUI: `python gui.py` → [http://127.0.0.1:5050](http://127.0.0.1:5050) (localhost only). Flask debug is off; restart after Python or static changes. Hard-refresh so `app.js?v=research14` loads.

---

## Hallucination controls (current)

- Competitive `selected` / `rejected` clipped to the harvest (or operator pin).
- Industry evidence and narrative dropped if they contain a web link, a novel ticker, or a number not in the ledger — except URLs Python already fetched onto the allowlist.
- Writer qualitative, thesis, and desk synthesis drop `http(s)`, `ftp://`, and `www.` unless the URL is already on the ledger source list.
- Qualitative LLM summary with an unsourced URL falls back to evidence-only filing quotes.
- Architect stretch reasons must cite ledger numbers; URLs are stripped.
- Reviewer notes with a URL are discarded (Python fallback text).
- Mix overlay overwrites any LLM-typed DCF/relative **percentage**; only menu labels survive.
- Assumption auditor may revert labels; it cannot type a new rate or a DCF figure.
- Memo auditor `contains_web_link` + `novel_tickers`; cannot rewrite model math or re-decide growth / years / *g*.
- High-band growth is unlocked by a 10-K quote or an evidenced scale-up path, not qualitative prose. Trailing Yahoo growth is not blended into the DCF. Stretch 80% still needs a forward sales-growth estimate.

**Residual (accepted):** agents can still paraphrase a 10-K without inventing a ticker, URL, or DCF figure. A weakly related filing sentence can still be offered as growth evidence; the reviewer and assumption auditor are the veto.

---

## How to run

```powershell
pip install -r requirements.txt
cp .env.example .env
```

Set `SEC_USER_AGENT` to a real contact. Set `OPENAI_API_KEY` or `GEMINI_API_KEY` (also pasteable in the GUI). `FINNHUB_API_KEY` is required only for the TRACE bond path.

```powershell
python main.py --ticker MSFT --openai-model gpt-5.6
python gui.py
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -q
```

Do not treat old files in `outputs/reports/` as the current pipeline. Those folders are gitignored.

---

## Scope

- **In:** Non-financial operating companies. FCFF + labeled relative blend (`dcf_heavy` 90/10, `base` 70/30, `balanced` 55/45).
- **Out:** Banks, insurers, brokers (router withholds FCFF). OpenBB is planned, not wired. Finnhub cannot list “all bonds for ticker X”; ISINs are CLI or conservative 10-K harvest.
- **Not claimed:** Independent verification of a target price, or that LLM prose is a substitute for a human analyst. The desk does not manufacture Buys.
