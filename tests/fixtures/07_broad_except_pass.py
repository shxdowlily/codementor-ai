"""except Exception: pass - тихо проглоченная ошибка."""
def save_to_disk(data, path):
    try:
        with open(path, "w") as f:
            f.write(data)
    except Exception:
        pass
