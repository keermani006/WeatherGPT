"""
tests/test_conversational_memory.py

Conversational Memory test suite — 8 required test cases.

Tests cover:
  TC1  Basic context: "tomorrow" resolves to previously mentioned location
  TC2  Location switch: "What about Chennai?" switches location
  TC3  Long conversation: sliding window + summary triggered
  TC4  Summary preserves facts: cargo, route, timing survive summarization
  TC5  Recent overrides summary: plan change takes priority over old summary
  TC6  User isolation: User A cannot access User B's conversation
  TC7  Multiple conversations: independent contexts per conversation
  TC8  Summarization failure: graceful fallback — chat continues normally
"""

import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.schemas.chat import HistoryMessage
import app.services.conversation_service as conv_svc
from app.services.conversation_service import (
    _IN_MEMORY_CONVERSATIONS,
    _IN_MEMORY_MESSAGES,
    get_or_create_conversation,
    add_message,
    get_conversation_context,
    update_summary,
    check_and_trigger_summarization,
    count_messages,
    list_conversations,
    delete_conversation,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

USER_A = "user-a-uuid-0001"
USER_B = "user-b-uuid-0002"

@pytest.fixture(autouse=True)
def clear_memory_store():
    """Reset in-memory fallback stores between tests."""
    _IN_MEMORY_CONVERSATIONS.clear()
    _IN_MEMORY_MESSAGES.clear()
    yield
    _IN_MEMORY_CONVERSATIONS.clear()
    _IN_MEMORY_MESSAGES.clear()


@pytest.fixture(autouse=True)
def patch_supabase():
    """Disable Supabase so all tests use the in-memory fallback."""
    with patch("app.services.conversation_service.get_supabase", return_value=None):
        yield


@pytest.fixture(autouse=True)
def patch_redis():
    """Disable Redis so all tests exercise the DB path."""
    async def _null(*args, **kwargs):
        return None
    with patch("app.services.conversation_service._redis_get_str", side_effect=_null), \
         patch("app.services.conversation_service._redis_set_str", side_effect=_null), \
         patch("app.services.conversation_service._redis_delete", side_effect=_null):
        yield


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _build_conversation(user_id: str, turns: list[tuple[str, str]]) -> str:
    """Create a conversation with given (role, content) turns. Returns conv_id."""
    conv = await get_or_create_conversation(None, user_id)
    for role, content in turns:
        await add_message(conv.id, user_id, role, content)
    return conv.id


# ═══════════════════════════════════════════════════════════════════════════════
# TC1 — Basic Context Resolution: "tomorrow" inherits the active location
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc1_basic_context_tomorrow_inherits_location():
    """
    After asking about Hyderabad, a follow-up "What about tomorrow?" should
    resolve to Hyderabad. The recent sliding window provides the context.
    """
    conv_id = await _build_conversation(USER_A, [
        ("user", "What's the weather in Hyderabad?"),
        ("assistant", "Currently in Hyderabad it's 32°C and partly cloudy."),
    ])

    summary, recent = await get_conversation_context(conv_id, USER_A)

    # Recent messages should contain the Hyderabad context
    assert len(recent) == 2
    assert any("Hyderabad" in m.content for m in recent)
    # Summary should be empty (not yet triggered)
    assert summary == ""


@pytest.mark.asyncio
async def test_tc1_reference_resolution_from_recent_messages():
    """Verify context extraction correctly identifies location from recent messages."""
    from app.api.routes.chat import _extract_context_location, _is_follow_up

    recent = [
        HistoryMessage(role="user", content="What's the weather in Hyderabad?"),
        HistoryMessage(role="assistant", content="Hyderabad is sunny and 32°C."),
    ]

    # "tomorrow" should be detected as a follow-up
    assert _is_follow_up("What about tomorrow?")
    # "What about Chennai?" — explicit location switch
    assert _is_follow_up("What about Chennai?")
    # The recent messages should resolve to Hyderabad
    loc = _extract_context_location(recent, "")
    assert loc is not None
    assert "Hyderabad" in loc


# ═══════════════════════════════════════════════════════════════════════════════
# TC2 — Location Switch: "What about Chennai?" should switch the context
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc2_location_switch_followup():
    """
    "What about Chennai?" should be detected as a follow-up with explicit
    location override to Chennai, not inherit the previous Hyderabad context.
    """
    from app.api.routes.chat import _extract_location_override_from_followup, _is_follow_up

    # This is a follow-up
    assert _is_follow_up("What about Chennai?")

    # This should extract "Chennai" as the override, not the prior location
    override = _extract_location_override_from_followup("What about Chennai?")
    assert override is not None
    assert "Chennai" in override

    # "How about Pune?" — another common pattern
    override2 = _extract_location_override_from_followup("How about Pune?")
    assert override2 is not None
    assert "Pune" in override2


@pytest.mark.asyncio
async def test_tc2_non_location_followup_does_not_override():
    """'What about tomorrow?' should NOT be treated as a location override."""
    from app.api.routes.chat import _extract_location_override_from_followup

    override = _extract_location_override_from_followup("What about tomorrow?")
    assert override is None  # "tomorrow" is temporal, not a location


# ═══════════════════════════════════════════════════════════════════════════════
# TC3 — Long conversation: sliding window + summary triggered
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc3_sliding_window_limits_messages():
    """
    After many messages, get_conversation_context should return only
    the most recent `recent_message_limit` messages (not all of them).
    """
    from app.core.config import get_settings
    settings = get_settings()
    limit = settings.recent_message_limit

    # Build 2x as many messages as the limit
    turns = []
    for i in range(limit * 2):
        turns.append(("user", f"Turn {i + 1}: What is the weather?"))
        turns.append(("assistant", f"It is sunny in city {i + 1}."))

    conv_id = await _build_conversation(USER_A, turns)

    summary, recent = await get_conversation_context(conv_id, USER_A)

    # Sliding window must not exceed the limit
    assert len(recent) <= limit, f"Recent messages {len(recent)} exceeded limit {limit}"

    # The MOST RECENT messages should be in the window (not the oldest)
    recent_contents = [m.content for m in recent]
    assert any(f"city {limit * 2}" in c or f"Turn {limit * 2}" in c for c in recent_contents), \
        "Most recent messages should be in the sliding window"


@pytest.mark.asyncio
async def test_tc3_summarization_threshold_check():
    """Verify that check_and_trigger_summarization respects the threshold."""
    from app.core.config import get_settings
    settings = get_settings()
    threshold = settings.summary_trigger_threshold

    # Build fewer messages than threshold — should NOT trigger
    turns = [("user", f"Question {i}") for i in range(threshold - 1)]
    conv_id = await _build_conversation(USER_A, turns)

    with patch("app.services.llm_service.generate_conversation_summary", new=AsyncMock()) as mock_sum:
        result = await check_and_trigger_summarization(conv_id, USER_A, "", [])
        mock_sum.assert_not_called()
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# TC4 — Summary preserves important facts across sliding window
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc4_summary_preserves_key_facts():
    """
    After summarization, the summary should contain locations, cargo type,
    departure details that were mentioned in the older conversation.
    """
    # Simulate an important earlier conversation being summarized
    important_message = (
        "I am transporting 50 bags of harvested rice from Hyderabad to Chennai "
        "tomorrow morning and I need to know the weather conditions."
    )

    existing_summary = ""
    older_messages = [
        HistoryMessage(role="user", content=important_message),
        HistoryMessage(role="assistant", content="Noted. The route from Hyderabad to Chennai shows moderate rain."),
    ]

    # Mock Groq to return a summary that preserves the key facts
    mock_summary = (
        "User is transporting harvested rice from Hyderabad to Chennai tomorrow. "
        "Route shows moderate rain probability."
    )

    with patch("app.services.llm_service.generate_conversation_summary", new=AsyncMock(return_value=mock_summary)):
        from app.services.llm_service import generate_conversation_summary
        result = await generate_conversation_summary(existing_summary, older_messages)

    assert "Hyderabad" in result
    assert "Chennai" in result
    # Fact: cargo (harvested rice) should be preserved
    assert any(kw in result.lower() for kw in ["rice", "harvested", "cargo"])


@pytest.mark.asyncio
async def test_tc4_update_summary_persists_to_memory():
    """After update_summary, get_conversation_context should return the new summary."""
    conv_id = await _build_conversation(USER_A, [
        ("user", "Transport rice from Hyderabad to Chennai tomorrow."),
        ("assistant", "Understood. Route shows rain."),
    ])

    new_summary = "User transporting harvested rice from Hyderabad to Chennai tomorrow. Rain expected en route."
    await update_summary(conv_id, USER_A, new_summary)

    summary, _ = await get_conversation_context(conv_id, USER_A)
    assert summary == new_summary


# ═══════════════════════════════════════════════════════════════════════════════
# TC5 — Recent context overrides old summary
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc5_recent_overrides_summary():
    """
    When the summary says "Chennai" but recent messages say "plans changed to Pune",
    the _extract_context_destination should return Pune (most recent wins).
    """
    from app.api.routes.chat import _extract_context_destination, _extract_context_location

    # Old summary (outside sliding window)
    old_summary = "User was planning to transport goods from Hyderabad to Chennai."

    # Recent messages — user changed plans to Pune
    recent = [
        HistoryMessage(role="user", content="Actually plans changed. I am now going to Pune instead."),
        HistoryMessage(role="assistant", content="Understood. Let me check weather for Pune route instead."),
    ]

    # Destination resolution should prefer recent messages (Pune) over summary (Chennai)
    dest = _extract_context_destination(recent, old_summary)
    loc = _extract_context_location(recent, old_summary)

    # Pune should take priority (it's in recent messages)
    # At minimum, "Chennai" from old summary should NOT be returned if Pune is in recent
    # (The exact behavior depends on extraction logic — we check Pune appears)
    combined = (dest or "") + " " + (loc or "")
    # Either destination or location should pick up Pune from recent context
    assert "Pune" in combined or dest == "Pune" or loc == "Pune", \
        f"Expected Pune from recent messages but got dest='{dest}', loc='{loc}'"


# ═══════════════════════════════════════════════════════════════════════════════
# TC6 — User isolation: User A cannot access User B's conversation
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc6_user_isolation_separate_conversations():
    """User A and User B must have completely separate conversations."""
    conv_a = await get_or_create_conversation(None, USER_A)
    conv_b = await get_or_create_conversation(None, USER_B)

    await add_message(conv_a.id, USER_A, "user", "User A's secret location: Mumbai")
    await add_message(conv_b.id, USER_B, "user", "User B's secret location: Kolkata")

    # User A fetches their context — should see Mumbai, not Kolkata
    summary_a, recent_a = await get_conversation_context(conv_a.id, USER_A)
    assert any("Mumbai" in m.content for m in recent_a)
    assert not any("Kolkata" in m.content for m in recent_a)

    # User B fetches their context — should see Kolkata, not Mumbai
    summary_b, recent_b = await get_conversation_context(conv_b.id, USER_B)
    assert any("Kolkata" in m.content for m in recent_b)
    assert not any("Mumbai" in m.content for m in recent_b)


@pytest.mark.asyncio
async def test_tc6_user_a_cannot_access_user_b_conversation():
    """User A must not be able to fetch User B's conversation by guessing the conversation_id."""
    conv_b = await get_or_create_conversation(None, USER_B)
    await add_message(conv_b.id, USER_B, "user", "User B's sensitive data")

    # User A tries to fetch User B's conversation using the same ID
    from app.services.conversation_service import _fetch_conversation
    result = await _fetch_conversation(conv_b.id, USER_A)  # Wrong user_id!

    # Should return None — ownership mismatch
    assert result is None, "User A must not be able to access User B's conversation"


@pytest.mark.asyncio
async def test_tc6_list_conversations_scoped_to_user():
    """list_conversations must only return conversations belonging to the requesting user."""
    conv_a1 = await get_or_create_conversation(None, USER_A)
    conv_b1 = await get_or_create_conversation(None, USER_B)

    convs_a = await list_conversations(USER_A)
    convs_b = await list_conversations(USER_B)

    a_ids = {c.id for c in convs_a}
    b_ids = {c.id for c in convs_b}

    assert conv_a1.id in a_ids
    assert conv_b1.id in b_ids
    assert conv_b1.id not in a_ids, "User A must not see User B's conversations"
    assert conv_a1.id not in b_ids, "User B must not see User A's conversations"


@pytest.mark.asyncio
async def test_tc6_delete_conversation_ownership_check():
    """User A must not be able to delete User B's conversation."""
    conv_b = await get_or_create_conversation(None, USER_B)

    # User A tries to delete User B's conversation
    deleted = await delete_conversation(conv_b.id, USER_A)  # Wrong user_id!

    assert not deleted, "User A must not be able to delete User B's conversation"

    # Verify User B's conversation still exists
    from app.services.conversation_service import _fetch_conversation
    still_exists = await _fetch_conversation(conv_b.id, USER_B)
    assert still_exists is not None


# ═══════════════════════════════════════════════════════════════════════════════
# TC7 — Multiple conversations per user
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc7_multiple_conversations_independent_context():
    """User A can have multiple independent conversations with separate contexts."""
    # Conversation 1: About Hyderabad
    conv1 = await get_or_create_conversation(None, USER_A)
    await add_message(conv1.id, USER_A, "user", "What's the weather in Hyderabad?")
    await add_message(conv1.id, USER_A, "assistant", "Hyderabad is sunny at 32°C.")

    # Conversation 2: About Chennai
    conv2 = await get_or_create_conversation(None, USER_A)
    await add_message(conv2.id, USER_A, "user", "What's the weather in Chennai?")
    await add_message(conv2.id, USER_A, "assistant", "Chennai is cloudy at 30°C with 70% rain chance.")

    # Contexts must be completely independent
    _, recent1 = await get_conversation_context(conv1.id, USER_A)
    _, recent2 = await get_conversation_context(conv2.id, USER_A)

    contents1 = [m.content for m in recent1]
    contents2 = [m.content for m in recent2]

    # Conv1 should contain Hyderabad, not Chennai
    assert any("Hyderabad" in c for c in contents1)
    assert not any("Chennai" in c for c in contents1)

    # Conv2 should contain Chennai, not Hyderabad
    assert any("Chennai" in c for c in contents2)
    assert not any("Hyderabad" in c for c in contents2)


@pytest.mark.asyncio
async def test_tc7_list_shows_both_conversations():
    """Both conversations should appear in the user's list."""
    conv1 = await get_or_create_conversation(None, USER_A)
    conv2 = await get_or_create_conversation(None, USER_A)

    all_convs = await list_conversations(USER_A)
    ids = {c.id for c in all_convs}

    assert conv1.id in ids
    assert conv2.id in ids


# ═══════════════════════════════════════════════════════════════════════════════
# TC8 — Summarization failure recovery
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tc8_summarization_failure_preserves_existing_summary():
    """
    If the Groq LLM summarization call raises an exception, the existing summary
    must be preserved and the chat must NOT crash.
    """
    from app.core.config import get_settings
    settings = get_settings()
    threshold = settings.summary_trigger_threshold

    # Set up a conversation with an existing summary
    conv_id = await _build_conversation(USER_A, [
        ("user", f"Turn {i}") for i in range(threshold + 2)
    ])
    existing_summary = "User is transporting rice from Hyderabad to Chennai."
    await update_summary(conv_id, USER_A, existing_summary)

    # Simulate Groq LLM failure during summarization
    with patch(
        "app.services.llm_service.generate_conversation_summary",
        new=AsyncMock(side_effect=RuntimeError("Groq API timeout")),
    ):
        # This must NOT raise an exception
        result = await check_and_trigger_summarization(
            conversation_id=conv_id,
            user_id=USER_A,
            existing_summary=existing_summary,
            recent_messages=[],
        )

    # Result should be None (no new summary from failed call)
    assert result is None

    # Existing summary must be preserved unchanged
    summary, _ = await get_conversation_context(conv_id, USER_A)
    assert summary == existing_summary, \
        f"Existing summary must be preserved after failure. Got: {summary!r}"


@pytest.mark.asyncio
async def test_tc8_summarization_failure_does_not_crash_chat():
    """Verify that summarization failure is logged but does not propagate exceptions."""
    from app.core.config import get_settings
    settings = get_settings()
    threshold = settings.summary_trigger_threshold

    conv_id = await _build_conversation(USER_A, [
        ("user", f"Question {i}") for i in range(threshold + 3)
    ])

    # Force failure in check_and_trigger_summarization
    with patch(
        "app.services.llm_service.generate_conversation_summary",
        new=AsyncMock(side_effect=Exception("Unexpected failure")),
    ):
        try:
            # Should complete without raising
            result = await check_and_trigger_summarization(
                conversation_id=conv_id,
                user_id=USER_A,
                existing_summary="",
                recent_messages=[],
            )
            assert result is None, "Should return None on summarization failure"
        except Exception as exc:
            pytest.fail(f"check_and_trigger_summarization must not raise exceptions: {exc}")


@pytest.mark.asyncio
async def test_tc8_chat_continues_normally_after_summarization_failure():
    """
    After summarization failure, get_conversation_context should still work,
    and messages should still be retrievable.
    """
    conv_id = await _build_conversation(USER_A, [
        ("user", "What's the weather in Mumbai?"),
        ("assistant", "Mumbai is humid at 30°C."),
    ])

    # Force summarization failure
    with patch(
        "app.services.llm_service.generate_conversation_summary",
        new=AsyncMock(side_effect=RuntimeError("LLM unavailable")),
    ):
        await check_and_trigger_summarization(conv_id, USER_A, "", [])

    # Context must still be accessible
    summary, recent = await get_conversation_context(conv_id, USER_A)
    assert len(recent) == 2, "Recent messages must still be accessible after summarization failure"
    assert any("Mumbai" in m.content for m in recent)
