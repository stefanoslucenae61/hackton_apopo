# Sniff times: data findings to discuss with the data expert

Source: `data/bronze/data_bronze.xlsb`, 204,740 rows, 34 columns. All counts reproduce the Data Guide (15 rats, 12,809 samples, 11,346 patients, 645 sessions, 1 Sep 2023 - 30 Sep 2024). Numbers come from `notebooks/01_bronze_data_exploration_executed.ipynb` and `notebooks/02_followup_analysis.py`.

**Cohort used below ("clinical rows"):** all rows except surrogate samples (SUG, SAL, BTE, CAM; these are exactly the 30,869 rows with `DOTS_NAME = SurrogateExp`) = 173,871 rows. Reference = clinic microscopy (`ID_BL_DOTS > 1` is positive). This is one of several possible references, so treat the percentages as a first view.

## 1. The headline: we do not reproduce 95%

| View | TP | FN | FP | TN | Sensitivity | Specificity | Accuracy |
|---|---|---|---|---|---|---|---|
| All rows | 18,651 | 8,116 | 10,938 | 167,035 | 69.7% | 93.9% | 90.7% |
| Clinical rows only | 14,590 | 6,840 | 8,657 | 143,784 | 68.1% | 94.3% | 91.1% |

- Accuracy is dominated by negatives (only about 12% of clinical rows are clinic-positive). A model that never fires would score about 88%.
- Precision of an indication is 14,590 / (14,590 + 8,657) = 63%.
- **Open question:** which metric, rows and reference give the 95%? Some slices come near it, for example `REUSED` 2-3 (accuracy 95.9%) and Gea (94.8%), but we cannot tell what APOPO uses.

## 2. Sniff time separates the classes well; the errors are in the tails

Sniff time (ms, rows with SniffTime > 0), clinical rows:

| Outcome | Median | 10th pct | 90th pct |
|---|---|---|---|
| TP | 3,091 | 2,431 | 4,050 |
| TN | 323 | 119 | 872 |
| FN | 662 | 145 | 1,564 |
| FP | 2,025 | 1,119 | 3,691 |

- Most false negatives are not "almost sniffed long enough": the median FN sniff is 0.66 s, far below any threshold. These look like the rat not engaging, not a threshold problem.
- False positives are long sniffs (median 2.0 s) on clinic-negative samples. A threshold cannot fix those by itself.
- Therefore, moving the threshold trades sensitivity against specificity but will not reach 98% on its own. Extra signals (rat, session, sample history) are needed.

## 3. The threshold column is missing for 79% of rows, and HIT is still consistent

- `tblEVALUATION_SniffThreshold = 0` in 161,898 of 204,740 rows (79%). The share rose from 69% (Sep 2023) to about 80% (since Dec 2023), so it is a systematic recording gap, not a one-off. It is 80%+ on Lines A, B and C and 34-75% on the E lines.
- Where the threshold is filled (42,842 rows), `SniffTime >= threshold` reproduces HIT in 97.2% of rows. 1,189 rows disagree; **1,099 of these are HIT = True with a sniff time below the threshold, and 728 of them have SniffTime = 0.**
- Where the threshold is 0, the data still shows an effective cut-off (HIT rate by sniff time):

| SniffTime (ms) | HIT rate |
|---|---|
| 1-500 | 0.1% |
| 500-1,000 | 0.9% |
| 1,000-1,500 | 13.6% |
| 1,500-1,800 | 43% |
| 1,800-2,000 | 51% |
| 2,000-2,500 | 90% |
| 2,500-3,000 | 98.5% |
| > 3,000 | 99.98% |

  So the real rule is around 1.5-2.5 s but "soft": probably per-rat thresholds that were not stored. `tblRAT_SESSION_SniffThreshold` (2,000 in 175,035 rows, 1,800 in 100) is nearly constant and cannot explain this.
- Thresholds that are filled vary a lot per rat: from 500 to 4,000 ms, with 5-14 distinct values per rat (Salvina median 3,000, Ella median 1,000). That variation is exactly what the project wants to optimise, but it is stored in only 21% of rows.

## 4. Zero sniff times: 13.9% of all rows (28,424)

- Share per rat differs a lot: Chamy 7%, Kenenisa 8%, Ella 9% ... Bertha 26%, Orpheus 33%. This probably explains why the previous team removed those two rats.
- 2,176 zero-sniff rows have HIT = True and 1,137 have REWARD = True. A hit with no recorded sniff means either a sensor drop-out or a manual indication. Either way, SniffTime is not reliable in those rows.
- 77% of zero-sniff rows are Line C (21,931 of 28,424). Is zero a non-sniff (a legitimate "skipped hole") or a missing measurement?

## 5. Samples with APOPO confirmation: "new cases" do not explain the gap

