# Tier B analysis

Sources: results/tierb50_v1.csv.
Tasks: 50.
Resamples: 5,000. Seed: 20261024. All intervals are 95% percentile intervals from a bootstrap that redraws whole tasks (and, when a task has several runs, redraws its runs). Effects are in percentage points of pass rate.


## Runs per task and effort

| effort | fewest runs on a task | most runs on a task |
|---|---|---|
| low | 1 | 1 |
| high | 1 | 1 |

## Mean pass rate by effort

Mean over tasks of the task's mean pass rate (points, 0-100).

| effort | mean pass rate [95% CI] |
|---|---|
| low | 57.1 [43.4, 70.6] |
| high | 78.0 [66.0, 88.0] |

## Per-task mean pass rate

| task | low | high |
|---|---|---|
| tierb_00_invoices | 100 | 100 |
| tierb_01_invoices | 0 | 0 |
| tierb_02_samples | 100 | 100 |
| tierb_03_bookings | 0 | 100 |
| tierb_04_invoices | 100 | 100 |
| tierb_05_tickets | 100 | 100 |
| tierb_06_samples | 100 | 100 |
| tierb_07_shipments | 0 | 100 |
| tierb_08_invoices | 100 | 100 |
| tierb_09_tickets | 100 | 100 |
| tierb_10_bookings | 100 | 100 |
| tierb_11_tickets | 100 | 100 |
| tierb_12_samples | 0 | 0 |
| tierb_13_invoices | 100 | 100 |
| tierb_14_samples | 100 | 100 |
| tierb_15_bookings | 0 | 100 |
| tierb_16_samples | 100 | 100 |
| tierb_17_shipments | 0 | 100 |
| tierb_18_tickets | 100 | 100 |
| tierb_19_bookings | 100 | 100 |
| tierb_20_invoices | 100 | 100 |
| tierb_21_tickets | 86 | 100 |
| tierb_22_samples | 100 | 100 |
| tierb_23_shipments | 86 | 100 |
| tierb_24_tickets | 100 | 100 |
| tierb_25_tickets | 0 | 0 |
| tierb_26_shipments | 100 | 100 |
| tierb_27_shipments | 100 | 100 |
| tierb_28_tickets | 0 | 0 |
| tierb_29_samples | 0 | 0 |
| tierb_30_samples | 100 | 100 |
| tierb_31_invoices | 100 | 100 |
| tierb_32_shipments | 0 | 0 |
| tierb_33_tickets | 0 | 100 |
| tierb_34_bookings | 100 | 100 |
| tierb_35_bookings | 0 | 100 |
| tierb_36_bookings | 86 | 100 |
| tierb_37_bookings | 0 | 0 |
| tierb_38_invoices | 100 | 100 |
| tierb_39_shipments | 100 | 100 |
| tierb_40_invoices | 0 | 100 |
| tierb_41_bookings | 0 | 100 |
| tierb_42_invoices | 0 | 100 |
| tierb_43_samples | 100 | 100 |
| tierb_44_shipments | 0 | 0 |
| tierb_45_bookings | 100 | 100 |
| tierb_46_samples | 0 | 0 |
| tierb_47_shipments | 0 | 100 |
| tierb_48_tickets | 0 | 0 |
| tierb_49_shipments | 0 | 0 |

## Which switches change the pass rate

Linear regression of each task's mean pass rate on the six switches plus domain (fixed effects). Reference task: auth = apikey_header, pagination = none, other switches off, first domain alphabetically. A coefficient is the change in pass rate (points) when that one switch is turned on, others held fixed. An interval that excludes 0 is evidence of an effect; an interval that includes 0 means the data cannot tell. This is a linear model of a 0-1 rate, so fitted values can pass 100; read it as average differences, not probabilities.

### Effort low

