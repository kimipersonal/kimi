"""Context Engine — Token counting and context window compaction.

Ensures messages fit within the model's context window by:
1. Counting tokens via tiktoken
2. Summarizing older messages when context is too large
3. Preserving system prompt + recent messages + memory context
"""

import logging
from typing import Optional

import tiktoken

from app.services import llm_router

logger = logging.getLogger(__name__)

# Reserve space for model output
OUTPUT_RESERVE_TOKENS = 4096
# Minimum recent messages to always keep (system + last N user/assistant pairs)
MIN_RECENT_MESSAGES = 6

# Use cl100k_base as a reasonable approximation for all models
_encoding: Optional[tiktoken.Encoding] = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def count_tokens(text: str) -> int:
    """Count tokens in a text string."""
    return len(_get_encoder().encode(text))


def count_message_tokens(messages: list[dict]) -> int:
    """Count total tokens across all messages (content only)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if content:
            total += count_tokens(content) + 4  # overhead per message
    return total


async def compact_messages(
    messages: list[dict],
    model: str | None = None,
    tier: str = "smart",
    max_tokens: int | None = None,
) -> list[dict]:
    """Compact messages to fit within the context window.

    Strategy:
    1. If messages fit, return as-is
    2. Keep system prompt (first message) and last MIN_RECENT_MESSAGES
    3. Summarize middle messages into a single summary message
    """
    if not messages:
        return messages

    context_window = max_tokens or llm_router.get_context_window(model, tier)
    budget = context_window - OUTPUT_RESERVE_TOKENS

    current_tokens = count_message_tokens(messages)
    if current_tokens <= budget:
        return messages

    logger.info(
        f"Context compaction needed: {current_tokens} tokens > {budget} budget"
    )

    # Split: system prompt | middle | recent
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    non_system = messages[1:] if system_msg else messages

    # Keep at least MIN_RECENT_MESSAGES from the end, but never split inside
    # a tool interaction group (assistant-with-tool_calls → tool results)
    keep_count = min(MIN_RECENT_MESSAGES, len(non_system))
    cut_idx = len(non_system) - keep_count
    # Walk the cut point backward until we reach a safe boundary
    while cut_idx > 0:
        msg = non_system[cut_idx]
        role = msg.get("role", "")
        if role == "user" or (role == "assistant" and not msg.get("tool_calls")):
            break
        cut_idx -= 1  # include orphaned tool/assistant-with-tool_calls in recent

    recent = non_system[cut_idx:] if cut_idx < len(non_system) else []
    middle = non_system[:cut_idx] if cut_idx > 0 else []

    if not middle:
        # Nothing to summarize, just truncate
        result = ([system_msg] if system_msg else []) + recent
        return result

    # Summarize the middle messages
    summary = await _summarize_messages(middle)

    result = []
    if system_msg:
        result.append(system_msg)
    result.append({"role": "user", "content": f"[Previous conversation summary: {summary}]"})
    result.extend(recent)

    new_tokens = count_message_tokens(result)
    logger.info(f"Context compacted: {current_tokens} → {new_tokens} tokens")

    # If still too large after summarization, truncate recent messages
    if new_tokens > budget and len(result) > 3:
        while new_tokens > budget and len(result) > 3:
            result.pop(2)  # Remove oldest after summary
            new_tokens = count_message_tokens(result)
        logger.warning(f"Additional truncation applied: {new_tokens} tokens")

    return result


async def _summarize_messages(messages: list[dict]) -> str:
    """Use a fast LLM to summarize a block of messages."""
    conversation_text = "\n".join(
        f"{m.get('role', 'unknown')}: {m.get('content', '')[:500]}"
        for m in messages
    )

    # Truncate if the conversation itself is very long
    if count_tokens(conversation_text) > 2000:
        enc = _get_encoder()
        tokens = enc.encode(conversation_text)[:2000]
        conversation_text = enc.decode(tokens)

    summary_messages = [
        {
            "role": "system",
            "content": "Summarize the following conversation concisely. "
                       "Capture key decisions, facts, and action items. "
                       "Be brief — max 3-4 sentences.",
        },
        {"role": "user", "content": conversation_text},
    ]

    try:
        response = await llm_router.chat(
            messages=summary_messages,
            tier="fast",
            temperature=0.3,
            max_tokens=300,
        )
        return response.get("content", "Previous conversation occurred.")
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        # Fallback: just take first and last message snippets
        first = messages[0].get("content", "")[:100] if messages else ""
        last = messages[-1].get("content", "")[:100] if messages else ""
        return f"Earlier: {first}... Recent: {last}..."
