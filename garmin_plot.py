import pandas as pd
import os
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# Load your data
while True:
    try:
        DAYS_BACK = int(input("How many days of data do you want to plot? (e.g. 30, 90, 180): "))
        if DAYS_BACK <= 0:
            raise ValueError
        break
    except ValueError:
        print("⚠️ You need to first download the data!")
    
FILE_NAME = f"./data/garmin_data_{DAYS_BACK}days/garmin_data_{DAYS_BACK}days.csv" 
df = pd.read_csv(FILE_NAME)

# ---- OUTPUT DIRECTORY ----
OUTPUT_DIR = f"./figs/garmin_data_{DAYS_BACK}days"
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"\n📂 Data will be saved in: {OUTPUT_DIR}\n")


# Convert date column to datetime
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date")


# ---- HRV ----
print("\n📈 Plotting HRV data...")

plt.figure(figsize=(12,6))
plt.title("HRV: Night vs Weekly Avg with Baseline Range")

# Shaded baseline
plt.fill_between(df["date"],
                 df["baseline_balancedLow"],
                 df["baseline_balancedUpper"],
                 color="skyblue", alpha=0.3, label="Baseline range")

# Weekly average (line)
plt.plot(df["date"], df["weekly_avg"], label="Weekly Avg", color="blue", linewidth=2)

# Night HRV (dots)
plt.scatter(df["date"], df["hrv_rmssd"], label="Last night HRV", color="black")

plt.ylabel("HRV (rMSSD)")
plt.legend()
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/hrv_plot.jpg")
#plt.show()


# ---- Resting Heart Rate ----
print("\n❤️ Plotting Resting HR data...")

plt.figure(figsize=(12,5))
plt.title("Resting Heart Rate (RHR)")
plt.plot(df["date"], df["resting_hr"], color="red", marker="o", label="RHR")
plt.ylabel("BPM")
plt.legend()
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/rhr_plot.jpg")
#plt.show()

# ---- HRV and RHR ----
print("\n📈 Plotting HRV/RHR data...")

plt.figure(figsize=(12,6))
plt.title("HRV and RHR")

# Primary y-axis for HRV
ax1 = plt.gca()
# Shaded baseline
ax1.fill_between(df["date"],
                 df["baseline_balancedLow"],
                 df["baseline_balancedUpper"],
                 color="skyblue", alpha=0.3, label="Baseline range")

# --- Daily HRV rMSSD (small markers, thin line) ---
plt.plot(df["date"], df["hrv_rmssd"], 
         color="black", marker="o", markersize=4, linewidth=2, alpha=0.9, label="Daily HRV rMSSD")
# --- Weekly average HRV (larger markers, thicker line) ---
plt.plot(df["date"], df["weekly_avg"], 
         color="blue", marker="x", markersize=2, linewidth=1, alpha=0.7, label="Weekly Avg HRV")

ax1.set_ylabel("HRV (rMSSD)")
ax1.grid(alpha=0.3)

# Secondary y-axis for RHR
ax2 = ax1.twinx()
ax2.plot(df["date"], df["resting_hr"], color="red", marker="o", markersize=4, linewidth=2, alpha=0.9, label="RHR")
ax2.set_ylabel("Resting Heart Rate (bpm)")

# Combine legends
lines_labels = [ax.get_legend_handles_labels() for ax in [ax1, ax2]]
lines, labels = [sum(lol, []) for lol in zip(*lines_labels)]
ax1.legend(lines, labels, loc="upper left")

plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/hrv_rhr_plot.jpg")



plt.plot(df["date"], df["resting_hr"], color="red", marker="o", label="RHR")
plt.ylabel("BPM")
plt.legend()
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/rhr_plot.jpg")
#plt.show()

# ---- Sleep ----
print("\n😴 Plotting Sleep data...")

# Colors for sleep stages
colors = {
    "Light": "#F9E79F",
    "Deep": "#85C1E9",
    "REM": "#BB8FCE",
    "Awake": "#E6B0AA"
}

plt.figure(figsize=(12,6))
plt.title("Sleep Composition & Score")
plt.xlabel("Date")
plt.ylabel("Hours of Sleep")

# Stacked bars
light = plt.bar(df["date"], df["light_sleep_hr"], color=colors["Light"])
deep = plt.bar(df["date"], df["deep_sleep_hr"], bottom=df["light_sleep_hr"], color=colors["Deep"])
rem = plt.bar(df["date"], df["rem_sleep_hr"], bottom=df["light_sleep_hr"] + df["deep_sleep_hr"], color=colors["REM"])
awake = plt.bar(df["date"], df["awake_sleep_hr"],
                bottom=df["light_sleep_hr"] + df["deep_sleep_hr"] + df["rem_sleep_hr"],
                color=colors["Awake"])

# Optional: sleep score line
ax2 = plt.gca().twinx()
ax2.set_ylabel("Sleep Score")
ax2.plot(df["date"], df["sleep_score"], color="black", marker="o", markersize=4, linewidth=1, alpha=0.7,)

# Custom legend for sleep stages only
plt.legend([light, deep, rem, awake], colors.keys(), title="Sleep Stage", loc="upper right")

plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/sleep_plot.jpg")
#plt.show()


# ---- Combined ----
print("\n Plotting combined data...")


plt.figure(figsize=(14,6))
plt.title("HRV, RHR & Menstrual Cycle Phases")

# ---- Cycle phase coloring ----
phase_colors = {
    "Fertile": "green",
    "Menstrual": "red",
    "Luteal": "pink",
    "Follicular": "yellow",
    "Ovulation": "purple",
    "Not logged": "white"
}

for i, row in df.iterrows():
    color = phase_colors.get(row["cycle_phase"], "white")
    plt.axvspan(row["date"], row["date"] + pd.Timedelta(days=1), color=color, alpha=0.4)

# ---- HRV baseline band ----
plt.fill_between(df["date"],
                 df["baseline_balancedLow"],
                 df["baseline_balancedUpper"],
                 color="lightblue", alpha=0.4, label="HRV Baseline Range")

# ---- HRV night ----
# --- Daily HRV rMSSD (small markers, thin line) ---
plt.plot(df["date"], df["hrv_rmssd"], 
         color="black", marker="x", markersize=4, linewidth=1, alpha=0.7, label="Daily HRV rMSSD")
# --- Weekly average HRV (larger markers, thicker line) ---
plt.plot(df["date"], df["weekly_avg"], 
         color="blue", marker="o", markersize=7, linewidth=3, alpha=0.9, label="Weekly Avg HRV")

# ---- RHR ----
plt.plot(df["date"], df["resting_hr"], color="red", marker="o", label="RHR")

# ---- Legend for phases ----
phase_patches = [mpatches.Patch(color=c, label=p) for p, c in phase_colors.items()]
plt.legend(handles=[*plt.gca().get_legend_handles_labels()[0], *phase_patches],
           loc="upper left", fontsize=8)

plt.ylabel("HRV (ms) / RHR (BPM)")
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/combined_plot.png")
plt.show()





