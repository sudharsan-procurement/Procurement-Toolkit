# 📑 Smart Document Comparison (MVP)

Compares two contracts by **meaning**, not just text. It matches clauses even
when they've been **reordered**, flags **changed numbers** loudly, and ranks the
important changes at the top.

This first version is **free and fully local** — no AI API key, and no document
text ever leaves the app.

---

## What it does (in plain English)

You upload two versions of a contract (an "original" and a "revised"). The app:

1. **Reads** the text out of each file (digital PDF or Word `.docx`).
2. **Splits** each document into clauses, using the clause numbering (1, 1.1, (a)…).
3. **Matches** each clause in the original to its counterpart in the revised
   version — *by meaning*, so a clause that was moved or lightly reworded is still
   recognised as the same clause.
4. **Compares** each matched pair: shows the word-level change, and — crucially —
   pulls out every number and compares the figures explicitly.
5. **Presents** a ranked list: number changes first, then added/removed clauses,
   then wording changes, then trivial formatting (hidden by default). You can
   download the whole thing as an **Excel report**.

---

## How "matching by meaning" works (the clever bit)

Each clause is converted into a list of numbers (a "fingerprint" of its meaning)
by a small language model that runs **on this machine** — no internet call, no
API key. Clauses that mean similar things get similar fingerprints. The app then
finds the single best pairing between the two documents' clauses. Because it
pairs on meaning rather than position, **a reordered clause is still matched to
the right counterpart.**

The model is `all-MiniLM-L6-v2` (~90 MB). It downloads automatically the first
time you run a comparison, then is cached.

> If the model ever fails to load, the app automatically falls back to a simpler
> text-similarity match so it still works — just a little less clever about
> heavy paraphrasing.

---

## Run it on your own computer

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the link it prints (usually http://localhost:8501).

To sanity-check the engine without the UI or the model:

```bash
python selftest.py
```

---

## Deploy it free (Streamlit Community Cloud)

1. Create a free GitHub account if you don't have one.
2. Put this whole folder into a new GitHub repository.
3. Go to **share.streamlit.io**, sign in with GitHub, click **New app**.
4. Pick your repo and set the main file to `app.py`.
5. Click **Deploy**. The first build takes a few minutes (it installs the
   packages and downloads the model). After that it's live at a shareable URL.

No secrets or API keys to configure — it's entirely self-contained.

---

## AI Quote Analysis — providers & how to choose one

The **🤖 AI Quote Analysis** tool reads vendor quotations (PDF, Word, images,
scanned PDFs) and produces a procurement-style summary, commercial/technical
analysis, risk assessment, vendor ranking, and a recommendation.

It runs on a pluggable **AI provider** layer (`docdiff/ai_providers.py`). The app
**does not require any local AI install** — a local Ollama server is entirely
**optional**. This matters on a corporate laptop where you can't install or run
background services.

### The providers

| Provider | Where it runs | Needs | Best for |
|---|---|---|---|
| **Local rules** (`LocalHeuristicProvider`) | In-process | nothing | Always-on safety net; no LLM reasoning |
| **Gemini** (`GeminiProvider`) | Google cloud | an API key + outbound HTTPS | **Corporate laptops** — no install, no admin rights |
| **Ollama** (`OllamaProvider`) | Your machine | a local Ollama install | Fully offline LLM reasoning |
| **OpenAI / Claude** | Cloud | _placeholders_ | Wired up later (structure is in place) |

All LLM providers share the same procurement prompts and JSON parsing, so they
behave identically — only the transport differs. Document reading and OCR always
happen **locally**; only the extracted text is sent to a cloud provider, and only
when one is selected.

### Use Gemini (recommended for cloud / no-install)

1. Get a free API key at <https://aistudio.google.com/apikey>.
2. Open **⚙️ Settings** in the app, select **Gemini (cloud AI)**, paste the key.
3. Click **Test connection**, then **Save configuration**.
4. The status indicator shows **✓ Gemini Connected (Cloud AI)**.

The default model is `gemini-2.5-flash` (fast and inexpensive); you can change it
in Settings (e.g. `gemini-2.5-pro`). Instead of pasting the key you may set the
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) environment variable — the app reads the
saved config first, then falls back to the environment.

### Use Ollama (optional, fully local)

1. Install **Ollama** from <https://ollama.com> and run `ollama pull llama3.1`.
2. In **⚙️ Settings** choose **Auto-detect** or **Ollama** and set the model name.
3. The status indicator shows **✓ Ollama Available (Local AI)**.

