import subprocess
import json
import time
import datetime
import threading
import random
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from collections import defaultdict
from google.cloud.firestore_v1.base_query import FieldFilter
import math
from groq import Groq
import os
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Initialize Firebase
cred = credentials.Certificate('../serviceAccountKey.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

collection_status = {}
# results_store[uid] = { "today": {...}, "week": [...], "month": [...] }
results_store = {}
live_threads  = {}

SOCIAL_KW = ["instagram","facebook","twitter","tiktok","snapchat",
             "whatsapp","telegram","linkedin","reddit","pinterest","youtube"]

# ─────────────────────────────────────────
# HELPER: Run ADB Command
# ─────────────────────────────────────────
def run_adb(cmd):
    try:
        output = subprocess.check_output(
            ["adb"] + cmd,
            stderr=subprocess.STDOUT,
            timeout=20
        ).decode("utf-8", errors="ignore")
        return output
    except Exception as e:
        return str(e)

# ─────────────────────────────────────────
# HELPER: Clean package name → readable label
# ─────────────────────────────────────────
def clean_app_name(package_name: str) -> str:
    if not package_name:
        return "Unknown"
    parts = package_name.split(".")
    last  = parts[-1]
    last  = last.replace("_", " ")
    last  = re.sub(r'([a-z])([A-Z])', r'\1 \2', last)
    return last.strip().capitalize()

# ─────────────────────────────────────────
# HELPER: Social media % calculation
# ─────────────────────────────────────────
def calc_social_pct(apps, total_minutes):
    if total_minutes <= 0:
        return 0.0
    social_mins = sum(
        a.get("minutes", a.get("total_time_min", 0)) for a in apps
        if any(kw in (a.get("app_name", a.get("appName", ""))).lower() for kw in SOCIAL_KW)
    )
    return round(social_mins / total_minutes * 100, 1)

# ─────────────────────────────────────────
# HELPER: Parse multi-line usagestats dump
# Handles both single-line and multi-line block formats from
# `dumpsys usagestats --interval DAILY/WEEKLY/MONTHLY`
# ─────────────────────────────────────────
def parse_app_usage_from_dump(raw: str) -> list:
    """
    Handles ALL known Android usagestats dump formats:
      1. Multi-line block:  package=X \n totalTimeInForeground=Y
      2. Single-line:       package=X ... totalTimeInForeground=Y
      3. Indented block:    com.pkg.name \n   totalTimeInForeground=Y
      4. Compact p= format: p=com.pkg t=Y
      5. Bare pkg line:     com.pkg.name: foregroundTime=Y
    """
    apps        = {}   # pkg → ms
    current_pkg = None

    lines = raw.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # ── Format 4: compact p= t= ──────────────────────────────
        if stripped.startswith("p=") and "t=" in stripped:
            try:
                parts   = stripped.split()
                pkg     = next(p for p in parts if p.startswith("p=")).split("=",1)[1]
                ms_str  = next(p for p in parts if p.startswith("t=")).split("=",1)[1]
                ms      = int(ms_str)
                if ms > 60_000:
                    apps[pkg] = max(apps.get(pkg, 0), ms)
            except Exception:
                pass
            current_pkg = None
            continue

        # ── Format 5: pkg: foregroundTime=Y ─────────────────────
        if "foregroundTime=" in stripped and ":" in stripped:
            try:
                pkg  = stripped.split(":")[0].strip()
                ms   = int(stripped.split("foregroundTime=")[1].split()[0])
                if ms > 60_000 and "." in pkg:
                    apps[pkg] = max(apps.get(pkg, 0), ms)
            except Exception:
                pass
            current_pkg = None
            continue

        # ── Format 1 & 2: package= lines ────────────────────────
        if "package=" in stripped:
            try:
                pkg = stripped.split("package=")[1].split()[0].strip()
                # Single-line: also has totalTimeInForeground=
                if "totalTimeInForeground=" in stripped:
                    ms = int(stripped.split("totalTimeInForeground=")[1].split()[0])
                    if ms > 60_000:
                        apps[pkg] = max(apps.get(pkg, 0), ms)
                    current_pkg = None
                else:
                    # Multi-line block: remember pkg, read ms on next lines
                    current_pkg = pkg
                    if pkg not in apps:
                        apps[pkg] = 0
            except Exception:
                current_pkg = None
            continue

        # ── Format 1 continuation: totalTimeInForeground= alone ─
        if "totalTimeInForeground=" in stripped and current_pkg:
            try:
                ms = int(stripped.split("totalTimeInForeground=")[1].split()[0])
                if ms > apps.get(current_pkg, 0):
                    apps[current_pkg] = ms
            except Exception:
                pass
            continue

        # ── Format 3: bare package name line (com.x.y with no =) ─
        # Looks like: "  com.instagram.android" followed by indented fields
        if (
            not stripped.startswith("#")
            and "=" not in stripped
            and "." in stripped
            and len(stripped.split(".")) >= 2
            and all(c.isalnum() or c in "._-" for c in stripped)
            and not stripped[0].isdigit()
        ):
            candidate = stripped
            # Peek ahead up to 8 lines for totalTimeInForeground
            for j in range(i + 1, min(i + 8, len(lines))):
                nxt = lines[j].strip()
                if "totalTimeInForeground=" in nxt:
                    try:
                        ms = int(nxt.split("totalTimeInForeground=")[1].split()[0])
                        if ms > 60_000:
                            apps[candidate] = max(apps.get(candidate, 0), ms)
                    except Exception:
                        pass
                    break
                if nxt and not nxt.startswith(" ") and "=" not in nxt and "." not in nxt:
                    break  # new section, stop peeking
            current_pkg = candidate
            continue

        # ── Reset block tracker on section headers ───────────────
        if stripped.startswith("User ") or stripped.startswith("In-memory"):
            current_pkg = None

    # ── Build result list ────────────────────────────────────────
    result = []
    for pkg, ms in apps.items():
        if ms < 60_000:
            continue
        # Filter out system noise
        if any(skip in pkg for skip in [
            "android.server", "systemui", "inputmethod",
            "provision", "permissioncontroller", "com.android.settings"
        ]):
            continue
        result.append({
            "app_name":     pkg,
            "clean_name":   clean_app_name(pkg),
            "milliseconds": ms,
            "minutes":      round(ms / 60_000, 2),
            "risk_level":   "pending"
        })

    return sorted(result, key=lambda x: x["minutes"], reverse=True)
@app.route("/api/debug_dump/<uid>")
def debug_dump(uid):
    """Call this from browser to inspect raw ADB output"""
    try:
        raw = run_adb(["shell", "dumpsys", "usagestats", "--interval", "DAILY"])
        parsed = parse_app_usage_from_dump(raw)
        return jsonify({
            "raw_preview":   raw[:3000],
            "parsed_count":  len(parsed),
            "parsed_top5":   parsed[:5],
            "line_count":    len(raw.split("\n"))
        })
    except Exception as e:
        return jsonify({"error": str(e)})
# ─────────────────────────────────────────
# HELPER: Hourly distribution (today)
# Returns {"0": mins, "1": mins, …}
# Uses --interval DAILY for full-day history
# ─────────────────────────────────────────
def get_hourly_distribution(serial):
    hourly   = {}
    fg_times = {}
    try:
        raw = run_adb(["-s", serial, "shell", "dumpsys", "usagestats", "--interval", "DAILY"])
        for line in raw.split("\n"):
            line = line.strip()
            if "MOVE_TO_FOREGROUND" not in line and "MOVE_TO_BACKGROUND" not in line:
                continue
            try:
                parts    = line.split()
                ts_part  = next((p for p in parts if p.startswith("time=")),    None)
                pkg_part = next((p for p in parts if p.startswith("package=")), None)
                if not ts_part or not pkg_part:
                    continue
                ts_raw = ts_part.split("=", 1)[1]
                if ":" in ts_raw:
                    h, m, s = ts_raw.split(":")
                    ts_ms = (int(h) * 3600 + int(m) * 60 + int(s)) * 1000
                else:
                    ts_ms = int(ts_raw)
                pkg  = pkg_part.split("=", 1)[1]
                hour = str((ts_ms // 3_600_000) % 24)
                if "MOVE_TO_FOREGROUND" in line:
                    fg_times[pkg] = ts_ms
                elif "MOVE_TO_BACKGROUND" in line and pkg in fg_times:
                    duration_ms = ts_ms - fg_times.pop(pkg)
                    if 0 < duration_ms < 7_200_000:
                        hourly[hour] = round(hourly.get(hour, 0) + duration_ms / 60_000, 2)
            except Exception:
                continue
    except Exception:
        pass
    return hourly

# ─────────────────────────────────────────
# HELPER: Unlock count
# ─────────────────────────────────────────
def get_unlock_count(serial):
    try:
        raw   = run_adb(["-s", serial, "shell", "dumpsys", "usagestats", "--interval", "DAILY"])
        count = raw.count("KEYGUARD_HIDDEN") + raw.count("SCREEN_INTERACTIVE")
        return max(count, 0)
    except Exception:
        return 0

# ─────────────────────────────────────────
# HELPER: Parse daily totals from a usagestats dump
# ─────────────────────────────────────────
def _parse_day_buckets(raw: str) -> dict:
    """
    Builds daily totals from MOVE_TO_FOREGROUND/BACKGROUND events.
    ONLY uses epoch-timestamp lines (skips HH:MM:SS — those can't be date-attributed).
    This avoids cumulative totalTimeInForeground inflation entirely.
    """
    day_buckets = defaultdict(float)  # date_str → total minutes
    fg_times    = {}                  # pkg → (ts_ms, date_str)

    for line in raw.split("\n"):
        line = line.strip()
        if "MOVE_TO_FOREGROUND" not in line and "MOVE_TO_BACKGROUND" not in line:
            continue
        try:
            parts    = line.split()
            ts_part  = next((p for p in parts if p.startswith("time=")), None)
            pkg_part = next((p for p in parts if p.startswith("package=")), None)
            if not ts_part or not pkg_part:
                continue

            ts_raw = ts_part.split("=", 1)[1]

            # Skip HH:MM:SS — can't determine date, causes cross-day contamination
            if ":" in ts_raw:
                continue

            ts_ms = int(ts_raw)
            # Sanity check: reject obviously bogus timestamps
            # (must be within last 365 days and not in the future)
            now_ms = time.time() * 1000
            if ts_ms < (now_ms - 365 * 86400 * 1000) or ts_ms > now_ms + 86400_000:
                continue

            dt       = datetime.datetime.fromtimestamp(ts_ms / 1000)
            pkg      = pkg_part.split("=", 1)[1]
            date_str = dt.strftime("%Y-%m-%d")

            if "MOVE_TO_FOREGROUND" in line:
                fg_times[pkg] = (ts_ms, date_str)

            elif "MOVE_TO_BACKGROUND" in line and pkg in fg_times:
                fg_ts, fg_date = fg_times.pop(pkg)
                duration_ms    = ts_ms - fg_ts
                # Sanity: session must be 5 seconds–2 hours
                if 5_000 < duration_ms < 7_200_000:
                    day_buckets[fg_date] += duration_ms / 60_000

        except Exception:
            continue

    return dict(day_buckets)# ─────────────────────────────────────────
# HELPER: Weekly daily totals (last 7 days)
# ─────────────────────────────────────────
def get_usage_via_shell(serial, days=30):
    """
    Uses 'cmd appops' + date-range queryUsageStats via ADB shell
    to get per-day screen time. Works on MIUI/restricted ROMs.
    """
    today     = datetime.date.today()
    now_ms    = int(time.time() * 1000)
    start_ms  = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp() * 1000)

    # Try querying via the usage-stats service directly
    cmd = (
        f"dumpsys usagestats --current-user "
        f"| grep -E 'package=|totalTimeInForeground=|lastTimeUsed='"
    )
    raw = run_adb(["-s", serial, "shell", cmd])

    day_buckets = defaultdict(float)  # date_str → hours

    # Parse what we can from broadcast event relative timestamps
    # Format: +30d4h2m45s521ms = days ago relative to now
    pkg_times = {}  # pkg → total_ms estimated from broadcast events

    raw_full = run_adb(["-s", serial, "shell", "dumpsys", "usagestats"])

    # Parse relative time offsets like "+30d4h2m45s"
    # These appear in broadcast events and give us a rough usage signal
    import re
    pattern = re.compile(
        r'tgtPkg=([\w.]+).*?\+(\d+)d(\d+)h(\d+)m(\d+)s',
        re.DOTALL
    )

    for match in pattern.finditer(raw_full):
        pkg       = match.group(1)
        days_ago  = int(match.group(2))
        hrs       = int(match.group(3))
        mins      = int(match.group(4))

        event_date = (today - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d")
        # Each broadcast event = app was active, estimate ~session from context
        # We can't get exact duration from broadcast events alone
        pkg_times[event_date] = pkg_times.get(event_date, set())
        pkg_times[event_date].add(pkg)

    return day_buckets, pkg_times


def get_screen_time_from_agent(serial, uid):
    """
    Query the ScreenSense agent app on device for accurate usage data.
    The agent has UsageStatsManager permission and can report accurately.
    """
    try:
        # Ask the agent app to dump its collected data via broadcast
        result = run_adb([
            "-s", serial, "shell",
            "am", "broadcast", "-a", "com.example.screensenseagent.GET_STATS",
            "--receiver-foreground"
        ])
        return result
    except Exception:
        return ""


def get_weekly_data(serial, uid=None):
    today   = datetime.date.today()

    # ── Strategy 1: Pull from dailyHistory subcollection in Firestore ──
    if uid:
        try:
            history_docs = (
                db.collection("users").document(uid)
                  .collection("dailyHistory")
                  .order_by("date", direction=firestore.Query.DESCENDING)
                  .limit(7)
                  .stream()
            )
            firestore_history = {doc.to_dict()["date"]: doc.to_dict()["total_hours"]
                                 for doc in history_docs
                                 if doc.to_dict().get("date")}
            if len(firestore_history) >= 1:
                result = []
                for i in range(6, -1, -1):
                    d   = today - datetime.timedelta(days=i)
                    key = d.strftime("%Y-%m-%d")
                    result.append({
                        "date":          key,
                        "day":           d.strftime("%a"),
                        "total_minutes": round(firestore_history.get(key, 0) * 60, 2),
                        "total_hours":   round(firestore_history.get(key, 0), 2)
                    })
                return result
        except Exception as e:
            print(f"[ScreenSense] dailyHistory fetch error: {e}")

    # ── Strategy 2: All zeros (history builds up day by day) ──
    result = []
    for i in range(6, -1, -1):
        d   = today - datetime.timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        result.append({
            "date":          key,
            "day":           d.strftime("%a"),
            "total_minutes": 0,
            "total_hours":   0
        })
    return result


def get_monthly_data(serial, uid=None):
    today   = datetime.date.today()

    if uid:
        try:
            history_docs = (
                db.collection("users").document(uid)
                  .collection("dailyHistory")
                  .order_by("date", direction=firestore.Query.DESCENDING)
                  .limit(30)
                  .stream()
            )
            firestore_history = {doc.to_dict()["date"]: doc.to_dict()["total_hours"]
                                 for doc in history_docs
                                 if doc.to_dict().get("date")}
            result = []
            for i in range(29, -1, -1):
                d   = today - datetime.timedelta(days=i)
                key = d.strftime("%Y-%m-%d")
                result.append({
                    "date":          key,
                    "day":           d.strftime("%a"),
                    "total_minutes": round(firestore_history.get(key, 0) * 60, 2),
                    "total_hours":   round(firestore_history.get(key, 0), 2)
                })
            return result
        except Exception as e:
            print(f"[ScreenSense] dailyHistory fetch error: {e}")

    result = []
    for i in range(29, -1, -1):
        d   = today - datetime.timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        result.append({
            "date": key, "day": d.strftime("%a"),
            "total_minutes": 0, "total_hours": 0
        })
    return result


def save_daily_snapshot(uid, total_hours):
    """Save today's accurate screen time to Firestore dailyHistory."""
    try:
        today_str = datetime.date.today().isoformat()
        db.collection("users").document(uid)\
          .collection("dailyHistory")\
          .document(today_str)\
          .set({
              "date":        today_str,
              "total_hours": round(total_hours, 2),
              "saved_at":    firestore.SERVER_TIMESTAMP
          }, merge=True)
        print(f"[ScreenSense] Saved dailyHistory for {today_str}: {total_hours:.2f}h")
    except Exception as e:
        print(f"[ScreenSense] dailyHistory save error: {e}")# ─────────────────────────────────────────
# HELPER: 7-day forecast
# ─────────────────────────────────────────
def compute_forecast(total_hours_today, risk_score=50):
    days      = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    today_idx = datetime.datetime.now().weekday()
    rng       = random.Random(int(datetime.datetime.now().strftime("%Y%W")))
    forecast  = []
    for i, day in enumerate(days):
        if i == today_idx:
            h = round(total_hours_today, 1)
        else:
            base     = total_hours_today * (1.15 if i >= 5 else 0.9)
            variance = rng.uniform(-0.8, 0.8)
            h        = round(max(0.3, base + variance), 1)
        forecast.append({"day": day, "predicted_hr": h})
    return forecast

# ─────────────────────────────────────────
# INTERNAL: Build the normalized today payload the dashboard expects
# ─────────────────────────────────────────
def _build_screen_time_data(uid):
    result      = (results_store.get(uid) or {}).get("today", {})
    apps        = result.get("app_risks", [])
    total_hours = result.get("total_hours",
                             round(sum(a.get("minutes", 0) for a in apps) / 60, 2))

    top_app, top_app_min = None, 0
    if apps:
        top         = max(apps, key=lambda x: x.get("minutes", 0))
        top_app     = top.get("app_name")
        top_app_min = round(top.get("minutes", 0))

    normalized_apps = [
        {
            "app_name":   a.get("app_name",   a.get("appName",   "Unknown")),
            "clean_name": a.get("clean_name", clean_app_name(a.get("app_name", ""))),
            "minutes":    a.get("minutes",    a.get("total_time_min", 0)),
            "risk_level": a.get("risk_level", "pending")
        }
        for a in apps
    ]

    return {
        "range":               "today",
        "totalScreenTimeHr":   total_hours,
        "totalMinutes":        result.get("total_minutes", round(total_hours * 60, 2)),
        "topApp":              top_app,
        "topAppMin":           top_app_min,
        "unlockCount":         result.get("unlock_count", 0),
        "riskScore":           result.get("risk_score",   0),
        "riskLevel":           result.get("risk_level",   "low"),
        "predictionLabel":     result.get("prediction_label", ""),
        "socialMediaPct":      result.get("social_media_pct", 0),
        "appUsage":            normalized_apps,
        "hourlyDistribution":  result.get("hourly_distribution", {}),
        "forecast": result.get("forecast") or compute_forecast(total_hours, result.get("risk_score", 50)),
        "updatedAt":           result.get("collected_at")
    }

# ─────────────────────────────────────────
# INTERNAL: Save current data to Firestore
# ─────────────────────────────────────────
def _save_to_firestore(uid):
    try:
        screen_time_data = _build_screen_time_data(uid)
        if not screen_time_data.get("appUsage") and screen_time_data.get("totalScreenTimeHr", 0) == 0:
            return

        save_daily_snapshot(uid, screen_time_data.get("totalScreenTimeHr", 0))  # ← ADD

        db.collection('users').document(uid).set(
            {
                "screenTimeData": screen_time_data,
                "today":          screen_time_data,
                "week":           results_store[uid].get("week",  []),
                "month":          results_store[uid].get("month", []),
                "lastSync":       firestore.SERVER_TIMESTAMP
            },
            merge=True
        )
        print(f"[ScreenSense] Saved to Firestore uid={uid}")
    except Exception as fe:
        print(f"[ScreenSense] Firestore save error uid={uid}: {fe}")


def get_today_screen_time_from_events(serial):
    """
    Parses MOVE_TO_FOREGROUND/BACKGROUND events filtered to TODAY only.
    This gives accurate per-app minutes without cumulative lifetime totals.
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    raw       = run_adb(["-s", serial, "shell", "dumpsys", "usagestats", "--interval", "DAILY"])

    apps     = {}   # pkg → total_ms today
    fg_times = {}   # pkg → last foreground timestamp (ms)

    for line in raw.split("\n"):
        line = line.strip()
        if "MOVE_TO_FOREGROUND" not in line and "MOVE_TO_BACKGROUND" not in line:
            continue
        try:
            parts    = line.split()
            ts_part  = next((p for p in parts if p.startswith("time=")), None)
            pkg_part = next((p for p in parts if p.startswith("package=")), None)
            if not ts_part or not pkg_part:
                continue

            ts_raw = ts_part.split("=", 1)[1]

            # Handle HH:MM:SS format — combine with today's date
            if ":" in ts_raw:
                h, m, s = ts_raw.split(":")
                dt = datetime.datetime.combine(
                    datetime.date.today(),
                    datetime.time(int(h) % 24, int(m), int(s))
                )
                ts_ms = int(dt.timestamp() * 1000)
            else:
                ts_ms = int(ts_raw)
                dt    = datetime.datetime.fromtimestamp(ts_ms / 1000)

            # ── CRITICAL: skip events not from today ──────────────
            if dt.strftime("%Y-%m-%d") != today_str:
                continue

            pkg = pkg_part.split("=", 1)[1]

            if "MOVE_TO_FOREGROUND" in line:
                fg_times[pkg] = ts_ms

            elif "MOVE_TO_BACKGROUND" in line and pkg in fg_times:
                duration_ms = ts_ms - fg_times.pop(pkg)
                if 0 < duration_ms < 7_200_000:  # cap at 2h per session (sanity check)
                    apps[pkg] = apps.get(pkg, 0) + duration_ms

        except Exception:
            continue

    # Build result list
    result = []
    for pkg, ms in apps.items():
        if ms < 30_000:  # skip under 30 seconds
            continue
        # Filter system noise
        if any(skip in pkg for skip in [
            "android.server", "systemui", "inputmethod",
            "provision", "permissioncontroller", "com.android.settings"
        ]):
            continue
        result.append({
            "app_name":     pkg,
            "clean_name":   clean_app_name(pkg),
            "milliseconds": ms,
            "minutes":      round(ms / 60_000, 2),
            "risk_level":   "pending"
        })

    return sorted(result, key=lambda x: x["minutes"], reverse=True)
# ─────────────────────────────────────────
# CORE DATA COLLECTION (today + week + month)
# ─────────────────────────────────────────
def collect_data(uid, serial):
    try:
        collection_status[uid] = "collecting"

        # ── TODAY: event-based (date-filtered, accurate) ──────────────
        print(f"[ScreenSense] Fetching TODAY events uid={uid} serial={serial}")
        apps = get_today_screen_time_from_events(serial)

        # Fallback to dump parser if events return nothing
        if not apps:
            print(f"[ScreenSense] Event parser empty, falling back to dump parser...")
            raw  = run_adb(["-s", serial, "shell", "dumpsys", "usagestats", "--interval", "DAILY"])
            apps = parse_app_usage_from_dump(raw)

        print(f"[ScreenSense] Parsed {len(apps)} apps")
        if apps:
            print(f"[ScreenSense] Top 5: {[(a['app_name'], a['minutes']) for a in apps[:5]]}")

        total_minutes = sum(a["minutes"] for a in apps)
        total_hours   = round(total_minutes / 60, 2)
        print(f"[ScreenSense] Total screen time: {total_minutes:.1f} min ({total_hours:.2f} hrs)")

        social_pct   = calc_social_pct(apps, total_minutes)
        hourly       = get_hourly_distribution(serial)
        unlock_count = get_unlock_count(serial)
        if unlock_count == 0:
            unlock_count = max(int(total_minutes / 8), 0)

        _proto_today = {
            "total_hours":         total_hours,
            "social_media_pct":    social_pct,
            "unlock_count":        unlock_count,
            "hourly_distribution": hourly,
            "app_risks":           apps,
        }
        _ml = ml_risk_score(_proto_today, _risk_config)
        score, risk_level = _ml["score"], _ml["level"]
        forecast = compute_forecast(total_hours, score)

        if uid not in results_store:
            results_store[uid] = {}

        results_store[uid]["today"] = {
            "risk_score":          score,
            "risk_level":          risk_level,
            "prediction_label":    f"{risk_level.capitalize()} Risk User",
            "app_risks":           apps[:20],
            "total_hours":         total_hours,
            "total_minutes":       total_minutes,
            "social_media_pct":    social_pct,
            "unlock_count":        unlock_count,
            "hourly_distribution": hourly,
            "forecast":            forecast,
            "collected_at":        datetime.datetime.now().isoformat(),
            "ml_factors":          _ml["factors"]
        }

        results_store[uid]["week"]  = get_weekly_data(serial, uid)
        results_store[uid]["month"] = get_monthly_data(serial, uid)

        _save_to_firestore(uid)
        collection_status[uid] = "done"
        print(f"[ScreenSense] STATUS=done uid={uid} | risk={risk_level}({score})")

    except Exception as e:
        collection_status[uid] = "error"
        if uid not in results_store:
            results_store[uid] = {}
        results_store[uid]["error"] = str(e)
        print(f"[ScreenSense] Collection error uid={uid}: {e}")
# ─────────────────────────────────────────
# BACKGROUND REFRESH (after first collection)
# Does not flip status back to "done" — keeps "live"
# so the dashboard auto-refreshes without re-triggering the loading flow
# ─────────────────────────────────────────
def collect_data_background(uid, serial):
    try:
        raw  = run_adb(["-s", serial, "shell", "dumpsys", "usagestats", "--interval", "DAILY"])
        apps = parse_app_usage_from_dump(raw)
        if not apps:
            raw  = run_adb(["-s", serial, "shell", "dumpsys", "usagestats"])
            apps = parse_app_usage_from_dump(raw)

        total_minutes = sum(a["minutes"] for a in apps)

# 🚨 CRITICAL PROTECTION
        if total_minutes <= 0:
            print(f"[ScreenSense] Skipping background update (empty usage) uid={uid}")
            return  # ← DO NOT overwrite existing data

        total_hours = round(total_minutes / 60, 2)

        _proto_today = {
            "total_hours":         total_hours,
            "social_media_pct":    calc_social_pct(apps, total_minutes),
            "unlock_count":        get_unlock_count(serial),
            "hourly_distribution": get_hourly_distribution(serial),
            "app_risks":           apps,
        }
        _ml = ml_risk_score(_proto_today, _risk_config)
        score, risk_level = _ml["score"], _ml["level"]

        if uid not in results_store:
            results_store[uid] = {}

        results_store[uid]["today"] = {
            "risk_score":          score,
            "risk_level":          risk_level,
            "prediction_label":    f"{risk_level.capitalize()} Risk User",
            "app_risks":           apps[:20],
            "total_hours":         total_hours,
            "total_minutes":       total_minutes,
            "social_media_pct":    calc_social_pct(apps, total_minutes),
            "unlock_count":        get_unlock_count(serial),
            "hourly_distribution": get_hourly_distribution(serial),
            "forecast":            compute_forecast(total_hours, score),
            "collected_at":        datetime.datetime.now().isoformat(),
            "ml_factors": _ml["factors"]   # ← add this line
        }
        results_store[uid]["week"]  = get_weekly_data(serial, uid)
        results_store[uid]["month"] = get_monthly_data(serial, uid)

        _save_to_firestore(uid)
        collection_status[uid] = "live"
        print(f"[ScreenSense] Background refresh done uid={uid} | {total_minutes:.1f} min")
    except Exception as e:
        print(f"[ScreenSense] Background refresh error uid={uid}: {e}")

# ─────────────────────────────────────────
# LIVE COLLECTOR THREAD
# ─────────────────────────────────────────
def live_collector(uid, serial):
    # First collection → sets status="done" → frontend redirects to dashboard
    collect_data(uid, serial)
    # Subsequent background refreshes every 60s
    while True:
        time.sleep(60)
        collect_data_background(uid, serial)

# ─────────────────────────────────────────
# CHECK ADB STATUS
# ─────────────────────────────────────────
@app.route("/api/status")
def status():
    try:
        output = run_adb(["devices"])
        lines  = output.strip().split("\n")[1:]
        devices, unauthorized, offline = [], [], []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "device":
                model       = run_adb(["-s", serial, "shell", "getprop", "ro.product.model"]).strip()
                android_ver = run_adb(["-s", serial, "shell", "getprop", "ro.build.version.release"]).strip()
                devices.append({
                    "serial":      serial,
                    "model":       model,
                    "android_ver": android_ver,
                    "type":        "usb"
                })
            elif state == "unauthorized":
                unauthorized.append(serial)
            elif state == "offline":
                offline.append(serial)
        return jsonify({
            "adb_found":    True,
            "devices":      devices,
            "unauthorized": unauthorized,
            "offline":      offline,
            "raw_output":   output
        })
    except Exception as e:
        return jsonify({"adb_found": False, "error": str(e)})

# ─────────────────────────────────────────
# CONNECT DEVICE
# ─────────────────────────────────────────
@app.route("/api/connect", methods=["POST"])
def connect():
    data = request.json or {}
    mode = data.get("mode", "usb")

    if mode == "wifi":
        ip   = data.get("ip")
        port = data.get("port", 5555)
        if not ip:
            return jsonify({"success": False, "error": "No IP provided"})
        out = run_adb(["connect", f"{ip}:{port}"])
        if "connected" in out.lower():
            return _status_with_flag(True, out)
        return jsonify({"success": False, "message": out})

    if mode == "wifi_setup":
        serial = data.get("serial") or ""
        cmd    = ["-s", serial, "shell", "ip", "route"] if serial else ["shell", "ip", "route"]
        ip_out = run_adb(cmd)
        ip = ""
        for ln in ip_out.split("\n"):
            if "src" in ln:
                parts = ln.split()
                try:
                    ip = parts[parts.index("src") + 1]
                    break
                except (ValueError, IndexError):
                    pass
        run_adb(["-s", serial, "tcpip", "5555"] if serial else ["tcpip", "5555"])
        time.sleep(1)
        return jsonify({"success": True, "device_ip": ip})

    # Default: USB
    return status()

def _status_with_flag(success, raw):
    try:
        output  = run_adb(["devices"])
        lines   = output.strip().split("\n")[1:]
        devices = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                model = run_adb(["-s", parts[0], "shell", "getprop", "ro.product.model"]).strip()
                devices.append({"serial": parts[0], "model": model, "type": "wifi"})
        return jsonify({"success": success, "devices": devices, "raw": raw})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ─────────────────────────────────────────
# TRIGGER COLLECTION
# ─────────────────────────────────────────
@app.route("/api/trigger/<uid>", methods=["POST"])
def trigger(uid):
    data = request.json or {}
    serial = data.get("serial")

    print(f"[ScreenSense] Trigger called uid={uid} serial={serial}")

    if not serial:
        return jsonify({
            "success": False,
            "error": "No device serial provided"
        }), 400

    collection_status[uid] = "starting"

    if uid not in live_threads or not live_threads[uid].is_alive():
        thread = threading.Thread(
            target=live_collector,
            args=(uid, serial),
            daemon=True
        )
        live_threads[uid] = thread
        thread.start()

    return jsonify({"success": True, "status": "starting"})
# ─────────────────────────────────────────
# COLLECTION STATUS
#
# Status flow:
#   starting → collecting → done   (first run, frontend redirects on "done")
#   live                           (subsequent background refreshes)
#   error                          (something went wrong)
# ─────────────────────────────────────────
@app.route("/api/collection_status/<uid>")
def get_collection_status(uid):
    return jsonify({
        "status": collection_status.get(uid, "idle"),
        "result": (results_store.get(uid) or {}).get("today")
    })

# ─────────────────────────────────────────
# GET TODAY'S DATA (manual backfill trigger)
# ─────────────────────────────────────────
@app.route("/api/todays_data/<uid>", methods=["POST"])
def get_todays_data(uid):
    try:
        data   = request.json or {}
        serial = data.get("serial")
        collect_data(uid, serial)
        result = (results_store.get(uid) or {}).get("today")
        if result and "error" not in result:
            return jsonify({"success": True, "screenTimeData": _build_screen_time_data(uid)})
        return jsonify({"success": False, "error": "Failed to collect data"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────
# GET USER DATA  ← dashboard primary endpoint
# ─────────────────────────────────────────
@app.route("/api/user/<uid>")
def get_user(uid):
    range_param = request.args.get("range", "today")
    store       = results_store.get(uid)

    # Firestore fallback
    if not store or range_param not in store:
        try:
            snap = db.collection("users").document(uid).get()
            if snap.exists:
                fdata    = snap.to_dict() or {}
                today_st = fdata.get("screenTimeData") or {}

                if range_param == "today":
                    if today_st:
                        return jsonify({"screenTimeData": today_st})

                elif range_param == "week":
                    week = fdata.get("week", [])
                    if week:
                        return jsonify({"screenTimeData": {
                            "range":     "week",
                            "daily":     week,
                            "riskScore": today_st.get("riskScore", 0),
                            "riskLevel": today_st.get("riskLevel", "low"),
                            "forecast": today_st.get("forecast") or compute_forecast(today_st.get("totalScreenTimeHr", 0), today_st.get("riskScore", 50)),
                            "updatedAt": today_st.get("updatedAt")
                        }})

                elif range_param == "month":
                    month = fdata.get("month", [])
                    if month:
                        return jsonify({"screenTimeData": {
                            "range":     "month",
                            "daily":     month,
                            "riskScore": today_st.get("riskScore", 0),
                            "riskLevel": today_st.get("riskLevel", "low"),
                            "forecast":  today_st.get("forecast",  []),
                            "updatedAt": today_st.get("updatedAt")
                        }})

        except Exception as e:
            print(f"[ScreenSense] Firestore fallback error: {e}")
        return jsonify({})

    if range_param == "today":
        return jsonify({"screenTimeData": _build_screen_time_data(uid)})

    if range_param == "week":
        today_data = store.get("today", {})
        return jsonify({"screenTimeData": {
            "range":     "week",
            "daily":     store.get("week", []),
            "riskScore": today_data.get("risk_score",  0),
            "riskLevel": today_data.get("risk_level",  "low"),
            "forecast":  today_data.get("forecast",    []),
            "updatedAt": today_data.get("collected_at")
        }})

    if range_param == "month":
        today_data = store.get("today", {})
        return jsonify({"screenTimeData": {
            "range":     "month",
            "daily":     store.get("month", []),
            "riskScore": today_data.get("risk_score",  0),
            "riskLevel": today_data.get("risk_level",  "low"),
            "forecast":  today_data.get("forecast",    []),
            "updatedAt": today_data.get("collected_at")
        }})

    return jsonify({})
# ─────────────────────────────────────────
# RECEIVE DATA FROM ANDROID APP / ML MODEL
# ─────────────────────────────────────────
@app.route('/api/receive_data', methods=['POST'])
def receive_data():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON body'}), 400

        uid = data.get('uid')
        if not uid:
            return jsonify({'success': False, 'error': 'No UID provided'}), 400

        print(f"[ScreenSense] receive_data uid={uid}")

        if uid not in results_store:
            results_store[uid] = {}

        # Full payload from phone
        if data.get('appUsage'):
            app_usage   = data.get('appUsage', [])
            total_hours = data.get('totalScreenTimeHr', 0)
            total_mins  = total_hours * 60

            for a in app_usage:
                if 'clean_name' not in a:
                    a['clean_name'] = clean_app_name(a.get('app_name', a.get('appName', '')))

            social_pct = calc_social_pct(app_usage, total_mins)
            forecast   = data.get('forecast') or compute_forecast(total_hours, data.get('riskScore', 50))

            results_store[uid]["today"] = {
                "risk_score":          data.get('riskScore', 0),
                "risk_level":          data.get('riskLevel', 'low'),
                "prediction_label":    data.get('predictionLabel', ''),
                "app_risks":           app_usage,
                "total_hours":         total_hours,
                "total_minutes":       total_mins,
                "social_media_pct":    social_pct,
                "unlock_count":        data.get('unlockCount', 0),
                "hourly_distribution": data.get('hourlyDistribution', {}),
                "forecast":            forecast,
                "collected_at":        datetime.datetime.now().isoformat(),
                "ml_factors": ml_risk_score({
                    "total_hours":         total_hours,
                    "social_media_pct":    social_pct,
                    "unlock_count":        data.get('unlockCount', 0),
                    "hourly_distribution": data.get('hourlyDistribution', {}),
                    "app_risks":           app_usage,
                }, _risk_config)["factors"]
            }
            if data.get('weeklyData'):
                results_store[uid]["week"]  = data['weeklyData']
            if data.get('monthlyData'):
                results_store[uid]["month"] = data['monthlyData']

        # ML model risk score update only
        elif data.get('riskScore') is not None and results_store[uid].get("today"):
            results_store[uid]["today"]["risk_score"]       = data['riskScore']
            results_store[uid]["today"]["risk_level"]       = data.get('riskLevel', 'low')
            results_store[uid]["today"]["prediction_label"] = data.get('predictionLabel', '')
            if data.get('appRisks'):
                risk_map = {a['app_name']: a['risk_level']
                            for a in data['appRisks'] if 'app_name' in a}
                for a in results_store[uid]["today"].get("app_risks", []):
                    if a.get("app_name") in risk_map:
                        a["risk_level"] = risk_map[a["app_name"]]

        _save_to_firestore(uid)
        collection_status[uid] = "done"
        return jsonify({'success': True, 'data': _build_screen_time_data(uid)})

    except Exception as e:
        print(f"[ScreenSense] receive_data error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({
        "status":      "ok",
        "timestamp":   datetime.datetime.now().isoformat(),
        "active_uids": list(results_store.keys()),
        "status_map":  dict(collection_status)
    })

@app.route("/api/total_screen_time/<uid>")
def total_screen_time(uid):
    """
    Parses totalScreenOnTime from `dumpsys usagestats`
    e.g.  totalScreenOnTime=+88d13h21m16s886ms  →  88 days, 13h 21m
    """
    try:
        # Find connected device
        serial = None
        adb_out = run_adb(["devices"])
        for line in adb_out.strip().split("\n")[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                break

        if not serial:
            return jsonify({"success": False, "error": "No device connected"}), 400

        raw = run_adb(["-s", serial, "shell", "dumpsys", "usagestats"])

        # Match: totalScreenOnTime=+88d13h21m16s886ms
        import re
        match = re.search(r'totalScreenOnTime=\+?(\d+)d(\d+)h(\d+)m(\d+)s', raw)
        if not match:
            return jsonify({"success": False, "error": "Pattern not found"}), 404

        days  = int(match.group(1))
        hours = int(match.group(2))
        mins  = int(match.group(3))

        total_hours = round(days * 24 + hours + mins / 60, 1)
        label = f"{days}d {hours}h {mins}m"

        return jsonify({
            "success":     True,
            "days":        days,
            "hours":       hours,
            "minutes":     mins,
            "total_hours": total_hours,
            "label":       label           # "88d 13h 21m"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    
    
# Add near top with other globals
_history_cache = {}
_HISTORY_CACHE_TTL = 120  # seconds

@app.route("/api/history/<uid>", methods=["GET"])
def get_history(uid):
    try:
        now = time.time()
        cached = _history_cache.get(uid)
        if cached and (now - cached["ts"]) < _HISTORY_CACHE_TTL:
            return jsonify(cached["data"])

        # 30 days ago cutoff
        cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=30)
        cutoff_iso = cutoff_dt.isoformat()

        docs = (
            db.collection("web_usage")
              .where(filter=FieldFilter("userId", "==", uid))
              .where(filter=FieldFilter("timestamp", ">=", cutoff_iso))
              .order_by("timestamp", direction=firestore.Query.DESCENDING)
              .limit(2000)           # enough for 30 days
              .stream()
        )

        visits = []
        count_map = defaultdict(int)
        by_date = defaultdict(list)  # date_str -> list of {url, time}

        for doc in docs:
            data = doc.to_dict()
            url = data.get("url")
            timestamp = data.get("timestamp")
            if not url:
                continue
            visits.append({"url": url, "lastVisited": timestamp})
            count_map[url] += 1

            # Group by date
            date_str = str(timestamp)[:10] if timestamp else "Unknown"
            by_date[date_str].append({"url": url, "timestamp": timestamp})

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        today_urls = {
            v["url"] for v in visits
            if v.get("lastVisited") and str(v["lastVisited"])[:10] == today_str
        }

        recent_sorted = sorted(visits, key=lambda x: x["lastVisited"] or "", reverse=True)[:10]
        most_sorted = sorted(
            [{"url": u, "count": c} for u, c in count_map.items()],
            key=lambda x: x["count"], reverse=True
        )[:10]

        # Build 30-day grouped history (sorted dates descending)
        history_30d = []
        for date_str in sorted(by_date.keys(), reverse=True):
            day_visits = by_date[date_str]
            # Deduplicate URLs per day but keep order
            seen = set()
            unique_visits = []
            for v in sorted(day_visits, key=lambda x: x["timestamp"] or "", reverse=True):
                if v["url"] not in seen:
                    seen.add(v["url"])
                    unique_visits.append(v)
            history_30d.append({
                "date": date_str,
                "visits": unique_visits[:50],  # max 50 unique URLs per day
                "total": len(day_visits),
                "unique": len(unique_visits)
            })

        result = {
            "recent":      recent_sorted,
            "most":        most_sorted,
            "today_count": len(today_urls),
            "history_30d": history_30d,
            "total_visits": len(visits)
        }

        _history_cache[uid] = {"data": result, "ts": now}
        return jsonify(result)

    except Exception as e:
        print("History error:", e)
        cached = _history_cache.get(uid)
        if cached:
            return jsonify(cached["data"])
        return jsonify({"error": str(e), "recent": [], "most": [], "today_count": 0, "history_30d": []}), 500



# ─────────────────────────────────────────
# GROQ AI HISTORY INSIGHTS
# Add this endpoint to your app.py
# Place it after the /api/history/<uid> route
# ─────────────────────────────────────────

_history_insights_cache = {}   # uid → {data, ts}
_HISTORY_INSIGHTS_CACHE_TTL = 120  # seconds

@app.route("/api/history_insights/<uid>", methods=["GET"])
def history_insights(uid):
    try:
        force_fresh = request.args.get("fresh") == "1"
        now = time.time()

        # Return cached if within TTL and not forced
        if not force_fresh and uid in _history_insights_cache:
            if (now - _history_insights_cache[uid]["ts"]) < _HISTORY_INSIGHTS_CACHE_TTL:
                return jsonify(_history_insights_cache[uid]["data"])

        # Pull history from Firestore (last 30 days)
        cutoff_dt  = datetime.datetime.now() - datetime.timedelta(days=30)
        cutoff_iso = cutoff_dt.isoformat()

        docs = (
            db.collection("web_usage")
              .where(filter=FieldFilter("userId", "==", uid))
              .where(filter=FieldFilter("timestamp", ">=", cutoff_iso))
              .order_by("timestamp", direction=firestore.Query.DESCENDING)
              .limit(500)
              .stream()
        )

        visits     = []
        domain_map = defaultdict(int)    # domain → count
        today_str  = datetime.datetime.now().strftime("%Y-%m-%d")
        today_visits = []

        for doc in docs:
            d   = doc.to_dict()
            url = d.get("url", "")
            ts  = d.get("timestamp", "")
            if not url:
                continue
            visits.append(url)

            # Extract domain
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:
                domain = url[:40]
            domain_map[domain] += 1

            if str(ts)[:10] == today_str:
                today_visits.append(url)

        if not visits:
            return jsonify({"insights": [], "error": "No browsing history available"}), 404

        # Top domains
        top_domains = sorted(domain_map.items(), key=lambda x: x[1], reverse=True)[:10]
        top_domains_str = ", ".join([f"{d[0]} ({d[1]} visits)" for d in top_domains[:6]])

        # Category detection
        CATEGORIES = {
            "social_media":    ["instagram","facebook","twitter","tiktok","snapchat","linkedin","reddit","x.com","threads","pinterest","youtube"],
            "productivity":    ["notion","docs.google","gmail","outlook","slack","trello","asana","github","stackoverflow","jira","confluence","drive.google"],
            "entertainment":   ["netflix","twitch","disneyplus","hulu","primevideo","spotify","soundcloud","youtube","9gag","imgur","reddit"],
            "news":            ["bbc","cnn","theguardian","nytimes","reuters","apnews","techcrunch","theverge","medium","substack"],
            "shopping":        ["amazon","flipkart","ebay","etsy","shopify","myntra","meesho","ajio","zara","alibaba"],
            "education":       ["coursera","udemy","khan","edx","duolingo","leetcode","hackerrank","codecademy","brilliant","wikipedia"],
            "adult_risk":      ["gambling","casino","bet365","1xbet","poker"],
        }

        cat_counts = defaultdict(int)
        all_domains = list(domain_map.keys())
        for domain in all_domains:
            for cat, keywords in CATEGORIES.items():
                if any(kw in domain for kw in keywords):
                    cat_counts[cat] += domain_map[domain]
                    break

        total_visits     = len(visits)
        today_count      = len(today_visits)
        unique_domains   = len(domain_map)
        social_visits    = cat_counts.get("social_media", 0)
        productivity_v   = cat_counts.get("productivity", 0)
        entertainment_v  = cat_counts.get("entertainment", 0)
        news_v           = cat_counts.get("news", 0)
        social_pct       = round((social_visits / total_visits) * 100) if total_visits else 0
        productive_pct   = round((productivity_v / total_visits) * 100) if total_visits else 0

        # Category breakdown string
        cat_summary = ", ".join([
            f"{cat.replace('_', ' ').title()}: {round((cnt/total_visits)*100)}%"
            for cat, cnt in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            if cnt > 0
        ]) or "No category data"

        prompt = f"""You are ScreenSense AI, an expert digital wellness analyst specializing in browsing behaviour and internet addiction.

Analyze this user's BROWSING HISTORY data and return EXACTLY 3 JSON insight objects.

BROWSING DATA (last 30 days):
- Total page visits: {total_visits}
- Unique domains visited: {unique_domains}
- Today's visits: {today_count}
- Top domains: {top_domains_str}
- Category breakdown: {cat_summary}
- Social media visits: {social_visits} ({social_pct}% of total)
- Productivity visits: {productivity_v} ({productive_pct}% of total)
- Entertainment visits: {entertainment_v}

Instructions:
- Give sharp, specific, data-driven insights based on the actual domains and patterns above
- Identify concerning patterns (excessive social media, doom-scrolling, procrastination loops)
- Highlight positive patterns if present
- Use the actual domain names in your insights (e.g., "You visited Instagram 47 times...")
- Be specific with numbers from the data
- Each insight should be actionable and practical

Respond ONLY with a JSON array of exactly 3 objects, no markdown, no extra text:
[
  {{"icon": "emoji", "title": "short title (max 5 words)", "body": "2-3 sentences with specific data and actionable advice", "action": "short CTA text", "severity": "high|medium|low", "category": "category name"}},
  ...
]"""

        client   = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model      = "llama-3.3-70b-versatile",
            messages   = [{"role": "user", "content": prompt}],
            max_tokens = 700,
            temperature= 0.6
        )

        raw_text = response.choices[0].message.content.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        insights = json.loads(raw_text)

        # Build category breakdown for frontend charts
        category_breakdown = {
            cat: {
                "count": cnt,
                "pct":   round((cnt / total_visits) * 100)
            }
            for cat, cnt in cat_counts.items() if cnt > 0
        }

        result = {
            "insights":           insights,
            "top_domains":        top_domains[:8],
            "category_breakdown": category_breakdown,
            "total_visits":       total_visits,
            "today_count":        today_count,
            "unique_domains":     unique_domains,
            "social_pct":         social_pct,
            "productive_pct":     productive_pct,
        }
        _history_insights_cache[uid] = {"data": result, "ts": time.time()}
        return jsonify(result)

    except Exception as e:
        print(f"[ScreenSense] history_insights error uid={uid}: {e}")
        return jsonify({"insights": [], "error": str(e)}), 500


@app.route("/api/web_usage", methods=["POST"])
def web_usage():
    data = request.json

    print("WEB DATA RECEIVED:", data)

    uid = data.get("userId")

    if not uid:
        return jsonify({"error": "Missing userId"}), 400

    db.collection("web_usage").add({
        "userId": uid,   # 🔥 THIS IS CRITICAL
        "url": data.get("url"),
        "domain": data.get("domain"),
        "timestamp": data.get("timestamp")
    })

    return jsonify({"success": True})
# ─────────────────────────────────────────




# ─────────────────────────────────────────
# ML RISK MODEL (rule-based + weighted scoring)
# Factors: screen time, social %, unlocks, night usage, top app concentration
# Settings can adjust thresholds via /api/risk_config
# ─────────────────────────────────────────

_risk_config = {
    "screen_time_weight":  0.35,   # weight for total screen time factor
    "social_pct_weight":   0.25,   # weight for social media %
    "unlock_weight":       0.20,   # weight for unlock frequency
    "night_usage_weight":  0.10,   # weight for late-night usage (22:00–05:00)
    "concentration_weight":0.10,   # weight for single-app overuse
    # thresholds
    "screen_time_max":     8.0,    # hours considered 100% for this factor
    "social_pct_max":      80.0,   # % considered 100% for this factor
    "unlock_max":          120,    # unlocks considered 100%
    "night_pct_max":       30.0,   # % of usage at night = 100%
    "concentration_max":   70.0,   # single app % = 100%
}

def ml_risk_score(today_data: dict, config: dict = None) -> dict:
    """
    Weighted ML-style risk scorer.
    Returns: { score, level, factors, prediction_label }
    """
    cfg = {**_risk_config, **(config or {})}

    total_hrs    = today_data.get("total_hours", 0)
    social_pct   = today_data.get("social_media_pct", 0)
    unlocks      = today_data.get("unlock_count", 0)
    hourly       = today_data.get("hourly_distribution", {})
    apps         = today_data.get("app_risks", [])

    # Factor 1: Screen time (sigmoid-style normalisation)
    st_factor = min(total_hrs / cfg["screen_time_max"], 1.0)
    st_factor = 1 / (1 + math.exp(-10 * (st_factor - 0.5)))  # sigmoid

    # Factor 2: Social media %
    soc_factor = min(social_pct / cfg["social_pct_max"], 1.0)

    # Factor 3: Unlock frequency
    unlock_factor = min(unlocks / cfg["unlock_max"], 1.0)

    # Factor 4: Night usage (hours 22–5)
    night_hours = [22, 23, 0, 1, 2, 3, 4, 5]
    total_mins  = sum(hourly.values()) or 1
    night_mins  = sum(hourly.get(str(h), 0) for h in night_hours)
    night_pct   = (night_mins / total_mins) * 100
    night_factor = min(night_pct / cfg["night_pct_max"], 1.0)

    # Factor 5: Single-app concentration (top app dominance)
    if apps:
        top_app_mins    = max((a.get("minutes", 0) for a in apps), default=0)
        total_app_mins  = sum(a.get("minutes", 0) for a in apps) or 1
        conc_pct        = (top_app_mins / total_app_mins) * 100
    else:
        conc_pct = 0
    conc_factor = min(conc_pct / cfg["concentration_max"], 1.0)

    # Weighted sum → 0–100 score
    raw_score = (
        st_factor    * cfg["screen_time_weight"] +
        soc_factor   * cfg["social_pct_weight"]  +
        unlock_factor * cfg["unlock_weight"]     +
        night_factor * cfg["night_usage_weight"] +
        conc_factor  * cfg["concentration_weight"]
    )
    score = round(raw_score * 100)

    if score >= 75:   level, label = "high",     "High Risk User"
    elif score >= 50: level, label = "moderate",  "Moderate Risk User"
    elif score >= 25: level, label = "low",       "Low Risk User"
    else:             level, label = "minimal",   "Healthy User"

    return {
        "score":             score,
        "level":             level,
        "prediction_label":  label,
        "factors": {
            "screen_time":   round(st_factor * 100),
            "social_media":  round(soc_factor * 100),
            "unlocks":       round(unlock_factor * 100),
            "night_usage":   round(night_factor * 100),
            "concentration": round(conc_factor * 100),
        }
    }

@app.route("/api/risk_config", methods=["GET"])
def get_risk_config():
    return jsonify(_risk_config)

@app.route("/api/risk_config", methods=["POST"])
def update_risk_config():
    """
    Frontend settings panel posts updated weights/thresholds here.
    Example body: { "screen_time_weight": 0.4, "screen_time_max": 6 }
    """
    global _risk_config
    updates = request.json or {}
    allowed = set(_risk_config.keys())
    for k, v in updates.items():
        if k in allowed:
            try:
                _risk_config[k] = float(v)
            except (TypeError, ValueError):
                pass

    # Re-score all cached UIDs with new config
    for uid in list(results_store.keys()):
        today = results_store[uid].get("today")
        if today:
            ml = ml_risk_score(today, _risk_config)
            today["risk_score"]       = ml["score"]
            today["risk_level"]       = ml["level"]
            today["prediction_label"] = ml["prediction_label"]
            today["ml_factors"]       = ml["factors"]
            _save_to_firestore(uid)

    return jsonify({"success": True, "config": _risk_config})


# ─────────────────────────────────────────
# GROQ AI INSIGHTS
# ─────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

_insights_cache = {}   # uid → {data, ts}
_INSIGHTS_CACHE_TTL = 90  # seconds

@app.route("/api/ai_insights/<uid>", methods=["GET"])
def ai_insights(uid):
    try:
        force_fresh = request.args.get("fresh") == "1"
        now = time.time()

        # Return cached if within TTL and not forced
        if not force_fresh and uid in _insights_cache:
            if (now - _insights_cache[uid]["ts"]) < _INSIGHTS_CACHE_TTL:
                return jsonify(_insights_cache[uid]["data"])

        # On fresh=1, also evict insights cache so Groq gets latest numbers
        if force_fresh and uid in _insights_cache:
            del _insights_cache[uid]

        today = (results_store.get(uid) or {}).get("today")
        # If fresh requested and results_store is stale, pull latest from Firestore
        if force_fresh or not today:
            try:
                snap = db.collection("users").document(uid).get()
                if snap.exists:
                    fdata = snap.to_dict() or {}
                    fs_today = fdata.get("screenTimeData") or {}
                    if fs_today:
                        # Merge into results_store so ml_risk_score uses latest values
                        if uid not in results_store:
                            results_store[uid] = {}
                        if force_fresh or not today:
                            results_store[uid]["today"] = {
                                "total_hours":         fs_today.get("totalScreenTimeHr", 0),
                                "social_media_pct":    fs_today.get("socialMediaPct", 0),
                                "unlock_count":        fs_today.get("unlockCount", 0),
                                "hourly_distribution": fs_today.get("hourlyDistribution", {}),
                                "app_risks":           fs_today.get("appUsage", []),
                                "risk_score":          fs_today.get("riskScore", 0),
                                "risk_level":          fs_today.get("riskLevel", "low"),
                                "collected_at":        fs_today.get("updatedAt"),
                            }
                            today = results_store[uid]["today"]
            except Exception as fe:
                print(f"[ScreenSense] Firestore refresh error uid={uid}: {fe}")

        if not today:
            today = (results_store.get(uid) or {}).get("today")
        if not today:
            # Try Firestore fallback
            snap = db.collection("users").document(uid).get()
            if snap.exists:
                today = (snap.to_dict() or {}).get("screenTimeData") or {}

        if not today:
            return jsonify({"insights": [], "error": "No data available"}), 404

        apps       = today.get("app_risks", today.get("appUsage", []))
        top_apps   = [
            f"{a.get('clean_name', a.get('app_name','?'))} ({round(a.get('minutes', a.get('total_time_min',0)))}min)"
            for a in apps[:5]
        ]
        ml         = ml_risk_score(today, _risk_config)
        factors    = ml["factors"]

        prompt = f"""You are ScreenSense AI, a screen-time addiction analyst. 
Analyze the user's phone usage and return EXACTLY 3 JSON insight objects.

USER DATA:
- Total screen time: {today.get('total_hours', 0):.1f} hours today
- Risk score: {ml['score']}/100 ({ml['level']} risk)
- Social media usage: {today.get('social_media_pct', 0):.0f}% of screen time
- Phone unlocks: {today.get('unlock_count', 0)}
- Top apps: {', '.join(top_apps) if top_apps else 'Unknown'}
- ML factors (0–100): screen_time={factors['screen_time']}, social={factors['social_media']}, unlocks={factors['unlocks']}, night_usage={factors['night_usage']}, concentration={factors['concentration']}

Respond ONLY with a JSON array of exactly 3 objects, no markdown, no extra text:
[
  {{"icon": "emoji", "title": "short title", "body": "2-sentence actionable insight", "action": "cta text", "severity": "high|medium|low"}},
  ...
]
Focus on the most critical risk factors. Be specific with numbers. Give actionable advice."""

        client   = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model    = "llama-3.3-70b-versatile",
            messages = [{"role": "user", "content": prompt}],
            max_tokens = 600,
            temperature = 0.7
        )

        raw_text = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        insights = json.loads(raw_text)

        result = {
            "insights":   insights,
            "ml_score":   ml["score"],
            "ml_level":   ml["level"],
            "ml_factors": factors
        }
        _insights_cache[uid] = {"data": result, "ts": time.time()}
        return jsonify(result)

    except Exception as e:
        print(f"[ScreenSense] AI insights error uid={uid}: {e}")
        return jsonify({"insights": [], "error": str(e)}), 500


# ─────────────────────────────────────────
# SEND PUSH ALERT TO PHONE VIA ADB
# ─────────────────────────────────────────
@app.route("/api/send_alert/<uid>", methods=["POST"])
def send_alert(uid):
    try:
        data       = request.json or {}
        title      = data.get("title",      "ScreenSense Alert")
        body       = data.get("body",       "Check your screen time.")
        clean_name = data.get("clean_name", "App")
        minutes    = data.get("minutes",    0)
        risk_level = data.get("risk_level", "low")

        # Find connected device — use stored serial or detect live
        store  = results_store.get(uid, {})
        serial = None

        # Try to get serial from live_threads or ADB devices
        adb_out = run_adb(["devices"])
        for line in adb_out.strip().split("\n")[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                break

        if not serial:
            return jsonify({"success": False, "error": "No device connected"}), 400

        # Send notification via ADB shell am broadcast (works on Android 8+)
        # Uses the built-in notification manager via am startservice
        safe_title = title.replace('"', '\\"').replace("'", "\\'")
        safe_body  = body.replace('"', '\\"').replace("'", "\\'")[:250]

        # Post as high-priority heads-up notification (pops up on screen)
        print(f"[ScreenSense] Sending alert to serial={serial} app={clean_name}")
        result = ""

        # ── Method 1: cmd notification post (Android 11+) ────────────
        try:
            cmd1   = f'cmd notification post -S bigtext -t "{safe_title}" ScreenSense "{safe_body}"'
            result = run_adb(["-s", serial, "shell", cmd1])
            print(f"[ScreenSense] Method1 result: {result[:120]}")
        except Exception as e1:
            print(f"[ScreenSense] Method1 failed: {e1}")

        # ── Method 2: am startactivity with ACTION_SEND (Android 8+) ─
        if not result.strip() or "error" in result.lower() or "exception" in result.lower():
            try:
                safe_body_short = safe_body[:180]
                cmd2 = (
                    f'am start -a android.intent.action.SEND '
                    f'-t text/plain '
                    f'--es android.intent.extra.SUBJECT "{safe_title}" '
                    f'--es android.intent.extra.TEXT "{safe_body_short}" '
                    f'--activity-no-history --activity-clear-top 2>/dev/null; '
                    f'cmd notification post -t "{safe_title}" ScreenSense "{safe_body_short}"'
                )
                result2 = run_adb(["-s", serial, "shell", cmd2])
                print(f"[ScreenSense] Method2 result: {result2[:120]}")
                if result2.strip():
                    result = result2
            except Exception as e2:
                print(f"[ScreenSense] Method2 failed: {e2}")

        # ── Method 3: Termux-notification (if Termux installed) ───────
        if not result.strip() or "error" in result.lower() or "exception" in result.lower():
            try:
                cmd3   = f'termux-notification --title "{safe_title}" --content "{safe_body[:180]}" --priority high --id 9999 2>/dev/null'
                result3 = run_adb(["-s", serial, "shell", cmd3])
                print(f"[ScreenSense] Method3 (Termux) result: {result3[:80]}")
                if result3.strip() and "not found" not in result3.lower():
                    result = result3
            except Exception as e3:
                print(f"[ScreenSense] Method3 failed: {e3}")

        # ── Method 4: Reliable overlay via ADB input + keyevent ───────
        # Wake screen, show a toast via uiautomator
        try:
            wake_cmd = "input keyevent KEYCODE_WAKEUP"
            run_adb(["-s", serial, "shell", wake_cmd])
            time.sleep(0.5)

            # Toast via am broadcast to system settings
            toast_text = f"{safe_title}: {safe_body[:100]}"
            toast_cmd  = (
                f'am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS 2>/dev/null; '
                f'input keyevent KEYCODE_WAKEUP; '
                f'cmd notification post -t "{safe_title}" com.screensense.alert "{safe_body[:200]}"'
            )
            result4 = run_adb(["-s", serial, "shell", toast_cmd])
            print(f"[ScreenSense] Method4 result: {result4[:80]}")
        except Exception as e4:
            print(f"[ScreenSense] Method4 failed: {e4}")

        # ── Always considered success if no exception reaching here ───
        result = result or "sent"

        # Log to Firestore
        try:
            db.collection("alerts").add({
                "userId":     uid,
                "title":      title,
                "body":       body,
                "app_name":   data.get("app_name"),
                "clean_name": clean_name,
                "minutes":    minutes,
                "risk_level": risk_level,
                "risk_score": data.get("risk_score", 0),
                "sent_at":    firestore.SERVER_TIMESTAMP
            })
        except Exception as fe:
            print(f"[ScreenSense] Alert Firestore log error: {fe}")

        return jsonify({"success": True, "serial": serial, "result": result[:100]})

    except Exception as e:
        print(f"[ScreenSense] send_alert error uid={uid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500




@app.route("/api/clear_cache/<uid>", methods=["POST"])
def clear_cache(uid):
    try:
        db.collection('users').document(uid).update({
            "week":  [],
            "month": []
        })
        if uid in results_store:
            results_store[uid].pop("week", None)
            results_store[uid].pop("month", None)
        return jsonify({"success": True, "message": "Cache cleared"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})





if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)