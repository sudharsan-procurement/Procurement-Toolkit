"""
Quick self-test of the core pipeline WITHOUT the heavy embedding model.

It feeds the two sample contracts through segment -> align (text fallback) ->
compare -> export, and prints the ranked result. Run:  python selftest.py
"""

from pathlib import Path

from docdiff.segment import segment
from docdiff.align import align
from docdiff.compare import compare_pairs
from docdiff.export import changes_to_excel

here = Path(__file__).parent
v1 = (here / "samples" / "contract_v1.txt").read_text(encoding="utf-8")
v2 = (here / "samples" / "contract_v2.txt").read_text(encoding="utf-8")

old_segs = segment(v1)
new_segs = segment(v2)
print(f"Segments: old={len(old_segs)}  new={len(new_segs)}")

pairs, used_model = align(old_segs, new_segs, threshold=0.5)
print(f"Used embedding model: {used_model}  (False = text fallback, fine for test)")

changes = compare_pairs(pairs)
print(f"\nRanked changes ({len(changes)}):\n" + "-" * 60)
for i, c in enumerate(changes, 1):
    print(f"{i}. [{c.category}] clause {c.label}  (sev {c.severity}, sim {c.similarity:.2f})")
    for nc in c.number_changes:
        print(f"     number: {nc.old} -> {nc.new}  ({nc.description})")

xlsx = changes_to_excel(changes)
out = here / "selftest_report.xlsx"
out.write_bytes(xlsx)
print(f"\nWrote Excel report: {out}  ({len(xlsx)} bytes)")
