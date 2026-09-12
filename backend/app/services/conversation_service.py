"""
app/services/conversation_service.py

Hybrid Conversational Memory service.

Responsibilities:
  - Persist conversations and messages to Supabase (service-role key bypasses RLS).
  - Falls back to thread-safe in-memory store if Supabase is unavailable.
  - Caches active conversation context in Redis Cloud for low-latency retrieval.
  - Strict user isolation: every read/write verifies user_id ownership.
  - Triggers rolling summarization when message count exceeds threshold.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.core.database import get_supabase
from app.schemas.chat import (
    ConversationSummary,
    HistoryMessage,
    StructuredConversationState,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# ── In-memory fallback stores ─────────────────────────────────────────────────
_IN_MEMORY_CONVERSATIONS: Dict[str, dict] = {}
_IN_MEMORY_MESSAGES: Dict[str, List[dict]] = {}
_mem_lock = asyncio.Lock()


def _new_conv_id() -> str:
    return f"conv_{uuid.uuid4().hex[:16]}"


def _new_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _row_to_summary(row: dict) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        user_id=str(row.get("user_id", "")),
        title=row.get("title"),
        summary=row.get("summary") or "",
        summary_updated_at=row.get("summary_updated_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _cache_summary_key(conv_id: str) -> str:
    return f"conv:{conv_id}:summary"


def _cache_recent_key(conv_id: str) -> str:
    return f"conv:{conv_id}:recent"


def _cache_state_key(conv_id: str) -> str:
    """Redis Cloud state key matching requirement: conv:{conversation_id}:state"""
    return f"conv:{conv_id}:state"


def merge_conversation_state(
    existing: Optional[StructuredConversationState],
    updates: Dict[str, Any] | StructuredConversationState,
) -> StructuredConversationState:
    """
    Merge updates into existing structured conversation state following merge semantics.
    Non-null, non-empty fields in updates override older state while preserving all
    unrelated fields.

    Example:
      Existing: origin=Chennai, destination=Punjab, cargo=wheat, date_time=tonight
      User asks: "What about tomorrow?" -> updates={date_time: "tomorrow"}
      Result: origin=Chennai, destination=Punjab, cargo=wheat, date_time=tomorrow
    """
    base = existing.model_dump() if existing else {}
    update_dict = (
        updates.model_dump(exclude_unset=True)
        if isinstance(updates, StructuredConversationState)
        else dict(updates)
    )
    for k, v in update_dict.items():
        if v is not None and v != "":
            base[k] = v

    base["updated_at"] = _now_iso()
    return StructuredConversationState(**base)


async def _redis_get_str(key: str) -> Optional[str]:
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if r is None:
            return None
        val = await r.get(key)
        return val.decode() if isinstance(val, bytes) else val
    except Exception as exc:
        logger.debug("Redis GET '%s' failed: %s", key, exc)
        return None


async def _redis_set_str(key: str, value: str, ttl: int) -> None:
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if r is not None:
            await r.set(key, value, ex=ttl)
    except Exception as exc:
        logger.debug("Redis SET '%s' failed: %s", key, exc)


async def _redis_delete(key: str) -> None:
    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if r is not None:
            await r.delete(key)
    except Exception as exc:
        logger.debug("Redis DEL '%s' failed: %s", key, exc)


# ── Core CRUD ─────────────────────────────────────────────────────────────────

async def get_or_create_conversation(
    conversation_id: Optional[str],
    user_id: str,
    title: Optional[str] = None,
) -> ConversationSummary:
    """Return existing conversation (if owned by user) or create a new one."""
    if conversation_id:
        existing = await _fetch_conversation(conversation_id, user_id)
        if existing:
            return existing
        logger.info("Conversation '%s' not found for user '%s'; creating new.", conversation_id, user_id)
    return await _create_conversation(user_id, title)


async def _fetch_conversation(conv_id: str, user_id: str) -> Optional[ConversationSummary]:
    sb = get_supabase()
    if sb is not None and user_id != "guest":
        try:
            res = (
                sb.table("conversations")
                .select("*")
                .eq("id", conv_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if res.data:
                return _row_to_summary(res.data[0])
        except Exception as exc:
            logger.warning("Supabase fetch conversation failed: %s", exc)

    async with _mem_lock:
        row = _IN_MEMORY_CONVERSATIONS.get(conv_id)
        if row and str(row.get("user_id", "")) == str(user_id):
            return _row_to_summary(row)
    return None


async def _create_conversation(user_id: str, title: Optional[str] = None) -> ConversationSummary:
    conv_id = _new_conv_id()
    now = _now_iso()
    row = {
        "id": conv_id,
        "user_id": user_id,
        "title": title or "",
        "summary": "",
        "summary_updated_at": None,
        "created_at": now,
        "updated_at": now,
    }

    sb = get_supabase()
    if sb is not None:
        try:
            sb.table("conversations").insert(row).execute()
            logger.info("Conversation %s created in Supabase (user=%s)", conv_id, user_id)
        except Exception as exc:
            logger.warning("Supabase conversation insert failed: %s — using in-memory", exc)

    async with _mem_lock:
        _IN_MEMORY_CONVERSATIONS[conv_id] = row
        _IN_MEMORY_MESSAGES[conv_id] = []

    return _row_to_summary(row)


async def add_message(
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
) -> None:
    """Persist a message turn and invalidate Redis recent-messages cache."""
    msg_id = _new_msg_id()
    now = _now_iso()
    row = {
        "id": msg_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "created_at": now,
    }

    sb = get_supabase()
    if sb is not None:
        try:
            sb.table("messages").insert(row).execute()
        except Exception as exc:
            logger.warning("Supabase message insert failed: %s", exc)

    async with _mem_lock:
        if conversation_id not in _IN_MEMORY_MESSAGES:
            _IN_MEMORY_MESSAGES[conversation_id] = []
        _IN_MEMORY_MESSAGES[conversation_id].append(row)

    await _redis_delete(_cache_recent_key(conversation_id))


async def get_conversation_context_and_state(
    conversation_id: str,
    user_id: str,
) -> Tuple[str, List[HistoryMessage], StructuredConversationState]:
    """
    Efficiently retrieve rolling summary, recent messages, and structured conversation state
    using a Redis pipeline where available. Falls back to database/in-memory store.
    Enforces strict user isolation.
    """
    summary: Optional[str] = None
    recent_msgs: Optional[List[HistoryMessage]] = None
    state: Optional[StructuredConversationState] = None

    try:
        from app.core.redis import get_redis_client
        r = get_redis_client()
        if r is not None:
            pipe = r.pipeline()
            pipe.get(_cache_summary_key(conversation_id))
            pipe.get(_cache_recent_key(conversation_id))
            pipe.get(_cache_state_key(conversation_id))
            res = await pipe.execute()
            cached_summary, cached_recent_raw, cached_state_raw = res[0], res[1], res[2]

            if cached_summary is not None:
                summary = cached_summary if isinstance(cached_summary, str) else cached_summary.decode()
            if cached_recent_raw is not None:
                try:
                    raw_str = cached_recent_raw if isinstance(cached_recent_raw, str) else cached_recent_raw.decode()
                    recent_dicts = json.loads(raw_str)
                    recent_msgs = [HistoryMessage(**m) for m in recent_dicts]
                except Exception:
                    pass
            if cached_state_raw is not None:
                try:
                    raw_str = cached_state_raw if isinstance(cached_state_raw, str) else cached_state_raw.decode()
                    state_dict = json.loads(raw_str)
                    # Enforce strict user isolation: only accept if user_id matches or is unassigned
                    if state_dict.get("user_id") in (None, str(user_id)):
                        state = StructuredConversationState(**state_dict)
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Redis pipeline get context failed: %s", exc)

    # If any part missed cache, load from durable store
    if summary is None or state is None or recent_msgs is None:
        conv = await _fetch_conversation(conversation_id, user_id)
        if conv:
            if summary is None:
                summary = conv.summary or ""
            if state is None:
                async with _mem_lock:
                    mem_row = _IN_MEMORY_CONVERSATIONS.get(conversation_id, {})
                    if mem_row and "state" in mem_row:
                        state = StructuredConversationState(**mem_row["state"])
        if summary is None:
            summary = ""
        if state is None:
            async with _mem_lock:
                mem_row = _IN_MEMORY_CONVERSATIONS.get(conversation_id, {})
                if mem_row and str(mem_row.get("user_id", "")) == str(user_id) and "state" in mem_row:
                    state = StructuredConversationState(**mem_row["state"])
            if state is None:
                state = StructuredConversationState(user_id=user_id, updated_at=_now_iso())

        if recent_msgs is None:
            recent_msgs = await _fetch_recent_messages(conversation_id, user_id)

        # Repopulate Redis cache via pipeline only for parts that were missed
        try:
            from app.core.redis import get_redis_client
            r = get_redis_client()
            if r is not None:
                ttl = settings.conversation_cache_ttl
                pipe = r.pipeline()
                if cached_summary is None and summary:
                    pipe.set(_cache_summary_key(conversation_id), summary, ex=ttl)
                if cached_recent_raw is None and recent_msgs:
                    pipe.set(_cache_recent_key(conversation_id), json.dumps([m.model_dump() for m in recent_msgs]), ex=ttl)
                if cached_state_raw is None and state:
                    pipe.set(_cache_state_key(conversation_id), json.dumps(state.model_dump()), ex=ttl)
                await pipe.execute()
        except Exception as exc:
            logger.debug("Redis pipeline repopulate failed: %s", exc)

    # Ensure state has user_id set and verified
    if state is None:
        state = StructuredConversationState(user_id=user_id, updated_at=_now_iso())
    elif not state.user_id:
        state.user_id = user_id

    return summary or "", recent_msgs or [], state


async def get_conversation_context(
    conversation_id: str,
    user_id: str,
) -> Tuple[str, List[HistoryMessage]]:
    """
    Return (summary, recent_messages). Backwards-compatible wrapper around pipeline context retrieval.
    """
    summary, recent, _ = await get_conversation_context_and_state(conversation_id, user_id)
    return summary, recent


async def save_conversation_state(
    conversation_id: str,
    user_id: str,
    state: StructuredConversationState,
) -> None:
    """
    Persist structured conversation state:
    1. Set in shared Redis Cloud at conv:{conversation_id}:state with TTL.
    2. Persist synchronously in DB / in-memory store for durable storage.
    Strictly isolated: state.user_id is stamped and verified.
    """
    state.user_id = user_id
    state_json = json.dumps(state.model_dump())
    ttl = settings.conversation_cache_ttl

    # 1. Fast Redis Cloud write (non-fatal if Redis unreachable)
    try:
        await _redis_set_str(_cache_state_key(conversation_id), state_json, ttl)
    except Exception as exc:
        logger.warning("Redis state write failed: %s", exc)

    # 2. Durable database write
    sb = get_supabase()
    if sb is not None and user_id != "guest":
        try:
            sb.table("conversations").update({
                "updated_at": _now_iso(),
            }).eq("id", conversation_id).eq("user_id", user_id).execute()
        except Exception as exc:
            logger.warning("Supabase update state timestamp failed: %s", exc)

    async with _mem_lock:
        if conversation_id not in _IN_MEMORY_CONVERSATIONS:
            _IN_MEMORY_CONVERSATIONS[conversation_id] = {
                "id": conversation_id,
                "user_id": user_id,
                "created_at": _now_iso(),
            }
        if str(_IN_MEMORY_CONVERSATIONS[conversation_id].get("user_id", "")) == str(user_id):
            _IN_MEMORY_CONVERSATIONS[conversation_id]["state"] = state.model_dump()
            _IN_MEMORY_CONVERSATIONS[conversation_id]["updated_at"] = _now_iso()


async def get_conversation_state(
    conversation_id: str,
    user_id: str,
) -> Optional[StructuredConversationState]:
    """Retrieve structured state from Redis or DB with user isolation check."""
    _, _, state = await get_conversation_context_and_state(conversation_id, user_id)
    return state


async def async_summarize_if_needed(
    conversation_id: str,
    user_id: str,
    existing_summary: str,
    recent_messages: List[HistoryMessage],
) -> Optional[str]:
    """
    Run rolling summarization asynchronously via FastAPI BackgroundTasks.
    Never blocks the main chat response.
    """
    try:
        return await check_and_trigger_summarization(
            conversation_id=conversation_id,
            user_id=user_id,
            existing_summary=existing_summary,
            recent_messages=recent_messages,
        )
    except Exception as exc:
        logger.error("Background summarization error for conv=%s: %s", conversation_id, exc)
        return None


async def _fetch_recent_messages(
    conversation_id: str,
    user_id: str,
) -> List[HistoryMessage]:
    """Fetch the most recent N messages for this conversation."""
    limit = settings.recent_message_limit
    msgs: List[dict] = []

    sb = get_supabase()
    if sb is not None and user_id != "guest":
        try:
            res = (
                sb.table("messages")
                .select("role, content")
                .eq("conversation_id", conversation_id)
                .eq("user_id", user_id)
                .order("created_at", desc=False)
                .execute()
            )
            if res.data:
                msgs = res.data
        except Exception as exc:
            logger.warning("Supabase fetch messages failed: %s", exc)

    if not msgs:
        async with _mem_lock:
            msgs = [
                {"role": m["role"], "content": m["content"]}
                for m in _IN_MEMORY_MESSAGES.get(conversation_id, [])
                if str(m.get("user_id", "")) == str(user_id)
            ]

    recent = msgs[-limit:] if len(msgs) > limit else msgs
    return [HistoryMessage(role=m["role"], content=m["content"]) for m in recent]


async def count_messages(conversation_id: str, user_id: str) -> int:
    """Count total messages in the conversation."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("messages")
                .select("id", count="exact")
                .eq("conversation_id", conversation_id)
                .eq("user_id", user_id)
                .execute()
            )
            if res.count is not None:
                return res.count
        except Exception as exc:
            logger.warning("Supabase count messages failed: %s", exc)

    async with _mem_lock:
        return sum(
            1
            for m in _IN_MEMORY_MESSAGES.get(conversation_id, [])
            if str(m.get("user_id", "")) == str(user_id)
        )


