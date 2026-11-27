"""Build a read-only SQLite snapshot from an explicitly named local YML feed."""
import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from smart_site_search.catalog.build import build_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, required=True, help="Path to the source YML (XML or gzip)")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "data" / "catalog" / "catalog.sqlite",
        help="Path for the SQLite snapshot",
    )
    args = parser.parse_args()
    manifest = build_catalog(args.feed, args.output)
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
