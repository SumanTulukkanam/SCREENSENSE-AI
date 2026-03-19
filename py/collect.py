"""
╔══════════════════════════════════════════════════════════════╗
║  ScreenSense AI — Collector v7 (Xiaomi HyperOS Final)        ║
║                                                              ║
║  FINDINGS: Xiaomi HyperOS V816 / Android 14 does NOT        ║
║  write totalTimeInForeground to dumpsys usagestats.          ║
║  Battery stats also blocked. DW also blocked.               ║
║                                                              ║
║  WORKING DATA SOURCES:                                       ║
║  ✅ /proc/uptime  → exact boot time                          ║
║  ✅ Broadcast timestamps → last-active time per app          ║
║  ✅ "Currently Active" field → app in fg right now           ║
║  ✅ pm list packages -3 → installed apps                     ║
║  ✅ logcat screen_toggled → unlock count                     ║
║  ✅ dumpsys notification → notification counts               ║
║  ✅ cc.honista.app → Instagram mod (counts as Instagram)     ║
╚══════════════════════════════════════════════════════════════╝
"""
import subprocess, json, time, datetime, argparse, os, sys, re, requests
from pathlib import Path
from collections import defaultdict

FLASK_SERVER     = "http://192.168.1.12:5000"
COLLECT_INTERVAL = 3600
DATA_DIR         = Path(__file__).parent / "collected_data"
DATA_DIR.mkdir(exist_ok=True)

# High-risk social/entertainment apps
HIGH_RISK_PKGS = {
    "com.instagram.android","com.facebook.katana","com.snapchat.android",
    "com.twitter.android","com.tiktok.android","com.google.android.youtube",
    "com.facebook.orca","com.whatsapp","com.reddit.frontpage","com.pinterest",
    "com.linkedin.android","com.zhiliaoapp.musically","com.netflix.mediaclient",
    "com.spotify.music","tv.twitch.android.app","com.discord","org.telegram.messenger",
    # Xiaomi-specific: modded/alternative apps
    "cc.honista.app",        # Instagram mod (very popular in India)
    "com.gbwhatsapp",        # GB WhatsApp
    "com.whatsapp.w4b",      # WhatsApp Business
    "me.teleplus.android",   # Telegram mod
}

APP_NAMES = {
    "com.instagram.android":            "Instagram",
    "cc.honista.app":                   "Instagram (Honista)",
    "com.facebook.katana":              "Facebook",
    "com.snapchat.android":             "Snapchat",
    "com.twitter.android":              "Twitter/X",
    "com.tiktok.android":               "TikTok",
    "com.zhiliaoapp.musically":         "TikTok",
    "com.google.android.youtube":       "YouTube",
    "com.whatsapp":                     "WhatsApp",
    "com.gbwhatsapp":                   "WhatsApp (GB)",
    "com.whatsapp.w4b":                 "WhatsApp Business",
    "com.facebook.orca":                "Messenger",
    "com.android.chrome":               "Chrome",
    "com.miui.browser":                 "Mi Browser",
    "com.google.android.gm":            "Gmail",
    "com.microsoft.office.outlook":     "Outlook",
    "com.netflix.mediaclient":          "Netflix",
    "com.spotify.music":                "Spotify",
    "com.reddit.frontpage":             "Reddit",
    "com.google.android.apps.maps":     "Google Maps",
    "org.telegram.messenger":           "Telegram",
    "me.teleplus.android":              "Telegram (Plus)",
    "com.viber.voip":                   "Viber",
    "com.amazon.mShop.android.shopping":"Amazon",
    "com.flipkart.android":             "Flipkart",
    "com.google.android.googlequicksearchbox":"Google",
    "com.linkedin.android":             "LinkedIn",
    "com.pinterest":                    "Pinterest",
    "tv.twitch.android.app":            "Twitch",
    "com.discord":                      "Discord",
    "com.picsart.studio":               "PicsArt",
    "com.google.android.apps.youtube.music": "YouTube Music",
    "com.cris.utsmobile":               "IRCTC/UTS",
    "com.google.android.apps.messaging":"Messages",
    "com.google.android.apps.nbu.paisa.user": "Google Pay",
}