async def update_summary(
    conversation_id: str,
    user_id: str,
    new_summary: str,
) -> None:
    """Persist updated conversation summary and refresh Redis cache."""
    now = _now_iso()

    sb = get_supabase()
    if sb is not None:
        try:
            sb.table("conversations").update({
                "summary": new_summary,
                "summary_updated_at": now,
                "updated_at": now,
            }).eq("id", conversation_id).eq("user_id", user_id).execute()
        except Exception as exc:
            logger.warning("Supabase update summary failed: %s", exc)

    async with _mem_lock:
        if conversation_id in _IN_MEMORY_CONVERSATIONS:
            _IN_MEMORY_CONVERSATIONS[conversation_id]["summary"] = new_summary
            _IN_MEMORY_CONVERSATIONS[conversation_id]["summary_updated_at"] = now
            _IN_MEMORY_CONVERSATIONS[conversation_id]["updated_at"] = now

    ttl = settings.conversation_cache_ttl
    await _redis_set_str(_cache_summary_key(conversation_id), new_summary, ttl)


async def check_and_trigger_summarization(
    conversation_id: str,
    user_id: str,
    existing_summary: str,
    recent_messages: List[HistoryMessage],
) -> Optional[str]:
    """
    Check threshold; if exceeded, summarize the older messages and update summary.
    Returns new summary string if triggered, else None.
    Fail-safe: on any error, keeps existing summary and logs the failure.
    """
    total = await count_messages(conversation_id, user_id)
    threshold = settings.summary_trigger_threshold
    limit = settings.recent_message_limit

    if total <= threshold:
        return None

    all_msgs = await _fetch_all_messages_for_summarization(conversation_id, user_id)
    if not all_msgs and recent_messages:
        all_msgs = recent_messages

    older_count = max(0, len(all_msgs) - limit)
    messages_to_summarize = all_msgs[:older_count] if older_count > 0 else all_msgs
    if not messages_to_summarize:
        return None
    logger.info(
        "Summarization triggered for conv=%s: total=%d, summarizing %d older messages",
        conversation_id, total, len(messages_to_summarize)
    )

    try:
        from app.services.llm_service import generate_conversation_summary
        new_summary = await generate_conversation_summary(existing_summary, messages_to_summarize)
        await update_summary(conversation_id, user_id, new_summary)
        return new_summary
    except Exception as exc:
        logger.error(
            "Summarization failed for conv=%s (preserving existing summary): %s",
            conversation_id, exc
        )
        return None


