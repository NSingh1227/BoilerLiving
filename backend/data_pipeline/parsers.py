# parsers.py
import re, json, time
from typing import Dict, Optional, Callable, List, Tuple, Union
from bs4 import BeautifulSoup

from normalize import (
    normalize_price, normalize_beds, normalize_baths, normalize_address
)

Parsed = Dict[str, object]

# ---------- Regex ----------
_PRICE_RX = re.compile(r"\$\s*\d[\d,]*(?:\s*-\s*\$\s*\d[\d,]*)?")
_BEDS_RX  = re.compile(r"(\d+)\s*(?:bed|beds|br|bd)\b", re.I)
_BATHS_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bath|baths|ba)\b", re.I)

def _is_plausible_rent(v: int) -> bool:
    return 300 <= v <= 6000

def _parse_baths(text: str) -> float:
    """Extract bath count from text, handling fractions like ½, ¼, ¾"""
    # Convert fraction symbols to decimal
    text = text.replace('½', '.5').replace('¼', '.25').replace('¾', '.75')
    bath_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Bath|BA|bathroom)', text, re.I)
    if bath_match:
        return float(bath_match.group(1))
    return 0.0

# ---------- Helpers ----------
def _visible_text(soup: BeautifulSoup) -> str:
    if not soup:
        return ""
    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    return soup.get_text(" ", strip=True)

def _json_in_script(soup: BeautifulSoup) -> Optional[dict]:
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except Exception:
            pass
    for script in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, (dict, list)):
                return data
        except Exception:
            continue
    for script in soup.find_all("script"):
        txt = (script.string or "").strip()
        if not txt or "{" not in txt or "}" not in txt:
            continue
        try:
            start = txt.find("{")
            end   = txt.rfind("}")
            blob  = txt[start:end+1]
            data  = json.loads(blob)
            if isinstance(data, (dict, list)):
                return data
        except Exception:
            continue
    return None

def _prices_from_text(text: str) -> List[int]:
    vals: List[int] = []
    for m in _PRICE_RX.finditer(text or ""):
        raw = m.group(0)
        part = raw.split("-")[0] if "-" in raw else raw
        digits = re.sub(r"[^\d]", "", part)
        if digits:
            v = int(digits)
            if _is_plausible_rent(v):
                vals.append(v)
    return vals

def _now() -> str:
    return time.strftime("%m-%d-%Y")

def _mk_record(url: str, company: str, title: str, price: Optional[Union[str,int]],
               beds: Optional[Union[str,int]]=0, baths: Optional[Union[str,float]]=0.0,
               address: Optional[str]="") -> Optional[Parsed]:
    if price is None:
        return None
    p = normalize_price(str(price)) if not isinstance(price, int) else price
    if not p or not _is_plausible_rent(int(p)):
        return None
    return {
        "url": url,
        "company": company,
        "title": (title or company)[:200],
        "price": int(p),
        "beds": normalize_beds(beds) if beds not in (None, "") else 0,
        "baths": normalize_baths(baths) if baths not in (None, "") else 0.0,
        "address": normalize_address(address) if address else "",
        "last_scraped_at": _now(),
    }

def _title_or(soup: BeautifulSoup, default: str) -> str:
    return (soup.title.get_text(strip=True) if soup and soup.title else default)

def _extract_address(soup: BeautifulSoup, fallback: str = "") -> str:
    """Extract address from JSON-LD structured data or HTML elements."""
    if not soup:
        return fallback
    
    # 1. Try JSON-LD structured data (most reliable)
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and 'address' in data:
                addr = data['address']
                if isinstance(addr, dict):
                    street = addr.get('streetAddress', '').strip()
                    city = addr.get('addressLocality', '').strip()
                    state = addr.get('addressRegion', '').strip()
                    zip_code = addr.get('postalCode', '').strip()
                    # Build full address
                    parts = [street, city]
                    if state:
                        parts.append(state)
                    if zip_code:
                        parts[-1] = f"{parts[-1]} {zip_code}" if parts else zip_code
                    full = ", ".join(p for p in parts if p)
                    if full:
                        return full
        except:
            pass
    
    # 2. Try address HTML elements
    for addr_el in soup.select('[itemprop="address"], .address, address, .location'):
        text = addr_el.get_text(strip=True)
        # Must have a street number or "West Lafayette"
        if (re.search(r'\d+\s+\w+', text) or 'West Lafayette' in text) and len(text) < 200:
            return text
    
    # 3. Try meta tags
    for meta in soup.find_all('meta', attrs={'property': re.compile('address|location', re.I)}):
        content = meta.get('content', '').strip()
        if content and len(content) < 200:
            return content
    
    # 4. Fallback to property name + West Lafayette
    return fallback

