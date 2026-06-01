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
import concurrent.futures
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

# ---------------------------------------------------------------------------
# Enrichment constants
# ---------------------------------------------------------------------------

NHTSA_VPIC_URL     = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
HISTORY_RESOURCE   = "56063a99-8a3e-4ff4-912e-5966c0279bad"
RECALLS_RESOURCE   = "2c33523f-87aa-44ec-a736-edbb0a82975e"
OPEN_RECALL_RESOURCE = "36bf1404-0be4-49d2-82dc-2f1ead4a8b93"

# NHTSA vPIC field names → human-readable labels (subset of the 140 returned)
NHTSA_FIELDS: Dict[str, str] = {
    "Make":                               "Make",
    "Model":                              "Model",
    "Model Year":                         "Model Year",
    "Body Class":                         "Body Class",
    "Doors":                              "Doors",
    "Drive Type":                         "Drive Type",
    "Fuel Type - Primary":                "Fuel Type",
    "Electrification Level":              "Electrification Level",
    "Engine Number of Cylinders":         "Cylinders",
    "Displacement (CC)":                  "Displacement (CC)",
    "Engine Power (kW)":                  "Power (kW)",
    "Transmission Style":                 "Transmission",
    "ABS":                                "ABS",
    "Electronic Stability Control (ESC)": "ESC",
    "Backup Camera":                      "Backup Camera",
    "Front Air Bag Locations":            "Front Airbags",
    "Side Air Bag Locations":             "Side Airbags",
    "Battery Energy (kWh) From":          "Battery (kWh)",
    "Trim":                               "Trim",
    "Series":                             "Series",
    "Plant Country":                      "Plant Country",
    "Plant City":                         "Plant City",
}

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