SYSTEM_KEYWORDS = [
    "daemon","systemui","launcher3","wallpaper","inputmethod","keyboard",
    "locationfetcher","bluetooth","nfc","sensor","audiofx","drm","ims",
    "securityadd","qualcomm","mediatek","mtk",".wfd.",".rcs.",
    "packageinstaller","vending",
]


def _find_adb_exe():
    """Find adb executable - checks PATH then common Windows locations."""
    import shutil as _shutil, getpass as _gp
    found = _shutil.which("adb")
    if found:
        return found
    _win_paths = [
        r"C:\platform-tools\adb.exe",
        r"C:\Android\platform-tools\adb.exe",
        rf"C:\Users\{_gp.getuser()}\AppData\Local\Android\Sdk\platform-tools\adb.exe",
        r"C:\Program Files\Android\platform-tools\adb.exe",
        r"C:\Program Files (x86)\Android\platform-tools\adb.exe",
    ]
    for p in _win_paths:
        if os.path.isfile(p):
            return p
    return None


class ADB:
    def __init__(self, serial=None):
        adb_exe = _find_adb_exe() or "adb"
        self.base = [adb_exe] + (["-s", serial] if serial else [])
        self._found = _find_adb_exe() is not None

    def shell(self, cmd, timeout=25):
        try:
            r = subprocess.run(
                self.base + ["shell", cmd],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout
            )
            return (r.stdout or "").strip()
        except subprocess.TimeoutExpired:
            return ""
        except FileNotFoundError:
            print("[ERROR] adb not found in PATH or common locations.")
            return ""   # ← never sys.exit — would kill Flask thread
        except Exception:
            return ""

    def check_connected(self):
        try:
            adb_exe = _find_adb_exe() or "adb"
            out = subprocess.run([adb_exe, "devices"], capture_output=True,
                                 text=True, timeout=10).stdout
            return any("\tdevice" in l for l in out.split("\n")[1:])
        except Exception:
            return False


def is_user_app(pkg):
    if not pkg or "." not in pkg or len(pkg) < 6: return False
    p = pkg.lower()
    return not any(kw in p for kw in SYSTEM_KEYWORDS)


def get_installed(adb):
    raw = adb.shell("pm list packages -3 2>/dev/null", timeout=20)
    pkgs = set()
    for line in raw.split("\n"):
        m = re.match(r'package:([\w.]+)', line.strip())
        if m: pkgs.add(m.group(1))
    return pkgs


# ─────────────────────────────────────────────
#  BROADCAST-BASED APP DETECTION (Xiaomi-specific)
# ─────────────────────────────────────────────
def get_boot_uptime_sec(adb):
    """Read /proc/uptime for exact seconds since boot."""
    raw = adb.shell("cat /proc/uptime")
    try:
        return float(raw.split()[0])
    except Exception:
        # Fallback: estimate from broadcast timestamps (assume most recent = ~15h ago)
        return 9.5 * 86400

def parse_elapsed_ms(s):
    """'+8d9h12m43s351ms' → milliseconds since boot."""
    ms = 0
    for pat, mult in [(r'(\d+)d', 86400000), (r'(\d+)h', 3600000),
                      (r'(\d+)m(?!s)', 60000), (r'(\d+)s', 1000)]:
        m = re.search(pat, s)
        if m: ms += int(m.group(1)) * mult
    return ms

