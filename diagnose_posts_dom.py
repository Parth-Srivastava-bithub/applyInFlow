"""
Diagnostic: connect via CDP, navigate to LinkedIn content search for "hr with mail",
wait for JS render, then dump all visible text blocks to find the exact selectors.
"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "https://www.linkedin.com/search/results/content/?keywords=hr+with+mail&origin=GLOBAL_SEARCH_HEADER"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "linkedin" in pg.url:
                page = pg
                break
        if not page:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(20000)

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(4)
        print(f"Current URL: {page.url}")

        # Dump structure: find all li elements with gmail in their text
        result = await page.evaluate(r"""
        () => {
            const out = [];
            // Dump all list items
            const items = document.querySelectorAll('li, div[data-view-name], div[class*="search-result"]');
            for (const el of items) {
                const txt = (el.innerText || '').trim();
                if (txt.includes('@') && txt.length > 50 && txt.length < 5000) {
                    out.push({
                        tag: el.tagName,
                        cls: (el.className || '').toString().slice(0, 200),
                        id: el.id || '',
                        dataView: el.getAttribute('data-view-name') || '',
                        len: txt.length,
                        snippet: txt.slice(0, 300).replace(/\n/g, ' | ')
                    });
                }
            }
            // Dedupe
            const seen = new Set();
            return out.filter(x => {
                const k = x.snippet.slice(0, 80);
                if (seen.has(k)) return false;
                seen.add(k);
                return true;
            }).sort((a,b) => a.len - b.len).slice(0, 20);
        }
        """)

        print(f"\nFound {len(result)} elements with '@' in text:\n" + "="*60)
        for r in result:
            print(f"\n<{r['tag']}> id={r['id']!r} data-view={r['dataView']!r}")
            print(f"class: {r['cls']}")
            print(f"[{r['len']} chars] {r['snippet']}")

        # Also dump the full page structure (tag counts)
        structure = await page.evaluate(r"""
        () => {
            const counts = {};
            document.querySelectorAll('*').forEach(el => {
                const k = el.tagName + (el.className ? '.' + el.className.toString().split(' ')[0] : '');
                counts[k] = (counts[k] || 0) + 1;
            });
            // Return top 30 by count
            return Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0,30);
        }
        """)
        print("\n\nPAGE TAG COUNTS (top 30):")
        for tag, count in structure:
            print(f"  {count:4d}x  {tag}")

        await browser.close()

asyncio.run(main())
