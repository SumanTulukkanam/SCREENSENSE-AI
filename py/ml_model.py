"""
╔══════════════════════════════════════════════════════════════╗
║   ScreenSense AI — ML Model v2 (Xiaomi-Aware)               ║
║                                                              ║
║   Key fix: Xiaomi HyperOS blocks late_night_unlocks,        ║
║   notifications, and browser data → all come in as 0.       ║
║   Old model saw these zeros and gave LOW risk despite        ║
║   12h screen time + 89% social usage.                       ║
║                                                              ║
║   Fix 1: Impute missing features from available ones        ║
║   Fix 2: Rule-based override for obvious high-risk patterns ║
║   Fix 3: Retrain with Xiaomi-realistic (zero-notification)  ║
║           samples mixed into training set                    ║
╚══════════════════════════════════════════════════════════════╝
"""

import numpy as np
import pandas as pd
import pickle
import json
import argparse
from pathlib import Path
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, accuracy_score
from sklearn.pipeline import Pipeline

MODEL_DIR  = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODEL_DIR / "screensense_rf_model.pkl"
META_PATH  = MODEL_DIR / "model_meta.json"

FEATURE_NAMES = [
    "total_screen_time_hr", "social_media_hr", "social_media_pct",
    "top_app_hr", "apps_used_count", "unlock_count", "late_night_unlocks",
    "avg_session_min", "total_notifications", "social_notifications",
    "social_notif_pct", "browser_social_pct", "late_night_browsing",
    "unlock_per_hour", "social_unlock_ratio", "notification_response_rate",
]

RISK_CLASSES  = ["minimal", "low", "moderate", "high"]
RISK_LABELS   = {0: "Minimal Risk", 1: "Low Risk", 2: "Moderate Risk", 3: "High Risk"}
RISK_COLORS   = {0: "#00e5a0", 1: "#6c63ff", 2: "#ffb347", 3: "#ff4f6d"}
RISK_SCORE_RANGE = {0: (0, 25), 1: (25, 50), 2: (50, 75), 3: (75, 100)}


