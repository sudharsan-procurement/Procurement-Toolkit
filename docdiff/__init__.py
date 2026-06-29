"""
docdiff — a smart document comparison pipeline.

The pipeline runs in stages, one module per stage:

    extract  -> pull raw text out of a PDF or Word file
    segment  -> split that text into clauses / paragraphs
    align    -> match clauses between the two documents BY MEANING
    compare  -> describe each change, flag changed numbers, rank by importance

The Streamlit app (app.py) just calls these in order and draws the result.
Keeping the logic here (and not in app.py) is deliberate: later stages
(OCR, tables, an optional AI layer) can be added without touching the UI.
"""
