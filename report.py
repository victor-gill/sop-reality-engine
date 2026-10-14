"""
Anomaly Report — Step 2 output of the pipeline.

Prints the gap-analysis findings to the terminal in a readable format.
(Phase 3 will render the Visual Reality Map; for Phase 1 the terminal
report is the deliverable.)
"""

SEVERITY = {
    "unmapped_leakage": "!!",
    "sop_deviation": ">>",
    "threshold_review": "**",
    "cycle_gap": "--",
}


def _section(title: str):
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")


def print_report(analysis: dict, sop_less: bool = False):
    s = analysis["summary"]
    _section("OPERATIONAL REALITY REPORT"
             + ("  [SOP-LESS MODE: process inferred from transactions]"
                if sop_less else ""))
    print(f"SOP steps parsed : {s['steps_parsed']}")
    print(f"Events analyzed  : {s['events_analyzed']} "
          f"({s['vendors_seen']} vendors)")

    leak = analysis["unmapped_leakage"]
    leak_title = ("1. INFERRED PROCESS — recurring spend with no documented SOP"
                  if sop_less else
                  "1. UNMAPPED LEAKAGE")
    _section(f"{leak_title} "
             f"(${s['monthly_leakage']:,.2f}/mo  |  "
             f"${s['annualized_leakage']:,.2f}/yr)")
    if not leak:
        print("None found — every recurring vendor maps to an SOP step.")
    for f in leak:
        print(f"\n  [!!] {f['vendor_display']} — {f['cadence']}, "
              f"{f['transactions']} transactions")
        print(f"       ~${f['monthly_cost']:,.2f}/mo "
              f"(~${f['annualized']:,.2f}/yr)  [{f['category']}]")
        print(f"       {f['detail']}")

    _section("2. SOP DEVIATIONS (documented, not executed)")
    devs = analysis["sop_deviations"]
    if not devs:
        print("None found — every cost-bearing SOP step has activity.")
    for f in devs:
        print(f"\n  [>>] {f['detail']}")

    _section("3. THRESHOLD REVIEWS (verify approval on file)")
    revs = analysis["threshold_reviews"]
    if not revs:
        print("None found — no transactions above SOP approval thresholds.")
    for f in revs:
        print(f"\n  [**] {f['detail']}")

    _section("4. CYCLE-TIME GAPS")
    gaps = analysis["cycle_gaps"]
    if not gaps:
        print("None found — payment cadences match SOP cadences.")
    for f in gaps:
        print(f"\n  [--] {f['detail']}")

    print(f"\n{'=' * 64}\n"
          f"Total annualized unmapped leakage: "
          f"${s['annualized_leakage']:,.2f}\n{'=' * 64}")
