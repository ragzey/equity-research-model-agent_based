"""LLM industry outlook from the competitive analyst's peer matrix."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .llm_client import chat_text

logger = logging.getLogger("LLMSynthesis")


def synthesize_industry_outlook(
    target: str,
    matrix: Dict[str, Any],
    filing_excerpt: Optional[str] = None,
) -> str:
    """LLM industry outlook grounded in the supplied peer metrics and 10-K excerpt."""
    competitors: List[str] = matrix.get("competitors", [])
    metrics_json = json.dumps(matrix.get("metrics", {}), indent=2, default=str)
    filing_snippet = (filing_excerpt or "")[:4000]

    system_prompt = (
        "You are an evidence-grounded equity research analyst. Use only the supplied "
        "peer metrics and SEC excerpt. Multiples and margins do not by themselves prove "
        "market share, barriers to entry, moat durability, saturation, or price erosion. "
        "State when evidence is insufficient; do not use model memory or outside facts."
    )
    user_prompt = f"""Target company: {target}
Peer group: {', '.join(competitors)}

Peer comparison metrics (JSON):
{metrics_json}

Optional SEC filing excerpt:
{filing_snippet}

Write a concise analysis (250-400 words) under:
1. OBSERVED PEER POSITIONING
2. INDUSTRY EVIDENCE AND LIMITATIONS

Compare only observed metrics. Discuss barriers, market share, saturation,
price wars, price erosion, secular decline, or obsolescence only when the SEC
excerpt explicitly supports that claim. Use those exact phrases only when
supported, because downstream rules may alter valuation assumptions."""

    content = chat_text(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=60,
        required=True,
    )
    logger.info("LLM industry outlook generated for %s (%d chars).", target, len(content or ""))
    return content or ""
