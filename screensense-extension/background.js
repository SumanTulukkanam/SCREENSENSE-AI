// ─── Config ───────────────────────────────────────────────────────────────────
const FLUSH_INTERVAL_MS = 5 * 60 * 1000; // Flush every 5 minutes
const BACKEND_URL = "http://127.0.0.1:5000/api/web_usage_batch";

// ─── Listen for UID from your dashboard ──────────────────────────────────────
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (message.type === "SET_UID") {
    chrome.storage.local.set({ userId: message.userId }, () => {
      console.log("UID stored from dashboard:", message.userId);
    });
  }
});

// ─── Track tab visits (with deduplication) ───────────────────────────────────
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;

  // Skip internal browser pages
  if (
    tab.url.startsWith("chrome://") ||
    tab.url.startsWith("edge://") ||
    tab.url.startsWith("about:") ||
    tab.url.startsWith("file:")
  ) return;

  try {
    const url = new URL(tab.url);
    const domain = url.hostname;

    chrome.storage.local.get(["userId", "pendingVisits", "lastDomain"], (result) => {
      const userId = result.userId;
      if (!userId) {
        console.log("No userId found, skipping...");
        return;
      }

      // ── Deduplication: skip if same domain as last visit ──────────────────
      if (result.lastDomain === domain) {
        console.log("Duplicate domain skipped:", domain);
        return;
      }

      // ── Queue the visit locally ───────────────────────────────────────────
      const pendingVisits = result.pendingVisits || [];
      pendingVisits.push({
        userId,
        url: tab.url,
        domain,
        timestamp: new Date().toISOString()
      });

      chrome.storage.local.set({
        pendingVisits,
        lastDomain: domain
      }, () => {
        console.log(`Queued visit to ${domain}. Queue size: ${pendingVisits.length}`);
      });
    });

  } catch (e) {
    console.log("Invalid URL skipped:", e);
  }
});

// ─── Flush queued visits to backend every 5 minutes ──────────────────────────
function flushVisits() {
  chrome.storage.local.get(["pendingVisits"], (result) => {
    const pendingVisits = result.pendingVisits || [];

    if (pendingVisits.length === 0) {
      console.log("Nothing to flush.");
      return;
    }

    console.log(`Flushing ${pendingVisits.length} visits to backend...`);

    fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visits: pendingVisits })
    })
      .then(res => res.json())
      .then(data => {
        console.log("Batch sent successfully:", data);
        // Clear the queue only after successful send
        chrome.storage.local.set({ pendingVisits: [] });
      })
      .catch(err => {
        // On failure, keep the queue — will retry next flush
        console.error("Batch send failed, will retry next flush:", err);
      });
  });
}

// ─── Schedule periodic flush ──────────────────────────────────────────────────
chrome.alarms.create("flushVisits", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "flushVisits") flushVisits();
});

// ─── Flush on browser shutdown (best-effort) ─────────────────────────────────
chrome.runtime.onSuspend.addListener(() => {
  flushVisits();
});