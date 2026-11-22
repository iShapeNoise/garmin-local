#!/usr/bin/env python3  
"""Garmin Data Viewer — entry point."""  
import ttkbootstrap as tb  
from app import GarminViewerApp  
  
  
def main():  
    app = tb.Window(themename="darkly", title="Garmin Data Viewer")  
    app.geometry("1400x900")  
    GarminViewerApp(app)  
    app.mainloop()  
  
  
if __name__ == "__main__":  
    main()
