"""
AI provider abstraction for quote analysis.

The rest of the app never talks to a specific AI engine directly — it asks an
`AIProvider` to (a) read a quote's text into structured fields, (b) optionally
narrate a recommendation, and (c) optionally produce a holistic reasoned
comparison. Swapping the brain needs no change to the business logic in
quote_intelligence.py.

Providers
---------
    LocalHeuristicProvider  - no LLM, pure rules/regex. Runs anywhere (incl. a
                              locked-down corporate laptop and the free cloud
                              host). Always available; the safety net.
    OllamaProvider          - a *local* LLM via http://localhost:11434, IF the
                              user happens to have Ollama running. OPTIONAL — the
                              app no longer depends on it.
    GeminiProvider          - Google Gemini over the public REST API. The primary
                              non-local AI option: needs only an API key and
                              outbound HTTPS, so it works without admin rights or
                              any local install. This is what makes cloud AI the
                              fallback when Ollama isn't there.
    OpenAIProvider          - placeholder (subclasses the LLM base; wire up later).
    ClaudeProvider          - placeholder (subclasses the LLM base; wire up later).

resolve_provider(settings) picks the right one from the user's saved settings and
returns it together with a human-readable status (the ✓/⚠ indicator the UI shows)
and any fallback message ("Ollama not available, switching to Gemini…").

All LLM providers share the same prompts and JSON parsing (the module-level
build_* / parse_* helpers), so each concrete provider only has to implement how
it sends a prompt and gets text back (`_chat`) — that's the future-proof seam.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .settings import get_api_key, load_settings

# The fields we try to pull out of every quotation.
QUOTE_FIELDS = [
    "Vendor Name", "Quotation Number", "Quotation Date", "Validity Period",
    "Payment Terms", "Delivery Timeline", "Warranty Details", "GST / Taxes",
    "Freight / Transport", "Total Value", "Additional Terms",
]

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class AIProvider(ABC):
    """Interface every AI backend implements."""

    name = "base"
    # True for cloud providers (text leaves the machine); drives privacy notices.
    is_cloud = False
    # True for real LLM backends (can do free-form reasoning via narrate()).
    is_llm = False

    @abstractmethod
    def available(self) -> bool:
        """Cheap, no-network-cost check that this provider *could* be used."""
        ...

    @abstractmethod
    def extract(self, text: str, items_df, vendor_hint: str = "") -> dict:
        """Return {field: {"value": str|None, "confidence": 0..1}} for QUOTE_FIELDS."""
        ...

    def recommend(self, context: str) -> str:
        """Optional richer narrative. Default: none (engine uses its own text)."""
        return ""

    def reason(self, quotes: list[tuple]) -> str:
        """Holistic, domain-agnostic reasoned comparison of all quotes.

        `quotes`: list of (vendor_name, raw_text). Returns markdown analysis, or
        "" if this provider can't truly reason (no LLM). Only a real LLM provider
        overrides this — it's what produces analyst-quality output for any kind of
        quotation (goods, hotels, services), not just the fixed field schema.
        """
        return ""

    def narrate(self, prompt: str) -> str:
        """Domain-agnostic escape hatch: run an arbitrary prompt and return text.

        This is the seam any feature (quote analysis, contract diff review, …)
        uses to get free-form LLM reasoning without coupling the provider to that
        feature's prompts. Non-LLM providers return "" so callers fall back to
        their own deterministic output.
        """
        return ""

    def test_connection(self) -> tuple[bool, str]:
        """Actively verify the provider works (used by the Settings page).

        Returns (ok, message). Default: report availability without a live call.
        """
        if self.available():
            return True, f"{self.name} is available."
        return False, f"{self.name} is not available."


# --- Heuristic (no-LLM) provider ---------------------------------------------
_GST_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]Z[A-Z\d]\b")
_DATE_RE = re.compile(
    r"\b(\d{1,2}[\-/ ](?:\d{1,2}|[A-Za-z]{3,9})[\-/ ]\d{2,4})\b")
_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
_VENDOR_HINT_WORDS = ("ltd", "pvt", "private limited", "inc", "llp", "industries",
                      "enterprises", "technologies", "solar", "systems", "company",
                      "corporation", "co.", "traders", "solutions",
                      "hotel", "inn", "resort", "restaurant", "lodge")
_GENERIC_SENDERS = ("general manager", "sales", "accounts", "front office manager",
                    "reservations", "admin", "info", "marketing")


def _guess_vendor(text: str, fallback: str) -> str:
    """Best-effort vendor name from an emailed/prose quote."""
    # 1. "Greetings from X" — very common in quote emails, gives clean names.
    m = re.search(r"greetings\s+from\s+([^\n!.,]{3,50})", text, re.I)
    if m:
        return m.group(1).strip().strip("!.,")
    # 2. Outlook "From <Name> <email>" — unless it's a generic role.
    m = re.search(r"(?im)^\s*from\s+([A-Z][^\n<]{2,50}?)\s*<", text)
    if m and m.group(1).strip().lower() not in _GENERIC_SENDERS:
        return m.group(1).strip()
    # 3. A line that looks like a company/venue name.
    for line in text.splitlines():
        s = line.strip()
        if 3 < len(s) < 60 and any(w in s.lower() for w in _VENDOR_HINT_WORDS):
            return s
    # 4. Fall back to the filename.
    return fallback


def _first(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def heuristic_extract(text: str, items_df, vendor_hint: str = "") -> dict:
    """Rule/regex based field extraction. Confidence reflects match strength."""
    t = text or ""
    low = t.lower()
    out: dict[str, dict] = {}

    def put(field, value, conf):
        out[field] = {"value": value, "confidence": conf if value else 0.0}

    # Vendor name from the email/prose (e.g. "Greetings from X"), else filename.
    vendor = _guess_vendor(t, vendor_hint)
    strong = vendor and vendor != vendor_hint
    put("Vendor Name", vendor or (vendor_hint or None), 0.85 if strong else 0.55)

    put("Quotation Number",
        _first(r"(?:quotation|quote|ref(?:erence)?|q\.?\s*no)[\s:.#\-]*([A-Z0-9][A-Z0-9\-/]{2,})", t),
        0.8)
    put("Quotation Date", _first(_DATE_RE.pattern, t), 0.75)
    put("Validity Period",
        _first(r"valid(?:ity)?[^.\n]*?(\d+\s*(?:days?|weeks?|months?))", t), 0.8)

    # Payment terms: capture a short phrase around the keyword.
    pay = None
    pm = re.search(r"(payment[^.\n]{0,80}|(?:100%\s*)?advance[^.\n]{0,40}|"
                   r"net\s*\d+[^.\n]{0,20}|\d+\s*days?\s*credit)", low)
    if pm:
        pay = pm.group(1).strip().capitalize()
    put("Payment Terms", pay, 0.7 if pay else 0.0)

    put("Delivery Timeline",
        _first(r"(?:delivery|deliver(?:ed)?|lead\s*time)[^.\n]*?(\d+\s*(?:days?|weeks?))", t)
        or _first(r"within\s+(\d+\s*(?:days?|weeks?))", t), 0.75)
    put("Warranty Details",
        _first(r"(\d+\s*(?:years?|yrs?|months?))\s*warranty", t)
        or _first(r"warranty[^.\n]*?(\d+\s*(?:years?|yrs?|months?))", t), 0.75)

    gst = _GST_RE.search(t)
    gst_pct = _first(r"gst[^.\n]*?(\d{1,2}\s*%)", t)
    gst_val = gst.group(0) if gst else gst_pct
    put("GST / Taxes", gst_val, 0.9 if gst else (0.65 if gst_pct else 0.0))

    freight = None
    if re.search(r"freight|transport|shipping|delivery charges", low):
        fm = re.search(r"(?:freight|transport|shipping)[^.\n]{0,40}", low)
        freight = fm.group(0).strip().capitalize() if fm else "Mentioned"
    put("Freight / Transport", freight, 0.65 if freight else 0.0)

    total = _first(r"(?:grand\s*total|total\s*(?:amount|value|payable)?)[\s:rs.₹inr]*([\d,]+(?:\.\d+)?)", t)
    if not total:
        amts = _AMOUNT_RE.findall(t)
        total = max(amts, key=lambda a: float(a.replace(",", "")) if a else 0) if amts else None
    put("Total Value", total, 0.7 if total else 0.0)

    terms = []
    for kw in ("installation", "commissioning", "training", "amc", "buyback", "penalty"):
        if kw in low:
            terms.append(kw)
    put("Additional Terms", ", ".join(terms) if terms else None, 0.6 if terms else 0.0)

    return out


class LocalHeuristicProvider(AIProvider):
    name = "Local (rules, no LLM)"

    def available(self) -> bool:
        return True

    def extract(self, text, items_df, vendor_hint=""):
        return heuristic_extract(text, items_df, vendor_hint)


# --- Shared LLM prompt building + parsing -------------------------------------
# These are deliberately provider-agnostic: every LLM backend (Ollama, Gemini,
# and the future OpenAI/Claude) uses the exact same prompts and parsing, so they
# behave identically and there's only one place to tune the procurement prompts.

def build_extract_prompt(text: str) -> str:
    return (
        "You are a procurement analyst. Extract these fields from the vendor "
        "quotation text and return ONLY JSON with these exact keys: "
        + ", ".join(f'"{f}"' for f in QUOTE_FIELDS)
        + '. For each key use an object {"value": <string or null>, '
        '"confidence": <0..1>}. If a field is absent, value null and '
        "confidence 0.\n\nQUOTATION TEXT:\n" + (text or "")[:6000]
    )


def parse_extract_json(raw: str) -> dict:
    """Turn an LLM's JSON reply into the {field: {value, confidence}} schema.

    Tolerates ```json fences and missing keys. Raises on unparseable input so the
    caller can fall back to heuristics.
    """
    data = json.loads(_strip_json_fence(raw))
    result = {}
    for f in QUOTE_FIELDS:
        cell = data.get(f) or {}
        if isinstance(cell, dict):
            result[f] = {"value": cell.get("value"),
                         "confidence": float(cell.get("confidence") or 0.0)}
        else:
            result[f] = {"value": cell, "confidence": 0.8 if cell else 0.0}
    return result


def build_recommend_prompt(context: str) -> str:
    return (
        "You are a procurement advisor. Based on the structured comparison below, "
        "write a concise, professional recommendation (5-8 sentences) for a "
        "procurement committee. Avoid jargon.\n\n" + context
    )


def build_reason_prompt(quotes: list[tuple]) -> str:
    blocks = []
    for i, (name, text) in enumerate(quotes, 1):
        blocks.append(f"--- QUOTE {i} (file: {name}) ---\n{(text or '')[:4500]}")
    return (
        "You are an experienced procurement analyst. Compare the vendor "
        "quotations below like a professional and produce a committee-ready "
        "note in markdown.\n\n"
        "Do this:\n"
        "1. Identify each vendor and what they are quoting.\n"
        "2. Put COMPARABLE line items side by side (same product / room type / "
        "plan / occupancy). Compute effective prices INCLUDING any taxes "
        "stated (e.g. '2800+5%' = 2940).\n"
        "3. For each comparable item, say which vendor is cheaper and by how "
        "much (amount and %).\n"
        "4. Note non-price differences: inclusions, availability/quantity, "
        "warranty, validity, payment terms, location, and any missing info or "
        "risks.\n"
        "5. End with a clear recommendation of best overall value and why.\n"
        "Be specific with numbers. Use short sections and bullet points.\n\n"
        + "\n\n".join(blocks)
    )


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        # Drop a leading ```json / ``` fence and the trailing ```.
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


