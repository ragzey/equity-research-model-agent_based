# Equity Research Pipeline

Multi-agent equity research pipeline built around a shared **LangGraph state ledger**. Agents read and write structured financial data so valuation math stays deterministic and auditable.

**Current desk (31 Aug 2026):** ticker in → Python WACC / three-stage FCFF / operating P&L / labeled DCF–relative mix / 12-month PT, plus LLM research on a clipped ledger. Full write-up: [`FINAL_MODEL.md`](FINAL_MODEL.md). Older session logs are historical; the 31 August passes (scale-up path, mix, assumption auditor) are recorded there and below.

## Financial Data & Technology Architecture

Corporate bonds trade **over-the-counter (OTC)** rather than on centralized public exchanges, so secondary-market fixed-income data is fragmented, lag-prone, and often behind institutional paywalls. To maintain institutional-grade modeling without a Bloomberg terminal, this pipeline implements a **hybrid fixed-income data strategy** with a deterministic fallback.

```
                 [Are structured TRACE YTMs available?]
                            /              \
                          YES               NO
                          /                  \
        [PRIMARY PATHWAY]                      [DETERMINISTIC FALLBACK]
  Finnhub Bond API → FINRA TRACE ticks    Extract EBIT & Interest Expense
  + linear interpolation to 10-year YTM   → Damodaran synthetic rating (AAA–D)
                    \                              /
                     \                            /
                      Apply 21% marginal tax shield
                                    │
              After-tax Cost of Debt → WACC → Three-stage FCFF DCF
                    │                         │
              discount_rate          calculated_dcf_value
```

### 1. Market data layer — implemented (`yfinance` + SEC EDGAR)

**Today:** Financial statements are pulled via **`yfinance`** (`market_api.py`) and cached locally in SQLite (`tools/cache.py`, default 12 hours). When Yahoo is thin, **SEC companyfacts** (`sec_facts.py`) overlay the ledger. Qualitative filing text comes from **SEC EDGAR** (`sec_api.py`) — latest 10-K Item 1A (Risk Factors) and Item 7 (MD&A), with Item 8 used only for conservative bond-identifier harvest. A company name maps to a listed ticker. CIK maps, submissions JSON, and 10-K text are cached (ticker map 24 hours; filings 7 days). Allowlisted IR/news pages are fetched in Python (`web_research.py`); the LLM does not browse.

