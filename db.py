"""
AutoApply MongoDB & Local Storage Module
Provides unified permanent storage in MongoDB collection: 'emails'
(and local fallback: output/emails.json)

Schema for each document in 'emails':
{
    "email": "user@gmail.com",       # unique lowercase email identifier
    "name": "Recruiter Name",        # resolved contact or author name
    "title": "Talent Acquisition",   # headline or role
    "company": "",                   # company name if extracted
    "post_text": "...",              # context snippet from LinkedIn post
    "linkedin_url": "...",           # profile or post url
    "query": "...",                  # search query that found this lead
    "is_gmail": True,                # boolean flag (@gmail.com)
    "status": "pending",             # "pending" | "sent"
    "created_at": "ISO-8601",        # date and time lead was extracted
    "updated_at": "ISO-8601",        # last update timestamp
    "sent_at": None,                 # timestamp when application email was sent
    "subject": None,                 # cold email subject line
    "body": None                     # cold email body text
}

Features:
- Unified MongoDB collection 'emails' with unique index on 'email'.
- Automatic 1-week cleanup: Purges emails with status='sent' after 7 days so they can be re-applied to.
- Automatic migration from legacy 'hr_contacts' and 'applied_emails' collections.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("autoapply.db")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

LOCAL_EMAILS_FILE   = OUTPUT_DIR / "emails.json"
LOCAL_CONTACTS_FILE = OUTPUT_DIR / "hr_gmail_posts.json"  # legacy compatibility
LOCAL_SENT_FILE     = OUTPUT_DIR / "sent_log.json"        # legacy compatibility

# ── MongoDB Client Setup ──────────────────────────────────────────────────────
_mongo_client = None
_db = None
_is_connected = False
_migration_done = False


def get_mongo_db():
    global _mongo_client, _db, _is_connected, _migration_done
    if _db is not None:
        return _db

    uri = os.getenv("MONGODB_URI", "").strip()
    username = os.getenv("MONGODB_USERNAME", "").strip()
    password = os.getenv("MONGODB_PASSWORD", "").strip()
    db_name = os.getenv("MONGODB_DB_NAME", "autoapply").strip() or "autoapply"

    if not uri:
        logger.info("MongoDB: MONGODB_URI not set. Operating in local JSON storage mode.")
        return None

    try:
        import pymongo

        connect_uri = uri
        if username and password and "@" not in uri:
            scheme_split = uri.split("://", 1)
            scheme = scheme_split[0] if len(scheme_split) == 2 else "mongodb"
            host = scheme_split[1] if len(scheme_split) == 2 else uri
            connect_uri = f"{scheme}://{quote_plus(username)}:{quote_plus(password)}@{host}"

        client_kwargs = {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
        }
        if username and "@" not in uri:
            client_kwargs["username"] = username
            client_kwargs["password"] = password

        _mongo_client = pymongo.MongoClient(connect_uri, **client_kwargs)
        _mongo_client.admin.command('ping')
        _db = _mongo_client[db_name]
        _is_connected = True
        logger.info(f"MongoDB: Connected successfully to database '{db_name}'.")

        # Setup indexes on unified 'emails' collection
        _db.emails.create_index("email", unique=True)
        _db.emails.create_index("status")
        _db.emails.create_index("sent_at")
        _db.emails.create_index("created_at")

        # Run automatic migration from legacy collections if needed
        if not _migration_done:
            migrate_to_unified_emails(_db)
            _migration_done = True

        return _db
    except Exception as e:
        logger.warning(f"MongoDB connection failed ({e}). Operating in local storage mode.")
        _is_connected = False
        return None


def is_db_connected() -> bool:
    db = get_mongo_db()
    return db is not None


def reset_mongo_connection():
    """
    Closes any existing MongoDB connection and resets cached client state.
    Attempts reconnect immediately using the latest environment variables.
    """
    global _mongo_client, _db, _is_connected, _migration_done
    if _mongo_client is not None:
        try:
            _mongo_client.close()
        except Exception:
            pass
    _mongo_client = None
    _db = None
    _is_connected = False
    _migration_done = False
    return get_mongo_db()


# ── Helpers for Local Files ──────────────────────────────────────────────────

def _load_local_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else []


def _save_local_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write local file {path}: {e}")


# ── Migration from Legacy Collections ─────────────────────────────────────────

def migrate_to_unified_emails(db=None):
    """
    Migrates existing data from legacy 'hr_contacts' and 'applied_emails'
    into the new unified 'emails' collection. Preserves original collections as backup.
    """
    if db is None:
        db = get_mongo_db()

    now_iso = datetime.now(timezone.utc).isoformat()
    unified_records: Dict[str, Dict[str, Any]] = {}

    # 1. First load any existing records in local 'emails.json'
    local_emails = _load_local_json(LOCAL_EMAILS_FILE, [])
    for rec in local_emails:
        em = (rec.get("email") or "").lower().strip()
        if em:
            unified_records[em] = rec

    # 2. Check local sent_log.json to make sure all past sent emails are captured
    local_sent = _load_local_json(LOCAL_SENT_FILE, [])
    for s_rec in local_sent:
        em = (s_rec.get("email") or s_rec.get("to") or "").lower().strip()
        if not em:
            continue
        unified_records[em] = {
            **(unified_records.get(em) or {}),
            "email": em,
            "name": s_rec.get("name") or (unified_records.get(em) or {}).get("name") or "Recruiter",
            "title": (unified_records.get(em) or {}).get("title") or "Talent Acquisition / HR",
            "company": (unified_records.get(em) or {}).get("company") or "",
            "post_text": (unified_records.get(em) or {}).get("post_text") or "",
            "linkedin_url": (unified_records.get(em) or {}).get("linkedin_url") or "",
            "query": (unified_records.get(em) or {}).get("query") or "",
            "is_gmail": em.endswith("@gmail.com"),
            "status": "sent",
            "created_at": s_rec.get("sent_at") or (unified_records.get(em) or {}).get("created_at") or now_iso,
            "updated_at": now_iso,
            "sent_at": s_rec.get("sent_at") or (unified_records.get(em) or {}).get("sent_at") or now_iso,
            "subject": s_rec.get("subject") or (unified_records.get(em) or {}).get("subject") or "",
            "body": s_rec.get("body") or (unified_records.get(em) or {}).get("body") or "",
        }

    # 3. Check MongoDB legacy collections
    if db is not None:
        try:
            # Check applied_emails
            for app_doc in db.applied_emails.find({}, {"_id": 0}):
                em = (app_doc.get("email") or app_doc.get("to") or "").lower().strip()
                if not em:
                    continue
                unified_records[em] = {
                    **(unified_records.get(em) or {}),
                    "email": em,
                    "name": app_doc.get("name") or (unified_records.get(em) or {}).get("name") or "",
                    "title": app_doc.get("title") or (unified_records.get(em) or {}).get("title") or "Talent Acquisition / HR",
                    "company": app_doc.get("company") or (unified_records.get(em) or {}).get("company") or "",
                    "post_text": app_doc.get("post_text") or (unified_records.get(em) or {}).get("post_text") or "",
                    "linkedin_url": app_doc.get("linkedin_url") or (unified_records.get(em) or {}).get("linkedin_url") or "",
                    "query": app_doc.get("query") or (unified_records.get(em) or {}).get("query") or "",
                    "is_gmail": em.endswith("@gmail.com"),
                    "status": "sent",
                    "created_at": app_doc.get("sent_at") or (unified_records.get(em) or {}).get("created_at") or now_iso,
                    "updated_at": now_iso,
                    "sent_at": app_doc.get("sent_at") or (unified_records.get(em) or {}).get("sent_at") or now_iso,
                    "subject": app_doc.get("subject") or (unified_records.get(em) or {}).get("subject") or "",
                    "body": app_doc.get("body") or (unified_records.get(em) or {}).get("body") or "",
                }

            # Check hr_contacts
            for hr_doc in db.hr_contacts.find({}, {"_id": 0}):
                em = (hr_doc.get("email") or hr_doc.get("hr_email") or hr_doc.get("gmail") or "").lower().strip()
                if not em:
                    continue
                if em in unified_records:
                    for k in ("name", "title", "company", "post_text", "linkedin_url", "query"):
                        if hr_doc.get(k) and not unified_records[em].get(k):
                            unified_records[em][k] = hr_doc[k]
                else:
                    unified_records[em] = {
                        "email": em,
                        "name": hr_doc.get("name") or "Recruiter",
                        "title": hr_doc.get("title") or "Talent Acquisition / HR",
                        "company": hr_doc.get("company") or "",
                        "post_text": hr_doc.get("post_text") or "",
                        "linkedin_url": hr_doc.get("linkedin_url") or "",
                        "query": hr_doc.get("query") or "",
                        "is_gmail": em.endswith("@gmail.com"),
                        "status": "pending",
                        "created_at": hr_doc.get("updated_at") or now_iso,
                        "updated_at": hr_doc.get("updated_at") or now_iso,
                        "sent_at": None,
                        "subject": None,
                        "body": None,
                    }

            # Bulk upsert into db.emails (ensure 'sent' status is preserved)
            if unified_records:
                from pymongo import UpdateOne
                ops = []
                for doc in unified_records.values():
                    if doc.get("status") == "sent":
                        ops.append(UpdateOne(
                            {"email": doc["email"]},
                            {
                                "$setOnInsert": {"created_at": doc["created_at"]},
                                "$set": {
                                    "status": "sent",
                                    "sent_at": doc["sent_at"],
                                    "name": doc.get("name") or "",
                                    "title": doc.get("title") or "",
                                    "company": doc.get("company") or "",
                                    "is_gmail": doc.get("is_gmail", True),
                                    "updated_at": doc["updated_at"],
                                    "subject": doc.get("subject") or "",
                                }
                            },
                            upsert=True
                        ))
                    else:
                        ops.append(UpdateOne({"email": doc["email"]}, {"$setOnInsert": doc}, upsert=True))

                res = db.emails.bulk_write(ops, ordered=False)
                logger.info(f"Unified 'emails' collection ready: {len(unified_records)} total records (upserted {res.upserted_count}).")
        except Exception as e:
            logger.error(f"Error during legacy migration to MongoDB 'emails': {e}")

    # Also sync to local JSON
    _save_local_json(LOCAL_EMAILS_FILE, list(unified_records.values()))


# ── 1-Week Sent Email Cleanup ────────────────────────────────────────────────

def cleanup_expired_sent_emails(days: int = 7) -> int:
    """
    Finds any email with status='sent' whose sent_at date is older than `days` days,
    and removes it from the 'emails' collection and local JSON.
    This allows re-applying to recruiters if they post new opportunities in the future.
    Returns the number of deleted records.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    removed_count = 0

    # 1. Cleanup in MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            # Query for status == "sent" and sent_at <= cutoff_iso
            res = db.emails.delete_many({
                "status": "sent",
                "sent_at": {"$lte": cutoff_iso, "$ne": None}
            })
            removed_count = res.deleted_count
            if removed_count > 0:
                logger.info(f"MongoDB: Removed {removed_count} sent emails older than {days} days from 'emails'.")
        except Exception as e:
            logger.error(f"Error running 1-week cleanup on MongoDB: {e}")

    # 2. Cleanup in local storage
    local_records = _load_local_json(LOCAL_EMAILS_FILE, [])
    kept_records = []
    local_removed = 0
    for r in local_records:
        if r.get("status") == "sent" and r.get("sent_at"):
            sent_str = str(r["sent_at"])
            try:
                sent_dt = datetime.fromisoformat(sent_str.replace("Z", "+00:00"))
                if sent_dt <= cutoff:
                    local_removed += 1
                    continue
            except Exception:
                pass
        kept_records.append(r)

    if local_removed > 0:
        _save_local_json(LOCAL_EMAILS_FILE, kept_records)
        logger.info(f"Local storage: Removed {local_removed} expired sent emails.")

    return max(removed_count, local_removed)


