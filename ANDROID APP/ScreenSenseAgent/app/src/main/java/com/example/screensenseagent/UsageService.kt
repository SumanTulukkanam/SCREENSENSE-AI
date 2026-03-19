package com.example.screensenseagent

import android.app.Service
import android.app.usage.UsageStatsManager
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log

class UsageService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var runnable: Runnable

    override fun onCreate() {
        super.onCreate()

        runnable = object : Runnable {
            override fun run() {
                fetchUsage()
                handler.postDelayed(this, 3000)
            }
        }

        handler.post(runnable)
    }

    override fun onDestroy() {
        handler.removeCallbacks(runnable)
        super.onDestroy()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    private fun fetchUsage() {
        val usageStatsManager = getSystemService(USAGE_STATS_SERVICE) as UsageStatsManager

        val endTime = System.currentTimeMillis()
        val startTime = endTime - 1000 * 60 * 5

        val stats = usageStatsManager.queryUsageStats(
            UsageStatsManager.INTERVAL_DAILY,
            startTime,
            endTime
        )

        stats?.forEach { usage ->
            if (usage.totalTimeInForeground > 0) {
                Log.d(
                    "ScreenSense",
                    "App: ${usage.packageName} Time: ${usage.totalTimeInForeground}"
                )
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null
}