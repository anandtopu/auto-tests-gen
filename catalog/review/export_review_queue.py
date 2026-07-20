#!/usr/bin/env python3
"""Export needs_review + orphan entries as CSV for the QE review pass (Stage 4)."""
import csv, json, sys
wtr = csv.writer(sys.stdout)
wtr.writerow(["test_id","title","status","confidence","proposed_app_repos","evidence_endpoints","decision(app_repos or ORPHAN)"])
for l in open(sys.argv[1]):
    e = json.loads(l)
    if e["mapping"]["status"] in ("needs_review", "orphan"):
        wtr.writerow([e["test_id"], e["title"], e["mapping"]["status"],
                      e["mapping"]["confidence"], ";".join(e["mapping"]["app_repos"]),
                      ";".join(e["evidence"]["endpoints"][:3]), ""])
