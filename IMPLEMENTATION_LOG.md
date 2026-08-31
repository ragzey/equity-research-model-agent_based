# Implementation Log — Hybrid Bond Architecture

**Date:** 2026-08-31 (sections through assumption auditor). Earlier dated blocks are historical.  
**Session goal:** Wire the hybrid fixed-income strategy (Finnhub TRACE primary + Damodaran fallback) into working agent nodes, with honest documentation.

---

## What was implemented

### 1. `src/equity_research/tools/finnhub_bond.py` (NEW)

**Why:** The other chatbot’s architecture called for a **gold-standard market YTM path** via FINRA TRACE. Finnhub exposes TRACE ticks through `/bond/tick` and bond metadata through `/bond/profile`.

**What it does:**
- Accepts a list of **corporate bond ISINs** (`target_bonds`) — Finnhub cannot discover all bonds from an equity ticker alone.
- For each ISIN: fetches `maturityDate` + latest TRACE yield (`y` field, converted from percent to decimal).
- Returns `[{isin, maturity_years, ytm}, ...]` for `debt_analysis.interpolate_bond_yields()`.

**Why ISINs, not ticker:** Finnhub’s bond API is identifier-driven (ISIN/CUSIP/FIGI). Equity tickers do not map to a bond universe endpoint. ISINs typically come from 10-K debt footnotes or a security master.

**Failure behavior:** Missing `FINNHUB_API_KEY` raises a clear `ValueError`; aggregator catches this and sets `outstanding_bonds=None` so the Damodaran path still runs.

---

### 2. `src/equity_research/agents/aggregator.py` (NEW)

**Why:** Needed a dedicated node to populate the shared ledger before quant math runs — the “junior intern” that gathers raw inputs.

**What it does:**
1. Calls `fetch_financial_statements(ticker)` → saves income/balance/cash-flow to state.
2. Calls `fetch_latest_10k_text(ticker)` → wraps excerpt in `sec_filing_chunks: [text]` (state expects `List[str]`).
3. If `target_bonds` ISINs exist → calls `get_outstanding_bonds_for_ticker()` → saves `outstanding_bonds`.
4. If no ISINs → explicitly sets `outstanding_bonds=None` (triggers synthetic fallback downstream).

**Why SEC in aggregator:** Qualitative agents will need filing text; pulling it here avoids duplicate EDGAR calls later.

---

### 3. `src/equity_research/graphs/state.py` (UPDATED)

**Added:** `target_bonds: Optional[List[str]]` — input list of bond ISINs for the TRACE path.

**Why:** Separates *user-supplied bond identifiers* from *parsed market quotes* (`outstanding_bonds`). Keeps the ledger explicit about what was requested vs what was resolved.

---

### 4. `src/equity_research/graphs/defaults.py` (NEW)

**Why:** `EquityResearchState` requires all keys (no `total=False`). LangGraph runs need a single factory for safe defaults (`is_math_verified=False`, `revision_count=0`, etc.).

**Function:** `initial_state(ticker, target_year, target_bonds=None)`.

---

### 5. `src/equity_research/agents/quant.py` (UNCHANGED logic, clearer logging)

**Why not change `discount_rate`:** After-tax cost of debt is a **WACC input**, not WACC itself. Storing Kd in `discount_rate` would mislabel the field to recruiters and break future WACC assembly.

**Output location:** `valuation_summary["cost_of_debt"]` + `valuation_summary["risk_free_rate"]`.

**Logging:** Now explicitly logs which pathway fired (Interpolation vs Synthetic).

---

### 6. `README.md` (NEW)

**Why:** Portfolio-ready documentation that distinguishes **implemented** vs **planned** (OpenBB, full LangGraph graph, DCF). Avoids claiming Finnhub/OpenBB integration that did not exist.

Includes architecture diagram, setup, ISIN guidance, and test commands.

---

### 7. Config updates

| File | Change | Why |
|------|--------|-----|
| `.env.example` | Added `FINNHUB_API_KEY` | Required for TRACE path |
| `tools/__init__.py` | Export `get_outstanding_bonds_for_ticker` | Clean imports |
| `agents/__init__.py` | Export `aggregator_node` | Clean imports |
| `graphs/__init__.py` | Export `initial_state` | Graph bootstrap |

---

## What was deliberately NOT implemented (and why)