# ---------- Site parsers (light; JS-heavy pages may still need JSON endpoints) ----------
def parse_granite(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "Granite Student Living, West Lafayette, IN")
    
    # Granite has a .listings-list__item structure with all listing data
    listing_items = soup.select("a.listings-list__item")
    
    for item in listing_items:
        txt = _visible_text(item)
        
        # Skip "Early Inquiry List" items that have "Not Available" (no price yet)
        if "not available" in txt.lower() and "early inquiry" in txt.lower():
            continue
        
        # Extract title (first line usually)
        title_match = re.match(r'^([^\n]+)', txt)
        title = title_match.group(1) if title_match else "Granite Property"
        
        # Extract beds - look for pattern like "1 Beds" or "2 Beds"
        # Need to be careful: "Unit 8" should not be parsed as beds
        # Pattern: number followed by "Bed" or "Beds" with comma or space before
        bed_match = re.search(r'(?:^|[,\s])(\d+)\s*Beds?(?:[,\s]|$)', txt, re.I)
        beds = int(bed_match.group(1)) if bed_match else 0
        
        # Check for studio
        if 'studio' in txt.lower():
            beds = 0
        
        # Extract baths
        baths = _parse_baths(txt)
        
        # Extract price - look for "$XXX / month"
        price_match = re.search(r'\$\s*([\d,]+)\s*/\s*month', txt)
        if not price_match:
            continue
        
        price = int(price_match.group(1).replace(',', ''))
        
        rec = _mk_record(url, "Granite Student Living", title, price, beds, baths, address=address)
        if rec:
            results.append(rec)
    
    return results

