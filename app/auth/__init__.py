"""Authentication & multi-tenancy core.

Security model:
- argon2id password hashing (memory-hard, OWASP recommended)
- Server-side sessions: signed Flask cookie carries only a session token;
  the session itself lives in the auth_sessions table (revocable)
- Login rate limiting: exponential backoff per identifier+IP
- Tenant isolation: every request resolves (user, active household);
  all collection-scoped repositories REQUIRE household_id - there is no
  code path that queries collection data without one.

The first registered user becomes platform admin and receives the legacy
household (id 1) that inherits all pre-existing data (single-user era).
"""
from __future__ import annotations

import functools
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import g, redirect, request, session, url_for

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.db.schema import connect

SESSION_COOKIE = "swu_session"
SESSION_LIFETIME_DAYS = 14
REMEMBER_LIFETIME_DAYS = 30
RESET_TOKEN_MINUTES = 30
VERIFY_TOKEN_HOURS = 48

_pwd = PasswordHasher()  # argon2id, sane defaults


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- password hashing -----------------------------------------------------------


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _pwd.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# --- user CRUD --------------------------------------------------------------------


def create_user(conn, username: str, email: str, password: str, is_platform_admin: bool = False) -> int:
    from app.db.schema import init_database  # noqa: F401 (ensured by caller)

    cur = conn.execute(
        """INSERT INTO users (username, email, password_hash, is_platform_admin, email_verified)
           VALUES (?, ?, ?, ?, ?)""",
        (username.strip(), email.strip().lower(), hash_password(password),
         1 if is_platform_admin else 0, 0),
    )
    conn.commit()
    return cur.lastrowid


def get_user_by_login(conn, login: str) -> dict[str, Any] | None:
    """Find a user by username OR email (case-insensitive)."""
    login = login.strip()
    cur = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE",
        (login, login),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_user(conn, user_id: int) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def set_user_password(conn, user_id: int, new_password: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE user_id = ?",
        (hash_password(new_password), user_id),
    )
    conn.commit()


def mark_email_verified(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET email_verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def count_users(conn) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]


# --- households ---------------------------------------------------------------------


def ensure_legacy_household(conn, owner_user_id: int) -> int:
    """Create/rebind the legacy default household that owns all
    pre-existing (household_id IS NULL) collection data.

    Called once when the first user registers; backfills every
    NULL household_id to 1 so old data belongs to that user.
    """
    cur = conn.execute("SELECT household_id FROM households WHERE household_id = 1")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO households (household_id, name, owner_user_id, invite_code) VALUES (1, ?, ?, ?)",
            ("Meine Sammlung", owner_user_id, secrets.token_hex(4)),
        )
    else:
        conn.execute("UPDATE households SET owner_user_id = ? WHERE household_id = 1", (owner_user_id,))

    # membership for the owner
    conn.execute(
        """INSERT OR IGNORE INTO household_members (household_id, user_id, role)
           VALUES (1, ?, 'owner')""",
        (owner_user_id,),
    )
    # backfill tenant columns on all legacy data
    for table in ("collection", "scans", "wishlist", "scan_undo"):
        conn.execute(f"UPDATE {table} SET household_id = 1 WHERE household_id IS NULL")
    conn.execute("UPDATE settings SET household_id = 1 WHERE household_id IS NULL")
    conn.commit()
    return 1


def create_household(conn, name: str, owner_user_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO households (name, owner_user_id, invite_code) VALUES (?, ?, ?)",
        (name.strip(), owner_user_id, secrets.token_hex(4)),
    )
    conn.execute(
        "INSERT INTO household_members (household_id, user_id, role) VALUES (?, ?, 'owner')",
        (cur.lastrowid, owner_user_id),
    )
    conn.commit()
    return cur.lastrowid


