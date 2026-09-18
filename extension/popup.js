document.getElementById('btn-open-dashboard').addEventListener('click', () => {
  chrome.storage.local.get(['lastDashboardUrl'], (res) => {
    const fallback = (typeof AUTOAPPLY_CONFIG !== 'undefined' && AUTOAPPLY_CONFIG.DEFAULT_SERVER_URL)
      ? AUTOAPPLY_CONFIG.DEFAULT_SERVER_URL
      : 'http://localhost:5000';
    const targetUrl = res.lastDashboardUrl || fallback;
    chrome.tabs.create({ url: targetUrl });
  });
});
