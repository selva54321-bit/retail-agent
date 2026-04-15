import sqlite3
import json

conn = sqlite3.connect('retailagent.db')
cursor = conn.cursor()
rid = 1

rows = cursor.execute("SELECT * FROM competitor_catalog WHERE retailer_id=? AND catalog_sku='' AND first_seen_at >= datetime('now', '-25 hours')", (rid,)).fetchall()
print('New Arrivals DB Result:', len(rows))

stockout = cursor.execute("SELECT * FROM competitor_catalog WHERE retailer_id=? AND times_seen >= 3 AND times_out_of_stock >= 2", (rid,)).fetchall()
print('Fast Movers DB Result:', len(stockout))

disc = cursor.execute("SELECT * FROM competitor_catalog WHERE retailer_id=? AND times_seen >= 3", (rid,)).fetchall()
print('Eligible Discontinued Count:', len(disc))

intel = cursor.execute("SELECT * FROM market_intelligence ORDER BY computed_at DESC LIMIT 5").fetchall()
print('Market Intel Output:')
for r in intel:
    print(r)
