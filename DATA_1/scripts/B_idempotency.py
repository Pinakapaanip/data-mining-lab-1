from DATA_1.scripts.utils import LAKE, canonicalize, load_raw_sales, sha256, write_evidence


def run_once(raw, read_stats: dict[str, int]) -> tuple[int, str, dict[str, int]]:
    canonical, stats = canonicalize(raw)
    output = LAKE / 'canonical_sales.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    canonical.to_csv(output, index=False, lineterminator='\n')
    stats.update(read_stats)
    return len(canonical), sha256(output), stats


def main() -> None:
    raw, read_stats = load_raw_sales()
    results = [run_once(raw, read_stats) for _ in range(3)]
    row_count, checksum, stats = results[0]
    identical = all(result[:2] == results[0][:2] for result in results)
    if not identical:
        raise RuntimeError(f'Idempotency failed: {results}')
    evidence = '\n'.join([
        'PART B COMPLETE', f'ROW COUNT: {row_count}', f'SHA256: {checksum}',
        'RUNS: 3', f'IDENTICAL: {identical}', f"RAW ROWS: {stats['raw_rows']}",
        f"DUPLICATE KEY ROWS: {stats['duplicate_key_rows']}", f"CONFLICTING KEYS: {stats['conflicting_keys']}",
        f"INVALID KEY ROWS: {stats['invalid_key_rows']}",
    ])
    print(evidence)
    write_evidence('B_idempotency_evidence.txt', evidence)
    write_evidence('part_b_idempotency_checksums.txt', evidence)


if __name__ == '__main__':
    main()
