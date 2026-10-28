import streamlit as st

from views._common import chip, go, job_name, page_head, require_store, score_html

store, user = require_store()
profile, counts, jobs = store.get_profile(), store.status_counts(), store.list_jobs()

if not jobs:
    st.markdown(
        """<div class="hero">
             <h1>Apply to fewer jobs, and get more replies.</h1>
             <p>Give CareerReady your resume once. For each job you add, it scores the fit, rewrites the
                resume, writes the cover letter and prepares the application, then waits for your approval.</p>
           </div>""", unsafe_allow_html=True)
else:
    page_head("Your search")

if len(profile["resume_text"]) < 100:
    st.info("Start by saving your resume and contact details. Every other step builds on them.")
    if st.button("Set up your profile", type="primary"):
        go("profile")
elif not jobs:
    if st.button("Add your first job", type="primary"):
        go("jobs")

if jobs:
    cols = st.columns(5)
    for col, status in zip(cols, ["Saved", "Ready to review", "Applied", "Interviewing", "Offer"]):
        col.markdown(f'<div class="stat"><div class="n">{counts[status]}</div><div class="l">{status}</div></div>',
                     unsafe_allow_html=True)

    ready = [j for j in jobs if j["status"] == "Ready to review"]
    st.markdown("#### Waiting for you" if ready else "#### Recent jobs")
    for job in (ready or jobs)[:6]:
        c1, c2, c3, c4 = st.columns([5, 1, 2, 1.2], vertical_alignment="center")
        c1.markdown(f'<div class="jobname">{job_name(job)}</div>', unsafe_allow_html=True)
        c2.markdown(score_html(job["fit_score"]), unsafe_allow_html=True)
        c3.markdown(chip(job["status"]), unsafe_allow_html=True)
        if c4.button("Open", key=f"home_open_{job['id']}", use_container_width=True):
            st.session_state.job_id = job["id"]
            go("jobs")
    c1, c2, _ = st.columns([1.3, 1.6, 6])
    if c1.button("Add a job", type="primary", use_container_width=True):
        go("jobs")
    if c2.button("Practise interview", use_container_width=True):
        go("interview")
else:
    st.markdown(
        """<ol class="steps">
             <li><b>Add a job.</b> Paste a link. The agent reads the posting, scores your fit, tailors your
                 resume and drafts a cover letter.</li>
             <li><b>Review and apply.</b> It reads the application form and proposes answers from your
                 profile, marking anything it had to guess. You approve before anything is sent.</li>
             <li><b>Practise.</b> Rehearse the interview for that exact role, out loud, and get feedback.</li>
           </ol>
           <p class="aside">Applications are only sent after you approve them. LinkedIn, Indeed and similar
              sites forbid automation, so those are handled by hand.</p>""", unsafe_allow_html=True)
