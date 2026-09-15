"""Unit tests for LinkedIn Scraper modules."""

import unittest
from parser import normalize_linkedin_url, extract_emails_from_text
from filter import is_hr_recruiter_profile
from storage import StorageManager
from pathlib import Path
import shutil


class TestLinkedInScraperModules(unittest.TestCase):

    def test_url_normalization(self):
        dirty_url = "https://www.linkedin.com/in/sarah-connor-12345/?miniProfileUrn=urn%3Ali%3Afsd_profile%3A123&lipi=urn%3Ali%3Apage%3Ad_flagship3"
        expected = "https://www.linkedin.com/in/sarah-connor-12345"
        self.assertEqual(normalize_linkedin_url(dirty_url), expected)

        trailing_slash_url = "https://www.linkedin.com/in/alex-smith-recruiter/"
        expected_slash = "https://www.linkedin.com/in/alex-smith-recruiter"
        self.assertEqual(normalize_linkedin_url(trailing_slash_url), expected_slash)

    def test_email_extraction(self):
        # Case 1: Gmail present
        text1 = "Passionate Technical Recruiter. Feel free to reach out at sarah.recruiting@gmail.com for open roles!"
        res1 = extract_emails_from_text(text1)
        self.assertEqual(res1["gmail"], "sarah.recruiting@gmail.com")

        # Case 2: Non-gmail corporate email
        text2 = "Director of Talent. Inquiries: hiring@techcorp.io or message me here."
        res2 = extract_emails_from_text(text2)
        self.assertIsNone(res2["gmail"])
        self.assertEqual(res2["email"], "hiring@techcorp.io")

        # Case 3: No email
        text3 = "HR Manager at Retail Corp. Open to networking."
        res3 = extract_emails_from_text(text3)
        self.assertIsNone(res3["gmail"])
        self.assertIsNone(res3["email"])

    def test_hr_recruiter_filter(self):
        # Positive cases
        p1 = {"name": "Alice", "title": "Senior Technical Recruiter | Ex-Amazon"}
        self.assertTrue(is_hr_recruiter_profile(p1)[0])

        p2 = {"name": "Bob", "title": "Head of Talent Acquisition & Sourcing"}
        self.assertTrue(is_hr_recruiter_profile(p2)[0])

        p3 = {"name": "Charlie", "title": "People & Culture Lead | HRBP"}
        self.assertTrue(is_hr_recruiter_profile(p3)[0])

        # Negative cases
        p4 = {"name": "David", "title": "Senior Software Engineer (Python/Go)"}
        self.assertFalse(is_hr_recruiter_profile(p4)[0])

        p5 = {"name": "Eve", "title": "Product Marketing Manager"}
        self.assertFalse(is_hr_recruiter_profile(p5)[0])

    def test_storage_and_deduplication(self):
        test_dir = Path("./test_output")
        test_dir.mkdir(exist_ok=True)
        json_path = test_dir / "test_results.json"
        csv_path = test_dir / "test_results.csv"
        seen_path = test_dir / "test_seen.json"

        try:
            storage = StorageManager(json_path=json_path, csv_path=csv_path, seen_urls_path=seen_path)
            
            rec1 = {
                "name": "Jane Recruiter",
                "title": "Lead Talent Acquisition Specialist",
                "company": "Acme Corp",
                "location": "New York, USA",
                "linkedin_url": "https://www.linkedin.com/in/jane-recruiter",
                "gmail": "jane.talent@gmail.com"
            }
            
            storage.add_record(rec1)
            self.assertTrue(storage.is_seen("https://www.linkedin.com/in/jane-recruiter"))
            self.assertEqual(len(storage.records), 1)

            # Check JSON file written
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())

        finally:
            if test_dir.exists():
                shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
