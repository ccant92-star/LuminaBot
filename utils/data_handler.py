import json
import os

DATA_FILE = "lumina_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"user_zips": {}, "sales_data": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)
