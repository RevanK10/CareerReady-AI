"""Browser step, run as a subprocess:  python -m src.services.apply_runner spec.json

Kept separate from the app so a crashing browser can't take the app down, and so the
fill logic (fill_fields) can be tested against a stub page without Playwright.
"""
import json
import os
import re
import sys

SUCCESS = re.compile(r"thank you for applying|application (has been )?(submitted|received)|thanks for applying", re.I)


def fill_fields(page, fields: list[dict], answers: dict, files: dict) -> list[dict]:
    """Fill the form. Returns one {key, ok, error} per field the agent touched."""
    from src.services.forms import file_role  # local import keeps the runner light

    report = []
    for f in fields:
        key, kind = f["key"], f["kind"]
        value = answers.get(key)
        try:
            if kind == "file":
                path = files.get(file_role(f))
                if not path:
                    continue
                page.locator(f["selector"]).first.set_input_files(path)
            elif kind in ("radio", "checkbox_group"):
                picks = [value] if kind == "radio" else (value or [])
                for pick in picks:
                    if pick:
                        page.locator(f["option_selectors"][pick]).first.check()
                if not any(picks):
                    continue
            elif kind == "checkbox":
                if value is not True:
                    continue
                page.locator(f["selector"]).first.check()
            elif kind == "select":
                if not value:
                    continue
                page.locator(f["selector"]).first.select_option(label=str(value))
            else:
                if not value:
                    continue
                page.locator(f["selector"]).first.fill(str(value))
            report.append({"key": key, "ok": True, "error": ""})
        except Exception as e:  # one bad field shouldn't abort the rest
            report.append({"key": key, "ok": False, "error": str(e).splitlines()[0][:160]})
    return report


def _submit(page) -> bool:
    for locator in (page.locator("button[type=submit], input[type=submit]"),
                    page.get_by_role("button", name=re.compile(r"submit|apply", re.I))):
        if locator.count():
            locator.first.click()
            return True
    return False


def main(spec: dict) -> dict:
    from playwright.sync_api import sync_playwright

    mode, out = spec["mode"], spec["out_dir"]
    with sync_playwright() as p:
        from src import netguard
        netguard.assert_public_url(spec["url"])
        args = ["--no-sandbox"] if os.getenv("CR_NO_SANDBOX") else []   # needed inside most containers
        browser = p.chromium.launch(headless=(mode != "handover"), args=args)
        page = browser.new_page(viewport={"width": 1100, "height": 1400})
        page.route("**/*", netguard.route_guard())                      # never let the page reach private IPs
        page.goto(spec["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)  # let JS-rendered forms appear

        if mode == "extract":
            result = {"ok": True, "html": page.content()}
            browser.close()
            return result

        report = fill_fields(page, spec["fields"], spec["answers"], spec["files"])
        shot = os.path.join(out, "preview.png")
        page.screenshot(path=shot, full_page=True)
        result = {"ok": True, "report": report, "screenshot": shot, "submitted": False, "note": ""}

        if mode == "submit":
            if _submit(page):
                page.wait_for_timeout(5000)
                after = os.path.join(out, "after.png")
                page.screenshot(path=after, full_page=True)
                result.update(screenshot=after, submitted=True,
                              confirmed=bool(SUCCESS.search(page.inner_text("body"))))
            else:
                result["note"] = "Couldn't find a submit button."
        elif mode == "handover":
            with open(os.path.join(out, "result.json"), "w") as fh:  # write before waiting
                json.dump(result, fh)
            page.wait_for_event("close", timeout=0)  # stay open until the user closes the window
            return result
        browser.close()
        return result


if __name__ == "__main__":
    spec_path = sys.argv[1]
    with open(spec_path) as fh:
        spec = json.load(fh)
    try:
        res = main(spec)
    except Exception as e:
        res = {"ok": False, "error": str(e).splitlines()[0][:300]}
    with open(os.path.join(spec["out_dir"], "result.json"), "w") as fh:
        json.dump(res, fh)
