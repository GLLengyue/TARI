from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .domain import CampaignState


class EventStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self):
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                campaign_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
                campaign_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")

    def append(self, campaign_id: str, turn: int, event_type: str, payload: dict):
        with self.connect() as c:
            c.execute(
                "INSERT INTO events(event_id,campaign_id,turn_number,event_type,payload,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), campaign_id, turn, event_type, json.dumps(payload, default=str), datetime.now(UTC).isoformat()),
            )

    def save_snapshot(self, state: CampaignState):
        with self.connect() as c:
            c.execute(
                "INSERT INTO snapshots(campaign_id,state_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                (state.campaign_id, state.model_dump_json(), datetime.now(UTC).isoformat()),
            )

    def load_snapshot(self, campaign_id: str) -> CampaignState:
        with self.connect() as c:
            row = c.execute("SELECT state_json FROM snapshots WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return CampaignState.model_validate_json(row[0])

    def events(self, campaign_id: str) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT seq,turn_number,event_type,payload,created_at FROM events WHERE campaign_id=? ORDER BY seq",
                (campaign_id,),
            ).fetchall()
        return [
            {"seq": r[0], "turn": r[1], "type": r[2], "payload": json.loads(r[3]), "created_at": r[4]}
            for r in rows
        ]
