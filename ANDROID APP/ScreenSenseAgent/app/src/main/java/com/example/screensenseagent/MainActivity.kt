package com.example.screensenseagent

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.screensenseagent.ui.theme.ScreenSenseAgentTheme
import com.google.firebase.auth.FirebaseAuth
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.*
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {

    private val auth = FirebaseAuth.getInstance()

    // ── CHANGE THIS to your PC's WiFi IP ─────────────────────────
    private val FLASK_URL = "http://172.20.10.2:5000"

    // System/noise packages — always excluded from results
    private val EXCLUDED_PKGS = setOf(
        "com.android.systemui",
        "com.android.launcher3",
        "com.miui.home",
        "com.mi.android.globallauncher",
        "com.android.settings",
        "com.miui.securitycenter",
        "com.google.android.packageinstaller",
        "com.miui.global.packageinstaller",
        "com.android.packageinstaller",
        "com.google.android.gms",
        "com.google.android.gsf",
        "com.android.phone",
        "com.miui.core",
        "com.miui.daemon",
        "com.xiaomi.xmsf",
        "android",
        "com.android.inputmethod.latin",
        "com.sohu.inputmethod.sogou",
        "com.baidu.input",
        "com.miui.miwallpaper",
        "com.miui.aod",
        "com.miui.keyguard",
        "com.miui.powerkeeper",
        "com.miui.system"
    )

    private val SOCIAL_KEYWORDS = listOf(
        "instagram", "facebook", "twitter", "tiktok", "snapchat",
        "whatsapp", "telegram", "linkedin", "reddit", "pinterest",
        "youtube", "honista", "threads", "discord", "sharechat",
        "moj", "josh", "roposo", "x.android", "hike"
    )

    // ─────────────────────────────────────────────────────────────
    // UI state
    // ─────────────────────────────────────────────────────────────
    private var statusText = mutableStateOf("Tap 'Collect & Send' to read usage data")
    private var isCollecting = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (auth.currentUser == null) {
            startActivity(Intent(this, AuthActivity::class.java))
            finish()
            return
        }

        enableEdgeToEdge()

        setContent {
            ScreenSenseAgentTheme {
                val status by statusText
                val collecting by isCollecting

                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(innerPadding)
                            .padding(24.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            "ScreenSense AI",
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Bold,
                            color = Color(0xFF6C63FF)
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        Text(
                            status,
                            fontSize = 13.sp,
                            color = Color(0xFF8888AA),
                            modifier = Modifier.padding(horizontal = 16.dp)
                        )

                        Spacer(modifier = Modifier.height(32.dp))

                        // Grant Usage Access
                        Button(
                            onClick = {
                                startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                            },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF1E1E35)
                            ),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("⚙️ Grant Usage Access", color = Color(0xFFE2E2F0))
                        }

                        Spacer(modifier = Modifier.height(12.dp))

                        // Collect & Send — main action
                        Button(
                            onClick = {
                                if (!hasUsagePermission()) {
                                    statusText.value = "❌ No Usage Access permission!\nGo to Settings → Apps → Special access → Usage access → ScreenSenseAgent → ON"
                                    startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                                } else {
                                    collectAndSend()
                                }
                            },
                            enabled = !collecting,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF6C63FF)
                            ),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(if (collecting) "⏳ Collecting…" else "📊 Collect & Send Data")
                        }

                        Spacer(modifier = Modifier.height(12.dp))

                        // Sign out
                        Button(
                            onClick = {
                                auth.signOut()
                                startActivity(Intent(this@MainActivity, AuthActivity::class.java))
                                finish()
                            },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF2A2A3F)
                            ),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Sign Out", color = Color(0xFFFF4F6D))
                        }
                    }
                }
            }
        }
    }

    // ═════════════════════════════════════════════════════════════
    // MAIN COLLECTION FUNCTION
    // Reads today + 7-day week + 30-day month from UsageStatsManager
    // Uses EVENTS-BASED calculation (avoids MIUI negative time bug)
    // ═════════════════════════════════════════════════════════════
    private fun collectAndSend() {
        isCollecting.value = true
        statusText.value = "📊 Reading usage data…"

        thread {
            try {
                val usm = getSystemService(USAGE_STATS_SERVICE) as UsageStatsManager
                val uid = auth.currentUser?.uid ?: throw Exception("Not signed in")
                val now = System.currentTimeMillis()

                // ── TODAY: midnight → now ─────────────────────────
                statusText.value = "📊 Reading today's data…"
                val todayStart = getMidnight(now)
                val todayMs    = computeForegroundMsByEvents(usm, todayStart, now)
                Log.d("ScreenSense", "===== RAW TODAY DATA =====")
                todayMs.forEach { (pkg, ms) ->
                    Log.d("ScreenSense", "RAW → $pkg = ${ms / 60000} min")
                }
                Log.d("ScreenSense", "==========================")
                Log.d("ScreenSense", "Today apps found: ${todayMs.size}")

                // ── WEEKLY: last 7 days ───────────────────────────
                statusText.value = "📊 Reading weekly data…"
                val weeklyData = buildDailyBreakdown(usm, 7, now)

                // ── MONTHLY: last 30 days ─────────────────────────
                statusText.value = "📊 Reading monthly data…"
                val monthlyData = buildDailyBreakdown(usm, 30, now)

                // ── HOURLY DISTRIBUTION for today ─────────────────
                val hourlyDist = buildHourlyDistribution(usm, todayStart, now)

                // ── UNLOCK COUNT ──────────────────────────────────
                val unlocks = countUnlocks(usm, todayStart, now)

                // ── Filter & sort today's apps ────────────────────
                val filteredApps = todayMs.entries
                    .filter { (pkg, ms) ->

                        if (ms <= 0) {
                            Log.d("ScreenSense", "SKIP zero → $pkg")
                            return@filter false
                        }

                        if (ms < 60_000L) {
                            Log.d("ScreenSense", "SKIP <1min → $pkg")
                            return@filter false
                        }

                        if (pkg == packageName) {
                            Log.d("ScreenSense", "SKIP self → $pkg")
                            return@filter false
                        }

                        if (isExcluded(pkg)) {
                            Log.d("ScreenSense", "SKIP excluded → $pkg")
                            return@filter false
                        }

                        Log.d("ScreenSense", "KEEP → $pkg")
                        true
                    }
                    .sortedByDescending { it.value }

                val totalMs      = filteredApps.sumOf { it.value }.toDouble()
                val totalMinutes = totalMs / 60_000.0
                val totalHours   = totalMinutes / 60.0

                Log.d("ScreenSense", String.format(
                    "TODAY: %d apps | %.1f min | %.2f hrs",
                    filteredApps.size, totalMinutes, totalHours
                ))

                // ── Social % ──────────────────────────────────────
                val socialMs   = filteredApps
                    .filter { isSocial(it.key) }
                    .sumOf { it.value }.toDouble()
                val socialPct  = if (totalMs > 0)
                    Math.min((socialMs / totalMs) * 100.0, 100.0) else 0.0

                // ── Risk score ────────────────────────────────────
                val (riskLevel, riskScore) = when {
                    totalMinutes > 480 -> Pair("high",     90)
                    totalMinutes > 360 -> Pair("high",     78)
                    totalMinutes > 240 -> Pair("moderate", 58)
                    totalMinutes > 120 -> Pair("low",      32)
                    else               -> Pair("minimal",  12)
                }

                // ── Top app ───────────────────────────────────────
                val topEntry = filteredApps.firstOrNull()
                val topApp   = topEntry?.key ?: ""
                val topMin   = ((topEntry?.value ?: 0L) / 60_000L).toInt()

                // ── appUsage JSON array ───────────────────────────
                val appArr = JSONArray()
                filteredApps.take(20).forEach { (pkg, ms) ->
                    val min = Math.round(ms / 60_000.0 * 100.0) / 100.0
                    appArr.put(JSONObject().apply {
                        put("app_name",   pkg)
                        put("clean_name", cleanName(pkg))
                        put("minutes",    min)
                        put("risk_level", "pending")
                    })
                }

                // ── Assemble payload ──────────────────────────────
                val payload = JSONObject().apply {
                    put("uid",                uid)
                    put("totalScreenTimeHr",  Math.round(totalHours   * 1000.0) / 1000.0)
                    put("totalMinutes",       Math.round(totalMinutes * 100.0)  / 100.0)
                    put("topApp",             topApp)
                    put("topAppMin",          topMin)
                    put("unlockCount",        if (unlocks > 0) unlocks else Math.max((totalMinutes / 8).toInt(), 1))
                    put("riskScore",          riskScore)
                    put("riskLevel",          riskLevel)
                    put("predictionLabel",    "${riskLevel.replaceFirstChar { it.uppercase() }} Risk User")
                    put("socialMediaPct",     Math.round(socialPct * 10.0) / 10.0)
                    put("hourlyDistribution", hourlyDist)
                    put("appUsage",           appArr)
                    put("weeklyData",         weeklyData)
                    put("monthlyData",        monthlyData)
                }

                Log.d("ScreenSense", "Payload: totalMins=${totalMinutes}, apps=${filteredApps.size}, weekly=${weeklyData.length()}, monthly=${monthlyData.length()}")

                // ── POST to Flask ─────────────────────────────────
                statusText.value = "📡 Sending to Flask…"
                val response = postToFlask(payload)
                Log.d("ScreenSense", "Flask response: $response")

                runOnUiThread {
                    isCollecting.value = false
                    // Flask returns {"success": true} with space — handle both formats
                    val isSuccess = response != null &&
                            (response.contains("\"success\":true") ||
                                    response.contains("\"success\": true"))
                    if (isSuccess) {
                        statusText.value = "✅ Done! ${filteredApps.size} apps | " +
                                String.format("%.1f", totalMinutes) + " min today\n" +
                                "Week: ${weeklyData.length()} days | Month: ${monthlyData.length()} days\n" +
                                "Open your browser dashboard!"
                    } else if (response == null) {
                        statusText.value = "❌ Cannot reach Flask.\nCheck FLASK_URL is your PC's IP."
                    } else {
                        statusText.value = "❌ Flask error:\n${response.take(300)}"
                    }
                }

            } catch (e: Exception) {
                Log.e("ScreenSense", "Collection failed: ${e.message}", e)
                runOnUiThread {
                    isCollecting.value = false
                    statusText.value = "❌ Error: ${e.message}"
                }
            }
        }
    }

    // ═════════════════════════════════════════════════════════════
    // FOREGROUND TIME CALCULATOR — DUAL METHOD (MIUI-safe)
    //
    // PROBLEM: MIUI has two bugs depending on version:
    //   • Old MIUI: getTotalTimeInForeground() has negative values (boot offset)
    //   • MIUI 14: Events-based misses time when screen locks mid-session
    //     (MOVE_TO_BACKGROUND fires on lock but FOREGROUND doesn't re-fire on unlock)
    //
    // SOLUTION: Use BOTH methods, take the HIGHER value per app.
    //   • queryAndAggregateUsageStats → accurate total, may have negatives
    //   • Events (FG/BG pairs)        → always positive, may undercount
    //   • Final = max(aggregate, events) but only if aggregate > 0
    // ═════════════════════════════════════════════════════════════
    private fun computeForegroundMsByEvents(
        usm: UsageStatsManager,
        startMs: Long,
        endMs: Long
    ): Map<String, Long> {
        val result = mutableMapOf<String, Long>()

        // ── PRIMARY: INTERVAL_DAILY — same source as Digital Wellbeing ──
        // This is what Settings → Digital Wellbeing shows. It resets at midnight.
        // On MIUI it's reliable and does NOT bleed previous days.
        try {
            val dailyStats = usm.queryUsageStats(
                UsageStatsManager.INTERVAL_DAILY,
                startMs,
                endMs
            )
            if (dailyStats != null && dailyStats.isNotEmpty()) {
                for (stats in dailyStats) {
                    val pkg = stats.packageName ?: continue
                    val ms  = stats.totalTimeInForeground
                    if (ms > 0) result[pkg] = ms
                }
                Log.d("ScreenSense", "INTERVAL_DAILY: ${result.size} apps, top=" +
                        result.entries.sortedByDescending { it.value }.take(3)
                            .joinToString { "${it.key}=${it.value/60000}min" })
                if (result.isNotEmpty()) return result
            }
        } catch (e: Exception) {
            Log.w("ScreenSense", "INTERVAL_DAILY failed: ${e.message}")
        }

        // ── FALLBACK: queryAndAggregateUsageStats ──
        // Only used if INTERVAL_DAILY returns nothing
        try {
            val statsMap = usm.queryAndAggregateUsageStats(startMs, endMs)
            statsMap?.forEach { (pkg, stats) ->
                val ms = stats.totalTimeInForeground
                if (ms > 0) result[pkg] = ms
            }
            Log.d("ScreenSense", "Aggregate fallback: ${result.size} apps")
            if (result.isNotEmpty()) return result
        } catch (e: Exception) {
            Log.w("ScreenSense", "Aggregate failed: ${e.message}")
        }

        // ── LAST RESORT: Events-based FG/BG pairs ──
        val fgStarts = mutableMapOf<String, Long>()
        try {
            val events = usm.queryEvents(startMs, endMs)
            val ev     = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(ev)
                val pkg = ev.packageName ?: continue
                val ts  = ev.timeStamp
                when (ev.eventType) {
                    UsageEvents.Event.ACTIVITY_RESUMED,
                    UsageEvents.Event.MOVE_TO_FOREGROUND -> fgStarts[pkg] = ts

                    UsageEvents.Event.ACTIVITY_PAUSED,
                    UsageEvents.Event.MOVE_TO_BACKGROUND -> {
                        val start = fgStarts.remove(pkg)
                        if (start != null) {
                            val dur = ts - start
                            if (dur in 1L..10_800_000L)
                                result[pkg] = (result[pkg] ?: 0L) + dur
                        }
                    }
                }
            }
            fgStarts.forEach { (pkg, start) ->
                val dur = endMs - start
                if (dur in 1L..10_800_000L)
                    result[pkg] = (result[pkg] ?: 0L) + dur
            }
            Log.d("ScreenSense", "Events fallback: ${result.size} apps")
        } catch (e: Exception) {
            Log.w("ScreenSense", "Events failed: ${e.message}")
        }

        return result
    }

    // ═════════════════════════════════════════════════════════════
    // HOURLY DISTRIBUTION
    // Returns JSONObject {"0": mins, "1": mins, … "23": mins}
    // ═════════════════════════════════════════════════════════════
    private fun buildHourlyDistribution(
        usm: UsageStatsManager,
        startMs: Long,
        endMs: Long
    ): JSONObject {
        val buckets  = DoubleArray(24)
        val fgStarts = mutableMapOf<String, Long>()

        try {
            val events = usm.queryEvents(startMs, endMs)
            val ev     = UsageEvents.Event()

            while (events.hasNextEvent()) {
                events.getNextEvent(ev)
                val pkg  = ev.packageName ?: continue
                if (isExcluded(pkg) || pkg == packageName) continue
                val ts   = ev.timeStamp

                when (ev.eventType) {
                    UsageEvents.Event.MOVE_TO_FOREGROUND -> fgStarts[pkg] = ts
                    UsageEvents.Event.MOVE_TO_BACKGROUND -> {
                        val start = fgStarts.remove(pkg) ?: continue
                        val dur   = ts - start
                        if (dur in 1L..7_200_000L) {
                            val cal = Calendar.getInstance()
                            cal.timeInMillis = start
                            val hour = cal.get(Calendar.HOUR_OF_DAY)
                            buckets[hour] += dur / 60_000.0
                        }
                    }
                }
            }
        } catch (e: Exception) {
            Log.w("ScreenSense", "buildHourlyDistribution: ${e.message}")
        }

        val out = JSONObject()
        for (h in 0..23) {
            if (buckets[h] > 0.1)
                out.put(h.toString(), Math.round(buckets[h] * 100.0) / 100.0)
        }
        return out
    }

    // ═════════════════════════════════════════════════════════════
    // UNLOCK COUNT via KEYGUARD_HIDDEN (type=18) events
    // ═════════════════════════════════════════════════════════════
    private fun countUnlocks(usm: UsageStatsManager, startMs: Long, endMs: Long): Int {
        var count = 0
        try {
            val events = usm.queryEvents(startMs, endMs)
            val ev     = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(ev)
                if (ev.eventType == 18 || ev.eventType == 15) count++ // KEYGUARD_HIDDEN or SCREEN_INTERACTIVE
            }
        } catch (e: Exception) {
            Log.w("ScreenSense", "countUnlocks: ${e.message}")
        }
        return count
    }

    // ═════════════════════════════════════════════════════════════
    // DAILY BREAKDOWN — queries each day independently
    // numDays=7  → weekly data
    // numDays=30 → monthly data
    // Returns JSONArray of {date, day, total_minutes, total_hours}
    // ═════════════════════════════════════════════════════════════
    // ═════════════════════════════════════════════════════════════
