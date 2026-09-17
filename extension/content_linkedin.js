/**
 * content_linkedin.js
 * Injected into LinkedIn search results page (https://www.linkedin.com/search/results/content/*).
 * Automatically scrolls the feed, extracts hiring posts with contact emails,
 * and streams discovered leads back to the background worker.
 */

const JUNK_EMAILS = new Set([
  'firstname.lastname@gmail.com', 'yourname@gmail.com', 'example@gmail.com',
  'name@gmail.com', 'xyz@gmail.com', 'abc@gmail.com', 'test@gmail.com',
  'sample@gmail.com', 'email@gmail.com', 'partyanimal@gmail.com'
]);

const EMAIL_RE = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}/gi;

function cleanName(raw) {
  if (!raw) return "Hiring Team";
  let s = raw.trim();
  // Strip common prefixes
  s = s.replace(/^(Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)\s*/i, '');
  // Remove emojis and non-standard symbols
  s = s.replace(/[^\w\s\-\.']/g, ' ').replace(/\s+/g, ' ').trim();
  const words = s.split(' ').filter(w => w.length > 0);
  if (words.length === 0) return "Hiring Team";
  // Cap at 3 words
  return words.slice(0, 3).map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

function getTitle(root) {
  if (!root) return '';
  const el = root.querySelector('.update-components-actor__description, .entity-result__primary-subtitle');
  return el ? el.innerText.trim().split('\n')[0] : '';
}

function getProfileUrl(root) {
  if (!root) return '';
  const a = root.querySelector('a[href*="/in/"]');
  if (!a) return '';
  const m = (a.getAttribute('href') || '').match(/\/in\/[^/?#]+/);
  return m ? 'https://www.linkedin.com' + m[0] : '';
}

function getCardAuthor(cardText) {
  const lines = cardText.split('\n').map(l => l.trim()).filter(Boolean);
  const fi = lines.indexOf('Feed post');
  if (fi >= 0 && lines[fi + 1]) return lines[fi + 1];
  for (const l of lines.slice(0, 6)) {
    if (/^[A-Z][a-z]+\s[A-Z][a-z]+/.test(l)) return l.split('\n')[0];
  }
  return '';
}

function getNameNearEmail(cardText, emailStr) {
  const el = emailStr.toLowerCase();
  const textLower = cardText.toLowerCase();
  const idx = textLower.indexOf(el);
  if (idx === -1) return getCardAuthor(cardText);

  const window = cardText.slice(Math.max(0, idx - 300), idx + 60);
  const lines = window.split('\n').map(l => l.trim()).filter(Boolean);
  const NAME_PAT = /^[A-Z][a-z]{1,20}(\s[A-Z][a-z]{1,25})+$/;
  const BAD = /hiring|manager|engineer|developer|recruiter|services|solutions|consulting|technologies|india|limited|pvt|inc|llc|corp|urgent|required/i;

  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i].replace(/,\s*$/, '').trim();
    if (NAME_PAT.test(l) && !BAD.test(l) && l.split(' ').length <= 4) {
      return l;
    }
    const m = l.match(/(?:to|for|contact|recruiter|from|cc)[:\s]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)/i);
    if (m && !BAD.test(m[1])) return m[1].trim();
  }
  return getCardAuthor(cardText);
}

function findPostCard(el) {
  let node = el;
  for (let i = 0; i < 20; i++) {
    if (!node.parentElement) break;
    node = node.parentElement;
    if (node.tagName === 'LI') break;
  }
  return node;
}

function extractPostsOnPage(seenEmails) {
  const results = [];

  function push(email, card) {
    const e = (email || '').toLowerCase().trim();
    if (!e || !e.includes('@') || seenEmails.has(e)) return;
    if (e[0] === '-' || e[0] === '.' || e[0] === '_' || e[0] === '+') return;
    if (e.split('@')[0].includes('+')) return;
    if (JUNK_EMAILS.has(e)) return;

    seenEmails.add(e);
    const cardText = card ? (card.innerText || '').trim() : '';
    const profile_url = getProfileUrl(card);
    const rawName = getNameNearEmail(cardText, e);
    const name = cleanName(rawName);
    const title = getTitle(card) || "Talent Acquisition / Recruiter";

    let postSnippet = cardText;
    const ci = cardText.toLowerCase().indexOf(e);
    if (ci !== -1) {
      postSnippet = cardText.slice(Math.max(0, ci - 350), ci + 150);
    }

    results.push({
      name,
      title,
      company: "",
      linkedin_url: profile_url,
      post_text: postSnippet.slice(0, 800),
      email: e,
      hr_email: e,
      gmail: e.includes('@gmail.com') ? e : null,
      status: "pending"
    });
  }

  // Strategy 1: Mailto links
  for (const a of document.querySelectorAll('a[href^="mailto:"]')) {
    const email = (a.getAttribute('href') || '').replace('mailto:', '').trim();
    push(email, findPostCard(a));
  }

  // Strategy 2: Regex scan across all LI cards
  for (const li of document.querySelectorAll('li')) {
    const txt = (li.innerText || '').trim();
    for (const em of (txt.match(EMAIL_RE) || [])) {
      push(em, li);
    }
  }

  return results;
}

let activeScrapeTimer = null;
let isScrapingActive = false;

// Handle scraping commands from background service worker
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "START_QUERY_SCRAPE") {
    const { query, maxPosts = 15 } = message;
    startScrapingLoop(query, maxPosts);
    sendResponse({ status: "started" });
  } else if (message.action === "STOP_SCRAPE") {
    stopScrapingLoop();
    sendResponse({ status: "stopped" });
  }
  return true;
});

function stopScrapingLoop() {
  isScrapingActive = false;
  if (activeScrapeTimer) {
    clearInterval(activeScrapeTimer);
    activeScrapeTimer = null;
  }
}

function startScrapingLoop(query, maxPosts) {
  stopScrapingLoop();
  isScrapingActive = true;

  const seenInQuery = new Set();
  let totalFound = 0;
  let emptyScrolls = 0;
  let scrollStep = 0;

  activeScrapeTimer = setInterval(() => {
    if (!isScrapingActive) {
      clearInterval(activeScrapeTimer);
      return;
    }

    const newLeads = extractPostsOnPage(seenInQuery);
    if (newLeads.length > 0) {
      emptyScrolls = 0;
      totalFound += newLeads.length;
      chrome.runtime.sendMessage({
        target: "background",
        type: "NEW_LEADS",
        leads: newLeads,
        query: query
      });
    } else {
      emptyScrolls++;
    }

    scrollStep++;

    // Check completion condition
    if (totalFound >= maxPosts || emptyScrolls >= 5 || scrollStep >= 25) {
      stopScrapingLoop();
      chrome.runtime.sendMessage({
        target: "background",
        type: "QUERY_DONE",
        query: query,
        foundInQuery: totalFound
      });
      return;
    }

    // Scroll smoothly down the feed
    window.scrollBy({
      top: 850,
      behavior: 'smooth'
    });
  }, 1800);
}
