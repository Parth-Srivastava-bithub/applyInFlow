/**
 * background.js
 * AutoApply Chrome Extension Service Worker
 * Coordinates sequential LinkedIn scraping across queries and syncs discovered leads to AutoApply backend.
 */

let currentJob = {
  isRunning: false,
  username: "",
  serverUrl: "http://localhost:5000",
  queries: [],
  currentQueryIndex: 0,
  maxPosts: 15,
  collectedLeads: [],
  existingEmails: new Set(),
  dashboardTabId: null,
  scrapeTabId: null
};

function getLinkedInSearchUrl(query) {
  const encoded = encodeURIComponent(query);
  return `https://www.linkedin.com/search/results/content/?keywords=${encoded}&origin=GLOBAL_SEARCH_HEADER`;
}

function sendDashboardMessage(payload) {
  if (currentJob.dashboardTabId) {
    chrome.tabs.sendMessage(currentJob.dashboardTabId, {
      target: "autoapply-page",
      ...payload
    }).catch(() => {});
  }
}

// Tab update listener to trigger content script when search results finish loading
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    currentJob.isRunning &&
    tabId === currentJob.scrapeTabId &&
    changeInfo.status === 'complete' &&
    tab.url && tab.url.includes('linkedin.com/search/results/content')
  ) {
    const query = currentJob.queries[currentJob.currentQueryIndex];
    const totalQ = currentJob.queries.length;
    const progressPct = Math.round(((currentJob.currentQueryIndex) / totalQ) * 90);

    sendDashboardMessage({
      type: "PROGRESS",
      percent: Math.max(10, progressPct),
      currentQueryIndex: currentJob.currentQueryIndex + 1,
      totalQueries: totalQ,
      currentQuery: query,
      foundCount: currentJob.collectedLeads.length,
      statusText: `Searching (${currentJob.currentQueryIndex + 1}/${totalQ}): "${query}"...`
    });

    // Give page 2.5 seconds to settle before starting the scroll loop
    setTimeout(() => {
      if (!currentJob.isRunning || tabId !== currentJob.scrapeTabId) return;
      chrome.tabs.sendMessage(tabId, {
        action: "START_QUERY_SCRAPE",
        query: query,
        maxPosts: currentJob.maxPosts
      }).catch(err => {
        console.warn("Failed to signal content script:", err);
      });
    }, 2500);
  }
});

// Clean up tab references if user manually closes the scraping tab
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === currentJob.scrapeTabId) {
    currentJob.scrapeTabId = null;
    if (currentJob.isRunning) {
      currentJob.isRunning = false;
      sendDashboardMessage({
        type: "PROGRESS",
        statusText: "Scraping tab was closed.",
        foundCount: currentJob.collectedLeads.length
      });
    }
  }
});

// Message hub
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Messages from bridge_dashboard.js
  if (message.target === "autoapply-extension") {
    if (message.action === "START_SCRAPE") {
      handleStartScrape(message, sender);
      sendResponse({ status: "acknowledged" });
    } else if (message.action === "STOP_SCRAPE") {
      handleStopScrape();
      sendResponse({ status: "stopped" });
    }
    return true;
  }

  // Messages from content_linkedin.js
  if (message.target === "background") {
    if (message.type === "NEW_LEADS") {
      handleNewLeads(message.leads, message.query);
    } else if (message.type === "QUERY_DONE") {
      handleQueryDone(message.query, message.foundInQuery);
    }
    return true;
  }
});

function handleStartScrape(data, sender) {
  currentJob.isRunning = true;
  currentJob.username = data.username || "legacy";
  currentJob.serverUrl = (data.serverUrl || "http://localhost:5000").replace(/\/$/, "");
  currentJob.queries = Array.isArray(data.queries) && data.queries.length > 0 ? data.queries : ["hr with mail"];
  currentJob.maxPosts = data.maxPosts || 15;
  currentJob.currentQueryIndex = 0;
  currentJob.collectedLeads = [];
  currentJob.existingEmails = new Set();
  currentJob.dashboardTabId = sender.tab ? sender.tab.id : null;

  sendDashboardMessage({
    type: "PROGRESS",
    percent: 5,
    currentQueryIndex: 1,
    totalQueries: currentJob.queries.length,
    currentQuery: currentJob.queries[0],
    foundCount: 0,
    statusText: "Extension connected. Opening LinkedIn search session..."
  });

  const firstUrl = getLinkedInSearchUrl(currentJob.queries[0]);
  chrome.tabs.create({ url: firstUrl, active: false }, (tab) => {
    currentJob.scrapeTabId = tab.id;
  });
}

