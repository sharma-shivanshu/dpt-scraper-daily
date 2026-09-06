---
trigger: always_on
---

# DPT Scraper Daily - Troubleshooting SOP

**Repository Info:**
- **URL:** `https://github.com/sharma-shivanshu/dpt-scraper-daily`
- **Purpose:** Scrapes Twitter (X.com) links and content via Selenium and Playwright on a scheduled GitHub Actions cron job.

**Historical Context & Known Issues:**
- **Issue (Sept 2026):** The scraper was returning 0 links because Twitter/Cloudflare aggressively blocked GitHub Actions data center IPs.
- **Current Fix:** We added Cloudflare WARP (`fscarmen/warp-on-actions`) to the GitHub workflow (`scrape-schedule.yml`), set the runner to `ubuntu-latest`, and configured the workflow to upload `debug_screenshot.png` as an artifact on failure to help diagnose future blocks.

**Standard Operating Procedure (Troubleshooting):**
If the scraper stops working or pulls 0 links again:
1. Check the latest GitHub Actions run and download `run-artifacts.zip`.
2. Inspect `debug_screenshot.png` to see if Twitter is showing a Cloudflare block page or Captcha, which indicates the WARP IP has also been flagged.
3. **The Fallback Plan:** If WARP is no longer bypassing the block, transition to a **Residential Proxy**.
4. Obtain a residential proxy (e.g., from Webshare or IPRoyal) and inject those proxy settings into both the Selenium and Playwright configurations within `scraper.py`.
