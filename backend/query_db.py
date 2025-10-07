import sqlite3

conn = sqlite3.connect('data_pipeline/listings.sqlite')
cursor = conn.cursor()

# Check SmartDigs
print("=== SmartDigs listings ===")
cursor.execute('SELECT company, title, beds, baths, price FROM listings WHERE company LIKE "%Smart%" OR url LIKE "%smartdigs%" LIMIT 10')
for row in cursor.fetchall():
    print(f'{row[0]}: {row[1][:40]} - {row[2]}bed/{row[3]}bath - ${row[4]}')

conn.close()