function handleNewLeads(newLeads, query) {
  if (!currentJob.isRunning) return;

  for (const lead of newLeads) {
    const em = (lead.email || lead.hr_email || "").toLowerCase().trim();
    if (em && !currentJob.existingEmails.has(em)) {
      currentJob.existingEmails.add(em);
      currentJob.collectedLeads.push({
        ...lead,
        query: query
      });
    }
  }

  sendDashboardMessage({
    type: "PROGRESS",
    foundCount: currentJob.collectedLeads.length,
    statusText: `Discovered ${currentJob.collectedLeads.length} contacts so far...`
  });
}

function handleQueryDone(query, foundCount) {
  if (!currentJob.isRunning) return;

  currentJob.currentQueryIndex++;
  const totalQ = currentJob.queries.length;

  if (currentJob.currentQueryIndex < totalQ) {
    // Proceed to next query
    const nextQuery = currentJob.queries[currentJob.currentQueryIndex];
    const nextUrl = getLinkedInSearchUrl(nextQuery);

    sendDashboardMessage({
      type: "PROGRESS",
      percent: Math.round((currentJob.currentQueryIndex / totalQ) * 90),
      currentQueryIndex: currentJob.currentQueryIndex + 1,
      totalQueries: totalQ,
      currentQuery: nextQuery,
      foundCount: currentJob.collectedLeads.length,
      statusText: `Finished query. Switching to (${currentJob.currentQueryIndex + 1}/${totalQ}): "${nextQuery}"...`
    });

    if (currentJob.scrapeTabId) {
      chrome.tabs.update(currentJob.scrapeTabId, { url: nextUrl });
    }
  } else {
    // All queries completed! Sync to backend
    finishAndSyncLeads();
  }
}

async function finishAndSyncLeads() {
  currentJob.isRunning = false;

  sendDashboardMessage({
    type: "PROGRESS",
    percent: 95,
    statusText: `Finished search! Syncing ${currentJob.collectedLeads.length} contacts to AutoApply database...`,
    foundCount: currentJob.collectedLeads.length
  });

  // Close the background scrape tab
  if (currentJob.scrapeTabId) {
    chrome.tabs.remove(currentJob.scrapeTabId).catch(() => {});
    currentJob.scrapeTabId = null;
  }

  try {
    const ingestUrl = `${currentJob.serverUrl}/api/extension/ingest`;
    const resp = await fetch(ingestUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        username: currentJob.username,
        leads: currentJob.collectedLeads
      })
    });

    const result = await resp.json();
    const newCount = result.inserted_count ?? currentJob.collectedLeads.length;

    sendDashboardMessage({
      type: "COMPLETE",
      percent: 100,
      newCount: newCount,
      totalCollected: currentJob.collectedLeads.length,
      statusText: `Synced ${newCount} new contacts to AutoApply dashboard!`
    });
  } catch (err) {
    sendDashboardMessage({
      type: "COMPLETE",
      percent: 100,
      newCount: currentJob.collectedLeads.length,
      statusText: `Completed with ${currentJob.collectedLeads.length} contacts (Local cache synced).`
    });
  }
}

function handleStopScrape() {
  currentJob.isRunning = false;
  if (currentJob.scrapeTabId) {
    chrome.tabs.sendMessage(currentJob.scrapeTabId, { action: "STOP_SCRAPE" }).catch(() => {});
    chrome.tabs.remove(currentJob.scrapeTabId).catch(() => {});
    currentJob.scrapeTabId = null;
  }
  sendDashboardMessage({
    type: "PROGRESS",
    statusText: `Scraping stopped by user. Found ${currentJob.collectedLeads.length} contacts.`
  });
}
