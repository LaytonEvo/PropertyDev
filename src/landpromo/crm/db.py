"""SQLite storage for the CRM. One file, no server; back it up by copying it."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .rules import CLOSED_STAGES, SiteState, is_stalled

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    authority TEXT DEFAULT '',
    land_use TEXT NOT NULL DEFAULT 'residential',
    gross_acres REAL,
    lat REAL,
    lon REAL,
    stage TEXT NOT NULL DEFAULT 'Identified',
    rating TEXT,
    expected_profit REAL,
    nutrient_catchment TEXT DEFAULT '',
    local_plan_stage TEXT DEFAULT '',
    next_deadline TEXT,
    next_deadline_note TEXT DEFAULT '',
    legal_budget REAL DEFAULT 0,
    agreement_budget REAL DEFAULT 75000,
    kill_reason TEXT DEFAULT '',
    appraisal_md TEXT,
    appraised_at TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    stage_changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS landowners (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner_type TEXT DEFAULT 'Individual',
    address TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    lawful_basis TEXT DEFAULT 'Legitimate interests',
    privacy_notice_sent TEXT,
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_landowners (
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    landowner_id INTEGER NOT NULL REFERENCES landowners(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'Owner',
    PRIMARY KEY (site_id, landowner_id)
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
    landowner_id INTEGER REFERENCES landowners(id) ON DELETE SET NULL,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT DEFAULT '',
    by_whom TEXT DEFAULT '',
    follow_up_date TEXT,
    follow_up_done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    supplier TEXT DEFAULT '',
    description TEXT DEFAULT '',
    stage TEXT NOT NULL,
    approved_by TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_history (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    by_whom TEXT DEFAULT '',
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_activities_site ON activities(site_id);
CREATE INDEX IF NOT EXISTS idx_spend_site ON spend(site_id);
"""

