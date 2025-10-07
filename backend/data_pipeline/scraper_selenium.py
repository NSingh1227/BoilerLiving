# scraper_selenium.py
"""
Selenium-based scraper for JavaScript-rendered sites.
Uses Chrome in headless mode with existing parsers for data extraction.
"""
import os
import time
import sqlite3
from collections import defaultdict
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from parsers import parse_html_many
from link_discovery import discover_links

DB_PATH = os.path.join(os.path.dirname(__file__), "listings.sqlite")
REQUEST_TIMEOUT = 20
MAX_LINKS_PER_SITE = 120


def safe_sleep(seconds):
    """Sleep function that handles interrupts gracefully by sleeping in small chunks."""
    # Sleep in 0.5 second intervals to make interrupts less likely to crash driver
    chunks = int(seconds / 0.5)
    remainder = seconds % 0.5
    
    for _ in range(chunks):
        try:
            time.sleep(0.5)
        except KeyboardInterrupt:
            # If interrupted during a chunk, just skip remaining sleep
            print(f"[WARNING] Sleep interrupted after {_ * 0.5:.1f}s of {seconds}s, continuing...")
            return
    
    if remainder > 0:
        try:
            time.sleep(remainder)
        except KeyboardInterrupt:
            print(f"[WARNING] Sleep interrupted, continuing...")
            pass


# ---------------- DB Helpers (same as scraper_requests.py) ---------------- #

