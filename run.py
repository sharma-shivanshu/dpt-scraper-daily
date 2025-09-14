# run.py
import os
import sys
import base64
import traceback
from datetime import datetime, timezone
from scraper import open_sheet_and_worksheet, scrape_and_save_links, fetch_raw_rows_async

def write_b64_secret_to_file(envname, outpath):
    b64 = os.environ.get(envname)
    if not b64:
        raise RuntimeError(f"Missing required secret env: {envname}")
    data = base64.b64decode(b64)
    with open(outpath, "wb") as f:
        f.write(data)
    print(f"✅ wrote {outpath}")

def main():
    try:
        # 1) Decode secrets to files
        write_b64_secret_to_file("GSPREAD_CREDENTIALS_B64", "credentials.json")
        write_b64_secret_to_file("COOKIES_JSON_B64", "cookies.json")

        # 2) ensure SHEET_KEY exists
        sheet_key = os.environ.get("SHEET_KEY")
        if not sheet_key:
            print("ERROR: SHEET_KEY missing", file=sys.stderr)
            sys.exit(2)

        # 3) open sheet & worksheet
        sh, worksheet = open_sheet_and_worksheet("credentials.json", sheet_key)

        # 4) run link scraping & save
        print("=== Running link scrape & save ===")
        scrape_summary = scrape_and_save_links(
            worksheet,
            cookies_file="cookies.json",
            account=os.environ.get("SCRAPE_ACCOUNT", "bstvlive"),
            date=os.environ.get("SCRAPE_DATE")
        )

        # 5) run raw fetch (async)
        print("=== Fetching raw text for rows (Playwright) ===")
        import asyncio, nest_asyncio
        nest_asyncio.apply()
        updated_cells = asyncio.run(fetch_raw_rows_async(worksheet))

        # 6) write summary.txt
        now = datetime.now(timezone.utc)
        lines = []
        headers_ok = scrape_summary.get("headers_ok", False)
        lines.append(f"✅ Headers already correct" if headers_ok else "✅ Headers reset to required")
        lines.append(f"✅ {scrape_summary.get('unique_found', 0)} unique tweets found for {datetime.now().strftime('%Y-%m-%d')}")
        lines.append(f"✅ {scrape_summary.get('new_added', 0)} new links added to tab '{scrape_summary.get('today_tab')}'")
        lines.append(f"✅ Batch updated {updated_cells} cells")
        lines.append(f"Run start (UTC): {now.isoformat()}")

        txt = "\n".join(lines)
        with open("summary.txt", "w", encoding="utf-8") as f:
            f.write(txt)
        print("\n==== RUN SUMMARY ====\n")
        print(txt)
        print("\n=====================\n")

    except Exception as e:
        traceback.print_exc()
        with open("summary.txt", "w", encoding="utf-8") as f:
            f.write("❌ Scraper failed with exception:\n")
            f.write(str(e))
            f.write("\n\nTraceback:\n")
            import traceback as _tb
            f.write(_tb.format_exc())
        raise

if __name__ == "__main__":
    main()
