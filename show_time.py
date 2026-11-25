#!/usr/bin/env python3
import time
import datetime

def display_time_continuously():
    """Display current time continuously every second in format: hours:minutes:seconds.milliseconds"""
    try:
        while True:
            # Get current time
            now = datetime.datetime.now()
            
            # Format: hours:minutes:seconds.milliseconds
            time_str = now.strftime("%H:%M:%S.%f")[:-3]  # Remove last 3 digits of microseconds to get milliseconds
            
            # Clear line and display time (for continuous update in terminal)
            print(f"\r{time_str}", end="", flush=True)
            
            # Wait for 1 second
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nTime display stopped.")

if __name__ == "__main__":
    display_time_continuously()