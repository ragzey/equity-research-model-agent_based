# Equity Research Pipeline — Work Log

**Project:** `C:\Equity research model`  
**Log date:** 2026-08-29

## 1. Initial project scaffold

- Created the Python source layout under `src/equity_research/`.
- Created folders for agents, graphs, tools, prompts, configuration, utilities,
  tests, notebooks, data, and report outputs.
- Added Python `__init__.py` files.
- Added `.gitignore` for secrets, virtual environments, caches, downloaded
  financial data, and generated reports.
- Added `.env.example` for API credentials and service configuration.
- Added `requirements.txt`.

## 2. Shared state ledger

Created `src/equity_research/graphs/state.py` with
`EquityResearchState`.

The state currently stores:

- ticker and target year;
- target bond ISINs and competitor tickers;
- income statement, balance sheet, and cash-flow statement;
- SEC filing chunks and recent news;
- structured outstanding bond quotes;
- peer metadata and peer comparison matrix;
- industry and qualitative summaries;
- DCF overrides;
- WACC, DCF value, and valuation details;
- review status, feedback, and revision count;
- final report path.

Created `graphs/defaults.py` with `initial_state()` so every run begins with
explicit defaults, including:

- `is_math_verified=False`;
- `revision_count=0`;
- all not-yet-populated outputs set to `None`.

## 3. Yahoo Finance market-data tool

Created `tools/market_api.py`.

- Fetches annual income, balance-sheet, and cash-flow statements through
  `yfinance`.
- Returns nested dictionaries for storage in the shared state.
- Detects empty data and invalid tickers.
- Adds execution logging and exception handling.

Verified the tool against Microsoft.

## 4. SEC EDGAR tool

Created `tools/sec_api.py`.

- Maps ticker to SEC CIK.
- Retrieves company submissions.
- Finds the latest 10-K.
- Downloads and cleans filing HTML.
- Extracts an Item 1A Risk Factors excerpt, with Item 7 MD&A as fallback.
- Reads `SEC_USER_AGENT` from `.env`.
- Adds request timeouts and a polite delay.

Corrections made:

- changed the ticker-map URL to
  `https://www.sec.gov/files/company_tickers.json`;
- used an unpadded CIK in archive URLs;
- skipped table-of-contents Item 1A matches.

Verified the tool against Apple and Microsoft filings.

## 5. Cost-of-debt engine

Created `tools/debt_analysis.py`.

- Interpolates structured bond YTMs to a target maturity.
- Supports `{maturity_years, ytm}` and
  `{years_to_maturity, yield}` inputs.
- Falls back to Damodaran interest-coverage synthetic ratings.
- Extracts EBIT and absolute interest expense from Yahoo statements.
- Uses a marginal tax rate for the debt tax shield.
- Rejects insufficient inputs instead of silently assigning a rating.

Corrected the parser to support the actual Yahoo period-major statement shape:

```text
{period: {line_item: value}}
```

## 6. Finnhub TRACE bond tool

Created `tools/finnhub_bond.py`.

- Accepts supplied corporate bond ISINs.
- Retrieves bond profiles and maturity dates.
- Requests FINRA TRACE ticks through Finnhub.
- Converts quoted yield percentages to decimals.
- Produces structured inputs for the debt interpolation function.
- Fails clearly when `FINNHUB_API_KEY` is absent.

Documented that Finnhub does not discover a company’s complete bond set from
an equity ticker; ISINs must be supplied from filings or a security master.

## 7. Data Aggregator agent

Created `agents/aggregator.py`.

The node:

- fetches target-company statements;
- retrieves an SEC 10-K excerpt;
- optionally fetches TRACE bond yields when ISINs are supplied;
- logs and pre-fetches target and competitor metadata;
- returns a partial state update suitable for LangGraph.

## 8. Competitive Analyst

Created:

- `tools/peer_analysis.py`;
- `agents/competitive.py`;
- `utils/llm_synthesis.py`.

The peer tool gathers:

- trailing P/E;
- forward P/E;
- EV/EBITDA;
- operating margin;
- year-over-year revenue growth;
- company metadata and market capitalization;
- peer-group medians.

The Competitive Analyst writes:

- `peer_comparison_matrix`;
- `industry_outlook`.

When `OPENAI_API_KEY` is configured, the industry outlook can use an LLM.
Without it, the system generates a deterministic comparison summary.