def generate_training_data(n_samples: int = 5000) -> pd.DataFrame:
    np.random.seed(42)
    rows = []

    def make_sample(risk_class: int, xiaomi_mode: bool = False) -> dict:
        """
        xiaomi_mode=True generates samples where notification/browser
        features are 0 (simulating Xiaomi HyperOS data collection).
        This teaches the model to classify correctly without those features.
        """
        if risk_class == 0:
            screen_hr      = np.random.uniform(1, 3)
            social_pct     = np.random.uniform(5, 20)
            unlocks        = np.random.randint(10, 35)
            late_unlocks   = np.random.randint(0, 3)
            notifications  = np.random.randint(10, 50)
            browser_social = np.random.uniform(5, 20)
            late_browse    = np.random.randint(0, 3)
        elif risk_class == 1:
            screen_hr      = np.random.uniform(2.5, 5)
            social_pct     = np.random.uniform(15, 35)
            unlocks        = np.random.randint(30, 65)
            late_unlocks   = np.random.randint(2, 8)
            notifications  = np.random.randint(40, 100)
            browser_social = np.random.uniform(15, 35)
            late_browse    = np.random.randint(2, 8)
        elif risk_class == 2:
            screen_hr      = np.random.uniform(4.5, 8)
            social_pct     = np.random.uniform(30, 55)
            unlocks        = np.random.randint(60, 110)
            late_unlocks   = np.random.randint(7, 20)
            notifications  = np.random.randint(90, 200)
            browser_social = np.random.uniform(30, 55)
            late_browse    = np.random.randint(8, 20)
        else:
            screen_hr      = np.random.uniform(7, 14)
            social_pct     = np.random.uniform(50, 90)
            unlocks        = np.random.randint(100, 220)
            late_unlocks   = np.random.randint(18, 60)
            notifications  = np.random.randint(180, 400)
            browser_social = np.random.uniform(50, 85)
            late_browse    = np.random.randint(18, 45)

        social_hr     = screen_hr * (social_pct / 100)
        top_app_hr    = social_hr * np.random.uniform(0.4, 0.7)
        apps_count    = np.random.randint(3, 25)
        avg_session   = (screen_hr * 60) / max(unlocks, 1)
        soc_notif     = int(notifications * (social_pct / 100))
        soc_notif_pct = (soc_notif / max(notifications, 1)) * 100

        # Xiaomi mode: zero out blocked features
        if xiaomi_mode:
            late_unlocks   = 0
            notifications  = np.random.randint(5, 25)   # only system notifs
            soc_notif      = 0
            soc_notif_pct  = 0.0
            browser_social = 0.0
            late_browse    = 0

        unlock_per_hr    = unlocks / max(screen_hr, 0.1)
        soc_unlock_ratio = late_unlocks / max(unlocks, 1)
        notif_resp_rate  = notifications / max(unlocks, 1)

        return {
            "total_screen_time_hr":      round(screen_hr, 2),
            "social_media_hr":           round(social_hr, 2),
            "social_media_pct":          round(social_pct, 1),
            "top_app_hr":                round(top_app_hr, 2),
            "apps_used_count":           apps_count,
            "unlock_count":              unlocks,
            "late_night_unlocks":        late_unlocks,
            "avg_session_min":           round(avg_session, 2),
            "total_notifications":       notifications,
            "social_notifications":      soc_notif,
            "social_notif_pct":          round(soc_notif_pct, 1),
            "browser_social_pct":        round(browser_social, 1),
            "late_night_browsing":       late_browse,
            "unlock_per_hour":           round(unlock_per_hr, 2),
            "social_unlock_ratio":       round(soc_unlock_ratio, 3),
            "notification_response_rate":round(notif_resp_rate, 2),
            "risk_class":                risk_class,
        }

    # Standard samples (60%)
    distribution = [0]*800 + [1]*1200 + [2]*1200 + [3]*800
    np.random.shuffle(distribution)
    for rc in distribution:
        rows.append(make_sample(rc))

    # Xiaomi-style samples (40%) — same risk classes but with zeroed features
    # This is the KEY addition: teaches model to classify on screen_time + social_pct + unlocks alone
    xiaomi_dist = [0]*400 + [1]*600 + [2]*600 + [3]*400
    np.random.shuffle(xiaomi_dist)
    for rc in xiaomi_dist:
        rows.append(make_sample(rc, xiaomi_mode=True))

    df = pd.DataFrame(rows)
    print(f"  Generated {len(df)} training samples ({len(xiaomi_dist)} Xiaomi-style)")
    print(f"  Class distribution:\n{df['risk_class'].value_counts().sort_index()}")
    return df


def impute_missing_features(s: dict) -> dict:
    """
    Impute blocked/zero features from available data.
    Called before feature extraction to fill in realistic estimates.
    """
    s = dict(s)  # don't modify original

    total_hr   = s.get("total_screen_time_hr", 0)
    unlocks    = s.get("unlock_count", 1)
    social_pct = s.get("social_media_pct", 0)

    # Estimate late-night unlocks if zero but unlocks is high
    # (about 15% of unlocks happen late-night on average)
    if s.get("late_night_unlocks", 0) == 0 and unlocks > 50:
        s["late_night_unlocks"] = int(unlocks * 0.12)

    # Estimate social notifications from social_pct if zero
    # (social apps generate ~1 notif per 3 min of usage on average)
    if s.get("social_notifications", 0) == 0 and social_pct > 20:
        social_min = s.get("social_media_min", total_hr * 60 * social_pct / 100)
        s["social_notifications"] = int(social_min / 3)
        s["total_notifications"]  = max(
            s.get("total_notifications", 0),
            s["social_notifications"] + int(unlocks * 0.2)
        )

    # Estimate browser social from social_pct
    if s.get("browser_social_pct", 0) == 0 and social_pct > 20:
        s["browser_social_pct"] = social_pct * 0.4  # browsing mirrors ~40% of app usage

    # Estimate late-night browsing
    if s.get("late_night_browsing", 0) == 0 and s.get("late_night_unlocks", 0) > 5:
        s["late_night_browsing"] = max(3, int(s["late_night_unlocks"] * 0.5))

    return s