def parse_weida(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    # Weida uses AppFolio structure: div.listing-item with h3.rent, div.feature.beds/baths, h2.address
    for item in soup.select(".listing-item"):
        rent_el = item.select_one("h3.rent")
        if not rent_el:
            continue
        price_text = rent_el.get_text(strip=True)
        prices = _prices_from_text(price_text)
        if not prices:
            continue
        
        addr_el = item.select_one("h2.address, .address")
        title = addr_el.get_text(strip=True) if addr_el else "Weida Unit"
        
        beds_el = item.select_one(".feature.beds")
        baths_el = item.select_one(".feature.baths")
        beds = re.search(r"(\d+)", beds_el.get_text()) if beds_el else None
        baths = re.search(r"(\d+\.?\d?)", baths_el.get_text()) if baths_el else None
        
        # Extract address from item
        addr = item.select_one("h2.address, .address")
        address = addr.get_text(strip=True) if addr else "Weida Apartments, West Lafayette, IN"
        
        rec = _mk_record(
            url, "Weida Apartments", title, prices[0],
            beds.group(1) if beds else 0,
            baths.group(1) if baths else 0,
            address=address
        )
        if rec: results.append(rec)
    return results

def parse_muinzer(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    # AppFolio structure: div.listing-item with h3.rent, div.amenities, h2.address
    for item in soup.select(".listing-item"):
        rent_el = item.select_one("h3.rent")
        if not rent_el:
            continue
        price_text = rent_el.get_text(strip=True)
        prices = _prices_from_text(price_text)
        if not prices:
            continue
        
        addr_el = item.select_one("h2.address, .address")
        title = addr_el.get_text(strip=True) if addr_el else "Muinzer Unit"
        
        beds_el = item.select_one(".feature.beds")
        baths_el = item.select_one(".feature.baths")
        beds_match = re.search(r"(\d+)", beds_el.get_text()) if beds_el else None
        baths_match = re.search(r"(\d+\.?\d?)", baths_el.get_text()) if baths_el else None
        
        # Convert to int/float
        beds = int(beds_match.group(1)) if beds_match else 0
        baths = float(baths_match.group(1)) if baths_match else 0.0
        
        # Extract address from item
        addr = item.select_one("h2.address, .address")
        address = addr.get_text(strip=True) if addr else "Muinzer Apartments, West Lafayette, IN"
        
        rec = _mk_record(
            url, "Muinzer", title, prices[0],
            beds, baths, address=address
        )
        if rec: results.append(rec)
    
    # Fallback to JSON and text parsing if no structured listings found
    if not results:
        data = _json_in_script(soup)
        if data:
            text = json.dumps(data)
            for p in _prices_from_text(text):
                rec = _mk_record(url, "Muinzer", _title_or(soup,"Muinzer Unit"), p)
                if rec: results.append(rec)
    return results

def parse_american_campus(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "American Campus, West Lafayette, IN")
    
    # Try parsing individual .property cards (Campus Edge format)
    for prop in soup.select(".property"):
        txt = _visible_text(prop)
        # Look for price ranges like "$1,499-$1,549"
        price_match = re.search(r'\$\s*([\d,]+)\s*-\s*\$\s*([\d,]+)', txt)
        if price_match:
            # Use the lower price from the range
            price_str = price_match.group(1).replace(',', '')
            price = int(price_str)
        else:
            prices = _prices_from_text(txt)
            if not prices:
                continue
            price = prices[0]
        
        # Extract beds/baths
        beds = 0
        baths = 0.0
        bed_match = re.search(r'(\d+)\s*(?:Bed|BR)', txt, re.I)
        if bed_match:
            beds = int(bed_match.group(1))
        bath_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Bath|BA)', txt, re.I)
        if bath_match:
            baths = float(bath_match.group(1))
        
        # Extract title
        title_el = prop.select_one("h3, h4, .title, .name")
        title = title_el.get_text(strip=True) if title_el else "American Campus Unit"
        
        rec = _mk_record(url, "American Campus", title, price, beds, baths, address=address)
        if rec: results.append(rec)
    
    if results: return results
    
    # Fallback: Try JSON-LD data
    data = _json_in_script(soup)
    if data:
        text = json.dumps(data)
        for p in _prices_from_text(text):
            rec = _mk_record(url, "American Campus", _title_or(soup,"American Campus Unit"), p, address=address)
            if rec: results.append(rec)
        if results: return results
    
    # Final fallback: scan all text
    for p in _prices_from_text(_visible_text(soup)):
        rec = _mk_record(url, "American Campus", _title_or(soup,"American Campus Unit"), p, address=address)
        if rec: results.append(rec)
    return results

def parse_redpoint(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "2900 Snowdrop Dr, West Lafayette, IN")
    
    # Try extracting from .floorplan containers
    for plan in soup.select(".floorplan"):
        txt = _visible_text(plan)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
        beds = int(bed_match.group(1)) if bed_match else 0
        baths = _parse_baths(txt)
        
        title = plan.select_one("h2, h3, .title, .name")
        title_text = title.get_text(strip=True) if title else "Redpoint Unit"
        
        for p in prices:
            rec = _mk_record(url, "Redpoint West Lafayette", title_text, p, beds, baths, address=address)
            if rec: results.append(rec)
    
    # Fallback: extract from page text if no floorplans found
    if not results:
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "Redpoint West Lafayette", _title_or(soup,"Redpoint Unit"), p, address=address)
            if rec: results.append(rec)
    return results

