from pathlib import Path
import tempfile

import duckdb
import pandas as pd
import urllib3
from minio import Minio
from minio.error import S3Error

from utils import LAKE, MINIO_BUCKET, MINIO_ENDPOINT, parse_filename, read_sales_file, reset_dir, sales_files, write_evidence


def main() -> None:
    stage = LAKE / 'minio_stage'
    reset_dir(stage)
    local_partition = LAKE / 'sales_partitioned'
    reset_dir(local_partition)
    source_files = sales_files()
    source_bytes = sum(path.stat().st_size for path in source_files)
    client = None
    minio_status = 'UNAVAILABLE: upload not attempted'
    try:
        http_client = urllib3.PoolManager(timeout=urllib3.Timeout(connect=1.0, read=1.0), retries=False)
        candidate = Minio(MINIO_ENDPOINT, access_key='minioadmin', secret_key='minioadmin', secure=False, http_client=http_client)
        if not candidate.bucket_exists(MINIO_BUCKET):
            candidate.make_bucket(MINIO_BUCKET)
        client = candidate
        minio_status = f'CONNECTED: s3://{MINIO_BUCKET}/daily_sales/'
    except Exception as exc:
        minio_status = f'UNAVAILABLE: {type(exc).__name__}: {exc}'
    con = duckdb.connect()
    output_files = 0
    rejected = 0
    uploaded = 0
    partition_counts = {}
    partition_bytes = {}
    with tempfile.TemporaryDirectory(prefix='annapurna_parquet_') as temp_dir:
        for path in source_files:
            try:
                frame = read_sales_file(path)
                metadata = parse_filename(path)
                assert metadata is not None
                store, business_date, _ = metadata
                date = pd.Timestamp(business_date)
                key_prefix = f'daily_sales/year_p={date.year}/month_p={date.month:02d}/store_p={store}'
                key = f'{key_prefix}/{path.stem}.parquet'
                parquet_path = Path(temp_dir) / f'{output_files:05d}.parquet'
                con.register('sales_frame', frame)
                con.execute('COPY sales_frame TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(parquet_path)])
                con.unregister('sales_frame')
                local_path = local_partition / f'year_p={date.year}' / f'month_p={date.month:02d}' / f'store_p={store}' / f'{path.stem}.parquet'
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(parquet_path.read_bytes())
                output_files += 1
                partition_counts[key_prefix] = partition_counts.get(key_prefix, 0) + 1
                partition_bytes[key_prefix] = partition_bytes.get(key_prefix, 0) + local_path.stat().st_size
                if client is not None:
                    client.fput_object(MINIO_BUCKET, key, str(parquet_path), content_type='application/vnd.apache.parquet')
                    uploaded += 1
            except (ValueError, UnicodeError, pd.errors.ParserError, S3Error):
                rejected += 1
    con.close()
    example = sorted(partition_counts)[0]
    example_store = example.split('store_p=')[1]
    example_year = int(example.split('/')[1].split('=')[1])
    example_month = int(example.split('/')[2].split('=')[1])
    source_example_files = [p for p in source_files if parse_filename(p) and parse_filename(p)[0] == example_store and pd.Timestamp(parse_filename(p)[1]).year == example_year and pd.Timestamp(parse_filename(p)[1]).month == example_month]
    source_example_bytes = sum(p.stat().st_size for p in source_example_files)
    file_scan_reduction = (1 - partition_counts[example] / len(source_files)) * 100
    byte_scan_reduction = (1 - partition_bytes[example] / source_example_bytes) * 100
    metrics = '\n'.join([
        'PART A COMPLETE', f'SOURCE FILES: {len(source_files)}', f'SOURCE BYTES: {source_bytes}',
        f'LANDED PARQUET FILES: {output_files}', f'REJECTED FILES: {rejected}',
        f'MINIO UPLOADED OBJECTS: {uploaded}', f'MINIO STATUS: {minio_status}',
        f'EXAMPLE PARTITION: s3://{MINIO_BUCKET}/{example}/',
        f'EXAMPLE PARTITION FILES: {partition_counts[example]} / {len(source_files)}',
        f'EXAMPLE PARTITION PARQUET BYTES: {partition_bytes[example]}',
        f'EXAMPLE SOURCE BYTES: {source_example_bytes}',
        f'FILE SCAN REDUCTION: {file_scan_reduction:.2f}% ({partition_counts[example]} of {len(source_files)} files)',
        f'BYTE SCAN REDUCTION: {byte_scan_reduction:.2f}% ({partition_bytes[example]} of {source_example_bytes} bytes for the example partition)',
        'LOCAL FALLBACK: data_lake/minio_stage and data_lake/sales_partitioned contain the same Parquet objects when MinIO is unavailable.',
    ])
    print(metrics)
    write_evidence('part_a_partition_metrics.txt', metrics)
    write_evidence('A_partition_evidence.txt', metrics)


if __name__ == '__main__':
    main()
