"""Unit tests for LinkedIn Scraper modules."""

import unittest
from parser import normalize_linkedin_url, extract_emails_from_text
from filter import is_hr_recruiter_profile
from storage import StorageManager
from pipeline.name_cleaner import clean_name, clean_first_name
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


class TestNameCleaner(unittest.TestCase):
    """Tests for pipeline.name_cleaner — fixes the 'Rahulsingh' bug."""

    def test_camel_case_split(self):
        """CamelCase concatenation must be split on case boundary."""
        self.assertEqual(clean_name("RahulSingh"), "Rahul Singh")
        self.assertEqual(clean_name("PriyaSharma"), "Priya Sharma")
        self.assertEqual(clean_name("AnkitGupta"), "Ankit Gupta")

    def test_all_lowercase_concat(self):
        """All-lowercase run-together names split via atom dictionary."""
        self.assertEqual(clean_name("rahulsingh"), "Rahul Singh")
        self.assertEqual(clean_name("priyasharma"), "Priya Sharma")

    def test_all_caps(self):
        """ALL-CAPS names should be title-cased."""
        self.assertEqual(clean_name("PRIYA SHARMA"), "Priya Sharma")
        self.assertEqual(clean_name("JOHN DOE"), "John Doe")

    def test_already_clean_passthrough(self):
        """Properly formatted names pass through unchanged."""
        self.assertEqual(clean_name("John Doe"), "John Doe")
        self.assertEqual(clean_name("Priya Sharma"), "Priya Sharma")
        self.assertEqual(clean_name("Rahul Singh"), "Rahul Singh")

    def test_extra_whitespace(self):
        """Leading, trailing, and internal extra spaces are collapsed."""
        self.assertEqual(clean_name("  John  Doe  "), "John Doe")

    def test_single_name(self):
        """A single name token is title-cased without error."""
        self.assertEqual(clean_name("rahul"), "Rahul")
        self.assertEqual(clean_name("PRIYA"), "Priya")

    def test_empty_and_none_fallback(self):
        """Empty or None input returns the fallback string."""
        self.assertEqual(clean_name(""), "Hiring Manager")
        self.assertEqual(clean_name(None), "Hiring Manager")
        self.assertEqual(clean_name("   "), "Hiring Manager")

    def test_clean_first_name(self):
        """clean_first_name returns only the first token."""
        self.assertEqual(clean_first_name("Rahul Singh"), "Rahul")
        self.assertEqual(clean_first_name("RahulSingh"), "Rahul")
        self.assertEqual(clean_first_name(""), "there")
        self.assertEqual(clean_first_name(None), "there")

    def test_hyphenated_name(self):
        """Hyphenated names are preserved correctly."""
        result = clean_name("Mary-Jane Watson")
        self.assertIn("Mary", result)

    def test_numeric_noise_stripped(self):
        """Digits and punctuation noise are stripped."""
        result = clean_name("John123 Doe!")
        self.assertEqual(result, "John Doe")