def collect_broadcast_apps(adb, uptime_sec, installed):
    """
    Parse broadcast timestamps from usagestats to find which apps
    were active in the last 24 hours.
    Returns dict: {pkg: hours_ago_last_active}
    """
    raw = adb.shell("dumpsys usagestats 2>/dev/null", timeout=40)
    if not raw:
        return {}

    now_sec     = time.time()
    boot_epoch  = now_sec - uptime_sec
    window_sec  = 24 * 3600

    app_last_active = {}
    current_pkg     = None

    for line in raw.split("\n"):
        s = line.strip()

        # Package header in broadcast section: "      com.whatsapp:"
        m_pkg = re.match(r'([\w.]{5,80}):\s*$', s)
        if m_pkg and "." in m_pkg.group(1):
            current_pkg = m_pkg.group(1)
            continue

        # BroadcastEvent with elapsed timestamp: "+8d9h12m43s351ms"
        if current_pkg and "BroadcastEvent" in s:
            # Extract all elapsed timestamps on this line and following lines
            for ts_str in re.findall(r'\+\d+[dhms][\d+dhms.]*', s):
                elapsed_ms  = parse_elapsed_ms(ts_str)
                event_epoch = boot_epoch + elapsed_ms / 1000
                hours_ago   = (now_sec - event_epoch) / 3600
                if hours_ago < 0: hours_ago = abs(hours_ago)

                if current_pkg not in app_last_active or \
                   hours_ago < app_last_active[current_pkg]:
                    app_last_active[current_pkg] = hours_ago

        # Inline timestamps: "+8d9h12m43s351ms,+8d9h13m0s"
        if current_pkg:
            for ts_str in re.findall(r'\+\d+[dhms][\d+dhms]*', s):
                elapsed_ms  = parse_elapsed_ms(ts_str)
                event_epoch = boot_epoch + elapsed_ms / 1000
                hours_ago   = (now_sec - event_epoch) / 3600
                if 0 <= hours_ago < 200:
                    if current_pkg not in app_last_active or \
                       hours_ago < app_last_active[current_pkg]:
                        app_last_active[current_pkg] = hours_ago

    return app_last_active

def get_currently_active(adb):
    """Get app(s) currently in foreground."""
    # Method 1: usagestats "Currently Active" field
    raw = adb.shell("dumpsys usagestats 2>/dev/null | grep 'Currently Active' | head -3")
    active = set()
    if raw:
        for m in re.finditer(r'([\w.]{5,80})', raw):
            if "." in m.group(1) and is_user_app(m.group(1)):
                active.add(m.group(1))
    # Method 2: activity top
    top = adb.shell("dumpsys activity top 2>/dev/null | grep 'ACTIVITY' | head -3")
    for m in re.finditer(r'ACTIVITY ([\w.]+)/', top):
        pkg = m.group(1)
        if is_user_app(pkg) and "launcher" not in pkg.lower():
            active.add(pkg)
    return active


# ─────────────────────────────────────────────
#  MAIN APP USAGE BUILDER
# ─────────────────────────────────────────────
def get_today_usage(adb):
    raw = adb.shell("dumpsys usagestats --daily 2>/dev/null", timeout=40)

    usage = {}
    current_pkg = None

    for line in raw.split("\n"):
        line = line.strip()

        # Package header
        m = re.match(r'Package (\S+):', line)
        if m:
            current_pkg = m.group(1)
            continue

        # totalTimeInForeground
        if current_pkg and "totalTimeInForeground" in line:
            m2 = re.search(r'(\d+)', line)
            if m2:
                ms = int(m2.group(1))
                if ms > 0:
                    usage[current_pkg] = ms / 60000  # convert to minutes

    return usage

# ─────────────────────────────────────────────
#  UNLOCKS
# ─────────────────────────────────────────────
def collect_unlocks(adb):
    raw = adb.shell(
        "logcat -d -b events 2>/dev/null | grep -c 'screen_toggled.*1' || echo 0",
        timeout=25
    )
    try:    count = int(raw.strip().split()[0])
    except: count = 0

    h_raw = adb.shell(
        "logcat -d -b events 2>/dev/null | grep 'screen_toggled.*1' | tail -500",
        timeout=25
    )
    hourly = {}
    for line in h_raw.split("\n"):
        m = re.search(r'\d{2}-\d{2} (\d{2}):\d{2}', line)
        if m:
            h = int(m.group(1))
            hourly[h] = hourly.get(h, 0) + 1

    late_night = sum(v for h, v in hourly.items() if h >= 23 or h <= 4)

    # Screen time estimate
    batt  = adb.shell("dumpsys batterystats 2>/dev/null | grep 'Screen on' | head -3")
    s_min = _parse_screen_time(batt)
    if s_min < 1 and count > 0:
        s_min = round(count * 8.5, 1)

    avg_session = round(s_min / count, 1) if count > 0 else 0

    return {
        "unlock_count_today":  count,
        "screen_on_minutes":   s_min,
        "late_night_unlocks":  late_night,
        "avg_session_min":     avg_session,
        "peak_unlock_hour":    max(hourly, key=hourly.get) if hourly else 0,
        "hourly_distribution": hourly,
    }

