"""Small stats helpers."""  
import pandas as pd  
  
  
def compute_stats(series: pd.Series) -> str:  
    s = series.dropna()  
    if s.empty:  
        return "No data"  
    return f"Avg: {s.mean():.1f}  |  Min: {s.min():.1f}  |  Max: {s.max():.1f}"
