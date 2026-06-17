"""Комбинация нескольких проблем в одном файле."""
import sqlite3

DB_PASSWORD = "admin123"

def get_orders_by_user(user_name):
    conn = sqlite3.connect("shop.db")
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM orders WHERE user = '{user_name}'")
        return cur.fetchall()
    except:
        return []