class TestMultiUserDb(unittest.TestCase):
    """Tests for multi-user MongoDB and local storage operations in db.py."""

    def setUp(self):
        self.test_user_a = "test_user_alpha"
        self.test_user_b = "test_user_beta"
        import db
        # Clean up any leftover test data
        db.clear_user_data(self.test_user_a)
        db.clear_user_data(self.test_user_b)

    def tearDown(self):
        import db
        db.clear_user_data(self.test_user_a)
        db.clear_user_data(self.test_user_b)
        # Clean up local test directories if created
        u_dir_a = db._get_user_dir(self.test_user_a)
        u_dir_b = db._get_user_dir(self.test_user_b)
        if u_dir_a.exists():
            shutil.rmtree(u_dir_a, ignore_errors=True)
        if u_dir_b.exists():
            shutil.rmtree(u_dir_b, ignore_errors=True)

    def test_sanitize_username(self):
        """Username sanitization for collection safety."""
        from db import sanitize_username
        self.assertEqual(sanitize_username("parth@gmail.com"), "parth_gmail_com")
        self.assertEqual(sanitize_username("parth.dev.123@gmail.com"), "parth_dev_123_gmail_com")
        self.assertEqual(sanitize_username("User-Name_99"), "user-name_99")
        self.assertEqual(sanitize_username(""), "legacy")
        self.assertEqual(sanitize_username(None), "legacy")

    def test_multi_user_isolation(self):
        """User A's pending and sent emails do not appear in User B's collections."""
        import db
        leads_a = [
            {"email": "lead1@gmail.com", "name": "Lead One", "post_text": "Hiring ML"},
            {"email": "lead2@gmail.com", "name": "Lead Two", "post_text": "Hiring Python"},
        ]
        leads_b = [
            {"email": "lead3@gmail.com", "name": "Lead Three", "post_text": "Hiring DevOps"},
        ]
        db.save_contacts(leads_a, username=self.test_user_a)
        db.save_contacts(leads_b, username=self.test_user_b)

        pending_a = [c["email"] for c in db.get_pending_gmails(self.test_user_a)]
        pending_b = [c["email"] for c in db.get_pending_gmails(self.test_user_b)]

        self.assertIn("lead1@gmail.com", pending_a)
        self.assertIn("lead2@gmail.com", pending_a)
        self.assertNotIn("lead3@gmail.com", pending_a)

        self.assertIn("lead3@gmail.com", pending_b)
        self.assertNotIn("lead1@gmail.com", pending_b)

    def test_sent_email_no_context_and_removal_from_pending(self):
        """Sent collection stores ONLY email + timestamp (no context), and removes from pending."""
        import db
        lead = [{"email": "recruiter@gmail.com", "name": "HR Jane", "post_text": "Secret details", "title": "Lead HR"}]
        db.save_contacts(lead, username=self.test_user_a)

        # Mark applied
        db.mark_email_applied("recruiter@gmail.com", name="HR Jane", subject="App", body="Letter", username=self.test_user_a)

        # Check pending: must be removed
        pending = [c["email"] for c in db.get_pending_gmails(self.test_user_a)]
        self.assertNotIn("recruiter@gmail.com", pending)

        # Check sent: must exist and have NO context (no post_text, no body, no subject)
        sent = db.get_sent_emails(self.test_user_a)
        self.assertEqual(len(sent), 1)
        sent_doc = sent[0]
        self.assertEqual(sent_doc["email"], "recruiter@gmail.com")
        self.assertIn("sent_at", sent_doc)
        self.assertNotIn("post_text", sent_doc)
        self.assertNotIn("body", sent_doc)
        self.assertNotIn("subject", sent_doc)

        # Strict check: is_email_applied returns True for A, False for B
        self.assertTrue(db.is_email_applied("recruiter@gmail.com", self.test_user_a))
        self.assertFalse(db.is_email_applied("recruiter@gmail.com", self.test_user_b))

    def test_mongo_document_contains_username(self):
        """Every record in emails and applied_emails must have username field."""
        import db
        lead = [{"email": "ceo@startup.ai", "name": "Founder", "title": "CEO", "post_text": "Hiring"}]
        db.save_contacts(lead, username=self.test_user_a)

        mongo = db.get_mongo_db()
        if mongo is not None:
            email_doc = mongo.emails.find_one({"email": "ceo@startup.ai"})
            self.assertIsNotNone(email_doc)
            self.assertEqual(email_doc.get("username"), self.test_user_a)

        db.mark_email_applied("ceo@startup.ai", username=self.test_user_a)

        if mongo is not None:
            applied_doc = mongo.applied_emails.find_one({"email": "ceo@startup.ai"})
            self.assertIsNotNone(applied_doc)
            self.assertEqual(applied_doc.get("username"), self.test_user_a)
            self.assertNotIn("post_text", applied_doc)
            self.assertNotIn("body", applied_doc)


