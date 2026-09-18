import duckdb

from utils import LAKE, load_masters, write_evidence


QUERY = """
WITH sales AS (
    SELECT *, strptime(_business_date, '%Y%m%d')::DATE AS business_date
    FROM read_csv('data_lake/canonical_sales.csv', header=true,
        types={'ts':'VARCHAR', '_business_date':'VARCHAR'}, ignore_errors=false)
), revenue AS (
    SELECT s.bill_no, s.line_no, s._store AS store, s.product_code,
               s.qty * s.unit_price AS revenue, s.line_type,
               s.business_date, p.category_id
    FROM sales s
    LEFT JOIN products p ON p.product_code = s.product_code
        AND s.business_date >= p.valid_from AND s.business_date < p.valid_to
    WHERE upper(s.line_type) IN ('SALE', 'RETURN', 'DISCOUNT', 'VOID')
)
SELECT store, COALESCE(c.category_name, 'UNKNOWN') AS category,
       strftime(business_date, '%A') AS day_of_week,
       strftime(business_date, '%Y-%m') AS month,
       SUM(revenue) AS revenue
FROM revenue
LEFT JOIN product_categories c ON c.category_id = revenue.category_id
GROUP BY store, category, day_of_week, month
ORDER BY store, month, category, day_of_week
"""


def main() -> None:
    con = load_masters()
    dashboard = con.execute(QUERY).fetchdf()
    fact = con.execute("""
        SELECT s.bill_no, s.line_no, s._store AS store, p.product_sk,
               s.product_code, strptime(s._business_date, '%Y%m%d')::DATE AS business_date,
               s.qty * s.unit_price AS revenue, s.line_type
        FROM read_csv('data_lake/canonical_sales.csv', header=true,
            types={'ts':'VARCHAR', '_business_date':'VARCHAR'}, ignore_errors=false) s
        LEFT JOIN products p ON p.product_code = s.product_code
            AND strptime(s._business_date, '%Y%m%d')::DATE >= p.valid_from
            AND strptime(s._business_date, '%Y%m%d')::DATE < p.valid_to
        WHERE upper(s.line_type) IN ('SALE', 'RETURN', 'DISCOUNT', 'VOID')
        ORDER BY store, business_date, bill_no, line_no
    """).fetchdf()
    products = con.execute("SELECT product_sk, product_code, product_name, category_id, valid_from, valid_to FROM products ORDER BY product_sk").fetchdf()
    categories = con.execute("SELECT category_id, category_name FROM product_categories ORDER BY category_id").fetchdf()
    stores = con.execute("SELECT * FROM stores ORDER BY store_id").fetchdf()
    con.close()
    LAKE.mkdir(parents=True, exist_ok=True)
    dashboard.to_csv(LAKE / 'dashboard.csv', index=False, lineterminator='\n')
    fact.to_csv(LAKE / 'fact_sales.csv', index=False, lineterminator='\n')
    products.to_csv(LAKE / 'dim_product.csv', index=False, lineterminator='\n')
    categories.to_csv(LAKE / 'dim_category.csv', index=False, lineterminator='\n')
    stores.to_csv(LAKE / 'dim_store.csv', index=False, lineterminator='\n')
    evidence = '\n'.join([
        'PART C COMPLETE', f'DASHBOARD ROWS: {len(dashboard)}', f"TOTAL REVENUE: {dashboard['revenue'].sum():.2f}",
        f'FACT ROWS: {len(fact)}', "REVENUE TYPES: SALE, RETURN, DISCOUNT, VOID; TAX and TENDER excluded",
        'PRODUCT JOIN: product_code plus business_date within valid_from/valid_to',
    ])
    print(evidence)
    write_evidence('C_dashboard_evidence.txt', evidence)
    dashboard.to_csv(__import__('pathlib').Path(__file__).resolve().parents[1] / 'evidence' / 'part_c_dashboard_aggregation.csv', index=False, lineterminator='\n')


if __name__ == '__main__':
    main()
