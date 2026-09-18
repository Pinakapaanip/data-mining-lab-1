import urllib.request

import duckdb

from DATA_1.scripts.utils import LAKE, MINIO_BUCKET, MINIO_ENDPOINT, ensure_sqlite_masters, write_evidence


def minio_available() -> bool:
    try:
        urllib.request.urlopen(f'http://{MINIO_ENDPOINT}/minio/health/live', timeout=3)
        return True
    except Exception:
        return False


def main() -> None:
    sqlite_path = ensure_sqlite_masters()
    con = duckdb.connect()
    sqlite_error = ''
    try:
        con.execute('LOAD sqlite')
        con.execute(f"ATTACH '{sqlite_path.as_posix()}' AS masters (TYPE SQLITE, READ_ONLY)")
    except Exception as exc:
        sqlite_error = f'SQLite attach failed: {type(exc).__name__}: {exc}'
        con.execute(f"CREATE OR REPLACE VIEW products AS SELECT * FROM read_csv('{(LAKE / 'dim_product.csv').as_posix()}')")
        con.execute(f"CREATE OR REPLACE VIEW product_categories AS SELECT * FROM read_csv('{(LAKE / 'dim_category.csv').as_posix()}')")
    use_minio = minio_available()
    httpfs_error = ''
    if use_minio:
        try:
            con.execute('INSTALL httpfs')
            con.execute('LOAD httpfs')
            con.execute("SET s3_endpoint='localhost:9000'")
            con.execute("SET s3_access_key_id='minioadmin'")
            con.execute("SET s3_secret_access_key='minioadmin'")
            con.execute("SET s3_use_ssl=false")
            con.execute("SET s3_url_style='path'")
            sales_source = f"read_parquet('s3://{MINIO_BUCKET}/daily_sales/**/*.parquet', hive_partitioning=true, union_by_name=true)"
            source_label = f's3://{MINIO_BUCKET}/daily_sales/**/*.parquet'
        except Exception as exc:
            use_minio = False
            httpfs_error = f'httpfs setup failed: {type(exc).__name__}: {exc}'
    if not use_minio:
        sales_source = "read_csv('data_lake/canonical_sales.csv', header=true, types={'ts':'VARCHAR', '_business_date':'VARCHAR'})"
        source_label = 'local fallback: data_lake/canonical_sales.csv'
    product_table = 'masters.products' if not sqlite_error else 'products'
    query = f"""
        SELECT p.category_id, COUNT(*) AS line_count,
               SUM(CAST(s.qty AS DOUBLE) * CAST(s.unit_price AS DOUBLE)) AS revenue
        FROM {sales_source} s
        JOIN {product_table} p ON p.product_code = s.product_code
          AND strptime(CAST(s._business_date AS VARCHAR), '%Y%m%d')::DATE >= p.valid_from
          AND strptime(CAST(s._business_date AS VARCHAR), '%Y%m%d')::DATE < p.valid_to
        WHERE upper(s.line_type) IN ('SALE', 'RETURN', 'DISCOUNT', 'VOID')
          AND s._store = 'S01' AND s._business_date >= '20240101' AND s._business_date < '20240201'
        GROUP BY p.category_id ORDER BY p.category_id
    """
    explain = con.execute('EXPLAIN ANALYZE ' + query).fetchall()
    result = con.execute(query).fetchdf()
    con.close()
    plan = '\n'.join(str(row[1] if len(row) > 1 else row[0]) for row in explain)
    evidence = '\n'.join([
        'PART E COMPLETE', f'SALES SOURCE: {source_label}', f'MASTER SOURCE: {sqlite_path}',
        'JOIN LOCATION: DuckDB', 'PRUNING FILTER: _store=S01 and _business_date in 20240101..20240131',
        f'HTTPFS/MINIO: {"ACTIVE" if use_minio else "UNAVAILABLE; local fallback used"}', httpfs_error, sqlite_error,
        'FEDERATED RESULT:', result.to_string(index=False), 'EXPLAIN ANALYZE:', plan,
    ])
    print(evidence)
    write_evidence('part_e_federation_explain.txt', evidence)
    write_evidence('E_federation_evidence.txt', evidence)


if __name__ == '__main__':
    main()