def _parse_screen_time(raw):
    for p in [r'(\d+)h\s*(\d+)m\s*(\d+)s', r'(\d+)m\s*(\d+)s', r'(\d+)h']:
        m = re.search(p, raw)
        if m:
            nums = [int(x) for x in m.groups() if x]
            if len(nums)==3: return nums[0]*60+nums[1]+nums[2]/60
            if len(nums)==2: return nums[0]+nums[1]/60
            if len(nums)==1: return float(nums[0])*60
    return 0.0


# ─────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────
def collect_notifications(adb):
    counts = {}
    raw = adb.shell("dumpsys notification --stats 2>/dev/null | head -500", timeout=25)
    for line in raw.split("\n"):
        m = re.search(r'([\w.]{5,80})\s+\|?\s*(\d+)', line)
        if m and "." in m.group(1) and is_user_app(m.group(1)):
            try:
                p, n = m.group(1).strip(), int(m.group(2))
                if n > 0: counts[p] = counts.get(p, 0) + n
            except ValueError:
                pass

    if not counts:
        raw2 = adb.shell("dumpsys notification --noredact 2>/dev/null | grep 'pkg=' | head -300")
        for line in raw2.split("\n"):
            m = re.search(r'pkg=(\S+)', line)
            if m and is_user_app(m.group(1)):
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1

    total  = sum(counts.values())
    top10  = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    social = sum(v for k, v in counts.items() if k in HIGH_RISK_PKGS)

    return {
        "total_notifications":        total,
        "per_app":                    dict(top10),
        "social_media_notifications": social,
        "social_notif_pct":          round(social/total*100, 1) if total else 0,
        "most_notified_app":          APP_NAMES.get(top10[0][0], top10[0][0]) if top10 else "None",
    }


# ─────────────────────────────────────────────
#  BROWSER
# ─────────────────────────────────────────────
def collect_browser(adb):
    CHROME_DB = "/data/data/com.android.chrome/app_chrome/Default/History"
    BACKUP    = "/sdcard/screensense_chrome.db"
    LOCAL     = str(DATA_DIR / "chrome_tmp.db")

    adb.shell(f"cp '{CHROME_DB}' '{BACKUP}' 2>/dev/null")
    time.sleep(0.3)
    subprocess.run(["adb","pull",BACKUP,LOCAL], capture_output=True, timeout=15)

    history = []
    if os.path.exists(LOCAL):
        try:
            import sqlite3
            conn = sqlite3.connect(LOCAL); cur = conn.cursor()
            since = int((time.time()-86400)*1_000_000 + 11644473600*1_000_000)
            cur.execute("SELECT url,title,visit_count,"
                        "(last_visit_time/1000000-11644473600) as ts "
                        "FROM urls WHERE last_visit_time>? "
                        "ORDER BY last_visit_time DESC LIMIT 200", (since,))
            history = [{"url":r[0],"title":r[1]or"","visits":r[2]or 1,"timestamp":r[3]}
                       for r in cur.fetchall()]
            conn.close()
        except Exception as e:
            print(f"    Chrome: {e}")
        try: os.remove(LOCAL)
        except: pass
    adb.shell(f"rm '{BACKUP}' 2>/dev/null")

    if not history:
        return {"total_urls_visited":0,"categories":{},"category_percentages":{},
                "top_domains":[],"late_night_browsing":0,"social_media_browsing_pct":0}

    cat_map = {
        "social_media":    ["instagram","facebook","twitter","snapchat","tiktok","reddit","x.com"],
        "video_streaming": ["youtube","netflix","hotstar","primevideo","twitch"],
        "news":            ["bbc","cnn","ndtv","thehindu","reuters"],
        "shopping":        ["amazon","flipkart","myntra","meesho"],
        "gaming":          ["game","steam"],
        "productivity":    ["docs.google","github","stackoverflow","notion"],
    }
    cats={k:0 for k in cat_map}; cats["other"]=0
    doms={}; late=0
    for e in history:
        url=e.get("url","").lower()
        dom=re.sub(r'https?://(www\.)?','',url).split('/')[0]
        doms[dom]=doms.get(dom,0)+e.get("visits",1)
        hit=False
        for c,kws in cat_map.items():
            if any(k in url for k in kws): cats[c]+=1; hit=True; break
        if not hit: cats["other"]+=1
        try:
            h=datetime.datetime.fromtimestamp(e.get("timestamp",0)).hour
            if h>=23 or h<=4: late+=1
        except: pass
    tot=sum(cats.values()) or 1
    top10=sorted(doms.items(),key=lambda x:x[1],reverse=True)[:10]
    return {
        "total_urls_visited":        len(history),
        "categories":                cats,
        "category_percentages":      {k:round(v/tot*100,1) for k,v in cats.items()},
        "top_domains":               [{"domain":d,"visits":v} for d,v in top10],
        "late_night_browsing":       late,
        "social_media_browsing_pct": round(cats["social_media"]/tot*100,1),
    }


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def _app(pkg, mins, launches):
    return {
        "package":        pkg,
        "app_name":       APP_NAMES.get(pkg, pkg.split(".")[-1].replace("_"," ").title()),
        "total_time_min": mins,
        "total_time_hr":  round(mins/60, 2),
        "total_time_ms":  int(mins*60000),
        "launch_count":   launches,
        "is_social":      pkg in HIGH_RISK_PKGS,
        "risk_level":     ("critical" if mins>120 else "high" if mins>60 else "medium")
                          if pkg in HIGH_RISK_PKGS else
                          ("high" if mins>180 else "medium" if mins>90 else "low"),
    }