// DAILY BREAKDOWN — accurate per-day totals for week/month chart
// Strategy per day:
//   1. queryAndAggregateUsageStats(dStart, dEnd) — pre-aggregated, MIUI-safe
//   2. Fall back to events-only if aggregate returns nothing
// ═════════════════════════════════════════════════════════════
    private fun buildDailyBreakdown(
        usm: UsageStatsManager,
        numDays: Int,
        now: Long
    ): JSONArray {
        val arr       = JSONArray()
        val DAY_NAMES = arrayOf("Sun","Mon","Tue","Wed","Thu","Fri","Sat")
        val sdf       = SimpleDateFormat("yyyy-MM-dd", Locale.US)

        for (i in numDays - 1 downTo 0) {
            val cal = Calendar.getInstance()
            cal.timeInMillis = now - i * 86_400_000L
            cal.set(Calendar.HOUR_OF_DAY, 0)
            cal.set(Calendar.MINUTE,      0)
            cal.set(Calendar.SECOND,      0)
            cal.set(Calendar.MILLISECOND, 0)
            val dStart  = cal.timeInMillis
            val dEnd    = minOf(dStart + 86_400_000L, now)
            val dateStr = sdf.format(Date(dStart))
            val dayName = DAY_NAMES[cal.get(Calendar.DAY_OF_WEEK) - 1]

            val dayMs = computeDayMs(usm, dStart, dEnd)

            val dayMins = dayMs / 60_000.0
            val dayHrs  = dayMins / 60.0

            Log.d("ScreenSense", "Day $dateStr ($dayName): ${dayMins.toInt()} min = ${dayHrs.format2()}h")

            arr.put(JSONObject().apply {
                put("date",          dateStr)
                put("day",           dayName)
                put("total_minutes", Math.round(dayMins * 100.0) / 100.0)
                put("total_hours",   Math.round(dayHrs  * 100.0) / 100.0)
            })
        }
        return arr
    }

    // ── Per-day ms calculator ─────────────────────────────────────────
