import re

# Prices like "$1,499", "$1,499 - $1,549", "1499-1549"
def normalize_price(text: str):
    if not text:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", ""))
    if not nums:
        return None
    vals = [float(n) for n in nums]
    avg = sum(vals) / len(vals)
    return int(round(avg))

# Beds like "2", "2 Bed", "2BR", "Studio"
def normalize_beds(text: str):
    if text is None:
        return None
    t = str(text).strip().lower()
    if "studio" in t:
        return 0
    m = re.search(r"\d+", t)
    return int(m.group(0)) if m else None

# Baths like "1", "1.5", "1 bath", "1.5ba"
def normalize_baths(text: str):
    if text is None:
        return None
    t = str(text).strip().lower()
    m = re.search(r"\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None

def normalize_address(addr_str: str):
    if not addr_str:
        return ""
    s = str(addr_str).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()
