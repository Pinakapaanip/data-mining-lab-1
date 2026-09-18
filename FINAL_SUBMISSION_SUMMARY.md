# Annapurna Stores Exam Submission

## A. Landing and storage

The pipeline processed 4,457 source CSV/Parquet files and rejected 0 files. It generated 4,457 deterministic Parquet objects under the Hive-style local fallback layout:

`data_lake/sales_partitioned/year_p=YYYY/month_p=MM/store_p=Sxx/`

The intended MinIO layout is:

`s3://annapurna-sales/daily_sales/year_p=YYYY/month_p=MM/store_p=Sxx/`

The uploader is implemented with the MinIO client and uses `localhost:9000`, `minioadmin/minioadmin`. During final execution, the endpoint timed out, so uploaded objects were **0**. The local Parquet fallback is complete and contains all 4,457 objects.

Source bytes: `68,706,877`.

For `S01 / January 2024`, partition pruning reads 31 of 4,457 files, a `99.30%` file-count reduction. The Parquet partition is `169,055` bytes versus `580,704` source bytes, a `70.89%` byte reduction.

Evidence: `evidence/part_a_partition_metrics.txt`.

## B. Idempotency

Three executions produced:

- Canonical rows: `1,120,924`
- SHA256: `19a3259c6642af4e6ea44b8073b239a52fc74e6b9b8112de88f20835133540c7`
- Identical across runs: `True`
- Raw rows: `1,137,585`
- Duplicate key rows: `29,650`
- Conflicting `(bill_no, line_no)` keys: `0`

Resends are merged deterministically at `(bill_no, line_no)`; complete and partial replacement files are not selected wholesale.

Evidence: `evidence/part_b_idempotency_checksums.txt`.

## C. Dashboard

The dashboard uses SALE, RETURN, DISCOUNT, and VOID revenue lines. TAX and TENDER are excluded. Product joins use `product_code` plus the business date within `products.valid_from` and `products.valid_to`.

- Dashboard rows: `15,120`
- Fact rows: `789,516`
- Total revenue: `522,865,735.75`

Evidence: `evidence/part_c_dashboard_aggregation.csv`.

## D. Historical price

The March 2024 effective-dated query returned `Anmol Cream Biscuit 100g (P103372)` with selling price `43.95`, effective from `2022-01-01` through `2024-07-14`. The query is parameterized by `report_start` and `report_end` and uses `price_revisions.effective_from/effective_to`.

Evidence: `evidence/part_d_price_revisions.csv`.

## E. Federation

DuckDB federation is implemented for:

- Sales: MinIO S3 Parquet at `s3://annapurna-sales/daily_sales/**/*.parquet`
- Masters: generated SQLite database `data_lake/masters.sqlite`
- Join execution: DuckDB

Because MinIO and DuckDB `httpfs` were unavailable during final execution, the evidence uses the local canonical fallback and explicitly records the limitation. The SQLite federation and actual `EXPLAIN ANALYZE` ran successfully. The query includes S01 and January 2024 pruning predicates.

Evidence: `evidence/part_e_federation_explain.txt`.

## F. Reconciliation

| Month | Pipeline | Finance | Difference | Classification |
|---|---:|---:|---:|---|
| 2024-01 | 38,446,071.33 | 38,446,071.33 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-02 | 34,887,085.55 | 34,887,085.55 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-03 | 41,971,649.09 | 42,457,899.09 | -486,250.00 | REVENUE DEFINITION DIFFERENCE |
| 2024-04 | 37,958,457.37 | 37,958,457.37 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-05 | 41,764,716.40 | 41,764,716.40 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-06 | 38,987,082.82 | 38,987,082.82 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-07 | 40,295,160.11 | 40,527,291.81 | -232,131.70 | SOURCE ISSUE |
| 2024-08 | 45,252,181.75 | 45,252,181.75 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-09 | 44,615,037.46 | 44,615,037.46 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-10 | 56,359,195.92 | 56,359,195.92 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-11 | 51,583,838.47 | 51,583,838.47 | 0.00 | NO MATERIAL DIFFERENCE |
| 2024-12 | 50,745,259.48 | 50,745,209.00 | 50.48 | NO MATERIAL DIFFERENCE |

The March variance is not a pipeline bug. `data_2/data/_truth/truth.json` documents a `486,250.00` institutional order invoiced outside the tills and included by finance. July is the documented S07 three-day source gap. December is documented bill-level rounding. October reconciles exactly, confirming TENDER is not counted as revenue.

Evidence: `evidence/part_f_reconciliation.csv`.

## Validation commands

```text
.venv\Scripts\python.exe scripts\A_land_data.py
.venv\Scripts\python.exe scripts\B_idempotency.py
.venv\Scripts\python.exe scripts\C_dashboard.py
.venv\Scripts\python.exe scripts\D_historical_prices.py
.venv\Scripts\python.exe scripts\E_federation.py
.venv\Scripts\python.exe scripts\F_reconciliation.py
```
