# link_discovery.py
import re
from typing import Iterable, List, Optional
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT = 15

# Per-site allow/deny to keep things Purdue-focused
SITE_RULES = [
    ("granitestudentliving.com",
        [r"/listings", r"/property", r"/purdue", r"location=purdue"],
        [r"/about", r"/blog", r"/contact", r"/payment", r"/maintenance",
         r"/banana-blog", r"/careers", r"/resident", r"/guarantor"]),

    ("weidaapartments.com",
        [r"/availability", r"/apartments", r"/available", r"/west-lafayette"],
        [r"/about", r"/blog", r"/contact", r"/resources", r"/residents", r"/maintenance", r"/documents", r"/pet-policy"]),

    ("muinzerclosetocampus.com",
        [r"/availability", r"/properties", r"/purdue", r"/west-lafayette"],
        [r"/residents", r"/parents", r"/sitemap", r"/contact"]),

    ("americancampus.com",
        [r"/chauncey-square/.*floor-plans", r"/campus-edge-on-pierce/.*floor-plans", r"/detail/"],
        [r"#", r"/gallery", r"/amenities", r"/contact", r"/parents", r"/faq", r"/jobs", r"/about-us", r"^/$"]),

    ("redpoint-westlafayette.com",
        [r"/rates-floorplans"],
        [r"/features", r"/photo-tour", r"/management", r"/sitemap", r"/contact"]),

    ("smartdigs.com",
        [r"/availability", r"/property-listing"],
        [r"/rental-application", r"/property-management", r"/useful-information"]),

    # ONLY West Lafayette River Market for Yugo
    ("yugo.com",
        [r"/west-lafayette-in/yugo-west-lafayette-river-market/rooms",
         r"/west-lafayette-in/yugo-west-lafayette-river-market$"],
        [r"/global/", r"/united-kingdom", r"/germany", r"/italy", r"/spain", r"/australia",
         r"/united-states-of-america/(?!west-lafayette-in)"]),

    ("alight-westlafayette.com",
        [r"/rates-floorplans"],
        [r"/photo-tour", r"/features", r"/management", r"/site-map"]),

    ("offcampushousing.purdue.edu",
        [r"/housing", r"/listing"],
        [r"/account", r"/resources", r"/help"]),

    ("riseonchauncey.com",
        [r"/availability"],
        [r"/amenities", r"/gallery", r"/neighborhood", r"/virtual-tour", r"/contact"]),

    ("everwestlafayette.com",
        [r"/floor-plans", r"floorplan="],
        [r"/contact", r"/gallery", r"/amenities", r"/neighborhood", r"/blog"]),

    ("bk-management.com",
        [r"/vacancies", r"/purdue", r"/west-lafayette"],
        [r"/about", r"/residents", r"/owner", r"/management", r"/blog"]),

    ("wabashlanding.com",
        [r"/floor-plans"],
        [r"/amenities", r"/location", r"/gallery", r"/about", r"/resident"]),

    ("lodgetrailpurdue.com",
        [r"/all-floor-plans", r"/pricing"],
        [r"/photos", r"/contact", r"/home"]),

    ("fairway-apartments.com",
        [r"/availability", r"/floor-plans"],
        [r"/the-amenities", r"/the-gallery", r"/accessibility", r"/photos"]),
]

def _match_any(patterns: Iterable[str], path: str) -> bool:
    if not patterns:
        return True
    return any(re.search(p, path) for p in patterns)

def discover_links(start_url: str, *, max_pages: int = 1, same_domain: bool = True,
                   allow_patterns: Optional[List[str]] = None,
                   deny_patterns: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    try:
        resp = requests.get(start_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] {start_url} -> {e}")
        return out

    print(f"[DISCOVERY] Scanning: {start_url}")
    soup = BeautifulSoup(resp.text, "lxml")

    parsed = urlparse(start_url)
    host = parsed.netloc

    # If caller didn’t pass filters, use site rule presets
    if allow_patterns is None or deny_patterns is None:
        for site, allow, deny in SITE_RULES:
            if site in host:
                if allow_patterns is None: allow_patterns = allow
                if deny_patterns   is None: deny_patterns   = deny
                break

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        url = urljoin(start_url, href)

        u = urlparse(url)
        if same_domain and u.netloc != host:
            continue
        if any(url.lower().endswith(ext) for ext in (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg")):
            continue
        # ignore generic fragments unless likely unit anchors
        if u.fragment and not re.search(r"(unit_|floorplan|detail)", u.fragment, re.I):
            continue

        path_q = (u.path or "") + ("?" + (u.query or "") if u.query else "")

        if deny_patterns and _match_any(deny_patterns, path_q):
            continue
        if allow_patterns and not _match_any(allow_patterns, path_q):
            continue

        out.append(url)

    # Deduplicate and cap
    out = list(dict.fromkeys(out))
    print(f"[DISCOVERY] Found {len(out)} links from {start_url}")
    return out[:200]
