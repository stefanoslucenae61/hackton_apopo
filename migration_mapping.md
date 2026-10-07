# Migration mapping: Excel (`data_bronze.xlsb`) to New LIMS export (`T1_NewLIMS_Export.csv`)

Source: `data/bronze/data_bronze.xlsb` (204,740 rows; checked through the silver parquet, which only drops columns).
Target: `data/bronze/T1_NewLIMS_Export.csv` (60,778 rows, 16 columns, `;` separated, 1 Sep 2023 to 31 Oct 2023).

All 16 target columns map to an Excel column. Each mapping was verified by joining the two files on session, rat, sample and position. 60,868 of the 60,778 target rows matched (the join over-counts slightly because some silver rows share a key). The 255 duplicate silver keys also show up in the target (114 duplicates).

## Column mapping

| New LIMS column | Excel column | Transformation | Check |
|---|---|---|---|
| `site_code` | `CONFIGURATION_NAME` | Value map: `Line A` to `DAR-L-A`, `Line C` to `DAR-L-C`, `Line E-3H` to `DAR-L-E-3H`, `LINE E-5H` to `DAR-L-E-5H` | 1:1 on all matched rows |
| `session_ref` | `ID_EVALUATION_SESSION` | `"S-" + id`, so 22012 becomes `S-022012` | Every target session exists in the Excel |
| `session_dt` | `SESSION_DATE` | Excel serial to `dd/mm/yyyy` | 99.2% match as full-date equality; the mismatches are not explained |
| `animal_id` | `RAT_NAME` | Value map, see the rat table below | 15 to 15, strictly 1:1 |
| `specimen_id` | `ID_SAMPLE` | `"SP" + id` | Every target specimen exists in the Excel |
| `specimen_type` | `SAMPLE_TYPE` | Copied as is, NaN becomes an empty string | 100% match, including `New Pot` (136) and `Pre` |
| `clinic_result` | `ID_BL_DOTS` | `1` to `NEG`, `>1` to `POS` | 100% match; the bacterial grade (1AFB, 2+ and so on) is lost |
| `clinic_gxp` | `ID_GXP_DOTS` | NaN to empty, `1` to `NEG` | See the notes below |
| `lab_result` | `ID_BL_APOPO` | NaN to empty, `1` to `NEG`, `>1` to `POS` | 100% match; the grade is lost |
| `control_type` | `STATUS_KNOWNPOS`, `STATUS_BLINDPOS` | `BLINDPOS` to `BLIND`, `KNOWNPOS` to `KNOWN`, neither to `NONE` | See the notes below |
| `rewarded` | `REWARD` | `True` to `Y`, `False` to `N` | 99.97% match |
| `indication` | `HIT` | `True` to `Y`, `False` to `N` | 99.97% match |
| `position` | `RUN` + `HOLE` | Concatenate, so `A` and `1` become `A1` | Used as a join key |
| `sniff_s` | `SniffTime` | Divide by 1000 | 94.4% exact at 3 decimals, see the notes below |
| `indication_threshold_s` | `tblEVALUATION_SniffThreshold` | Divide by 1000, keep `0` as `0` | 99.9% match. `tblRAT_SESSION_SniffThreshold` matches only 6.5%, so it is the wrong source |
| `room_temp_c` | `TEMPERATURE` | Copied as is, a blank for some `0` values | 99.4% match |

### Rat mapping (`RAT_NAME` to `animal_id`)

| `RAT_NAME` | `animal_id` | `RAT_NAME` | `animal_id` |
|---|---|---|---|
| Bertha | RAT-101 | Orpheus | RAT-110 |
| Bieber | RAT-102 | Salvina | RAT-111 |
| Chamy | RAT-103 | Splinter | RAT-112 |
| Chilleta | RAT-104 | Tamasha | RAT-113 |
| Ella | RAT-105 | Tirunesh | RAT-114 |
| Gea | RAT-106 | Tivane | RAT-115 |
| Kenenisa | RAT-107 | | |
| Malaika | RAT-108 | | |
| Mayele | RAT-109 | | |

The ids follow the alphabetical order of the rat names.

## Things to flag to the migration team

- **`sniff_s` loses precision on some rows.** About 5.6% of rows are rounded to 0.1 s in the target (784 ms became 0.8). Check whether that is a rounding rule in the export or a bug.
- **A tiny share of rows disagree.** `indication` and `rewarded` differ on 16 rows each, and `room_temp_c` differs on about 0.6%, with some blanks where the Excel has 0 or 21. Pull those rows and ask the source owner.
- **`clinic_gxp`:** the target contains only `NEG` or blank. Excel `ID_GXP_DOTS` also has `2`, `4` and `8` (positive grades). The `NEG` and blank counts match the Excel, so none of the positives fall in the target period.
- **`control_type`:** this merges a pair of booleans into one field. Some rows are `KNOWN` while `BLINDPOS` is true in the Excel. Check how the target resolves that conflict.
- **Date range.** The target covers only 1 Sep to 31 Oct 2023, which is 60,778 of the 204,740 Excel rows. The mappings are validated on that window only.
- **The ID prefixes are inferred.** They were derived from the data (`S-` + session id, `SP` + sample id, `RAT-` + 101 and up). Whether the new LIMS has its own id scheme is a question for the migration team.

## Excel columns with no target

| Excel column | Why it matters |
|---|---|
| `ID_PATIENT` | Needed for patient-level grouping and leakage-free splits |
| `DOTS_NAME` | Partner clinic |
| `START_TIME`, `END_TIME` | Session duration checks |
| `DATE_INCOMING` | Sample arrival date |
| `tblEVALUATION_SESSION_REMARKS` | Separates operational from training sessions; rule 7 in `04_stage2_clean_demo_subset` cannot run on the new data without it |
| `tblRAT_SESSION_REMARKS` | Rat-level session remarks |
| `REUSED` | Number of times a sample has been presented |
| `tblRAT_SESSION_SniffThreshold` | Rat-session threshold |

## Excel columns already dropped in stage 1

`ID_GXP_APOPO`, `ReadTotalSnifftime`, `Trainer`, `Documenter`, `Handler`, `ID_CONFIGURATION` and `LEVEL_NAME` are not in the target either. See `notebooks/03_stage1_drop_columns.ipynb` for the reasons.
