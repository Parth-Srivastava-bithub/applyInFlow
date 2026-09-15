"""Browser management module for connecting to existing authenticated sessions."""

import logging
from typing import Optional, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from config import CDP_URL, PAGE_LOAD_TIMEOUT

logger = logging.getLogger("linkedin_scraper.browser")


class BrowserManager:
    """Manages connection to an authenticated Chromium browser instance."""

    def __init__(self, cdp_url: str = CDP_URL):
        self.cdp_url = cdp_url
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def connect(self) -> Page:
        """
        Connects to an existing Chrome browser instance running with Remote Debugging Port enabled.
        Reuses an existing open LinkedIn page if available, or creates a new page in the context.
        """
        logger.info(f"Connecting to existing browser via CDP: {self.cdp_url}...")
        self._playwright = await async_playwright().start()

        try:
            self.browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as e:
            logger.error(f"Failed to connect to browser at {self.cdp_url}: {e}")
            logger.error(
                "\n"
                "=" * 60 + "\n"
                "HOW TO FIX:\n"
                "1. Make sure Google Chrome is completely closed.\n"
                "2. Start Chrome with remote debugging enabled using:\n"
                "   chrome.exe --remote-debugging-port=9222\n"
                "   OR run the provided 'start_chrome.bat' script.\n"
                "3. Log in to LinkedIn manually in that Chrome window.\n"
                "4. Re-run this scraper.\n"
                "=" * 60
            )
            raise ConnectionError(
                f"Could not connect to Chrome on {self.cdp_url}. "
                "Ensure Chrome is running with --remote-debugging-port=9222."
            ) from e

        # Get existing context or default context
        contexts = self.browser.contexts
        if contexts:
            self.context = contexts[0]
        else:
            self.context = await self.browser.new_context()

        # Find existing LinkedIn page or create a new one
        for p in self.context.pages:
            if "linkedin.com" in p.url:
                logger.info(f"Found existing LinkedIn tab: {p.url}")
                self.page = p
                break

        if not self.page:
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = await self.context.new_page()

        self.page.set_default_timeout(PAGE_LOAD_TIMEOUT)
        return self.page

    async def verify_login_status(self) -> bool:
        """Checks if the connected browser session is authenticated on LinkedIn."""
        if not self.page:
            raise RuntimeError("Browser not connected. Call connect() first.")

        logger.info("Verifying LinkedIn authentication status...")
        try:
            current_url = self.page.url
            if "linkedin.com" not in current_url:
                await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            
            # Check for common login elements or authenticated feed indicators
            # If on login/signup page or checkpoint
            if any(path in self.page.url for path in ["/login", "/checkpoint", "/signup", "/authwall"]):
                return False

            # Try locating common authenticated elements (nav bar, profile button)
            auth_indicator = await self.page.query_selector(
                "#global-nav, .global-nav__me, input[aria-label*='Search'], .feed-identity-module"
            )
            return auth_indicator is not None or "feed" in self.page.url
        except Exception as e:
            logger.warning(f"Error during login verification: {e}")
            return False

    async def close(self):
        """Cleanly disconnects from the browser without closing the user's manual session."""
        if self.browser:
            try:
                # Disconnect CDP so user's browser stays open
                await self.browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        logger.info("Browser connection closed successfully.")
