"""
LinkedIn Posts Scraper
Searches LinkedIn content/posts for HR hiring posts that contain
publicly visible Gmail/email addresses (via mailto: links or plain text).
"""

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from urllib.parse import quote_plus
from typing import List, Dict, Any, Set
from playwright.async_api import async_playwright, Page

logger = logging.getLogger("linkedin_posts_scraper")

GMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@gmail\.com\b', re.I)
EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b', re.I)

JUNK_EMAILS = {
    'firstname.lastname@gmail.com', 'yourname@gmail.com', 'example@gmail.com',
    'name@gmail.com', 'xyz@gmail.com', 'abc@gmail.com', 'test@gmail.com',
    'sample@gmail.com', 'email@gmail.com', 'partyanimal@gmail.com'
}

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
POSTS_JSON = OUTPUT_DIR / "hr_gmail_posts.json"
SEEN_POSTS = OUTPUT_DIR / "seen_posts.json"

DEFAULT_QUERIES = [
    "hr with mail",
    "mail your cv gmail hiring",
    "send resume gmail hr recruiter",
    "mail cv hiring india gmail",
    "gmail apply now hiring recruiter",
]

# Inline JS — extracts emails from the current page via two strategies:
# 1. All mailto: links (LinkedIn renders emails as <a href="mailto:...">)
# 2. Regex scan of all <li> innerText
JS_EXTRACT = """
() => {
    const results = [];
    const SKIP = /^\\d|comment|reaction|repost|like|follow|send|connect/i;
    const EMAIL_RE = /[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,7}/gi;

    function getTitle(root) {
        if (!root) return '';
        const el = root.querySelector('.update-components-actor__description, .entity-result__primary-subtitle');
        return el ? el.innerText.trim().split('\\n')[0] : '';
    }

    function getProfileUrl(root) {
        if (!root) return '';
        const a = root.querySelector('a[href*="/in/"]');
        if (!a) return '';
        const m = (a.getAttribute('href') || '').match(/\\/in\\/[^/?#]+/);
        return m ? 'https://www.linkedin.com' + m[0] : '';
    }

    // Extract the name that appears CLOSEST to this specific email within the card text
    function getNameNearEmail(cardText, emailStr) {
        const el = emailStr.toLowerCase();
        const textLower = cardText.toLowerCase();
        const idx = textLower.indexOf(el);
        if (idx === -1) {
            // email not literally in card text, fall back to card author
            return getCardAuthor(cardText);
        }
        // Take a ~300 char window before the email
        const window = cardText.slice(Math.max(0, idx - 300), idx + 60);
        // Split window into lines and look for a proper name pattern
        const lines = window.split('\\n').map(l => l.trim()).filter(Boolean);
        const NAME_PAT = /^[A-Z][a-z]{1,20}(\\s[A-Z][a-z]{1,25})+$/;
        const BAD = /hiring|manager|engineer|developer|recruiter|services|solutions|consulting|technologies|india|limited|pvt|inc|llc|corp|urgent|required/i;
        // Walk lines in reverse (closest before email first)
        for (let i = lines.length - 1; i >= 0; i--) {
            const l = lines[i].replace(/,\\s*$/, '').trim();
            if (NAME_PAT.test(l) && !BAD.test(l) && l.split(' ').length <= 4) {
                return l;
            }
            // "to: Name" / "Contact Name" / "Send resume to Name"
            const m = l.match(/(?:to|for|contact|recruiter|from|cc)[:\\s]+([A-Z][a-z]+(?:\\s[A-Z][a-z]+)+)/i);
            if (m && !BAD.test(m[1])) return m[1].trim();
        }
        return getCardAuthor(cardText);
    }

    function getCardAuthor(cardText) {
        const lines = cardText.split('\\n').map(l => l.trim()).filter(Boolean);
        const fi = lines.indexOf('Feed post');
        if (fi >= 0 && lines[fi + 1]) return lines[fi + 1];
        // first capitalised 2-word line
        for (const l of lines.slice(0, 6)) {
            if (/^[A-Z][a-z]+\\s[A-Z][a-z]+/.test(l)) return l.split('\\n')[0];
        }
        return '';
    }

    function findPostCard(el) {
        // Walk up DOM to find the LI post card
        let node = el;
        for (let i = 0; i < 20; i++) {
            if (!node.parentElement) break;
            node = node.parentElement;
            if (node.tagName === 'LI') break;
        }
        return node;
    }

    const seenEmails = new Set();

    function push(email, card) {
        const e = email.toLowerCase().trim();
        if (!e.includes('@') || seenEmails.has(e)) return;
        seenEmails.add(e);
        const cardText = card ? (card.innerText || '').trim() : '';
        const profile_url = getProfileUrl(card);
        // Find the name nearest to this specific email in the card text
        const name  = getNameNearEmail(cardText, e);
        const title = getTitle(card);
        // Extract a focused post snippet: ~350 chars before + email mention + ~150 after
        let postSnippet = cardText;
        const ci = cardText.toLowerCase().indexOf(e);
        if (ci !== -1) {
            postSnippet = cardText.slice(Math.max(0, ci - 350), ci + 150);
        }
        results.push({
            name,
            title,
            profile_url,
            post_text:   postSnippet.slice(0, 800),
            email:       e,
            gmail:       e.includes('@gmail.com') ? e : null,
            key:         (profile_url || e) + '|' + e
        });
    }

    // Strategy 1: all mailto: anchor hrefs
    for (const a of document.querySelectorAll('a[href^="mailto:"]')) {
        const email = (a.getAttribute('href') || '').replace('mailto:', '').trim();
        push(email, findPostCard(a));
    }

    // Strategy 2: regex scan on all LI innerText
    for (const li of document.querySelectorAll('li')) {
        const txt = (li.innerText || '').trim();
        for (const em of (txt.match(EMAIL_RE) || [])) {
            push(em, li);
        }
    }

    return results;
}
"""


