"""FIT file parsing via fitparse."""  
from pathlib import Path  
import pandas as pd  
  
try:  
    from fitparse import FitFile  
    FIT_AVAILABLE = True  
except ImportError:  
    FIT_AVAILABLE = False  
  
  
def read_fit_file(filepath) -> dict:  
    fit = FitFile(filepath)  
    data = {"file": Path(filepath).name if isinstance(filepath, str) else "unknown",  
            "records": [], "session": {}, "laps": []}  
    for msg in fit.get_messages("session"):  
        for field in msg:  
            if field.name in ("total_distance", "total_time", "avg_heart_rate",  
                              "max_heart_rate", "avg_speed", "max_speed",  
                              "total_ascent", "total_descent", "sport"):  
                data["session"][field.name] = field.value  
        break  
    for msg in fit.get_messages("lap"):  
        lap = {}  
        for field in msg:  
            if field.name in ("total_distance", "avg_heart_rate", "max_heart_rate",  
                              "total_time", "avg_speed", "start_time"):  
                lap[field.name] = field.value  
        data["laps"].append(lap)  
    for msg in fit.get_messages("record"):  
        rec = {}  
        for field in msg:  
            if field.name in ("timestamp", "position_lat", "position_long",  
                              "heart_rate", "altitude", "distance",  
                              "speed", "cadence", "power", "temperature"):  
                rec[field.name] = field.value  
        data["records"].append(rec)  
    data["records_df"] = pd.DataFrame(data["records"]) if data["records"] else pd.DataFrame()  
    return data
