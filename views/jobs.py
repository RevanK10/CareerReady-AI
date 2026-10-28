import base64
import json

import streamlit as st

from config import settings
from src import billing
from src.agents.cover_letter_agent import write_cover_letter
from src.agents.fit_agent import analyze_fit
from src.agents.resume_agent import tailor_resume
from src.errors import QuotaError
from src.services import documents, forms, tasks
from views._common import chip, go, job_name, page_head, require_store, score_html, task_panel, when

store, user = require_store()
S = st.session_state
profile = store.get_profile()
SOURCE_NOTE = {"profile": "From your profile", "resume": "From your resume",
               "inferred": "Drafted by the agent. Check it", "unknown": "Needs your answer"}


# --------------------------------------------------------------- add a job
def add_job_panel() -> None:
    with st.container(key="addjob"):
        st.markdown("#### Add a job")
        url = st.text_input("Job posting link", placeholder="https://jobs.lever.co/company/role", key="add_url")
        text = st.text_area("Or paste the description", height=110, key="add_text",
                            help="Use this if the link needs a login or doesn't load.")
        if st.button("Add and prepare", type="primary"):
            try:
                S.job_id = tasks.submit_job(store, profile, url=url, description=text)
            except (ValueError, QuotaError) as e:
                st.error(str(e))
                return
            st.rerun()


def job_list() -> None:
    jobs = store.list_jobs()
    if not jobs:
        st.caption("No jobs yet. Add one above.")
        return
    choice = st.segmented_control("Show", ["All", *[s for s in ["Processing", "Needs attention", "Saved", "Ready to review",
                                  "Applied", "Interviewing", "Offer", "Rejected"]]], default="All", label_visibility="collapsed")
    shown = [j for j in jobs if choice in (None, "All") or j["status"] == choice]
    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    for job in shown:
        c1, c2, c3, c4, c5 = st.columns([5, 1, 2, 1.6, 1.1], vertical_alignment="center")
        c1.markdown(f'<div class="jobname">{job_name(job)}</div>', unsafe_allow_html=True)
        c2.markdown(score_html(job["fit_score"]), unsafe_allow_html=True)
        c3.markdown(chip(job["status"]), unsafe_allow_html=True)
        c4.caption(when(job["updated_at"]))
        if c5.button("Open", key=f"open_{job['id']}", use_container_width=True):
            S.job_id = job["id"]
            st.rerun()
    if not shown:
        st.caption("Nothing in this stage.")


# ----------------------------------------------------------------- detail
def run_step(label: str, fn) -> bool:
    with st.spinner(label + "…"):
        try:
            fn()
            return True
        except Exception as e:
            st.error(str(e))
            return False


def overview_tab(job: dict) -> None:
    jid = job["id"]
    c1, c2 = st.columns(2)
    title = c1.text_input("Title", job["title"], key=f"t_{jid}")
    company = c2.text_input("Company", job["company"], key=f"c_{jid}")
    url = st.text_input("Posting link", job["url"], key=f"u_{jid}")
    s1, s2 = st.columns([2, 1], vertical_alignment="bottom")
    stages = ["Saved", "Ready to review", "Applied", "Interviewing", "Offer", "Rejected", "Archived"]
    status = s1.selectbox("Stage", stages, index=stages.index(job["status"]) if job["status"] in stages else 0, key=f"s_{jid}")
    if s2.button("Save details", use_container_width=True, key=f"sv_{jid}"):
        store.update_job(jid, title=title, company=company, url=url, status=status)
        if status != job["status"]:
            store.log_event(jid, "status", f"{job['status']} → {status}")
        st.rerun()

    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    fit = json.loads(job["fit_json"]) if job["fit_json"] else None
    if not fit:
        if st.button("Score my fit", key=f"fit_{jid}"):
            def go_fit():
                f = analyze_fit(profile["resume_text"], job["description"])
                store.update_job(jid, fit_score=f["score"], fit_json=json.dumps(f),
                                 title=job["title"] or f["title"], company=job["company"] or f["company"])
            if run_step("Scoring your fit", go_fit):
                st.rerun()
    else:
        left, right = st.columns([1, 4], vertical_alignment="center")
        left.markdown(f'<div class="bigscore">{score_html(fit["score"])}<span>fit</span></div>', unsafe_allow_html=True)
        right.markdown(fit["summary"])
        a, b = st.columns(2)
        with a:
            st.markdown("**Where you match**")
            st.markdown("\n".join(f"- {x}" for x in fit["strengths"]) or "_Nothing clear._")
        with b:
            st.markdown("**Gaps**")
            st.markdown("\n".join(f"- {x}" for x in fit["gaps"]) or "_None found._")
        if fit["red_flags"]:
            st.warning("Possible blockers: " + "; ".join(fit["red_flags"]))

    with st.expander("Job description"):
        st.text(job["description"])

    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    b1, b2, _ = st.columns([2, 1.2, 4])
    if b1.button("Practise interview for this role", key=f"prac_{jid}"):
        S.interview_agent, S.feedback, S.spoken = None, None, 0
        S.interview_context, S.interview_job_id = job["description"], jid
        S.interview_role_prefill = job_name(job)
        go("interview")
    with b2.popover("Delete job"):
        st.write("This removes the job and its history.")
        if st.button("Delete permanently", key=f"del_{jid}"):
            store.delete_job(jid)
            S.job_id = None
            st.rerun()