def extract_features(payload_summary: dict) -> np.ndarray:
    s = impute_missing_features(payload_summary)
    total_hr = s.get("total_screen_time_hr", 0)
    unlocks  = s.get("unlock_count", 1)
    notifs   = s.get("total_notifications", 0)
    late_ul  = s.get("late_night_unlocks", 0)

    features = [
        total_hr,
        s.get("social_media_min", 0) / 60,
        s.get("social_media_pct", 0),
        s.get("top_app_min", 0) / 60,
        s.get("apps_used_count", 0),
        unlocks,
        late_ul,
        s.get("avg_session_min", 0),
        notifs,
        s.get("social_notifications", 0),
        (s.get("social_notifications", 0) / max(notifs, 1)) * 100,
        s.get("browser_social_pct", 0),
        s.get("late_night_browsing", 0),
        unlocks / max(total_hr, 0.1),
        late_ul / max(unlocks, 1),
        notifs / max(unlocks, 1),
    ]
    return np.array(features).reshape(1, -1)


def rule_based_override(s: dict, ml_class: int, ml_score: int) -> tuple[int, int]:
    """
    Override ML prediction with hard rules for clear-cut cases.
    Prevents Xiaomi zero-feature issue from under-classifying obvious risks.
    """
    total_hr   = s.get("total_screen_time_hr", 0)
    social_pct = s.get("social_media_pct", 0)
    unlocks    = s.get("unlock_count", 0)

    # Hard HIGH RISK conditions
    if total_hr >= 10 and social_pct >= 70:
        return 3, max(ml_score, 82)
    if total_hr >= 8 and social_pct >= 60 and unlocks >= 100:
        return 3, max(ml_score, 76)
    if total_hr >= 10:
        return 3, max(ml_score, 75)

    # Hard MODERATE RISK conditions
    if total_hr >= 6 and social_pct >= 50:
        new_class = max(ml_class, 2)
        return new_class, max(ml_score, 58) if new_class == 2 else ml_score
    if total_hr >= 7 and unlocks >= 90:
        new_class = max(ml_class, 2)
        return new_class, max(ml_score, 55) if new_class == 2 else ml_score
    if unlocks >= 150:
        new_class = max(ml_class, 2)
        return new_class, max(ml_score, 52) if new_class == 2 else ml_score

    # Hard MINIMAL RISK conditions (prevent over-classifying)
    if total_hr <= 2 and unlocks <= 30 and social_pct <= 20:
        return 0, min(ml_score, 22)

    return ml_class, ml_score