class LLMProvider(AIProvider):
    """Base for real LLM backends.

    A concrete provider only implements `available()` and `_chat()`. The shared
    procurement logic (extract/recommend/reason) lives here so Ollama, Gemini and
    any future cloud model produce identical, well-tested behaviour — including
    the all-important "fall back to heuristics, never break" guarantee.
    """

    is_llm = True

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        raise NotImplementedError

    def narrate(self, prompt: str) -> str:
        try:
            return self._chat(prompt).strip()
        except Exception:
            return ""

    def extract(self, text, items_df, vendor_hint=""):
        try:
            raw = self._chat(build_extract_prompt(text), expect_json=True)
            return parse_extract_json(raw)
        except Exception:
            # Any failure (network, quota, bad JSON) → heuristics, so the app
            # never breaks just because the cloud hiccuped.
            return heuristic_extract(text, items_df, vendor_hint)

    def recommend(self, context: str) -> str:
        try:
            return self._chat(build_recommend_prompt(context)).strip()
        except Exception:
            return ""

    def reason(self, quotes: list[tuple]) -> str:
        try:
            return self._chat(build_reason_prompt(quotes)).strip()
        except Exception:
            return ""


# --- Ollama provider (used when running locally with Ollama) ------------------
class OllamaProvider(LLMProvider):
    name = "Ollama (local LLM)"
    is_cloud = False

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434"):
        self.model = model or "llama3.1"
        self.host = (host or "http://localhost:11434").rstrip("/")

    def available(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self.host}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        import requests
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if expect_json:
            payload["format"] = "json"
        r = requests.post(f"{self.host}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "")

    def test_connection(self) -> tuple[bool, str]:
        if not self.available():
            return False, (f"No Ollama server reachable at {self.host}. Start "
                           "Ollama, or pick a cloud provider in Settings.")
        return True, f"Ollama reachable at {self.host} (model: {self.model})."


# --- Gemini provider (primary cloud option) -----------------------------------
class GeminiProvider(LLMProvider):
    """Google Gemini via the public Generative Language REST API.

    Deliberately uses plain `requests` (already a dependency) rather than the
    google SDK: one less thing to install on a restricted laptop, and it works
    through a normal corporate HTTPS proxy.
    """

    name = "Gemini (cloud LLM)"
    is_cloud = True
    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str = "", model: str = DEFAULT_GEMINI_MODEL):
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_GEMINI_MODEL
        # Reflect the active model in the label so the UI shows what's in use.
        self.name = f"Gemini {self.model} (cloud LLM)"

    def available(self) -> bool:
        # Cheap check: we have a key. A live ping is done by test_connection().
        return bool(self.api_key)

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        import requests
        gen_cfg = {"temperature": 0.1 if expect_json else 0.2}
        if expect_json:
            gen_cfg["response_mime_type"] = "application/json"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        url = self._ENDPOINT.format(model=self.model)
        r = requests.post(
            url,
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        return _gemini_text(data)

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "No Gemini API key configured."
        try:
            reply = self._chat("Reply with the single word: OK")
            if reply:
                return True, f"Connected to Gemini ({self.model})."
            return False, "Gemini responded but returned no text."
        except Exception as e:  # noqa: BLE001
            return False, f"Gemini connection failed: {_short_error(e)}"


def _gemini_text(data: dict) -> str:
    """Pull the text out of a Gemini generateContent response (or raise)."""
    candidates = data.get("candidates") or []
    if not candidates:
        # Surface a blocked-prompt / error reason instead of silently returning "".
        feedback = data.get("promptFeedback") or {}
        reason = feedback.get("blockReason") or (data.get("error") or {}).get("message")
        raise RuntimeError(f"Gemini returned no candidates ({reason or 'unknown reason'}).")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def _short_error(e: Exception) -> str:
    msg = str(e)
    # Try to surface the API's own error message for HTTP errors.
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            api_msg = (resp.json().get("error") or {}).get("message")
            if api_msg:
                return api_msg
        except Exception:
            pass
    return msg[:200]


# --- OpenAI-compatible provider (OpenAI / OpenRouter / Groq / Mistral / …) -----
# Common base URLs for the OpenAI Chat Completions API. The user can pick one of
# these (or enter a custom URL) in Settings and bring THEIR OWN free key.
OPENAI_COMPATIBLE_PRESETS = {
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "Mistral": "https://api.mistral.ai/v1",
    "SiliconFlow": "https://api.siliconflow.com/v1",
    "Together": "https://api.together.xyz/v1",
    "DeepSeek": "https://api.deepseek.com/v1",
}


class OpenAICompatibleProvider(LLMProvider):
    """Any service that speaks the OpenAI `/chat/completions` API.

    One class serves OpenAI, OpenRouter, Groq, Mistral, SiliconFlow, Together,
    DeepSeek and local OpenAI-style servers — only base_url / api_key / model
    differ. Uses plain `requests` (no SDK), like the rest of the app. Bring your
    OWN key from the provider; never paste keys harvested from public sites.
    """

    name = "OpenAI-compatible (cloud LLM)"
    is_cloud = True
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini",
                 base_url: str = "", label: str | None = None,
                 auth_extra: dict | None = None):
        self.api_key = (api_key or "").strip()
        self.model = model or "gpt-4o-mini"
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if label:
            self.name = label
        self._auth_extra = auth_extra or {}

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}",
             "Content-Type": "application/json"}
        h.update(self._auth_extra)
        return h

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        import requests
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            # We DON'T send response_format=json_object: many free/community
            # OpenAI-compatible models reject it. The prompt asks for JSON and
            # parse_extract_json() tolerates ``` fences, so this stays portable.
            "temperature": 0.1 if expect_json else 0.2,
        }
        r = requests.post(f"{self.base_url}/chat/completions",
                          headers=self._headers(), json=payload, timeout=90)
        r.raise_for_status()
        return _openai_text(r.json())

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"No API key configured for {self.name}."
        try:
            reply = self._chat("Reply with the single word: OK")
            if reply:
                return True, f"Connected to {self.name} (model: {self.model})."
            return False, f"{self.name} responded but returned no text."
        except Exception as e:  # noqa: BLE001
            return False, f"{self.name} connection failed: {_short_error(e)}"


