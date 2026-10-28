import json
import re
import time

import requests

from config.settings import (
    ANALYTICAL_TEMP,
    CREATIVE_TEMP,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    REQUEST_TIMEOUT,
)

RETRIES = 2
RETRY_STATUS = {429, 500, 502, 503, 504}


def extract_json(text: str):
    """Pull a JSON object/array out of a model reply, tolerating code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        raise ValueError("no JSON found")
    return json.loads(text[start:end + 1])


class LLMClient:
    """Thin wrapper around the OpenRouter chat completions endpoint."""

    def __init__(self):
        if not OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Add it to your .env file "
                "(see .env.example) and restart the app."
            )
        self.headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "CareerReady AI",
        }

    def _post(self, payload: dict) -> requests.Response:
        """POST with a couple of retries: free models often rate-limit or time out."""
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                response = requests.post(OPENROUTER_URL, headers=self.headers, json=payload, timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as e:
                if last:
                    raise RuntimeError(f"Could not reach OpenRouter: {e}") from e
            else:
                if response.status_code not in RETRY_STATUS or last:
                    return response
            time.sleep(2 * (attempt + 1))

    def chat(self, messages: list[dict], temperature: float = CREATIVE_TEMP) -> str:
        payload = {"model": OPENROUTER_MODEL, "messages": messages, "temperature": temperature}
        response = self._post(payload)
        if response.status_code == 401:
            raise RuntimeError("OpenRouter rejected the API key. Check OPENROUTER_API_KEY in your .env file.")
        if response.status_code != 200:
            raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text[:300]}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise RuntimeError("OpenRouter returned an unexpected response.") from e

        if not content or not content.strip():
            raise RuntimeError("The model returned an empty response. Try again.")
        return content.strip()

    def generate(self, prompt: str, temperature: float = CREATIVE_TEMP) -> str:
        return self.chat([{"role": "user", "content": prompt}], temperature)

    def generate_json(self, prompt: str, temperature: float = ANALYTICAL_TEMP):
        """Ask for JSON and parse it; retry once because free models sometimes wrap it in prose."""
        suffix = "\n\nReturn ONLY valid JSON. No commentary and no code fences."
        for _ in range(2):
            try:
                return extract_json(self.generate(prompt + suffix, temperature))
            except ValueError:
                continue
        raise RuntimeError("The model didn't return usable structured output. Try again.")
