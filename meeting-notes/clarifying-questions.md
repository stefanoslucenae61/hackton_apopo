# Clarifying Questions — APOPO PoC
_Raised by Parci (PM) on Day 2 morning. Filter and send before stand-up._

---

## Data & Technical

1. **What is the new LIMS system?** Vendor, version, target data model. We can't design the migration without knowing what we're migrating *to*.
2. **Is `lab_result` the confirmed gold standard**, or can clinic results override it in some cases? This directly affects how we calculate accuracy.
3. **`room_temp_c` shows 0.0 in many records** — is zero a valid temperature or a missing value sentinel? Same question for zero `indication_threshold_s`.
4. **`specimen_type` is empty in all rows we've seen** — is this expected, and does it affect which threshold or protocol applies?
5. **What links rat session records to lab confirmation records?** Is `specimen_id` the reliable join key, or does it break across systems?
6. **How many data sources exist in total?** We've seen the AutomatedCage export and the LIMS export — are there others (e.g. a national TB register, partner clinic systems)?
7. **What is a "reuse count" for a sample**, and does it affect how we interpret indication results?

---

## Source System Ownership

8. **Who owns the source system?**
   - Is the current LIMS a third-party vendor product, or built/maintained internally by APOPO?
   - If vendor-owned: do we need their cooperation to extract data, and is there a contract or API that governs access?
   - If internally owned: who is the technical owner — Julie, an IT team, or someone else we haven't spoken to?
   - Who has the authority to approve decommissioning the source system after migration?

---

## Migration & Scope

9. **Exact December deadline?** Day matters for timeline and T-shirt sizing.
10. **Source system preservation:** after migration, does the old LIMS stay live in parallel, or is it decommissioned? This changes the rollback strategy entirely.
11. **Who owns the migration decision on APOPO's side** — Pieter, IT, or both? Is there an IT team we haven't spoken to?
12. **Can APOPO pause new session data entry during the migration window**, or does data need to keep flowing while we migrate? Zero-downtime has a very different cost if sessions run daily.
13. **Are there regulatory or donor audit requirements around data lineage** — e.g. must every record have a traceable origin from source to target?

---

## Accuracy & Detection

14. **How is the 95% accuracy figure currently calculated?** Internally by APOPO, or externally validated? Sensitivity only, or balanced with specificity?
15. **Is the FDA 98% requirement specifically for sensitivity** (catching true positives), or overall accuracy? These lead to very different optimisation strategies.
16. **Which rats are currently active and operational?** Are there rats with known performance issues already excluded from the 95% claim?
17. **What is the defined follow-up protocol when a rat flags a sample?** Does it always go to lab confirmation, or only sometimes?

---

## Reporting & Stakeholders

18. **What does current reporting look like?** Manual Excel, LIMS-generated reports, something else? Who produces it and how long does it take?
19. **Who are the specific donors APOPO reports to, and what format do they require?** This scopes Horizon 2.
20. **Are there external auditors**, and if so, what do they need — raw data exports, summary reports, or access to a live system?

---

## Budget & Roadmap

21. **Is the €1,000/month ceiling for Horizon 1 compute costs only**, or does it include element61 support?
22. **Is there budget headroom for Horizons 2 and 3**, or is the board deciding on the full three-horizon commitment today?
23. **Who maintains the solution after delivery** — APOPO's own team (Julie?), or is ongoing element61 support expected?

---

## Priority (blockers for today's presentation)

| # | Question | Why it's a blocker |
|---|----------|--------------------|
| 1 | New LIMS system | Can't design migration target without it |
| 2 | Gold standard for accuracy | Affects every KPI we present |
| 8 | Source system ownership | Vendor lock-in could kill the migration timeline |
| 9 | Exact December date | Determines feasibility of each horizon |
| 14 | How 95% is calculated | Board will ask — we need to know if we can stand behind it |
