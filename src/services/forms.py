"""Find the fillable fields in an application form (pure HTML parsing, no browser needed)."""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config.settings import BLOCKED_APPLY_DOMAINS

SKIP_TYPES = {"hidden", "submit", "button", "image", "reset", "search"}
CAPTCHA_MARKERS = ("g-recaptcha", "recaptcha/api", "h-captcha", "hcaptcha", "cf-turnstile", "turnstile")
PLACEHOLDER_OPTION = re.compile(r"^\s*(select|choose|please select|--+|—+)?[\s.…-]*$", re.I)


def host(url: str) -> str:
    return (urlparse(url if "//" in url else "//" + url).hostname or "").lower()


def is_blocked(url: str) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in BLOCKED_APPLY_DOMAINS)


def detect_ats(url: str) -> str:
    h = host(url)
    for name, domain in (("greenhouse", "greenhouse.io"), ("lever", "lever.co"), ("ashby", "ashbyhq.com")):
        if h == domain or h.endswith("." + domain):
            return name
    return "other"


def application_url(url: str) -> str:
    """Point at the application form itself rather than the job description page."""
    url = url.strip().split("#")[0]
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    ats = detect_ats(url)
    if ats == "lever" and not path.endswith("/apply"):
        return parts._replace(path=path + "/apply", query="").geturl()
    if ats == "ashby" and not path.endswith("/application"):
        return parts._replace(path=path + "/application", query="").geturl()
    return url


def has_captcha(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CAPTCHA_MARKERS)


# ------------------------------------------------------------------ parsing
def _clean(text: str) -> str:
    text = re.sub(r"[✱*]", "", text or "")
    text = re.sub(r"\(?\brequired\b\)?", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" :\n\t")


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _hidden(el) -> bool:
    for node in [el, *el.parents]:
        attrs = getattr(node, "attrs", None) or {}
        if re.search(r"display\s*:\s*none", attrs.get("style", "") or "") or "hidden" in attrs:
            return True
    return False


def _selector(el, with_value: bool = False) -> str | None:
    q = lambda s: s.replace('"', '\\"')
    if with_value and el.get("name") and el.get("value") is not None:
        return f'input[name="{q(el["name"])}"][value="{q(el["value"])}"]'
    if el.get("id"):
        return f'[id="{q(el["id"])}"]'
    if el.get("name"):
        return f'{el.name}[name="{q(el["name"])}"]'
    return None


def _option_label(el, root) -> str:
    lab = el.find_parent("label")
    if lab:
        return _clean(_text(lab))
    if el.get("id"):
        lab = root.find("label", attrs={"for": el["id"]})
        if lab:
            return _clean(_text(lab))
    sib = el.next_sibling
    return _clean(str(sib)) if sib and isinstance(sib, str) else _clean(el.get("value", ""))


def _field_label(el, root, own_option_text: str = "") -> str:
    if el.get("id"):
        lab = root.find("label", attrs={"for": el["id"]})
        if lab and _clean(_text(lab)):
            return _clean(_text(lab))
    if el.get("aria-label"):
        return _clean(el["aria-label"])
    if el.get("aria-labelledby"):
        ref = root.find(id=el["aria-labelledby"].split()[0])
        if ref and _clean(_text(ref)):
            return _clean(_text(ref))
    for anc in list(el.parents)[:6]:
        if anc.name in ("form", "[document]"):
            break
        legend = anc.find("legend")
        cand = legend or anc.find(class_=re.compile(r"(application-)?label|question-title|field-label", re.I))
        if cand is None and len(anc.find_all(["input", "select", "textarea"])) == 1:
            cand = anc.find("label")  # a lone <label> in a single-field wrapper
        if cand is None and anc.name == "label":
            cand = anc  # plain <label>Text <input></label>
        text = _clean(_text(cand))
        if text and text != own_option_text:
            return text
    return _clean(el.get("placeholder") or el.get("name") or "")


def parse_form(html: str) -> list[dict]:
    """Return fields: key, label, kind, required, options, selector, option_selectors."""
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    root = max(forms, key=lambda f: len(f.find_all(["input", "select", "textarea"]))) if forms else soup

    fields: list[dict] = []
    groups: dict[str, dict] = {}
    for el in root.find_all(["input", "select", "textarea"]):
        etype = (el.get("type") or "text").lower() if el.name == "input" else el.name
        if etype in SKIP_TYPES or _hidden(el):
            continue
        name = el.get("name") or ""
        if "captcha" in name.lower() or "g-recaptcha-response" in (el.get("id") or ""):
            continue

        if etype in ("radio", "checkbox") and name:
            opt = _option_label(el, root)
            group = groups.get(name)
            if group is None:
                label = _field_label(el, root, own_option_text=opt)
                group = {"label": label, "kind": etype, "options": [], "option_selectors": {},
                         "required": el.has_attr("required") or "*" in label or "✱" in label}
                groups[name] = group
                fields.append(group)
            group["options"].append(opt)
            group["option_selectors"][opt] = _selector(el, with_value=True)
            if len(group["options"]) > 1 and group["kind"] == "checkbox":
                group["kind"] = "checkbox_group"
            continue

        raw_label = ""
        if el.get("id"):
            lab = root.find("label", attrs={"for": el["id"]})
            raw_label = _text(lab)
        label = _field_label(el, root)
        required = (el.has_attr("required") or el.get("aria-required") == "true"
                    or bool(re.search(r"[*✱]\s*$", raw_label or label)))
        field = {"label": label, "kind": etype, "required": required, "options": [],
                 "selector": _selector(el), "option_selectors": {}}
        if el.name == "select":
            field["kind"] = "select"
            field["options"] = [_clean(o.get_text()) for o in el.find_all("option")
                                if o.get("value", "x") != "" and not PLACEHOLDER_OPTION.match(o.get_text())]
        elif el.name == "textarea":
            field["kind"] = "textarea"
        elif etype == "file":
            field["kind"] = "file"
        elif etype == "checkbox":
            field["kind"] = "checkbox"
        elif etype not in ("email", "tel", "url", "number", "date"):
            field["kind"] = "text"
        fields.append(field)

    seen, out = set(), []
    for f in fields:
        sig = f.get("selector") or f["label"]
        if sig in seen:
            continue
        seen.add(sig)
        f["key"] = f"q{len(out) + 1}"
        out.append(f)
    return out


def file_role(field: dict) -> str:
    label = field["label"].lower()
    if re.search(r"resume|\bcv\b|curriculum", label):
        return "resume"
    if "cover" in label:
        return "cover"
    return "other"
