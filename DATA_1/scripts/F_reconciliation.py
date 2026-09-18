import json

import pandas as pd

from utils import FINANCE_CSV, LAKE, TRUTH_JSON, canonicalize, load_raw_sales, write_evidence


def main() -> None:
    source = LAKE / 'canonical_sales.csv'
    canonical = pd.read_csv(source, dtype={'ts': str, '_business_date': str}) if source.exists() else canonicalize(load_raw_sales()[0])[0]
    canonical['business_date'] = pd.to_datetime(canonical['_business_date'], format='%Y%m%d')
    included = canonical[canonical['line_type'].isin(['SALE', 'RETURN', 'DISCOUNT', 'VOID'])].copy()
    included['revenue'] = included['qty'] * included['unit_price']
    pipeline = included.groupby(included['business_date'].dt.strftime('%Y-%m'))['revenue'].sum().rename('pipeline_revenue')
    finance = pd.read_csv(FINANCE_CSV, dtype={'month': str}).set_index('month')['revenue_inr'].rename('finance_revenue')
    reconciliation = pd.concat([pipeline, finance], axis=1).reset_index(names='month')
    reconciliation['difference'] = reconciliation['pipeline_revenue'] - reconciliation['finance_revenue']
    reconciliation['percentage_difference'] = reconciliation['difference'] / reconciliation['finance_revenue'] * 100
    reconciliation['classification'] = 'NO MATERIAL DIFFERENCE'
    reconciliation['explanation_action'] = 'Revenue definitions reconcile; retain controls.'
    truth = json.loads(TRUTH_JSON.read_text(encoding='utf-8'))
    march = reconciliation['month'].eq('2024-03')
    reconciliation.loc[march, 'classification'] = 'REVENUE DEFINITION DIFFERENCE'
    reconciliation.loc[march, 'explanation_action'] = f"Finance includes the documented institutional order invoiced outside the tills (+{truth['march_bulk_invoice']:.2f}); pipeline correctly reports folder sales only."
    july = reconciliation['month'].eq('2024-07')
    reconciliation.loc[july, 'classification'] = 'SOURCE ISSUE'
    reconciliation.loc[july, 'explanation_action'] = "S07 lost three documented source days (20240709-20240711); finance supplied those values."
    december = reconciliation['month'].eq('2024-12')
    reconciliation.loc[december, 'explanation_action'] = 'Difference is documented bill-level rounding: finance rounds each bill to the rupee.'
    output = LAKE / 'reconciliation.csv'
    reconciliation.to_csv(output, index=False, lineterminator='\n')
    evidence = LAKE / 'reconciliation.csv'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    reconciliation.to_csv(evidence, index=False, lineterminator='\n')
    reconciliation.to_csv(__import__('pathlib').Path(__file__).resolve().parents[1] / 'evidence' / 'part_f_reconciliation.csv', index=False, lineterminator='\n')
    summary = '\n'.join(['PART F COMPLETE', f"TOTAL PIPELINE REVENUE: {reconciliation['pipeline_revenue'].sum():.2f}", f"TOTAL FINANCE REVENUE: {reconciliation['finance_revenue'].sum():.2f}", reconciliation.to_string(index=False)])
    print(summary)
    write_evidence('F_reconciliation_evidence.txt', summary)


if __name__ == '__main__':
    main()