- APOPO confirmation exists for 14,721 rows (7.2%); 1,359 are positive, of which 1,017 rows (87 unique samples) are clinic-negative = new cases.
- Rats indicate new-case samples in 21% of rows, i.e. they find a part of them.
- But only 215 of the 8,657 false alarms (vs. clinic) are on new-case samples. Removing them moves specificity from 94.3% to 94.4%. So the clinic-only label is not what holds accuracy at 91%.
- 2,518 false alarms are on samples that APOPO's own lab confirmed negative. These are real false alarms.

## 6. Context effects (descriptive only, heavily confounded)

| Slice | n | Sensitivity | Specificity | Accuracy |
|---|---|---|---|---|
| Line C | 108,702 | 66.4% | 96.4% | 93.1% |
| Line A | 53,120 | 72.3% | 92.4% | 90.4% |
| Line E-5H | 9,592 | 67.8% | 76.2% | 73.2% |
| Threshold filled | 35,196 | 79.7% | 86.0% | 85.1% |
| Threshold = 0 | 138,675 | 64.6% | 96.4% | 92.6% |
| `REUSED` = 1 | 36,927 | 40.0% (n=742 pos) | 90.5% | 89.5% |
| `REUSED` > 20 | 7,462 | 70.0% | 74.2% | 71.8% |

- Per rat: sensitivity 62-79%, specificity 85-97%. Gea is best (78.5% / 96.7%), Ella weakest on specificity (85.4%), Tivane on sensitivity (62.1%).
- Specificity drops for heavily reused samples (> 20 presentations): possibly rats recognise or learn those samples. Needs a check by sample and by rat.
- Sensitivity by clinic grade: 1AFB 48%, 7AFB 52%, 5AFB 57%, but 19AFB 83% and 2+ 70%. Low bacterial load is harder, but the pattern is not monotonic (small groups).
- Near the threshold, errors jump: a sniff just below the threshold (ratio 0.9-1.0) has a 20% error rate versus 3-6% far below it. Just above the threshold (ratio 1.0-1.5) 91-95% of the indications are false alarms (only about 5% of those samples are positive), while at 2x the threshold or more, 83% of the samples are positive. A sniff only marginally above the threshold is therefore a weak indication. This is the main data-driven lead for a "grey zone" that could be re-checked.
- The session remarks show mixed purposes: "Discrimination 10 Holes ..." (about 55k+ rows), "Refresher Training" (33k) and "Normal & Refresher Training" (20k). Training and operational sessions are mixed in the data and may behave differently.

## 7. Data quality and structure notes

- `ID_BL_DOTS` is consistent per sample (no sample has two values). Only 1,449 patients have more than one sample.
- A sample is presented about 16 times on average (max 209) by 117k sample-rat pairs; modelling needs a grouped split by sample (and patient) to avoid leakage.
- `REWARD` is never True without `HIT`, but 12,547 of 29,589 hits (42%) get no reward. Do not use REWARD as a label.
- 4,696 rows are `STATUS_KNOWNPOS`, yet 21% of them have a clinic-negative result (991 rows, with 196 hits). What does "known positive" mean there? Known positives with `STATUS_BLINDPOS` have 28.6% sensitivity (n=98).
- Temperature = 0 in 44,864 rows (22%) is a missing-value code; temperature is otherwise nearly constant (median 21 C).
- Session start/end gives a median of 12.9 min but a maximum of about 67 hours, so some timestamps are not reliable.
- `ID_GXP_APOPO` is empty, and `Trainer`/`Documenter`/`Handler` have 30 rows each. `ID_GXP_DOTS` is blank for 54.6% of rows; microscopy-vs-GeneXpert conflicts were not analysed yet.
- Not checked: the 3.1% disagreement figure in the guide (I measured 2.8% on all rows with a threshold, a slightly different population).

## 8. Questions for the data expert that come out of this

1. Which metric, rows and reference produce the 95%? We get 91.1% accuracy (68% sensitivity, 94% specificity) against clinic microscopy.
2. Why is `tblEVALUATION_SniffThreshold` 0 for 79% of rows, and where is the threshold actually stored in those cases?
3. What does `SniffTime = 0` with HIT = True mean (sensor failure, manual indication)? Should those be excluded from accuracy?
4. Which sessions are operational and which are training or refresher sessions? Should the 98% be computed on operational sessions only?
5. What does `STATUS_KNOWNPOS` mean when the clinic result is negative (991 rows)?
6. Do rats learn heavily reused samples (specificity 74% above 20 presentations)? How is reuse managed?
7. Why are Line E-5H results so different (specificity 76%)? Is that a different setup or training line?
8. Which diagnostic rule should combine microscopy, clinic GeneXpert and APOPO confirmation?