| Item | Reason |
|------|--------|
| **OpenBB** | Not in requirements; yfinance + requests already work. README marks it as planned. |
| **LangGraph `StateGraph`** | User asked for tool + agent wiring; graph compilation is the next sprint. |
| **Saving Kd to `discount_rate`** | Methodologically wrong; reserved for WACC. |
| **Auto-discovery of bond ISINs by ticker** | Finnhub API does not support it; would be hallucinated functionality. |
| **numpy in debt_analysis** | Avoided extra dependency; pure-Python interpolation already works. |

---

## Data flow (end-to-end)

```
initial_state(MSFT, 2025, target_bonds=[...])
        │
        ▼
aggregator_node
  ├─ market_api  → income_statement, balance_sheet, cash_flow_statement
  ├─ sec_api     → sec_filing_chunks
  └─ finnhub_bond (if ISINs + API key) → outstanding_bonds
        │
        ▼
quant_analyst_node
  ├─ ^TNX live risk-free rate
  ├─ if outstanding_bonds → interpolate YTMs → after-tax Kd
  └─ else → extract EBIT/interest → Damodaran → after-tax Kd
        │
        ▼
valuation_summary.cost_of_debt
```

---

## How to verify

```bash
set PYTHONPATH=src
python -c "
from equity_research.graphs.defaults import initial_state
from equity_research.agents.aggregator import aggregator_node
from equity_research.agents.quant import quant_analyst_node

state = initial_state('MSFT', '2025')
state.update(aggregator_node(state))
state.update(quant_analyst_node(state))
print(state['valuation_summary']['cost_of_debt'])
"
```

With `FINNHUB_API_KEY` and valid ISINs in `target_bonds`, the log should show **Linear Interpolation (Market-Implied)**. Without, it should show **Synthetic Credit Rating (Fallback)**.

---

## Files touched this session

| Action | Path |
|--------|------|
| Created | `src/equity_research/tools/finnhub_bond.py` |
| Created | `src/equity_research/agents/aggregator.py` |
| Created | `src/equity_research/graphs/defaults.py` |
| Created | `README.md` |
| Created | `IMPLEMENTATION_LOG.md` |
| Updated | `src/equity_research/graphs/state.py` |
| Updated | `src/equity_research/agents/quant.py` |
| Updated | `src/equity_research/tools/__init__.py` |
| Updated | `src/equity_research/agents/__init__.py` |
| Updated | `src/equity_research/graphs/__init__.py` |
| Updated | `.env.example` |

---

## Double-check fixes (2026-08-29)

| Issue | Fix |
|-------|-----|
| Missing `FINNHUB_API_KEY` swallowed inside per-ISIN loop | `_api_key()` called at start of `get_outstanding_bonds_for_ticker()` so `ValueError` reaches aggregator |
| Quant skipped EBIT parse when `outstanding_bonds` was truthy but malformed | Quant now always extracts EBIT/interest when statements exist; synthetic fallback works if interpolation fails |
| README used unverified MSFT ISINs | Replaced with generic example format |
| `outstanding_bonds` type hint too strict (`Dict[str, float]`) | Relaxed to `Dict[str, Any]` to allow `isin` string field |

## Competitive Analyst session (2026-08-29)

**New files:**
- `tools/peer_analysis.py` — yfinance P/E, EV/EBITDA, margins, revenue growth + peer medians
- `utils/llm_synthesis.py` — LLM industry outlook (OpenAI) with deterministic fallback
- `agents/competitive.py` — `competitive_analyst_node`

**State fields added:** `competitor_tickers`, `peer_metadata`, `peer_comparison_matrix`, `industry_outlook`

**Aggregator update:** pre-fetches `peer_metadata` when `competitor_tickers` is set.

**Verified:** MSFT vs GOOGL/AAPL/ORCL — matrix + deterministic outlook generated.

**Accuracy notes:**
- Metrics come from yfinance `.info` (can be null for some sectors)
- LLM outlook requires `OPENAI_API_KEY`; otherwise rule-based summary from peer medians
- Porter/SWOT framing is prompt-guided for LLM path, not a separate structured output schema

---

## WACC, lifecycle classifier, and 3-stage DCF (2026-08-29)

### Added

- `tools/valuation.py`
  - Validated market-equity/book-debt WACC.
  - Three-stage FCFF projection using incremental sales-to-capital reinvestment.
  - Transition assumptions reach terminal values exactly in the last transition year.
  - Terminal FCFF uses the same sales-to-capital framework as explicit forecasts.
  - Rejects terminal WACC ≤ growth and spreads below 1%.
