"""
Database CRUD operations using the Supabase API Client.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
import os

from supabase import create_async_client, AsyncClient
from thefuzz import fuzz

logger = logging.getLogger(__name__)

# Cache the client instance
_client: Optional[AsyncClient] = None

async def _get_client() -> AsyncClient:
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set.")
        _client = await create_async_client(url, key)
    return _client


# =====================================================================
# Users
# =====================================================================

async def register_user(
    db_path: str, # unused, kept for API compatibility
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    is_admin: bool = False,
) -> None:
    """Insert a new user or update username / first_name if they already exist."""
    db = await _get_client()
    data = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "is_admin": 1 if is_admin else 0
    }
    # upsert handles ON CONFLICT DO UPDATE automatically in Supabase if PK matches
    await db.table("users").upsert(data).execute()


async def get_user(db_path: str, user_id: int) -> Optional[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("users").select("*").eq("user_id", user_id).execute()
    return resp.data[0] if resp.data else None


async def set_admin(db_path: str, user_id: int, is_admin: bool = True) -> None:
    db = await _get_client()
    await db.table("users").update({"is_admin": 1 if is_admin else 0}).eq("user_id", user_id).execute()


async def get_all_users(db_path: str) -> list[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("users").select("*").order("joined_at", desc=True).execute()
    return resp.data


async def get_admin_ids(db_path: str) -> list[int]:
    db = await _get_client()
    resp = await db.table("users").select("user_id").eq("is_admin", 1).execute()
    return [row["user_id"] for row in resp.data]


# =====================================================================
# Titles
# =====================================================================

async def add_title(
    db_path: str,
    name: str,
    slug: str,
    language: str,
    category: str = "Newspaper",
    source: Optional[str] = None,
) -> int:
    db = await _get_client()
    data = {
        "name": name,
        "slug": slug,
        "language": language,
        "category": category,
        "source": source
    }
    # Supabase upsert: if slug exists, we want to fail or update? Original SQL did not have ON CONFLICT, so it fails.
    resp = await db.table("titles").insert(data).execute()
    return resp.data[0]["id"]


async def get_all_titles(db_path: str, active_only: bool = True) -> list[dict[str, Any]]:
    db = await _get_client()
    query = db.table("titles").select("*")
    if active_only:
        query = query.eq("is_active", 1)
    resp = await query.order("name").execute()
    return resp.data


async def get_title_by_slug(db_path: str, slug: str) -> Optional[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("titles").select("*").eq("slug", slug).execute()
    return resp.data[0] if resp.data else None


async def search_titles(db_path: str, query: str) -> list[dict[str, Any]]:
    """Fuzzy search across active titles."""
    titles = await get_all_titles(db_path, active_only=True)
    if not query:
        return titles
    
    query = query.lower()
    results = []
    for t in titles:
        score = fuzz.partial_ratio(query, t["name"].lower())
        if score > 60:
            results.append((score, t))
            
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results]


# =====================================================================
# Editions
# =====================================================================

async def add_edition(
    db_path: str,
    title_id: int,
    edition_date: date,
    download_url: Optional[str] = None,
    status: str = "pending"
) -> int:
    db = await _get_client()
    data = {
        "title_id": title_id,
        "date": edition_date.isoformat(),
        "download_url": download_url,
        "status": status
    }
    # upsert matches the UNIQUE(title_id, date) IF we configure Supabase upsert to use that constraint.
    # By default, upsert in PostgREST uses the primary key. To upsert on a unique constraint, we must pass on_conflict.
    resp = await db.table("editions").upsert(data, on_conflict="title_id,date").execute()
    return resp.data[0]["id"]


async def get_edition(db_path: str, title_id: int, edition_date: date) -> Optional[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("editions").select("*").eq("title_id", title_id).eq("date", edition_date.isoformat()).execute()
    return resp.data[0] if resp.data else None


async def update_edition_status(
    db_path: str,
    edition_id: int,
    status: str,
    file_id: Optional[str] = None,
    message_id: Optional[int] = None
) -> None:
    db = await _get_client()
    data = {"status": status}
    if file_id is not None:
        data["file_id"] = file_id
    if message_id is not None:
        data["message_id"] = message_id
        
    await db.table("editions").update(data).eq("id", edition_id).execute()


async def get_recent_editions(db_path: str, title_id: int, limit: int = 30) -> list[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("editions").select("*").eq("title_id", title_id).eq("status", "delivered").order("date", desc=True).limit(limit).execute()
    return resp.data


# =====================================================================
# Subscriptions
# =====================================================================

async def subscribe(db_path: str, user_id: int, title_id: int) -> bool:
    """Return True if inserted, False if already subscribed."""
    db = await _get_client()
    try:
        await db.table("subscriptions").insert({"user_id": user_id, "title_id": title_id}).execute()
        return True
    except Exception:
        # Conflict usually raises an exception in PostgREST
        return False


async def unsubscribe(db_path: str, user_id: int, title_id: int) -> bool:
    db = await _get_client()
    resp = await db.table("subscriptions").delete().eq("user_id", user_id).eq("title_id", title_id).execute()
    return len(resp.data) > 0


async def get_user_subscriptions(db_path: str, user_id: int) -> list[dict[str, Any]]:
    """Returns joined data: subscriptions + titles."""
    db = await _get_client()
    resp = await db.table("subscriptions").select("*, titles(*)").eq("user_id", user_id).execute()
    # Flatten the result to match the expected format
    subs = []
    for row in resp.data:
        title = row.get("titles", {})
        subs.append({
            "id": row["title_id"],
            "title_id": row["title_id"],
            "name": title.get("name"),
            "slug": title.get("slug"),
            "language": title.get("language"),
            "category": title.get("category")
        })
    # Sort by name
    subs.sort(key=lambda x: x["name"])
    return subs


async def get_subscribers_for_title(db_path: str, title_id: int) -> list[int]:
    db = await _get_client()
    resp = await db.table("subscriptions").select("user_id").eq("title_id", title_id).execute()
    return [row["user_id"] for row in resp.data]




# =====================================================================
# Delivery Log
# =====================================================================

async def log_delivery(db_path: str, user_id: int, edition_id: int, status: str = "success") -> None:
    db = await _get_client()
    await db.table("delivery_log").insert({
        "user_id": user_id,
        "edition_id": edition_id,
        "status": status
    }).execute()


async def has_been_delivered(db_path: str, user_id: int, edition_id: int) -> bool:
    db = await _get_client()
    resp = await db.table("delivery_log").select("id").eq("user_id", user_id).eq("edition_id", edition_id).eq("status", "success").execute()
    return len(resp.data) > 0


# =====================================================================
# Scraper Status
# =====================================================================

async def get_scrape_status(db_path: str, title_id: int, scrape_date: date) -> Optional[dict[str, Any]]:
    db = await _get_client()
    resp = await db.table("daily_scrape_status").select("*").eq("title_id", title_id).eq("date", scrape_date.isoformat()).execute()
    return resp.data[0] if resp.data else None


async def upsert_scrape_status(
    db_path: str,
    title_id: int,
    scrape_date: date,
    status: str,
    increment_attempts: bool = False
) -> None:
    db = await _get_client()
    existing = await get_scrape_status(db_path, title_id, scrape_date)
    
    if existing:
        attempts = existing.get("attempts", 0)
        if increment_attempts:
            attempts += 1
    else:
        attempts = 1 if increment_attempts else 0

    data = {
        "title_id": title_id,
        "date": scrape_date.isoformat(),
        "status": status,
        "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
    }
    await db.table("daily_scrape_status").upsert(
        data, on_conflict="title_id,date"
    ).execute()


async def get_pending_scrapes(db_path: str, scrape_date: date, max_attempts: int = 3) -> list[dict[str, Any]]:
    db = await _get_client()
    
    # 1. Find all title IDs that have at least one active subscriber
    subs_resp = await db.table("subscriptions").select("title_id").execute()
    subscribed_title_ids = {row["title_id"] for row in subs_resp.data}
    
    # 2. Get active titles and filter them by the subscribed IDs
    titles_resp = await db.table("titles").select("*").eq("is_active", 1).execute()
    active_titles = [t for t in titles_resp.data if t["id"] in subscribed_title_ids]
    
    status_resp = await db.table("daily_scrape_status").select("*").eq("date", scrape_date.isoformat()).execute()
    status_map = {row["title_id"]: row for row in status_resp.data}
    
    pending = []
    for t in active_titles:
        tid = t["id"]
        st = status_map.get(tid)
        if not st:
            pending.append(t)
        elif st["status"] in ("pending", "failed") and st["attempts"] < max_attempts:
            pending.append(t)
            
    return pending

async def get_available_dates(db_path: str, title_id: int) -> list[str]:
    db = await _get_client()
    resp = await db.table('editions').select('date').eq('title_id', title_id).not_.is_('file_id', 'null').order('date', desc=True).execute()
    return [row['date'] for row in resp.data]

async def get_titles_with_editions(db_path: str) -> list[int]:
    db = await _get_client()
    resp = await db.table('editions').select('title_id').not_.is_('file_id', 'null').execute()
    return list(set(row['title_id'] for row in resp.data))



async def sync_titles_from_config(db_path: str, titles: list) -> None:
    db = await _get_client()
    config_slugs = []
    for t in titles:
        slug = getattr(t, 'slug', '')
        config_slugs.append(slug)
        data = {
            'name': getattr(t, 'name', ''),
            'slug': slug,
            'language': getattr(t, 'language', 'English'),
            'category': getattr(t, 'category', 'Newspaper'),
            'source': getattr(t, 'scrape_website', 'careerswave_in'),
            'is_active': 1
        }
        await db.table('titles').upsert(data, on_conflict='slug').execute()

    # Deactivate titles not in config (only newspapers, since magazines are dynamic)
    db_titles = await db.table('titles').select('id, slug, category, is_active').execute()
    for row in db_titles.data:
        if row.get('category') == 'Newspaper' and row['slug'] not in config_slugs and row['is_active'] == 1:
            await db.table('titles').update({'is_active': 0}).eq('id', row['id']).execute()




async def get_failed_scrapes(db_path: str, scrape_date: date) -> list[str]:
    db = await _get_client()
    resp = await db.table('daily_scrape_status')\
        .select('attempts, titles(name)')\
        .eq('date', scrape_date.isoformat())\
        .eq('status', 'failed')\
        .execute()
    return sorted([row['titles']['name'] for row in resp.data if row.get('titles') and row.get('attempts', 0) >= 7])



async def get_titles_by_language(db_path: str, language: str) -> list[dict]:
    db = await _get_client()
    resp = await db.table('titles').select('*').ilike('language', language).eq('is_active', 1).order('name').execute()
    return resp.data



async def is_subscribed(db_path: str, user_id: int, title_id: int) -> bool:
    db = await _get_client()
    resp = await db.table('subscriptions').select('user_id').eq('user_id', user_id).eq('title_id', title_id).execute()
    return len(resp.data) > 0






async def get_latest_edition(db_path: str, title_id: int) -> dict | None:
    db = await _get_client()
    resp = await db.table('editions').select('*').eq('title_id', title_id).not_.is_('file_id', 'null').order('date', desc=True).limit(1).execute()
    return resp.data[0] if resp.data else None

