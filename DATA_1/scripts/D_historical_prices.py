from utils import LAKE, load_masters, write_evidence


def main() -> None:
    report_start = "2024-03-01"
    report_end = "2024-04-01"
    con = load_masters()
    query = """
        SELECT p.product_sk, p.product_code, p.product_name, c.category_name,
               r.mrp, r.selling_price, r.effective_from, r.effective_to
        FROM products p
        JOIN product_categories c ON c.category_id = p.category_id
        JOIN price_revisions r ON r.product_sk = p.product_sk
                WHERE lower(c.category_name) LIKE '%biscuit%'
                    AND lower(p.product_name) LIKE '%biscuit%'
          AND p.valid_from < CAST(? AS DATE)
          AND p.valid_to > CAST(? AS DATE)
          AND r.effective_from < CAST(? AS DATE)
          AND r.effective_to > CAST(? AS DATE)
        ORDER BY p.product_name, p.product_sk, r.effective_from
    """
    result = con.execute(query, [report_end, report_start, report_end, report_start]).fetchdf()
    con.close()
    if result.empty:
        raise RuntimeError("No biscuit price revision overlaps the reporting period")
    output = LAKE / "historical_biscuit_prices.csv"
    result.to_csv(output, index=False, lineterminator="\n")
    selected = result.iloc[0]
    evidence = "\n".join([
        "PART D COMPLETE", f"REPORT PERIOD: [{report_start}, {report_end})",
        f"SELECTED PRODUCT: {selected['product_name']} ({selected['product_code']})",
        f"SELLING PRICE REVISION: {selected['selling_price']}",
        f"REVISION EFFECTIVE: {selected['effective_from']} to {selected['effective_to']}",
        f"OVERLAPPING BISCUIT REVISIONS: {len(result)}",
        "LOOKUP: product_code plus effective_from/effective_to overlap; change only report_start/report_end",
    ])
    print(evidence)
    write_evidence("D_price_evidence.txt", evidence)
    result.to_csv(__import__('pathlib').Path(__file__).resolve().parents[1] / 'evidence' / 'part_d_price_revisions.csv', index=False, lineterminator='\n')


if __name__ == "__main__":
    main()