- `tools/firm_classifier.py`
  - Reads the actual Yahoo period-major dictionary produced by `market_api.py`.
  - Calculates up to three-year revenue CAGR and latest operating margin.
  - Assigns bounded lifecycle policy assumptions and flags financial-services firms as unsupported for this FCFF model.
- `agents/quant.py`
  - Retrieves reviewed live market inputs without fabricated `$100`/`1 share` defaults.
  - Computes CAPM, hybrid cost of debt, WACC, and three-stage DCF.
  - Writes WACC to `discount_rate`, value/share to `calculated_dcf_value`, and full audit detail to `valuation_summary`.
- `tests/test_valuation.py`
  - Five deterministic tests covering Yahoo data shape, classification, zero-debt WACC, terminal transitions, and unsafe terminal spreads.

### Corrections versus the proposed external code

- Fixed metric-major versus Yahoo period-major statement mismatch.
- Fixed invalid `rev_history[sorted_dates]` indexing.
- Removed unused NumPy dependency.
- Rejected missing critical inputs instead of inventing price, shares, revenue, or beta.
- Replaced terminal growth = live Treasury with a bounded 1.5–2.5% policy.
- Replaced `min(8%, WACC)` terminal WACC rule with a minimum 2% WACC-growth spread.
- Used `transition_step / transition_years`, avoiding a terminal-year assumption jump.
- Kept after-tax Kd separate and wrote actual WACC to `discount_rate`.
- Uses book debt as an explicitly disclosed proxy, not as claimed market debt.

### Verification

- Unit tests: **5/5 passing**.
- Live MSFT run: WACC **10.62%**, DCF value/share **$389.41**, classification **High-Growth Large-Cap**, terminal value **58.5%** of enterprise value.
- These figures are model outputs under heuristic assumptions—not price targets or investment recommendations.

---

## Qualitative-to-quantitative translation layer (2026-08-29)

### Added

- `tools/qual_to_quant.py` — converts peer profitability, explicit risk phrases,
  and saturation evidence into bounded DCF overrides.
- `agents/reviewer.py` — pre-Quant assumption reviewer that writes
  `dcf_overrides`; it deliberately does not mark arithmetic as verified.
- State fields: `qualitative_analysis_summary` and `dcf_overrides`.
- Quant now applies reviewed terminal margin, growth horizon, market ERP, and a
  direct company-specific risk premium.

### Accuracy corrections versus the proposed external code

- Reads the repository's actual peer matrix (`metrics` and
  `operating_margin_pct`), not a nonexistent `peers` structure.
- Keeps market ERP at the market level and adds company-specific risk directly
  to cost of equity. A 75 bp risk bump is therefore 75 bp—not beta × 75 bp.
- Phrase matching is labelled decision support, not independent risk
  measurement; rationales are retained for review.
- Terminal-margin uplift requires a 3 percentage-point peer advantage and is
  capped at the lower of current margin, 30%, or baseline +3 percentage points.
- Growth headwinds can shorten but never extend the classifier horizon.
- Transition years remain a classifier decision; they are not automatically
  forced equal to high-growth years.

### Verification

- Test suite: **9/9 passing**.
- Live MSFT peer/risk flow: 75 bp company risk premium, terminal margin capped
  at 30%, WACC 11.36%, illustrative DCF value/share $374.10.
- Outputs are heuristic scenario results, not independently verified price
  targets. A dedicated Qualitative Analyst and compiled LangGraph remain to be
  built.

---

## SEC Qualitative Analyst and graph integration (2026-08-30)

- Replaced single-excerpt SEC processing with one-download, separate Item 1A
  and Item 7 extraction.
- Added `agents/qualitative.py` with source-constrained LLM analysis and an
  evidence-only deterministic fallback.
- Rejected the proposed “use historical model knowledge when SEC is down”
  behavior because it would create unsourced filing claims and potentially
  trigger unsupported WACC adjustments.
- Added `graphs/graph.py`:
  `Aggregator → [Competitive, Qualitative] → Reviewer → Quant → END`.
- Added `langgraph>=1.2.11`.
- Added section-extraction, no-evidence, and graph-compilation tests.

**Verified:** 12/12 unit tests, separate live AAPL sections, and a complete
compiled MSFT graph invocation.

---

## Closed FCFF desk and report pack (2026-08-30)

**Why this block exists:** after Qualitative + the first compiled graph, the
desk still lacked industry/operations packets, labeled assumption menus,
an independent memo auditor, operating P&L, scenarios, catalysts, and
Street comparison. Those landed in `d489374` / `75a7793`.

