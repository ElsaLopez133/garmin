# Garmin Data Exporter

This project allows you to download, merge, and analyze Garmin data including **HRV, resting heart rate, sleep, and menstrual cycle**. It provides 3 Python scripts:

1. garmin_download.py to download the data
2. garmin_plot.py to plot data
3. garmin_explore_metrics.py to visualize and save the information that ides for each metric

---

## Features

- Download last 3 months (or more) of Garmin data.
- Merge HRV, resting heart rate, sleep, and menstrual cycle into a single CSV for analysis.
- Handles missing data.
- Optional progress bars to visualize download progress.

---

## Installation

1. **Install Python 3.10+** (Windows: download from [python.org](https://www.python.org/downloads/))
2. **Install required packages**:

```bash
pip install garminconnect tqdm pandas matplotlib seaborn numpy
```

3. **(Optional)** Install Garmin Exporter CLI if you want an alternative command-line approach. This allows to only export activities, not health data.

```bash
pip install garminexport
```

4. On Windows, make sure the scripts folder is added to your PATH:

```bash
C:\Users\<YourUser>\AppData\Roaming\Python\Python312\Scripts
```

## Usage

### Option 1 — Python Script

1. Edit garmin_download.py and replace:

```bash
EMAIL = "your_email"
PASSWORD = "your_password"
```

2. Run the script:

```bash
python garmin_download.py
```

3. Output: garmin_data_last_xdays.csv containing columns: date, resting_hr, hrv_rmssd, sleep_hours, cycle_phase

### Option 2 — Garmin Exporter Command-Line

1. Run Garmin Exporter directly:

```bash
garmin-backup.exe your_email --password your_password --backup-dir ./garmin_data -E
```

-E ensures the script continues if an activity fails to download.
You can increase retries if necessary:

```bash
garmin-backup.exe your_email --password your_password --backup-dir ./garmin_data -E --max-retries 10
```
