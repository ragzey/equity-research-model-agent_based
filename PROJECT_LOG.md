# Equity Research Pipeline — Project Log

**Last updated:** 2026-08-31. The live model is documented in [`FINAL_MODEL.md`](FINAL_MODEL.md). Sections 1–17 are historical; 18+ record the close and the 31 August passes.  
**Workspace:** `C:\Equity research model`  
**Purpose:** Multi-agent equity research pipeline (LangGraph target architecture)

---

## 1. Project scaffold (Session 1)

Created initial directory layout and Python package structure:

```
.
├── .env.example
├── .gitignore
├── requirements.txt
├── test_tool.py
├── data/raw|processed|filings/
├── outputs/reports/
├── notebooks/
└── src/
    ├── __init__.py
    └── equity_research/
        ├── agents/
        ├── graphs/
        ├── tools/
        ├── prompts/
        ├── utils/
        └── config/
```

- `.gitignore` — ignores `.env`, virtualenvs, caches, and local data/report artifacts while keeping folder placeholders.
- `.env.example` — template for LLM keys, LangSmith, market-data APIs, and `SEC_USER_AGENT`.

---

## 2. Shared LangGraph ledger — `state.py`

**File:** `src/equity_research/graphs/state.py`

Defined `EquityResearchState` (`TypedDict`) as the shared ledger all agents read/write:

| Section | Fields |
|---------|--------|
| Inputs | `ticker`, `target_year` |
| Raw material | `income_statement`, `balance_sheet`, `cash_flow_statement`, `recent_news`, `sec_filing_chunks`, `outstanding_bonds` |
| Quant outputs | `discount_rate`, `calculated_dcf_value`, `valuation_summary` |
| Qualitative | `business_risks`, `competitive_advantages` |
| QC | `is_math_verified`, `reviewer_feedback`, `revision_count` |
| Deliverable | `final_equity_memo_path` |

**Later addition:** `outstanding_bonds` for structured bond YTMs (TRACE / bond API feed).

---

## 3. Market data tool — `market_api.py`

**File:** `src/equity_research/tools/market_api.py`

- Function: `fetch_financial_statements(ticker)`
- Source: Yahoo Finance via `yfinance`
- Returns: `income_statement`, `balance_sheet`, `cash_flow_statement` as nested dicts (`DataFrame.to_dict()`), plus `info` metadata
- Error handling: empty statements → `None`; logging throughout

**Verified:** MSFT pull succeeded (Microsoft Corporation, Technology sector).

**Note:** `Timestamp` keys in statement dicts are fine in Python; serialize to strings before JSON persistence.

---

## 4. SEC EDGAR tool — `sec_api.py`

**File:** `src/equity_research/tools/sec_api.py`

- Functions: `get_cik_for_ticker()`, `fetch_latest_10k_text()`
- Flow: ticker → CIK → submissions JSON → latest 10-K → HTML → clean text → Item 1A (Risk Factors) or Item 7 (MD&A) excerpt (50k chars)
- Compliance: reads `SEC_USER_AGENT` from `.env`; request timeouts; 0.2s polite delay between calls

**Fixes applied during build:**
- CIK map URL corrected to `https://www.sec.gov/files/company_tickers.json` (not `data.sec.gov`, which 404s)
- Archive URLs use unpadded CIK (`320193`, not `0000320193`)
- TOC skip logic: first `ITEM 1A.` hit is often the table of contents; tool skips short matches

**Verified:** AAPL CIK `0000320193`, 10-K downloaded, real Risk Factors text extracted.

---

## 5. Cost of debt engine — `debt_analysis.py`

**File:** `src/equity_research/tools/debt_analysis.py`

Hybrid methodology:

1. **Gold standard:** linear interpolation of structured bond YTMs to ~10-year maturity (`maturity_years`/`ytm` or `years_to_maturity`/`yield`)
2. **Fallback:** Damodaran large-cap interest-coverage → synthetic rating → default spread + live risk-free rate

Key design choices:
- `abs(interest_expense)` before coverage ratio (Yahoo stores interest as negative)
- 21% **marginal** statutory tax shield (not effective tax rate)
- Raises `ValueError` if neither bonds nor EBIT/interest are available (no silent fake AAA)
- `extract_ebit_and_interest()` supports Yahoo's period-major dict layout from `market_api`

**Verified:** MSFT EBIT ~$169B, interest ~$3.05B, coverage 55.4 → AAA; bond interpolation 8y/12y → 10y = 5.15%.

---

## 6. Quant Analyst node — `quant.py`

**File:** `src/equity_research/agents/quant.py`

- `fetch_ten_year_treasury_yield()` — live `^TNX` from Yahoo (percent ÷ 100)
- `quant_analyst_node(state)` — computes cost of debt, writes to `valuation_summary["cost_of_debt"]` and `valuation_summary["risk_free_rate"]`