SITE_FIELDS = [
    "name", "authority", "land_use", "gross_acres", "lat", "lon", "nutrient_catchment",
    "local_plan_stage", "next_deadline", "next_deadline_note", "legal_budget",
    "agreement_budget", "notes",
]
LANDOWNER_FIELDS = [
    "name", "owner_type", "address", "email", "phone", "lawful_basis",
    "privacy_notice_sent", "do_not_contact", "notes",
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class CRM:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def _exec(self, sql: str, args: tuple = ()) -> int:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    # --- sites ---------------------------------------------------------------
    def create_site(self, data: dict, by_whom: str = "") -> int:
        fields = [f for f in SITE_FIELDS if f in data]
        ts = now()
        site_id = self._exec(
            f"INSERT INTO sites ({', '.join(fields)}, created_at, stage_changed_at) "
            f"VALUES ({', '.join('?' * len(fields))}, ?, ?)",
            (*[data[f] for f in fields], ts, ts),
        )
        self._exec(
            "INSERT INTO stage_history (site_id, from_stage, to_stage, changed_at, by_whom) "
            "VALUES (?, NULL, 'Identified', ?, ?)",
            (site_id, ts, by_whom),
        )
        return site_id

    def update_site(self, site_id: int, data: dict) -> None:
        fields = [f for f in SITE_FIELDS if f in data]
        if fields:
            self._exec(
                f"UPDATE sites SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                (*[data[f] for f in fields], site_id),
            )

    def site(self, site_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM sites WHERE id = ?", (site_id,))

    def sites(self, stage: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT s.*, COALESCE(sp.total, 0) AS spent,
                   (SELECT MAX(date) FROM activities a WHERE a.site_id = s.id) AS last_activity
            FROM sites s
            LEFT JOIN (SELECT site_id, SUM(amount) AS total FROM spend GROUP BY site_id) sp
              ON sp.site_id = s.id
        """
        if stage:
            return self.q(sql + " WHERE s.stage = ? ORDER BY s.name", (stage,))
        return self.q(sql + " ORDER BY s.stage_changed_at DESC")

    def site_state(self, site_id: int) -> SiteState:
        s = self.site(site_id)
        n = self.one("SELECT COUNT(*) AS n FROM site_landowners WHERE site_id = ?", (site_id,))["n"]
        return SiteState(
            stage=s["stage"], rating=s["rating"], expected_profit=s["expected_profit"],
            legal_budget=s["legal_budget"] or 0, agreement_budget=s["agreement_budget"] or 0,
            landowner_count=n,
        )

    def change_stage(self, site_id: int, new_stage: str, by_whom: str, note: str = "",
                     kill_reason: str = "") -> None:
        old = self.site(site_id)["stage"]
        ts = now()
        self.conn.execute(
            "UPDATE sites SET stage = ?, stage_changed_at = ?, kill_reason = ? WHERE id = ?",
            (new_stage, ts, kill_reason if new_stage == "Killed" else "", site_id),
        )
        self._exec(
            "INSERT INTO stage_history (site_id, from_stage, to_stage, changed_at, by_whom, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (site_id, old, new_stage, ts, by_whom, note),
        )

    def stage_history(self, site_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM stage_history WHERE site_id = ? ORDER BY changed_at DESC, id DESC",
                      (site_id,))

    def save_appraisal(self, site_id: int, markdown: str, rating: str,
                       expected_profit: float | None, nutrient: str | None = None) -> None:
        self._exec(
            "UPDATE sites SET appraisal_md = ?, appraised_at = ?, rating = ?, expected_profit = ?, "
            "nutrient_catchment = COALESCE(?, nutrient_catchment) WHERE id = ?",
            (markdown, now(), rating, expected_profit, nutrient, site_id),
        )

    # --- landowners ----------------------------------------------------------
    def create_landowner(self, data: dict) -> int:
        fields = [f for f in LANDOWNER_FIELDS if f in data]
        return self._exec(
            f"INSERT INTO landowners ({', '.join(fields)}, created_at) "
            f"VALUES ({', '.join('?' * len(fields))}, ?)",
            (*[data[f] for f in fields], now()),
        )

    def update_landowner(self, lid: int, data: dict) -> None:
        fields = [f for f in LANDOWNER_FIELDS if f in data]
        if fields:
            self._exec(
                f"UPDATE landowners SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                (*[data[f] for f in fields], lid),
            )

    def landowner(self, lid: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM landowners WHERE id = ?", (lid,))

    def landowners(self) -> list[sqlite3.Row]:
        return self.q("""
            SELECT l.*, GROUP_CONCAT(s.name, ', ') AS site_names
            FROM landowners l
            LEFT JOIN site_landowners sl ON sl.landowner_id = l.id
            LEFT JOIN sites s ON s.id = sl.site_id
            GROUP BY l.id ORDER BY l.name
        """)

    def link(self, site_id: int, lid: int, role: str = "Owner") -> None:
        self._exec("INSERT OR REPLACE INTO site_landowners VALUES (?, ?, ?)", (site_id, lid, role))

    def unlink(self, site_id: int, lid: int) -> None:
        self._exec("DELETE FROM site_landowners WHERE site_id = ? AND landowner_id = ?", (site_id, lid))

    def site_landowners(self, site_id: int) -> list[sqlite3.Row]:
        return self.q(
            "SELECT l.*, sl.role FROM landowners l JOIN site_landowners sl ON sl.landowner_id = l.id "
            "WHERE sl.site_id = ? ORDER BY l.name", (site_id,))

    def landowner_sites(self, lid: int) -> list[sqlite3.Row]:
        return self.q(
            "SELECT s.*, sl.role FROM sites s JOIN site_landowners sl ON sl.site_id = s.id "
            "WHERE sl.landowner_id = ? ORDER BY s.name", (lid,))

    # --- activities ----------------------------------------------------------
    def add_activity(self, data: dict) -> int:
        return self._exec(
            "INSERT INTO activities (site_id, landowner_id, date, kind, summary, by_whom, "
            "follow_up_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (data.get("site_id"), data.get("landowner_id"), data["date"], data["kind"],
             data.get("summary", ""), data.get("by_whom", ""), data.get("follow_up_date"), now()),
        )

    def activities(self, site_id: int | None = None, landowner_id: int | None = None,
                   limit: int = 200) -> list[sqlite3.Row]:
        where, args = [], []
        if site_id is not None:
            where.append("a.site_id = ?"); args.append(site_id)
        if landowner_id is not None:
            where.append("a.landowner_id = ?"); args.append(landowner_id)
        sql = ("SELECT a.*, s.name AS site_name, l.name AS landowner_name FROM activities a "
               "LEFT JOIN sites s ON s.id = a.site_id LEFT JOIN landowners l ON l.id = a.landowner_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.q(sql + " ORDER BY a.date DESC, a.id DESC LIMIT ?", (*args, limit))

    def close_follow_ups_for(self, landowner_id: int) -> None:
        """A reply closes every open follow-up with that landowner."""
        self._exec("UPDATE activities SET follow_up_done = 1 WHERE landowner_id = ? "
                   "AND follow_up_date IS NOT NULL", (landowner_id,))

    def complete_follow_up(self, activity_id: int) -> None:
        self._exec("UPDATE activities SET follow_up_done = 1 WHERE id = ?", (activity_id,))

    def follow_ups_due(self, today: date | None = None) -> list[sqlite3.Row]:
        today = (today or date.today()).isoformat()
        return self.q(
            "SELECT a.*, s.name AS site_name, l.name AS landowner_name FROM activities a "
            "LEFT JOIN sites s ON s.id = a.site_id LEFT JOIN landowners l ON l.id = a.landowner_id "
            "WHERE a.follow_up_date IS NOT NULL AND a.follow_up_done = 0 AND a.follow_up_date <= ? "
            "AND (s.stage IS NULL OR s.stage NOT IN ('Sold', 'Killed')) ORDER BY a.follow_up_date",
            (today,))

    # --- spend ---------------------------------------------------------------
    def add_spend(self, site_id: int, data: dict) -> int:
        stage = self.site(site_id)["stage"]
        return self._exec(
            "INSERT INTO spend (site_id, date, amount, category, supplier, description, stage, "
            "approved_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (site_id, data["date"], data["amount"], data["category"], data.get("supplier", ""),
             data.get("description", ""), stage, data.get("approved_by", ""), now()),
        )

    def spend(self, site_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM spend WHERE site_id = ? ORDER BY date DESC, id DESC", (site_id,))

    def spent(self, site_id: int) -> float:
        return self.one("SELECT COALESCE(SUM(amount), 0) AS t FROM spend WHERE site_id = ?",
                        (site_id,))["t"]

    def total_spent(self) -> float:
        return self.one("SELECT COALESCE(SUM(amount), 0) AS t FROM spend")["t"]

    # --- dashboard -----------------------------------------------------------
    def stage_counts(self) -> dict[str, int]:
        return {r["stage"]: r["n"] for r in self.q("SELECT stage, COUNT(*) AS n FROM sites GROUP BY stage")}

    def stalled_sites(self, today: date | None = None) -> list[sqlite3.Row]:
        out = []
        for s in self.sites():
            if s["stage"] in CLOSED_STAGES:
                continue
            last = max(filter(None, [s["stage_changed_at"][:10], s["last_activity"]]))
            if is_stalled(s["stage"], date.fromisoformat(last), today):
                out.append(s)
        return out

    def upcoming_deadlines(self, days: int = 30) -> list[sqlite3.Row]:
        return self.q(
            "SELECT * FROM sites WHERE next_deadline IS NOT NULL AND next_deadline != '' "
            "AND stage NOT IN ('Sold', 'Killed') AND next_deadline <= date('now', ?) "
            "ORDER BY next_deadline", (f"+{days} days",))
