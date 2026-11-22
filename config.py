"""Paths, constants and user settings."""  
import json  
from pathlib import Path  
  
APP_DIR = Path(__file__).resolve().parent  
DATA_DIR = APP_DIR / "data"  
SETTINGS_PATH = DATA_DIR / "settings.json"  
DB_PATH = DATA_DIR / "garmin_data.db"  
TOKEN_DIR = DATA_DIR / "garmin_tokens"
  
DEFAULTS = {  
    "email": "",  
    "password": "",  
    "local_fit_path": "",  
    "api_token": str(TOKEN_DIR),
    "connect_on_start": False,  
    "sleep_start": "22:00",  
    "sleep_end": "07:00",  
    "age": 30,  
    "theme": "darkly", 
    "bp_2x2_measure": False,  
    "bp_measure_gap_min": 5, 
}
  
def load_settings() -> dict:  
    if SETTINGS_PATH.exists():  
        try:  
            return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text())}  
        except Exception:  
            pass  
    return dict(DEFAULTS)
  
  
def save_settings(s: dict):  
    DATA_DIR.mkdir(exist_ok=True)  
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))
