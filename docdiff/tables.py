"""
Tabular extraction — turn many file types into a single pandas DataFrame.

Used by the Procurement Toolkit so quotes/invoices can arrive as Excel, CSV,
Word, PDF, or even a scanned image, and still be compared/validated.

Reliability, by format:
    CSV / XLSX      -> exact (real columns).
    DOCX            -> reliable (Word stores real table cells).
    digital PDF     -> reliable (pdfplumber reads ruled/aligned tables).
    image / scanned -> best-effort: the table grid is RECONSTRUCTED from OCR word
                       positions, so it should be visually checked.

The caller maps columns afterwards, so even rough column names are usable.
"""

from __future__ import annotations

import io

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")


def file_to_dataframe(file_bytes: bytes, filename: str):
    """Dispatch on file type and return a pandas DataFrame of the table found."""
    name = (filename or "").lower()
    if name.endswith((".csv", ".xlsx")):
        from .reconcile import read_table
        return read_table(file_bytes, filename)
    if name.endswith(".docx"):
        return _docx_to_df(file_bytes)
    if name.endswith(".pdf"):
        return _pdf_to_df(file_bytes)
    if name.endswith(_IMAGE_EXTS):
        return _image_to_df(file_bytes)
    raise ValueError(
        f"Unsupported file type for tables: {filename!r}. Upload Excel, CSV, "
        "Word (.docx), PDF, or an image."
    )


# --- Build a DataFrame from raw rows (first row treated as the header) --------
def _rows_to_df(rows: list[list]):
    import pandas as pd

    rows = [[("" if c is None else str(c)).strip() for c in row] for row in rows]
    rows = [r for r in rows if any(cell for cell in r)]  # drop blank rows
    if not rows:
        raise ValueError("No table rows could be read from this file.")

    width = max(len(r) for r in rows)
    rows = [(r + [""] * width)[:width] for r in rows]  # pad ragged rows

    # First row becomes the header; fill blanks and de-duplicate names.
    raw_header = rows[0]
    header, seen = [], {}
    for i, h in enumerate(raw_header):
        name = h or f"Column {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        header.append(name)

    body = rows[1:] if len(rows) > 1 else []
    return pd.DataFrame(body, columns=header)


def _pick_largest(tables: list[list[list]]) -> list[list]:
    """From several extracted tables, return the one with the most cells."""
    tables = [t for t in tables if t and any(any(c for c in row) for row in t)]
    if not tables:
        raise ValueError("No tables were found in the document.")
    return max(tables, key=lambda t: sum(len(row) for row in t))


# --- DOCX --------------------------------------------------------------------
def _docx_to_df(file_bytes: bytes):
    from .convert import extract_word_tables
    return _rows_to_df(_pick_largest(extract_word_tables(file_bytes)))


# --- PDF (digital first, OCR fallback for scans) -----------------------------
def _pdf_to_df(file_bytes: bytes):
    import pdfplumber

    tables: list[list[list]] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            for t in page.extract_tables() or []:
                tables.append(t)

    if tables:
        return _rows_to_df(_pick_largest(tables))

    # No ruled/aligned table found — likely a scanned PDF, so OCR each page.
    return _ocr_pdf_to_df(file_bytes)


# --- OCR-based reconstruction (images / scanned PDFs) ------------------------
def _image_to_df(file_bytes: bytes):
    from .ocr import tesseract_available
    if not tesseract_available():
        raise ValueError("Reading tables from images needs OCR, which isn't "
                         "available on this server.")
    from PIL import Image
    rows = _ocr_image_to_rows(Image.open(io.BytesIO(file_bytes)))
    if not rows:
        raise ValueError("Couldn't read a table from this image. Try a clearer "
                         "scan, or upload the data as Excel/CSV.")
    return _rows_to_df(rows)


def _ocr_pdf_to_df(file_bytes: bytes):
    from .ocr import tesseract_available
    if not tesseract_available():
        raise ValueError("This PDF looks scanned and needs OCR, which isn't "
                         "available on this server.")
    import fitz  # PyMuPDF
    from PIL import Image

    matrix = fitz.Matrix(200 / 72.0, 200 / 72.0)
    all_rows: list[list] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            all_rows.extend(_ocr_image_to_rows(img))
    if not all_rows:
        raise ValueError("Couldn't read a table from this scanned PDF. Try a "
                         "clearer scan, or upload the data as Excel/CSV.")
    return _rows_to_df(all_rows)


def _ocr_image_to_rows(image) -> list[list[str]]:
    """
    Reconstruct a table grid from an image using Tesseract word boxes.

    Words are grouped into rows by their text line, and into columns by clustering
    their horizontal (left) positions — wherever there's a wide horizontal gap, a
    new column begins. This is approximate but works for clean, columnar tables.
    """
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(image, output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        txt = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if txt and conf >= 0:
            words.append({
                "row": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
                "left": int(data["left"][i]),
                "width": int(data["width"][i]),
                "text": txt,
            })
    if not words:
        return []

    # Cluster column positions from word left-edges using gap detection.
    avg_w = sum(w["width"] for w in words) / len(words)
    gap = max(avg_w * 2.0, 20)
    lefts = sorted(w["left"] for w in words)
    centers, current = [], [lefts[0]]
    for x in lefts[1:]:
        if x - current[-1] > gap:
            centers.append(sum(current) / len(current))
            current = [x]
        else:
            current.append(x)
    centers.append(sum(current) / len(current))

    def column_of(left):
        return min(range(len(centers)), key=lambda k: abs(centers[k] - left))

    # Group words by text-row, then by nearest column.
    from collections import defaultdict
    grid = defaultdict(lambda: defaultdict(list))
    for w in words:
        grid[w["row"]][column_of(w["left"])].append((w["left"], w["text"]))

    rows = []
    n_cols = len(centers)
    for key in sorted(grid.keys()):
        cells = [""] * n_cols
        for ci, items in grid[key].items():
            items.sort()
            cells[ci] = " ".join(t for _, t in items)
        rows.append(cells)
    return rows