# ─────────────────────────────────────────────
#  BUILD APP USAGE (Hybrid for Xiaomi)
# ─────────────────────────────────────────────
def build_app_usage(adb, installed, notif_counts, unlock_count):
    """
    Xiaomi HyperOS does NOT provide totalTimeInForeground.
    So we combine:
        - Broadcast last-active timestamps
        - Currently active app
        - Notification weighting
    """

    uptime_sec = get_boot_uptime_sec(adb)
    broadcast_data = collect_broadcast_apps(adb, uptime_sec, installed)
    active_now = get_currently_active(adb)

    apps = []

    for pkg, hours_ago in broadcast_data.items():
        if pkg not in installed:
            continue
        if not is_user_app(pkg):
            continue

        # Basic scoring logic
        score_min = 0

        # Recently active = more minutes
        if hours_ago <= 1:
            score_min += 45
        elif hours_ago <= 3:
            score_min += 25
        elif hours_ago <= 6:
            score_min += 15
        elif hours_ago <= 12:
            score_min += 8
        else:
            score_min += 3

        # Currently active boost
        if pkg in active_now:
            score_min += 20

        # Notification weighting
        notif_boost = notif_counts.get(pkg, 0) * 1.5
        score_min += notif_boost

        if score_min > 0:
            apps.append(_app(pkg, round(score_min, 1), 0))

    # Sort by estimated minutes
    apps.sort(key=lambda x: x["total_time_min"], reverse=True)

    return apps
