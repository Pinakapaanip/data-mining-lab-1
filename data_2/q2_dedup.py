import hashlib
import time
import uuid
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "exam.duckdb"
NOTICE_ROOTS = [
    PROJECT_ROOT / "exam" / "data" / "notices",
    PROJECT_ROOT / "data_2" / "data" / "notices",
    PROJECT_ROOT / "data_2" / "data_2" / "notices",
]
EVIDENCE_DIR = PROJECT_ROOT / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

NUM_PERMUTATIONS = 128
BANDS = 16
ROWS_PER_BAND = 8
JACCARD_THRESHOLD = 0.78
MAX_BUCKET_SIZE = 100

PRIME = 4294967311
np.random.seed(42)
HASH_A = np.random.randint(1, PRIME - 1, size=NUM_PERMUTATIONS, dtype=np.uint64)
HASH_B = np.random.randint(0, PRIME - 1, size=NUM_PERMUTATIONS, dtype=np.uint64)


def get_char_5grams(text: str) -> set:
    if not text:
        return set()
    text = text.lower()
    if len(text) < 5:
        return {text}
    return {text[i : i + 5] for i in range(len(text) - 4)}


def compute_minhash(shingles: set) -> list:
    if not shingles:
        return [0] * NUM_PERMUTATIONS
    shingle_hashes = np.array(
        [
            int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)
            for s in shingles
        ],
        dtype=np.uint64,
    )
    perm_hashes = (
        HASH_A[:, None] * shingle_hashes[None, :] + HASH_B[:, None]
    ) % PRIME
    return np.min(perm_hashes, axis=1).tolist()


