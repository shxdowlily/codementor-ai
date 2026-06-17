"""Хардкод секрета прямо в коде."""
import requests

API_KEY = "sk-live-9f8a7b6c5d4e3f2a1b"

def fetch_weather(city):
    return requests.get(f"https://api.example.com/weather?city={city}&key={API_KEY}")
