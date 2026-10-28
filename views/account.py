import json

import streamlit as st

from config import settings
from src import billing, db
from views._common import page_head, require_store

store, user = require_store()
page_head("Account", user["email"] or "Local account")

plan = billing.plan_of(user["id"])
st.markdown(f"#### Plan: {plan['name']}")
if settings.HOSTED:
    used = store.usage()
    for metric, label in billing.LABELS.items():
        limit = plan[metric]
        st.progress(min(used.get(metric, 0) / limit, 1.0) if limit else 0.0,
                    text=f"{label.capitalize()}: {used.get(metric, 0)} of {limit} this month" if limit
                    else f"{label.capitalize()}: not included")
    c1, c2, _ = st.columns([1.4, 1.6, 4])
    link = billing.upgrade_url(user["id"], user["email"])
    if plan["key"] == "free" and link:
        c1.link_button("Upgrade to Pro", link, type="primary")
    if plan["key"] != "free" and settings.STRIPE_PORTAL_URL:
        c2.link_button("Manage billing", settings.STRIPE_PORTAL_URL)
else:
    st.caption("Running locally: no limits apply.")

st.markdown('<hr class="rule">', unsafe_allow_html=True)
st.markdown("#### Your data")
st.caption("Download everything stored for this account, or delete it permanently.")
st.download_button("Export my data (JSON)", json.dumps(store.export_all(), indent=2, default=str),
                   file_name="careerready-export.json", mime="application/json")
with st.popover("Delete my account"):
    st.write("This permanently deletes your profile, jobs, resumes and history. It can't be undone.")
    if st.text_input("Type DELETE to confirm", key="del_confirm") == "DELETE" and st.button("Delete everything", type="primary"):
        db.delete_account(user["id"])
        st.session_state.clear()
        if settings.HOSTED:
            st.logout()
        else:
            st.rerun()

if settings.HOSTED:
    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    st.button("Sign out", on_click=st.logout)
