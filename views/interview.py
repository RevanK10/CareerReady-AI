import html
import json

import streamlit as st

from src import billing
from src.agents.interview_agent import MockInterviewAgent
from src.services.scraper import ScrapeError, scrape_job_posting
from src.services.transcriber import TranscriptionError, transcribe_audio
from views._common import require_store

store, user = require_store()
S = st.session_state


def speak(text: str) -> None:
    payload = json.dumps(text).replace("<", "\\u003c")  # model output: keep inert inside <script>
    st.iframe(
        f"""<script>
            window.parent.speechSynthesis.cancel();
            window.parent.speechSynthesis.speak(new window.parent.SpeechSynthesisUtterance({payload}));
        </script>""", height=1)


def turn_html(role: str, text: str) -> str:
    who, css = ("Interviewer", "") if role == "assistant" else ("You", " you")
    body = html.escape(text).replace("\n", "<br>")
    return f'<div class="turn{css}"><div class="who">{who}</div><p class="said">{body}</p></div>'


def submit_answer(text: str) -> None:
    with st.spinner("Thinking about your answer…"):
        try:
            S.interview_agent.reply(text)
        except Exception as e:
            st.error(str(e))
            return
    S.mic_n += 1  # resets the recorder so the same clip isn't sent twice
    st.rerun()


def reset_interview() -> None:
    S.interview_agent, S.feedback, S.spoken = None, None, 0
    S.interview_context, S.interview_job_id, S.interview_role_prefill = "", None, ""


st.markdown('<div class="page-head"><h1>Practise the interview</h1><p>Questions are read aloud. Answer by '
            'recording or typing. Your browser will ask for microphone access.</p></div>', unsafe_allow_html=True)
agent = S.interview_agent

if agent is None:
    role = st.text_input("Role", value=S.get("interview_role_prefill", ""),
                         placeholder="Senior backend engineer at Stripe")
    if S.interview_context:
        st.caption("Using the job description you saved for this role.")
        link = ""
    else:
        link = st.text_input("Job posting link (optional)", placeholder="https://…")
    if st.button("Start interview", type="primary"):
        if not role.strip():
            st.error("Enter the role you're interviewing for.")
            st.stop()
        ok, _, _ = billing.check(user["id"], "interviews")
        if not ok:
            st.error(billing.denial_message(user["id"], "interviews"))
            st.stop()
        context = S.interview_context
        if link.strip():
            try:
                context = scrape_job_posting(link.strip())
            except ScrapeError as e:
                st.warning(f"{e} Starting with general questions for the role.")
        with st.spinner("Preparing your first question…"):
            try:
                new_agent = MockInterviewAgent(role_title=role.strip(),
                                               role_context=context or "Standard competencies for this role.")
                new_agent.start_interview()
            except Exception as e:
                st.error(str(e))
                st.stop()
        billing.consume(user["id"], "interviews")
        S.interview_agent, S.spoken = new_agent, 0
        st.rerun()
    st.stop()

history, feedback = agent.conversation_history, S.feedback
head, a, b = st.columns([5, 2, 1.3], vertical_alignment="center")
head.subheader(agent.role_title)
if feedback is None:
    if a.button("Finish and get feedback", use_container_width=True, disabled=agent.answers_given == 0):
        with st.spinner("Writing your feedback…"):
            try:
                S.feedback = agent.get_feedback()
            except Exception as e:
                st.error(str(e))
                st.stop()
        if S.interview_job_id:
            store.log_event(S.interview_job_id, "interview", f"Practice interview, {agent.answers_given} answers")
        st.rerun()
    if b.button("Discard", use_container_width=True):
        reset_interview()
        st.rerun()
else:
    if a.button("New interview", type="primary", use_container_width=True):
        reset_interview()
        st.rerun()

if feedback is not None:
    st.markdown("#### Feedback")
    with st.container(key="feedback"):
        st.markdown(feedback)
    with st.expander("Read the transcript"):
        st.markdown("".join(turn_html(m["role"], m["content"]) for m in history), unsafe_allow_html=True)
    st.stop()

st.caption(f"{agent.answers_given} answer{'s' if agent.answers_given != 1 else ''} so far")
st.markdown("".join(turn_html(m["role"], m["content"]) for m in history), unsafe_allow_html=True)

voice = st.toggle("Read questions aloud", value=True)
if len(history) > S.spoken or S.replay:
    if voice:
        speak(history[-1]["content"])
    S.spoken, S.replay = len(history), False
if voice and st.button("Replay question"):
    S.replay = True
    st.rerun()

st.markdown('<hr class="rule">', unsafe_allow_html=True)
clip = st.audio_input("Record your answer", key=f"mic_{S.mic_n}")
if clip is not None:
    with st.spinner("Transcribing…"):
        try:
            heard = transcribe_audio(clip.getvalue())
        except TranscriptionError as e:
            heard = ""
            st.error(str(e))
    if heard:
        submit_answer(heard)

with st.form("typed_answer", clear_on_submit=True):
    typed = st.text_area("Or type your answer", height=110)
    if st.form_submit_button("Send answer") and typed.strip():
        submit_answer(typed.strip())
