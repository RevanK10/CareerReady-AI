import streamlit as st

from src.services.pdf_parser import extract_text_from_pdf
from views._common import page_head, require_store

store, user = require_store()
page_head("Your profile", "Saved on this computer. The agents use it to write and fill in applications.")
p = store.get_profile()

upload = st.file_uploader("Upload your resume (PDF)", type=["pdf"], help="Replaces the resume text below.")
if upload and st.session_state.get("_parsed") != upload.file_id:
    text = extract_text_from_pdf(upload.getvalue())
    if len(text) < 100:
        st.error("Couldn't read text from that PDF. It may be a scan; export a text-based PDF.")
    else:
        st.session_state["resume_box"] = text
        st.session_state["_parsed"] = upload.file_id
st.session_state.setdefault("resume_box", p["resume_text"])

with st.form("profile"):
    a, b = st.columns(2)
    full_name = a.text_input("Full name", p["full_name"])
    email = b.text_input("Email", p["email"])
    phone = a.text_input("Phone", p["phone"])
    location = b.text_input("City, region", p["location"])
    linkedin = a.text_input("LinkedIn URL", p["linkedin"])
    github = b.text_input("GitHub URL", p["github"])
    website = a.text_input("Website / portfolio", p["website"])
    st.markdown("##### Application answers")
    st.caption("Only what you enter here is ever used for these questions. Blank means the agent asks you.")
    c, d = st.columns(2)
    auth = c.text_input("Work authorization", p["work_authorization"], placeholder="e.g. US citizen")
    sponsor = d.selectbox("Need visa sponsorship?", ["", "No", "Yes"],
                          index=["", "No", "Yes"].index(p["needs_sponsorship"] or ""))
    start = c.text_input("Earliest start date", p["earliest_start"], placeholder="e.g. 2 weeks notice")
    salary = d.text_input("Salary expectation", p["salary_expectation"], placeholder="e.g. $120k-$140k")
    st.markdown("##### Autopilot")
    levels = ["Off", "60", "70", "80", "90"]
    auto = st.selectbox("Prepare the application automatically when the fit score is at least", levels,
                        index=levels.index(p["auto_prepare_min_score"] or "Off"),
                        help="The agent reads the form and drafts answers for you to review. It never submits on its own.")
    resume_text = st.text_area("Resume text", key="resume_box", height=260,
                               help="Edit freely. This is the only source the agents may draw facts from.")
    extra = st.text_area("Extra facts for screening questions", p["extra_facts"], height=110,
                         placeholder="Years with Python: 6\nOpen to relocation: no\nNotice period: 2 weeks")
    if st.form_submit_button("Save profile", type="primary"):
        store.save_profile({
            "full_name": full_name, "email": email, "phone": phone, "location": location,
            "linkedin": linkedin, "github": github, "website": website,
            "work_authorization": auth, "needs_sponsorship": sponsor, "earliest_start": start,
            "salary_expectation": salary, "resume_text": resume_text, "extra_facts": extra,
            "auto_prepare_min_score": "" if auto == "Off" else auto,
        })
        st.success("Saved.")