# ── Ingest & Save Contacts ───────────────────────────────────────────────────

def save_contacts(contacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Saves and deduplicates newly scraped contacts into the 'emails' collection.
    - If email already exists with status='sent', preserves 'sent' status.
    - If email already exists with status='pending', updates metadata without resetting created_at.
    - If email is brand new, creates document with status='pending' and created_at=now.
    Runs 1-week cleanup at the end of each save.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    existing = {e["email"]: e for e in get_all_emails()}

    updated_or_new = []
    for c in contacts:
        raw_email = c.get("hr_email") or c.get("gmail") or c.get("email") or ""
        clean_email = raw_email.lower().strip()
        if not clean_email or "@" not in clean_email:
            continue

        is_gmail = clean_email.endswith("@gmail.com")
        existing_doc = existing.get(clean_email)

        if existing_doc:
            doc = {
                **existing_doc,
                "name": c.get("name") or existing_doc.get("name") or "Recruiter",
                "title": c.get("title") or existing_doc.get("title") or "",
                "company": c.get("company") or existing_doc.get("company") or "",
                "post_text": c.get("post_text") or existing_doc.get("post_text") or "",
                "linkedin_url": c.get("linkedin_url") or existing_doc.get("linkedin_url") or "",
                "query": c.get("query") or existing_doc.get("query") or "",
                "is_gmail": is_gmail,
                "updated_at": now_iso,
            }
        else:
            doc = {
                "email": clean_email,
                "name": c.get("name") or "Recruiter",
                "title": c.get("title") or "Talent Acquisition / HR",
                "company": c.get("company") or "",
                "post_text": c.get("post_text") or "",
                "linkedin_url": c.get("linkedin_url") or "",
                "query": c.get("query") or "",
                "is_gmail": is_gmail,
                "status": "pending",
                "created_at": now_iso,
                "updated_at": now_iso,
                "sent_at": None,
                "subject": None,
                "body": None,
            }

        existing[clean_email] = doc
        updated_or_new.append(doc)

    all_emails_list = list(existing.values())
    _save_local_json(LOCAL_EMAILS_FILE, all_emails_list)
    _save_local_json(LOCAL_CONTACTS_FILE, all_emails_list)

    # Sync to MongoDB Atlas
    db = get_mongo_db()
    if db is not None and updated_or_new:
        try:
            from pymongo import UpdateOne
            operations = []
            for doc in updated_or_new:
                update_fields = {k: v for k, v in doc.items() if k not in ("_id", "email")}
                if doc.get("status") == "pending":
                    operations.append(UpdateOne(
                        {"email": doc["email"]},
                        {
                            "$setOnInsert": {"created_at": doc["created_at"], "status": "pending"},
                            "$set": {
                                "name": doc["name"],
                                "title": doc["title"],
                                "company": doc["company"],
                                "post_text": doc["post_text"],
                                "linkedin_url": doc["linkedin_url"],
                                "query": doc["query"],
                                "is_gmail": doc["is_gmail"],
                                "updated_at": doc["updated_at"],
                            }
                        },
                        upsert=True
                    ))
                else:
                    operations.append(UpdateOne(
                        {"email": doc["email"]},
                        {"$set": update_fields},
                        upsert=True
                    ))

            if operations:
                db.emails.bulk_write(operations, ordered=False)
                logger.info(f"MongoDB: Upserted {len(operations)} contacts into 'emails'.")
        except Exception as e:
            logger.error(f"Error syncing contacts to MongoDB 'emails': {e}")

    # Run 1-week cleanup at the end of scraping/saving
    cleanup_expired_sent_emails(days=7)

    return all_emails_list


# ── Querying Emails ──────────────────────────────────────────────────────────

def get_all_emails() -> List[Dict[str, Any]]:
    """
    Returns all email records from MongoDB 'emails' collection (or local JSON fallback).
    """
    emails_dict = {}

    # 1. Local fallback
    local_data = _load_local_json(LOCAL_EMAILS_FILE, [])
    for d in local_data:
        em = (d.get("email") or "").lower().strip()
        if em:
            emails_dict[em] = d

    # 2. MongoDB Atlas
    db = get_mongo_db()
    if db is not None:
        try:
            for doc in db.emails.find({}, {"_id": 0}):
                em = (doc.get("email") or "").lower().strip()
                if em:
                    emails_dict[em] = {**emails_dict.get(em, {}), **doc}
        except Exception as e:
            logger.error(f"Error reading from MongoDB 'emails': {e}")

    return list(emails_dict.values())


def get_pending_gmails() -> List[Dict[str, Any]]:
    """
    Returns ONLY non-applied (pending) Gmail addresses (@gmail.com).
    Ideal for the streamlined right sidebar!
    """
    all_emails = get_all_emails()
    pending_gmails = []
    for doc in all_emails:
        em = (doc.get("email") or "").lower().strip()
        status = doc.get("status", "pending")
        is_gmail = doc.get("is_gmail") or em.endswith("@gmail.com")
        if status == "pending" and is_gmail:
            doc_copy = dict(doc)
            doc_copy["hr_email"] = em
            doc_copy["gmail"] = em
            doc_copy["is_applied"] = False
            pending_gmails.append(doc_copy)

    # Sort newest created first
    pending_gmails.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return pending_gmails


def get_all_contacts() -> List[Dict[str, Any]]:
    """
    Backwards compatibility: Returns all contacts with 'is_applied' boolean.
    """
    all_records = get_all_emails()
    res = []
    for r in all_records:
        doc = dict(r)
        em = (doc.get("email") or "").lower().strip()
        doc["hr_email"] = em
        doc["gmail"] = em
        doc["is_applied"] = (doc.get("status") == "sent")
        res.append(doc)
    return res


def get_applied_emails() -> Set[str]:
    """
    Returns a set of all lowercase email addresses that have status='sent'.
    """
    all_records = get_all_emails()
    return {
        r["email"].lower().strip()
        for r in all_records
        if r.get("status") == "sent" and r.get("email")
    }


def get_sent_emails() -> List[Dict[str, Any]]:
    """
    Returns all email records that have status='sent', sorted newest sent first.
    """
    all_emails = get_all_emails()
    sent_dict = {}
    for doc in all_emails:
        em = (doc.get("email") or "").lower().strip()
        status = doc.get("status", "pending")
        if status == "sent" and em:
            doc_copy = dict(doc)
            doc_copy["hr_email"] = em
            doc_copy["gmail"] = em
            doc_copy["is_applied"] = True
            sent_dict[em] = doc_copy

    # Also include any in local sent_log.json
    for r in _load_local_json(LOCAL_SENT_FILE, []):
        em = (r.get("email") or r.get("to") or "").lower().strip()
        if em and em not in sent_dict:
            sent_dict[em] = {
                "email": em,
                "name": r.get("name") or "Recruiter",
                "title": "Talent Acquisition / HR",
                "status": "sent",
                "sent_at": r.get("sent_at"),
                "subject": r.get("subject") or "Cold Outreach Application",
                "is_applied": True,
                "is_gmail": em.endswith("@gmail.com"),
            }

    sent_list = list(sent_dict.values())
    sent_list.sort(key=lambda x: x.get("sent_at") or x.get("updated_at") or "", reverse=True)
    return sent_list


def is_email_applied(email: str) -> bool:
    """
    Ultra-strict check: returns True if this email has already been sent to
    across MongoDB (emails & applied_emails) or any local storage logs.
    """
    clean = (email or "").lower().strip()
    if not clean:
        return False

    # 1. MongoDB Atlas checks
    db = get_mongo_db()
    if db is not None:
        try:
            # Check emails collection
            doc = db.emails.find_one({"email": clean, "status": "sent"}, {"_id": 1})
            if doc is not None:
                return True
            # Check legacy applied_emails collection
            if db.applied_emails.find_one({"email": clean}, {"_id": 1}) is not None:
                return True
        except Exception as e:
            logger.error(f"Error checking is_email_applied in Mongo: {e}")

    # 2. Local emails.json
    local_emails = _load_local_json(LOCAL_EMAILS_FILE, [])
    if any((r.get("email") or "").lower().strip() == clean and r.get("status") == "sent" for r in local_emails):
        return True

    # 3. Local sent_log.json
    local_sent = _load_local_json(LOCAL_SENT_FILE, [])
    if any((r.get("email") or r.get("to") or "").lower().strip() == clean for r in local_sent):
        return True

    # 4. Local cold_emails.json drafts
    cold_drafts = _load_local_json(OUTPUT_DIR / "cold_emails.json", [])
    if any((r.get("to_email") or "").lower().strip() == clean and r.get("status") == "sent" for r in cold_drafts):
        return True

    return False


def mark_email_applied(email: str, name: str = "", subject: str = "", body: str = "") -> bool:
    """
    Marks an email as 'sent' in MongoDB 'emails' collection and local JSON.
    Sets sent_at to the current UTC timestamp.
    """
    clean = (email or "").lower().strip()
    if not clean:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    fields = {
        "status": "sent",
        "sent_at": now_iso,
        "updated_at": now_iso,
        "subject": subject,
        "body": body,
    }
    if name:
        fields["name"] = name

    # 1. Update MongoDB Atlas
    db = get_mongo_db()
    if db is not None:
        try:
            db.emails.update_one(
                {"email": clean},
                {
                    "$set": fields,
                    "$setOnInsert": {
                        "email": clean,
                        "created_at": now_iso,
                        "is_gmail": clean.endswith("@gmail.com"),
                        "title": "Talent Acquisition / HR",
                    }
                },
                upsert=True
            )
            # Legacy applied_emails collection sync
            db.applied_emails.update_one(
                {"email": clean},
                {"$set": {"email": clean, "to": clean, "name": name, "subject": subject, "sent_at": now_iso, "status": "sent"}},
                upsert=True
            )
            logger.info(f"MongoDB: Marked {clean} as 'sent' in 'emails' collection.")
        except Exception as e:
            logger.error(f"Error marking email applied in MongoDB: {e}")

    # 2. Update local storage
    local_emails = _load_local_json(LOCAL_EMAILS_FILE, [])
    found = False
    for r in local_emails:
        if (r.get("email") or "").lower().strip() == clean:
            r.update(fields)
            found = True
            break
    if not found:
        local_emails.append({
            "email": clean,
            "created_at": now_iso,
            "is_gmail": clean.endswith("@gmail.com"),
            "title": "Talent Acquisition / HR",
            **fields
        })
    _save_local_json(LOCAL_EMAILS_FILE, local_emails)

    # Legacy sent_log.json
    local_sent = _load_local_json(LOCAL_SENT_FILE, [])
    if not any((r.get("to") or r.get("email") or "").lower().strip() == clean for r in local_sent):
        local_sent.append({"email": clean, "to": clean, "name": name, "subject": subject, "sent_at": now_iso, "status": "sent"})
        _save_local_json(LOCAL_SENT_FILE, local_sent)

    return True


def delete_email(email: str) -> bool:
    """Deletes a contact by email from both MongoDB and local JSON."""
    clean = (email or "").lower().strip()
    if not clean:
        return False

    db = get_mongo_db()
    if db is not None:
        try:
            db.emails.delete_one({"email": clean})
        except Exception as e:
            logger.error(f"Error deleting email from MongoDB: {e}")

    local_emails = _load_local_json(LOCAL_EMAILS_FILE, [])
    filtered = [r for r in local_emails if (r.get("email") or "").lower().strip() != clean]
    _save_local_json(LOCAL_EMAILS_FILE, filtered)

    legacy = _load_local_json(LOCAL_CONTACTS_FILE, [])
    _save_local_json(LOCAL_CONTACTS_FILE, [r for r in legacy if (r.get("hr_email") or r.get("gmail") or r.get("email") or "").lower().strip() != clean])

    return True


def clear_pending_emails() -> bool:
    """Clears all pending contacts, preserving already sent records."""
    db = get_mongo_db()
    if db is not None:
        try:
            db.emails.delete_many({"status": "pending"})
        except Exception as e:
            logger.error(f"Error clearing pending emails from MongoDB: {e}")

    local_emails = _load_local_json(LOCAL_EMAILS_FILE, [])
    kept = [r for r in local_emails if r.get("status") == "sent"]
    _save_local_json(LOCAL_EMAILS_FILE, kept)
    _save_local_json(LOCAL_CONTACTS_FILE, [])

    return True
