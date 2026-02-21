# How Load Forecasting Works in LocalShift

Here's a plain-English explanation of how the system predicts your household's electricity consumption (load) and uses it to make smart battery decisions.

---

## 🏠 What is "Load Forecasting"?

Load forecasting means **predicting how much electricity your home will use** over the next 24 hours, broken into 15-minute chunks (96 time slots). The system needs this prediction so it can decide:

- When to charge the battery from solar vs. the grid
- When to export stored energy for profit
- Whether you have excess solar to run extra appliances

---

## 📊 The Three Ingredients

### 1. Historical Usage Profile (Your Past Behaviour)

The system looks at your **past electricity usage** from the Home Assistant recorder database (controlled by the `HISTORY_WINDOW_DAYS` constant). It builds an **hour-by-hour average** of how much power your home typically uses.

**Key details:**
- It separates **weekdays vs. weekends** — your Monday-Friday usage pattern is different from Saturday-Sunday. If it's a Tuesday forecast, it uses your weekday profile; if it's a Saturday, it uses your weekend profile.
- It needs at least **12 hours with sufficient samples** in each profile (weekday/weekend) before it trusts the day-specific data. If it doesn't have enough weekend data yet, it falls back to a combined (all-days) profile.
- The data source is whatever entity you've configured for load power (typically Teslemetry's load power sensor).
- This profile is **cached until midnight** and refreshed daily, so it doesn't hammer the database.

**Example:** If your home historically uses ~1.2 kW at 7 PM on weekdays but only ~0.8 kW at 7 PM on weekends, the system knows this and uses the right number.

---

### 2. Recent Real-Time Load (What's Happening Right Now)

The system also grabs your **average load over the last 1 hour** from 5-minute statistics. This captures what's happening *right now* — maybe you've got the oven on, or the house is empty.

**How it blends with historical data:**
- There's a configurable **weighting** between recent load and historical data (the `load_weight_recent` setting, default seems to be a fraction like 0.3–0.5).
- **Crucially, recent load only matters for hours close to the current time** (within ±3 hours). If it's 2 PM now, your current usage is a good predictor for 3 PM but a terrible predictor for 2 AM. For distant hours, the system relies **entirely on your historical profile**.
- This recent load data is **cached for 5 minutes** to avoid excessive queries.

**Example:** It's 4 PM and you're running the air conditioner hard (2.5 kW). The system blends this with your historical 4 PM average (1.8 kW) using the configured weighting. But for tonight at 11 PM, it ignores your current high usage and uses the historical 11 PM average (0.4 kW) instead.

---

### 3. Weather/Temperature Correlation (Learning Over Time)

The system has a **weather correlation module** that learns how temperature affects your energy use. It works like this:

- It uses a **degree-day model** — a well-known energy industry approach. It learns:
  - Your **base load** per hour (electricity use at comfortable temperatures)
  - A **cooling coefficient** (how much extra power you use when it's hot — air conditioning)
  - A **heating coefficient** (how much extra power you use when it's cold — heating)
- It defines **temperature thresholds** for cooling and heating (e.g., above 25°C you start cooling, below 18°C you start heating).
- It **learns incrementally** over time using a moving-average approach — every hour, it observes the actual temperature and actual load, and slightly adjusts its model. The more data it sees, the more confident it becomes.
- It can pull **temperature forecasts** from your weather entity and predict load adjustments for future hours.
- Each hour of the day gets its own coefficients (because your 2 PM cooling behaviour is different from your 8 PM behaviour).
- It tracks a **confidence score** per hour, and predictions are only applied when confidence is sufficient.

**Example:** The system has learned that at 3 PM on a 38°C day, your house uses an extra 1.5 kW for cooling compared to a 22°C day. Tomorrow's weather forecast says 36°C at 3 PM, so it bumps up the predicted load accordingly.

---

## 🔄 How It All Comes Together

For each of the 96 fifteen-minute slots in the 24-hour forecast:

1. **Start with the historical profile** for that hour (weekday or weekend as appropriate)
2. **Blend in recent load** if the slot is within 3 hours of the current time
3. **Adjust for temperature** if the weather model has enough confidence for that hour
4. If none of the above are available (e.g., brand new installation), **fall back to the current live load reading**, or a conservative default of 0.6 kW

The result is a consumption estimate in kW for each slot, which gets multiplied by 0.25 hours (15 minutes) to give kWh consumed per slot.

---

## 🔋 What the Forecast Drives

This load prediction feeds directly into critical battery decisions:

- **Grid charging logic:** "Will solar + battery get me through the expensive peak period, or do I need to charge from the grid while prices are cheap?"
- **Proactive export decisions:** "I have excess energy — is it safe to sell some now, or will I need it later tonight?"
- **Overnight drain simulation:** "If I export now, will the battery last through the night until solar kicks in tomorrow morning?"
- **Load shifting signals:** "There's excess solar coming — you could safely run an extra 2 kW appliance for the next hour without triggering grid charging."

---

## 📈 Diagnostics Available

The system exposes several diagnostic attributes so you can see what it's doing:
- **Consumption source** — whether it's using the historical profile, weighted blend, or live fallback
- **Hourly profile** — the actual kW values it's using for each hour
- **Sample counts per hour** — how much historical data backs each hour's estimate
- **Recent 1-hour load** — the real-time average it's blending in
- **Weighting** — the configured balance between recent and historical data
- **Weather correlation diagnostics** — the learned coefficients, confidence, and current temperature adjustments

---

**In short:** The system builds a personalised picture of your home's energy appetite by combining what your home *usually* does (history), what it's doing *right now* (recent load), and what the *weather* will make it do (temperature model). This three-layered approach gets more accurate over time as it learns your patterns.