def train_model(n_samples: int = 5000, verbose: bool = True):
    print("\n" + "="*55)
    print("  ScreenSense AI — Model Training v2 (Xiaomi-Aware)")
    print("="*55)

    print("\n📊 Generating training data...")
    df = generate_training_data(n_samples)

    X = df[FEATURE_NAMES].values
    y = df["risk_class"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"  Train: {len(X_train)} | Test: {len(X_test)}")

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=200, max_depth=12,
            min_samples_split=5, min_samples_leaf=2,
            max_features="sqrt", class_weight="balanced",
            random_state=42, n_jobs=-1,
        ))
    ])

    print("\n🔄 Cross-validation...")
    cv = cross_val_score(pipeline, X_train, y_train, cv=5, scoring="accuracy")
    print(f"  CV Accuracy: {cv.mean():.3f} ± {cv.std():.3f}")

    print("\n🚀 Training...")
    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"  Test Accuracy: {acc:.4f}")
    print(classification_report(y_test, y_pred, target_names=RISK_CLASSES))

    rf = pipeline.named_steps["clf"]
    feat_imp = sorted(zip(FEATURE_NAMES, rf.feature_importances_),
                      key=lambda x: x[1], reverse=True)
    print("  Top 8 Features:")
    for name, imp in feat_imp[:8]:
        print(f"    {name:<35} {imp:.4f} {'█'*int(imp*40)}")

    with open(MODEL_PATH, "wb") as f: pickle.dump(pipeline, f)
    meta = {
        "trained_at": datetime.now().isoformat(),
        "n_samples": n_samples, "n_features": len(FEATURE_NAMES),
        "feature_names": FEATURE_NAMES, "risk_classes": RISK_CLASSES,
        "risk_labels": RISK_LABELS, "cv_accuracy": float(cv.mean()),
        "test_accuracy": float(acc),
        "feature_importances": {k: float(v) for k,v in feat_imp},
    }
    with open(META_PATH, "w") as f: json.dump(meta, f, indent=2)
    print(f"\n✅ Model saved → {MODEL_PATH}")
    return pipeline, meta


def load_model():
    if not MODEL_PATH.exists():
        print("[INFO] No model found. Training now...")
        pipeline, _ = train_model(verbose=False)
        return pipeline
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def predict_risk(payload_summary: dict) -> dict:
    model    = load_model()
    features = extract_features(payload_summary)

    ml_class    = int(model.predict(features)[0])
    proba       = model.predict_proba(features)[0]
    lo, hi      = RISK_SCORE_RANGE[ml_class]
    class_proba = float(proba[ml_class])
    ml_score    = int(lo + (hi - lo) * class_proba)
    confidence  = round(float(max(proba)) * 100, 1)

    # Apply rule-based override for Xiaomi zero-feature cases
    risk_class, risk_score = rule_based_override(payload_summary, ml_class, ml_score)

    warnings         = generate_warnings(payload_summary, risk_class)
    app_risks        = compute_app_risks(payload_summary)
    forecast         = generate_forecast(payload_summary, risk_score)
    recommendations  = generate_recommendations(payload_summary, risk_class)

    return {
        "risk_class":       risk_class,
        "risk_level":       RISK_CLASSES[risk_class],
        "prediction_label": RISK_LABELS[risk_class],
        "risk_score":       risk_score,
        "risk_color":       RISK_COLORS[risk_class],
        "confidence":       confidence,
        "probabilities":    {RISK_CLASSES[i]: round(float(p)*100,1) for i,p in enumerate(proba)},
        "top_warning":      warnings[0] if warnings else "Usage within normal range.",
        "warnings":         warnings,
        "app_risks":        app_risks,
        "forecast":         forecast,
        "recommendations":  recommendations,
        "feature_values":   {name: float(features[0][i]) for i,name in enumerate(FEATURE_NAMES)},
    }


def generate_warnings(s: dict, risk_class: int) -> list:
    w = []
    if s.get("total_screen_time_hr", 0) > 6:
        w.append(f"⚠️ Screen time {s['total_screen_time_hr']:.1f}h exceeds healthy 6h daily limit.")
    if s.get("social_media_pct", 0) > 40:
        w.append(f"📱 Social media is {s['social_media_pct']:.0f}% of your screen time — addiction risk.")
    if s.get("unlock_count", 0) > 100:
        w.append(f"🔓 {s['unlock_count']} phone unlocks today — compulsive checking detected.")
    if s.get("late_night_unlocks", 0) > 10:
        w.append(f"🌙 {s['late_night_unlocks']} late-night unlocks — disrupting sleep.")
    if s.get("social_notifications", 0) > 100:
        w.append(f"🔔 {s['social_notifications']} social notifications — attention fragmentation risk.")
    if s.get("avg_session_min", 0) > 45:
        w.append(f"⏱️ Avg session {s['avg_session_min']:.0f} min — long unbroken usage periods.")
    if not w and risk_class >= 2:
        w.append("⚠️ Overall usage pattern indicates elevated digital addiction risk.")
    return w[:5]