def parse_alight(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "2243 Sagamore Parkway West, West Lafayette, IN 47906")
    
    # Try extracting from .floorplan containers
    for plan in soup.select(".floorplan"):
        txt = _visible_text(plan)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        title = plan.select_one("h2, h3, .title, .name")
        title_text = title.get_text(strip=True) if title else "Alight Unit"
        
        # Check if it's a studio
        if 'studio' in txt.lower() or 'studio' in title_text.lower():
            beds = 0  # Studios have 0 bedrooms by definition
            baths = _parse_baths(txt)
            if baths == 0.0:  # If baths not found, assume 1 bath for studios
                baths = 1.0
        else:
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
            beds = int(bed_match.group(1)) if bed_match else 0
            baths = _parse_baths(txt)
        
        for p in prices:
            rec = _mk_record(url, "Alight West Lafayette", title_text, p, beds, baths, address=address)
            if rec: results.append(rec)
    
    # Fallback: extract from page text if no floorplans found
    if not results:
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "Alight West Lafayette", _title_or(soup,"Alight Unit"), p, address=address)
            if rec: results.append(rec)
    return results

def parse_yugo(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "Yugo West Lafayette, West Lafayette, IN")
    
    # Try extracting from article containers
    for article in soup.select("article"):
        txt = _visible_text(article)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        title = article.select_one("h2, h3, .title, .name, .room-name")
        title_text = title.get_text(strip=True) if title else "Yugo Unit"
        
        # Check if it's a studio
        if 'studio' in txt.lower() or 'studio' in title_text.lower():
            beds = 0  # Studios have 0 bedrooms by definition
            baths = _parse_baths(txt)
            if baths == 0.0:  # If no baths found, assume 1 bath for studios
                baths = 1.0
        else:
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
            beds = int(bed_match.group(1)) if bed_match else 0
            baths = _parse_baths(txt)
        
        for p in prices:
            rec = _mk_record(url, "Yugo", title_text, p, beds, baths, address=address)
            if rec: results.append(rec)
    
    # Fallback to JSON if no articles found
    if not results:
        data = _json_in_script(soup)
        if data:
            text = json.dumps(data)
            for p in _prices_from_text(text):
                rec = _mk_record(url, "Yugo", _title_or(soup,"Yugo Unit"), p, address=address)
                if rec: results.append(rec)
    
    # Final fallback: extract from page text
    if not results:
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "Yugo", _title_or(soup,"Yugo Unit"), p, address=address)
            if rec: results.append(rec)
    return results

def parse_bk(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    # AppFolio structure: same as Muinzer
    for item in soup.select(".listing-item"):
        rent_el = item.select_one("h3.rent")
        if not rent_el:
            continue
        price_text = rent_el.get_text(strip=True)
        prices = _prices_from_text(price_text)
        if not prices:
            continue
        
        addr_el = item.select_one("h2.address, .address")
        title = addr_el.get_text(strip=True) if addr_el else "BK Listing"
        
        beds_el = item.select_one(".feature.beds")
        baths_el = item.select_one(".feature.baths")
        beds_match = re.search(r"(\d+)", beds_el.get_text()) if beds_el else None
        baths_match = re.search(r"(\d+\.?\d?)", baths_el.get_text()) if baths_el else None
        
        # Convert to int/float
        beds = int(beds_match.group(1)) if beds_match else 0
        baths = float(baths_match.group(1)) if baths_match else 0.0
        
        addr = item.select_one("h2.address, .address")
        address = addr.get_text(strip=True) if addr else "BK Management, West Lafayette, IN"
        
        rec = _mk_record(
            url, "BK Management", title, prices[0],
            beds, baths, address=address
        )
        if rec: results.append(rec)
    
    # Fallback to text parsing
    if not results:
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "BK Management", _title_or(soup,"BK Listing"), p)
            if rec: results.append(rec)
    return results

def parse_wabash(url: str, soup: BeautifulSoup) -> List[Parsed]:
    """Parse Wabash Landing - Entrata-based floor plans"""
    results: List[Parsed] = []
    address = _extract_address(soup, "Wabash Landing, West Lafayette, IN")
    
    # Look for floor plan unit containers
    for unit in soup.select(".floorp-unit-container, .floor-plan-box"):
        txt = _visible_text(unit)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        # Extract title/floor plan name
        title_el = unit.select_one(".floor-title, .floorplan-block-info-name, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else "Wabash Unit"
        
        # Look for pattern like "2 | 2" or "2 | 2 |" (beds | baths)
        pipe_match = re.search(r'(\d+)\s*\|\s*(\d+(?:\.\d+)?)', txt)
        if pipe_match:
            beds = int(pipe_match.group(1))
            baths = float(pipe_match.group(2))
        else:
            # Fallback to standard bed/bath patterns
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
            beds = int(bed_match.group(1)) if bed_match else 0
            baths = _parse_baths(txt)
        
        # Check for studio
        if 'studio' in txt.lower() or 'studio' in title.lower():
            beds = 0
            if baths == 0.0:
                baths = 1.0
        
        for p in prices:
            rec = _mk_record(url, "Wabash Landing", title, p, beds, baths, address=address)
            if rec: results.append(rec)
    
    # Fallback if no units found
    if not results:
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "Wabash Landing", _title_or(soup,"Wabash Listing"), p, address=address)
            if rec: results.append(rec)
    
    return results

