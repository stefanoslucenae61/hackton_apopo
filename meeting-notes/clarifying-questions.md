# Clarifying Questions — APOPO PoC

_Raised by Parci (PM) on Day 2 morning. Julie replied 07/10/2026._

---

## Julie's Answers (07/10/2026)

| #                    | Question                   | Answer                                                                                                                                                                   | Impact on plan                                                                             |
| -------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| 9                    | Exact deadline             | **1 January 2027** — old LIMS read-only max 3 months after go-live, then gone                                                                                            | Horizon 1 must complete in ~12 weeks                                                       |
| 10                   | Source system preservation | Old tool decommissioned after go-live (max 3 months read-only). Original raw exports must be archived, unchanged, readable — FDA requirement                             | Archived read-only source copy is a hard deliverable, not optional                         |
| 12                   | Study continuity           | **Study cannot stop** — rats screen every working day including December                                                                                                 | Zero-downtime is non-negotiable; must be in KPI commitment                                 |
| 13                   | FDA audit trail            | **Hard constraint**: every field mapping must be human-approved and documented (field mapped, approver name, timestamp). Vendor's test migration was never row-verified. | Agentic migration must include human-in-the-loop approval layer + full immutable audit log |
| Migration preference | —                          | Julie: greenfield (redesign properly). Pieter: no downtime, no paying twice. Both want the trade-off shown — cost, duration, risk per option                             | Present all three options with explicit trade-off table; don't just recommend one          |

**Key quote from Julie:** _"We must be able to prove to the FDA that no record was lost or changed."_

---

## Still Open

### Data & Technical

1. **What is the new LIMS system?** Vendor, version, target data model. We can't design the migration without knowing what we're migrating _to_.

2. **`room_temp_c` shows 0.0 in many records** — is zero a valid temperature or a missing value sentinel? Same question for zero `indication_threshold_s`. Do we leave them out?
3. **`specimen_type` is empty in all rows we've seen** — is this expected, and does it affect which threshold or protocol applies?
4. **What links rat session records to lab confirmation records?** Is `specimen_id` the reliable join key, or does it break across systems?
5. **How many data sources exist in total?** We've seen the AutomatedCage export and the LIMS export — are there others (e.g. a national TB register, partner clinic systems)?
6. **What is a "reuse count" for a sample**, and does it affect how we interpret indication results?

### Source System Ownership

8. **Who owns the source system?**
   - Is the current LIMS a third-party vendor product, or built/maintained internally by APOPO?
   - If vendor-owned: do we need their cooperation to extract data, and is there a contract or API that governs access?
   - Who has the authority to approve decommissioning?

### Migration & Scope

11. **Who owns the migration decision on APOPO's side** — Pieter, IT, or both? Is there an IT team we haven't spoken to?

### Accuracy & Detection

14. **How is the 95% accuracy figure currently calculated?** Internally by APOPO, or externally validated? Sensitivity only, or balanced with specificity?
15. **Is the FDA 98% requirement specifically for sensitivity**, or overall accuracy?
16. **Which rats are currently active and operational?** Any with known performance issues already excluded from the 95% claim?
17. **What is the defined follow-up protocol when a rat flags a sample?** Does it always go to lab confirmation, or only sometimes?

### Reporting & Stakeholders

18. **What does current reporting look like?** Manual Excel, LIMS-generated reports, something else? Who produces it and how long does it take?
19. **Who are the specific donors APOPO reports to, and what format do they require?** This scopes Horizon 2.
20. **Are there external auditors**, and if so, what do they need?

### Budget & Roadmap

21. **Is the €1,000/month ceiling for Horizon 1 compute costs only**, or does it include element61 support?
22. **Is there budget headroom for Horizons 2 and 3**, or is the board deciding on the full three-horizon commitment today?
23. **Who maintains the solution after delivery** — Julie's team or ongoing element61 support?

---

## Updated Priority (blockers for today's board presentation)

| #   | Question                   | Why it's a blocker                                         |
| --- | -------------------------- | ---------------------------------------------------------- |
| 1   | New LIMS system            | Can't design migration target without it                   |
| 2   | Gold standard for accuracy | Affects every KPI we present                               |
| 8   | Source system ownership    | Vendor lock-in could kill the Jan 1 timeline               |
| 14  | How 95% is calculated      | Board will ask — we need to know if we can stand behind it |

_Questions 9, 10, 12, 13 are now answered and absorbed into the plan._