def build_resource_url(
    resource_id: str,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Generic URL builder for any data.gov.il resource (used by enrichment)."""
    params: dict[str, Any] = {
        "resource_id": resource_id,
        "limit": limit,
        "offset": offset,
    }
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


def fetch_safe(url: str) -> Optional[Dict]:
    """Like fetch() but returns None on error — used for optional enrichment calls."""
    try:
        return fetch(url)
    except SystemExit:
        return None


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
# RTL / BiDi helpers
# ---------------------------------------------------------------------------

def _has_hebrew(text: str) -> bool:
    """Return True if *text* contains any Hebrew Unicode character."""
    return any("\u0590" <= c <= "\u05FF" for c in text)


def rtl_display(text: str) -> str:
    """
    Prepare *text* for correct visual rendering in a left-to-right terminal.

    Strategy (in order):
      1. Use python-bidi (``pip install python-bidi``) for full Unicode BiDi
         algorithm support — handles mixed LTR/RTL content correctly.
      2. Fall back to reversing the string, which is accurate for strings
         that are purely Hebrew (no embedded Latin runs).  This covers all
         common values in the Israeli vehicle registry (colors, ownership
         types, fuel types, etc.).
    """
    if not _has_hebrew(text):
        return text
    try:
        from bidi.algorithm import get_display  # type: ignore
        return get_display(text)
    except ImportError:
        pass
    # Fallback: reverse the whole string.  Correct for pure-Hebrew values;
    # mixed content (e.g. "BMW פרטי") will look odd without python-bidi.
    return text[::-1]


def apply_rtl(obj: Any) -> Any:
    """Recursively apply :func:`rtl_display` to every string in *obj*."""
    if isinstance(obj, str):
        return rtl_display(obj)
    if isinstance(obj, dict):
        return {k: apply_rtl(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply_rtl(item) for item in obj]
    return obj


def format_cell(value: Any, width: int) -> str:
    """
    Render a table cell of exactly *width* characters.

    Hebrew values are passed through :func:`rtl_display` and then
    **right-aligned** so the text starts at the natural right edge of the
    cell.  Latin / numeric values are left-aligned as usual.
    """
    val = str(value or "")
    if len(val) > width:
        val = val[:width]
    if _has_hebrew(val):
        return rtl_display(val).rjust(width)
    return val.ljust(width)


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
    # Headers are always Latin field names — left-aligned
    header_fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"

    print(sep)
    print(header_fmt.format(*[h[:w] for h, w in zip(headers, widths)]))
    print(sep)
    for rec in records:
        cells = [format_cell(rec.get(f, ""), w) for f, w in zip(display_fields, widths)]
        print("| " + " | ".join(cells) + " |")
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
# Enrichment functions
# ---------------------------------------------------------------------------

def enrich_nhtsa(vin: str) -> Dict[str, Any]:
    """Decode VIN via the free NHTSA vPIC API (~140 fields, no key required)."""
    vin = (vin or "").strip()
    if not vin:
        return {"_error": "No VIN available"}
    url = NHTSA_VPIC_URL.format(vin=urllib.parse.quote(vin))
    data = fetch_safe(url)
    if data is None:
        return {"_error": "NHTSA API unreachable"}

    result: Dict[str, Any] = {}
    warning_parts: List[str] = []
    for item in data.get("Results", []):
        var = item.get("Variable", "")
        val = (item.get("Value") or "").strip()
        if not val or val in ("Not Applicable", "null", "None"):
            continue
        if var in NHTSA_FIELDS:
            result[NHTSA_FIELDS[var]] = val
        elif var == "Error Code" and val != "0":
            err_text = next(
                ((i.get("Value") or "") for i in data["Results"]
                 if i.get("Variable") == "Additional Error Text"),
                "",
            )
            warning_parts.append(f"Error {val}: {err_text}".strip(": "))
    if warning_parts:
        result["_warning"] = "; ".join(warning_parts)
    return result


def enrich_history(plate: Any) -> Dict[str, Any]:
    """Fetch vehicle history from data.gov.il (odometer, damage/mod flags, etc.)."""
    try:
        plate_val: Any = int(plate)
    except (TypeError, ValueError):
        plate_val = plate
    url = build_resource_url(HISTORY_RESOURCE, filters={"mispar_rechev": plate_val}, limit=1)
    data = fetch_safe(url)
    if data is None:
        return {"_error": "History API unreachable"}
    records = data.get("result", {}).get("records", [])
    if not records:
        return {"_error": "No history record found"}
    r = records[0]

    def flag(val: Any) -> str:
        return "Yes" if val and str(val).strip() not in ("0", "", "None") else "No"

    return {
        "Engine number":           r.get("mispar_manoa") or "—",
        "First registration":      r.get("rishum_rishon_dt") or "—",
        "Odometer at last test":   r.get("kilometer_test_aharon") or "—",
        "Structural modification": flag(r.get("shinui_mivne_ind")),
        "Body damage":             flag(r.get("gapam_ind")),
        "Color change":            flag(r.get("shnui_zeva_ind")),
        "Tire change":             flag(r.get("shinui_zmig_ind")),
        "Originality":             r.get("mkoriut_nm") or "—",
    }


def enrich_recalls(tozeret_cd: Any) -> List[Dict[str, Any]]:
    """Fetch Israeli-market recalls for a manufacturer code."""
    if not tozeret_cd:
        return []
    try:
        cd_val: Any = int(tozeret_cd)
    except (TypeError, ValueError):
        cd_val = tozeret_cd
    url = build_resource_url(RECALLS_RESOURCE, filters={"TOZAR_CD": cd_val}, limit=100)
    data = fetch_safe(url)
    if data is None:
        return []
    out = []
    for r in data.get("result", {}).get("records", []):
        out.append({
            "Recall ID":   r.get("RECALL_ID") or "—",
            "Model":       r.get("DEGEM") or "—",
            "Year":        r.get("SHNAT_RECALL") or "—",
            "Type":        r.get("SUG_RECALL") or "—",
            "Component":   r.get("SUG_TAKALA") or "—",
            "Description": r.get("TEUR_TAKALA") or "—",
            "Fix":         r.get("OFEN_TIKUN") or "—",
            "Importer":    r.get("YEVUAN_TEUR") or "—",
        })
    return out


def enrich_open_recall(plate: Any) -> List[Dict[str, Any]]:
    """Check if this vehicle has any pending unperformed recalls."""
    try:
        plate_val: Any = int(plate)
    except (TypeError, ValueError):
        plate_val = plate
    url = build_resource_url(OPEN_RECALL_RESOURCE, filters={"mispar_rechev": plate_val}, limit=10)
    data = fetch_safe(url)
    if data is None:
        return []
    return data.get("result", {}).get("records", [])


def run_enrichment(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Run all four enrichment sources concurrently for a single vehicle record."""
    plate      = rec.get("mispar_rechev", "")
    vin        = rec.get("misgeret", "")
    tozeret_cd = rec.get("tozeret_cd")

    tasks = {
        "nhtsa":   (enrich_nhtsa,       vin),
        "history": (enrich_history,     plate),
        "recalls": (enrich_recalls,     tozeret_cd),
        "open":    (enrich_open_recall, plate),
    }
    results: Dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        future_to_key = {ex.submit(fn, arg): key for key, (fn, arg) in tasks.items()}
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                results[key] = {"_error": str(exc)}
    return results


def print_enrichment_card(rec: Dict[str, Any], enrichment: Dict[str, Any]) -> None:
    """Print a human-readable enrichment detail card for one vehicle."""
    plate = rec.get("mispar_rechev", "?")
    model = rec.get("kinuy_mishari", "")
    mfr   = rec.get("tozeret_nm", "")
    year  = rec.get("shnat_yitzur", "")

    bar = "=" * 64
    print(f"\n{bar}")
    print(f"  {plate}  —  {model}  ({mfr}, {year})")
    print(bar)

    # --- NHTSA specs ---
    print("\n[ NHTSA Specs ]")
    nhtsa = dict(enrichment.get("nhtsa") or {})
    if not nhtsa:
        print("  (no data)")
    elif "_error" in nhtsa:
        print(f"  {nhtsa['_error']}")
    else:
        warning = nhtsa.pop("_warning", None)
        for label, val in nhtsa.items():
            print(f"  {label:<30} {val}")
        if warning:
            print(f"  [!] {warning}")

    # --- Vehicle history ---
    print("\n[ Israeli Vehicle History ]")
    history = enrichment.get("history") or {}
    if not history:
        print("  (no data)")
    elif "_error" in history:
        print(f"  {history['_error']}")
    else:
        for label, val in history.items():
            s = str(val)
            display_val = rtl_display(s) if _has_hebrew(s) else s
            print(f"  {label:<30} {display_val}")

    # --- Israeli recalls ---
    print("\n[ Israeli Recalls ]")
    recalls = enrichment.get("recalls") or []
    if not recalls:
        print("  No recalls on record for this manufacturer.")
    else:
        for r in recalls:
            rid     = r.get("Recall ID", "—")
            mdl     = r.get("Model", "—")
            mdl_d   = rtl_display(mdl) if _has_hebrew(mdl) else mdl
            yr      = r.get("Year", "—")
            comp    = r.get("Component", "—")
            comp_d  = rtl_display(comp) if _has_hebrew(comp) else comp
            desc    = r.get("Description", "—")
            desc_d  = rtl_display(desc) if _has_hebrew(desc) else desc
            fix     = r.get("Fix", "—")
            fix_d   = rtl_display(fix) if _has_hebrew(fix) else fix
            print(f"  [{rid}] {mdl_d} ({yr})")
            print(f"    Component  : {comp_d}")
            print(f"    Description: {desc_d}")
            print(f"    Fix        : {fix_d}")

    # --- Open / unperformed recalls ---
    print("\n[ Open Recall Status ]")
    open_r = enrichment.get("open") or []
    if not open_r:
        print("  No pending unperformed recalls.")
    else:
        print(f"  *** {len(open_r)} UNPERFORMED RECALL(S) for plate {plate} ***")
        for r in open_r:
            print(f"  {r}")

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

  # Show ALL fields for search results
  python vehicle_query.py --search TOYOTA --all-fields

  # Full enrichment for a single vehicle by plate number
  python vehicle_query.py --filter mispar_rechev=12345678 --enrich

  # Same but as JSON
  python vehicle_query.py --filter mispar_rechev=12345678 --enrich --json
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
    p.add_argument("--all-fields", "-A", action="store_true",
                   help="Display all available fields in results (overrides --fields)")
    p.add_argument("--count", "-c", action="store_true",
                   help="Print only the total count, no records")
    p.add_argument("--export", "-e", metavar="FILE.csv",
                   help="Export results to a CSV file")
    p.add_argument("--json", action="store_true",
                   help="Print raw JSON output instead of a table")
    p.add_argument("--enrich", action="store_true",
                   help="Fetch extra data (NHTSA specs, vehicle history, Israeli recalls, "
                        "open recall status). Requires the query to return exactly one "
                        "vehicle — use --filter mispar_rechev=PLATE to target a single plate.")
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
    if args.all_fields:
        display_fields = list(FIELDS.keys())
    elif args.fields and args.fields != "__list__":
        display_fields = [f.strip() for f in args.fields.split(",")]
        unknown = [f for f in display_fields if f not in FIELDS and f != "_id"]
        if unknown:
            print(f"[WARN] Unknown fields: {', '.join(unknown)}", file=sys.stderr)
    else:
        display_fields = DEFAULT_DISPLAY_FIELDS

    # --enrich: validate we have exactly one vehicle
    enrichment: Optional[Dict[str, Any]] = None
    if args.enrich:
        if len(records) != 1:
            print(
                f"\n[ERROR] --enrich requires exactly one vehicle in results "
                f"(got {len(records)}).\n"
                f"       Use --filter mispar_rechev=PLATE to query a specific plate number.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("Fetching enrichment data...", file=sys.stderr)
        enrichment = run_enrichment(records[0])

    # Output
    if args.json:
        out = apply_rtl(records)
        if enrichment is not None:
            out[0]["_enrichment"] = apply_rtl(enrichment)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_table(records, display_fields)
        if enrichment is not None:
            print_enrichment_card(records[0], enrichment)

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