def parse_ever(url: str, soup: BeautifulSoup) -> List[Parsed]:
    """Parse EVER West Lafayette floor plans"""
    results: List[Parsed] = []
    address = _extract_address(soup, "147 W. Wood Street, West Lafayette, IN 47906")
    
    # EVER uses a complex JavaScript-based floor plan system
    # Try to extract from modal components or grid items
    for item in soup.select("[class*='floor'], [class*='plan'], [class*='unit'], .js-filter > div"):
        txt = _visible_text(item)
        
        # Skip if too short or too long (not a listing)
        if len(txt) < 20 or len(txt) > 1000:
            continue
        
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        # Extract bed/bath
        bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
        beds = int(bed_match.group(1)) if bed_match else 0
        
        bath_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Bath|BA)', txt, re.I)
        baths = float(bath_match.group(1)) if bath_match else 0.0
        
        # Check for studio
        if 'studio' in txt.lower():
            beds = 0
            if baths == 0.0:
                baths = 1.0
        
        # Extract title
        title_el = item.select_one("h2, h3, [class*='title'], [class*='name']")
        title = title_el.get_text(strip=True) if title_el else "EVER Unit"
        
        if beds > 0 or baths > 0:  # Only add if we found bed or bath data
            for p in prices:
                rec = _mk_record(url, "EVER West Lafayette", title, p, beds, baths, address=address)
                if rec: results.append(rec)
    
    # If no structured data found, parse full page text
    if not results:
        full_text = _visible_text(soup)
        # Look for patterns like "1 Bed 1 Bath $1,459"
        pattern = r'(\d+)\s*Bed[^$]*?(\d+(?:\.\d+)?)\s*Bath[^$]*?\$\s*([\d,]+)'
        matches = re.findall(pattern, full_text, re.I)
        
        for beds_str, baths_str, price_str in matches:
            beds = int(beds_str)
            baths = float(baths_str)
            price = int(price_str.replace(',', ''))
            
            rec = _mk_record(url, "EVER West Lafayette", "EVER Unit", price, beds, baths, address=address)
            if rec: results.append(rec)
    
    return results