**Roadmap:** [**OpenBB**](https://docs.openbb.co/) as a unified, vendor-agnostic gateway for statements, macro series, and yield curves. OpenBB is **not wired in yet**; the current connectors are deliberate, minimal, and tested.

### 2. Live secondary-market pricing — implemented (FINRA TRACE via Finnhub)

For the gold-standard **pre-tax cost of debt**, the system uses **market-implied yields**, not 10-K coupon rates or web-search snippets.

- Supply corporate bond **ISINs** via `--target-bonds` / `target_bonds` when you have them. Explicit CLI ISINs always win.
- If none are supplied, the aggregator harvests **check-digit-valid** ISINs (and US CUSIP-9 → ISIN conversions) from Item 7 / Item 8 text **only when nearby language looks like a debt footnote**. This is candidate discovery, not a security master; junk identifiers are dropped.
- `finnhub_bond.py` queries [**Finnhub's Bond API**](https://finnhub.io/docs/api/bond-tick) for **TRACE** transaction ticks and bond profiles (maturity dates).
- `debt_analysis.py` linearly interpolates YTMs to a **10-year horizon**, aligned with the `^TNX` risk-free rate.

**Limitation (documented honestly):** Finnhub does not expose “all bonds for ticker X.” Harvested ISINs are best-effort candidates from the 10-K.

### 3. High-fidelity fallback — implemented (Damodaran synthetic rating)

When TRACE data is empty, illiquid, or unavailable:

1. `extract_ebit_and_interest()` parses **Operating Income (EBIT)** and **absolute Interest Expense** from the ledger income statement (`yfinance` layout).
2. **Interest coverage ratio** maps to a **synthetic rating (AAA–D)** using the dated snapshot in `data/damodaran_spreads.json` (Aswath Damodaran large-cap non-financial buckets). HTML scrape is never the source of truth; `DAMODARAN_REFRESH=1` may refresh a cache copy only.
3. **Pre-tax Kd** = live **10-Year Treasury (`^TNX`)** + implied default spread.

### 4. Statutory marginal tax shield — implemented

**After-tax Kd** = pre-tax Kd × (1 − 0.21), using the **US statutory marginal rate (21%)** — appropriate for forward-looking WACC, not noisy effective tax rates from filings.

---

### Implementation status

| Component | File(s) | Status |
|-----------|---------|--------|
| Shared ledger | `graphs/state.py`, `graphs/defaults.py` | ✅ `outstanding_bonds`, `target_bonds`, `is_math_verified=False` at init |
| Yahoo statements | `tools/market_api.py` | ✅ SQLite TTL cache |
| SEC 10-K sections | `tools/sec_api.py` | ✅ Item 1A, Item 7, Item 8 harvest; cached |
| TRACE / Finnhub | `tools/finnhub_bond.py` | ✅ (requires `FINNHUB_API_KEY`; CLI ISINs or harvested candidates) |
| Cost of debt math | `tools/debt_analysis.py` | ✅ interpolation + dated Damodaran file |
| Consensus growth overlay | `tools/consensus.py`, `agents/reviewer.py` | ✅ labeled Yahoo +1y blend; not management guidance |
| Bond identifier harvest | `tools/bond_identifiers.py` | ✅ check-digit ISIN/CUSIP candidates |
| Markdown + PDF memo | `agents/writer.py`, `tools/pdf_memo.py` | ✅ presentation PDF beside the Markdown |
| Data Aggregator node | `agents/aggregator.py` | ✅ |
| Quant Analyst node | `agents/quant.py` | ✅ live `^TNX`, logs pathway |
| Competitive Analyst node | `agents/competitive.py` | ✅ harvested/pinned comps; no invented tickers |
| Qualitative Analyst node | `agents/qualitative.py` | ✅ evidence-only synthesis + section-tagged quotes |
| Name → ticker | `tools/sec_api.py`, `agents/aggregator.py` | ✅ listed-ticker resolve; Yahoo aliases |
| SEC companyfacts overlay | `tools/sec_facts.py`, `tools/market_api.py` | ✅ fills statements when Yahoo or 10-K quotes are thin |
| Allowlisted web research | `tools/web_research.py` | ✅ Python-fetched IR/SEC/high-quality pages; LLM may copy quotes and URLs, not mint them |
| Industry / macro node | `agents/industry_macro.py` | ✅ categorical demand/cycle packet; peer-growth cycle overlays generic consumer snippets |
| Company / products node | `agents/company_products.py` | ✅ Item 1 products, mix, firm catalysts; not category growth |
| Operations node | `agents/operations.py`, `tools/operating_cycle.py` | ✅ Python CCC/NWC/STC; skipped for financials |
| Growth-path node | `agents/growth_path.py` | ✅ scale-up horizon / STC fade / margin path; not_applicable for mature names |
| Valuation-mix node | `agents/valuation_mix.py`, `tools/valuation_mix.py` | ✅ labeled mix only (`dcf_heavy` 90/10, `base` 70/30, `balanced` 55/45); LLM cannot type a percentage |
| Assumption architect | `agents/assumption_architect.py`, `tools/assumption_menus.py` | ✅ labeled menus only; stretch labels need ledger reasons |
| Qual → Quant reviewer | `agents/reviewer.py` | ✅ accept/reject only; no invented DCF numbers |
| Assumption auditor | `agents/assumption_auditor.py`, `tools/assumption_audit.py` | ✅ independent second check before Quant; may only revert to classifier baseline |
| Memo auditor | `agents/independent_auditor.py` | ✅ per-agent narrative check; cannot rewrite WACC/DCF or re-decide growth labels |
| Research desk handoffs | `graphs/desk.py`, `agent_messages` | ✅ Qual / Competitive / Industry / Products / Operations / Growth-path / Mix / Architect / Reviewer / Assumption auditor / Writer / Memo auditor |
| LangGraph `StateGraph` | `graphs/graph.py` | ✅ compiled and tested |
| OpenBB gateway | — | ⏳ planned |
| Firm lifecycle classifier | `tools/firm_classifier.py` | ✅ mature / high-growth / scale-up (P/S ≥ 15 and CAGR ≥ 25%); high-growth starts at 10% CAGR |
| WACC + 3-stage FCFF DCF | `tools/valuation.py`, `agents/quant.py` | ✅ |
| Post-Quant arithmetic review | `agents/post_quant_reviewer.py` | ✅ bounded retry + FCFF durability diagnostics |
| 5x5 DCF sensitivity | `agents/sensitivity.py` | ✅ WACC ±100 bp; *g* centered on applied perpetuity |
| Financial-firm scope gate | `tools/firm_classifier.py`, `agents/valuation_router.py` | ✅ FCFF withheld; banks/insurers/brokers out of scope |
| Operating P&L | `tools/valuation.py`, `tools/firm_classifier.py` | ✅ Same fiscal period as DCF; FCFF stays unlevered |
| Operating bear/base/bull | `tools/operating_scenarios.py`, `agents/sensitivity.py` | ✅ Evidence-gated menus; WACC held at base |
| Dated catalysts | `tools/catalysts.py` | ✅ Yahoo / 10-K dates; no invented dates |
| Model versus Street | `tools/street.py` | ✅ Yahoo fields + Python thesis spine |
| URL / ticker grounding | `utils/grounding.py`, auditor / writer / qualitative | ✅ unsourced `www.` and `http(s)` dropped; allowlisted fetched URLs may be copied |

### Design correction vs. generic tutorials

**After-tax cost of debt is not stored in `discount_rate`.** Quant combines it with CAPM cost of equity and capital weights; the resulting **WACC** is stored in `discount_rate`. Cost of debt remains in `valuation_summary["cost_of_debt"]`.

---

## Project structure

```
src/equity_research/
├── agents/
│   ├── aggregator.py
│   ├── competitive.py
│   ├── qualitative.py
│   ├── industry_macro.py
│   ├── company_products.py
│   ├── operations.py
│   ├── growth_path.py
│   ├── valuation_mix.py
│   ├── assumption_architect.py
│   ├── reviewer.py
│   ├── assumption_auditor.py
│   ├── valuation_router.py
│   ├── quant.py
│   ├── post_quant_reviewer.py
│   ├── sensitivity.py
│   ├── writer.py
│   └── independent_auditor.py
├── graphs/
│   ├── state.py
│   ├── defaults.py
│   ├── desk.py
│   └── graph.py
└── tools/
    ├── market_api.py
    ├── web_research.py
    ├── sec_facts.py
    ├── sec_api.py
    ├── operating_cycle.py
    ├── assumption_menus.py
    ├── assumption_audit.py
    ├── valuation_mix.py
    ├── firm_classifier.py
    ├── peer_analysis.py
    ├── peer_discovery.py
    ├── operating_scenarios.py
    ├── street.py
    ├── report_pack.py
    ├── valuation.py
    └── …
```

Current FCFF path:

```text
Aggregator
  → Competitive ∥ Qualitative
  → Industry/macro ∥ Company/products ∥ Operations
  → Growth-path → Valuation-mix
  → Router (financials stop)
  → Architect (labels) → Reviewer (accept/reject)
  → Assumption auditor (revert only)
  → Quant (Python WACC + P&L + FCFF)
  → Post-quant → Sensitivity → Writer → Memo auditor
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

**SEC (required for filings):**
```
SEC_USER_AGENT="MyResearchProject/1.0 (replace-with-your-real-email)"
```
Replace the placeholder with a monitored contact address before querying EDGAR.

**LLM (required for Competitive, Qualitative, industry/macro, company/products, operations, growth-path, valuation-mix, architect, reviewer, assumption auditor, writer, memo auditor):** OpenAI (`OPENAI_API_KEY`, `sk-…`) or Gemini from [Google AI Studio](https://aistudio.google.com/apikey) (`GEMINI_API_KEY` or `GOOGLE_API_KEY`, `AIza…`). Paste the key in the GUI, or:

```
python main.py --ticker TJX --openai-api-key AIza... --llm-provider gemini
```

Default Gemini model is `gemini-2.5-flash`. WACC and DCF stay in Python.

**Finnhub (required for TRACE primary path):**
```
FINNHUB_API_KEY=your_finnhub_key
```

## Run the compiled pipeline

```powershell
python main.py --ticker MSFT
```

### GUI

```powershell
python gui.py
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050). The local desk UI runs a ticker, streams the log, and shows the memo, mix, assumption audit, accept/reject decisions, and downloads. It binds to localhost only. Flask debug is off; restart after Python or static changes and hard-refresh so `app.js?v=research14` loads.

Peers and bond ISINs are optional. Additional arguments:

```powershell
python main.py --ticker MSFT --target-year 2026 `
  --peers AAPL GOOGL AMZN `
  --target-bonds US0000000000
```

The CLI output is an illustrative model result, not an investment
recommendation or independently verified target price.

This pipeline is for **non-financial operating companies**. Banks, insurers,
brokers, and other financial-services firms are detected from Yahoo's sector
label and skip FCFF rather than receiving a fabricated bank model. Run names
such as MSFT, AAPL, or GOOGL.

## Component tests

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -q
```

## Bond ISINs

Finnhub requires **ISINs**, not equity tickers. Prefer explicit CLI values; otherwise the pipeline may try conservative 10-K candidates:

```powershell
python main.py --ticker MSFT --target-bonds US594918AH34
```

Harvested identifiers are check-digit validated and require debt-footnote context. They are not a substitute for a security master.

Year-1/Year-2 Yahoo consensus, when available and inside a 40% absolute cap, is blended 50/50 with bounded historical revenue CAGR, then clipped again to the firm-type band. The memo labels this as sell-side consensus, not management guidance.

## References

1. [OpenBB Documentation](https://docs.openbb.co/) — planned unified data layer  
2. [Damodaran — Ratings & Interest Coverage](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html)  
3. [SEC EDGAR](https://www.sec.gov/edgar)  
4. [FINRA TRACE](https://www.finra.org/filing-reporting/trace)  
5. [Finnhub Bond API](https://finnhub.io/docs/api/bond-tick)  

## Why the 31 August passes exist

GUI runs of **NBIS, TJX, TPR, and MNST** all printed **Sell**. Two failures were model policy, not the tape:

1. **Scale-ups (NBIS).** Trailing EV/EBITDA was ~$18 versus a DCF around $177. A fixed 70/30 blend pulled fair value to a Sell even though DCF alone was inside the ±15% Hold band. Last year’s P&L is not the firm the market is pricing when P/S is 15×+ and CAGR is hyper. The desk now has a **Scale-up High-Growth** lifecycle, a **growth-path** agent (extend / fade / scale), and a **labeled mix** (`dcf_heavy` 90/10 when EV/EBITDA is a poor descriptor). The LLM cannot type a percentage. Stretch 80% growth still needs a **forward** sales-growth estimate on the ledger.
2. **Mature compounders (TJX).** The industry agent tagged a downswing from a Yahoo article about Walmart/Home Depot consumers. TJX’s own ledger was 6.5% CAGR and category growth **in_line**. That snippet stacked **2% growth, a 2-year horizon, 1.5% perpetuity *g*, and heavy STC**. Peer median EV/EBITDA was also pulled by a distressed department-store multiple. Cycle now follows **peer/target trailing growth**. Low / compress / low *g* need a real hostile packet. Distressed EV/EBITDA outliers are dropped from the median. Heavy STC needs a lengthening CCC or heavy reinvestment, not one year of inventory.

The **assumption auditor** (`3ced79d`) is a second independent agent **before Quant**. It may only revert labels to the classifier baseline. The **memo auditor** still runs last and does not re-decide growth, years, or perpetuity *g*. Two auditors on the same accept/reject pass would rubber-stamp each other.

High-growth classification starts at **10%** trailing CAGR (was 15%) so 10–15% growers use the 8–20% / 5-year rail instead of mature 2–7% / 3 years. Mid-single-digit compounders stay mature. The ±15% Buy/Hold/Sell band, no invented TAM, and no reverse-engineering P/S into growth are unchanged.

Unit tests: **218** passing (`python -m unittest discover -s tests -q`).

## Logs

- `FINAL_MODEL.md` — current model: lock-ins, graph, 31 August passes, hallucination controls
- `PROJECT_LOG.md` — chronology (scaffold through 31 August)
- `WORK_LOG.md` — session work notes, including why later agents were added
- `IMPLEMENTATION_LOG.md` — implementation rationale and earlier audit fixes

