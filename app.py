from pathlib import Path

import streamlit as st

from config import settings
from src import auth

st.set_page_config(page_title="CareerReady", layout="wide")
st.markdown(f"<style>{(Path(__file__).parent / 'assets' / 'style.css').read_text()}</style>",
            unsafe_allow_html=True)


@st.cache_resource
def _embedded_worker():
    """Local installs: run the background worker inside the app so one command is enough."""
    from src import worker
    return worker.start_background_thread()


if settings.EMBEDDED_WORKER:
    _embedded_worker()

if auth.current_user() is None:
    st.markdown(
        """<div class="hero">
             <h1>Apply to fewer jobs, and get more replies.</h1>
             <p>CareerReady scores each job against your resume, rewrites the resume and cover letter for it,
                prepares the application, and waits for your approval before anything is sent.</p>
           </div>""", unsafe_allow_html=True)
    st.button("Sign in", type="primary", on_click=st.login)
    st.caption("Your resume is encrypted at rest and never used to train models.")
    st.stop()

st.navigation([
    st.Page("views/home.py", title="Dashboard", url_path="home", default=True),
    st.Page("views/jobs.py", title="Jobs", url_path="jobs"),
    st.Page("views/interview.py", title="Practise interview", url_path="interview"),
    st.Page("views/resume.py", title="Quick resume", url_path="resume"),
    st.Page("views/profile.py", title="Profile", url_path="profile"),
    st.Page("views/account.py", title="Account", url_path="account"),
], position="top").run()
