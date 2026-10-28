import json
import pytest

from src.agents import apply_agent as aa
from src.services import forms
from src.services.apply_runner import fill_fields
from tests.conftest import PROFILE
from tests.fixtures import GREENHOUSE, LEVER

JOB = {"title": "Backend Engineer", "company": "Acme", "description": "Build APIs in Python.", "resume_md": "# Jane"}


def by_label(fields, text):
    return next(f for f in fields if text.lower() in f["label"].lower())


def test_parse_greenhouse():
    fs = forms.parse_form(GREENHOUSE)
    labels = [f["label"] for f in fs]
    assert "First Name" in labels and "Resume/CV" in labels
    assert not any("recaptcha" in f["label"].lower() for f in fs)           # captcha textarea ignored
    assert by_label(fs, "authorized")["options"] == ["Yes", "No"]           # placeholder option dropped
    assert by_label(fs, "First Name")["required"] and not by_label(fs, "Phone")["required"]
    assert forms.has_captcha(GREENHOUSE)


def test_parse_lever_radio_group():
    fs = forms.parse_form(LEVER)
    radio = by_label(fs, "sponsorship")
    assert radio["kind"] == "radio" and radio["options"] == ["Yes", "No"]
    assert radio["option_selectors"]["No"] == 'input[name="cards[1][field0]"][value="No"]'
    assert not forms.has_captcha(LEVER)


def test_urls_and_blocking():
    assert forms.application_url("https://jobs.lever.co/acme/1?lever-source=x") == "https://jobs.lever.co/acme/1/apply"
    assert forms.application_url("https://jobs.ashbyhq.com/acme/1") .endswith("/application")
    assert forms.is_blocked("https://www.linkedin.com/jobs/view/1")
    assert forms.is_blocked("https://acme.wd1.myworkdayjobs.com/x")
    assert not forms.is_blocked("https://boards.greenhouse.io/acme/jobs/1")
    with pytest.raises(aa.ApplyError):
        aa.load_form("https://www.indeed.com/viewjob?jk=1")


def test_plan_uses_profile_for_contact_and_never_invents(fake_llm):
    fs = forms.parse_form(GREENHOUSE)
    fake_llm["answers"] = json.dumps({
        by_label(fs, "authorized")["key"]: {"value": "yes", "source": "profile"},       # fuzzy option match
        by_label(fs, "Why do you want")["key"]: {"value": "I like APIs.", "source": "inferred"},
        by_label(fs, "Gender")["key"]: {"value": "Decline To Self Identify", "source": "profile"},
    })
    ans = aa.plan_answers(PROFILE, JOB, fs)
    assert ans[by_label(fs, "First Name")["key"]] == {"value": "Jane", "source": "profile"}
    assert ans[by_label(fs, "Last Name")["key"]]["value"] == "Q Doe"
    assert ans[by_label(fs, "Email")["key"]]["value"] == "jane@example.com"
    assert ans[by_label(fs, "authorized")["key"]]["value"] == "Yes"
    # contact fields were answered by rules, so the model never saw them
    sent = fake_llm["calls"][-1]
    assert "First Name" not in sent.split("QUESTIONS (JSON)")[1]


def test_plan_rejects_option_not_in_list_and_marks_unknown(fake_llm):
    fs = forms.parse_form(GREENHOUSE)
    fake_llm["answers"] = json.dumps({by_label(fs, "authorized")["key"]: {"value": "Maybe", "source": "profile"}})
    ans = aa.plan_answers(PROFILE, JOB, fs)
    assert ans[by_label(fs, "authorized")["key"]] == {"value": "", "source": "unknown"}
    assert ans[by_label(fs, "Why do you want")["key"]]["source"] == "unknown"       # model skipped it


def test_validate_blocks_submission(fake_llm):
    fs = forms.parse_form(GREENHOUSE)
    fake_llm["answers"] = "{}"
    ans = aa.plan_answers(PROFILE, JOB, fs)
    issues = aa.validate(fs, ans, files={"resume": "r.pdf"}, captcha=True)
    text = " ".join(issues)
    assert "authorized" in text and "Why do you want" in text and "CAPTCHA" in text
    assert "Resume" not in text                                                      # resume file is provided
    assert any("Resume" in i for i in aa.validate(fs, ans, files={}, captcha=False))


def test_validate_passes_when_complete(fake_llm):
    fs = forms.parse_form(LEVER)
    fake_llm["answers"] = json.dumps({
        by_label(fs, "sponsorship")["key"]: {"value": "No", "source": "profile"}})
    ans = aa.plan_answers(PROFILE, JOB, fs)
    assert aa.validate(fs, ans, files={"resume": "r.pdf"}, captcha=False) == []


class StubPage:
    """Records what the runner would do in a browser."""
    def __init__(self, fail_on=None):
        self.log, self.fail_on = [], fail_on

    def locator(self, sel):
        page = self
        class L:
            first = property(lambda s: s)
            def _do(s, verb, arg=None):
                if page.fail_on == sel:
                    raise RuntimeError("element not found\nstack…")
                page.log.append((verb, sel, arg))
            fill = lambda s, v: s._do("fill", v)
            check = lambda s: s._do("check")
            select_option = lambda s, label: s._do("select", label)
            set_input_files = lambda s, p: s._do("upload", p)
        return L()


def test_fill_fields_drives_the_right_controls():
    fs = forms.parse_form(LEVER)
    key = lambda t: by_label(fs, t)["key"]
    answers = {key("Full name"): "Jane Doe", key("Email"): "j@x.com", key("LinkedIn"): "",
               key("sponsorship"): "No", key("Additional"): "Hi"}
    page = StubPage()
    report = fill_fields(page, fs, answers, {"resume": "/tmp/r.pdf"})
    verbs = {(v, a) for v, _, a in page.log}
    assert ("fill", "Jane Doe") in verbs and ("upload", "/tmp/r.pdf") in verbs
    assert ("check", None) in verbs
    assert not any(a == "" for _, _, a in page.log)                                  # empty answers never typed
    assert all(r["ok"] for r in report)


def test_fill_fields_survives_one_bad_field():
    fs = forms.parse_form(LEVER)
    answers = {by_label(fs, "Full name")["key"]: "Jane", by_label(fs, "Email")["key"]: "j@x.com"}
    page = StubPage(fail_on='input[name="name"]')
    report = fill_fields(page, fs, answers, {})
    assert [r["ok"] for r in report] == [False, True]
    assert report[0]["error"] == "element not found"


def test_run_application_refuses_bad_mode_and_blocked_sites():
    with pytest.raises(ValueError):
        aa.run_application("https://x.com", [], {}, {}, "yolo", "/tmp")
    with pytest.raises(aa.ApplyError):
        aa.run_application("https://www.linkedin.com/jobs/1", [], {}, {}, "submit", "/tmp")


def test_build_files_writes_pdfs(tmp_path):
    fs = forms.parse_form(GREENHOUSE)
    files = aa.build_files(PROFILE, {**JOB, "cover_letter": "Dear Team,\nHi\nJane"}, fs, str(tmp_path))
    assert set(files) == {"resume", "cover"}
    assert open(files["resume"], "rb").read(4) == b"%PDF"
