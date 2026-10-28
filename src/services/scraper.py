import requests
from bs4 import BeautifulSoup

from src import netguard

MAX_CHARS = 6000
MIN_USEFUL_CHARS = 300

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


class ScrapeError(Exception):
    """Raised when a job posting can't be turned into usable text."""


def scrape_job_posting(url: str) -> str:
    """Return clean text from a job posting URL, or raise ScrapeError."""
    if not url or not url.startswith(("http://", "https://")):
        raise ScrapeError("Enter a full link that starts with http:// or https://")

    try:
        response = netguard.safe_get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except netguard.UnsafeURL as e:
        raise ScrapeError(str(e)) from e
    except requests.exceptions.RequestException as e:
        raise ScrapeError(f"Couldn't open that page ({e.__class__.__name__}).") from e

    soup = BeautifulSoup(response.text, "html.parser")
    for element in soup(["script", "style", "nav", "footer", "noscript"]):
        element.extract()

    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    text = "\n".join(lines)

    if len(text) < MIN_USEFUL_CHARS:
        raise ScrapeError(
            "That page returned almost no text. It probably loads its content with "
            "JavaScript or requires a login."
        )
    return text[:MAX_CHARS]


# Alias kept for backward compatibility
scrape_company_website = scrape_job_posting
