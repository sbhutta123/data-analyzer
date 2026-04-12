# tests/test_history_truncation.py
# Tests for conversation history sliding-window truncation.
# Related module: backend/llm.py
# PRD: #3 (Conversational Q&A — prevents token limit overflow)
#
# Confirmed behavior list (TEST-STRATEGY Steps 1–2):
#  1. History under the token limit is returned unchanged
#  2. History over the token limit drops oldest messages first
#  3. Most recent user message is always preserved
#  4. Empty history returns empty list
#
# Token estimation uses word_count * 1.3 as a heuristic.
# REVIEW: if this causes premature truncation or overflows, switch to tiktoken.

from llm import truncate_history


def test_short_history_returned_unchanged():
    # Behavior 1: history under the token limit passes through unchanged.
    # Why: premature truncation drops useful context, making follow-up questions
    #      less accurate than they should be.
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = truncate_history(history, max_tokens=10000)
    assert len(result) == 2
    assert result == history


def test_long_history_drops_oldest_first():
    # Behavior 2: when history exceeds max_tokens, oldest messages are dropped first.
    # Why: recent context is more relevant; dropping newest messages first would break
    #      the current conversation thread.
    history = [
        {"role": "user", "content": f"message {i} " * 100}
        for i in range(50)
    ]
    result = truncate_history(history, max_tokens=2000)
    assert len(result) < 50
    assert result[-1] == history[-1]


def test_most_recent_message_always_preserved():
    # Behavior 3: even if a single message exceeds max_tokens, it is kept.
    # Why: the user's question is the minimum viable input — dropping it means
    #      nothing can be answered.
    history = [{"role": "user", "content": "x " * 10000}]
    result = truncate_history(history, max_tokens=100)
    assert len(result) == 1
    assert result[0] == history[0]


def test_empty_history_returns_empty_list():
    # Behavior 4: empty history returns empty list without error.
    # Why: the first question has no history; crashing here blocks every new
    #      session's first interaction.
    result = truncate_history([], max_tokens=10000)
    assert result == []
