"""Прямая SQL-инъекция через f-string."""
import sqlite3

def find_user_by_name(name):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchall()
