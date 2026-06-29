"""
Reconcile — compare two tabular exports (OLD vs NEW) record by record.

Given two spreadsheets/CSVs listing the same kind of records (customers, invoices,
…) with a unique ID column, this finds:

    MISSING   - records in OLD that are absent from NEW
    MISMATCH  - records in both, but with one or more differing field values
    EXTRA     - records only in NEW (usually created after a cutover)
    SUMMARY   - counts for each of the above

It returns a single Excel report (as bytes) plus the summary numbers.

Adapted from the user's reconcile.py, but working on UPLOADED files (CSV or Excel)
held in memory, instead of folders of files on disk — so it fits the web app.
"""

from __future__ import annotations

import io
from datetime import datetime

# System/audit fields that almost always differ between two systems even when the
# real business data matches — excluded from comparison so they don't create noise.
DEFAULT_EXCLUDE_COLUMNS = {
    "modified", "modified_by", "creation", "owner", "idx",
    "_user_tags", "_comments", "_assign", "_liked_by",
    "lft", "rgt", "old_parent",
    "created on", "created by", "modified on", "modified by",
    "last modified", "last modified by",
}

# Date formats tried when normalising date-like values for comparison.
_DATE_FORMATS = (
    "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d",
    "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y",
)


def normalize_value(val: str) -> str:
    """Smooth over harmless formatting differences so they aren't flagged as
    mismatches: '371800' vs '371800.0', or '01-04-22' vs '01-04-2022'."""
    val = (val or "").strip()
    if val == "":
        return ""

    try:
        f = float(val)
        return str(int(f)) if f == int(f) else f"{f:g}"
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return val


def read_table(file_bytes: bytes, filename: str):
    """Read an uploaded CSV or Excel file into a text-only DataFrame."""
    import pandas as pd

    name = (filename or "").lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
    elif name.endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str).fillna("")
    else:
        raise ValueError("Reconcile needs a CSV or Excel (.xlsx) file.")

    df.columns = [str(c).strip() for c in df.columns]

    # ERPNext Data Export quirk: a blank leading "Column Labels:" column.
    if len(df.columns) and df.columns[0] == "Column Labels:":
        df = df.drop(columns=[df.columns[0]])

    return df


def list_columns(file_bytes: bytes, filename: str) -> list[str]:
    """Return the column names of an uploaded table (for the ID-column picker)."""
    return list(read_table(file_bytes, filename).columns)


def reconcile(
    old_bytes: bytes,
    old_name: str,
    new_bytes: bytes,
    new_name: str,
    id_column: str,
    prefix_to_strip: str = "",
    exclude_columns: list[str] | None = None,
) -> tuple[bytes, dict]:
    """Compare two tables and return (excel_bytes, summary_dict)."""
    import pandas as pd

    exclude = {c.strip().lower() for c in (exclude_columns or [])} | DEFAULT_EXCLUDE_COLUMNS

    old_df = read_table(old_bytes, old_name)
    new_df = read_table(new_bytes, new_name)

    # Accept ERPNext's "ID" column as an alias for the technical id column.
    for df in (old_df, new_df):
        if "ID" in df.columns and id_column not in df.columns:
            df.rename(columns={"ID": id_column}, inplace=True)

    if id_column not in old_df.columns or id_column not in new_df.columns:
        raise ValueError(f"Both files must contain the ID column '{id_column}'.")

    # Clean the ID column (strip stray quotes/space) and drop blank/duplicate rows.
    for df in (old_df, new_df):
        df[id_column] = df[id_column].astype(str).str.strip().str.strip('"').str.strip()
    old_df = old_df[old_df[id_column] != ""].drop_duplicates(subset=id_column)
    new_df = new_df[new_df[id_column] != ""].drop_duplicates(subset=id_column)

    # Remember the new system's real ID before optional prefix-stripping.
    new_df["_new_actual_id"] = new_df[id_column]
    if prefix_to_strip:
        new_df[id_column] = new_df[id_column].apply(
            lambda v: v[len(prefix_to_strip):] if v.startswith(prefix_to_strip) else v
        )
        new_df = new_df.drop_duplicates(subset=id_column)

    old_df = old_df.set_index(id_column)
    new_df = new_df.set_index(id_column)

    old_ids, new_ids = set(old_df.index), set(new_df.index)
    missing_ids = sorted(old_ids - new_ids)
    extra_ids = sorted(new_ids - old_ids)
    common_ids = sorted(old_ids & new_ids)

    missing_df = old_df.loc[missing_ids].reset_index() if missing_ids else pd.DataFrame()

    extra_df = new_df.loc[extra_ids].reset_index() if extra_ids else pd.DataFrame()
    if not extra_df.empty and "_new_actual_id" in extra_df.columns:
        extra_df = extra_df.drop(columns=["_new_actual_id"])

    # Fields to compare = columns present in both, minus excluded/system fields.
    compare_cols = [
        c for c in old_df.columns
        if c in new_df.columns
        and c.strip().lower() not in exclude
        and c != "_new_actual_id"
    ]

    mismatches = []
    renamed_count = 0
    for rid in common_ids:
        old_row, new_row = old_df.loc[rid], new_df.loc[rid]
        new_actual_id = new_row.get("_new_actual_id", rid)
        was_renamed = new_actual_id != rid
        if was_renamed:
            renamed_count += 1
        for col in compare_cols:
            old_val = str(old_row[col]).strip()
            new_val = str(new_row[col]).strip()
            if normalize_value(old_val) != normalize_value(new_val):
                mismatches.append({
                    "ID": rid,
                    "New System ID (if renamed)": new_actual_id if was_renamed else "",
                    "Field": col,
                    "Old Value": old_val,
                    "New Value": new_val,
                })
    mismatch_df = pd.DataFrame(mismatches)

    summary = {
        "Total in Old": len(old_ids),
        "Total in New": len(new_ids),
        "Missing in New": len(missing_ids),
        "Only in New (new records)": len(extra_ids),
        "Matched IDs": len(common_ids),
        "Matched IDs with Mismatches":
            mismatch_df["ID"].nunique() if not mismatch_df.empty else 0,
        "Total Field-Level Mismatches": len(mismatch_df),
    }
    if prefix_to_strip:
        summary[f"Matched via '{prefix_to_strip}' prefix"] = renamed_count

    excel_bytes = _to_excel(summary, missing_df, mismatch_df, extra_df)
    return excel_bytes, summary


def _to_excel(summary: dict, missing_df, mismatch_df, extra_df) -> bytes:
    import pandas as pd

    summary_df = pd.DataFrame([{"Metric": k, "Value": v} for k, v in summary.items()])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        if not missing_df.empty:
            missing_df.to_excel(writer, sheet_name="Missing", index=False)
        if not mismatch_df.empty:
            mismatch_df.to_excel(writer, sheet_name="Mismatch", index=False)
        if not extra_df.empty:
            extra_df.to_excel(writer, sheet_name="Extra", index=False)
    return buffer.getvalue()