def get_households_for_user(conn, user_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT h.*, m.role FROM households h
           JOIN household_members m ON m.household_id = h.household_id
           WHERE m.user_id = ? ORDER BY h.household_id""",
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_household(conn, household_id: int) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM households WHERE household_id = ?", (household_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_membership(conn, household_id: int, user_id: int) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM household_members WHERE household_id = ? AND user_id = ?",
        (household_id, user_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_members(conn, household_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT m.*, u.username, u.display_name, u.email
           FROM household_members m JOIN users u ON u.user_id = m.user_id
           WHERE m.household_id = ? ORDER BY m.joined_at""",
        (household_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def join_household_by_code(conn, invite_code: str, user_id: int, role: str = "editor") -> dict[str, Any]:
    cur = conn.execute(
        "SELECT * FROM households WHERE invite_code = ?", (invite_code.strip(),)
    )
    household = cur.fetchone()
    if household is None:
        return {"ok": False, "error": "Einladungscode ungültig"}
    existing = get_membership(conn, household["household_id"], user_id)
    if existing:
        return {"ok": False, "error": "Du bist bereits Mitglied dieser Sammlung"}
    conn.execute(
        "INSERT INTO household_members (household_id, user_id, role) VALUES (?, ?, ?)",
        (household["household_id"], user_id, role),
    )
    conn.commit()
    return {"ok": True, "household_id": household["household_id"], "name": household["name"]}


def leave_household(conn, household_id: int, user_id: int) -> dict[str, Any]:
    household = get_household(conn, household_id)
    if household is None:
        return {"ok": False, "error": "Sammlung nicht gefunden"}
    if household["owner_user_id"] == user_id:
        return {"ok": False, "error": "Owner kann die Sammlung nicht verlassen (erst übertragen)"}
    conn.execute(
        "DELETE FROM household_members WHERE household_id = ? AND user_id = ?",
        (household_id, user_id),
    )
    conn.commit()
    return {"ok": True}


# --- sessions -------------------------------------------------------------------------


def create_session(conn, user_id: int, remember: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    days = REMEMBER_LIFETIME_DAYS if remember else SESSION_LIFETIME_DAYS
    expires = _iso(utcnow() + timedelta(days=days))
    conn.execute(
        "INSERT INTO auth_sessions (session_token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE user_id = ?", (user_id,))
    conn.commit()
    return token


def get_session_user(conn, token: str) -> dict[str, Any] | None:
    if not token:
        return None
    cur = conn.execute(
        """SELECT s.expires_at, u.* FROM auth_sessions s
           JOIN users u ON u.user_id = s.user_id
           WHERE s.session_token = ?""",
        (token,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if utcnow() > expires:
            conn.execute("DELETE FROM auth_sessions WHERE session_token = ?", (token,))
            conn.commit()
            return None
    except ValueError:
        return None
    user = dict(row)
    user.pop("expires_at", None)
    return user


def destroy_session(conn, token: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE session_token = ?", (token,))
    conn.commit()


# --- login rate limiting -----------------------------------------------------------------


def _recent_failures(conn, login: str, ip: str) -> int:
    # attempted_at uses SQLite datetime('now') -> 'YYYY-MM-DD HH:MM:SS' (UTC)
    cutoff = (utcnow() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """SELECT COUNT(*) FROM login_attempts
           WHERE username_or_email = ? AND success = 0 AND attempted_at > ?
           AND (ip IS NULL OR ip = ?)""",
        (login.strip().lower(), cutoff, ip or ""),
    )
    return cur.fetchone()[0]


def record_login_attempt(conn, login: str, ip: str, success: bool) -> None:
    conn.execute(
        "INSERT INTO login_attempts (username_or_email, ip, success) VALUES (?, ?, ?)",
        (login.strip().lower(), ip, 1 if success else 0),
    )
    conn.commit()


def login_allowed(conn, login: str, ip: str) -> tuple[bool, int]:
    """Returns (allowed, seconds_to_wait). 5 failures -> lockout."""
    failures = _recent_failures(conn, login, ip)
    if failures < 5:
        return True, 0
    wait_seconds = min(3600, 2 ** (failures - 5) * 60)
    return False, wait_seconds


# --- password reset / email verification ---------------------------------------------------


def create_password_reset(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = _iso(utcnow() + timedelta(minutes=RESET_TOKEN_MINUTES))
    conn.execute(
        "INSERT INTO password_resets (reset_token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.commit()
    return token


def consume_password_reset(conn, token: str) -> int | None:
    """Valid token -> user_id (and marks used). Invalid/expired -> None."""
    cur = conn.execute(
        "SELECT * FROM password_resets WHERE reset_token = ? AND used = 0",
        (token,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if utcnow() > expires:
            return None
    except ValueError:
        return None
    conn.execute("UPDATE password_resets SET used = 1 WHERE reset_token = ?", (token,))
    conn.commit()
    return row["user_id"]


def create_email_verification(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = _iso(utcnow() + timedelta(hours=VERIFY_TOKEN_HOURS))
    conn.execute(
        "INSERT INTO email_verifications (verify_token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.commit()
    return token


def consume_email_verification(conn, token: str) -> int | None:
    cur = conn.execute(
        "SELECT * FROM email_verifications WHERE verify_token = ? AND used = 0",
        (token,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if utcnow() > expires:
            return None
    except ValueError:
        return None
    conn.execute("UPDATE email_verifications SET used = 1 WHERE verify_token = ?", (token,))
    conn.commit()
    return row["user_id"]


# --- Flask request integration ---------------------------------------------------------------


def load_current_user(app) -> None:
    """before_request hook: resolve user + active household into g."""
    g.user = None
    g.household = None
    g.membership = None
    token = session.get(SESSION_COOKIE)
    if not token:
        return
    conn = connect()
    try:
        user = get_session_user(conn, token)
        if not user:
            session.pop(SESSION_COOKIE, None)
            return
        g.user = user
        # active household: stored selection, else first membership
        household_id = session.get("active_household_id")
        households = get_households_for_user(conn, user["user_id"])
        if not households:
            return
        if household_id is None or household_id not in {h["household_id"] for h in households}:
            household_id = households[0]["household_id"]
            session["active_household_id"] = household_id
        g.household = next(h for h in households if h["household_id"] == household_id)
        g.membership = get_membership(conn, household_id, user["user_id"])
    finally:
        conn.close()


def login_required(view):
    """Decorator: 401 for API, redirect to /login for pages."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if request.path.startswith("/api/"):
                from flask import jsonify

                return jsonify({"error": "Nicht eingeloggt"}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def household_required(view):
    """Decorator: requires login AND an active household (403 otherwise)."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            from flask import jsonify

            if request.path.startswith("/api/"):
                return jsonify({"error": "Nicht eingeloggt"}), 401
            return redirect(url_for("auth.login_page"))
        if g.get("household") is None:
            if request.path.startswith("/api/"):
                from flask import jsonify

                return jsonify({"error": "Keine Sammlung ausgewählt"}), 403
            return redirect(url_for("auth.no_household_page"))
        return view(*args, **kwargs)

    return wrapped


def role_at_least(required: str) -> bool:
    """Check current user's role in the active household. owner > editor > viewer."""
    order = {"viewer": 0, "editor": 1, "owner": 2}
    role = (g.get("membership") or {}).get("role", "viewer")
    return order.get(role, 0) >= order.get(required, 99)


def can_edit() -> bool:
    return role_at_least("editor")


def is_platform_admin() -> bool:
    user = g.get("user")
    return bool(user and user.get("is_platform_admin"))