**Important design correction:** `discount_rate` on state is reserved for **WACC**, not after-tax cost of debt. Cost of debt is stored under `valuation_summary`, not `discount_rate`.

**Verified:** Node runs end-to-end on MSFT income statement; after-tax Kd ~4.27% at live 10Y ~4.72%.

---

## 7. Dependencies — `requirements.txt`

```
yfinance>=0.2.40
pandas>=2.0.0
requests>=2.31.0
beautifulsoup4>=4.11.1
python-dotenv>=1.0.0
typing_extensions>=4.0.0
```

**Not yet added:** `langgraph`, `langchain-core` (orchestration graph not built yet).

---

## 8. Scratchpad test — `test_tool.py`

Root-level manual test script. Currently exercises SEC EDGAR (`fetch_latest_10k_text("AAPL")`).

Run from project root:
```bash
python test_tool.py
```

For agent/tool imports using the `equity_research` package name:
```bash
set PYTHONPATH=src   # Windows
python -c "from equity_research.agents.quant import quant_analyst_node"
```

---

## 9. Audit results (2026-08-29)

### ✅ What works (runtime-verified)

| Check | Result |
|-------|--------|
| All module imports | PASS |
| State field schema | PASS |
| Yahoo MSFT statements + EBIT/interest extraction | PASS |
| Bond YTM interpolation math | PASS |
| Synthetic path raises without inputs | PASS |
| SEC CIK lookup (AAPL) | PASS |
| Live ^TNX treasury yield | PASS |
| SEC 10-K excerpt (AAPL) | PASS |

### ⚠️ Gaps / not yet built (not bugs, but incomplete)

| Item | Status |
|------|--------|
| LangGraph `StateGraph` / `graph.py` | Not created |
| Data Aggregator agent | Not created |
| Qualitative, Reviewer, Lead Writer agents | Not created |
| Finnhub / TRACE bond YTM feed | Referenced in comments only |
| WACC / DCF calculation | Not implemented |
| `discount_rate` field | Defined on state, never populated |
| `sec_filing_chunks` wiring | Tool returns `str`; state expects `List[str]` — aggregator must wrap |
| `tests/` package | Scaffolded in plan but not on disk |

### ⚠️ Known limitations / methodology notes

| Issue | Detail |
|-------|--------|
| Damodaran spreads | Hardcoded snapshot (e.g. AAA 0.69%); Damodaran's Jan 2026 table shows ~0.40% for AAA. Refresh periodically from [his ratings page](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html). |
| Zero-debt firms | Coverage set to 999 → AAA spread; for WACC, debt weight should be ~0, not a full AAA cost. |
| Yahoo EBIT | Uses "Operating Income" as EBIT proxy; may differ from reported EBIT for some issuers. |
| SEC excerpt | 50k char slice, not full Item 1A; sufficient for prototyping, not for production RAG. |
| Import style | `test_tool.py` uses `src.equity_research.*`; agents use `equity_research.*` with `PYTHONPATH=src`. Standardize when packaging. |
| `EquityResearchState` | All keys required (no `total=False`); LangGraph nodes need sensible defaults at graph init. |

### ❌ Hallucinations / incorrect claims caught and corrected

| Claim | Reality |
|-------|---------|
| `data.sec.gov/files/company_tickers.json` | 404 — fixed to `www.sec.gov` |
| v2 `extract_ebit_and_interest` metric-major parser | Would fail on Yahoo period-major dicts already stored by `market_api` — fixed with dual-layout parser |
| "Save after-tax cost of debt into `discount_rate`" | Wrong — `discount_rate` is WACC; cost of debt goes to `valuation_summary` |
| Finnhub TRACE tool exists | Does not — only planned |
| LangGraph pipeline operational | State + tools + one agent node only; no compiled graph |

---

## 10. Suggested next steps

1. **Data Aggregator** (`agents/aggregator.py`) — call `market_api`, `sec_api`, populate state fields including `sec_filing_chunks=[text]`
2. **Bond feed** — implement TRACE/bond API → `outstanding_bonds` on state
3. **WACC + DCF** — extend Quant agent; write final `discount_rate`
4. **`graphs/graph.py`** — wire nodes with LangGraph `StateGraph`
5. **Add `langgraph` to requirements** and create `langgraph.json` deploy config
6. **Refresh Damodaran spreads** from latest data file
7. **Formal tests** under `tests/` with pytest

---

## 11. File inventory (current)

