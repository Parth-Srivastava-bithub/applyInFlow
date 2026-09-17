document.getElementById('btn-open-dashboard').addEventListener('click', () => {
  chrome.tabs.create({ url: 'http://localhost:5000' });
});