def parse_purdue(url: str, soup: BeautifulSoup) -> List[Parsed]:
    """Parse Purdue Off-Campus Housing (offcampushousing.purdue.edu)"""
    results: List[Parsed] = []
    
    # Look for listing cards (Angular-based site)
    for card in soup.select(".listing-card, article.listing-card"):
        txt = _visible_text(card)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        
        # Skip property-level listings with bed ranges (e.g., "Studio - 2 Beds", "1-2 Beds")
        # These are aggregated property listings, not specific units
        if re.search(r'(?:Studio|Bed|\d+)\s*-\s*\d+\s*Bed', txt, re.I):
            continue
        
        # Extract beds (usually present)
        bed_match = re.search(r'(\d+)\s*(?:Bed|BR|bedroom)', txt, re.I)
        beds = int(bed_match.group(1)) if bed_match else 0
        
        # Check for studio
        if 'studio' in txt.lower():
            beds = 0
        
        # Extract baths (may not always be present)
        baths = _parse_baths(txt)
        if baths == 0.0:
            # If no explicit bath count, assume 1 bath minimum for valid listings
            bath_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Bath|BA)', txt, re.I)
            if bath_match:
                baths = float(bath_match.group(1))
            else:
                # Default to 1 bath if beds are specified
                baths = 1.0 if beds > 0 else 0.0
        
        # Extract title/property name
        title_el = card.select_one("h2, h3, [class*='title'], [class*='name']")
        title = title_el.get_text(strip=True) if title_el else "Purdue Off-Campus Unit"
        
        # Extract address (may be in card)
        address = _extract_address(card, "West Lafayette, IN")
        
        for p in prices:
            rec = _mk_record(url, "Purdue Off-Campus Housing", title, p, beds, baths, address=address)
            if rec: results.append(rec)
    
    # Fallback: try article tags
    if not results:
        for article in soup.select("article"):
            txt = _visible_text(article)
            prices = _prices_from_text(txt)
            if not prices or len(prices) > 5:  # Skip if too many prices (likely not a listing)
                continue
            
            # Skip property-level listings with bed ranges
            if re.search(r'(?:Studio|Bed|\d+)\s*-\s*\d+\s*Bed', txt, re.I):
                continue
            
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR)', txt, re.I)
            beds = int(bed_match.group(1)) if bed_match else 0
            baths = _parse_baths(txt)
            if baths == 0.0 and beds > 0:
                baths = 1.0
            
            title = _title_or(soup, "Purdue Off-Campus Unit")
            address = _extract_address(article, "West Lafayette, IN")
            
            for p in prices:
                rec = _mk_record(url, "Purdue Off-Campus Housing", title, p, beds, baths, address=address)
                if rec: results.append(rec)
    
    return results

def parse_rise(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    # Try structured elements first (similar patterns to other sites)
    for item in soup.select(".listing-item, .unit-card, .floorplan, .property-card"):
        txt = _visible_text(item)
        prices = _prices_from_text(txt)
        if not prices:
            continue
        beds  = (re.search(_BEDS_RX, txt).group(1) if re.search(_BEDS_RX, txt) else 0)
        baths = (re.search(_BATHS_RX, txt).group(1) if re.search(_BATHS_RX, txt) else 0)
        title_el = item.select_one("h2, h3, .title, .name")
        title = title_el.get_text(strip=True) if title_el else "RISE Unit"
        address = _extract_address(item, "RISE on Chauncey, West Lafayette, IN")
        rec = _mk_record(url, "RISE on Chauncey", title, prices[0], beds, baths, address=address)
        if rec: results.append(rec)
    
    if not results:
        address = _extract_address(soup, "RISE on Chauncey, West Lafayette, IN")
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "RISE on Chauncey", _title_or(soup,"RISE Listing"), p, address=address)
            if rec: results.append(rec)
    return results

