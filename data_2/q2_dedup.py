import hashlib
import os
import re
import time
import uuid
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

# --- CONFIGURATION ---
DB_PATH = "exam.duckdb"
NOTICES_DIR = Path("exam/data/notices")
EVIDENCE_DIR = Path("evidence")
EVIDENCE_DIR.mkdir(exist_ok=True)

NUM_PERMUTATIONS = 128
BANDS = 16
ROWS_PER_BAND = 8
JACCARD_THRESHOLD = 0.78
MAX_BUCKET_SIZE = 100  # Capping heavy-hitter buckets

# Hash parameters for MinHash permutations
PRIME = 4294967311
np.random.seed(42)
HASH_A = np.random.randint(1, PRIME - 1, size=NUM_PERMUTATIONS, dtype=np.uint64)
HASH_B = np.random.randint(0, PRIME - 1, size=NUM_PERMUTATIONS, dtype=np.uint64)


def normalize_text(text: str) -> str:
    """Normalize text and replace volatile patterns with unified tokens."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(
        r"\b(rs\.?|inr|\$)\s*\d+([.,]\d+)*\b", " _AMOUNT_ ", text
    )  # Amounts
    text = re.sub(
        r"\b\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}\b", " _DATE_ ", text
    )  # Dates
    text = re.sub(
        r"\b[a-z0-9\-_]{6,20}\b", " _REF_ ", text
    )  # Reference codes
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_char_5grams(text: str) -> set:
    """Decompose text into character 5-grams."""
    norm = normalize_text(text)
    if len(norm) < 5:
        return {norm}
    return {norm[i : i + 5] for i in range(len(norm) - 4)}


def compute_minhash(shingles: set) -> list:
    """Compute 128 MinHash signature array for a set of shingles."""
    if not shingles:
        return [0] * NUM_PERMUTATIONS

    # Hash shingles to 32-bit integers
    shingle_hashes = np.array(
        [
            int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)
            for s in shingles
        ],
        dtype=np.uint64,
    )

    # Vectorized hash permutations
    # h(x) = (a * x + b) % PRIME
    perm_hashes = (
        HASH_A[:, None] * shingle_hashes[None, :] + HASH_B[:, None]
    ) % PRIME
    min_hashes = np.min(perm_hashes, axis=1)
    return min_hashes.tolist()


def init_db(con):
    """Initialize persistent relational schema in DuckDB."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS notice_signatures (
            notice_id VARCHAR PRIMARY KEY,
            published_at TIMESTAMP,
            title VARCHAR,
            estimated_value DOUBLE,
            closing_date VARCHAR,
            signature HUGETINT[]
        );
        
        CREATE TABLE IF NOT EXISTS lsh_buckets (
            band_id UTINYINT,
            bucket_hash BIGINT,
            notice_id VARCHAR,
            PRIMARY KEY (band_id, bucket_hash, notice_id)
        );
        
        CREATE TABLE IF NOT EXISTS opportunity_clusters (
            card_id VARCHAR PRIMARY KEY,
            canonical_notice_id VARCHAR UNIQUE,
            created_at TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS notice_cluster_map (
            notice_id VARCHAR PRIMARY KEY,
            card_id VARCHAR,
            assigned_at TIMESTAMP
        );
    """)


class UnionFind:

    def __init__(self):
        self.parent = {}

    def find(self, i):
        if i not in self.parent:
            self.parent[i] = i
            return i
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i, j):
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j


def run_pipeline():
    start_time = time.time()
    con = duckdb.connect(DB_PATH)
    init_db(con)

    print("--- 1. Loading Parquet Files & Extracting MinHash ---")
    parquet_files = list(NOTICES_DIR.glob("*.parquet"))
    if not parquet_files:
        print(
            f"No parquet files found under {NOTICES_DIR}. Checking data_23/ fallback..."
        )
        parquet_files = list(Path("data_23").rglob("*.parquet"))

    records = []
    bucket_rows = []

    for pfile in parquet_files:
        df = pd.read_parquet(pfile)
        for _, row in df.iterrows():
            nid = str(row["notice_id"])
            body = str(row.get("body", "")) + " " + str(row.get("title", ""))
            shingles = get_char_5grams(body)
            sig = compute_minhash(shingles)

            records.append((
                nid,
                row.get("published_at"),
                row.get("title"),
                row.get("estimated_value"),
                str(row.get("closing_date")),
                sig,
            ))

            # Generate LSH band bucket hashes
            for band_id in range(BANDS):
                band_slice = sig[
                    band_id * ROWS_PER_BAND : (band_id + 1) * ROWS_PER_BAND
                ]
                b_hash = int(
                    hashlib.md5(
                        str(band_slice).encode("utf-8")
                    ).hexdigest()[:15],
                    16,
                )
                bucket_rows.append((band_id, b_hash, nid))

    print(
        f"Processed {len(records)} notices in {time.time() - start_time:.2f}s"
    )

    # --- 2. Bulk Insert to Relational Database ---
    sig_df = pd.DataFrame(
        records,
        columns=[
            "notice_id",
            "published_at",
            "title",
            "estimated_value",
            "closing_date",
            "signature",
        ],
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

    # --- 3. Capped Candidate Pair Matching ---
    print("--- 3. Running LSH Candidate Retrieval with Bucket Capping ---")
    candidate_pairs = set()

    # Query buckets while filtering out boilerplate heavy-hitters (> MAX_BUCKET_SIZE)
    valid_buckets = con.execute(f"""
        SELECT band_id, bucket_hash, COUNT(notice_id) as bucket_size
        FROM lsh_buckets
        GROUP BY band_id, bucket_hash
        HAVING bucket_size > 1 AND bucket_size <= {MAX_BUCKET_SIZE}
    """).fetchall()

    for band_id, bucket_hash, b_size in valid_buckets:
        nids = [
            r[0]
            for r in con.execute(
                """
            SELECT notice_id FROM lsh_buckets 
            WHERE band_id = ? AND bucket_hash = ?
        """,
                [band_id, bucket_hash],
            ).fetchall()
        ]

        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                pair = tuple(sorted([nids[i], nids[j]]))
                candidate_pairs.add(pair)

    print(
        f"Generated {len(candidate_pairs)} candidate pairs after bucket capping."
    )

    # --- 4. Asymmetric Jaccard Verification & Cluster Assembly ---
    print("--- 4. Asymmetric Verification & Stable Card ID Clustering ---")
    dsu = UnionFind()

    for n1, n2 in candidate_pairs:
        # Retrieve signatures
        s1 = con.execute(
            "SELECT signature FROM notice_signatures WHERE notice_id = ?",
            [n1],
        ).fetchone()[0]
        s2 = con.execute(
            "SELECT signature FROM notice_signatures WHERE notice_id = ?",
            [n2],
        ).fetchone()[0]

        # Estimated Jaccard from MinHash signatures
        jaccard_est = np.mean(np.array(s1) == np.array(s2))

        if jaccard_est >= JACCARD_THRESHOLD:
            dsu.union(n1, n2)

    # Map connected components to stable canonical card IDs
    components = {}
    all_notices = [r[0] for r in con.execute("SELECT notice_id FROM notice_signatures").fetchall()]

    for nid in all_notices:
        root = dsu.find(nid)
        components.setdefault(root, []).append(nid)

    cluster_map_rows = []
    cluster_rows = []

    for root, members in components.items():
        # Canonical notice ID is the earliest published
        canon_nid = con.execute(
            f"""
            SELECT notice_id FROM notice_signatures 
            WHERE notice_id IN ({','.join(['?']*len(members))})
            ORDER BY published_at ASC NULLS LAST LIMIT 1
        """,
            members,
        ).fetchone()[0]

        # Check if canonical notice already has a card_id in DB
        existing_card = con.execute(
            "SELECT card_id FROM opportunity_clusters WHERE canonical_notice_id = ?",
            [canon_nid],
        ).fetchone()

        card_id = (
            existing_card[0] if existing_card else str(uuid.uuid4())
        )

        if not existing_card:
            cluster_rows.append((card_id, canon_nid, pd.Timestamp.now()))

        for m_id in members:
            cluster_map_rows.append((m_id, card_id, pd.Timestamp.now()))

    if cluster_rows:
        con.executemany(
            "INSERT OR REPLACE INTO opportunity_clusters VALUES (?, ?, ?)",
            cluster_rows,
        )
    con.executemany(
        "INSERT OR REPLACE INTO notice_cluster_map VALUES (?, ?, ?)",
        cluster_map_rows,
    )

    elapsed = time.time() - start_time
    print(f"--- Pipeline completed successfully in {elapsed:.2f} seconds ---")

    # Save evidence metrics
    with open(EVIDENCE_DIR / "q2_runtime_evidence.txt", "w") as f:
        f.write(f"Total Runtime: {elapsed:.2f} seconds\n")
        f.write(f"Notices Processed: {len(records)}\n")
        f.write(f"Clusters Formed: {len(components)}\n")
        f.write(f"Candidate Pairs Evaluated: {len(candidate_pairs)}\n")


if __name__ == "__main__":
    run_pipeline()