| Path | Role |
|------|------|
| `.env` / `.env.example` | Secrets template (local only) |
| `.gitignore` | Git ignore rules |
| `requirements.txt` | Python dependencies |
| `test_tool.py` | Manual SEC scratchpad test |
| `PROJECT_LOG.md` | This file |
| `src/equity_research/graphs/state.py` | Shared ledger schema |
| `src/equity_research/tools/market_api.py` | Yahoo financial statements |
| `src/equity_research/tools/sec_api.py` | SEC 10-K text extraction |
| `src/equity_research/tools/debt_analysis.py` | Cost of debt engine |
| `src/equity_research/agents/quant.py` | Quant Analyst node |
| `data/`, `outputs/`, `notebooks/` | Placeholder dirs for artifacts |

---

## 12. Hybrid bond architecture session (2026-08-29)

**New files:**
- `src/equity_research/tools/finnhub_bond.py` — Finnhub TRACE YTM fetch by ISIN
- `src/equity_research/agents/aggregator.py` — Data pull node (yfinance + SEC + optional bonds)
- `src/equity_research/graphs/defaults.py` — `initial_state()` factory with `is_math_verified=False`
- `README.md` — honest architecture doc (implemented vs planned)
- `IMPLEMENTATION_LOG.md` — detailed rationale for this session

**State update:** added `target_bonds: Optional[List[str]]` for ISIN input list.

**Verified:** MSFT aggregator → quant chain runs end-to-end (synthetic fallback when no Finnhub key/ISINs).

See `IMPLEMENTATION_LOG.md` for full design rationale.

---

## 13. WACC + dynamic 3-stage DCF session (2026-08-29)

Created `tools/valuation.py`, `tools/firm_classifier.py`, and deterministic
valuation tests. Extended `agents/quant.py` from cost-of-debt only to:

1. lifecycle classification,
2. CAPM cost of equity,
3. hybrid cost of debt,
4. capital-weighted WACC,
5. three-stage sales-to-capital FCFF valuation.

Quant now writes WACC to `discount_rate` and intrinsic value/share to
`calculated_dcf_value`. Full assumptions and projections remain auditable in
`valuation_summary`.

**Verified:** 5/5 unit tests and a live MSFT run. See `IMPLEMENTATION_LOG.md`
for methodology corrections and observed output.

---

## 14. Qualitative-to-Quant review layer (2026-08-29)

Added `tools/qual_to_quant.py` (the requested filename) and
`agents/reviewer.py`. Competitive and qualitative evidence can now produce
bounded, fully rationalized DCF overrides before Quant executes.

Quant consumes these overrides without conflating company-specific risk with
the market ERP. State now includes `qualitative_analysis_summary` and
`dcf_overrides`.

**Verified:** 9/9 unit tests pass and a live MSFT peer/risk scenario completed.
See `IMPLEMENTATION_LOG.md` for guardrails and limitations.

---

## 15. Qualitative Analyst + LangGraph integration (2026-08-30)

Created the evidence-grounded SEC Qualitative Analyst and compiled the first
complete LangGraph workflow. SEC Item 1A and Item 7 are now extracted
separately from one filing download.

**Verification:** 12/12 tests pass; live AAPL section extraction and an
end-to-end MSFT graph invocation both succeeded.

See `WORK_LOG.md` section 17 for details.

---

## 16. Review controls and reporting (2026-08-30)

Added post-Quant arithmetic review and bounded retry routing, a serializable
5x5 WACC/g sensitivity grid, a deterministic Markdown memo writer, and a
standalone regulatory-capital-constrained bank DDM.

The proposed automatic assumption changes used to force positive values or
terminal-value concentration below 85% were rejected as outcome-fitting.
Negative equity is preserved and terminal concentration is reported as a
warning.

**Verification:** 18/18 tests pass and the live MSFT graph generated a verified
memo. See `WORK_LOG.md` section 20.

---

## 17. Bank regulatory routing (2026-08-30)

Added sourced Item 7/8 regulatory metric extraction, provenance and confidence
tracking, automatic bank/corporate graph routing, a safe bank-DDM node, and CLI
support for reviewed bank assumptions. Synthetic RWA/CET1/asset fallbacks were
rejected. Low-confidence RWA candidates are withheld from valuation.

**Verification:** 28/28 tests pass; MSFT FCFF regression passes; JPM routes to
the bank branch and safely withholds an unreviewed valuation. See
`WORK_LOG.md` section 22.

---

## 18. Financial-firm path removed; FCFF-only desk (2026-08-30)

The bank DDM, FR Y-9C / iXBRL ingestion, and bank CLI flags were removed.
Yahoo Financial Services / Financials tickers skip valuation and write a
restrained out-of-scope memo. The production model is three-stage FCFF for
non-financial operating companies.

**Why:** A fabricated bank model was worse than an honest withhold. The desk
is not a bank-equity product.