# ─────────────────────────────────────────────
#  MAIN COLLECTOR
# ─────────────────────────────────────────────
class Collector:
    def __init__(self, uid, serial=None):
        self.uid, self.adb = uid, ADB(serial)

    def collect_all(self, hours=24):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        print(f"\n{'═'*55}")
        print(f"  ScreenSense AI  |  {hours}h  |  v7")
        print(f"{'═'*55}\n")

        print("📱 Device...")
        dev = {
            "model":       self.adb.shell("getprop ro.product.model"),
            "manufacturer":self.adb.shell("getprop ro.product.manufacturer"),
            "android_ver": self.adb.shell("getprop ro.build.version.release"),
            "sdk":         self.adb.shell("getprop ro.build.version.sdk"),
            "device_id":   self.adb.shell("settings get secure android_id"),
            "miui":        self.adb.shell("getprop ro.miui.ui.version.name"),
        }
        print(f"   {dev['manufacturer']} {dev['model']}  Android {dev['android_ver']}  {dev['miui']}")

        print("\n📦 Installed apps...")
        installed = get_installed(self.adb)
        social_inst = [APP_NAMES.get(p,p.split(".")[-1]) for p in HIGH_RISK_PKGS if p in installed]
        print(f"   {len(installed)} total  |  Social: {', '.join(social_inst) or 'none'}")

        print("\n🔔 Notifications...")
        notifs = collect_notifications(self.adb)
        print(f"   {notifs['total_notifications']} total  |  {notifs['social_media_notifications']} social")

        print("\n🔓 Unlocks...")
        unlocks = collect_unlocks(self.adb)
        print(f"   {unlocks['unlock_count_today']} unlocks  |  ~{unlocks['screen_on_minutes']:.0f} min screen-on")

        print("\n📊 App usage (broadcast + foreground analysis)...")
        apps = build_app_usage(
            self.adb, installed,
            notifs.get("per_app", {}),
            unlocks["unlock_count_today"]
        )

        print("\n🌐 Browser...")
        browser = collect_browser(self.adb)
        print(f"   {browser['total_urls_visited']} URLs")

        total_min  = sum(a.get("total_time_min", 0) for a in apps)
        social_min = sum(a.get("total_time_min", 0) for a in apps if a.get("is_social"))
        if total_min < 1:
            total_min = unlocks["screen_on_minutes"]

        top_app = apps[0] if apps else {}
        summary = {
            "total_screen_time_min": round(total_min, 2),
            "total_screen_time_hr":  round(total_min/60, 2),
            "social_media_min":      round(social_min, 2),
            "social_media_pct":      round(social_min/total_min*100, 1) if total_min else 0,
            "top_app":               top_app.get("app_name", "Unknown"),
            "top_app_min":           top_app.get("total_time_min", 0),
            "apps_used_count":       len(apps),
            "unlock_count":          unlocks["unlock_count_today"],
            "late_night_unlocks":    unlocks["late_night_unlocks"],
            "avg_session_min":       unlocks["avg_session_min"],
            "total_notifications":   notifs["total_notifications"],
            "social_notifications":  notifs["social_media_notifications"],
            "browser_social_pct":    browser.get("social_media_browsing_pct", 0),
            "late_night_browsing":   browser.get("late_night_browsing", 0),
            "data_method":           "broadcast_timestamps",
        }

        print(f"\n{'═'*54}")
        print(f"  📊 RESULT")
        print(f"{'═'*54}")
        print(f"  Screen Time  : {summary['total_screen_time_hr']:.1f} hrs  ({summary['total_screen_time_min']:.0f} min)")
        print(f"  Social Media : {summary['social_media_min']:.0f} min  ({summary['social_media_pct']:.0f}%)")
        print(f"  Top App      : {summary['top_app']}  ({summary['top_app_min']:.0f} min)")
        print(f"  Unlocks      : {summary['unlock_count']}")
        print(f"  Notifications: {summary['total_notifications']}")
        if apps:
            print(f"\n  ┌─ App Usage ({'broadcast-based':^30})──┐")
            for a in apps[:8]:
                bar  = "▓" * min(int(a["total_time_min"]/8), 20)
                flag = " 🔴" if a["is_social"] else ""
                print(f"  │ {a['app_name']:<24} {a['total_time_min']:5.0f} min  {bar}{flag}")
            print(f"  └{'─'*47}┘")
        print(f"{'═'*54}\n")

        return {
            "uid":self.uid, "collected_at":ts, "period_hours":hours,
            "device":dev, "summary":summary,
            "app_usage":apps[:20], "browser":browser,
            "unlocks":unlocks, "notifications":notifs,
        }

    def send(self, payload):
        try:
            print("🚀 Sending to Flask...")
            r = requests.post(f"{FLASK_SERVER}/api/analyze", json=payload, timeout=30)
            if r.status_code == 200:
                res = r.json()
                print(f"\n{'═'*48}")
                print(f"  ✅ ML RESULT")
                print(f"{'═'*48}")
                print(f"  Score      : {res.get('risk_score','?')} / 100")
                print(f"  Level      : {res.get('prediction_label','?')}")
                print(f"  Confidence : {res.get('confidence','?')}%")
                for w in res.get("warnings",[])[:4]:
                    print(f"  ⚠️  {w}")
                print(f"  Firestore  : ✓ saved")
                print(f"{'═'*48}\n")
                return res
            print(f"[ERROR] {r.status_code}")
            return {}
        except requests.ConnectionError:
            print("[ERROR] Flask offline. Run: py app.py")
            return {}

    def save_json(self, p):
        fn = DATA_DIR/f"data_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fn,"w") as f: json.dump(p,f,indent=2,default=str)
        print(f"💾 {fn}")