Verified with Microsoft against Google, Apple, and Oracle.

## 9. Firm lifecycle classifier

Created `tools/firm_classifier.py`.

- Reads the Yahoo period-major statement structure.
- Calculates up to three years of historical revenue CAGR.
- Calculates the latest operating margin.
- Classifies firms using transparent market-cap and growth heuristics.
- Produces bounded assumptions for:
  - high-growth rate;
  - size premium;
  - sales-to-capital ratio;
  - high-growth and transition periods;
  - terminal margin;
  - stable sales-to-capital ratio.
- Flags financial-services firms as unsupported by the current FCFF model.

Corrected issues found in externally proposed code:

- invalid revenue-history indexing;
- unsupported statement orientation;
- unbounded use of historical growth;
- sector-blind application of FCFF.

## 10. WACC and three-stage DCF engine

Created `tools/valuation.py`.

Implemented:

- market-value-equity/book-debt-proxy WACC;
- high-growth and transition-stage FCFF projections;
- sales-to-capital reinvestment;
- linearly transitioning growth, margins, reinvestment efficiency, and WACC;
- stable terminal FCFF;
- enterprise-to-equity bridge using cash and debt;
- intrinsic equity value per share.

Safeguards include:

- required positive price, shares, and revenue;
- no fabricated `$100`, `1 share`, or `$1 revenue` defaults;
- terminal WACC must exceed terminal growth;
- minimum 1% WACC-growth spread;
- transition assumptions reach terminal assumptions exactly;
- terminal reinvestment uses the same sales-to-capital framework as the
  explicit forecast.

## 11. Quant Analyst

Expanded `agents/quant.py` from cost-of-debt only to the full quantitative
pipeline.

It now:

1. reads live price, shares outstanding, beta, and `^TNX`;
2. classifies the company;
3. extracts debt and cash;
4. calculates TRACE-based or synthetic cost of debt;
5. calculates CAPM cost of equity;
6. calculates WACC;
7. runs the three-stage FCFF DCF;
8. writes:
   - WACC to `discount_rate`;
   - intrinsic value per share to `calculated_dcf_value`;
   - assumptions, projections, and audit details to `valuation_summary`.

The after-tax cost of debt remains a WACC input under
`valuation_summary["cost_of_debt"]`; it is not incorrectly stored as the
discount rate.

## 12. Qualitative-to-quantitative translation layer

Created `tools/qual_to_quant.py`.

This replaced the proposed filename `investment_committee.py`.

It converts peer and qualitative evidence into bounded DCF overrides:

- peer profitability can adjust terminal margin;
- explicit regulatory and operational phrases can add a company-specific
  risk premium;
- saturation or structural-decline phrases can shorten the high-growth
  period.

Guardrails:

- target margin must exceed peer median by at least three percentage points;
- terminal-margin uplift is capped at baseline +3 percentage points;
- terminal margin cannot exceed current margin or 30%;
- regulatory risk adds at most 75 basis points;
- operational risk adds at most 50 basis points;
- combined company-specific premium is capped at 125 basis points;
- growth horizon cannot fall below two years;
- all rationales are retained in state.

The company-specific premium is added directly to cost of equity. It is not
embedded in market ERP and multiplied by beta.

## 13. Valuation assumption reviewer

Created `agents/reviewer.py`.

- Runs after Aggregator and Competitive Analyst but before Quant.
- Calls the qualitative-to-quantitative translator.
- Stores reviewed assumptions in `dcf_overrides`.
- Does not set `is_math_verified`; post-calculation arithmetic review remains
  a separate future responsibility.

Updated Quant to validate and apply reviewed overrides.

## 14. Tests and verification

Created:

- `tests/test_valuation.py`;
- `tests/test_qual_to_quant.py`.

Current deterministic test result:

```text
9 tests run
9 tests passed
```

Coverage includes:

- Yahoo statement orientation;
- revenue CAGR and firm classification;
- zero-debt WACC;
- transition-stage endpoint behavior;
- terminal WACC-growth safeguards;
- current peer-matrix shape;
- company-specific risk-premium cap;
- growth-horizon compression;
- reviewer override generation.

Live checks performed:

- Yahoo statements: MSFT;
- SEC filing extraction: AAPL and MSFT;
- Treasury yield: `^TNX`;
- peer comparison: MSFT versus GOOGL/AAPL/ORCL;
- baseline MSFT WACC/DCF;
- MSFT qualitative-risk override scenario.

