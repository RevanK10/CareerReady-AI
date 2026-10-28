import streamlit as st

from src import billing
from src.agents.resume_agent import run_resume_tailor_agent
from src.services import documents
from src.services.scraper import ScrapeError
from views._common import page_head, require_store

store, user = require_store()
page_head("Quick resume tailor", "One-off rewrite without saving a job. For the full workflow, add the job under Jobs.")
saved = store.get_profile()["resume_text"]

left, right = st.columns(2, gap="large")
with left:
    uploaded = st.file_uploader("Resume (PDF)" + (" — optional, your saved resume is used otherwise" if saved else ""),
                                type=["pdf"])
    job_url = st.text_input("Job posting link", placeholder="https://company.com/careers/role")
with right:
    job_text = st.text_area("Or paste the job description", height=185,
                            help="Takes priority over the link. Use it when the page needs a login.")

if st.button("Tailor resume", type="primary"):
    if not uploaded and not saved:
        st.error("Upload your resume as a PDF, or save one on the Profile page.")
    elif not (job_url.strip() or job_text.strip()):
        st.error("Add a job posting link or paste the job description.")
    else:
        ok, _, _ = billing.check(user["id"], "jobs")
        if not ok:
            st.error(billing.denial_message(user["id"], "jobs"))
            st.stop()
        billing.consume(user["id"], "jobs")
        with st.spinner("Reading the posting and rewriting your resume…"):
            try:
                st.session_state.resume_result = run_resume_tailor_agent(
                    pdf_bytes=uploaded.getvalue() if uploaded else b"",
                    resume_text="" if uploaded else saved,
                    job_description=job_text, job_url=job_url.strip())
            except ScrapeError as e:
                billing.refund(user["id"], "jobs")
                st.session_state.resume_result = None
                st.error(f"{e} Paste the job description above instead.")
            except Exception as e:
                billing.refund(user["id"], "jobs")
                st.session_state.resume_result = None
                st.error(str(e))

result = st.session_state.resume_result
if result:
    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    sheet_col, notes_col = st.columns([3, 2], gap="large")
    with sheet_col:
        with st.container(key="sheet"):
            st.markdown(result["resume"])
    with notes_col:
        st.markdown("#### Notes on this version")
        st.markdown(result["notes"] or "_No notes returned this time. Try again._")
        st.caption("Check every line against your real experience before you send it.")
        a, b = st.columns(2)
        a.download_button("Word (.docx)", documents.to_docx(result["resume"]), "tailored-resume.docx",
                          use_container_width=True)
        b.download_button("PDF", documents.to_pdf(result["resume"]), "tailored-resume.pdf",
                          mime="application/pdf", use_container_width=True)
