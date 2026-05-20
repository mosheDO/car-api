#!/usr/bin/env python3
"""
Israel Vehicle Registry Query Tool
Queries the data.gov.il API (resource: 053cea08-09bc-40ec-8f7a-156f0677aff3)

Usage examples:
  python vehicle_query.py --search HIGHLANDER
  python vehicle_query.py --search HIGHLANDER --filter moed_aliya_lakvish=2026-1
  python vehicle_query.py --filter kinuy_mishari=HIGHLANDER --filter shnat_yitzur=2026
  python vehicle_query.py --search TESLA --fields mispar_rechev,kinuy_mishari,tzeva_rechev,shnat_yitzur
  python vehicle_query.py --search HONDA --count
  python vehicle_query.py --search HIGHLANDER --filter moed_aliya_lakvish=2026-1 --export results.csv
  python vehicle_query.py --fields
  python vehicle_query.py --search TOYOTA --limit 50 --all-pages
"""

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_BASE = "https://data.gov.il/api/action/datastore_search"
RESOURCE_ID = "053cea08-09bc-40ec-8f7a-156f0677aff3"
DEFAULT_LIMIT = 20

FIELDS = {
    "mispar_rechev":       "Vehicle number",
    "tozeret_cd":          "Manufacturer code",
    "tozeret_nm":          "Manufacturer name",
    "sug_degem":           "Vehicle type (P=private)",
    "degem_cd":            "Model code",
    "degem_nm":            "Model name",
    "ramat_gimur":         "Trim level",
    "ramat_eivzur_betihuty": "Safety equipment level",
    "kvutzat_zihum":       "Pollution group",
    "shnat_yitzur":        "Year of manufacture",
    "degem_manoa":         "Engine model",
    "mivchan_acharon_dt":  "Last inspection date",
    "tokef_dt":            "License expiry date",
    "baalut":              "Ownership (פרטי/ליסינג/חברה)",
    "misgeret":            "VIN / Chassis number",
    "tzeva_cd":            "Color code",
    "tzeva_rechev":        "Color name",
    "zmig_kidmi":          "Front tire size",
    "zmig_ahori":          "Rear tire size",
    "sug_delek_nm":        "Fuel type",
    "horaat_rishum":       "Registration order number",
    "moed_aliya_lakvish":  "Date first on road  (YYYY-M)",
    "kinuy_mishari":       "Commercial / trade name",
}

# Columns shown by default when --fields is not specified
DEFAULT_DISPLAY_FIELDS = [
    "mispar_rechev",
    "kinuy_mishari",
    "tozeret_nm",
    "shnat_yitzur",
    "tzeva_rechev",
    "sug_delek_nm",
    "baalut",
    "moed_aliya_lakvish",
    "misgeret",
]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def build_url(
    search: Optional[str],
    filters: Dict[str, Any],
    limit: int,
    offset: int,
) -> str:
    params: dict[str, Any] = {
        "resource_id": RESOURCE_ID,
        "limit": limit,
        "offset": offset,
        "include_total": "true",
    }
    if search:
        params["q"] = search
    if filters:
        params["filters"] = json.dumps(filters)
    return API_BASE + "?" + urllib.parse.urlencode(params)