def resume_tab(job: dict) -> None:
    jid = job["id"]
    if not job["resume_md"]:
        st.info("No tailored resume yet.")
        if st.button("Tailor my resume", type="primary", key=f"tr_{jid}"):
            def go_tailor():
                r = tailor_resume(profile["resume_text"], job["description"])
                store.update_job(jid, resume_md=r["resume"], notes=r["notes"])
                store.log_event(jid, "resume", "Tailored resume generated")
            if run_step("Tailoring your resume", go_tailor):
                st.rerun()
        return
    sheet, notes = st.columns([3, 2], gap="large")
    with sheet:
        with st.container(key=f"sheet_{jid}"):
            st.markdown(job["resume_md"])
    with notes:
        st.markdown("#### Notes on this version")
        st.markdown(job["notes"] or "_No notes saved._")
        st.caption("Check every line against your real experience before you send it.")
        a, b = st.columns(2)
        a.download_button("Word (.docx)", documents.to_docx(job["resume_md"]), f"resume-{jid}.docx",
                          use_container_width=True, key=f"dd_{jid}")
        b.download_button("PDF", documents.to_pdf(job["resume_md"]), f"resume-{jid}.pdf",
                          mime="application/pdf", use_container_width=True, key=f"dp_{jid}")
        if st.button("Regenerate", key=f"rr_{jid}"):
            def go_again():
                r = tailor_resume(profile["resume_text"], job["description"])
                store.update_job(jid, resume_md=r["resume"], notes=r["notes"])
                store.log_event(jid, "resume", "Resume regenerated")
            if run_step("Rewriting", go_again):
                st.rerun()


def cover_tab(job: dict) -> None:
    jid = job["id"]
    if not job["cover_letter"]:
        st.info("No cover letter yet.")
    letter = st.text_area("Cover letter", job["cover_letter"], height=320, key=f"cl_{jid}")
    a, b, c, d = st.columns([1.2, 1.4, 1.2, 1.2])
    if a.button("Save edits", key=f"cls_{jid}", disabled=not letter):
        store.update_job(jid, cover_letter=letter)
        st.toast("Saved")
    if b.button("Write a new one" if job["cover_letter"] else "Write it", key=f"clg_{jid}"):
        def go_letter():
            store.update_job(jid, cover_letter=write_cover_letter(profile, job))
            store.log_event(jid, "cover_letter", "Cover letter generated")
            S.pop(f"cl_{jid}", None)
        if run_step("Writing", go_letter):
            st.rerun()
    if letter:
        c.download_button("Word", documents.to_docx(letter), f"cover-letter-{jid}.docx", key=f"cld_{jid}",
                          use_container_width=True)
        d.download_button("PDF", documents.to_pdf(letter), f"cover-letter-{jid}.pdf", mime="application/pdf",
                          key=f"clp_{jid}", use_container_width=True)


def answer_sheet(form: dict, answers: dict) -> str:
    lines = []
    for f in form["fields"]:
        if f["kind"] == "file":
            continue
        v = answers[f["key"]]["value"]
        lines.append(f"{f['label']}{' *' if f['required'] else ''}\n  {', '.join(v) if isinstance(v, list) else (v if v not in (False, '') else '(your answer)')}\n")
    return "\n".join(lines)


