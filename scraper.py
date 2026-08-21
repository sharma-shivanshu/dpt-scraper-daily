# scraper.py
import os
import json
import time
import re
import traceback
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from newspaper import Article
from playwright.async_api import async_playwright
import asyncio

IST = pytz.timezone("Asia/Kolkata")


def open_sheet_and_worksheet(credentials_json_path: str, sheet_key: str):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_json_path, scope)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_key)
    today_tab = datetime.now().strftime("%Y-%m-%d")
    try:
        worksheet = sh.worksheet(today_tab)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=today_tab, rows="1000", cols="10")
    return sh, worksheet


def ensure_headers_for_ws(worksheet):
    required_headers = ["Link", "Raw", "Relevance", "Cleaned", "Refined", "RunTime"]
    current_headers = worksheet.row_values(1)
    if current_headers != required_headers:
        if current_headers:
            try:
                worksheet.delete_rows(1)
            except Exception:
                pass
        worksheet.insert_row(required_headers, 1)
        return False
    return True


def scroll_and_collect_links(driver, max_scrolls=80, pause_time=2):
    seen_links = set()
    last_height = driver.execute_script("return document.body.scrollHeight")
    retries = 0

    for _ in range(max_scrolls):
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        time.sleep(pause_time)

        tweets = driver.find_elements(By.CSS_SELECTOR, "a[href*='/status/']")
        for t in tweets:
            try:
                link = t.get_attribute("href")
                if link and "/status/" in link:
                    parts = link.split("/status/")
                    main_part = parts[0] + "/status/" + parts[1].split("/")[0]
                    seen_links.add(main_part)
            except Exception:
                continue

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            retries += 1
            if retries >= 3:
                break
        else:
            retries = 0
            last_height = new_height

    return list(seen_links)


def scrape_and_save_links(
    worksheet,
    cookies_file="cookies.json",
    account="bstvlive",
    date=None,
    dates=None,
    max_scrolls=80,
    pause_time=2,
    batch_size=50
):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    if dates is None:
        dates = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    headers_ok = ensure_headers_for_ws(worksheet)

    # Configure Chrome options to avoid headless bot detection
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # Open base domain
    driver.get("https://x.com")
    time.sleep(3)

    # Load and inject cookies
    with open(cookies_file, "r") as f:
        cookies_data = json.load(f)
    if isinstance(cookies_data, list):
        cookies = cookies_data
    elif "cookies" in cookies_data:
        cookies = cookies_data["cookies"]
    else:
        raise ValueError("cookies.json format not recognized")

    for c in cookies:
        cookie_dict = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain", ".x.com"),
            "path": c.get("path", "/"),
        }
        try:
            driver.add_cookie(cookie_dict)
        except Exception:
            pass

    search_url = f"https://x.com/search?q=(from:{account})%20until:{dates}%20since:{date}&f=live"
    print(f"Navigating to: {search_url}")
    driver.get(search_url)

    # Wait for tweets with detailed diagnostics if it fails
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/status/']"))
        )
    except Exception:
        print(f"⚠️ Page landing URL: {driver.current_url}")
        print(f"⚠️ Page title: {driver.title}")
        driver.save_screenshot("debug_screenshot.png")
        print("⚠️ Screenshot saved as 'debug_screenshot.png'. Check GitHub Action artifacts if needed.")

    all_links = scroll_and_collect_links(driver, max_scrolls=max_scrolls, pause_time=pause_time)
    driver.quit()

    existing_links = set(worksheet.col_values(1)[1:]) if worksheet.row_values(1) else set()
    new_links = [link for link in all_links if link not in existing_links]

    ist_now = datetime.now(tz=IST)
    ist_time_str = ist_now.strftime("%I:%M %p · %d %b, %Y")
    rows_to_add = [[link, "", "", "", "", ist_time_str] for link in new_links]

    if rows_to_add:
        start_row = len(worksheet.get_all_values()) + 1
        required_rows = start_row + len(rows_to_add) - 1
        if worksheet.row_count < required_rows:
            worksheet.add_rows(required_rows - worksheet.row_count)

        for i in range(0, len(rows_to_add), batch_size):
            batch = rows_to_add[i:i+batch_size]
            batch_start = start_row + i
            batch_end = batch_start + len(batch) - 1
            cell_range = f"A{batch_start}:F{batch_end}"
            worksheet.update(cell_range, batch)

    return {
        "headers_ok": headers_ok,
        "unique_found": len(all_links),
        "new_added": len(new_links),
        "today_tab": worksheet.title
    }


