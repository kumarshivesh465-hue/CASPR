# AgriSense — Full System Setup Guide

## System Overview

```
ESP32 (rover)
  │  reads NPK, pH, moisture, temp, humidity every 60s
  │  uploads over mobile hotspot WiFi
  ▼
Firebase Realtime Database
  │  /rover/{field_id}/readings/{timestamp}   ← raw sensor readings
  │  /rover/{field_id}/latest                 ← most recent reading
  │  /satellite/{field_id}/latest             ← from Streamlit app
  │  /recommendations/{field_id}/latest       ← from Cloud Function
  ▼
Cloud Function (auto-triggers on new rover data)
  │  fuses rover + satellite data
  │  runs recommendation logic
  ▼
Website (index.html)
  │  real-time dashboard
  │  connects directly to Firebase
```

---

## STEP 1 — Firebase Setup (5 minutes)

1. Go to https://console.firebase.google.com
2. Click **"Add project"** → name it (e.g. `agrisense`) → Continue
3. **Realtime Database** → Create database → Start in **test mode** (you can add security rules later)
4. Note your Database URL — looks like: `https://agrisense-default-rtdb.firebaseio.com`

### Create an Auth user for the ESP32
1. Firebase Console → **Authentication** → Get started → **Email/Password** → Enable
2. **Users** tab → **Add user**
   - Email: `rover@agrisense.com`
   - Password: choose something strong
3. Keep these credentials — you'll paste them into the ESP32 firmware

### Get your Web API Key
- Firebase Console → ⚙️ Project Settings → General tab
- Copy **Web API Key** (looks like `AIzaSy...`)

---

## STEP 2 — Streamlit App

### Install
```bash
pip3 install streamlit numpy pandas matplotlib rasterio requests earthengine-api
```

### Configure (in the sidebar when running)
- **Firebase Database URL**: paste your URL from Step 1
- **Field ID**: e.g. `field_hyderabad_01` (use the same string everywhere)
- **Auto-upload toggle**: turn ON

### Run
```bash
streamlit run app.py
```

After analysis completes, results upload automatically to:
`/satellite/{field_id}/latest` and `/satellite/{field_id}/{date}`

---

## STEP 3 — ESP32 Firmware

### Arduino IDE Setup
1. Install **Arduino IDE** from https://arduino.cc
2. **File → Preferences** → Additional boards manager URLs:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Tools → Board → Board Manager** → search `esp32` → install **esp32 by Espressif**

### Install Libraries
**Tools → Manage Libraries**, install:
- `Firebase ESP Client` by Mobizt
- `DHT sensor library` by Adafruit
- `ArduinoJson` by Benoit Blanchon

### Configure the firmware
Open `esp32/rover_firmware.ino` and edit these lines:

```cpp
#define WIFI_SSID        "YourHotspotName"       // your phone hotspot name
#define WIFI_PASSWORD    "YourHotspotPassword"   // hotspot password
#define FIREBASE_API_KEY      "AIzaSy..."         // from Step 1
#define FIREBASE_DATABASE_URL "https://agrisense-default-rtdb.firebaseio.com"
#define FIREBASE_USER_EMAIL   "rover@agrisense.com"
#define FIREBASE_USER_PASS    "YourPassword"
#define FIELD_ID "field_hyderabad_01"             // must match Streamlit
```

### Upload
1. Connect ESP32 via USB
2. **Tools → Board** → select your ESP32 variant
3. **Tools → Port** → select the ESP32 COM/USB port
4. Click Upload (→)
5. Open **Serial Monitor** (115200 baud) — you should see sensor readings and "✅ Upload success"

### Wiring
```
MAX3485 module (RS485 ↔ TTL converter):
  MAX3485 A/B  → RS485 bus to NPK sensor
  MAX3485 RO   → ESP32 GPIO 16
  MAX3485 DI   → ESP32 GPIO 17
  MAX3485 DE   → ESP32 GPIO 5
  MAX3485 RE   → ESP32 GPIO 5  (tie DE+RE together)

DHT22:
  DATA pin → ESP32 GPIO 4 (with 10kΩ pull-up resistor to 3.3V)
  VCC      → 3.3V
  GND      → GND

Capacitive soil moisture sensor:
  AOUT → ESP32 GPIO 34
  VCC  → 3.3V
  GND  → GND
```

---

## STEP 4 — Cloud Function

### Install Firebase CLI
```bash
npm install -g firebase-tools
firebase login
```

### Deploy
```bash
cd cloud_functions
npm install
firebase use --add    # select your project
firebase deploy --only functions
```

The function `fuseAndRecommend` will now auto-trigger every time the ESP32 uploads a reading. It fetches satellite data for the same field and writes recommendations to `/recommendations/{field_id}/latest`.

---

## STEP 5 — Website

The website is a single HTML file — no build step needed.

### Option A: Open locally
Just double-click `website/index.html` in Finder. Works for testing.

### Option B: Host on Firebase Hosting (free)
```bash
cd website
firebase init hosting    # public dir = "." or current folder
firebase deploy --only hosting
```
Your site will be live at `https://your-project.web.app`

### Using the website
1. Open the site
2. If first time, a config banner asks for your Firebase URL + Field ID
3. Enter them and click **Connect**
4. Dashboard updates in real-time as rover sends data

---

## Firebase Database Structure

```
/
├── rover/
│   └── field_hyderabad_01/
│       ├── latest/               ← most recent rover reading
│       │   ├── timestamp
│       │   ├── npk_n, npk_p, npk_k
│       │   ├── ph
│       │   ├── soil_moisture
│       │   ├── air_temp, humidity
│       │   └── lat, lon
│       └── readings/
│           └── 2025-01-15T10:30:00Z/   ← one node per reading
│
├── satellite/
│   └── field_hyderabad_01/
│       ├── latest/               ← most recent satellite analysis
│       │   ├── meta/
│       │   └── indices/
│       │       ├── NDVI: {mean, min, max, std}
│       │       ├── EVI:  {mean, min, max, std}
│       │       └── ...
│       └── 2025-01-15/           ← per-date satellite snapshot
│
├── fused/
│   └── field_hyderabad_01/
│       ├── latest/               ← latest fused record
│       └── history/
│
└── recommendations/
    └── field_hyderabad_01/
        └── latest/
            ├── timestamp
            └── items: [ {category, severity, message, action}, ... ]
```

---

## Calibration Notes

### Soil moisture sensor
The capacitive sensor needs calibration for your specific soil type:
1. Read ADC value when sensor is completely dry → set `DRY_VAL`
2. Read ADC value when sensor is in water → set `WET_VAL`
3. Update these values in the firmware

### NPK sensor baud rate
Most RS485 NPK sensors use 4800 baud. If yours uses a different rate, change:
```cpp
Serial2.begin(4800, ...)
```

### Upload interval
Default is 60 seconds. For field testing, you can reduce to 10s:
```cpp
#define UPLOAD_INTERVAL_MS 10000
```
