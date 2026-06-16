/*
  ROVER SENSOR FIRMWARE
  =====================
  ESP32 — reads NPK, pH, soil moisture, air temp/humidity sensors
  and uploads readings to Firebase Realtime Database over WiFi (mobile hotspot).

  HARDWARE:
    - ESP32 DevKit (any variant)
    - NPK + pH sensor  → RS485 via MAX3485 module → GPIO 16 (RX2), 17 (TX2)
    - Soil moisture    → Capacitive sensor analog out → GPIO 34
    - DHT22            → Air temp + humidity         → GPIO 4
    - GPS module       → UART → GPIO 26 (RX), 27 (TX)   [optional]
    - DE/RE pins MAX3485 → GPIO 5

  LIBRARIES (install via Arduino Library Manager):
    - Firebase ESP Client  by Mobizt     v4.x
    - DHT sensor library   by Adafruit
    - TinyGPSPlus          by Mikal Hart  [optional]
    - ArduinoJson          by Benoit Blanchon

  WIRING DIAGRAM:
    MAX3485 A/B → RS485 bus to NPK+pH sensor
    MAX3485 RO  → ESP32 GPIO 16
    MAX3485 DI  → ESP32 GPIO 17
    MAX3485 DE+RE → ESP32 GPIO 5
    DHT22 DATA  → ESP32 GPIO 4 (with 10kΩ pull-up to 3.3V)
    Capacitive moisture → GPIO 34 (ADC1)
    GPS TX      → ESP32 GPIO 26
    GPS RX      → ESP32 GPIO 27
*/

#include <Arduino.h>
#include <WiFi.h>
#include <Firebase_ESP_Client.h>
#include <addons/TokenHelper.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include <time.h>

// ── USER CONFIG ───────────────────────────────────────────────────────────────
#define WIFI_SSID        "YourHotspotName"
#define WIFI_PASSWORD    "YourHotspotPassword"

// Firebase — from Firebase Console → Project Settings → Your apps → SDK config
#define FIREBASE_API_KEY      "YOUR_FIREBASE_WEB_API_KEY"
#define FIREBASE_DATABASE_URL "https://your-project-default-rtdb.firebaseio.com"
#define FIREBASE_USER_EMAIL   "rover@yourproject.com"   // create in Firebase Auth
#define FIREBASE_USER_PASS    "RoverPassword123"

// Field identifier — must match what you set in the Streamlit app
#define FIELD_ID "field_01"

// Upload interval (milliseconds) — 60 seconds
#define UPLOAD_INTERVAL_MS 60000
// ─────────────────────────────────────────────────────────────────────────────

// ── PIN DEFINITIONS ───────────────────────────────────────────────────────────
#define RS485_RX_PIN    16
#define RS485_TX_PIN    17
#define RS485_DE_RE_PIN  5
#define DHT_PIN          4
#define MOISTURE_PIN    34
// ─────────────────────────────────────────────────────────────────────────────

// ── NPK/pH SENSOR RS485 COMMANDS (Modbus RTU) ────────────────────────────────
// Most common 7-in-1 soil sensor uses these command bytes
uint8_t CMD_NPK[]  = {0x01, 0x03, 0x00, 0x1E, 0x00, 0x03, 0x65, 0xCD};
uint8_t CMD_PH[]   = {0x01, 0x03, 0x00, 0x06, 0x00, 0x01, 0x64, 0x0B};
uint8_t CMD_MOIST[]= {0x01, 0x03, 0x00, 0x12, 0x00, 0x01, 0x24, 0x0F};
// ─────────────────────────────────────────────────────────────────────────────

FirebaseData   fbdo;
FirebaseAuth   auth;
FirebaseConfig config;
DHT            dht(DHT_PIN, DHT22);

float  npk_n = 0, npk_p = 0, npk_k = 0;
float  ph = 0;
float  soil_moisture_pct = 0;
float  air_temp = 0, humidity = 0;
double gps_lat = 0, gps_lon = 0;

unsigned long lastUpload = 0;
bool firebaseReady = false;


// ── RS485 HELPERS ─────────────────────────────────────────────────────────────
void rs485Send(uint8_t* cmd, size_t len) {
    digitalWrite(RS485_DE_RE_PIN, HIGH);  // transmit mode
    delayMicroseconds(100);
    Serial2.write(cmd, len);
    Serial2.flush();
    delayMicroseconds(100);
    digitalWrite(RS485_DE_RE_PIN, LOW);   // receive mode
}

bool rs485Receive(uint8_t* buf, size_t expected, uint32_t timeout_ms = 500) {
    uint32_t start = millis();
    size_t   idx   = 0;
    while (millis() - start < timeout_ms && idx < expected) {
        if (Serial2.available()) {
            buf[idx++] = Serial2.read();
        }
    }
    return idx >= expected;
}


// ── SENSOR READING FUNCTIONS ──────────────────────────────────────────────────
bool readNPK() {
    uint8_t response[11] = {0};
    rs485Send(CMD_NPK, sizeof(CMD_NPK));
    delay(200);
    if (!rs485Receive(response, 11)) return false;

    npk_n = ((response[3] << 8) | response[4]);
    npk_p = ((response[5] << 8) | response[6]);
    npk_k = ((response[7] << 8) | response[8]);
    return true;
}

