"""받아 온 공고, 내가 단 표시(관심·지원·숨김), 하루 호출 수를 저장하는 작은 데이터베이스."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "jobs.db"
STATUSES = ["", "관심", "지원함", "숨김"]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            source TEXT NOT NULL,          -- 'api' 또는 'sample'
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS marks (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calls (
            day TEXT PRIMARY KEY,
            n INTEGER NOT NULL
        );
        """
    )
    return con


def upsert_jobs(con: sqlite3.Connection, jobs: list[dict], source: str) -> int:
    """공고 저장. 새로 들어온 공고 수를 돌려줌."""
    today = date.today().isoformat()
    new = 0
    for j in jobs:
        if not j.get("id"):
            continue
        row = con.execute("SELECT 1 FROM jobs WHERE id=?", (j["id"],)).fetchone()
        if row:
            con.execute("UPDATE jobs SET data=?, last_seen=? WHERE id=?",
                        (json.dumps(j, ensure_ascii=False), today, j["id"]))
        else:
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)",
                        (j["id"], json.dumps(j, ensure_ascii=False), source, today, today))
            new += 1
    con.commit()
    return new


def load_jobs(con: sqlite3.Connection, source: str) -> list[dict]:
    rows = con.execute(
        "SELECT j.data, j.first_seen, COALESCE(m.status,''), COALESCE(m.memo,'') "
        "FROM jobs j LEFT JOIN marks m ON j.id=m.id WHERE j.source=?", (source,)
    ).fetchall()
    out = []
    for data, first_seen, status, memo in rows:
        j = json.loads(data)
        j.update(first_seen=first_seen, status=status, memo=memo)
        out.append(j)
    return out


def set_mark(con: sqlite3.Connection, job_id: str, status: str, memo: str) -> None:
    con.execute(
        "INSERT INTO marks VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
        "memo=excluded.memo, updated=excluded.updated",
        (job_id, status, memo, date.today().isoformat()),
    )
    con.commit()


def add_calls(con: sqlite3.Connection, n: int) -> None:
    today = date.today().isoformat()
    con.execute("INSERT INTO calls VALUES (?,?) ON CONFLICT(day) DO UPDATE SET n=n+?", (today, n, n))
    con.commit()


def calls_today(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT n FROM calls WHERE day=?", (date.today().isoformat(),)).fetchone()
    return row[0] if row else 0