def show_run_result(run: dict, form: dict) -> None:
    if run["status"] == "failed":
        st.error(run["error"] or "The browser step failed.")
        return
    res = run["result"]
    if res.get("mode") == "handover":
        st.info("A browser window opened with the form filled in. Check it, solve any CAPTCHA, and press Submit there. "
                "Then come back and mark it as applied.")
        return
    if res.get("screenshot_b64"):
        try:
            st.image(base64.b64decode(res["screenshot_b64"]), caption="What the form looked like")
        except Exception:                       # a corrupt screenshot must not take the page down
            st.caption("The screenshot couldn't be displayed.")
    bad = [r for r in res.get("report", []) if not r["ok"]]
    if bad:
        labels = {f["key"]: f["label"] for f in form["fields"]}
        st.warning("Couldn't fill: " + "; ".join(f"{labels.get(r['key'], r['key'])} ({r['error']})" for r in bad))
    if res["mode"] == "submit":
        if res.get("confirmed"):
            st.success("Submitted. The site showed a confirmation, so this job is marked as applied.")
        elif res.get("submitted"):
            st.warning("Clicked submit, but I couldn't see a confirmation. Check the screenshot, then mark it as applied yourself.")
        else:
            st.error(res.get("note") or "Not submitted.")


def apply_tab(job: dict) -> None:
    jid = job["id"]
    if job["status"] == "Applied":
        st.success("Marked as applied.")
    url = st.text_input("Application page", job["url"], key=f"aurl_{jid}",
                        help="The page with the application form. For Lever and Ashby, the job link works.")
    if not url.strip():
        st.info("Add the posting link on the Overview tab first.")
        return
    if forms.is_blocked(url):
        st.warning("This site doesn't allow automated applications, so you'll apply there yourself. "
                   "Download your tailored resume and cover letter from their tabs.")
        st.link_button("Open the posting", url)
        return
    if not job["resume_md"]:
        st.info("Tailor your resume first, so the application uses it.")
        return

    st.caption("The agent reads the form, proposes answers from your profile, and shows them here. "
               "Nothing is sent until you approve.")
    prep = store.latest_task("prepare_application", jid)
    if st.button("Read the form and prepare answers", type="primary", key=f"prep_{jid}",
                 disabled=bool(prep and prep["status"] in ("queued", "running"))):
        try:
            tasks.request_prepare(store, jid, url.strip())
        except (ValueError, QuotaError) as e:
            st.error(str(e))
            return
        st.rerun()
    if prep is None:
        return
    if prep["status"] in ("queued", "running"):
        task_panel(prep["id"], "Reading the form")
        return
    if prep["status"] == "failed":
        st.error(prep["error"] or "Couldn't read the form.")
        return

    form, plan = prep["result"]["form"], prep["result"]["plan"]
    if S.get(f"loaded_{jid}") != prep["id"]:        # first view of this preparation: seed the editable widgets
        for f in form["fields"]:
            if f["kind"] != "file":
                S[f"ans_{jid}_{f['key']}"] = plan[f["key"]]["value"]
        S[f"loaded_{jid}"] = prep["id"]

    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    st.markdown("#### Review the answers")
    answers = {}
    for f in form["fields"]:
        if f["kind"] == "file":
            continue
        wk, label, kind = f"ans_{jid}_{f['key']}", f["label"] + (" *" if f["required"] else ""), f["kind"]
        if kind in ("select", "radio"):
            value = st.selectbox(label, ["", *f["options"]], key=wk, format_func=lambda x: x or "—")
        elif kind == "textarea":
            value = st.text_area(label, key=wk, height=110)
        elif kind == "checkbox":
            value = st.checkbox(label, key=wk)
        elif kind == "checkbox_group":
            value = st.multiselect(label, f["options"], key=wk)
        else:
            value = st.text_input(label, key=wk)
        src = "unknown" if value in ("", False, []) else plan[f["key"]]["source"]
        st.markdown(f'<div class="src src-{src}">{SOURCE_NOTE[src]}</div>', unsafe_allow_html=True)
        answers[f["key"]] = {"value": value, "source": src}

    from src.agents import apply_agent
    attach_roles = {forms.file_role(f) for f in form["fields"] if f["kind"] == "file"}
    files = {r: "x" for r in ("resume", "cover") if r in attach_roles and (r == "resume" or job["cover_letter"])}
    st.caption("Will attach: " + (", ".join({"resume": "Tailored resume (PDF)", "cover": "Cover letter (PDF)"}[r] for r in files) or "nothing"))
    other = [f["label"] for f in form["fields"] if f["kind"] == "file" and forms.file_role(f) == "other"]
    if other:
        st.caption("You'll need to add these yourself: " + ", ".join(other))
    issues = apply_agent.validate(form["fields"], answers, files, form["captcha"])
    if issues:
        st.warning("Before the agent can submit:\n\n" + "\n".join(f"- {i}" for i in issues))
    st.download_button("Download answers (.txt) to apply by hand", answer_sheet(form, answers),
                       file_name=f"answers-{jid}.txt", key=f"sheet_dl_{jid}")

    st.markdown("#### Send it")
    modes = {"Preview: fill the form in a hidden browser and show me a screenshot. Nothing is sent.": "preview"}
    if not settings.HOSTED:
        modes["Fill it, then I submit: opens a browser window on this computer. You review and press Submit."] = "handover"
    modes["Submit for me: fill the form and send it."] = "submit"
    mode = modes[st.radio("Mode", list(modes), label_visibility="collapsed", key=f"mode_{jid}")]
    approved = st.checkbox("I've read every answer above and approve sending this application.", key=f"ok_{jid}") \
        if mode == "submit" else False
    run = store.latest_task("run_application", jid)
    busy = bool(run and run["status"] in ("queued", "running"))
    blocked = busy or (mode == "submit" and (bool(issues) or not approved))
    label = {"preview": "Run preview", "handover": "Open browser and fill", "submit": "Submit application"}[mode]
    if st.button(label, type="primary", disabled=blocked, key=f"run_{jid}"):
        try:
            tasks.request_run(store, jid, prep["id"], {k: v["value"] for k, v in answers.items()}, mode, approved)
        except (ValueError, QuotaError) as e:
            st.error(str(e))
            return
        st.rerun()

    if run and run["id"] > prep["id"]:
        if busy:
            task_panel(run["id"], "Working in the browser")
        else:
            show_run_result(run, form)
    if job["status"] != "Applied" and st.button("Mark as applied", key=f"done_{jid}"):
        store.update_job(jid, status="Applied")
        store.log_event(jid, "status", f"{job['status']} → Applied")
        st.rerun()