// Uses queryAndAggregateUsageStats bounded to [dStart, dEnd].
// This is the same data source as Digital Wellbeing and is MIUI-safe.
// Falls back to events-only if aggregate returns nothing.
    private fun computeDayMs(
        usm: UsageStatsManager,
        startMs: Long,
        endMs: Long
    ): Long {
        // ── METHOD 1: queryAndAggregateUsageStats ─────────────────
        // Bounded by startMs/endMs, so it only counts usage within that window.
        // Accurate even on MIUI because it reads pre-computed buckets.
        try {
            val statsMap = usm.queryAndAggregateUsageStats(startMs, endMs)
            if (!statsMap.isNullOrEmpty()) {
                val total = statsMap.entries
                    .filter { (pkg, _) ->
                        pkg != packageName && !isExcluded(pkg)
                    }
                    .sumOf { (_, stats) ->
                        val ms = stats.totalTimeInForeground
                        if (ms in 60_000L..86_400_000L) ms else 0L
                    }
                if (total > 0L) {
                    Log.d("ScreenSense", "computeDayMs [aggregate]: ${total/60000} min for window ${startMs}-${endMs}")
                    return total
                }
            }
        } catch (e: Exception) {
            Log.w("ScreenSense", "computeDayMs aggregate failed: ${e.message}")
        }

        // ── METHOD 2: Events-based FG/BG pairs (fallback) ─────────
        // Less accurate on MIUI (misses locked-screen sessions) but
        // better than nothing if aggregate returns empty.
        return computeDayMsByEventsOnly(usm, startMs, endMs)
    }

    // ── Events-only fallback (used only when aggregate fails) ─────────
    private fun computeDayMsByEventsOnly(
        usm: UsageStatsManager,
        startMs: Long,
        endMs: Long
    ): Long {
        val result   = mutableMapOf<String, Long>()
        val fgStarts = mutableMapOf<String, Long>()

        try {
            val events = usm.queryEvents(startMs, endMs)
            val ev     = UsageEvents.Event()

            while (events.hasNextEvent()) {
                events.getNextEvent(ev)
                val pkg = ev.packageName ?: continue
                if (pkg == packageName || isExcluded(pkg)) continue
                val ts  = ev.timeStamp

                when (ev.eventType) {
                    UsageEvents.Event.ACTIVITY_RESUMED,
                    UsageEvents.Event.MOVE_TO_FOREGROUND -> fgStarts[pkg] = ts

                    UsageEvents.Event.ACTIVITY_PAUSED,
                    UsageEvents.Event.MOVE_TO_BACKGROUND -> {
                        val start = fgStarts.remove(pkg)
                        if (start != null) {
                            val dur = ts - start
                            if (dur in 1L..10_800_000L)
                                result[pkg] = (result[pkg] ?: 0L) + dur
                        }
                    }
                }
            }

            // Close any still-open sessions at endMs
            fgStarts.forEach { (pkg, start) ->
                val dur = endMs - start
                if (dur in 1L..10_800_000L)
                    result[pkg] = (result[pkg] ?: 0L) + dur
            }

        } catch (e: Exception) {
            Log.w("ScreenSense", "computeDayMsByEventsOnly: ${e.message}")
        }

        val total = result.values.sumOf { it }
        Log.d("ScreenSense", "computeDayMs [events fallback]: ${total/60000} min")
        return total
    }

    // ── Extension for clean log formatting ───────────────────────────
    private fun Double.format2() = String.format("%.2f", this)
    // ═════════════════════════════════════════════════════════════
    // POST JSON to Flask
    // ═════════════════════════════════════════════════════════════
    private fun postToFlask(payload: JSONObject): String? {
        return try {
            val conn = URL("$FLASK_URL/api/receive_data").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
            conn.connectTimeout = 10_000
            conn.readTimeout    = 15_000
            conn.doOutput       = true
            conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val body = BufferedReader(InputStreamReader(conn.inputStream)).readText()
            Log.d("ScreenSense", "Flask HTTP $code: $body")
            body
        } catch (e: Exception) {
            Log.e("ScreenSense", "postToFlask: ${e.message}")
            null
        }
    }

    // ═════════════════════════════════════════════════════════════
    // UTILITIES
    // ═════════════════════════════════════════════════════════════

    private fun hasUsagePermission(): Boolean {
        val aom  = getSystemService(APP_OPS_SERVICE) as AppOpsManager
        val mode = aom.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(), packageName
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private fun getMidnight(now: Long): Long {
        val cal = Calendar.getInstance()
        cal.timeInMillis = now
        cal.set(Calendar.HOUR_OF_DAY, 0)
        cal.set(Calendar.MINUTE,      0)
        cal.set(Calendar.SECOND,      0)
        cal.set(Calendar.MILLISECOND, 0)
        return cal.timeInMillis
    }

    private fun isExcluded(pkg: String): Boolean =
        pkg in EXCLUDED_PKGS ||

                pkg.startsWith("com.miui.")    ||
                pkg.startsWith("com.xiaomi.")

    private fun isSocial(pkg: String): Boolean {
        val l = pkg.lowercase()
        return SOCIAL_KEYWORDS.any { l.contains(it) }
    }

    /**
     * com.cricbuzz.android  → "Cricbuzz"   (skips trailing "android")
     * com.instagram.android → "Instagram"
     * com.linkedin.android  → "Linkedin"
     * com.supercell.clashofclans → "Clashofclans"
     * com.whatsapp          → "Whatsapp"
     * org.telegram.messenger → "Messenger"
     */
    private fun cleanName(pkg: String): String {
        val SKIP = setOf("android", "com", "org", "net", "io", "app", "mobile")
        val parts = pkg.split(".")
        // Find last meaningful part (not a generic suffix)
        val meaningful = parts.lastOrNull { it.lowercase() !in SKIP } ?: parts.last()
        val result = meaningful
            .replace("_", " ")
            .replace(Regex("([a-z])([A-Z])"), "$1 $2")
        return result.replaceFirstChar { it.uppercase() }.ifEmpty { "Unknown" }
    }

    // Unused — kept so existing code that calls it doesn't break
    @Volatile private var monitoring = false
    private fun startMonitoring() { monitoring = true }
    private fun stopMonitoring()  { monitoring = false }

    override fun onDestroy() {
        monitoring = false
        super.onDestroy()
    }
}