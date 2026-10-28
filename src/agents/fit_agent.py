from src.services.llm_client import LLMClient
from templates.prompts import FIT_PROMPT


def analyze_fit(resume_text: str, job_description: str) -> dict:
    """Score the match and pull out title/company. Always returns a well-formed dict."""
    raw = LLMClient().generate_json(
        FIT_PROMPT.format(resume_text=resume_text[:9000], job_description=job_description[:7000])
    )
    if not isinstance(raw, dict):
        raise RuntimeError("Unexpected fit analysis format. Try again.")

    def as_list(v):
        return [str(x) for x in v][:4] if isinstance(v, list) else []

    try:
        score = max(0, min(100, int(float(raw.get("score", 0)))))
    except (TypeError, ValueError):
        score = 0
    return {
        "title": str(raw.get("title", "") or "").strip(),
        "company": str(raw.get("company", "") or "").strip(),
        "score": score,
        "summary": str(raw.get("summary", "") or "").strip(),
        "strengths": as_list(raw.get("strengths")),
        "gaps": as_list(raw.get("gaps")),
        "red_flags": as_list(raw.get("red_flags")),
    }