- Industry/macro and assumption architect: categorical views and labeled
  menus; stretch labels need ledger reasons; economy-linked terminal *g*.
- Operations: Python CCC / NWC / STC; banks skip the corporate working-
  capital path.
- Independent memo auditor: citations and invented tickers; cannot rewrite
  WACC or DCF.
- Operating P&L on the same fiscal path as unlevered FCFF. Bear/base/bull
  change operating labels only; WACC stays on base.
- Dated catalysts from Yahoo / 10-K. Model versus Street with a Python
  thesis spine.
- Hallucination close: `www.` as well as `http(s)`; empty allow-lists do
  not reopen `high` / `light`.

Financial-firm DDM / FR Y-9C work was **removed**. Yahoo Financials skip
FCFF. **Why:** a fabricated bank model is worse than an honest withhold.

---

## Name → ticker, SEC facts, products, web research (2026-08-31)

| Piece | Why |
| --- | --- |
| `resolve_listed_symbol` | Operators type names; runs must still be a listed ticker |
| SEC companyfacts overlay | Yahoo / 10-K quotes can be thin; do not invent statements |
| `company_products` node | Industry was mixing SKUs with category demand |
| `web_research.py` | Market size lives on IR/web; LLM must not mint URLs |

Python fetches allowlisted hosts. Agents copy `source_url` values already
on that list.

---

## Scale-up lifecycle and growth-path (2026-08-31)

**Why:** high P/S hyper-growth names (NBIS-class) were parameterized as
mature compounders. Last year's P&L is not the firm the market is pricing.

- `Scale-up High-Growth` when P/S ≥ 15, CAGR ≥ 25%, and revenue is material.
- Base cap 50%; stretch 80% only with a **forward** sales-growth estimate.
- Terminal margin floored so later overlays cannot pull it under the
  classifier.
- Growth-path agent proposes extend / fade / scale. Mature → `not_applicable`.

Do not reverse-engineer P/S into growth. Do not invent TAM.

---

## Labeled mix and stacked-recession cut (2026-08-31)

**Why:** GUI runs of NBIS, TJX, TPR, and MNST all printed Sell.

**NBIS.** DCF ~$177 vs ~$205 last (~−14%, Hold on DCF). 70/30 × trailing
EV/EBITDA ~$18 → FV ~$129 Sell. Mix is now a label. `dcf_heavy` = 90/10 when
P/S ≥ 15, scale-up, or EBITDA non-positive. Overlay overwrites forged
floats. Scale-ups cannot pick `balanced`.

**TJX.** Industry tagged downswing from a Walmart/Home Depot consumer
snippet while TJX CAGR was 6.5% and category `in_line`. That stacked 2%
growth, 2-year horizon, 1.5% *g*, and heavy STC. Fixes:

- Cycle = peer/target trailing growth median.
- Negative inflection cleared when cycle is mid/upswing and category is not
  `below_history`.
- `_hostile_macro` = downswing **and** (negative inflection **or**
  below-history).
- Mature stays on classifier-base growth, years, and *g* unless hostile.
- Heavy STC needs lengthening CCC or heavy reinvestment.
- Distressed EV/EBITDA outliers dropped from the peer median (Kohl’s-type
  5× must not pull an off-price set).
- Mature terminal margin holds current.

High-growth cutoff: **10%** trailing CAGR (was 15%). High-growth / scale-up
cannot pick `low` / `compress` without a real hostile packet; they get
`extend` without a constructive industry packet.

---

## Assumption auditor (2026-08-31)

**Why:** a second independent check was requested. Reviewer accept/reject
and memo-auditor comments on the same labels would rubber-stamp each other.

- Node sits after reviewer, before Quant.
- Python overlay + LLM.
- Action: **revert to classifier baseline only**. No new rates, no DCF
  numbers.
- Memo auditor stays last: narrative, citations, frozen PT/FV/WACC/rating.
  It does not re-decide growth, years, or perpetuity *g*.

**Verified:** 218 unit tests (`python -m unittest discover -s tests -q`).
Live graph:

```text
Aggregator → Competitive ∥ Qualitative
  → Industry/macro ∥ Company/products ∥ Operations
  → Growth-path → Valuation-mix → Router
  → Architect → Reviewer → Assumption auditor
  → Quant → Post-quant → Sensitivity → Writer → Memo auditor
```

---

*End of implementation log. Source of truth for the live desk: [`FINAL_MODEL.md`](FINAL_MODEL.md).*

