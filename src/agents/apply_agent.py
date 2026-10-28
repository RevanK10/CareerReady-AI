"""Plans and (with explicit approval) performs a job application.

Safety rules baked in:
  * blocked sites are refused (see config.settings.BLOCKED_APPLY_DOMAINS)
  * every answer carries its source; anything unknown stays empty for the user to fill
  * nothing is submitted unless the caller passes mode="submit" after the user approved,
    and never when a CAPTCHA is present or a required answer is missing
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from src import netguard
from src.services import documents, forms
from src.services.llm_client import LLMClient
from templates.prompts import ANSWER_PROMPT

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UA = {"User-Agent": "Mozilla/5.0 (CareerReady)"}


class ApplyError(Exception):
    pass


# ------------------------------------------------------------- load the form
def _run_runner(spec: dict, timeout: int, wait: bool = True) -> dict:
    out_dir = spec.setdefault("out_dir", tempfile.mkdtemp(prefix="careerready_"))
    spec_path = os.path.join(out_dir, "spec.json")
    with open(spec_path, "w") as f:
        json.dump(spec, f)
    cmd = [sys.executable, "-m", "src.services.apply_runner", spec_path]
    if not wait:  # visible browser the user drives; don't block the app
        subprocess.Popen(cmd, cwd=ROOT)
        return {"ok": True, "out_dir": out_dir}
    try:
        subprocess.run(cmd, cwd=ROOT, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        raise ApplyError("The browser step timed out.") from e
    result_path = os.path.join(out_dir, "result.json")
    if not os.path.exists(result_path):
        raise ApplyError("The browser step didn't finish. Is Playwright installed? "
                         "Run: pip install playwright && playwright install chromium")
    with open(result_path) as f:
        result = json.load(f)
    result["out_dir"] = out_dir
    return result


def load_form(url: str) -> dict:
    """Return {"url", "ats", "fields", "captcha"} for an application page."""
    if forms.is_blocked(url):
        raise ApplyError("This site doesn't allow automated applications. Apply on the site yourself; "
                         "your tailored resume and cover letter are ready to download.")
    try:
        netguard.assert_public_url(url)
    except netguard.UnsafeURL as e:
        raise ApplyError(str(e)) from e
    target = forms.application_url(url)
    html = ""
    try:
        html = netguard.safe_get(target, headers=UA, timeout=15).text
    except netguard.UnsafeURL as e:
        raise ApplyError(str(e)) from e
    except Exception:
        pass  # fall through to the browser
    fields = [f for f in forms.parse_form(html) if f.get("selector") or f.get("option_selectors")] if html else []
    if len(fields) < 3:  # JS-rendered form: let a real browser load it
        res = _run_runner({"mode": "extract", "url": target}, timeout=90)
        if not res.get("ok"):
            raise ApplyError(res.get("error", "Couldn't load the application form."))
        html = res["html"]
        fields = [f for f in forms.parse_form(html) if f.get("selector") or f.get("option_selectors")]
    if len(fields) < 3:
        raise ApplyError("Couldn't find an application form on that page. Open the job's Apply button, "
                         "copy that page's address, and try again.")
    return {"url": target, "ats": forms.detect_ats(target), "fields": fields, "captcha": forms.has_captcha(html)}


# ---------------------------------------------------------- plan the answers
def _first_last(full: str) -> tuple[str, str]:
    parts = full.split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def _rule_value(field: dict, profile: dict) -> str:
    """Deterministic answers for obvious contact fields: more reliable than asking a model."""
    if field["kind"] not in ("text", "email", "tel", "url"):
        return ""
    label = field["label"].lower().strip()
    first, last = _first_last(profile["full_name"])
    rules = [
        (r"first name|given name", first),
        (r"last name|surname|family name", last),
        (r"^(your |full |legal )?name$|^full legal name$", profile["full_name"]),
        (r"e-?mail", profile["email"]),
        (r"phone|mobile|telephone", profile["phone"]),
        (r"linkedin", profile["linkedin"]),
        (r"github", profile["github"]),
        (r"website|portfolio|personal site", profile["website"]),
    ]
    if field["kind"] == "email":
        return profile["email"]
    if field["kind"] == "tel":
        return profile["phone"]
    for pattern, value in rules:
        if re.search(pattern, label):
            return value
    if re.fullmatch(r"(current )?(city|location)( \(.*\))?", label):
        return profile["location"]
    return ""


def _match_option(value: str, options: list[str]) -> str:
    v = str(value).strip().lower()
    for o in options:
        if o.lower() == v:
            return o
    for o in options:
        if v and (o.lower().startswith(v) or v.startswith(o.lower())):
            return o
    return ""


def plan_answers(profile: dict, job: dict, fields: list[dict]) -> dict:
    """Return {key: {"value", "source"}} for every non-file field."""
    answers: dict[str, dict] = {}
    pending = []
    for f in fields:
        if f["kind"] == "file":
            continue
        if f["kind"] in ("checkbox", "checkbox_group"):  # consents and opt-ins are the user's call
            answers[f["key"]] = {"value": False if f["kind"] == "checkbox" else [], "source": "unknown"}
            continue
        value = _rule_value(f, profile)
        if value:
            answers[f["key"]] = {"value": value, "source": "profile"}
        else:
            pending.append(f)

    if pending:
        questions = [{"key": f["key"], "label": f["label"], "kind": f["kind"],
                      "required": f["required"], "options": f["options"]} for f in pending]
        raw = LLMClient().generate_json(ANSWER_PROMPT.format(
            profile=json.dumps({k: v for k, v in profile.items() if k not in ("resume_text", "extra_facts")}),
            extra_facts=profile["extra_facts"] or "(none)",
            resume_text=(job.get("resume_md") or profile["resume_text"])[:7000],
            title=job.get("title") or "the role", company=job.get("company") or "the company",
            job_description=job["description"][:4000],
            questions=json.dumps(questions),
        ))
        raw = raw if isinstance(raw, dict) else {}
        for f in pending:
            item = raw.get(f["key"]) or {}
            value = str(item.get("value", "") if isinstance(item, dict) else item).strip()
            source = item.get("source", "unknown") if isinstance(item, dict) else "inferred"
            if source not in ("profile", "resume", "inferred", "unknown"):
                source = "inferred"
            if f["kind"] in ("select", "radio"):
                value = _match_option(value, f["options"])
            if not value:
                source = "unknown"
            answers[f["key"]] = {"value": value, "source": source}
    return answers


# ------------------------------------------------------------------ validate
def build_files(profile: dict, job: dict, fields: list[dict], out_dir: str) -> dict:
    """Write the tailored resume (and cover letter if the form asks) as PDFs. Returns {role: path}."""
    files = {}
    roles = {forms.file_role(f) for f in fields if f["kind"] == "file"}
    if "resume" in roles:
        path = os.path.join(out_dir, "resume.pdf")
        with open(path, "wb") as fh:
            fh.write(documents.to_pdf(job.get("resume_md") or profile["resume_text"]))
        files["resume"] = path
    if "cover" in roles and job.get("cover_letter"):
        path = os.path.join(out_dir, "cover_letter.pdf")
        with open(path, "wb") as fh:
            fh.write(documents.to_pdf(job["cover_letter"]))
        files["cover"] = path
    return files


def validate(fields: list[dict], answers: dict, files: dict, captcha: bool) -> list[str]:
    """Problems that must be fixed before the agent may submit. Empty list = OK to submit."""
    issues = []
    for f in fields:
        if not f["required"]:
            continue
        if f["kind"] == "file":
            role = forms.file_role(f)
            if role not in files:
                issues.append(f"“{f['label']}” needs a file the agent can't provide")
            continue
        value = answers.get(f["key"], {}).get("value")
        if value in ("", None, False, []):
            issues.append(f"“{f['label']}” is required and still empty")
    if captcha:
        issues.append("This form has a CAPTCHA. Use “Fill, then I submit” and solve it yourself")
    return issues


# ----------------------------------------------------------------------- run
def run_application(url: str, fields: list[dict], answers: dict, files: dict, mode: str, out_dir: str) -> dict:
    """mode: preview (headless, screenshot only) | handover (visible browser, user submits) | submit."""
    if mode not in ("preview", "handover", "submit"):
        raise ValueError(f"Unknown mode {mode}")
    if forms.is_blocked(url):
        raise ApplyError("This site doesn't allow automated applications.")
    flat = {k: v["value"] for k, v in answers.items()}
    spec = {"mode": mode, "url": url, "fields": fields, "answers": flat, "files": files, "out_dir": out_dir}
    return _run_runner(spec, timeout=150, wait=(mode != "handover"))