def parse_fairway(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    # AppFolio structure: same as Muinzer and BK
    for item in soup.select(".listing-item"):
        rent_el = item.select_one("h3.rent")
        if not rent_el:
            continue
        price_text = rent_el.get_text(strip=True)
        prices = _prices_from_text(price_text)
        if not prices:
            continue
        
        addr_el = item.select_one("h2.address, .address")
        title = addr_el.get_text(strip=True) if addr_el else "Fairway Unit"
        
        beds_el = item.select_one(".feature.beds")
        baths_el = item.select_one(".feature.baths")
        beds = re.search(r"(\d+)", beds_el.get_text()) if beds_el else None
        baths = re.search(r"(\d+\.?\d?)", baths_el.get_text()) if baths_el else None
        
        # Extract address from item or use fallback
        addr = item.select_one("h2.address, .address")
        address = addr.get_text(strip=True) if addr else "Fairway Apartments, West Lafayette, IN"
        
        rec = _mk_record(
            url, "Fairway Apartments", title, prices[0],
            beds.group(1) if beds else 0,
            baths.group(1) if baths else 0,
            address=address
        )
        if rec: results.append(rec)
    
    # Fallback to text parsing
    if not results:
        address = _extract_address(soup, "Fairway Apartments, West Lafayette, IN")
        for p in _prices_from_text(_visible_text(soup)):
            rec = _mk_record(url, "Fairway Apartments", _title_or(soup,"Fairway Unit"), p, address=address)
            if rec: results.append(rec)
    return results

def parse_smartdigs(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    
    # SmartDigs embeds listing data as JSON in HTML comments
    # Look for JSON data like: "bedrooms": "3", "bathrooms": "1.0", "marketrent": "$1,395.00"
    import json
    html_text = str(soup)
    
    # Find JSON arrays in comments
    for match in re.finditer(r'<!--\s*Query Data.*?\[(.*?)\]\s*-->', html_text, re.DOTALL):
        json_text = '[' + match.group(1) + ']'
        try:
            listings = json.loads(json_text)
            for listing in listings:
                if not isinstance(listing, dict):
                    continue
                    
                # Extract data from JSON
                beds = int(listing.get('bedrooms', 0))
                baths = float(listing.get('bathrooms', 0.0))
                
                # Parse price from marketrent field: "$1,395.00"
                price_str = listing.get('marketrent', '')
                prices = _prices_from_text(price_str)
                if not prices:
                    continue
                
                # Build address from street1, city, state, zip
                street1 = listing.get('street1', '')
                city = listing.get('city', 'Lafayette')
                state = listing.get('state', 'IN')
                zip_code = listing.get('zip', '')
                address = f"{street1}, {city}, {state} {zip_code}".strip()
                if not address or address == ", ,  ":
                    address = "SmartDigs, Lafayette, IN"
                
                # Use unit name or street as title
                title = listing.get('unit', 'SmartDigs Property')
                if not title or title.strip() == '':
                    title = street1 if street1 else 'SmartDigs Property'
                
                rec = _mk_record(url, "SmartDigs", title, prices[0], beds, baths, address=address)
                if rec: results.append(rec)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            # If JSON parsing fails, continue to next match
            continue
    
    return results

def parse_default(url: str, soup: BeautifulSoup) -> List[Parsed]:
    results: List[Parsed] = []
    address = _extract_address(soup, "West Lafayette, IN")
    for p in _prices_from_text(_visible_text(soup)):
        rec = _mk_record(url, "Unknown", _title_or(soup,"Listing"), p, address=address)
        if rec: results.append(rec)
    return results

# ---------- Registry ----------
PARSERS: Dict[str, Callable[[str, BeautifulSoup], List[Parsed]]] = {
    "granitestudentliving.com": parse_granite,
    "weidaapartments.com":      parse_weida,
    "muinzerclosetocampus.com": parse_muinzer,
    "americancampus.com":       parse_american_campus,
    "redpoint-westlafayette.com": parse_redpoint,
    "alight-westlafayette.com": parse_alight,
    "yugo.com":                 parse_yugo,
    "smartdigs.com":            parse_smartdigs,
    "bk-management.com":        parse_bk,
    "wabashlanding.com":        parse_wabash,
    "everwestlafayette.com":    parse_ever,
    "offcampushousing.purdue.edu": parse_purdue,
    "riseonchauncey.com":       parse_rise,
    "fairway-apartments.com":   parse_fairway,
}

def _pick_parser(url: str) -> Callable[[str, BeautifulSoup], List[Parsed]]:
    for host, fn in PARSERS.items():
        if host in url:
            return fn
    return parse_default

# ---------- Entry Points ----------
def parse_html(url: str, html: str) -> List[Parsed]:
    soup = BeautifulSoup(html or "", "lxml")
    parser = _pick_parser(url)
    return parser(url, soup)

def parse_html_many(*args) -> List[Parsed]:
    """
    Flexible entry:
      - parse_html_many(url, html)
      - parse_html_many({url: html, url2: html2, ...})
    """
    items: List[Tuple[str, str]] = []
    if len(args) == 2 and isinstance(args[0], str):
        items = [(args[0], args[1])]
    elif len(args) == 1 and isinstance(args[0], dict):
        items = list(args[0].items())
    else:
        raise TypeError("parse_html_many expects (url, html) or ({url: html})")

    all_results: List[Parsed] = []
    for url, html in items:
        try:
            recs = parse_html(url, html)
            recs = [r for r in recs if r.get("price")]
            all_results.extend(recs)
        except Exception as e:
            print(f"[ERROR]   [parse error] {url} -> {e}")
    return all_results