class GitHubModelsProvider(OpenAICompatibleProvider):
    """GitHub Models — free hosted models authenticated with a GitHub PAT.

    GitHub Models exposes an OpenAI-compatible endpoint, so we reuse the base
    class and just point it at the GitHub inference URL with the PAT as the
    bearer token. Model ids are namespaced, e.g. 'openai/gpt-4o-mini',
    'meta/Llama-3.3-70B-Instruct', 'microsoft/Phi-3.5-mini-instruct'.
    Create a fine-grained PAT with 'Models' read access at github.com/settings.
    """

    name = "GitHub Models (cloud LLM)"
    DEFAULT_BASE_URL = "https://models.github.ai/inference"

    def __init__(self, token: str = "", model: str = "openai/gpt-4o-mini",
                 base_url: str = ""):
        model = model or "openai/gpt-4o-mini"
        super().__init__(api_key=token, model=model,
                         base_url=base_url or self.DEFAULT_BASE_URL,
                         label=f"GitHub Models · {model} (cloud LLM)")


def _openai_text(data: dict) -> str:
    """Pull the assistant text out of an OpenAI-style response (or raise)."""
    choices = data.get("choices") or []
    if not choices:
        reason = (data.get("error") or {}).get("message")
        raise RuntimeError(f"No choices returned ({reason or 'unknown reason'}).")
    message = choices[0].get("message") or {}
    return message.get("content") or ""