def ensure_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                company TEXT,
                title TEXT,
                price INTEGER,
                beds INTEGER,
                baths REAL,
                address TEXT,
                last_scraped_at TEXT
            );"""
        )
    print(f"[INFO] Using database: {DB_PATH}")


def _slug(s: str) -> str:
    s = s or ""
    s = s.lower()
    import re
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "unit"


def _stabilize_unit_urls(listings):
    """
    If multiple listings share the same page URL, add a stable fragment
    so they can coexist under a UNIQUE(url) constraint.
    """
    by_url = defaultdict(list)
    for l in listings:
        by_url[l["url"]].append(l)

    for url, rows in by_url.items():
        if len(rows) == 1:
            continue
        # Multiple units per page
        seen = set()
        for l in rows:
            title = (l.get("title") or "").strip()
            price = int(l.get("price") or 0)
            frag = f"#unit-{_slug(title)}-{price}"
            synth = url + frag
            # De-dup if collision happens
            i = 1
            unique_url = synth
            while unique_url in seen:
                i += 1
                unique_url = f"{synth}-{i}"
            seen.add(unique_url)
            l["url"] = unique_url

    return listings


def insert_listings(listings):
    """Insert only valid/priced rows with UPSERT on url conflict."""
    valid = [l for l in listings if l.get("price") not in (None, 0)]
    if not valid:
        return 0

    valid = _stabilize_unit_urls(valid)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executemany(
            """INSERT INTO listings
               (url, company, title, price, beds, baths, address, last_scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   company=excluded.company,
                   title=excluded.title,
                   price=excluded.price,
                   beds=excluded.beds,
                   baths=excluded.baths,
                   address=excluded.address,
                   last_scraped_at=excluded.last_scraped_at
            """,
            [
                (
                    l["url"],
                    l["company"],
                    l["title"],
                    int(l["price"]),
                    int(l.get("beds") or 0),
                    float(l.get("baths") or 0),
                    l.get("address") or "",
                    l["last_scraped_at"],
                )
                for l in valid
            ],
        )
    return len(valid)


# ---------------- Selenium Setup ---------------- #

def create_driver():
    """Create Chrome driver with headless options."""
    options = Options()
    options.add_argument("--headless=new")  # New headless mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # User agent
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(REQUEST_TIMEOUT)
    return driver


# ---------------- Scraper Logic ---------------- #

def scrape_with_selenium(driver, url):
    """Load page with Selenium and return rendered HTML."""
    try:
        print(f"[INFO]   [selenium] Loading: {url}")
        driver.get(url)
        
        # Wait for body to be present
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Handle specific sites with heavy JavaScript
        if "riseonchauncey.com" in url or "lodgetrailpurdue.com" in url:
            print(f"[INFO]   [selenium] Detected JS-heavy site, waiting for dynamic content...")
            safe_sleep(6)  # Extra wait for JS to fully load
            
            # Scroll to trigger lazy loading
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            safe_sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            safe_sleep(2)
            
            # Try clicking "View All" or filter buttons
            try:
                button_texts = ['View All', 'Show All', 'See All', 'Load More']
                for text in button_texts:
                    try:
                        buttons = driver.find_elements(By.XPATH, f"//button[contains(text(), '{text}')]|//a[contains(text(), '{text}')]")
                        if buttons:
                            print(f"[INFO]   [selenium] Clicking '{text}' button...")
                            buttons[0].click()
                            safe_sleep(3)
                            break
                    except:
                        continue
            except Exception as e:
                print(f"[INFO]   [selenium] Could not click filter buttons: {e}")
        
        elif "americancampus.com" in url:
            print(f"[INFO]   [selenium] American Campus site, waiting for property cards...")
            safe_sleep(4)  # Wait for property cards to load
            
            # Scroll to load all cards
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            safe_sleep(2)
        
        elif "weidaapartments.com" in url:
            print(f"[INFO]   [selenium] Weida site, waiting for dynamic listing data...")
            safe_sleep(8)  # Longer wait for listings widget to load via JS
            
            # Scroll to trigger any lazy-loaded content
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            safe_sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            safe_sleep(2)
        
        else:
            # Standard wait for other sites
            safe_sleep(2)
        
        html = driver.page_source
        print(f"[INFO]   [selenium] Rendered {len(html)} bytes")
        return html
    except Exception as e:
        print(f"[ERROR]   [selenium] Failed to load {url}: {e}")
        return None


def check_driver_health(driver):
    """Check if driver is still alive and responsive."""
    try:
        driver.current_url  # Simple check
        return True
    except:
        return False


def scrape_site(driver, start_url, allow_patterns=None, deny_patterns=None):
    """Scrape a site using Selenium for rendering."""
    print(f"\n[INFO] [entry] {start_url}")

    # Try to discover links (this uses requests under the hood)
    discovered = discover_links(
        start_url,
        max_pages=1,
        same_domain=True,
        allow_patterns=allow_patterns,
        deny_patterns=deny_patterns
    )
    print(f"[INFO]   -> found {len(discovered)} candidate links")

    # If no links discovered, scrape the start URL directly with Selenium
    if not discovered:
        print(f"[INFO]   -> No links found, scraping start URL directly with Selenium")
        discovered = [start_url]

    total_inserted = 0

    for link in discovered[:MAX_LINKS_PER_SITE]:
        # Check if driver is still healthy before proceeding
        if not check_driver_health(driver):
            print(f"[ERROR] Driver is dead, cannot continue scraping this site")
            break
        # Skip non-HTML resources
        if any(link.lower().endswith(ext) for ext in (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg")):
            print(f"[INFO]   [skip] Ignored asset: {link}")
            continue

        # Use Selenium to render the page
        html = scrape_with_selenium(driver, link)
        if not html:
            continue

        # Parse with existing parsers
        try:
            results = parse_html_many(link, html)
        except Exception as e:
            print(f"[ERROR]   [parse error] {link} -> {e}")
            continue

        if not results:
            print(f"[INFO]   [skip] No units parsed: {link}")
            continue

        # Insert to database
        inserted = insert_listings(results)
        if inserted:
            total_inserted += inserted
            sample_price = next((r["price"] for r in results if r.get("price")), None)
            if sample_price:
                print(f"[INFO]   [ok] {link} -> {inserted} listings inserted (sample ${sample_price})")
            else:
                print(f"[INFO]   [ok] {link} -> {inserted} listings inserted")
        else:
            print(f"[INFO]   [ok] {link} -> 0 listings inserted")

        safe_sleep(1.5)  # Polite delay between pages

    print(f"[INFO] [done site] {start_url} -> {total_inserted} listings total.\n")


if __name__ == "__main__":
    ensure_db()

    # Same sites as scraper_requests.py
    START_SITES = [
        # Granite
        ("https://granitestudentliving.com/listings/?location=purdue-university",
         [r"/listings", r"location=purdue"], [r"/about", r"/blog", r"/careers", r"/maintenance", r"/payment", r"/resident"]),

        # Weida
        ("https://www.weidaapartments.com/availability",
            [r"/availability", r"/apartments", r"/west-lafayette"],
            [r"/about", r"/blog", r"/contact", r"/resources", r"/residents", r"/maintenance", r"/documents"]),

        # Muinzer
        ("https://www.muinzerclosetocampus.com/availability",
            [r"/availability", r"/properties", r"/purdue", r"/west-lafayette"],
            [r"/residents", r"/parents", r"/sitemap", r"/contact"]),

        # American Campus – floor-plans for both properties
        ("https://www.americancampus.com/student-apartments/in/west-lafayette/chauncey-square/floor-plans#/",
            [r"/chauncey-square/.*floor-plans", r"/detail/"],
            [r"/gallery", r"/amenities", r"/contact", r"/faq", r"/parents", r"/jobs", r"/about-us"]),
        ("https://www.americancampus.com/student-apartments/in/west-lafayette/campus-edge-on-pierce/floor-plans#/",
            [r"/campus-edge-on-pierce/.*floor-plans", r"/detail/"],
            [r"/gallery", r"/amenities", r"/contact", r"/faq", r"/parents", r"/jobs", r"/about-us"]),

        # Redpoint
        ("https://redpoint-westlafayette.com/rates-floorplans/",
            [r"/rates-floorplans"],
            [r"/features", r"/photo-tour", r"/management", r"/sitemap", r"/contact"]),

        # SmartDigs
        ("https://smartdigs.com/availability/",
            [r"/availability", r"/property-listing"],
            [r"/rental-application", r"/useful-information", r"/property-management"]),

        # Yugo – ONLY West Lafayette River Market
        ("https://yugo.com/en-us/global/united-states-of-america/west-lafayette-in/yugo-west-lafayette-river-market/rooms",
            [r"/west-lafayette-in/yugo-west-lafayette-river-market"],
            [r"/global/$", r"/united-kingdom", r"/germany", r"/italy", r"/spain", r"/australia", r"/canada",
             r"/france", r"/ireland", r"/portugal", r"/netherlands", r"/poland",
             r"/united-states-of-america/(?!west-lafayette-in)",
             r"/about", r"/blog", r"/news", r"/careers"]),

        # Alight
        ("https://alight-westlafayette.com/rates-floorplans/",
            [r"/rates-floorplans"],
            [r"/photo-tour", r"/features", r"/management", r"/site-map"]),

        # Purdue Off-Campus Housing
        ("https://offcampushousing.purdue.edu/housing",
            [r"/housing", r"/listing"],
            [r"/account", r"/resources", r"/help"]),

        # RISE - TEMPORARILY DISABLED (requires complex JS interaction)
        # ("https://riseonchauncey.com/availability",
        #     [r"/availability"],
        #     [r"/amenities", r"/gallery", r"/neighborhood", r"/virtual-tour", r"/contact"]),

        # EVER
        ("https://everwestlafayette.com/floor-plans/",
            [r"/floor-plans", r"floorplan="],
            [r"/contact", r"/gallery", r"/amenities", r"/neighborhood", r"/blog"]),

        # BK Management
        ("https://www.bk-management.com/purdue",
            [r"/vacancies", r"/purdue", r"/west-lafayette"],
            [r"/about", r"/residents", r"/owner", r"/management", r"/blog"]),
        ("https://www.bk-management.com/vacancies",
            [r"/vacancies", r"/purdue", r"/west-lafayette"],
            [r"/about", r"/residents", r"/owner", r"/management", r"/blog"]),

        # Wabash Landing
        ("https://wabashlanding.com/floor-plans/",
            [r"/floor-plans"],
            [r"/amenities", r"/location", r"/gallery", r"/about", r"/resident"]),

        # Lodge on the Trail - TEMPORARILY DISABLED (requires complex JS interaction)
        # ("https://www.lodgetrailpurdue.com/all-floor-plans",
        #     [r"/all-floor-plans", r"/pricing"],
        #     [r"/photos", r"/contact", r"/home"]),

        # Fairway
        ("https://www.fairway-apartments.com/availability",
            [r"/availability", r"/floor-plans"],
            [r"/the-amenities", r"/the-gallery", r"/accessibility", r"/photos"]),
    ]

    # Create driver once and reuse, with recovery on crash
    print("[INFO] Initializing Chrome driver...")
    driver = create_driver()
    
    try:
        for i, (url, allow, deny) in enumerate(START_SITES, 1):
            print(f"[INFO] Processing site {i}/{len(START_SITES)}")
            try:
                # Check driver health before starting site
                if not check_driver_health(driver):
                    print("[WARNING] Driver is dead before starting site, recreating...")
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = create_driver()
                
                scrape_site(driver, url, allow_patterns=allow, deny_patterns=deny)
                
            except Exception as e:
                print(f"[ERROR] Site {url} failed with error: {e}")
                print("[INFO] Attempting to recover driver...")
                try:
                    driver.quit()
                except:
                    pass
                # Recreate driver
                driver = create_driver()
                print("[INFO] Driver recovered, continuing to next site...")
    finally:
        print("[INFO] Closing driver...")
        try:
            driver.quit()
        except:
            pass

    print("[INFO] [done]")