The live outputs are illustrative model results, not verified investment price
targets.

## 15. Documentation and configuration

Created or updated:

- `README.md`;
- `PROJECT_LOG.md`;
- `IMPLEMENTATION_LOG.md`;
- `.env.example`;
- module export files.

Added environment placeholders for:

- LLM providers;
- Finnhub;
- SEC identity;
- market-data/search providers;
- LangSmith.

## 16. Current outstanding work

- Build a post-Quant arithmetic reviewer and revision loop.
- Build the final Writer agent.
- Add WACC/terminal-growth sensitivity and scenario matrices.
- Add analyst-consensus and management-guidance forecasts.
- Add bond-ISIN discovery.
- Refresh the hardcoded Damodaran spread table periodically.
- Calibrate classifier and qualitative override policies.
- Add separate valuation approaches for banks and insurers.

## 17. Qualitative Analyst and compiled LangGraph

Updated `tools/sec_api.py`:

- downloads the latest 10-K once;
- extracts separate Item 1A and Item 7 sections;
- preserves filing URL, date, and accession metadata;
- exposes `fetch_latest_10k_sections()` and `fetch_sec_section()`;
- retains `fetch_latest_10k_text()` for backward compatibility.

Updated `agents/aggregator.py` to store both sourced sections in
`sec_filing_chunks`.

Created `agents/qualitative.py`:

- reads the SEC sections already fetched by Aggregator;
- requests them directly only when state has no filing evidence;
- uses an evidence-constrained OpenAI prompt when configured;
- uses a deterministic source-sentence fallback without an API key;
- never substitutes LLM historical knowledge when SEC evidence is unavailable;
- writes `qualitative_analysis_summary`.

Created `graphs/graph.py` with the flow:

```text
Aggregator
   ├─ Competitive Analyst ─┐
   └─ Qualitative Analyst ─┴─ Reviewer → Quant → END
```

Competitive and Qualitative run in parallel. Reviewer waits for both.

Installed and declared `langgraph>=1.2.11`.

Verification after this change:

- full deterministic suite: **12/12 passing**;
- live AAPL extraction: Item 1A = 50,000 characters, Item 7 = 50,000
  characters;
- compiled MSFT graph:
  - qualitative summary: 649 characters;
  - two peers processed;
  - DCF overrides generated;
  - WACC: 10.62%;
  - illustrative DCF value/share: $389.41.

## 18. Competitive Analyst re-audit

Confirmed that the Competitive Analyst, state fields, peer tool, parallel graph
branch, and Reviewer join were already implemented.

Corrections made during the re-audit:

- did not replace the working peer function with the nonexistent proposed
  `extract_competitor_multiples`; the repository uses
  `build_peer_comparison_matrix`;
- made peer metric failures isolated per ticker rather than failing the entire
  peer matrix;
- made Aggregator always fetch target metadata, allowing the graph to complete
  even when no competitor list is supplied;
- constrained the competitive LLM to supplied metrics and SEC evidence;
- prohibited claims about market share, barriers, saturation, or price erosion
  based on multiples alone;
- set the competitive LLM temperature to zero;
- passed both Item 1A and Item 7 context to competitive synthesis.

Verification:

- **12/12 tests pass**;
- compiled MSFT graph completes with no competitor tickers;
- no-peer result correctly leaves `peer_comparison_matrix=None` while WACC and
  DCF still complete.

## 19. Central orchestrator and CLI re-audit

Confirmed `graphs/graph.py` already contained the correct LangGraph topology.
The external proposal was not pasted because:

- it referenced nonexistent functions (`data_aggregator_node`,
  `assumption_reviewer_node`, and `init_research_state`);
- two separate Reviewer incoming edges do not express the intended explicit
  all-predecessor join as clearly as the existing list-based edge;
- its state initializer omitted the required `target_year`;
- it printed the entire classification dictionary instead of `firm_type`;
- it described a semantic RAG database that is not present;
- it predicted antitrust and moat overrides before inspecting sourced evidence.

Created root `main.py` using the actual project APIs. It supports:

- `--ticker`;
- optional `--target-year`;
- optional `--peers`;
- optional `--target-bonds`;
- configurable log level;
- concise output with classification, WACC, illustrative DCF value, terminal
  value share, and override rationales.

