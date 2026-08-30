# Equity Research Pipeline

Multi-agent equity research pipeline built around a shared **LangGraph state ledger**. Agents read and write structured financial data so valuation math stays deterministic and auditable.

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

**Today:** Financial statements are pulled via **`yfinance`** (`market_api.py`) and cached locally in SQLite (`tools/cache.py`, default 12 hours). Qualitative filing text comes from **SEC EDGAR** (`sec_api.py`) — latest 10-K Item 1A (Risk Factors) and Item 7 (MD&A), with Item 8 used only for conservative bond-identifier harvest. CIK maps, submissions JSON, and 10-K text are cached (ticker map 24 hours; filings 7 days).

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
| Industry / macro node | `agents/industry_macro.py` | ✅ categorical demand/cycle packet; filing-grounded |
| Operations node | `agents/operations.py`, `tools/operating_cycle.py` | ✅ Python CCC/NWC/STC; skipped for financials |
| Assumption architect | `agents/assumption_architect.py`, `tools/assumption_menus.py` | ✅ labeled menus only; stretch labels need ledger reasons |
| Qual → Quant reviewer | `agents/reviewer.py` | ✅ accept/reject only; no invented DCF numbers |
| Independent auditor | `agents/independent_auditor.py` | ✅ per-agent narrative check; cannot rewrite WACC/DCF |
| Research desk handoffs | `graphs/desk.py`, `agent_messages` | ✅ Qual / Competitive / Industry / Operations / Architect / Reviewer / Writer |
| LangGraph `StateGraph` | `graphs/graph.py` | ✅ compiled and tested |
| OpenBB gateway | — | ⏳ planned |
| Firm lifecycle classifier | `tools/firm_classifier.py` | ✅ bounded fallback when statements cannot support STC |
| WACC + 3-stage FCFF DCF | `tools/valuation.py`, `agents/quant.py` | ✅ |
| Post-Quant arithmetic review | `agents/post_quant_reviewer.py` | ✅ bounded retry + FCFF durability diagnostics |
| 5x5 DCF sensitivity | `agents/sensitivity.py` | ✅ WACC ±100 bp; *g* centered on applied perpetuity |
| Financial-firm scope gate | `tools/firm_classifier.py`, `agents/valuation_router.py` | ✅ FCFF withheld; banks/insurers/brokers out of scope |

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
│   ├── operations.py
│   ├── assumption_architect.py
│   ├── valuation_router.py
│   ├── reviewer.py
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
    ├── cache.py
    ├── consensus.py
    ├── operating_cycle.py
    ├── assumption_menus.py
    ├── bond_identifiers.py
    ├── pdf_memo.py
    ├── sec_api.py
    ├── finnhub_bond.py
    ├── firm_classifier.py
    ├── peer_analysis.py
    ├── qual_to_quant.py
    ├── debt_analysis.py
    └── valuation.py
```

Current FCFF path:

```text
Aggregator
  → Competitive ∥ Qualitative
  → Industry/macro ∥ Operations
  → Router (financials stop)
  → Architect (labels) → Reviewer (accept/reject)
  → Quant (Python WACC + FCFF)
  → Post-quant → Sensitivity → Writer → Auditor
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

**LLM (required for Competitive, Qualitative, industry/macro, operations, architect, reviewer, writer, auditor):** OpenAI (`OPENAI_API_KEY`, `sk-…`) or Gemini from [Google AI Studio](https://aistudio.google.com/apikey) (`GEMINI_API_KEY` or `GOOGLE_API_KEY`, `AIza…`). Paste the key in the GUI, or:

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

Open [http://127.0.0.1:5050](http://127.0.0.1:5050). The local desk UI runs a ticker, streams the log, and shows the memo, accept/reject decisions, and downloads. It binds to localhost only.

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

## Logs

- `PROJECT_LOG.md` — full project chronology  
- `IMPLEMENTATION_LOG.md` — implementation rationale and audit fixes  