# ---------------------------
# Playwright-based raw fetch
# ---------------------------
async def scrape_tweet_with_page(page, url):
    try:
        await page.goto(url, timeout=15000)
        try:
            # 5-second strict timeout for text
            await page.wait_for_selector('[data-testid="tweetText"]', timeout=5000)
            content = await page.locator('[data-testid="tweetText"]').first.inner_text()
            return content.strip() if content else None
        except Exception:
            # 5-second fallback for media-only tweets
            await page.wait_for_selector("article", timeout=5000)
            content = await page.locator("article").first.inner_text()
            return content.strip() if content else None
    except Exception as e:
        print(f"⚠️ Scrape failed for {url}: {e}")
        return None


def fetch_from_web_newspaper(url):
    try:
        art = Article(url, keep_article_html=False, fetch_images=False)
        art.download()
        art.parse()
        return ((art.title or "") + "। " + (art.text or "")).strip()
    except Exception as e:
        print(f"⚠️ Newspaper fetch failed for {url}: {e}")
        return None


def is_x_link(url):
    return "x.com/" in url or "twitter.com/" in url


async def fetch_raw_rows_async(worksheet, max_raw_len=4500):
    headers = worksheet.row_values(1)
    if not headers:
        worksheet.insert_row(["Link","Raw","Relevance","Cleaned","Refined","RunTime"], 1)
        headers = worksheet.row_values(1)
    idx_map = {name: headers.index(name) + 1 for name in headers}

    links = worksheet.col_values(idx_map["Link"])[1:]
    raw_col = worksheet.col_values(idx_map["Raw"])[1:] if idx_map.get("Raw") else []

    updates = []
    
    # Initialize Playwright ONCE for the entire batch
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        
        # Inject cookies once
        try:
            with open("cookies.json", "r") as f:
                cookies_data = json.load(f)
                cookies_list = cookies_data.get("cookies", cookies_data) if isinstance(cookies_data, dict) else cookies_data
                pw_cookies = [{"name": c.get("name"), "value": c.get("value"), "domain": c.get("domain", ".x.com"), "path": c.get("path", "/")} for c in cookies_list]
                await context.add_cookies(pw_cookies)
        except Exception as e:
            print(f"⚠️ Could not load cookies for Playwright: {e}")

        page = await context.new_page()

        for i, link in enumerate(links, start=2):
            link = link.strip()
            if not link:
                continue

            existing_raw = raw_col[i-2] if i-2 < len(raw_col) else ""
            if existing_raw and existing_raw not in ["⚠️ fetch failed", ""]:
                continue

            try:
                if is_x_link(link):
                    raw_text = await scrape_tweet_with_page(page, link)
                else:
                    raw_text = fetch_from_web_newspaper(link)

                if not raw_text:
                    updates.append({"range": f"{gspread.utils.rowcol_to_a1(i, idx_map['Raw'])}", "values": [["⚠️ fetch failed"]]})
                    continue

                raw_snip = raw_text[:max_raw_len]
                updates.append({"range": f"{gspread.utils.rowcol_to_a1(i, idx_map['Raw'])}", "values": [[raw_snip]]})
                updates.append({"range": f"{gspread.utils.rowcol_to_a1(i, idx_map['RunTime'])}", "values": [[datetime.now().astimezone(IST).strftime("%I:%M %p · %d %b, %Y")]]})

            except Exception:
                traceback.print_exc()
            
            # Brief pause to avoid hammering the server
            await asyncio.sleep(1)

        await browser.close()

    if updates:
        try:
            worksheet.batch_update([{"range": u["range"], "values": u["values"]} for u in updates])
            return len(updates)
        except Exception as e:
            print("Batch update failed:", e)
            return 0
    return 0
