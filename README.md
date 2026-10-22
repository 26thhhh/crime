# SAPD Call History

Terminal viewer and collector for San Antonio Police Department public calls-for-service data. The source is city-specific; ZIP configuration only sets the user's proximity reference.

Run `python3 crime_ui.py`. The first interactive launch asks for a five-digit United States ZIP code. Change it later with `python3 crime_ui.py --set-zip [ZIP_CODE]`.

Run `python3 collect.py` to fetch public records and `python3 -m unittest -v` to run the tests. This project uses only the Python standard library.

Generated SQLite history, logs, and local location settings are excluded from source control.