Additional fixes:

- SEC now warns when `SEC_USER_AGENT` still contains a placeholder.
- README now contains verified PowerShell CLI instructions.

Verification:

- CLI help exits successfully;
- **12/12 tests pass**;
- requested command
  `python main.py --ticker MSFT --peers AAPL GOOGL AMZN`
  completed successfully;
- output: High-Growth Large-Cap, WACC 10.62%, illustrative DCF $389.41/share,
  terminal value 58.5% of enterprise value;
- no antitrust premium was added because sourced evidence did not meet the
  configured phrase threshold.

## 20. Post-Quant review, sensitivity, writer, and bank DDM

Created `agents/post_quant_reviewer.py`:

- reconciles enterprise value to equity value and equity value per share;
- verifies finite outputs and the minimum terminal WACC-growth spread;
- flags terminal value above 85% of enterprise value as a warning;
- preserves negative raw equity values as distress signals;
- retries only recomputable integrity failures, capped at three;
- does not alter growth or reinvestment assumptions merely to force a passing
  valuation.

Created a serializable 5x5 sensitivity engine:

- WACC scenarios: calculated WACC ±100 bps in 50 bp steps;
- terminal growth: 1.50%-2.50% in 25 bp steps;
- initial and terminal WACC shift together to preserve transition structure;
- stored as lists/dictionaries rather than a pandas DataFrame so LangGraph
  checkpoint serialization remains safe.

Created `agents/writer.py`:

- compiles the validated ledger into a Markdown memo;
- includes valuation, capital costs, peer data, SEC evidence, override
  rationales, review findings, and sensitivity;
- labels raw negative equity and a zero limited-liability display floor;
- reports a descriptive model-implied valuation signal rather than an
  unsupported Buy/Hold/Sell recommendation.

Created `tools/bank_valuation.py`:

- observed-levered-beta cost of equity for banks;
- regulatory-capital-constrained three-stage DDM;
- sustainable dividends equal net income less incremental RWA capital
  retention at a target CET1 ratio;
- explicitly documents omitted buffers, stress constraints, issuance,
  buybacks, and jurisdiction-specific rules;
- remains standalone until bank regulatory data ingestion is implemented.

Updated the LangGraph:

```text
Aggregator
  → [Competitive + Qualitative]
  → Assumption Reviewer
  → Quant
  → Post-Quant Reviewer
      ├─ retryable integrity error → Quant (maximum 3)
      └─ pass/warn/stop → Sensitivity → Writer → END
```

Additional evidence corrections:

- excluded Private Securities Litigation Reform Act / forward-looking
  safe-harbor boilerplate from deterministic litigation evidence;
- changed target-versus-peer margin review to use comparable yfinance margin
  fields when available.

Verification:

- **18/18 tests pass**;
- live MSFT graph completed with arithmetic status `VERIFIED`;
- generated
  `outputs/reports/MSFT_2026-08-30_memo.md`;
- WACC 10.62%, illustrative DCF $389.41/share, terminal value 58.5% of EV;
- sensitivity center cell reconciles to the base DCF.

## 21. Advanced-blueprint accuracy audit

Reviewed the proposed "institutional-grade" blueprint and retained only
defensible controls.

Added:

- persistent negative FCFF screening at 70% of explicit forecast years;
- a high-severity cash-flow durability warning and dedicated memo section;
- explicit clarification that negative FCFF is not, by itself, evidence of
  bankruptcy or a going-concern qualification;
- section-tagged direct SEC evidence and source filing URL/date/accession;
- direct-source evidence as the preferred input to qualitative risk rules;
- full Year 1-10 Revenue/EBIT/NOPAT/Reinvestment/FCFF memo table;
- base-case highlighting in the 5x5 sensitivity matrix;
- bank terminal payout identity, implied ROE on required CET1, and terminal
  excess-return diagnostics.

Rejected or corrected:

- negative DCF equity is not automatically recalibrated into a positive target
  or a forced Sell rating;
- terminal value above 85% is not a universal mathematical failure;
- growth and sales-to-capital are not changed merely to pass an output test;
- CAPM company-specific premiums remain explicitly labelled policy heuristics;
- 12.5% CET1 is not treated as a universal regulatory requirement;
- `1 - 2.5% / 10.0%` equals a 75.0% payout ratio, not 77.3%;
- automatic bank routing remains disabled until reliable RWA, CET1, buffer,
  and jurisdiction-specific regulatory data are ingested.

