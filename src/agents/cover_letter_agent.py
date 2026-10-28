from config.settings import CREATIVE_TEMP
from src.services.llm_client import LLMClient
from templates.prompts import COVER_LETTER_PROMPT


def write_cover_letter(profile: dict, job: dict) -> str:
    return LLMClient().generate(
        COVER_LETTER_PROMPT.format(
            name=profile.get("full_name") or "the candidate",
            resume_text=(job.get("resume_md") or profile["resume_text"])[:9000],
            title=job.get("title") or "the role",
            company=job.get("company") or "the company",
            job_description=job["description"][:6000],
        ),
        temperature=CREATIVE_TEMP,
    ).strip()
