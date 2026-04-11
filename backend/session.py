# session.py
# In-memory session store: creates, retrieves, and deletes sessions.
# Supports: every PRD feature — sessions are the central state carrier across
#   upload (#1), Q&A (#3), cleaning (#4), ML (#5), and export (#7).
# Key deps: pandas (dataframe storage), uuid (unique session IDs),
#   sandbox_libraries.py (single source of truth for exec namespace libraries)
#
# Architecture ref: "Session Management" in planning/architecture.md
# Tests: backend/tests/test_session.py

import uuid
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from sandbox_libraries import SANDBOX_NAMESPACE_LIBRARIES


@dataclass
class Session:
    """
    All mutable state for one user session.

    dataframe_original — immutable snapshot taken at upload time; used to restore
        state if the user resets after cleaning operations.
    dataframe — the working copy; mutated by cleaning actions and exec operations.
    exec_namespace — Python namespace injected into sandboxed code execution (executor.py).
        Pre-loaded with df and standard analysis libraries so LLM-generated code can
        reference them without importing.
    conversation_history — list of {role, content} dicts sent to the LLM on each turn.
    code_history — list of {code, explanation} dicts written into the exported notebook.
    api_key — stored per-session after BYOK validation so it doesn't need re-sending.
    """

    session_id: str
    dataframe_original: pd.DataFrame
    dataframe: pd.DataFrame
    conversation_history: list = field(default_factory=list)
    code_history: list = field(default_factory=list)
    exec_namespace: dict = field(default_factory=dict)
    api_key: str = ""

    def __post_init__(self) -> None:
        # Libraries come from sandbox_libraries.py — the single source of truth shared
        # with llm.py's system prompt. Add new libraries there, not here.
        self.exec_namespace = {
            **SANDBOX_NAMESPACE_LIBRARIES,
            "df": self.dataframe,
            "print": print,
        }


class SessionStore:
    """
    Thread-unsafe in-memory store for Session objects.

    Acceptable for single-process FastAPI prototype.
    REVIEW: replace with a thread-safe store if concurrency requirements grow.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, dataframe: pd.DataFrame, api_key: str = "") -> str:
        """
        Create a new session from the uploaded dataframe and return its session_id.

        Both the working copy and the original are taken as independent copies of the
        caller's dataframe, so external mutations after this call have no effect.

        Failure modes: none — always succeeds, including for empty dataframes.
        """
        session_id = str(uuid.uuid4())
        session = Session(
            session_id=session_id,
            dataframe_original=dataframe.copy(),
            dataframe=dataframe.copy(),
            api_key=api_key,
        )
        self._sessions[session_id] = session
        return session_id

    def get(self, session_id: str) -> Optional[Session]:
        """
        Return the Session for session_id, or None if it does not exist.

        Callers must check for None and return a 404 — never let a missing session
        propagate as an AttributeError or KeyError into a route handler.
        """
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """
        Remove a session from the store.

        Returns True if the session existed and was deleted.
        Returns False if session_id was not found — no exception is raised,
        but the caller should surface this to the user so they can clarify.
        """
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        return True