## 19. Closed research desk (`d489374`, `75a7793`, 2026-08-30)

Operating P&L on the same fiscal path as FCFF. Evidence-gated bear/base/bull
(WACC held at base). Dated catalysts from Yahoo / 10-K only. Model versus
Street with a Python thesis spine. Shared URL detector (`www.` as well as
`http(s)`). Missing allow-lists no longer reopen `high` / `light`.

**Why:** The memo was quoting Street and writing catalysts without a Python
owner, and stretch labels could reopen when an allow-list was empty.

Documented lock-ins: Python owns WACC / DCF / P&L / PT; the LLM may not
invent tickers, DCF numbers, URLs, or citations. Runs are ticker-only.

## 20. Name → ticker and SEC facts (`c7ab2f9`, 2026-08-31)

`resolve_listed_symbol` maps a company name or alias to an SEC-listed ticker.
When Yahoo statements or 10-K quotes are thin, SEC companyfacts overlay the
ledger.

**Why:** Operators type names as well as tickers. Empty Yahoo / thin 10-K
left agents inventing or stalling. Facts already published at the SEC should
fill the ledger before anyone writes prose.

## 21. Company/products agent and allowlisted web research (`9190f19`, 2026-08-31)

New parallel node beside industry/macro and operations. Industry stays on
category demand/cycle. Company/products owns Item 1 products, mix, and firm
catalysts. `web_research.py` fetches allowlisted IR/SEC/high-quality pages;
the LLM copies quotes and `source_url` values already on that list.

**Why:** Industry views were mixing **this firm's products** with **category
TAM**. Market-size language lives on IR and news, not in the 10-K. The LLM
must not mint URLs.

## 22. Scale-up High-Growth lifecycle (`3e0dbfe`, 2026-08-31)

Classifier: P/S ≥ 15, CAGR ≥ 25%, and material revenue → `Scale-up High-Growth`
(8-year explicit, 20–50% base cap, terminal margin floored). Terminal margin
is no longer pulled below that floor.

**Why:** NBIS-like names were valued as mature 2–7% / 3-year rails. Last
year's P&L is not the firm the market is pricing. The desk still does not
reverse-engineer P/S into growth to match the tape.

## 23. Growth-path agent (`0fc21b0`, 2026-08-31)

Proposes extend / fade / scale for scale-up names (horizon, STC fade from
build-phase, margin path). Mature names get `not_applicable`. Overlay clips
the packet to the ledger.

**Why:** A scale-up needs a path, not last year's print copied forward.
Without this node the architect had no evidenced reason to extend years or
fade STC.

## 24. Labeled mix and stacked-recession cut (`91ce064`, 2026-08-31)

Labeled DCF/relative mix: `dcf_heavy` 90/10, `base` 70/30, `balanced` 55/45.
Python overwrites forged percentages. Scale-ups / P/S ≥ 15 / non-positive
EBITDA default to `dcf_heavy` and cannot pick `balanced`.

High-growth classification starts at 10% trailing CAGR (was 15%). Peer/target
trailing growth owns the industry cycle. Hostile macro requires downswing
**and** (negative inflection or below-history). Mature names stay on
classifier-base growth, years, and perpetuity *g* unless demand is actually
hostile. Heavy STC needs a lengthening CCC or heavy reinvestment. Distressed
EV/EBITDA outliers are dropped from the peer median. Mature terminal margin
holds current (no 5% fade).

**Why:** Live GUI runs of NBIS, TJX, TPR, and MNST all printed Sell.

- NBIS: DCF ~$177 vs ~$205 is Hold on DCF alone; 70/30 × trailing EV/EBITDA
  ~$18 pulled FV to a Sell. Trailing EV/EBITDA is a poor descriptor on
  scale-ups.
- TJX: a Yahoo snippet about Walmart/Home Depot consumers tagged downswing
  while TJX CAGR was 6.5% and category `in_line`. That stacked 2% growth,
  2 years, 1.5% *g*, and heavy STC. A Kohl’s-type 5× multiple was also
  pulling the off-price peer median.

The desk does not manufacture Buys. Mid-single-digit compounders stay mature.

## 25. Assumption auditor (`3ced79d`, 2026-08-31)

Independent node after the reviewer and before Quant. Python overlay plus
LLM. May only **revert** labels to the classifier baseline. Memo auditor
(`independent_auditor`) stays last: citations, invented tickers, frozen
figures. It cannot re-decide growth, years, or perpetuity *g*.

**Why:** The user asked for a second independent auditor. Putting two agents
on the same accept/reject pass would rubber-stamp each other.

**Verification:** 218 unit tests passing.

---

*End of log. Live graph, lock-ins, and hallucination controls: [`FINAL_MODEL.md`](FINAL_MODEL.md).*