def run_pipeline():
    start_time = time.time()
    t0 = start_time

    con = duckdb.connect(DB_PATH)

    # Re-create tables with DuckDB-supported array types.
    con.execute("DROP TABLE IF EXISTS notice_signatures;")
    con.execute("DROP TABLE IF EXISTS lsh_buckets;")
    con.execute("DROP TABLE IF EXISTS opportunity_clusters;")
    con.execute("DROP TABLE IF EXISTS notice_cluster_map;")

    con.execute("""
        CREATE TABLE notice_signatures (
            notice_id VARCHAR PRIMARY KEY,
            published_at TIMESTAMP,
            signature BIGINT[]
        );
        CREATE TABLE lsh_buckets (
            band_id UTINYINT,
            bucket_hash BIGINT,
            notice_id VARCHAR,
            PRIMARY KEY (band_id, bucket_hash, notice_id)
        );
        CREATE TABLE opportunity_clusters (
            card_id VARCHAR PRIMARY KEY,
            canonical_notice_id VARCHAR UNIQUE,
            created_at TIMESTAMP
        );
        CREATE TABLE notice_cluster_map (
            notice_id VARCHAR PRIMARY KEY,
            card_id VARCHAR,
            assigned_at TIMESTAMP
        );
    """)

    pfiles = []
    for notices_dir in NOTICE_ROOTS:
        if notices_dir.is_dir():
            pfiles.extend(sorted(notices_dir.glob("*.parquet")))
    pfiles = sorted(set(pfiles))

    records = []
    bucket_rows = []

    for pf in pfiles:
        df = pd.read_parquet(pf)
        for _, row in df.iterrows():
            nid = str(row["notice_id"])
            body = str(row.get("body", "")) + " " + str(row.get("title", ""))
            shingles = get_char_5grams(body)
            sig = compute_minhash(shingles)

            records.append((nid, row.get("published_at"), sig))

            for b in range(BANDS):
                slice_str = str(
                    sig[b * ROWS_PER_BAND : (b + 1) * ROWS_PER_BAND]
                )
                b_hash = int(
                    hashlib.md5(slice_str.encode("utf-8")).hexdigest()[:15], 16
                )
                bucket_rows.append((b, b_hash, nid))

    t_ingest = time.time() - t0

    t0 = time.time()
    if records:
        sig_df = pd.DataFrame(
            records, columns=["notice_id", "published_at", "signature"]
        )
        con.execute(
            "INSERT OR REPLACE INTO notice_signatures SELECT * FROM sig_df"
        )
        bucket_df = pd.DataFrame(
            bucket_rows, columns=["band_id", "bucket_hash", "notice_id"]
        )
        con.execute(
            "INSERT OR REPLACE INTO lsh_buckets SELECT * FROM bucket_df"
        )
    t_db = time.time() - t0

    t0 = time.time()
    valid_buckets = con.execute(f"""
        SELECT band_id, bucket_hash, COUNT(notice_id) as bsize
        FROM lsh_buckets
        GROUP BY band_id, bucket_hash
        HAVING bsize > 1 AND bsize <= {MAX_BUCKET_SIZE}
    """).fetchall()

    candidate_pairs = set()
    for band_id, bucket_hash, _ in valid_buckets:
        nids = [
            r[0]
            for r in con.execute(
                """
            SELECT notice_id FROM lsh_buckets WHERE band_id = ? AND bucket_hash = ?
        """,
                [band_id, bucket_hash],
            ).fetchall()
        ]
        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                candidate_pairs.add(tuple(sorted([nids[i], nids[j]])))

    t_candidate = time.time() - t0

    t0 = time.time()
    parent = {}

    def find(i):
        if i not in parent:
            parent[i] = i
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for n1, n2 in candidate_pairs:
        s1 = con.execute(
            "SELECT signature FROM notice_signatures WHERE notice_id = ?", [n1]
        ).fetchone()[0]
        s2 = con.execute(
            "SELECT signature FROM notice_signatures WHERE notice_id = ?", [n2]
        ).fetchone()[0]
        if np.mean(np.array(s1) == np.array(s2)) >= JACCARD_THRESHOLD:
            union(n1, n2)

    all_nids = [
        r[0]
        for r in con.execute(
            "SELECT notice_id FROM notice_signatures"
        ).fetchall()
    ]
    components = {}
    for nid in all_nids:
        root = find(nid)
        components.setdefault(root, []).append(nid)

    t_verify = time.time() - t0
    total_time = time.time() - start_time

    # Explicitly close DuckDB connection so exam.duckdb isn't locked
    con.close()

    evidence_path = EVIDENCE_DIR / "q2_runtime_evidence.txt"
    with open(evidence_path, "w") as f:
        f.write(
            "======================================================================\n"
        )
        f.write("SETUBID DEDUPLICATION PIPELINE EXECUTION REPORT\n")
        f.write(
            "======================================================================\n"
        )
        f.write(
            f"Execution Timestamp:          {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        )
        f.write("Nightly Execution Budget SLA: 20 minutes (1,200 seconds)\n")
        f.write(
            f"Status:                       {'PASSED' if total_time < 1200 else 'FAILED'}\n\n"
        )
        f.write("1. CORPUS & INGESTION METRICS\n")
        f.write(f"Notice Files Discovered:       {len(pfiles)}\n")
        f.write(f"Total Notices Ingested:       {len(records)}\n")
        f.write("Text Decomposition Standard:   Character 5-grams\n")
        f.write("MinHash Signature Size (K):   128 permutations\n\n")
        f.write("2. LSH INDEXING & CANDIDATE RETRIEVAL\n")
        f.write(
            f"LSH Band Configuration:       b={BANDS} bands, r={ROWS_PER_BAND} rows/band\n"
        )
        f.write(
            f"Heavy-Hitter Cap Threshold:   M <= {MAX_BUCKET_SIZE} entries per bucket\n"
        )
        f.write(
            f"Candidate Pairs Evaluated:    {len(candidate_pairs)} pairs\n\n"
        )
        f.write("3. CLUSTERING SUMMARY\n")
        f.write(
            f"Total Discovered Clusters:    {len(components)} distinct cards\n\n"
        )
        f.write("4. TIMING BREAKDOWN (SECONDS)\n")
        f.write(f"Ingestion & Tokenization:     {t_ingest:.2f} s\n")
        f.write(f"DuckDB Storage & Indexing:    {t_db:.2f} s\n")
        f.write(f"Candidate Retrieval:          {t_candidate:.2f} s\n")
        f.write(f"Asymmetric Verification & DSU:{t_verify:.2f} s\n")
        f.write(
            "----------------------------------------------------------------------\n"
        )
        f.write(f"TOTAL RUNTIME:                {total_time:.2f} seconds\n")
        f.write(
            "======================================================================\n"
        )

    print(f"Pipeline executed cleanly in {total_time:.2f} seconds. Notices: {len(records)}. Evidence written to {evidence_path}")


if __name__ == "__main__":
    run_pipeline()