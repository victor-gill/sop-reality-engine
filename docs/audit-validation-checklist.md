# Audit Validation Checklist

Use this after the engine produces its findings and **before** anything
goes to a client. The engine is exhaustive; you are the judge. Every
finding gets one of three dispositions:

- **CONFIRM** — real issue, goes in the report.
- **DISMISS** — false positive, documented below with the reason.
- **ESCALATE** — can't tell from the documents; needs a client question.

Rule of thumb: if you can't explain *why* it's a problem in one
sentence a non-accountant owner would understand, it's not confirmed yet.

---

## 0. Before you review findings — data sanity

- [ ] **Same period?** All documents cover the same date range. A P&L
      through May vs. bank through June manufactures variances.
- [ ] **Complete?** Ask: "Is this every business account and card?" A
      missing card looks exactly like missing execution.
- [ ] **Duplicates across documents?** The same payroll run appears in
      the bank export *and* the payroll ledger. The workflow ledger
      dedups these, but eyeball the finding list once.
- [ ] **Cash vs. accrual?** If the books are accrual and the bank is
      cash, timing differences are expected — note it, don't flag it.

## 1. Unmapped leakage (recurring spend, no SOP step)

The engine flags anything recurring. Recurring ≠ wrong. Check:

- [ ] **Annual charges misread as monthly?** Look at the actual
      transaction *dates*, not the cadence label. Two charges ~12 months
      apart are an annual renewal, not monthly leakage. Confirm the real
      interval before writing it up.
- [ ] **Seasonal vendors?** Spend clustered in specific months (HVAC in
      summer, holiday freight, harvest-season suppliers). Legitimate
      seasonality — disposition: add an SOP step with a seasonal note,
      or dismiss as expected.
- [ ] **Known one-offs?** Project spend, equipment purchases, setup or
      onboarding fees. Non-recurring by nature — confirm with the client,
      then dismiss.
- [ ] **Forgotten SaaS?** The most common *true* positive. Nobody
      remembers signing up for it, which is exactly why it's leaking.
      Quick web search on the vendor name before the client call.
- [ ] **Owner commingling?** Personal spend on the business card. Flag
      it separately as a bookkeeping issue — don't call it leakage.
- [ ] **Related-party vendors?** Family members, the owner's other
      business. Note it; don't alarm.

## 2. SOP deviations (documented step, zero transactions)

- [ ] **Is the SOP still current?** Stale documentation is the #1 cause.
      If the process was retired, the fix is retiring the step, not
      finding the spend.
- [ ] **Timing.** Quarterly/annual steps may simply not have come due
      inside the data window. Check the step's frequency against the
      window before flagging.
- [ ] **Another account?** Paid from a card or account not in the
      dataset — back to checklist item 0.
- [ ] **Bundled billing?** The service is inside another vendor's
      invoice (e.g., insurance bundled with the lease).

## 3. Threshold reviews (over the approval limit)

- [ ] **Approval on file?** This is the actual question. Ask for the
      signed/email approval.
- [ ] **Split transactions?** Same vendor, adjacent dates, amounts just
      under the threshold. That's the pattern that matters more than
      any single overage.
- [ ] **Emergency spend?** Verbal approval happens. If legitimate,
      the fix is documenting it after the fact, not a lecture.

## 4. Cycle-time gaps (cadence drift)

- [ ] **Net-30 vs. calendar month.** The most boring and most common
      cause.
- [ ] **Vendor changed the billing date.** Confirm before rewriting
      the SOP.
- [ ] **SOP is outdated.** The business changed; the document didn't.
      Update the step.

## 5. Payroll gaps

- [ ] **Record with no payment:** second account? Voided and reissued
      check? Duplicate payroll entry? Terminated employee still in the
      system?
- [ ] **Payment with no record:** off-cycle bonus? Contractor paid as a
      vendor (1099 vs. W-2 mix-up)? Owner draw mislabeled as payroll?
- [ ] Payroll is P1 by default — people not getting paid (or money
      leaving without a record) outranks everything else on this list.

## 6. P&L variance (books vs. bank)

- [ ] **Accrual vs. cash timing** — the #1 cause. An invoice booked in
      March and paid in April is not a problem.
- [ ] **Miscategorization** — the #2 cause. The money is real; it's in
      the wrong bucket.
- [ ] **Missing periods** in the P&L export.
- [ ] **Intercompany/related-party eliminations** the bookkeeper
      handled outside the export.

## 7. Before the report goes out

- [ ] Every CONFIRMED finding has a one-sentence plain-English
      explanation and a dollar figure.
- [ ] Every DISMISSED finding has its reason noted (client will ask).
- [ ] Every ESCALATED finding has a specific question written for the
      client — not "please review," but "was the $X payment to Y on
      [date] approved, and by whom?"
- [ ] No real client names, account numbers, or raw transaction detail
      in anything that leaves your machine unencrypted.