def do_setup(adb):
    print("\n" + "═"*55 + "\n  🔧 SETUP\n" + "═"*55)
    for cmd in [
        "appops set android GET_USAGE_STATS allow",
        "appops set com.android.shell GET_USAGE_STATS allow",
        "pm grant android android.permission.DUMP 2>/dev/null",
    ]:
        print(f"  {adb.shell(cmd) or 'OK'}")
    print("═"*55)


def generate_demo_payload(uid):
    import random; random.seed(int(time.time())%999)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    apps = [
        _app("cc.honista.app",         142, 28),
        _app("org.telegram.messenger",  95, 35),
        _app("com.whatsapp",            72, 48),
        _app("com.snapchat.android",    58, 22),
        _app("com.linkedin.android",    35, 12),
        _app("com.spotify.music",       28,  6),
        _app("com.netflix.mediaclient", 22,  3),
    ]
    total=sum(a["total_time_min"] for a in apps)
    social=sum(a["total_time_min"] for a in apps if a["is_social"])
    ul=random.randint(80,130)
    return {
        "uid":uid,"collected_at":ts,"period_hours":24,"demo_mode":True,
        "device":{"model":"Xiaomi 22021211RI","manufacturer":"Xiaomi",
                  "android_ver":"14","sdk":"34","miui":"V816"},
        "summary":{"total_screen_time_min":total,"total_screen_time_hr":round(total/60,2),
                   "social_media_min":social,"social_media_pct":round(social/total*100,1),
                   "top_app":"Instagram (Honista)","top_app_min":142,"apps_used_count":len(apps),
                   "unlock_count":ul,"late_night_unlocks":random.randint(5,25),
                   "avg_session_min":round(total/ul,1),
                   "total_notifications":random.randint(60,150),
                   "social_notifications":random.randint(30,90),
                   "browser_social_pct":random.uniform(20,55),"late_night_browsing":random.randint(5,20)},
        "app_usage":apps,
        "browser":{"total_urls_visited":55,"social_media_browsing_pct":38.0,"late_night_browsing":6,
                   "top_domains":[{"domain":"honista.app","visits":18},
                                  {"domain":"telegram.org","visits":12}]},
        "unlocks":{"unlock_count_today":ul,"screen_on_minutes":total,"late_night_unlocks":14},
        "notifications":{"total_notifications":120,"social_media_notifications":72,"social_notif_pct":60.0},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode",    choices=["adb","demo"], default="demo")
    p.add_argument("--uid",     default="demo_user")
    p.add_argument("--serial",  default=None)
    p.add_argument("--hours",   type=int, default=24)
    p.add_argument("--loop",    action="store_true")
    p.add_argument("--save",    action="store_true")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--setup",   action="store_true")
    args = p.parse_args()

    print("""
╔══════════════════════════════════════════╗
║  📱 ScreenSense AI — Data Collector     ║
╚══════════════════════════════════════════╝""")

    adb = ADB(args.serial)
    if args.setup:
        if not adb.check_connected(): print("[ERROR] No phone."); sys.exit(1)
        do_setup(adb); return

    def once():
        if args.mode == "demo":
            print("  [DEMO]\n"); payload = generate_demo_payload(args.uid)
        elif not adb.check_connected():
            print("[WARN] Phone not found, using DEMO.\n")
            payload = generate_demo_payload(args.uid)
        else:
            payload = Collector(args.uid, args.serial).collect_all(args.hours)
        c = Collector(args.uid, args.serial)
        if args.save:        c.save_json(payload)
        if not args.no_send: c.send(payload)

    if args.loop:
        while True:
            try:
                once(); print("  💤 Sleeping...\n"); time.sleep(COLLECT_INTERVAL)
            except KeyboardInterrupt:
                print("\n  Stopped."); break
    else:
        once()

if __name__ == "__main__":
    main()