### How provider switching works

The provider is chosen by `resolve_provider()` from your saved settings:

- **Auto-detect** (default): a running **Ollama → Gemini (if a key is set) →
  Local rules**.
- **Ollama** selected but **not reachable**: the app automatically switches to
  Gemini when a key is configured — *"Local AI (Ollama) is not available.
  Switching to Gemini Cloud AI."* — otherwise it drops to the local rules engine.
- **Gemini** selected but **no key**: *"Please configure a Gemini API key in
  Settings."* and the local rules engine is used so the app still works.

The sidebar and the AI Quote Analysis page always show a clear status badge:
**✓ Ollama Available (Local AI)**, **✓ Gemini Connected (Cloud AI)**, or
**⚠ No AI Provider Configured**.

### Where settings are stored

Settings (provider choice + API key) are saved to a per-user JSON file at
`~/.smartdoc/config.json` (owner-only permissions where the OS supports it). It
lives in your home directory, so writing it needs no admin rights, and it is
**git-ignored** so keys never land in the repo. Set `SMARTDOC_CONFIG_DIR` to use a
different location.

### Adding another provider (e.g. enabling OpenAI/Claude)

`OpenAIProvider` and `ClaudeProvider` already subclass the shared `LLMProvider`
base. To enable one, implement its `_chat()` against the vendor's API and have
`available()` return `bool(self.api_key)` — the extraction, recommendation, and
reasoning logic is inherited unchanged, and it's already registered in
`PROVIDER_CHOICES` for the Settings dropdown.

---

## Project layout

```
app.py                 The Streamlit user interface (incl. ⚙️ Settings page)
docdiff/
  extract.py           Stage 1 — read text from PDF / .docx (local + OCR)
  segment.py           Stage 2 — split text into clauses
  align.py             Stage 3 — match clauses by meaning (local model)
  numbers.py           Number extraction + comparison
  compare.py           Stage 4 — classify & rank each change
  export.py            Stage 5 — Excel report
  ai_providers.py      AI provider layer (Local / Gemini / Ollama / OpenAI / Claude)
  settings.py          Local config + API-key resolution (file → env var)
  quote_intelligence.py Provider-independent quote scoring / ranking / risk
samples/               Two example contracts for testing
selftest.py            Runs the engine on the samples and prints results
requirements.txt       The packages to install
```

The five stages are deliberately separate modules so the roadmap items below can
be added without rewriting what already works.

---

## What's intentionally NOT in this MVP (the roadmap)

In priority order, matching the project plan:

1. **OCR for scanned PDFs** — read text out of photographed/scanned documents
   (PaddleOCR / PP-Structure). Today, a scanned PDF is detected and flagged, not
   read.
2. **Table & number hardening** — proper table extraction and smarter handling of
   dates, durations, and figures inside tables.
3. **Optional AI "meaning summary" layer** — a one-line plain-English summary of
   *why* each change matters. This is the one piece that may use a cloud AI API.

### The AI "meaning summary" decision (resolved)

The optional AI layer is now implemented as a **pluggable provider** rather than a
single hard-coded choice — see [AI Quote Analysis — providers](#ai-quote-analysis--providers--how-to-choose-one)
above:

| Option | Pros | Cons |
|---|---|---|
| **Local rules** (default safety net) | Free, fully private, nothing sent out | No LLM reasoning |
| **Gemini** (cloud) | Sharp reasoning, **no install / no admin rights** | Sends extracted text to Google; needs an API key |
| **Ollama** (local LLM) | Sharp reasoning, fully offline | Needs a local install (not possible on locked-down laptops) |

The app **auto-detects** what's available and falls back gracefully, so it works
on a corporate laptop with **no local model installed** — just add a Gemini key in
**⚙️ Settings** for full AI reasoning, or run with the local rules engine as-is.

### The same AI layer powers Compare documents

**Document Tools → Compare documents** uses the *same* provider setting. After it
detects the clause-level changes (locally, by meaning), it can add an optional
**🤖 AI Contract Review** — an analyst-style write-up of *what changed, why it
matters, and the risk flags* (higher penalties, reduced warranty, longer payment
terms, removed protections, new obligations). With no LLM configured it's hidden
and the offline rule-based "Executive Summary" is shown instead. When a cloud
provider is active, only the **changed clause text** (not the whole document) is
sent out — a notice says so, and switching to local Ollama keeps everything
on-device.
