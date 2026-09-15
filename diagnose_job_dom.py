"""
Deep DOM dump v3:
- navigate with domcontentloaded (no networkidle)
- explicitly wait_for_selector on known stable elements
- slow scroll to trigger lazy loading
- dump all substantial text elements with their classes/ids
"""
import asyncio
from playwright.async_api import async_playwright

JOB_URL = "https://www.linkedin.com/jobs/view/4459103393"


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

        print(f"Before nav: {page.url}")
        # Use domcontentloaded - never networkidle on LinkedIn SPA
        await page.goto(JOB_URL, wait_until="domcontentloaded", timeout=25000)
        print(f"After nav: {page.url}")

        # Wait for top card to confirm page loaded
        try:
            await page.wait_for_selector("h1, .job-details-jobs-unified-top-card__job-title, .top-card-layout__title", timeout=10000)
            print("Top card loaded.")
        except Exception:
            print("Top card not found, continuing...")

        # Scroll slowly to trigger lazy-loading of description
        for i in range(1, 6):
            await page.evaluate(f"window.scrollBy(0, {i * 400});")
            await asyncio.sleep(0.8)

        # Try to find and click any "show more" button
        show_more_js = """
        () => {
            const btns = [...document.querySelectorAll('button')];
            const more = btns.find(b => /see more|show more/i.test(b.innerText));
            if (more) { more.click(); return more.innerText; }
            return null;
        }
        """
        clicked = await page.evaluate(show_more_js)
        if clicked:
            print(f"Clicked button: {clicked}")
            await asyncio.sleep(1)

        await asyncio.sleep(3)

        # Deep dump: all elements with >150 chars text, sorted by length
        results = await page.evaluate("""
        () => {
            const found = [];
            const tags = ['div', 'section', 'article', 'span', 'p', 'ul'];
            for (const tag of tags) {
                for (const el of document.querySelectorAll(tag)) {
                    const text = (el.innerText || '').trim();
                    if (text.length > 150 && text.length < 10000) {
                        found.push({
                            tag: el.tagName,
                            id: el.id || '',
                            cls: (el.className || '').toString().slice(0, 150),
                            len: text.length,
                            snippet: text.slice(0, 180).replace(/\\n/g, ' ')
                        });
                    }
                }
            }
            const seen = new Set();
            return found.filter(f => {
                const key = f.snippet.slice(0, 60);
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            }).sort((a,b) => b.len - a.len).slice(0, 25);
        }
        """)

        print(f"\nFound {len(results)} substantial elements:\n" + "="*60)
        for r in results:
            print(f"  <{r['tag']}> id={r['id']!r}")
            print(f"  class: {r['cls']}")
            print(f"  [{r['len']} chars] {r['snippet']}...")
            print()

        # Also dump all emails from full page text
        import re
        body = await page.evaluate("document.body.innerText")
        emails = re.findall(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b', body)
        print("="*60)
        print(f"EMAILS IN PAGE: {list(set(emails)) or 'none'}")
        print(f"FINAL URL: {page.url}")

        await browser.close()


asyncio.run(main())