# --- Claude provider (Anthropic Messages API) ---------------------------------
class ClaudeProvider(LLMProvider):
    """Anthropic Claude via the Messages REST API (plain `requests`, no SDK)."""

    name = "Claude (cloud LLM)"
    is_cloud = True
    _ENDPOINT = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"

    def __init__(self, api_key: str = "", model: str = "claude-haiku-4-5-20251001"):
        self.api_key = (api_key or "").strip()
        self.model = model or "claude-haiku-4-5-20251001"
        self.name = f"Claude {self.model} (cloud LLM)"

    def available(self) -> bool:
        return bool(self.api_key)

    def _chat(self, prompt: str, expect_json: bool = False) -> str:
        import requests
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0.1 if expect_json else 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(
            self._ENDPOINT,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self._API_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        return _claude_text(r.json())

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "No Claude (Anthropic) API key configured."
        try:
            reply = self._chat("Reply with the single word: OK")
            if reply:
                return True, f"Connected to Claude ({self.model})."
            return False, "Claude responded but returned no text."
        except Exception as e:  # noqa: BLE001
            return False, f"Claude connection failed: {_short_error(e)}"


def _claude_text(data: dict) -> str:
    """Pull text out of an Anthropic Messages response (or raise)."""
    blocks = data.get("content")
    if not blocks:
        reason = (data.get("error") or {}).get("message")
        raise RuntimeError(f"Claude returned no content ({reason or 'unknown reason'}).")
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


# --- Provider selection + status ----------------------------------------------
# A registry so the Settings page can list providers without hard-coding them,
# and so a new provider is added in exactly one place.
PROVIDER_CHOICES = [
    ("auto", "Auto-detect (Ollama → Gemini → OpenAI-compatible → GitHub → Local)"),
    ("ollama", "Ollama (local AI)"),
    ("gemini", "Gemini (cloud AI)"),
    ("openai", "OpenAI-compatible (OpenAI / OpenRouter / Groq / Mistral / SiliconFlow)"),
    ("github", "GitHub Models (GitHub PAT)"),
    ("claude", "Claude (Anthropic) (cloud AI)"),
    ("local", "Local rules only (no LLM)"),
]


@dataclass
class ProviderStatus:
    """What the UI needs: the chosen provider plus how to describe it."""

    provider: AIProvider
    label: str                       # e.g. "✓ Gemini Connected (Cloud AI)"
    level: str                       # "ok" | "warn" | "error"
    messages: list[str] = field(default_factory=list)  # fallback / action notes


def _gemini_from_settings(settings: dict) -> GeminiProvider:
    return GeminiProvider(api_key=get_api_key("gemini", settings),
                          model=settings.get("gemini_model") or DEFAULT_GEMINI_MODEL)


def _ollama_from_settings(settings: dict) -> OllamaProvider:
    return OllamaProvider(model=settings.get("ollama_model") or "llama3.1",
                          host=settings.get("ollama_host") or "http://localhost:11434")


def _openai_from_settings(settings: dict) -> OpenAICompatibleProvider:
    base_url = (settings.get("openai_base_url")
                or OpenAICompatibleProvider.DEFAULT_BASE_URL)
    # Name the provider after its host so the status badge is meaningful.
    host = re.sub(r"^https?://(www\.)?", "", base_url).split("/")[0]
    return OpenAICompatibleProvider(
        api_key=get_api_key("openai", settings),
        model=settings.get("openai_model") or "gpt-4o-mini",
        base_url=base_url,
        label=f"OpenAI-compatible · {host} (cloud LLM)",
    )


def _github_from_settings(settings: dict) -> GitHubModelsProvider:
    return GitHubModelsProvider(
        token=get_api_key("github", settings),
        model=settings.get("github_model") or "openai/gpt-4o-mini",
        base_url=settings.get("github_base_url") or "")


def _claude_from_settings(settings: dict) -> ClaudeProvider:
    return ClaudeProvider(
        api_key=get_api_key("claude", settings),
        model=settings.get("anthropic_model") or "claude-haiku-4-5-20251001")


def resolve_provider(settings: dict | None = None) -> ProviderStatus:
    """Pick the active provider from saved settings, applying fallback rules.

    Rules (see the task spec):
      * Ollama is OPTIONAL. If chosen but unreachable, switch to Gemini when a
        key is configured, else drop to the local rules engine.
      * Gemini is the primary cloud option; needs only an API key.
      * "auto" prefers a running Ollama, then Gemini, then local rules.
    """
    settings = settings if settings is not None else load_settings()
    pref = (settings.get("provider") or "auto").lower()

    gemini = _gemini_from_settings(settings)
    has_gemini = gemini.available()
    openai = _openai_from_settings(settings)
    has_openai = openai.available()
    github = _github_from_settings(settings)
    has_github = github.available()
    claude = _claude_from_settings(settings)
    has_claude = claude.available()

    def gemini_status(level="ok", messages=None):
        return ProviderStatus(gemini, "✓ Gemini Connected (Cloud AI)", level,
                              messages or [])

    def openai_status(level="ok", messages=None):
        return ProviderStatus(openai, "✓ OpenAI-compatible Connected (Cloud AI)",
                              level, messages or [])

    def github_status(level="ok", messages=None):
        return ProviderStatus(github, "✓ GitHub Models Connected (Cloud AI)",
                              level, messages or [])

    def claude_status(level="ok", messages=None):
        return ProviderStatus(claude, "✓ Claude Connected (Cloud AI)",
                              level, messages or [])

    def local_status(level="warn", messages=None):
        return ProviderStatus(LocalHeuristicProvider(),
                              "⚠ No AI Provider Configured", level, messages or [])

    # Best available cloud provider, used as a fallback target.
    def first_cloud(level, messages):
        if has_gemini:
            return gemini_status(level, messages)
        if has_openai:
            return openai_status(level, messages)
        if has_github:
            return github_status(level, messages)
        if has_claude:
            return claude_status(level, messages)
        return None

    # Explicit "local rules only".
    if pref == "local":
        return ProviderStatus(LocalHeuristicProvider(),
                              "✓ Local Rules Engine (no LLM)", "ok", [])

    # Explicit Gemini.
    if pref == "gemini":
        if has_gemini:
            return gemini_status()
        return local_status("error",
                            ["Please configure a Gemini API key in Settings."])

    # Explicit OpenAI-compatible (OpenAI / OpenRouter / Groq / Mistral / …).
    if pref == "openai":
        if has_openai:
            return openai_status()
        return local_status("error", [
            "Please add an OpenAI-compatible API key (and base URL) in Settings."])

    # Explicit GitHub Models.
    if pref == "github":
        if has_github:
            return github_status()
        return local_status("error",
                            ["Please add a GitHub personal access token in Settings."])

    # Explicit Ollama — the OPTIONAL local engine, with cloud fallback.
    if pref == "ollama":
        ollama = _ollama_from_settings(settings)
        if ollama.available():
            return ProviderStatus(ollama, "✓ Ollama Available (Local AI)", "ok", [])
        switched = first_cloud(
            "warn", ["Local AI (Ollama) is not available. Switching to cloud AI."])
        if switched:
            return switched
        return local_status("error", [
            "Local AI (Ollama) is not available.",
            "Please configure a cloud provider (Gemini / OpenAI-compatible / "
            "GitHub) in Settings.",
        ])

    # Explicit Claude (Anthropic).
    if pref == "claude":
        if has_claude:
            return claude_status()
        return local_status("error",
                            ["Please configure a Claude (Anthropic) API key in Settings."])

    # Default: auto-detect (Ollama → Gemini → OpenAI-compatible → GitHub → Local).
    ollama = _ollama_from_settings(settings)
    if ollama.available():
        return ProviderStatus(ollama, "✓ Ollama Available (Local AI)", "ok", [])
    chosen = first_cloud("ok", [])
    if chosen:
        return chosen
    return local_status("warn", [
        "No local Ollama and no cloud key found — using the built-in rules "
        "engine (works, but without LLM reasoning). Add a Gemini / "
        "OpenAI-compatible / GitHub key in Settings for cloud AI.",
    ])


def build_provider(provider_id: str, settings: dict | None = None) -> AIProvider:
    """Construct a single named provider (used by the Settings 'Test' button)."""
    settings = settings if settings is not None else load_settings()
    if provider_id == "gemini":
        return _gemini_from_settings(settings)
    if provider_id == "ollama":
        return _ollama_from_settings(settings)
    if provider_id == "openai":
        return _openai_from_settings(settings)
    if provider_id == "github":
        return _github_from_settings(settings)
    if provider_id == "claude":
        return _claude_from_settings(settings)
    return LocalHeuristicProvider()


def get_provider(prefer: str = "auto") -> AIProvider:
    """Backward-compatible helper: return just the provider for a preference."""
    return resolve_provider({"provider": prefer}).provider
