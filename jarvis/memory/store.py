"""SQLite memory. Text files are imported once. A forgotten fact stays gone."""

import hashlib
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


STOPWORDS = {
    "a", "an", "the", "i", "me", "my", "you", "your", "is", "are", "was", "what",
    "who", "do", "does", "did", "about", "to", "of", "and", "or", "for", "that",
    "this", "it", "please", "jarvis",
}
FORGET_NOISE = {"everything", "all", "anything", "saved", "fact", "facts", "preference", "preferences"}
FORGOTTEN = "[forgotten]"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MemoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self._init()

    def close(self):
        with self._lock:
            self.db.close()

    def _init(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                provenance TEXT NOT NULL,
                confidence REAL NOT NULL,
                superseded_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY,
                task_id TEXT,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                trigger_text TEXT NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                permission TEXT NOT NULL,
                result TEXT NOT NULL,
                verified TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routines (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                argument TEXT NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                last_run TEXT,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forgotten (
                content_hash TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        self._migrate_soft_deletes()
        self.db.commit()

    def _migrate_soft_deletes(self):
        """Older versions only hid forgotten rows. Erase them, and any copy a restart re-imported."""
        rows = self.db.execute("SELECT id, content FROM memories WHERE superseded_by = -1").fetchall()
        for row in rows:
            self.db.execute(
                "DELETE FROM memories WHERE lower(content) = lower(?) AND provenance LIKE 'imported%' AND superseded_by IS NULL",
                (row["content"],),
            )
            self._erase(row["id"], row["content"])

    def _meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def _set_meta(self, key, value):
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def add_memory(self, content, category, provenance, kind="fact", confidence=1.0):
        content = " ".join(content.split())
        now = utc_now()
        with self._lock:
            existing = self.db.execute(
                "SELECT id FROM memories WHERE lower(content) = lower(?) AND superseded_by IS NULL",
                (content,),
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            cursor = self.db.execute(
                """
                INSERT INTO memories(category, kind, content, provenance, confidence, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (category, kind, content, provenance, confidence, now, now),
            )
            self.db.execute("DELETE FROM forgotten WHERE content_hash = ?", (_content_hash(content),))
            self.db.commit()
            return int(cursor.lastrowid), True

    def search(self, query, limit=5, category=None):
        tokens = [word for word in re_words(query) if word not in STOPWORDS]
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM memories WHERE superseded_by IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        scored = []
        for row in rows:
            if category and row["category"] != category:
                continue
            haystack = row["content"].lower()
            if not tokens:
                score = 0.1
            else:
                score = sum(1 for token in tokens if token in haystack) / len(tokens)
            if score <= 0:
                continue
            scored.append((score, row["updated_at"], dict(row)))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def list_memories(self, category=None, limit=20):
        sql = "SELECT * FROM memories WHERE superseded_by IS NULL"
        values = []
        if category:
            sql += " AND category = ?"
            values.append(category)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        with self._lock:
            rows = self.db.execute(sql, values).fetchall()
        return [dict(row) for row in rows]

    def forget_candidates(self, query):
        """Facts a forget request could mean. Whole words only. A vague query matches nothing."""
        tokens = [_stem(word) for word in re_words(query) if word not in STOPWORDS and word not in FORGET_NOISE]
        if not tokens:
            return []
        with self._lock:
            rows = self.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL").fetchall()
        strong = []
        weak = []
        for row in rows:
            words = {_stem(word) for word in re_words(row["content"])}
            if all(token in words for token in tokens):
                strong.append(dict(row))
            elif any(token in words for token in tokens):
                weak.append(dict(row))
        return strong or weak

    def forget_ids(self, ids, legacy_files=()):
        """Erase these facts everywhere Jarvis keeps them. Existing backups are not touched."""
        forgotten = []
        with self._lock:
            for memory_id in ids:
                row = self.db.execute("SELECT id, content FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if row is None:
                    continue
                self._erase(row["id"], row["content"])
                forgotten.append(row["content"])
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for path in legacy_files:
            for candidate in (Path(path), Path(path).with_name(Path(path).name + ".bak")):
                _remove_lines(candidate, forgotten)
        return len(forgotten)

    def _erase(self, memory_id, content):
        self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.db.execute(
            "INSERT OR IGNORE INTO forgotten(content_hash, created_at) VALUES(?, ?)",
            (_content_hash(content), utc_now()),
        )
        core = " ".join(content.split()).strip(" .").lower()
        if not core:
            return
        triggers = self.db.execute(
            "SELECT DISTINCT trigger_text FROM audit WHERE tool = 'memory.remember' AND instr(lower(arguments), ?) > 0",
            (core,),
        ).fetchall()
        for text in [core] + [row["trigger_text"].lower() for row in triggers if row["trigger_text"].strip()]:
            self.db.execute(
                "DELETE FROM turns WHERE instr(lower(user_text), ?) > 0 OR instr(lower(assistant_text), ?) > 0",
                (text, text),
            )
            self.db.execute(
                "UPDATE tasks SET objective = ?, detail = '' WHERE instr(lower(objective), ?) > 0 OR instr(lower(detail), ?) > 0",
                (FORGOTTEN, text, text),
            )
            self.db.execute(
                "UPDATE audit SET trigger_text = ?, arguments = '{}' WHERE instr(lower(trigger_text), ?) > 0 OR instr(lower(arguments), ?) > 0",
                (FORGOTTEN, text, text),
            )

    def import_legacy(self, user_file, existence_file):
        """Copy original text files aside once, then import lines that are not stored yet."""
        imported = 0
        for path, provenance, category in (
            (Path(user_file), "imported_user_txt", "semantic"),
            (Path(existence_file), "imported_existence", "episodic"),
        ):
            if not path.exists():
                continue
            backup = path.with_name(path.name + ".bak")
            if not backup.exists():
                shutil.copy2(path, backup)
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                fact = line.strip()
                if not fact or self._is_forgotten(fact):
                    continue
                _, created = self.add_memory(fact, category, provenance, confidence=1.0)
                imported += int(created)
        return imported

    def _is_forgotten(self, content):
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM forgotten WHERE content_hash = ?",
                (_content_hash(content),),
            ).fetchone()
        return row is not None

    def append_legacy_user_line(self, user_file, fact):
        path = Path(user_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if fact.lower() in existing.lower():
            return
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(fact + "\n")

    def add_turn(self, user_text, assistant_text, task_id=""):
        with self._lock:
            self.db.execute(
                "INSERT INTO turns(task_id, user_text, assistant_text, created_at) VALUES(?, ?, ?, ?)",
                (task_id, user_text, assistant_text, utc_now()),
            )
            self.db.commit()

    def create_task(self, objective):
        task_id = uuid.uuid4().hex[:12]
        now = utc_now()
        with self._lock:
            self.db.execute(
                "INSERT INTO tasks(id, objective, status, detail, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
                (task_id, objective, "CREATED", "", now, now),
            )
            self.db.commit()
        return task_id

    def update_task(self, task_id, status, detail=""):
        with self._lock:
            self.db.execute(
                "UPDATE tasks SET status = ?, detail = ?, updated_at = ? WHERE id = ?",
                (status, detail, utc_now(), task_id),
            )
            self.db.commit()

    def unfinished_tasks(self):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE status IN ('CREATED', 'PLANNING', 'RUNNING', 'WAITING_FOR_TOOL', 'RETRYING', 'WAITING_FOR_USER', 'WAITING_FOR_APPROVAL')"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_audit(self, trigger_text, tool, arguments, permission, result, verified):
        with self._lock:
            self.db.execute(
                """
                INSERT INTO audit(created_at, trigger_text, tool, arguments, permission, result, verified)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (utc_now(), trigger_text[:500], tool, arguments[:2000], permission, result[:2000], verified),
            )
            self.db.commit()

    def add_reminder(self, text, due_at):
        with self._lock:
            cursor = self.db.execute(
                "INSERT INTO reminders(text, due_at, status, created_at) VALUES(?, ?, 'pending', ?)",
                (text, due_at, utc_now()),
            )
            self.db.commit()
            return int(cursor.lastrowid)

    def due_reminders(self, now_iso):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at",
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_reminder(self, reminder_id):
        with self._lock:
            self.db.execute("UPDATE reminders SET status = 'delivered' WHERE id = ?", (reminder_id,))
            self.db.commit()

    def pending_reminders(self):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_reminder(self, reminder_id):
        with self._lock:
            self.db.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
            self.db.commit()

    def add_routine(self, name, action, argument, hour, minute, now_local=None):
        """Saving the same routine twice keeps one. A time already passed today starts tomorrow."""
        now_local = now_local or datetime.now()
        hour, minute = int(hour), int(minute)
        passed = (hour, minute) <= (now_local.hour, now_local.minute)
        last_run = now_local.date().isoformat() if passed else ""
        with self._lock:
            existing = self.db.execute(
                "SELECT id FROM routines WHERE enabled = 1 AND action = ? AND argument = ? AND hour = ? AND minute = ?",
                (action, argument, hour, minute),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = self.db.execute(
                """
                INSERT INTO routines(name, action, argument, hour, minute, last_run, enabled)
                VALUES(?, ?, ?, ?, ?, ?, 1)
                """,
                (name, action, argument, hour, minute, last_run),
            )
            self.db.commit()
            return int(cursor.lastrowid)

    def active_routines(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM routines WHERE enabled = 1 ORDER BY hour, minute").fetchall()
        return [dict(row) for row in rows]

    def disable_routine(self, routine_id):
        with self._lock:
            self.db.execute("UPDATE routines SET enabled = 0 WHERE id = ?", (routine_id,))
            self.db.commit()

    def mark_routine_ran(self, routine_id, today):
        with self._lock:
            self.db.execute("UPDATE routines SET last_run = ? WHERE id = ?", (today, routine_id))
            self.db.commit()

    def backup_to(self, dest):
        """Copy through SQLite so recent changes in the WAL file are included, then check the copy."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        partial.unlink(missing_ok=True)
        copy = sqlite3.connect(partial)
        try:
            with self._lock:
                self.db.commit()
                self.db.backup(copy)
            status = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
        if status != "ok":
            partial.unlink(missing_ok=True)
            raise sqlite3.DatabaseError(f"Backup failed its integrity check: {status}")
        os.replace(partial, dest)
        return dest


def re_words(text):
    import re

    return re.findall(r"[a-z0-9']+", text.lower())


def _stem(word):
    word = word.removesuffix("'s")
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _content_hash(content):
    return hashlib.sha256(" ".join(content.split()).lower().encode()).hexdigest()


def _remove_lines(path, facts):
    if not facts or not path.exists():
        return
    unwanted = {" ".join(fact.split()).lower() for fact in facts}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    kept = [line for line in lines if " ".join(line.split()).lower() not in unwanted]
    if len(kept) == len(lines):
        return
    partial = path.with_name(path.name + ".partial")
    partial.write_text("".join(kept), encoding="utf-8")
    os.replace(partial, path)
