import shutil
from pathlib import Path

file_path = Path("/content/pipeline_output_q2_dedup.txt")
archive_path = Path("/content/pipeline_output_q2_eval.txt")

shutil.make_archive(
    str(archive_path.with_suffix("")),
    "zip",
    root_dir=file_path.parent,
    base_dir=file_path.name
)

print(f"✅ Archived successfully: {archive_path}")