"""
AutoApply Multi-User MongoDB & Local Storage Module

Multi-Tenant Architecture:
- Two unified MongoDB collections partitioned by the `username` field:
  1. `emails`: Full contact record with all context (name, email, title, company,
     post_text, linkedin_url, query, status, created_at, updated_at, username).
     Capacity limit: 100 entries per username (FIFO: oldest deleted when limit exceeded).
  2. `applied_emails`: Minimal record containing strictly ONLY `username`, `email`,
     and `sent_at` timestamp. Strictly NO CONTEXT (no post_text, no resume, no body).
     Capacity limit: 200 entries per username (FIFO: oldest deleted when limit exceeded).
- Existing historical data is migrated to `username: "legacy"` in both collections.
- No original records are deleted.
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("autoapply.db")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

USERS_DIR = OUTPUT_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)

# Legacy file paths for backward compatibility
LOCAL_EMAILS_FILE   = OUTPUT_DIR / "emails.json"
LOCAL_CONTACTS_FILE = OUTPUT_DIR / "hr_gmail_posts.json"
LOCAL_SENT_FILE     = OUTPUT_DIR / "sent_log.json"

PENDING_LIMIT = 100
SENT_LIMIT = 200

EMAILS_COLLECTION = "emails"
APPLIED_COLLECTION = "applied_emails"

# ── Database Client States (PostgreSQL / MongoDB) ─────────────────────────────
_mongo_client = None
_db = None
_is_connected = False
_migration_done = False

_pg_conn = None
_pg_schema_initialized = False


def _resolve_pg_url() -> Optional[str]:
    db_url = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()

    # If DATABASE_URL contains unexpanded Railway template variables like ${{PGUSER}}
    if db_url and "${{" in db_url:
        pghost = os.getenv("PGHOST") or os.getenv("RAILWAY_PRIVATE_DOMAIN") or ""
        pgport = os.getenv("PGPORT") or "5432"
        pguser = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "postgres"
        pgpass = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
        pgdb = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or "railway"
        if pghost and pgpass:
            db_url = f"postgresql://{quote_plus(pguser)}:{quote_plus(pgpass)}@{pghost}:{pgport}/{pgdb}"
        else:
            db_url = ""

    if not db_url:
        pghost = os.getenv("PGHOST") or os.getenv("RAILWAY_PRIVATE_DOMAIN") or ""
        pgport = os.getenv("PGPORT") or "5432"
        pguser = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "postgres"
        pgpass = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
        pgdb = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or "railway"
        if pghost and pgpass:
            db_url = f"postgresql://{quote_plus(pguser)}:{quote_plus(pgpass)}@{pghost}:{pgport}/{pgdb}"

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    return db_url or None


def get_pg_conn():
    """Returns an active PostgreSQL connection if DATABASE_URL or POSTGRES_URL is configured."""
    global _pg_conn, _pg_schema_initialized
    if _pg_conn is not None:
        try:
            if not _pg_conn.closed:
                return _pg_conn
        except Exception:
            _pg_conn = None

    db_url = _resolve_pg_url()
    if not db_url:
        return None

    try:
        import psycopg2
        import psycopg2.extras

        _pg_conn = psycopg2.connect(db_url, connect_timeout=8)
        _pg_conn.autocommit = True

        if not _pg_schema_initialized:
            _init_pg_schema(_pg_conn)
            _pg_schema_initialized = True

        logger.info("PostgreSQL: Connected successfully to database.")
        return _pg_conn
    except Exception as e:
        logger.warning(f"PostgreSQL connection failed ({e}). Checking alternative storage...")
        _pg_conn = None
        return None


def _init_pg_schema(conn):
    """Initializes PostgreSQL tables and indexes for multi-tenant AutoApply data."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                username VARCHAR(255) PRIMARY KEY,
                profile_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                name TEXT,
                title TEXT,
                company TEXT,
                post_text TEXT,
                linkedin_url TEXT,
                query TEXT,
                is_gmail BOOLEAN DEFAULT TRUE,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_user_contact_email UNIQUE (username, email)
            );
            ALTER TABLE contacts ADD COLUMN IF NOT EXISTS is_gmail BOOLEAN DEFAULT TRUE;
            ALTER TABLE contacts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
            CREATE INDEX IF NOT EXISTS idx_contacts_user_status ON contacts(username, status);
            CREATE TABLE IF NOT EXISTS applied_emails (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                name TEXT,
                subject TEXT,
                body TEXT,
                sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_user_applied_email UNIQUE (username, email)
            );
            CREATE INDEX IF NOT EXISTS idx_applied_user ON applied_emails(username);
            """)
        logger.info("PostgreSQL: Tables and indexes verified successfully.")
    except Exception as e:
        logger.warning(f"PostgreSQL schema initialization error: {e}")


def save_user_profile_db(username: str, profile_data: dict) -> bool:
    """
    Persists user profile (including API keys, resume metadata, sender email)
    to PostgreSQL (first) or MongoDB, and local JSON backup.
    """
    u = sanitize_username(username)
    saved_somewhere = False

    # 1. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            import psycopg2.extras
            with pg.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_profiles (username, profile_data, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (username)
                    DO UPDATE SET profile_data = EXCLUDED.profile_data, updated_at = CURRENT_TIMESTAMP;
                """, (u, psycopg2.extras.Json(profile_data)))
            logger.info(f"PostgreSQL: Saved user profile for '{u}'.")
            saved_somewhere = True
        except Exception as e:
            logger.error(f"PostgreSQL error saving profile for '{u}': {e}")

    # 2. MongoDB
    mdb = get_mongo_db()
    if mdb is not None:
        try:
            mdb["user_profiles"].update_one(
                {"username": u},
                {"$set": {"username": u, "profile_data": profile_data, "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True
            )
            logger.info(f"MongoDB: Saved user profile for '{u}'.")
            saved_somewhere = True
        except Exception as e:
            logger.error(f"MongoDB error saving profile for '{u}': {e}")

    # 3. Local file backup
    try:
        p_file = _get_user_dir(u) / "profile.json"
        _save_local_json(p_file, profile_data)
    except Exception:
        pass

    return saved_somewhere


def load_user_profile_db(username: str) -> Optional[dict]:
    """
    Loads user profile from PostgreSQL (first) or MongoDB.
    Returns None if not found in database.
    """
    u = sanitize_username(username)

    # 1. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("SELECT profile_data FROM user_profiles WHERE username = %s LIMIT 1;", (u,))
                row = cur.fetchone()
                if row and row[0]:
                    data = row[0]
                    if isinstance(data, str):
                        data = json.loads(data)
                    return data
        except Exception as e:
            logger.error(f"PostgreSQL error loading profile for '{u}': {e}")

    # 2. MongoDB
    mdb = get_mongo_db()
    if mdb is not None:
        try:
            doc = mdb["user_profiles"].find_one({"username": u})
            if doc and "profile_data" in doc:
                return doc["profile_data"]
        except Exception as e:
            logger.error(f"MongoDB error loading profile for '{u}': {e}")

    # 3. Local file fallback
    p_file = _get_user_dir(u) / "profile.json"
    if p_file.exists():
        return _load_local_json(p_file, None)

    return None


def sanitize_username(username: Optional[str]) -> str:
    """
    Sanitizes a username or email address into a safe MongoDB username value.
    E.g. 'parthsrivastava6112004@gmail.com' -> 'parthsrivastava6112004_gmail_com'
    'Alex-Dev' -> 'alex_dev'
    Empty/None -> 'legacy'
    """
    if not username:
        return "legacy"
    s = str(username).strip().lower()
    clean = re.sub(r'[^a-z0-9_-]', '_', s)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean or "legacy"


def get_pending_collection_name(username: Optional[str] = "legacy") -> str:
    return EMAILS_COLLECTION


def get_sent_collection_name(username: Optional[str] = "legacy") -> str:
    return APPLIED_COLLECTION


def get_mongo_db():
    global _mongo_client, _db, _is_connected, _migration_done
    if _db is not None:
        return _db

    uri = os.getenv("MONGODB_URI", "").strip()
    username = os.getenv("MONGODB_USERNAME", "").strip()
    password = os.getenv("MONGODB_PASSWORD", "").strip()
    db_name = os.getenv("MONGODB_DB_NAME", "autoapply").strip() or "autoapply"

    if not uri:
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

        # Run automatic legacy migration if needed
        if not _migration_done:
            _migration_done = True
            try:
                migrate_legacy_data(_db)
            except Exception as e:
                logger.warning(f"Error during legacy migration: {e}")

        return _db
    except Exception as e:
        logger.warning(f"MongoDB connection failed ({e}). Operating in alternative storage mode.")
        _is_connected = False
        return None


def is_db_connected() -> bool:
    if get_pg_conn() is not None:
        return True
    return get_mongo_db() is not None


def get_active_db_type() -> str:
    if get_pg_conn() is not None:
        return "PostgreSQL"
    if get_mongo_db() is not None:
        return "MongoDB"
    return "Local Storage"


def reset_mongo_connection():
    global _mongo_client, _db, _is_connected, _migration_done, _pg_conn, _pg_schema_initialized
    if _mongo_client is not None:
        try:
            _mongo_client.close()
        except Exception:
            pass
    if _pg_conn is not None:
        try:
            _pg_conn.close()
        except Exception:
            pass
    _mongo_client = None
    _db = None
    _pg_conn = None
    _pg_schema_initialized = False
    _is_connected = False
    _migration_done = False


# ── Local File Helpers ────────────────────────────────────────────────────────

def _load_local_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else []


def _save_local_json(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write local file {path}: {e}")


def _get_user_dir(username: Optional[str]) -> Path:
    u = sanitize_username(username)
    d = USERS_DIR / u
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_user_pending_file(username: Optional[str]) -> Path:
    return _get_user_dir(username) / "pending.json"


def _get_user_sent_file(username: Optional[str]) -> Path:
    return _get_user_dir(username) / "sent.json"


# ── Capacity & FIFO Enforcement Helpers ──────────────────────────────────────

def _enforce_mongo_cap(col, max_limit: int, sort_field: str = "created_at", username: Optional[str] = None):
    """
    Enforces FIFO cap for a specific username in the shared collection.
    If total documents for this username > max_limit, deletes the oldest records by sort_field ascending.
    """
    try:
        query = {"username": sanitize_username(username)} if username else {}
        count = col.count_documents(query)
        if count > max_limit:
            excess = count - max_limit
            oldest_docs = list(col.find(query, {"_id": 1}).sort([(sort_field, 1), ("_id", 1)]).limit(excess))
            if oldest_docs:
                oldest_ids = [d["_id"] for d in oldest_docs]
                res = col.delete_many({"_id": {"$in": oldest_ids}})
                logger.info(f"Enforced FIFO cap on {col.name} for user '{username}': deleted {res.deleted_count} oldest items (limit {max_limit}).")
    except Exception as e:
        logger.error(f"Error enforcing FIFO cap on {col.name} for user '{username}': {e}")


def _enforce_local_cap(path: Path, max_limit: int, sort_field: str = "created_at") -> List[Dict[str, Any]]:
    """
    Enforces FIFO cap on a local JSON file.
    Sorts newest first and keeps only the latest max_limit records.
    """
    records = _load_local_json(path, [])
    records.sort(key=lambda x: str(x.get(sort_field) or ""), reverse=True)
    if len(records) > max_limit:
        records = records[:max_limit]
        _save_local_json(path, records)
    return records


# ── Legacy Data Migration ─────────────────────────────────────────────────────

def migrate_legacy_data(db=None):
    """
    Migrates historical data in 'emails' and 'applied_emails' so every record has
    a valid `username` field (defaulting to 'legacy').
    Strips context from 'applied_emails' so it strictly contains username, email, sent_at.
    Preserves all existing records.
    """
    if db is None:
        db = get_mongo_db()
    if db is None:
        return

    try:
        # Drop legacy single-user unique index on email if present
        for col in (db.emails, db.applied_emails):
            try:
                for idx in col.list_indexes():
                    if idx.get("name") == "email_1" and idx.get("unique"):
                        col.drop_index("email_1")
                        logger.info(f"Dropped obsolete unique index 'email_1' from {col.name}")
            except Exception:
                pass

        # 1. Update emails collection: tag documents missing username with 'legacy'
        db.emails.update_many({"username": {"$exists": False}}, {"$set": {"username": "legacy"}})
        db.emails.create_index([("username", 1), ("email", 1)], unique=True)
        db.emails.create_index([("username", 1), ("created_at", 1)])

        # 2. Update applied_emails collection: tag documents missing username with 'legacy'
        db.applied_emails.update_many({"username": {"$exists": False}}, {"$set": {"username": "legacy"}})
        # Strip context fields from applied_emails (strictly keep email, sent_at, username)
        db.applied_emails.update_many(
            {},
            {"$unset": {"body": "", "subject": "", "post_text": "", "company": "", "name": "", "to": "", "status": ""}}
        )
        db.applied_emails.create_index([("username", 1), ("email", 1)], unique=True)
        db.applied_emails.create_index([("username", 1), ("sent_at", 1)])

        # 3. Clean up any temporary collections if present
        temp_cols = [c for c in db.list_collection_names() if c.startswith("pending_") or c.startswith("sent_")]
        for tc in temp_cols:
            db[tc].drop()

        logger.info("Database migration complete: 'emails' and 'applied_emails' indexed and partitioned by username.")
    except Exception as e:
        logger.error(f"Error during legacy migration: {e}")


def migrate_to_legacy_collections(db=None):
    """Alias for backwards compatibility."""
    return migrate_legacy_data(db)


# ── Ingest & Save Contacts (Pending Leads, Limit 100) ─────────────────────────

def save_contacts(contacts: List[Dict[str, Any]], username: str = "legacy") -> List[Dict[str, Any]]:
    """
    Saves newly scraped recruiter leads into the unified `emails` collection
    under the user's `username`.
    Enforces a strict maximum capacity of 100 entries per username (FIFO: oldest deleted when exceeded).
    If an email is already present in applied_emails for this username, it is skipped.
    """
    u = sanitize_username(username)
    now_iso = datetime.now(timezone.utc).isoformat()

    applied_set = get_applied_emails(username=u)

    # 1. Load existing pending records for this user
    local_pending_file = _get_user_pending_file(u)
    existing_pending = {e["email"]: e for e in get_pending_gmails(username=u)}

    new_or_updated = []
    base_epoch = time.time()
    for idx, c in enumerate(contacts):
        raw_email = c.get("hr_email") or c.get("gmail") or c.get("email") or ""
        clean_email = raw_email.lower().strip()
        if not clean_email or "@" not in clean_email:
            continue

        # Strictly skip if already sent by this user
        if clean_email in applied_set:
            continue

        is_gmail = clean_email.endswith("@gmail.com")
        existing_doc = existing_pending.get(clean_email)
        item_iso = datetime.fromtimestamp(base_epoch + (idx * 0.001), tz=timezone.utc).isoformat()

        if existing_doc:
            doc = {
                **existing_doc,
                "username": u,
                "name": c.get("name") or existing_doc.get("name") or "Recruiter",
                "title": c.get("title") or existing_doc.get("title") or "",
                "company": c.get("company") or existing_doc.get("company") or "",
                "post_text": c.get("post_text") or existing_doc.get("post_text") or "",
                "linkedin_url": c.get("linkedin_url") or existing_doc.get("linkedin_url") or "",
                "query": c.get("query") or existing_doc.get("query") or "",
                "is_gmail": is_gmail,
                "status": "pending",
                "updated_at": item_iso,
            }
        else:
            doc = {
                "username": u,
                "email": clean_email,
                "name": c.get("name") or "Recruiter",
                "title": c.get("title") or "Talent Acquisition / HR",
                "company": c.get("company") or "",
                "post_text": c.get("post_text") or "",
                "linkedin_url": c.get("linkedin_url") or "",
                "query": c.get("query") or "",
                "is_gmail": is_gmail,
                "status": "pending",
                "created_at": item_iso,
                "updated_at": item_iso,
            }

        existing_pending[clean_email] = doc
        new_or_updated.append(doc)

    # 2. Sync to PostgreSQL if connected
    pg = get_pg_conn()
    if pg is not None and new_or_updated:
        try:
            with pg.cursor() as cur:
                for doc in new_or_updated:
                    cur.execute("""
                        INSERT INTO contacts (username, email, name, title, company, post_text, linkedin_url, query, status, is_gmail, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username, email)
                        DO UPDATE SET
                            name = EXCLUDED.name,
                            title = EXCLUDED.title,
                            company = EXCLUDED.company,
                            post_text = EXCLUDED.post_text,
                            linkedin_url = EXCLUDED.linkedin_url,
                            query = EXCLUDED.query,
                            status = 'pending',
                            updated_at = EXCLUDED.updated_at;
                    """, (
                        u,
                        doc["email"],
                        doc.get("name") or "Recruiter",
                        doc.get("title") or "",
                        doc.get("company") or "",
                        doc.get("post_text") or "",
                        doc.get("linkedin_url") or "",
                        doc.get("query") or "",
                        "pending",
                        doc.get("is_gmail", True),
                        doc.get("created_at"),
                        doc.get("updated_at")
                    ))
                # Enforce strict 100 capacity limit for this user in PostgreSQL
                cur.execute("""
                    DELETE FROM contacts
                    WHERE username = %s AND id NOT IN (
                        SELECT id FROM contacts
                        WHERE username = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                    );
                """, (u, u, PENDING_LIMIT))
            logger.info(f"PostgreSQL: Saved {len(new_or_updated)} contacts for '{u}'.")
        except Exception as e:
            logger.error(f"Error syncing contacts to PostgreSQL for '{u}': {e}")

    # 3. Sync to MongoDB Atlas
    db = get_mongo_db()
    if db is not None and new_or_updated:
        try:
            from pymongo import UpdateOne
            p_col = db[EMAILS_COLLECTION]
            p_col.create_index([("username", 1), ("email", 1)], unique=True)
            p_col.create_index([("username", 1), ("created_at", 1)])

            ops = []
            for doc in new_or_updated:
                doc["username"] = u
                update_fields = {k: v for k, v in doc.items() if k not in ("_id", "email", "username", "status", "created_at")}
                ops.append(UpdateOne(
                    {"username": u, "email": doc["email"]},
                    {
                        "$setOnInsert": {"username": u, "created_at": doc["created_at"], "status": "pending"},
                        "$set": update_fields,
                    },
                    upsert=True
                ))

            if ops:
                p_col.bulk_write(ops, ordered=False)
                # Enforce strict 100 capacity limit for this user
                _enforce_mongo_cap(p_col, PENDING_LIMIT, "created_at", username=u)
        except Exception as e:
            logger.error(f"Error syncing contacts to MongoDB '{EMAILS_COLLECTION}' for '{u}': {e}")

    # 4. Enforce limit locally and save
    all_pending = list(existing_pending.values())
    all_pending.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    all_pending = all_pending[:PENDING_LIMIT]
    _save_local_json(local_pending_file, all_pending)

    # If legacy user, also sync legacy files for backwards compatibility
    if u == "legacy":
        _save_local_json(LOCAL_EMAILS_FILE, all_pending)
        _save_local_json(LOCAL_CONTACTS_FILE, all_pending)

    return all_pending


# ── Mark Email Applied (Sent Log, Limit 200, NO CONTEXT) ──────────────────────

def mark_email_applied(email: str, name: str = "", subject: str = "", body: str = "", username: str = "legacy") -> bool:
    """
    Marks an email as sent for the given user.
    - Adds minimal record { "username": u, "email": clean, "sent_at": now_iso } to `applied_emails`.
      Strictly NO CONTEXT (no post_text, no body, no resume).
    - Removes the contact from `emails` / `contacts` for this user.
    - Enforces a strict maximum capacity of 200 entries per username (FIFO: oldest deleted when exceeded).
    """
    clean = (email or "").lower().strip()
    if not clean:
        return False

    u = sanitize_username(username)
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Update PostgreSQL if connected
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                # Add to applied_emails
                cur.execute("""
                    INSERT INTO applied_emails (username, email, name, subject, body, sent_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (username, email)
                    DO UPDATE SET sent_at = EXCLUDED.sent_at;
                """, (u, clean, name or "", subject or "", body or "", now_iso))
                # Remove from contacts
                cur.execute("DELETE FROM contacts WHERE username = %s AND email = %s;", (u, clean))
                # Enforce limit of 200 per username
                cur.execute("""
                    DELETE FROM applied_emails
                    WHERE username = %s AND id NOT IN (
                        SELECT id FROM applied_emails
                        WHERE username = %s
                        ORDER BY sent_at DESC, id DESC
                        LIMIT %s
                    );
                """, (u, u, SENT_LIMIT))
            logger.info(f"PostgreSQL: Recorded sent email {clean} for '{u}' & removed from pending.")
        except Exception as e:
            logger.error(f"Error marking email applied in PostgreSQL for '{u}': {e}")

    # 2. Update MongoDB Atlas
    db = get_mongo_db()
    if db is not None:
        try:
            s_col = db[APPLIED_COLLECTION]
            s_col.create_index([("username", 1), ("email", 1)], unique=True)
            s_col.create_index([("username", 1), ("sent_at", 1)])

            # Store ONLY username, email, and sent_at — strictly NO CONTEXT
            s_col.update_one(
                {"username": u, "email": clean},
                {"$set": {"username": u, "email": clean, "sent_at": now_iso}},
                upsert=True
            )

            # Remove from emails collection for this user
            p_col = db[EMAILS_COLLECTION]
            p_col.delete_one({"username": u, "email": clean})

            # Enforce 200 limit on applied_emails for this user
            _enforce_mongo_cap(s_col, SENT_LIMIT, "sent_at", username=u)
            logger.info(f"MongoDB: Recorded sent email {clean} for '{u}' in '{APPLIED_COLLECTION}' (no context) & removed from pending.")
        except Exception as e:
            logger.error(f"Error marking email applied in MongoDB '{APPLIED_COLLECTION}': {e}")

    # 3. Update local storage
    # Remove from local pending
    local_pending_file = _get_user_pending_file(u)
    pending_records = _load_local_json(local_pending_file, [])
    pending_records = [r for r in pending_records if (r.get("email") or "").lower().strip() != clean]
    _save_local_json(local_pending_file, pending_records)

    # Add to local sent (minimal record: username + email + sent_at only)
    local_sent_file = _get_user_sent_file(u)
    sent_records = _load_local_json(local_sent_file, [])
    sent_records = [r for r in sent_records if (r.get("email") or "").lower().strip() != clean]
    sent_records.insert(0, {"username": u, "email": clean, "sent_at": now_iso})
    # Enforce 200 limit locally
    if len(sent_records) > SENT_LIMIT:
        sent_records = sent_records[:SENT_LIMIT]
    _save_local_json(local_sent_file, sent_records)

    # Backward compatibility for legacy user
    if u == "legacy":
        local_sent_legacy = _load_local_json(LOCAL_SENT_FILE, [])
        if not any((r.get("email") or r.get("to") or "").lower().strip() == clean for r in local_sent_legacy):
            local_sent_legacy.insert(0, {"email": clean, "to": clean, "name": name, "subject": subject, "sent_at": now_iso, "status": "sent"})
            _save_local_json(LOCAL_SENT_FILE, local_sent_legacy[:SENT_LIMIT])

    return True


# ── Checking Applied Status ───────────────────────────────────────────────────

def is_email_applied(email: str, username: str = "legacy") -> bool:
    """
    Checks whether this email has already been sent to by the given user.
    Inspects PostgreSQL, MongoDB, and local sent records.
    """
    clean = (email or "").lower().strip()
    if not clean:
        return False

    u = sanitize_username(username)

    # 1. PostgreSQL check
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("SELECT 1 FROM applied_emails WHERE username = %s AND email = %s LIMIT 1;", (u, clean))
                if cur.fetchone():
                    return True
        except Exception as e:
            logger.error(f"Error checking is_email_applied in PostgreSQL: {e}")

    # 2. MongoDB check
    db = get_mongo_db()
    if db is not None:
        try:
            s_col = db[APPLIED_COLLECTION]
            if s_col.find_one({"username": u, "email": clean}, {"_id": 1}) is not None:
                return True
        except Exception as e:
            logger.error(f"Error checking is_email_applied in Mongo: {e}")

    # 3. Local user sent file check
    user_sent = _load_local_json(_get_user_sent_file(u), [])
    if any((r.get("email") or "").lower().strip() == clean for r in user_sent):
        return True

    # 4. Legacy file fallback
    if u == "legacy":
        legacy_sent = _load_local_json(LOCAL_SENT_FILE, [])
        if any((r.get("email") or r.get("to") or "").lower().strip() == clean for r in legacy_sent):
            return True

    return False


# ── Querying Leads & Emails ───────────────────────────────────────────────────

def get_pending_gmails(username: str = "legacy") -> List[Dict[str, Any]]:
    """
    Returns pending Gmail addresses for the given user, sorted newest first.
    Capacity capped at 100.
    """
    u = sanitize_username(username)
    records_dict: Dict[str, Dict[str, Any]] = {}

    # 1. Local storage
    for r in _load_local_json(_get_user_pending_file(u), []):
        em = (r.get("email") or "").lower().strip()
        if em:
            records_dict[em] = r

    # 2. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("""
                    SELECT email, name, title, company, post_text, linkedin_url, query, status, created_at, is_gmail
                    FROM contacts
                    WHERE username = %s AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, (u, PENDING_LIMIT))
                for row in cur.fetchall():
                    em = (row[0] or "").lower().strip()
                    if em:
                        records_dict[em] = {
                            "username": u,
                            "email": em,
                            "name": row[1] or "Recruiter",
                            "title": row[2] or "",
                            "company": row[3] or "",
                            "post_text": row[4] or "",
                            "linkedin_url": row[5] or "",
                            "query": row[6] or "",
                            "status": row[7] or "pending",
                            "created_at": str(row[8]),
                            "is_gmail": row[9] if row[9] is not None else em.endswith("@gmail.com"),
                        }
        except Exception as e:
            logger.error(f"PostgreSQL error reading contacts for '{u}': {e}")

    # 3. MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            p_col = db[EMAILS_COLLECTION]
            for doc in p_col.find({"username": u}, {"_id": 0}):
                em = (doc.get("email") or "").lower().strip()
                if em:
                    records_dict[em] = {**records_dict.get(em, {}), **doc}
        except Exception as e:
            logger.error(f"Error reading from MongoDB '{EMAILS_COLLECTION}' for '{u}': {e}")

    pending_list = []
    for doc in records_dict.values():
        em = (doc.get("email") or "").lower().strip()
        is_gmail = doc.get("is_gmail") or em.endswith("@gmail.com")
        if is_gmail and doc.get("status", "pending") == "pending":
            doc_copy = dict(doc)
            doc_copy["hr_email"] = em
            doc_copy["gmail"] = em
            doc_copy["is_applied"] = False
            pending_list.append(doc_copy)

    pending_list.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return pending_list[:PENDING_LIMIT]


def get_sent_emails(username: str = "legacy") -> List[Dict[str, Any]]:
    """
    Returns all sent emails for the given user from `applied_emails`.
    Contains ONLY minimal information (username + email + sent_at).
    Capacity capped at 200.
    """
    u = sanitize_username(username)
    sent_dict: Dict[str, Dict[str, Any]] = {}

    # 1. Local storage
    for r in _load_local_json(_get_user_sent_file(u), []):
        em = (r.get("email") or "").lower().strip()
        if em:
            sent_dict[em] = {
                "username": u,
                "email": em,
                "sent_at": r.get("sent_at"),
                "is_applied": True,
                "status": "sent",
                "is_gmail": em.endswith("@gmail.com"),
            }

    # 2. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("""
                    SELECT username, email, sent_at
                    FROM applied_emails
                    WHERE username = %s
                    ORDER BY sent_at DESC
                    LIMIT %s;
                """, (u, SENT_LIMIT))
                for row in cur.fetchall():
                    em = (row[1] or "").lower().strip()
                    if em:
                        sent_dict[em] = {
                            "username": row[0] or u,
                            "email": em,
                            "sent_at": str(row[2]),
                            "is_applied": True,
                            "status": "sent",
                            "is_gmail": em.endswith("@gmail.com"),
                        }
        except Exception as e:
            logger.error(f"PostgreSQL error reading applied emails for '{u}': {e}")

    # 3. MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            s_col = db[APPLIED_COLLECTION]
            for doc in s_col.find({"username": u}, {"_id": 0}):
                em = (doc.get("email") or "").lower().strip()
                if em:
                    sent_dict[em] = {
                        "username": u,
                        "email": em,
                        "sent_at": doc.get("sent_at"),
                        "is_applied": True,
                        "status": "sent",
                        "is_gmail": em.endswith("@gmail.com"),
                    }
        except Exception as e:
            logger.error(f"Error reading from MongoDB '{APPLIED_COLLECTION}' for '{u}': {e}")

    sent_list = list(sent_dict.values())
    sent_list.sort(key=lambda x: str(x.get("sent_at") or ""), reverse=True)
    return sent_list[:SENT_LIMIT]


def get_applied_emails(username: str = "legacy") -> Set[str]:
    """Returns set of email strings that have already been sent to by this user."""
    sent = get_sent_emails(username=username)
    return {s["email"].lower().strip() for s in sent if s.get("email")}


def get_all_emails(username: str = "legacy") -> List[Dict[str, Any]]:
    """Returns all records for this user (both pending and sent)."""
    pending = get_pending_gmails(username=username)
    sent = get_sent_emails(username=username)
    return pending + sent


def get_all_contacts(username: str = "legacy") -> List[Dict[str, Any]]:
    """Compatibility helper: returns all contacts with is_applied boolean."""
    return get_all_emails(username=username)


def delete_email(email: str, username: str = "legacy") -> bool:
    """Deletes an email record from both pending and sent collections for the user."""
    clean = (email or "").lower().strip()
    if not clean:
        return False

    u = sanitize_username(username)

    # 1. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("DELETE FROM contacts WHERE username = %s AND email = %s;", (u, clean))
                cur.execute("DELETE FROM applied_emails WHERE username = %s AND email = %s;", (u, clean))
        except Exception as e:
            logger.error(f"PostgreSQL error deleting email for '{u}': {e}")

    # 2. MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            db[EMAILS_COLLECTION].delete_one({"username": u, "email": clean})
            db[APPLIED_COLLECTION].delete_one({"username": u, "email": clean})
        except Exception as e:
            logger.error(f"Error deleting email from MongoDB for '{u}': {e}")

    # 3. Local storage
    p_file = _get_user_pending_file(u)
    pending = [r for r in _load_local_json(p_file, []) if (r.get("email") or "").lower().strip() != clean]
    _save_local_json(p_file, pending)

    s_file = _get_user_sent_file(u)
    sent = [r for r in _load_local_json(s_file, []) if (r.get("email") or "").lower().strip() != clean]
    _save_local_json(s_file, sent)

    return True


def clear_pending_emails(username: str = "legacy") -> bool:
    """Clears all pending contacts for this user, strictly preserving sent history."""
    u = sanitize_username(username)

    # 1. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("DELETE FROM contacts WHERE username = %s;", (u,))
        except Exception as e:
            logger.error(f"PostgreSQL error clearing pending emails for '{u}': {e}")

    # 2. MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            db[EMAILS_COLLECTION].delete_many({"username": u})
        except Exception as e:
            logger.error(f"Error clearing pending emails from MongoDB for '{u}': {e}")

    # 3. Local storage
    _save_local_json(_get_user_pending_file(u), [])
    return True


def clear_user_data(username: str) -> bool:
    """Completely resets a user's pending, sent, and profile data."""
    u = sanitize_username(username)

    # 1. PostgreSQL
    pg = get_pg_conn()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("DELETE FROM contacts WHERE username = %s;", (u,))
                cur.execute("DELETE FROM applied_emails WHERE username = %s;", (u,))
                cur.execute("DELETE FROM user_profiles WHERE username = %s;", (u,))
        except Exception as e:
            logger.error(f"PostgreSQL error clearing user data for '{u}': {e}")

    # 2. MongoDB
    db = get_mongo_db()
    if db is not None:
        try:
            db[EMAILS_COLLECTION].delete_many({"username": u})
            db[APPLIED_COLLECTION].delete_many({"username": u})
            db["user_profiles"].delete_one({"username": u})
        except Exception as e:
            logger.error(f"Error resetting user data in MongoDB for '{u}': {e}")

    # 3. Local storage
    _save_local_json(_get_user_pending_file(u), [])
    _save_local_json(_get_user_sent_file(u), [])
    p_file = _get_user_dir(u) / "profile.json"
    if p_file.exists():
        p_file.unlink(missing_ok=True)
    return True


def get_user_stats(username: str = "legacy") -> Dict[str, Any]:
    """Returns storage counts and limits for the given user."""
    u = sanitize_username(username)
    pending = get_pending_gmails(username=u)
    sent = get_sent_emails(username=u)
    return {
        "username": u,
        "pending_count": len(pending),
        "sent_count": len(sent),
        "pending_limit": PENDING_LIMIT,
        "sent_limit": SENT_LIMIT,
        "connected": is_db_connected(),
        "db_type": get_active_db_type(),
    }
