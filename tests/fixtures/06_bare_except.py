"""Голый except, скрывающий все ошибки."""
def parse_age(raw_value):
    try:
        return int(raw_value)
    except:
        return 0
