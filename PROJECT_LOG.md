# Equity Research Pipeline — Project Log

**Last updated:** 2026-08-29  
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

*End of log.*