class TestZeroDataLeakage(unittest.TestCase):
    """Tests ensuring zero data leakage for unauthenticated requests."""

    def setUp(self):
        import server
        self.app = server.app.test_client()

    def test_unauthenticated_contacts_empty(self):
        """No gmails or contacts leaked without sign-in."""
        res = self.app.get("/api/contacts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), [])

    def test_unauthenticated_applied_emails_empty(self):
        """No sent emails leaked without sign-in."""
        res = self.app.get("/api/applied-emails")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), [])

    def test_unauthenticated_drafts_empty(self):
        """No cold email drafts leaked without sign-in."""
        res = self.app.get("/api/emails")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), [])

    def test_unauthenticated_profile_sanitized(self):
        """No candidate name, phone, resume or API keys leaked without sign-in."""
        res = self.app.get("/api/profile")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get("authenticated"))
        self.assertEqual(data.get("name"), "")
        self.assertEqual(data.get("phone"), "")
        self.assertEqual(data.get("gmail_sender"), "")
        self.assertEqual(data.get("groq_api_key"), "")

    def test_unauthenticated_db_status_zeroes(self):
        """Counts are strictly zero without sign-in."""
        res = self.app.get("/api/db-status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get("authenticated"))
        self.assertEqual(data.get("pending_count"), 0)
        self.assertEqual(data.get("sent_count"), 0)

    def test_spoofed_legacy_header_blocked(self):
        """Sending X-User-Name: legacy is rejected to prevent pre-migration data leakage."""
        res = self.app.get("/api/contacts", headers={"X-User-Name": "legacy"})
        self.assertEqual(res.get_json(), [])

    def test_resolve_contact_name_rejects_email_usernames(self):
        """Email usernames like bharatkp or softwared250 must never be derived as names."""
        from server import resolve_contact_name, derive_name_from_email
        self.assertEqual(derive_name_from_email("bharatkp@gmail.com"), "")
        self.assertEqual(derive_name_from_email("softwared250@gmail.com"), "")
        # Empty/garbage name with email must return empty string
        self.assertEqual(resolve_contact_name("", "bharatkp@gmail.com"), "")
        self.assertEqual(resolve_contact_name("bharatkp", "bharatkp@gmail.com"), "")
        self.assertEqual(resolve_contact_name("Showcase Your Creativity", "bharatkp@gmail.com"), "")
        # Valid name is preserved
        self.assertEqual(resolve_contact_name("Bharat Kancharla", "bharat.kancharla@gmail.com"), "Bharat Kancharla")

    def test_recipient_salutation_resolution(self):
        """Test Groq-based salutation resolution hierarchy: Name -> Gender -> None."""
        from pipeline.recipient_resolver import resolve_salutation_with_groq
        # 1. Real human name
        sal = resolve_salutation_with_groq(
            author_name="Bharat Kancharla",
            title="IT Recruiter",
            post_text="Hiring Gen AI engineers. Send resumes to bharat.kancharla@gmail.com",
            email="bharat.kancharla@gmail.com"
        )
        self.assertEqual(sal, "Bharat")

        # 2. Gender pronouns
        sal_female = resolve_salutation_with_groq(
            author_name="HR Recruiter",
            title="Technical Recruiter (She/Her)",
            post_text="Hiring engineers. Reach out to hr@company.com",
            email="hr@company.com"
        )
        self.assertEqual(sal_female, "Ma'am")

        # 3. Garbage headline + email username -> None (so email opens with Hi,)
        sal_none = resolve_salutation_with_groq(
            author_name="Showcase Your Creativity",
            title="Staffing",
            post_text="Hiring engineers. Send resume to bharatkp@gmail.com",
            email="bharatkp@gmail.com"
        )
        self.assertIsNone(sal_none)


if __name__ == "__main__":
    unittest.main()