def compute_app_risks(payload: dict) -> list:
    HIGH = {"com.instagram.android","cc.honista.app","com.facebook.katana",
            "com.snapchat.android","com.twitter.android","com.tiktok.android",
            "com.google.android.youtube","org.telegram.messenger","com.whatsapp"}
    results = []
    for app in payload.get("app_usage", [])[:10]:
        mins = app.get("total_time_min", 0)
        pkg  = app.get("package", "")
        risk = "high"   if pkg in HIGH and mins > 60 else \
               "medium" if pkg in HIGH or mins > 90 else "low"
        results.append({
            "app_name":   app.get("app_name", pkg),
            "package":    pkg,
            "minutes":    round(mins, 1),
            "risk_level": risk,
            "is_social":  app.get("is_social", False),
        })
    return results


def generate_forecast(s: dict, current_score: int) -> list:
    import random; random.seed(42)
    base = s.get("total_screen_time_hr", 6)
    days = ["Tomorrow","Day 3","Day 4","Day 5","Day 6","Day 7","Day 8"]
    return [{
        "day":             d,
        "predicted_hr":    round(max(0.5, base + random.uniform(-0.8, 0.8)), 1),
        "predicted_score": max(0, min(100, current_score + int(random.uniform(-4,4)))),
        "trend":           "↑" if random.uniform(-1,1)>0.2 else "↓" if random.uniform(-1,1)<-0.2 else "→",
    } for d in days]


def generate_recommendations(s: dict, risk_class: int) -> list:
    recs = []
    if s.get("social_media_pct", 0) > 35:
        recs.append("Set a 1-hour daily limit for social apps in Digital Wellbeing settings.")
    if s.get("unlock_count", 0) > 80:
        recs.append("Turn on Focus Mode to reduce compulsive phone checking.")
    if s.get("late_night_unlocks", 0) > 8:
        recs.append("Enable Bedtime Mode at 10 PM to protect sleep quality.")
    if s.get("total_screen_time_hr", 0) > 6:
        recs.append("Schedule screen-free hours (e.g., 7–9 AM and 9–11 PM).")
    if risk_class >= 3:
        recs.append("Consider a 24-hour digital detox — research shows significant wellbeing benefits.")
    recs.append("Take a 5-minute break every 30 minutes of screen use.")
    return recs[:5]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train",   action="store_true")
    parser.add_argument("--test",    action="store_true")
    parser.add_argument("--samples", type=int, default=5000)
    args = parser.parse_args()

    if args.train or not MODEL_PATH.exists():
        train_model(args.samples)

    if args.test:
        print("\n" + "="*55 + "\n  🧪 TEST — Xiaomi real data\n" + "="*55)
        # Actual values from collection run
        real = {
            "total_screen_time_hr": 12.0, "social_media_min": 641,
            "social_media_pct": 89.0, "top_app_min": 113, "apps_used_count": 8,
            "unlock_count": 121, "late_night_unlocks": 0, "avg_session_min": 8.5,
            "total_notifications": 18, "social_notifications": 0,
            "browser_social_pct": 0, "late_night_browsing": 0,
        }
        result = predict_risk(real)
        print(f"  Risk Score : {result['risk_score']} / 100")
        print(f"  Risk Level : {result['prediction_label']}")
        print(f"  Confidence : {result['confidence']}%")
        print(f"\n  Imputed features:")
        s = impute_missing_features(real)
        print(f"    late_night_unlocks:  {s['late_night_unlocks']}")
        print(f"    social_notifications:{s['social_notifications']}")
        print(f"    browser_social_pct:  {s['browser_social_pct']:.1f}")
        print(f"\n  Warnings:")
        for w in result["warnings"]: print(f"    {w}")

if __name__ == "__main__":
    main()