def load_seen(path: Path) -> Set[str]:
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(seen: Set[str], path: Path):
    path.write_text(json.dumps(list(seen), indent=2), encoding="utf-8")


def load_records(path: Path) -> List[Dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_records(records: List[Dict], path: Path):
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Saved {len(records)} records -> {path}")


async def scrape_posts(
    page: Page,
    queries: List[str],
    max_posts_per_query: int = 30,
    min_delay: float = 1.5,
    max_delay: float = 3.5,
) -> List[Dict[str, Any]]:

    seen = load_seen(SEEN_POSTS)
    all_records: List[Dict] = load_records(POSTS_JSON)
    existing_emails = {
        (r.get("hr_email") or r.get("gmail") or "").lower()
        for r in all_records
        if (r.get("hr_email") or r.get("gmail"))
    }

    for query in queries:
        encoded = quote_plus(query)
        url = f"https://www.linkedin.com/search/results/content/?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"

        logger.info(f"\nSearching posts: '{query}'")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            logger.warning(f"Navigation failed for '{query}': {e}")
            continue

        await asyncio.sleep(3.0)

        collected = 0
        last_height = 0
        scrolls_without_new = 0

        while collected < max_posts_per_query and scrolls_without_new < 4:
            posts_data = await page.evaluate(JS_EXTRACT)

            new_found = 0
            for post in posts_data:
                key = post.get("key", "")
                if not key or key in seen:
                    continue

                gmail    = post.get("gmail")
                hr_email = post.get("email")
                # Skip invalid emails, plus-aliases, or junk template emails
                if not hr_email or hr_email[0] in ('-', '.', '_', '+') or '+' in hr_email.split('@')[0] or hr_email in JUNK_EMAILS:
                    seen.add(key)
                    continue

                email_clean = hr_email.lower()
                if email_clean in existing_emails:
                    seen.add(key)
                    continue

                existing_emails.add(email_clean)
                record = {
                    "name":         post.get("name") or "Unknown",
                    "title":        post.get("title") or "",
                    "linkedin_url": post.get("profile_url") or "",
                    "post_text":    post.get("post_text", "")[:600],
                    "gmail":        gmail,
                    "hr_email":     hr_email,
                    "query":        query,
                }
                seen.add(key)
                all_records.append(record)
                collected += 1
                new_found += 1
                logger.info(f"  [FOUND] {record['name']} | {gmail or hr_email}")

            save_seen(seen, SEEN_POSTS)
            save_records(all_records, POSTS_JSON)

            if new_found == 0:
                scrolls_without_new += 1
            else:
                scrolls_without_new = 0

            if collected >= max_posts_per_query:
                break

            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                scrolls_without_new += 1
            last_height = new_height
            await page.evaluate("window.scrollBy(0, 1200);")
            await asyncio.sleep(random.uniform(min_delay, max_delay))

        logger.info(f"Query '{query}': collected {collected} posts with emails.")

    return all_records


async def main(
    queries: List[str] = DEFAULT_QUERIES,
    max_posts: int = 30,
    cdp_url: str = "http://localhost:9222",
):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]

        page = None
        for pg in ctx.pages:
            if "linkedin" in pg.url:
                page = pg
                break
        if not page:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(20000)

        try:
            records = await scrape_posts(
                page=page,
                queries=queries,
                max_posts_per_query=max_posts,
            )
            print(f"\n{'='*55}")
            print(f"Done! Extracted {len(records)} posts with HR emails.")
            print(f"Output: {POSTS_JSON}")
            print(f"{'='*55}\n")

            print(f"{'Name':<28} {'Gmail / Email':<40}")
            print("-" * 70)
            for r in records:
                print(f"{r['name'][:27]:<28} {(r.get('gmail') or r.get('hr_email') or ''):<40}")

        finally:
            await browser.close()
