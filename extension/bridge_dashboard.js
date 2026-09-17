/**
 * bridge_dashboard.js
 * Injected into AutoApply dashboard (localhost and cloud domains).
 * Bridges communication between the Web Page (window) and the Extension Service Worker.
 */

// Notify the web page that the extension is installed and ready
function notifyPageReady() {
  window.postMessage({
    target: "autoapply-page",
    type: "EXTENSION_READY",
    version: "1.0.0"
  }, "*");
}

// Notify immediately and on DOMContentLoaded
notifyPageReady();
window.addEventListener("DOMContentLoaded", notifyPageReady);

// Periodically notify for the first 5 seconds to ensure any async auth/init catches it
let announceCount = 0;
const announceInterval = setInterval(() => {
  notifyPageReady();
  announceCount++;
  if (announceCount >= 5) clearInterval(announceInterval);
}, 1000);

// Listen for messages sent from the dashboard web page
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.target !== "autoapply-extension") return;

  if (data.action === "PING") {
    notifyPageReady();
    return;
  }

  // Forward action to background service worker
  chrome.runtime.sendMessage(data, (response) => {
    if (chrome.runtime.lastError) {
      window.postMessage({
        target: "autoapply-page",
        type: "ERROR",
        message: chrome.runtime.lastError.message
      }, "*");
    } else if (response) {
      window.postMessage(response, "*");
    }
  });
});

// Listen for progress/status messages from background.js and forward to the page
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.target === "autoapply-page") {
    window.postMessage(message, "*");
  }
});
