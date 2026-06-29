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

## Project layout

```
app.py                 The Streamlit user interface (drawing only)
docdiff/
  extract.py           Stage 1 — read text from PDF / .docx
  segment.py           Stage 2 — split text into clauses
  align.py             Stage 3 — match clauses by meaning (local model)
  numbers.py           Number extraction + comparison
  compare.py           Stage 4 — classify & rank each change
  export.py            Stage 5 — Excel report
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

### The one deferred decision

For that future AI summary layer:

| Option | Pros | Cons |
|---|---|---|
| **Local model** (current default) | Free, fully private, nothing sent out | Slightly weaker summaries |
| **Cloud AI API** | Sharper summaries | Small cost, sends contract text to a provider |

Everything in this MVP uses the **local** option. The cloud option is only worth
revisiting for the optional summary layer, and only if privacy rules allow it.