def fetch(url: str) -> Dict:
    req = urllib.request.Request(url, headers={"User-Agent": "vehicle-query/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[ERROR] HTTP {e.code}: {body[:300]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERROR] Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def query(
    search: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    all_pages: bool = False,
) -> Tuple[List[Dict], int]:
    """
    Returns (records, total_count).
    If all_pages=True, fetches every page and returns all records.
    """
    filters = filters or {}
    records: List[Dict] = []
    total = 0

    url = build_url(search, filters, limit, offset)
    data = fetch(url)

    if not data.get("success"):
        print(f"[ERROR] API returned success=false", file=sys.stderr)
        sys.exit(1)

    result = data["result"]
    total = result.get("total", 0)
    records.extend(result.get("records", []))

    if all_pages:
        fetched = len(records)
        while fetched < total:
            url = build_url(search, filters, limit, fetched)
            data = fetch(url)
            batch = data["result"].get("records", [])
            if not batch:
                break
            records.extend(batch)
            fetched += len(batch)
            print(
                f"  Fetched {fetched}/{total}...",
                end="\r",
                flush=True,
                file=sys.stderr,
            )
        print(file=sys.stderr)  # newline after progress

    return records, total


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def col_width(header: str, rows: List[Dict], key: str) -> int:
    w = len(header)
    for r in rows:
        val = str(r.get(key, "") or "")
        if len(val) > w:
            w = len(val)
    return min(w, 40)  # cap at 40 chars so table doesn't blow up


def print_table(records: List[Dict], display_fields: List[str]) -> None:
    if not records:
        print("No records found.")
        return

    headers = [f[:20] for f in display_fields]
    widths = [col_width(h, records, f) for h, f in zip(headers, display_fields)]

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"

    print(sep)
    print(fmt.format(*[h[:w] for h, w in zip(headers, widths)]))
    print(sep)
    for rec in records:
        row = [str(rec.get(f, "") or "")[:w] for f, w in zip(display_fields, widths)]
        print(fmt.format(*row))
    print(sep)


def export_csv(records: List[Dict], path: str, display_fields: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=display_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(f"Exported {len(records)} records to {path}")


def print_fields() -> None:
    print(f"\n{'Field name':<30} {'Description'}")
    print("-" * 65)
    for name, desc in FIELDS.items():
        print(f"  {name:<28} {desc}")
    print()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_filters(raw: List[str]) -> Dict[str, Any]:
    """
    Parse 'key=value' strings into a dict.
    Numeric values are automatically cast to int/float.
    """
    result: Dict[str, Any] = {}
    for item in raw:
        if "=" not in item:
            print(f"[ERROR] Filter must be key=value, got: {item!r}", file=sys.stderr)
            sys.exit(1)
        key, _, val = item.partition("=")
        key = key.strip()
        val = val.strip()
        # Try numeric cast
        try:
            val = int(val)  # type: ignore[assignment]
        except ValueError:
            try:
                val = float(val)  # type: ignore[assignment]
            except ValueError:
                pass
        result[key] = val
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Query the Israeli vehicle registry (data.gov.il)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # All Highlanders
  python vehicle_query.py --search HIGHLANDER

  # Highlanders registered in January 2026
  python vehicle_query.py --search HIGHLANDER --filter moed_aliya_lakvish=2026-1

  # Exact model + year (multiple filters)
  python vehicle_query.py --filter kinuy_mishari=HIGHLANDER --filter shnat_yitzur=2026

  # Show only specific columns
  python vehicle_query.py --search TESLA --fields mispar_rechev,kinuy_mishari,shnat_yitzur,tzeva_rechev

  # Just get the count
  python vehicle_query.py --search MAZDA --count

  # Fetch ALL pages and export to CSV
  python vehicle_query.py --search HIGHLANDER --filter moed_aliya_lakvish=2026-1 --all-pages --export hl_jan26.csv

  # List available field names
  python vehicle_query.py --fields
        """,
    )
    p.add_argument("--search", "-s", metavar="TEXT",
                   help="Full-text search across all fields (e.g. HIGHLANDER, TESLA)")
    p.add_argument("--filter", "-f", metavar="KEY=VALUE", action="append", default=[],
                   dest="filters",
                   help="Exact field filter. Repeat for multiple filters. "
                        "E.g. --filter shnat_yitzur=2026 --filter baalut=פרטי")
    p.add_argument("--fields", metavar="COL1,COL2,...", nargs="?", const="__list__",
                   help="Comma-separated columns to display. "
                        "Use --fields alone to list all available field names.")
    p.add_argument("--limit", "-l", type=int, default=DEFAULT_LIMIT,
                   help=f"Max records per page (default: {DEFAULT_LIMIT})")
    p.add_argument("--offset", "-o", type=int, default=0,
                   help="Start offset for pagination (default: 0)")
    p.add_argument("--all-pages", action="store_true",
                   help="Fetch ALL pages (overrides --limit for total fetch)")
    p.add_argument("--count", "-c", action="store_true",
                   help="Print only the total count, no records")
    p.add_argument("--export", "-e", metavar="FILE.csv",
                   help="Export results to a CSV file")
    p.add_argument("--json", action="store_true",
                   help="Print raw JSON output instead of a table")
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # --fields with no value → list fields and exit
    if args.fields == "__list__":
        print_fields()
        sys.exit(0)

    # Must have at least one search/filter
    if not args.search and not args.filters:
        parser.print_help()
        print("\n[ERROR] Provide at least --search TEXT or --filter KEY=VALUE", file=sys.stderr)
        sys.exit(1)

    filters = parse_filters(args.filters)

    # --count: just fetch 1 record to get the total
    if args.count:
        _, total = query(search=args.search, filters=filters, limit=1)
        label = f"search={args.search!r}" if args.search else ""
        if filters:
            label += (" " if label else "") + " ".join(f"{k}={v}" for k, v in filters.items())
        print(f"Total records matching [{label}]: {total:,}")
        return

    # Fetch records
    page_limit = args.limit if not args.all_pages else 100
    records, total = query(
        search=args.search,
        filters=filters,
        limit=page_limit,
        offset=args.offset,
        all_pages=args.all_pages,
    )

    shown = len(records)
    print(f"\nTotal matching: {total:,}  |  Showing: {shown}\n")

    # Determine display fields
    if args.fields and args.fields != "__list__":
        display_fields = [f.strip() for f in args.fields.split(",")]
        unknown = [f for f in display_fields if f not in FIELDS and f != "_id"]
        if unknown:
            print(f"[WARN] Unknown fields: {', '.join(unknown)}", file=sys.stderr)
    else:
        display_fields = DEFAULT_DISPLAY_FIELDS

    # Output
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        print_table(records, display_fields)

    if not args.all_pages and shown < total:
        next_offset = args.offset + shown
        print(
            f"\nMore results available. Use --offset {next_offset} for next page, "
            f"or --all-pages to fetch everything."
        )

    # Export
    if args.export:
        export_csv(records, args.export, list(FIELDS.keys()))


if __name__ == "__main__":
    main()