Verification:

- **21/21 automated tests pass**;
- live MSFT graph completed with arithmetic status `VERIFIED`;
- memo contains source filing link, explicit projections, and highlighted
  sensitivity base case.

## 22. Bank regulatory ingestion and automatic routing

Audited the proposed regulatory-ingestion design. The suggested fallback rules
were rejected because they manufacture valuation-critical facts:

- RWA is not reliably 68% of total assets;
- ROE does not determine a bank's required or target CET1 ratio;
- market capitalization multiplied by 8 is not a defensible total-assets
  estimate;
- a 2.5% capital conservation buffer is not the complete binding requirement
  for every institution or jurisdiction.

Implemented `tools/bank_regulatory_ingestion.py`:

- parses reported CET1 ratios, RWA amounts, and disclosed conservation/stress
  capital buffers from supplied SEC text;
- handles trillion/billion/million units;
- ranks actual/reported candidates above regulatory minima;
- penalizes component RWA and change disclosures;
- rejects low-confidence RWA candidates for valuation;
- stores evidence excerpts, candidate counts, selection confidence, source
  filing metadata, and a human-review flag;
- preserves missing values instead of applying synthetic bank heuristics;
- distinguishes depository banks from insurers and other financial companies.

SEC extraction now includes Item 8 and extended Item 7/8 windows for banks.
The Aggregator classifies the business and packages sourced regulatory data.

Graph routing now selects:

```text
non-financial → corporate FCFF branch
depository bank → reviewed bank DDM branch
other financial → safe stop for a sector-specific model
```

The bank branch requires reviewed growth assumptions, target CET1, and an
explicit `regulatory_data_reviewed=True` confirmation. Missing data produces a
restrained memo with no valuation rather than a crash or fabricated target.
CLI flags support reviewed inputs and RWA/net-income overrides.

Live validation exposed a real parser hazard: an initial JPM run selected a
small component figure as total RWA. Candidate matching was tightened and
low-confidence RWA selections are now rejected. This confirms why regex output
must remain unreviewed until checked against the filing table.

Verification:

- **28/28 automated tests pass**;
- MSFT still routes through FCFF and produces the prior verified result;
- JPM routes automatically to bank DDM and safely withholds valuation when
  reviewed assumptions/confirmation are absent.

## 23. Structured FR Y-9C / FFIEC HC-R ingestion

Implemented the missing authoritative RWA/CET1 path:

- ticker → holding-company RSSD from `data/regulatory/ticker_to_rssd.json`,
  `--bank-rssd`, or `BANK_RSSD_ID`;
- NIC BHCF reader for caret/CSV/ZIP files, using `BHCAA223` (total RWA),
  `BHCAP859` (CET1 capital), and `BHCAP793` (CET1 ratio);
- optional download from
  `https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles`;
- structured HC-R values replace 10-K regex for valuation;
- regex retained only as corroboration;
- forecast assumptions remain required; regex confirmation is not required
  when HC-R RWA is present.

Rejected heuristics remain rejected. A missing or unpublished BHCF file still
withholds RWA rather than estimating it.

## 24. SEC iXBRL holding-company capital (live path)

FR Y-9C remains preferred when a local BHCF file exists. Unattended NIC
download stays off by default because the public zip endpoint returns HTTP
403. The working structured source is now parent-company iXBRL from the
latest 10-Q/10-K:

- `us-gaap:RiskWeightedAssets`, CET1 capital, and CET1/RWA tags;
- bank-subsidiary legal-entity members are excluded;
- Basel III Standardized is preferred over Advanced when both exist;
- CET1 capital / RWA is checked against the tagged ratio (100 bp tolerance);
- DDM target CET1 maintains the reported ratio;
- NI/RWA growth uses a bounded observed run-rate, or a labeled 2.5%
  placeholder. Sub-year RWA changes are not annualized.

This is still not a management forecast. Explicit CLI growth/CET1 flags
override the derived defaults.

## 25. Financial-firm path removed

The bank DDM, FR Y-9C/iXBRL regulatory ingestion, and bank CLI flags were
removed. The production model is three-stage FCFF for non-financial operating
companies. Yahoo Financial Services / Financials tickers skip valuation and
write a restrained out-of-scope memo instead of inventing RWA or CET1.

