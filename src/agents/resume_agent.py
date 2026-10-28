from config.settings import CREATIVE_TEMP
from src.services.llm_client import LLMClient
from src.services.pdf_parser import extract_text_from_pdf
from src.services.scraper import scrape_job_posting
from templates.prompts import NOTES_MARKER, RESUME_TAILOR_PROMPT


def tailor_resume(resume_text: str, job_description: str) -> dict:
    """Tailor resume text to a job description. Returns {"resume": markdown, "notes": markdown}."""
    prompt = RESUME_TAILOR_PROMPT.format(resume_text=resume_text[:9000], job_description=job_description[:7000])
    output = LLMClient().generate(prompt, temperature=CREATIVE_TEMP)
    resume, _, notes = output.partition(NOTES_MARKER)
    return {"resume": resume.strip(), "notes": notes.strip()}


def run_resume_tailor_agent(pdf_bytes: bytes = b"", job_description: str = "", job_url: str = "",
                            resume_text: str = "") -> dict:
    """Tailor from an uploaded PDF (or already-extracted text) plus a posting link or pasted text."""
    if not resume_text:
        resume_text = extract_text_from_pdf(pdf_bytes) if pdf_bytes else ""
    if len(resume_text) < 100:
        raise ValueError("No readable resume text. Upload a text-based PDF or save your resume in Profile.")

    job_description = job_description.strip()
    if not job_description and job_url:
        job_description = scrape_job_posting(job_url)  # raises ScrapeError
    if not job_description:
        raise ValueError("Provide a job link or paste the job description.")
    return tailor_resume(resume_text, job_description)
