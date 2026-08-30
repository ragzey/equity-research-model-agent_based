"""Local research-desk GUI. Bind to localhost only."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_file
from markdown import markdown

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from main import run_pipeline  # noqa: E402
from equity_research.tools.report_pack import build_report_pack  # noqa: E402
from equity_research.utils.llm_client import redact_secrets  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "outputs" / "reports"
TEMPLATE_DIR = PROJECT_ROOT / "gui_templates"
STATIC_DIR = PROJECT_ROOT / "gui_static"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)

logger = logging.getLogger("ResearchDeskGUI")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()


def _parse_symbols(raw: str) -> List[str]:
    symbols: List[str] = []
    for token in (raw or "").replace(",", " ").split():
        clean = token.strip().upper()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols


def _env_status() -> Dict[str, bool]:
    user_agent = os.getenv("SEC_USER_AGENT", "")
    placeholder = (
        not user_agent.strip()
        or "example.com" in user_agent.lower()
        or "yourname@" in user_agent.lower()
        or "your.email" in user_agent.lower()
    )
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "finnhub": bool(os.getenv("FINNHUB_API_KEY", "").strip()),
        "sec_user_agent_ok": not placeholder,
    }


def _render_memo_html(text: str) -> str:
    return markdown(
        text or "",
        extensions=["tables", "fenced_code"],
        output_format="html",
    )


def summarize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    classification = summary.get("firm_classification") or {}
    dcf = summary.get("dcf") or {}
    overrides = state.get("dcf_overrides") or {}
    cost_of_debt = summary.get("cost_of_debt") or {}
    price = inputs.get("share_price")
    raw_value = state.get("calculated_dcf_value")
    display_value = max(float(raw_value), 0.0) if raw_value is not None else None
    gap = None
    if price not in (None, "") and display_value is not None and float(price) > 0:
        gap = display_value / float(price) - 1.0

    memo_path = state.get("final_equity_memo_path")
    pdf_path = state.get("final_equity_memo_pdf_path")
    memo_text = ""
    if memo_path and Path(memo_path).is_file():
        memo_text = Path(memo_path).read_text(encoding="utf-8")

    pack = summary.get("report_pack")
    if not isinstance(pack, dict) or not pack.get("key_data"):
        try:
            pack = build_report_pack(state)
        except Exception:
            logger.exception("Could not rebuild report pack for GUI summary.")
            pack = pack if isinstance(pack, dict) else {}

    handoffs = []
    for item in state.get("agent_messages") or []:
        handoffs.append(
            {
                "from_agent": item.get("from_agent"),
                "to_agent": item.get("to_agent"),
                "kind": item.get("kind"),
                "body": item.get("body"),
            }
        )

    payload = {
        "ticker": state.get("ticker"),
        "company_name": pack.get("company_name"),
        "industry": pack.get("industry"),
        "sector": pack.get("sector"),
        "country": pack.get("country"),
        "target_year": state.get("target_year"),
        "firm_type": classification.get("firm_type"),
        "valuation_method": summary.get("valuation_method")
        or state.get("valuation_method")
        or "corporate_fcff",
        "is_financial": bool(state.get("is_financial")),
        "verified": bool(state.get("is_math_verified")),
        "share_price": pack.get("share_price") if pack.get("share_price") is not None else price,
        "model_value": raw_value,
        "display_value": display_value,
        "fair_value": pack.get("fair_value"),
        "price_target_12m": pack.get("price_target_12m"),
        "upside_to_pt": pack.get("upside_to_pt"),
        "upside_to_fair_value": pack.get("upside_to_fair_value"),
        "model_rating": pack.get("model_rating"),
        "model_rating_note": pack.get("model_rating_note"),
        "dcf_value": pack.get("dcf_value"),
        "relative_value": pack.get("relative_value"),
        "dcf_weight": pack.get("dcf_weight"),
        "relative_weight": pack.get("relative_weight"),
        "gap": gap,
        "wacc": state.get("discount_rate"),
        "cost_of_equity": summary.get("cost_of_equity"),
        "cost_of_debt_method": cost_of_debt.get("method_used"),
        "terminal_share": dcf.get("terminal_value_share_of_enterprise_value"),
        "high_growth_rate": (summary.get("applied_dcf_assumptions") or {}).get(
            "high_growth_rate"
        ),
        "desk_mode": overrides.get("desk_mode"),
        "decisions": overrides.get("decisions") or [],
        "rationales": overrides.get("rationales") or {},
        "handoffs": handoffs,
        "peer_selection": state.get("peer_selection"),
        "discovered_bond_isins": state.get("discovered_bond_isins"),
        "review_findings": state.get("review_findings") or [],
        "report_pack": pack,
        "memo_markdown": memo_text,
        "memo_html": _render_memo_html(memo_text),
        "memo_name": Path(memo_path).name if memo_path else None,
        "pdf_name": Path(pdf_path).name if pdf_path else None,
        "has_pdf": bool(pdf_path and Path(pdf_path).is_file()),
    }
    return payload


def _write_sidecar(summary: Dict[str, Any]) -> None:
    name = summary.get("memo_name")
    if not name:
        return
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = REPORTS_DIR / name.replace("_memo.md", "_gui.json")
    slim = {key: value for key, value in summary.items() if key != "memo_html"}
    sidecar.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")


def _list_reports(limit: int = 12) -> List[Dict[str, Any]]:
    if not REPORTS_DIR.is_dir():
        return []
    files = sorted(
        REPORTS_DIR.glob("*_memo.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    items = []
    for path in files[:limit]:
        sidecar = REPORTS_DIR / path.name.replace("_memo.md", "_gui.json")
        ticker = path.name.split("_")[0]
        items.append(
            {
                "memo_name": path.name,
                "ticker": ticker,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                    timespec="seconds"
                ),
                "has_sidecar": sidecar.is_file(),
            }
        )
    return items


class _JobLogHandler(logging.Handler):
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
        self.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        line = redact_secrets(self.format(record))
        with JOB_LOCK:
            job = JOBS.get(self.job_id)
            if job is not None:
                job["logs"].append(line)


def _run_job(
    job_id: str,
    ticker: str,
    year: str,
    peers: List[str],
    bonds: List[str],
    openai_api_key: str,
    openai_model: str,
) -> None:
    handler = _JobLogHandler(job_id)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        if not RUN_LOCK.acquire(blocking=False):
            with JOB_LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = "Another run is already in progress."
            return
        try:
            with JOB_LOCK:
                JOBS[job_id]["status"] = "running"
            state = run_pipeline(
                ticker=ticker,
                target_year=year,
                peer_tickers=peers,
                target_bonds=bonds,
                openai_api_key=openai_api_key or None,
                openai_model=openai_model or None,
            )
            summary = summarize_state(state)
            _write_sidecar(summary)
            with JOB_LOCK:
                JOBS[job_id]["status"] = "done"
                JOBS[job_id]["summary"] = summary
        finally:
            RUN_LOCK.release()
    except Exception as exc:
        logger.exception("GUI pipeline run failed.")
        with JOB_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = redact_secrets(str(exc))
            JOBS[job_id]["trace"] = redact_secrets(traceback.format_exc())
    finally:
        root.removeHandler(handler)


def _job_public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["id"],
        "status": job["status"],
        "logs": job["logs"][-400:],
        "error": job.get("error"),
        "summary": job.get("summary"),
    }


@app.get("/")
def index():
    return render_template("index.html", default_year=str(date.today().year))


@app.get("/api/meta")
def api_meta():
    return jsonify({"env": _env_status(), "reports": _list_reports()})


@app.post("/api/run")
def api_run():
    payload = request.get_json(silent=True) or {}
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400
    year = str(payload.get("target_year") or date.today().year).strip()
    peers = _parse_symbols(str(payload.get("peers") or ""))
    bonds = _parse_symbols(str(payload.get("bonds") or ""))
    openai_api_key = str(payload.get("openai_api_key") or "").strip()
    openai_model = str(payload.get("openai_model") or "").strip()
    if RUN_LOCK.locked():
        return jsonify({"error": "A run is already in progress."}), 409
    job_id = uuid.uuid4().hex[:10]
    with JOB_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "logs": [],
            "error": None,
            "summary": None,
        }
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, ticker, year, peers, bonds, openai_api_key, openai_model),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            abort(404)
        return jsonify(_job_public(job))


@app.get("/api/reports/<name>")
def api_report(name: str):
    if "/" in name or "\\" in name or not name.endswith("_memo.md"):
        abort(400)
    path = REPORTS_DIR / name
    if not path.is_file():
        abort(404)
    text = path.read_text(encoding="utf-8")
    sidecar = REPORTS_DIR / name.replace("_memo.md", "_gui.json")
    summary: Optional[Dict[str, Any]] = None
    if sidecar.is_file():
        summary = json.loads(sidecar.read_text(encoding="utf-8"))
        summary["memo_markdown"] = text
        summary["memo_html"] = _render_memo_html(text)
        pdf = REPORTS_DIR / name.replace("_memo.md", "_memo.pdf")
        summary["has_pdf"] = pdf.is_file()
        summary["pdf_name"] = pdf.name if pdf.is_file() else None
        summary["memo_name"] = name
    else:
        ticker = name.split("_")[0]
        summary = {
            "ticker": ticker,
            "memo_markdown": text,
            "memo_html": _render_memo_html(text),
            "memo_name": name,
            "has_pdf": (REPORTS_DIR / name.replace("_memo.md", "_memo.pdf")).is_file(),
            "pdf_name": name.replace("_memo.md", "_memo.pdf"),
            "partial": True,
        }
    return jsonify(summary)


@app.get("/api/files/<name>")
def api_file(name: str):
    if "/" in name or "\\" in name:
        abort(400)
    path = REPORTS_DIR / name
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True)


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print("Research desk GUI  http://127.0.0.1:5050")
    print("Paste an OpenAI API key in the form, or set OPENAI_API_KEY.")
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)


if __name__ == "__main__":
    main()
