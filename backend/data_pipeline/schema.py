import sqlite3
import os
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "listings.sqlite")

def create_listings_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        company TEXT,
        title TEXT NOT NULL,
        price INTEGER,
        beds REAL,
        baths REAL,
        address TEXT,
        last_scraped_at TEXT
    )
    """)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_listings_table()