def activity_tab(job: dict) -> None:
    for e in store.list_events(job["id"]):
        st.markdown(f'<div class="event"><span>{when(e["ts"])}</span> {e["kind"].replace("_", " ")}'
                    f'{" — " + e["detail"] if e["detail"] else ""}</div>', unsafe_allow_html=True)


def job_detail(job_id: int) -> None:
    job = store.get_job(job_id)
    back, head = st.columns([1, 6], vertical_alignment="center")
    if back.button("← All jobs"):
        S.job_id = None
        st.rerun()
    head.markdown(f'<div class="detail-title">{job_name(job)} {chip(job["status"])}</div>', unsafe_allow_html=True)

    work = store.latest_task("process_job", job_id)
    if job["status"] == "Processing" and work and work["status"] in ("queued", "running"):
        st.caption("You can leave this page. The work continues in the background and the job will be ready when you return.")
        task_panel(work["id"], "Preparing your job")
        return
    if job["status"] == "Processing":         # task finished but status never settled: show why
        st.warning("This job didn't finish. Open the Activity tab for details.")
    if work and work["status"] == "failed" and job["status"] == "Needs attention":
        st.error(work["error"] or "Something went wrong.")
    elif work and work["status"] == "failed":
        st.warning("Some steps couldn't finish: " + (work["error"] or "unknown error") + " Retry them from the tabs below.")

    tabs = st.tabs(["Overview", "Resume", "Cover letter", "Apply", "Activity"])
    for tab, fn in zip(tabs, (overview_tab, resume_tab, cover_tab, apply_tab, activity_tab)):
        with tab:
            fn(job)


# ------------------------------------------------------------------- route
if S.job_id and store.get_job(S.job_id):
    job_detail(S.job_id)
else:
    S.job_id = None
    page_head("Jobs", "Add a posting. The agents work in the background: fit score, tailored resume, cover letter.")
    if len(profile["resume_text"]) < 100:
        st.info("Save your resume on the Profile page first.")
        if st.button("Go to Profile", type="primary"):
            go("profile")
    else:
        add_job_panel()
        job_list()