bool readPH() {
    uint8_t response[7] = {0};
    rs485Send(CMD_PH, sizeof(CMD_PH));
    delay(200);
    if (!rs485Receive(response, 7)) return false;

    ph = ((response[3] << 8) | response[4]) / 10.0f;
    return true;
}

bool readSoilMoisture() {
    // Capacitive sensor: 0V = dry (~4095 ADC), 3.3V = wet (~1200 ADC)
    int raw = analogRead(MOISTURE_PIN);
    // Calibrate these values for your specific sensor
    const int DRY_VAL = 3200;
    const int WET_VAL = 1400;
    soil_moisture_pct = constrain(
        map(raw, DRY_VAL, WET_VAL, 0, 100), 0, 100
    );
    return true;
}

bool readDHT() {
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (isnan(h) || isnan(t)) return false;
    humidity = h;
    air_temp = t;
    return true;
}


// ── FIREBASE UPLOAD ───────────────────────────────────────────────────────────
String getISOTimestamp() {
    struct tm timeinfo;
    if (!getLocalTime(&timeinfo)) return "1970-01-01T00:00:00Z";
    char buf[30];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(buf);
}

void uploadToFirebase() {
    if (!firebaseReady || !Firebase.ready()) {
        Serial.println("[Firebase] Not ready, skipping upload");
        return;
    }

    String timestamp = getISOTimestamp();
    // Use timestamp as key so readings stack up (newest first in sorted queries)
    String path = "/rover/" + String(FIELD_ID) + "/readings/" + timestamp;

    FirebaseJson json;
    json.set("timestamp",     timestamp);
    json.set("npk_n",         npk_n);
    json.set("npk_p",         npk_p);
    json.set("npk_k",         npk_k);
    json.set("ph",            ph);
    json.set("soil_moisture", soil_moisture_pct);
    json.set("air_temp",      air_temp);
    json.set("humidity",      humidity);
    json.set("lat",           gps_lat);
    json.set("lon",           gps_lon);
    json.set("field_id",      FIELD_ID);

    if (Firebase.RTDB.setJSON(&fbdo, path.c_str(), &json)) {
        Serial.println("[Firebase] ✅ Upload success: " + path);
    } else {
        Serial.println("[Firebase] ❌ Upload failed: " + fbdo.errorReason());
    }

    // Also update /rover/{field}/latest for fast dashboard reads
    String latest_path = "/rover/" + String(FIELD_ID) + "/latest";
    Firebase.RTDB.setJSON(&fbdo, latest_path.c_str(), &json);
}


// ── SETUP ─────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Serial2.begin(4800, SERIAL_8N1, RS485_RX_PIN, RS485_TX_PIN);
    pinMode(RS485_DE_RE_PIN, OUTPUT);
    digitalWrite(RS485_DE_RE_PIN, LOW);

    dht.begin();
    analogReadResolution(12);

    // WiFi
    Serial.print("[WiFi] Connecting to " + String(WIFI_SSID));
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    int retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 30) {
        delay(500); Serial.print("."); retries++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[WiFi] ✅ Connected: " + WiFi.localIP().toString());
        // NTP time sync (needed for timestamps)
        configTime(19800, 0, "pool.ntp.org");  // IST = UTC+5:30 = 19800s
    } else {
        Serial.println("\n[WiFi] ⚠️ Not connected — will retry each loop");
    }

    // Firebase
    config.api_key           = FIREBASE_API_KEY;
    config.database_url      = FIREBASE_DATABASE_URL;
    config.token_status_callback = tokenStatusCallback;

    auth.user.email    = FIREBASE_USER_EMAIL;
    auth.user.password = FIREBASE_USER_PASS;

    Firebase.begin(&config, &auth);
    Firebase.reconnectWiFi(true);
    firebaseReady = true;

    Serial.println("[System] ✅ Setup complete — reading sensors every " +
                   String(UPLOAD_INTERVAL_MS / 1000) + "s");
}


// ── LOOP ──────────────────────────────────────────────────────────────────────
void loop() {
    unsigned long now = millis();

    if (now - lastUpload >= UPLOAD_INTERVAL_MS) {
        lastUpload = now;

        Serial.println("\n[Sensors] Reading...");

        bool npk_ok   = readNPK();
        bool ph_ok    = readPH();
        bool moist_ok = readSoilMoisture();
        bool dht_ok   = readDHT();

        Serial.printf("  NPK  : N=%.0f  P=%.0f  K=%.0f  [%s]\n",
                      npk_n, npk_p, npk_k, npk_ok ? "OK" : "FAIL");
        Serial.printf("  pH   : %.1f  [%s]\n", ph, ph_ok ? "OK" : "FAIL");
        Serial.printf("  Moist: %.1f%%  [%s]\n", soil_moisture_pct, moist_ok ? "OK" : "FAIL");
        Serial.printf("  Temp : %.1f°C  Hum: %.1f%%  [%s]\n",
                      air_temp, humidity, dht_ok ? "OK" : "FAIL");

        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("[WiFi] Reconnecting...");
            WiFi.reconnect();
            delay(3000);
        }

        uploadToFirebase();
    }

    // Firebase token refresh
    Firebase.ready();
    delay(10);
}