| term | effect (points) [95% CI] |
|---|---|
| intercept | +88.9 [+56.0, +115.0] |
| auth=bearer_login | +2.1 [-27.9, +31.6] |
| auth=cookie_csrf | -0.5 [-28.2, +27.2] |
| pagination=offset | +28.5 [+2.7, +56.2] |
| pagination=cursor | +18.2 [-2.4, +46.1] |
| pagination=link_header | +14.5 [-7.8, +44.0] |
| token_expiry=on | -14.3 [-35.6, +6.7] |
| etag_update=on | -6.2 [-26.5, +13.0] |
| hidden_header=on | -73.8 [-90.9, -54.3] |
| envelope=on | -16.8 [-37.4, +2.8] |
| domain=invoices | +18.1 [-8.6, +45.5] |
| domain=samples | +16.8 [-11.7, +49.1] |
| domain=shipments | -14.1 [-46.7, +9.5] |
| domain=tickets | +7.8 [-15.8, +31.3] |

### Effort high

| term | effect (points) [95% CI] |
|---|---|
| intercept | +134.4 [+101.3, +175.5] |
| auth=bearer_login | -14.5 [-47.0, +17.3] |
| auth=cookie_csrf | -0.5 [-28.3, +25.8] |
| pagination=offset | +3.5 [-28.3, +39.2] |
| pagination=cursor | +16.4 [-10.2, +46.5] |
| pagination=link_header | -11.9 [-43.3, +23.0] |
| token_expiry=on | -8.2 [-36.7, +19.4] |
| etag_update=on | -11.8 [-35.4, +10.9] |
| hidden_header=on | -39.7 [-62.2, -17.5] |
| envelope=on | -17.5 [-40.2, +4.5] |
| domain=invoices | -7.2 [-44.6, +29.6] |
| domain=samples | -23.8 [-58.9, +14.4] |
| domain=shipments | -26.6 [-62.5, +11.4] |
| domain=tickets | -22.9 [-60.9, +13.2] |

### Both efforts in one model

`effort=high` is the change from low to high on the reference task. A row `effort=high x <switch>` is how much that switch's effect differs at high compared with low. Domain effects are shared by both efforts.

| term | effect (points) [95% CI] |
|---|---|
| intercept | +100.8 [+67.6, +132.4] |
| effort=high | +21.6 [-4.2, +56.6] |
| auth=bearer_login | -0.7 [-30.6, +28.1] |
| auth=cookie_csrf | -3.4 [-31.8, +25.2] |
| pagination=offset | +29.9 [+3.3, +58.5] |
| pagination=cursor | +19.2 [-2.1, +46.3] |
| pagination=link_header | +17.0 [-6.0, +47.2] |
| token_expiry=on | -12.5 [-33.5, +9.3] |
| etag_update=on | -6.5 [-26.7, +12.3] |
| hidden_header=on | -73.7 [-91.2, -53.8] |
| envelope=on | -18.5 [-39.7, +1.6] |
| domain=invoices | +5.5 [-21.2, +29.6] |
| domain=samples | -3.5 [-31.5, +29.0] |
| domain=shipments | -20.3 [-47.1, +4.1] |
| domain=tickets | -7.6 [-33.9, +17.0] |
| effort=high x auth=bearer_login | -10.9 [-39.6, +17.7] |
| effort=high x auth=cookie_csrf | +5.8 [-23.3, +32.8] |
| effort=high x pagination=offset | -27.8 [-59.8, +5.3] |
| effort=high x pagination=cursor | -3.9 [-37.7, +29.9] |
| effort=high x pagination=link_header | -31.3 [-63.7, +1.7] |
| effort=high x token_expiry=on | +2.4 [-22.9, +26.5] |
| effort=high x etag_update=on | -5.0 [-27.0, +15.9] |
| effort=high x hidden_header=on | +33.9 [+13.3, +55.5] |
| effort=high x envelope=on | +2.7 [-18.3, +24.1] |

## Effort contrast (high minus low)

Paired by task. Tokens are output tokens (`completion_tokens`, which include reasoning) and total tokens (prompt + completion), averaged per task.

| quantity | value [95% CI] |
|---|---|
| pass-rate gain (points) | +20.9 [+10.6, +32.6] |
| extra output tokens per run | 3,700 [3,152, 4,312] |
| extra total tokens per run | 3,702 [3,154, 4,314] |
| output tokens per point gained | 177.4 [117.4, 331.5] |
| total tokens per point gained | 177.5 [117.4, 331.7] |

## Reliability across runs (pass^k)

Every task has a single run at every effort, so pass^k is not defined here.
