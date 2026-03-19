# ⚡ How ScreenSense AI Works

---

## 🔄 Complete Workflow

```
User signs in on index.html (Firebase Auth)
            ↓
Firebase returns unique UID
            ↓
User connects Android phone via USB cable
            ↓
Click "Detect Device" → Flask runs "adb devices"
            ↓
Device serial number detected & stored
            ↓
Click "Start Analysis" → POST /api/trigger/<uid>
            ↓
Flask starts background thread → live_collector()
            ↓
ADB command runs on phone:
"adb shell dumpsys usagestats --interval DAILY"
            ↓
Raw app usage logs extracted from Android
            ↓
parse_app_usage_from_dump() parses logs
            ↓
┌─────────────────────────────────────┐
│         Data Extracted              │
│  • App names & usage minutes        │
│  • Hourly distribution (24hrs)      │
│  • Unlock count                     │
│  • Social media %                   │
│  • Weekly & Monthly trends          │
└─────────────────────────────────────┘
            ↓
ml_risk_score() computes weighted score
            ↓
┌─────────────────────────────────────┐
│         5 Behavioral Factors        │
│  • Screen Time      → 35%           │
│  • Social Media %   → 25%           │
│  • Unlock Frequency → 20%           │
│  • Night Usage      → 10%           │
│  • App Concentration→ 10%           │
└─────────────────────────────────────┘
            ↓
Risk Score (0–100) computed
            ↓
Classified → Minimal / Low / Moderate / High
            ↓
compute_forecast() generates 7-day prediction
            ↓
_save_to_firestore() stores all data
            ↓
collection_status = "done"
            ↓
Frontend polling detects "done"
            ↓
Auto-redirect → dashboard.html
            ↓
Dashboard fetches data → GET /api/user/<uid>
            ↓
Groq API called → LLaMA 3.3 70B generates insights
            ↓
┌─────────────────────────────────────┐
│        Dashboard Renders            │
│  • Hourly Line Chart                │
│  • App Doughnut Chart               │
│  • Weekly Bar Chart                 │
│  • Addiction Risk Gauge             │
│  • AI Insights (Groq)               │
│  • 7-Day Usage Forecast             │
│  • Goals & Risk Report              │
└─────────────────────────────────────┘
```

---

## 🌐 Chrome Extension Workflow

```
User installs Chrome Extension
            ↓
User signs in on index.html
            ↓
Firebase UID sent to extension via:
chrome.runtime.sendMessage({ type: "SET_UID", userId: uid })
            ↓
Extension stores UID in chrome.storage.local
            ↓
User browses any website
            ↓
chrome.tabs.onUpdated fires on tab complete
            ↓
URL + domain + timestamp captured
            ↓
POST /api/web_usage → Flask
            ↓
Stored in Firestore "web_usage" collection
            ↓
Dashboard → History tab fetches GET /api/history/<uid>
            ↓
Groq AI analyzes domains → GET /api/history_insights/<uid>
            ↓
Browsing insights rendered with category breakdown
```

---

## 🔔 Alert Workflow

```
User clicks "Alert" button on any app in dashboard
            ↓
POST /api/send_alert/<uid>
            ↓
Flask detects connected device serial via ADB
            ↓
Method 1: cmd notification post (Android 11+)
            ↓
Method 2: am startactivity broadcast (Android 8+)
            ↓
Method 3: termux-notification (if Termux installed)
            ↓
Method 4: KEYCODE_WAKEUP + notification post
            ↓
Phone screen wakes → notification appears
            ↓
Alert logged to Firestore "alerts" collection
```

---

## 🔁 Auto Refresh Workflow

```
Dashboard loads → applySettings()
            ↓
autoRefreshTimer starts (every 30 seconds)
            ↓
fetchAndRender() called → GET /api/user/<uid>
            ↓
Flask checks results_store (in-memory cache)
            ↓
If empty → Firestore fallback
            ↓
Dashboard updates charts & stats silently
            ↓
Background ADB thread also refreshes every 60s
            ↓
New data saved to Firestore automatically
```

---

## 🗄️ Data Storage Structure (Firestore)

```
Firestore
│
├── users/
│   └── <uid>/
│       ├── screenTimeData {}     ← today's full data
│       ├── week []               ← last 7 days
│       ├── month []              ← last 30 days
│       └── lastSync              ← timestamp
│
├── web_usage/
│   └── <doc>/
│       ├── userId
│       ├── url
│       ├── domain
│       └── timestamp
│
└── alerts/
    └── <doc>/
        ├── userId
        ├── title
        ├── body
        ├── app_name
        ├── risk_level
        └── sent_at
```

---

## 🔌 API Flow Summary

```
Frontend          Flask Backend         External
   │                    │                  │
   │─ POST /trigger ───▶│                  │
   │                    │─ adb shell ─────▶│ Android Device
   │                    │◀─ raw logs ──────│
   │◀─ {status:start} ──│                  │
   │                    │                  │
   │─ GET /status ──────▶ (polling x N)    │
   │◀─ {status:done} ───│                  │
   │                    │                  │
   │─ GET /user ────────▶│                  │
   │◀─ screenTimeData ──│                  │
   │                    │                  │
   │─ GET /ai_insights ─▶│                  │
   │                    │─ Groq prompt ───▶│ Groq API
   │                    │◀─ JSON insights ─│
   │◀─ insights ────────│                  │
```