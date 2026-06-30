"""
Self-test for the AI layer — providers, fallback logic, FCRA engine, the
contract-summary seam, and the usage counter. No network and no pytest.

It mocks `requests` so each provider's REQUEST SHAPE (URL, auth, payload) and
RESPONSE PARSING are checked offline, and exercises resolve_provider()'s
fallback rules. Run:  python selftest_ai.py   (exits non-zero on any failure)
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from unittest import mock

# Isolate settings + analytics into a throwaway dir BEFORE importing docdiff
# (settings.CONFIG_DIR is resolved from this env var at import time).
os.environ["SMARTDOC_CONFIG_DIR"] = tempfile.mkdtemp(prefix="smartdoc_selftest_")

import requests  # noqa: E402

from docdiff import ai_providers as ap  # noqa: E402
from docdiff import summary as smry  # noqa: E402
from docdiff import analytics  # noqa: E402
from docdiff.fcra import (  # noqa: E402
    analyze_fcra, ai_fcra_review, build_fcra_excel, build_fcra_prompt,
)
from docdiff.compare import Change  # noqa: E402
from docdiff.numbers import NumberChange  # noqa: E402


# --- tiny test runner --------------------------------------------------------
_RESULTS = []


def test(name):
    def deco(fn):
        _RESULTS.append((name, fn))
        return fn
    return deco


# --- requests mocking helpers ------------------------------------------------
class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


@contextmanager
def mock_post(payload):
    """Patch requests.post to capture the call and return `payload`."""
    cap = {}

    def fake_post(url, headers=None, json=None, params=None, timeout=None, **kw):
        cap.update(url=url, headers=headers or {}, json=json or {}, params=params or {})
        return FakeResp(payload)

    with mock.patch.object(requests, "post", fake_post):
        yield cap


@contextmanager
def mock_post_raises(exc=ConnectionError("network down")):
    def fake_post(*a, **k):
        raise exc
    with mock.patch.object(requests, "post", fake_post):
        yield


@contextmanager
def ollama_reachable(reachable: bool):
    """Patch requests.get so OllamaProvider.available() is deterministic."""
    def fake_get(url, timeout=None, **kw):
        if reachable:
            return FakeResp({"models": []}, 200)
        raise ConnectionError("refused")
    with mock.patch.object(requests, "get", fake_get):
        yield


# --- provider request-shape tests --------------------------------------------
@test("Gemini: endpoint, key param, json mime, parse")
def _():
    with mock_post({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}) as cap:
        p = ap.GeminiProvider(api_key="AIzaTEST", model="gemini-2.5-flash")
        assert p.narrate("hi") == "OK"
        out = p.extract("x", None, "f")  # expect_json path
    assert cap["url"].endswith("models/gemini-2.5-flash:generateContent"), cap["url"]
    assert cap["params"] == {"key": "AIzaTEST"}, cap["params"]
    assert cap["json"]["generationConfig"]["response_mime_type"] == "application/json"
    assert isinstance(out, dict) and "Vendor Name" in out


@test("OpenAI-compatible: /chat/completions, bearer, messages, parse")
def _():
    with mock_post({"choices": [{"message": {"content": "OK"}}]}) as cap:
        p = ap.OpenAICompatibleProvider(api_key="sk-x", model="m",
                                        base_url="https://api.groq.com/openai/v1")
        assert p.narrate("hello") == "OK"
    assert cap["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer sk-x"
    assert cap["json"]["messages"][0]["content"] == "hello"
    assert cap["json"]["model"] == "m"


@test("GitHub Models: github endpoint + PAT bearer")
def _():
    with mock_post({"choices": [{"message": {"content": "OK"}}]}) as cap:
        p = ap.GitHubModelsProvider(token="ghp_x", model="openai/gpt-4o-mini")
        assert p.narrate("hi") == "OK"
    assert cap["url"] == "https://models.github.ai/inference/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer ghp_x"


@test("Claude: messages endpoint, x-api-key, version, max_tokens, parse")
def _():
    with mock_post({"content": [{"type": "text", "text": "OK"}]}) as cap:
        p = ap.ClaudeProvider(api_key="sk-ant-x", model="claude-haiku-4-5-20251001")
        assert p.narrate("hi") == "OK"
    assert cap["url"] == "https://api.anthropic.com/v1/messages"
    assert cap["headers"]["x-api-key"] == "sk-ant-x"
    assert cap["headers"]["anthropic-version"] == "2023-06-01"
    assert cap["json"]["max_tokens"] >= 1


@test("Ollama: available() via /api/tags, _chat via /api/generate (json format)")
def _():
    with ollama_reachable(True):
        p = ap.OllamaProvider(model="llama3.1", host="http://localhost:11434")
        assert p.available() is True
    with mock_post({"response": "OK"}) as cap:
        assert p.narrate("hi") == "OK"
        p._chat("x", expect_json=True)
    assert cap["url"].endswith("/api/generate")
    assert cap["json"]["format"] == "json"


@test("Response parsers raise on empty payloads")
def _():
    for fn, payload in [(ap._gemini_text, {"promptFeedback": {"blockReason": "SAFETY"}}),
                        (ap._openai_text, {"error": {"message": "bad key"}}),
                        (ap._claude_text, {"error": {"message": "bad key"}})]:
        try:
            fn(payload)
            assert False, f"{fn.__name__} should have raised"
        except RuntimeError:
            pass


# --- shared LLM behaviour ----------------------------------------------------
@test("extract() falls back to heuristics when the LLM call fails")
def _():
    with mock_post_raises():
        p = ap.OpenAICompatibleProvider(api_key="sk-x")
        fields = p.extract("Greetings from Acme Pvt Ltd. Total Rs 1,20,000.", None, "acme")
    assert fields["Vendor Name"]["value"] == "Acme Pvt Ltd"
    assert fields["Total Value"]["value"] == "1,20,000"


@test("parse_extract_json tolerates ```json fences and missing keys")
def _():
    raw = '```json\n{"Vendor Name": {"value": "X", "confidence": 0.9}}\n```'
    out = ap.parse_extract_json(raw)
    assert out["Vendor Name"]["value"] == "X"
    assert out["Total Value"]["value"] is None  # missing key filled


@test("non-LLM provider narrate() returns '' (callers fall back)")
def _():
    assert ap.LocalHeuristicProvider().narrate("anything") == ""
    assert ap.LocalHeuristicProvider().is_llm is False


# --- resolve_provider fallback rules -----------------------------------------
@test("resolve: explicit local / gemini-no-key / claude-no-key")
def _():
    with ollama_reachable(False):
        assert ap.resolve_provider({"provider": "local"}).level == "ok"
        r = ap.resolve_provider({"provider": "gemini"})
        assert r.level == "error" and "Gemini" in r.messages[0]
        r = ap.resolve_provider({"provider": "claude"})
        assert r.level == "error"


@test("resolve: ollama unreachable + gemini key -> switch to cloud")
def _():
    with ollama_reachable(False):
        r = ap.resolve_provider({"provider": "ollama", "gemini_api_key": "AIzaX"})
    assert r.level == "warn"
    assert r.label == "✓ Gemini Connected (Cloud AI)"
    assert "not available" in " ".join(r.messages).lower()


@test("resolve: auto chain falls through Gemini -> OpenAI -> GitHub -> Claude")
def _():
    with ollama_reachable(False):
        r = ap.resolve_provider({"provider": "auto", "openai_api_key": "sk-x"})
        assert r.label.startswith("✓ OpenAI-compatible"), r.label
        r = ap.resolve_provider({"provider": "auto", "github_token": "ghp_x"})
        assert r.label.startswith("✓ GitHub Models"), r.label
        r = ap.resolve_provider({"provider": "auto", "anthropic_api_key": "sk-ant-x"})
        assert r.label.startswith("✓ Claude"), r.label
        r = ap.resolve_provider({"provider": "auto"})
        assert r.provider.name == ap.LocalHeuristicProvider().name


@test("resolve: ollama reachable wins under auto")
def _():
    with ollama_reachable(True):
        r = ap.resolve_provider({"provider": "auto", "gemini_api_key": "AIzaX"})
    assert r.label == "✓ Ollama Available (Local AI)"


@test("every PROVIDER_CHOICES id is buildable")
def _():
    for pid, _label in ap.PROVIDER_CHOICES:
        if pid == "auto":
            continue
        prov = ap.build_provider(pid, {})
        assert hasattr(prov, "extract") and hasattr(prov, "narrate")


# --- FCRA engine -------------------------------------------------------------
@test("FCRA: risky agreement -> High with expected findings")
def _():
    risky = ("USD 500000 foreign donor. Recipient may use funds for any purpose. "
             "Administrative overhead up to 35%. Shall sub-grant to downstream "
             "partner organisations and pool funds with local funds. Funds may be "
             "used abroad. A foreign national shall serve as a trustee.")
    res = analyze_fcra(risky)
    ids = {f["id"] for f in res["findings"]}
    assert res["rating"] == "High"
    for need in ("FC-TRANSFER", "FC-ADMIN-CAP", "FC-COMMINGLE",
                 "FC-USE-OUTSIDE-INDIA", "FC-FOREIGN-FUNCTIONARY"):
        assert need in ids, need
    assert len(build_fcra_excel(res)) > 0


@test("FCRA: clean agreement -> Low, negation guard works")
def _():
    clean = ("Recipient registered under the Foreign Contribution (Regulation) "
             "Act receives foreign contribution only into its designated FCRA "
             "Account at the State Bank of India, New Delhi Main Branch. Complies "
             "with FCRA, keeps separate books, files annual return Form FC-4, caps "
             "administrative expenses at 15%, shall not transfer the funds to any "
             "other person, and shall not use funds outside India. USD 100000.")
    res = analyze_fcra(clean)
    found = {f["id"] for f in res["findings"] if f["kind"] == "found"
             and f["severity"] != "info"}
    assert res["rating"] == "Low", res["rating"]
    assert "FC-TRANSFER" not in found and "FC-USE-OUTSIDE-INDIA" not in found


@test("FCRA: grounded prompt carries rule refs; non-LLM review is ''")
def _():
    res = analyze_fcra("USD 1 lakh foreign donor; shall sub-grant to partners.")
    prompt = build_fcra_prompt("text", res)
    assert "FC-TRANSFER" in prompt and "ONLY" in prompt
    assert ai_fcra_review("text", res, ap.LocalHeuristicProvider()) == ""


# --- contract-summary seam ---------------------------------------------------
@test("summary.ai_summarize_changes: '' for non-LLM, digest excludes formatting")
def _():
    changes = [
        Change(label="4.2", category="Number change", severity=100,
               old_text="Payment within 30 days", new_text="Payment within 60 days",
               number_changes=[NumberChange(old="30", new="60",
                                            description="increased by 100.0%")],
               similarity=0.9),
        Change(label="2.1", category="Formatting only", severity=10,
               old_text="The  Parties", new_text="The Parties",
               number_changes=[], similarity=1.0),
    ]
    assert smry.ai_summarize_changes(changes, ap.LocalHeuristicProvider()) == ""
    digest = smry.build_change_digest(changes)
    assert "30 -> 60" in digest and "2.1" not in digest


# --- usage analytics ---------------------------------------------------------
@test("analytics: one row per session/day; users = distinct identities")
def _():
    analytics.record_visit("sess-A", None)
    analytics.record_visit("sess-A", None)          # idempotent
    analytics.record_visit("sess-B", "a@x.com")
    analytics.record_visit("sess-B", "a@x.com")     # idempotent
    analytics.record_visit("sess-C", "b@x.com")
    today = analytics.counts_for()
    assert today["sessions"] == 3, today
    assert today["users"] == 2, today
    assert analytics.totals()["sessions"] == 3
    assert analytics.daily_counts(7)[-1]["sessions"] == 3


# --- run ---------------------------------------------------------------------
def main():
    passed = failed = 0
    for name, fn in _RESULTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}\n          {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(_RESULTS)} total")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
