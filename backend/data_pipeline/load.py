import sqlite3
from schema import DB_PATH

def insert_listing(listing):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT OR IGNORE INTO listings 
    (url, company, title, price, beds, baths, address, last_scraped_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        listing["url"],
        listing["company"],
        listing["title"],
        listing["price"],
        listing["beds"],
        listing["baths"],
        listing["address"],
        listing["last_scraped_at"]
    ))
    conn.commit()
    conn.close()
