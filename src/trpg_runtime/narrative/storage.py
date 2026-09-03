from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..storage import EventStore
from .domain import NarrativeTurnResult, StorySessionState


class StoryStore(EventStore):
    """SQLite persistence for Story Mode.

    Story tables are deliberately separate from the existing TRPG campaign
    tables. A single database file can therefore hold both modes without
    changing the established CampaignState APIs.
    """

    def _init(self) -> None:
        super()._init()
        with self.connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS story_branches(
                    session_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    parent_branch_id TEXT,
                    forked_from_event_id TEXT,
                    forked_from_turn INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, branch_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS story_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS story_snapshots(
                    session_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, branch_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS story_turn_results(
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _insert_story_event(
        conn: sqlite3.Connection,
        session_id: str,
        branch_id: str,
        turn_number: int,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> str:
        event_id = str(uuid4())
        conn.execute(
            "INSERT INTO story_events(event_id,session_id,branch_id,turn_number,event_type,payload,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                event_id,
                session_id,
                branch_id,
                turn_number,
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
                created_at,
            ),
        )
        return event_id

    def create_story_session(self, state: StorySessionState) -> None:
        now = self._now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM story_snapshots WHERE session_id=? LIMIT 1", (state.session_id,)
            ).fetchone()
            if exists:
                raise ValueError(f"story session already exists: {state.session_id}")
            conn.execute(
                "INSERT INTO story_branches(session_id,branch_id,parent_branch_id,forked_from_event_id," 
                "forked_from_turn,created_at) VALUES(?,?,?,?,?,?)",
                (state.session_id, state.branch_id, None, None, state.turn_number, now),
            )
            self._insert_story_event(
                conn,
                state.session_id,
                state.branch_id,
                state.turn_number,
                "story_session_created",
                {
                    "story_id": state.story_id,
                    "title": state.title,
                    "seed": state.seed,
                    "canon_policy": state.canon_policy.value,
                },
                now,
            )
            conn.execute(
                "INSERT INTO story_snapshots(session_id,branch_id,state_json,updated_at) VALUES(?,?,?,?)",
                (state.session_id, state.branch_id, state.model_dump_json(), now),
            )

    def has_story_session(self, session_id: str) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM story_snapshots WHERE session_id=? LIMIT 1", (session_id,)
                ).fetchone()
                is not None
            )

    def load_story_snapshot(self, session_id: str, branch_id: str = "main") -> StorySessionState:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM story_snapshots WHERE session_id=? AND branch_id=?",
                (session_id, branch_id),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown story session branch: {session_id}/{branch_id}")
        return StorySessionState.model_validate_json(row[0])

    def save_story_snapshot(self, state: StorySessionState) -> None:
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO story_snapshots(session_id,branch_id,state_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(session_id,branch_id) DO UPDATE SET state_json=excluded.state_json,"
                "updated_at=excluded.updated_at",
                (state.session_id, state.branch_id, state.model_dump_json(), now),
            )

    def append_story(
        self,
        session_id: str,
        branch_id: str,
        turn_number: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        now = self._now()
        with self.connect() as conn:
            return self._insert_story_event(
                conn, session_id, branch_id, turn_number, event_type, payload, now
            )

    def begin_story_turn(self, session_id: str, branch_id: str, turn: int) -> StoryTurnTransaction:
        return StoryTurnTransaction(self, session_id, branch_id, turn)

    def load_story_turn_result(
        self,
        request_id: str,
        session_id: str | None = None,
        branch_id: str | None = None,
    ) -> NarrativeTurnResult | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT session_id,branch_id,result_json FROM story_turn_results "
                "WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        stored_session_id, stored_branch_id, result_json = row
        if session_id is not None and stored_session_id != session_id:
            raise ValueError("request_id belongs to a different story session")
        if branch_id is not None and stored_branch_id != branch_id:
            raise ValueError("request_id belongs to a different story branch")
        return NarrativeTurnResult.model_validate_json(result_json)

    def _collect_story_events(
        self, conn: sqlite3.Connection, session_id: str, branch_id: str
    ) -> list[dict[str, Any]]:
        branch = conn.execute(
            "SELECT parent_branch_id,forked_from_turn FROM story_branches "
            "WHERE session_id=? AND branch_id=?",
            (session_id, branch_id),
        ).fetchone()
        if branch is None:
            raise KeyError(f"unknown story branch: {session_id}/{branch_id}")

        inherited: list[dict[str, Any]] = []
        parent_id, forked_turn = branch
        if parent_id:
            inherited = [
                event
                for event in self._collect_story_events(conn, session_id, parent_id)
                if event["turn"] <= forked_turn
            ]

        rows = conn.execute(
            "SELECT seq,event_id,branch_id,turn_number,event_type,payload,created_at "
            "FROM story_events WHERE session_id=? AND branch_id=? ORDER BY seq",
            (session_id, branch_id),
        ).fetchall()
        own = [
            {
                "seq": row[0],
                "event_id": row[1],
                "branch_id": row[2],
                "turn": row[3],
                "type": row[4],
                "payload": json.loads(row[5]),
                "created_at": row[6],
            }
            for row in rows
        ]
        return sorted([*inherited, *own], key=lambda event: event["seq"])

    def story_events(
        self, session_id: str, branch_id: str = "main", include_ancestors: bool = True
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if include_ancestors:
                return self._collect_story_events(conn, session_id, branch_id)
            rows = conn.execute(
                "SELECT seq,event_id,branch_id,turn_number,event_type,payload,created_at "
                "FROM story_events WHERE session_id=? AND branch_id=? ORDER BY seq",
                (session_id, branch_id),
            ).fetchall()
        return [
            {
                "seq": row[0],
                "event_id": row[1],
                "branch_id": row[2],
                "turn": row[3],
                "type": row[4],
                "payload": json.loads(row[5]),
                "created_at": row[6],
            }
            for row in rows
        ]

    def list_story_branches(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT branch_id,parent_branch_id,forked_from_event_id,forked_from_turn,created_at "
                "FROM story_branches WHERE session_id=? ORDER BY created_at,branch_id",
                (session_id,),
            ).fetchall()
        return [
            {
                "branch_id": row[0],
                "parent_branch_id": row[1],
                "forked_from_event_id": row[2],
                "forked_from_turn": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def create_story_branch(
        self, state: StorySessionState, branch_id: str
    ) -> StorySessionState:
        if not branch_id or branch_id in {state.branch_id, "main"}:
            raise ValueError(f"invalid or existing branch id: {branch_id!r}")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in branch_id):
            raise ValueError("branch_id may contain only letters, numbers, underscores, and hyphens")

        child = state.model_copy(
            update={
                "branch_id": branch_id,
                "parent_branch_id": state.branch_id,
            }
        )
        parent_events = self.story_events(state.session_id, state.branch_id)
        forked_event_id = parent_events[-1]["event_id"] if parent_events else None
        now = self._now()
        with self.connect() as conn:
            parent_exists = conn.execute(
                "SELECT 1 FROM story_branches WHERE session_id=? AND branch_id=?",
                (state.session_id, state.branch_id),
            ).fetchone()
            if parent_exists is None:
                raise KeyError(f"unknown parent story branch: {state.session_id}/{state.branch_id}")
            try:
                conn.execute(
                    "INSERT INTO story_branches(session_id,branch_id,parent_branch_id,forked_from_event_id,"
                    "forked_from_turn,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        child.session_id,
                        child.branch_id,
                        state.branch_id,
                        forked_event_id,
                        state.turn_number,
                        now,
                    ),
                )
                self._insert_story_event(
                    conn,
                    child.session_id,
                    child.branch_id,
                    child.turn_number,
                    "story_branch_created",
                    {
                        "parent_branch_id": state.branch_id,
                        "forked_from_event_id": forked_event_id,
                        "forked_from_turn": state.turn_number,
                    },
                    now,
                )
                conn.execute(
                    "INSERT INTO story_snapshots(session_id,branch_id,state_json,updated_at) VALUES(?,?,?,?)",
                    (child.session_id, child.branch_id, child.model_dump_json(), now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"story branch already exists: {child.branch_id}") from exc
        return child


class StoryTurnTransaction:
    def __init__(self, store: StoryStore, session_id: str, branch_id: str, turn: int):
        self._store = store
        self.session_id = session_id
        self.branch_id = branch_id
        self.turn = turn
        self._events: list[tuple[str, dict[str, Any]]] = []

    @property
    def event_types(self) -> list[str]:
        return [event_type for event_type, _ in self._events]

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        self._events.append((event_type, payload))

    def commit(
        self,
        state: StorySessionState,
        request_id: str | None = None,
        result: NarrativeTurnResult | None = None,
    ) -> None:
        now = self._store._now()
        with self._store.connect() as conn:
            for event_type, payload in self._events:
                self._store._insert_story_event(
                    conn,
                    self.session_id,
                    self.branch_id,
                    self.turn,
                    event_type,
                    payload,
                    now,
                )
            conn.execute(
                "INSERT INTO story_snapshots(session_id,branch_id,state_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(session_id,branch_id) DO UPDATE SET state_json=excluded.state_json,"
                "updated_at=excluded.updated_at",
                (state.session_id, state.branch_id, state.model_dump_json(), now),
            )
            if request_id is not None and result is not None:
                conn.execute(
                    "INSERT INTO story_turn_results(request_id,session_id,branch_id,turn_number,result_json,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        request_id,
                        self.session_id,
                        self.branch_id,
                        self.turn,
                        result.model_dump_json(),
                        now,
                    ),
                )

    def abort(self, error: str) -> None:
        self._store.append_story(
            self.session_id,
            self.branch_id,
            self.turn,
            "story_turn_aborted",
            {"error": error, "events": self.event_types},
        )
