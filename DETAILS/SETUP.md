# ⚙️ ScreenSense AI — Setup Guide

Complete step-by-step installation and configuration guide.

---

## 📋 Requirements

| Requirement | Version |
|---|---|
| Python | 3.8 or above |
| pip | Latest |
| Node.js | Not required |
| ADB (Platform Tools) | Latest |
| Chrome Browser | Any recent version |
| Android Phone | Android 8.0+ |
| Firebase Account | Free tier works |
| Groq Account | Free tier works |

---

## 🪟 Step 1 — Install ADB (Android Debug Bridge)

### Windows
1. Download **Android Platform Tools** from:
   ```
   https://developer.android.com/tools/releases/platform-tools
   ```
2. Extract the zip to `C:\platform-tools\`
3. Add to PATH:
   ```
   Search → "Environment Variables" → System Variables → Path → New
   → Add: C:\platform-tools
   ```
4. Verify in terminal:
   ```bash
   adb version
   ```
   Should show: `Android Debug Bridge version 1.x.x`

### Mac/Linux
```bash
# Mac
brew install android-platform-tools

# Linux (Ubuntu)
sudo apt install adb
```

---

## 📱 Step 2 — Enable USB Debugging on Android

```
1. Settings → About Phone
2. Tap "Build Number" 7 times rapidly
3. Go back → Developer Options (now visible)
4. Enable "USB Debugging"
5. Connect phone via USB cable
6. Tap "ALLOW" on the popup that appears on phone
```

Verify connection:
```bash
adb devices
```
Should show:
```
List of devices attached
XXXXXXXX    device
```

---

## 🐍 Step 3 — Install Python Dependencies

```bash
pip install flask
pip install flask-cors
pip install firebase-admin
pip install groq
```

Or install all at once:
```bash
pip install flask flask-cors firebase-admin groq
```

---

## 🔥 Step 4 — Firebase Setup

### 4.1 Create Firebase Project
1. Go to [https://console.firebase.google.com](https://console.firebase.google.com)
2. Click **Add Project** → Enter name → Continue
3. Disable Google Analytics (optional) → Create Project

### 4.2 Enable Authentication
```
Firebase Console → Authentication → Get Started
→ Sign-in method → Enable:
   ✅ Email/Password
   ✅ Google
```

### 4.3 Enable Firestore
```
Firebase Console → Firestore Database → Create Database
→ Start in Test Mode → Select region → Enable
```

### 4.4 Download Service Account Key
```
Firebase Console → Project Settings (gear icon)
→ Service Accounts → Generate New Private Key
→ Download JSON file
→ Rename to: serviceAccountKey.json
→ Place in project root folder (same level as app.py)
```

### 4.5 Get Firebase Web Config
```
Firebase Console → Project Settings → General
→ Your Apps → Add App → Web (</>)
→ Copy the firebaseConfig object
→ Paste into index.html and dashboard.html (replace existing cfg{})
```

---

## 🤖 Step 5 — Groq API Setup

1. Go to [https://console.groq.com](https://console.groq.com)
2. Sign up for a free account
3. Go to **API Keys** → Create API Key
4. Copy the key
5. Open `app.py` and replace:
   ```python
   GROQ_API_KEY = "your_groq_api_key_here"
   ```

---

## 🔧 Step 6 — Configure Backend URL

In `index.html` and `dashboard.html`, update the Flask server URL:

```javascript
// If running locally
const F = 'http://127.0.0.1:5000';

// If running on same WiFi network (for phone access)
const F = 'http://YOUR_PC_IP:5000';
```

Find your PC IP:
```bash
# Windows
ipconfig
# Look for: IPv4 Address → e.g. 192.168.1.12

# Mac/Linux
ifconfig | grep inet
```

---

## 🚀 Step 7 — Run the Flask Backend

```bash
# Navigate to project folder
cd screensense-ai

# Run the server
python app.py
```

You should see:
```
* Running on http://0.0.0.0:5000
* Debug mode: on
```

---

## 🌐 Step 8 — Open the Frontend

### Option 1 — VS Code Live Server (Recommended)
1. Install **Live Server** extension in VS Code
2. Right click `index.html` → **Open with Live Server**
3. Opens at `http://127.0.0.1:5500`

### Option 2 — Direct Browser
1. Open `index.html` directly in Chrome
2. Note: Some features may not work due to CORS on `file://` protocol

---

## 🧩 Step 9 — Install Chrome Extension

1. Open Chrome → go to:
   ```
   chrome://extensions
   ```
2. Enable **Developer Mode** (top right toggle)
3. Click **Load Unpacked**
4. Select the `extension/` folder from your project
5. Extension should appear as **ScreenSense AI**
6. Pin it to toolbar (optional)

---

## ✅ Step 10 — Full System Test

1. Open `index.html` in Chrome
2. Sign in with Google or Email
3. Connect Android phone via USB
4. Click **Detect Device** — phone model should appear
5. Click **Start Analysis**
6. Watch progress steps complete
7. Dashboard opens automatically
8. Browse some websites → check History tab in dashboard

---

## 🛠️ Troubleshooting

### ADB not found
```
❌ 'adb' is not recognized as an internal or external command
```
**Fix:** Add `C:\platform-tools` to Windows PATH and restart terminal

---

### No device detected
```
❌ No device connected
```
**Fix checklist:**
- USB Debugging enabled on phone ✅
- Tapped ALLOW on phone popup ✅
- Data cable (not charge-only) ✅
- Run `adb devices` in terminal to verify ✅

---

### Flask server offline
```
❌ Backend offline — run: py app.py
```
**Fix:** Open terminal in project folder and run `python app.py`

---

### Firestore permission denied
```
❌ PERMISSION_DENIED: Missing or insufficient permissions
```
**Fix:** Go to Firestore → Rules → set to:
```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if true;
    }
  }
}
```
(For development only — restrict in production)

---

### Groq API error
```
❌ Could not load AI insights
```
**Fix:** Check your `GROQ_API_KEY` in `app.py` is valid and not expired

---

### Chrome extension not sending data
```
❌ No userId found, skipping...
```
**Fix:** Sign in on `index.html` first — the UID is sent to the extension on login

---

## 📁 Final Folder Structure

```
screensense-ai/
│
├── index.html
├── app.py
├── serviceAccountKey.json        ← DO NOT commit to GitHub
├── html/
│   └── dashboard.html
├── css/
│   ├── base.css
│   ├── dashboard.css
│   └── index.css
├── assets/
│   ├── srm.jpg
│   └── srm logo.jpg
├── extension/
│   ├── manifest.json
│   └── background.js
├── README.md
└── SETUP.md
```

---

## 🔒 Important — Before Pushing to GitHub

Add a `.gitignore` file:
```
serviceAccountKey.json
__pycache__/
*.pyc
.env
```

Never commit your `serviceAccountKey.json` or `GROQ_API_KEY` to a public repository.

---

**Setup complete! ScreenSense AI is ready to use. 🚀**