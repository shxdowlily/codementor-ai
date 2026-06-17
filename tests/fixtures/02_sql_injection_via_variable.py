"""SQL-инъекция, но строка сначала собирается в переменную."""
import sqlite3

def delete_order(order_id):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    query = "DELETE FROM orders WHERE id = " + str(order_id)
    cur.execute(query)
    conn.commit()