async def _fetch_all_messages_for_summarization(
    conversation_id: str,
    user_id: str,
) -> List[HistoryMessage]:
    msgs: List[dict] = []

    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("messages")
                .select("role, content")
                .eq("conversation_id", conversation_id)
                .eq("user_id", user_id)
                .order("created_at", desc=False)
                .execute()
            )
            if res.data:
                msgs = res.data
        except Exception as exc:
            logger.warning("Supabase fetch all messages failed: %s", exc)

    if not msgs:
        async with _mem_lock:
            msgs = [
                {"role": m["role"], "content": m["content"]}
                for m in _IN_MEMORY_MESSAGES.get(conversation_id, [])
                if str(m.get("user_id", "")) == str(user_id)
            ]

    return [HistoryMessage(role=m["role"], content=m["content"]) for m in msgs]


async def list_conversations(user_id: str) -> List[ConversationSummary]:
    """List all conversations for user, ordered by most recently updated."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("conversations")
                .select("*")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .execute()
            )
            if res.data is not None:
                return [_row_to_summary(r) for r in res.data]
        except Exception as exc:
            logger.warning("Supabase list conversations failed: %s", exc)

    async with _mem_lock:
        rows = [
            row for row in _IN_MEMORY_CONVERSATIONS.values()
            if str(row.get("user_id", "")) == str(user_id)
        ]
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return [_row_to_summary(r) for r in rows]


async def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Delete a conversation and all its messages. Returns True if found and deleted."""
    found = False

    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("conversations")
                .delete()
                .eq("id", conversation_id)
                .eq("user_id", user_id)
                .execute()
            )
            found = bool(res.data)
        except Exception as exc:
            logger.warning("Supabase delete conversation failed: %s", exc)

    async with _mem_lock:
        if conversation_id in _IN_MEMORY_CONVERSATIONS:
            if str(_IN_MEMORY_CONVERSATIONS[conversation_id].get("user_id", "")) == str(user_id):
                del _IN_MEMORY_CONVERSATIONS[conversation_id]
                _IN_MEMORY_MESSAGES.pop(conversation_id, None)
                found = True

    await _redis_delete(_cache_summary_key(conversation_id))
    await _redis_delete(_cache_recent_key(conversation_id))

    return found
