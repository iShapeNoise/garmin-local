"""Chart colors and blood-pressure category colors."""  
import pandas as pd  
  
COLORS = {  
    "steps":        "#4A90D9",  
    "distance":     "#F5A623",  
    "calories":     "#E74C3C",  
    "active_cal":   "#D35400",  
    "resting_hr":   "#2ECC71",  
    "max_hr":       "#E74C3C",  
    "min_hr":       "#85C1E9",  
    "stress":       "#9B59B6",  
    "stress_low":   "#2ECC71",  
    "stress_med":   "#F1C40F",  
    "stress_high":  "#E74C3C",  
    "sleep_deep":   "#1B2A4A",  
    "sleep_light":  "#5DADE2",  
    "sleep_rem":    "#F4D03F",  
    "sleep_awake":  "#E74C3C",  
    "body_battery": "#27AE60",  
    "respiration":  "#1ABC9C",  
    "bp_sys":       "#E74C3C",  
    "bp_dia":       "#2980B9",  
    "bp_pulse":     "#2ECC71",  
}  
  
BP_COLORS = {  
    "green":  "#2ECC71",  
    "yellow": "#F1C40F",  
    "orange": "#E67E22",  
    "red":    "#E74C3C",  
}  
  
  
def get_bp_color(sys_val, dia_val) -> str:  
    def _cat(s, d):  
        if s >= 180 or d >= 110:  
            return 3  
        elif s >= 160 or d >= 100:  
            return 2  
        elif s >= 140 or d >= 90:  
            return 1  
        else:  
            return 0  
  
    if sys_val is None or (isinstance(sys_val, float) and pd.isna(sys_val)):  
        s = 0  
    else:  
        s = float(sys_val)  
    if dia_val is None or (isinstance(dia_val, float) and pd.isna(dia_val)):  
        d = 0  
    else:  
        d = float(dia_val)  
  
    worst = max(_cat(s, d), _cat(s, 0), _cat(0, d))  
    return [BP_COLORS["green"], BP_COLORS["yellow"], BP_COLORS["orange"], BP_COLORS["red"]][worst]
