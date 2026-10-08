"""Auth blueprint: login, register, password reset, household management."""
from __future__ import annotations

import re

from flask import (
    Blueprint,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.auth import (
    SESSION_COOKIE,
    can_edit,
    consume_email_verification,
    consume_password_reset,
    count_users,
    create_email_verification,
    create_household,
    create_password_reset,
    create_session,
    create_user,
    destroy_session,
    ensure_legacy_household,
    get_household,
    get_households_for_user,
    get_members,
    get_user,
    get_user_by_login,
    household_required,
    is_platform_admin,
    join_household_by_code,
    leave_household,
    login_allowed,
    login_required,
    mark_email_verified,
    record_login_attempt,
    set_user_password,
    verify_password,
)
from app.auth import mail
from app.db.schema import connect

bp = Blueprint("auth", __name__, url_prefix="/")

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@bp.route("/login", methods=["GET"])
def login_page():
    if g.get("user"):
        return redirect(url_for("index_page"))
    return render_template("login.html")


@bp.route("/register", methods=["GET"])
def register_page():
    if g.get("user"):
        return redirect(url_for("index_page"))
    return render_template("register.html")


@bp.route("/reset", methods=["GET"])
def reset_page():
    return render_template("reset.html")


@bp.route("/no-household", methods=["GET"])
def no_household_page():
    if g.get("user") is None:
        return redirect(url_for("auth.login_page"))
    return render_template("no_household.html")


# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------


@bp.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    login = (body.get("login") or "").strip()
    password = body.get("password") or ""
    remember = bool(body.get("remember", False))
    if not login or not password:
        return jsonify({"error": "Benutzername/E-Mail und Passwort erforderlich"}), 400

    conn = connect()
    try:
        allowed, wait_s = login_allowed(conn, login, _client_ip())
        if not allowed:
            return jsonify({"error": f"Zu viele Versuche. Bitte {wait_s}s warten."}), 429
        user = get_user_by_login(conn, login)
        ok = bool(user and verify_password(user["password_hash"], password))
        record_login_attempt(conn, login, _client_ip(), ok)
        if not ok:
            return jsonify({"error": "Anmeldedaten ungültig"}), 401

        token = create_session(conn, user["user_id"], remember)
        session[SESSION_COOKIE] = token
        return jsonify({
            "ok": True,
            "user": {"username": user["username"], "display_name": user.get("display_name")},
        })
    finally:
        conn.close()


@bp.route("/api/auth/register", methods=["POST"])
def api_register():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""

    if not _USERNAME_RE.match(username):
        return jsonify({"error": "Benutzername: 3–30 Zeichen, nur Buchstaben/Zahlen/._-"}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Ungültige E-Mail-Adresse"}), 400
    if len(password) < 8:
        return jsonify({"error": "Passwort: mindestens 8 Zeichen"}), 400

    conn = connect()
    try:
        if get_user_by_login(conn, username) or get_user_by_login(conn, email):
            return jsonify({"error": "Benutzername oder E-Mail bereits vergeben"}), 409

        first = count_users(conn) == 0
        user_id = create_user(conn, username, email, password, is_platform_admin=first)

        # First user inherits all legacy data via household 1 + gets a fresh
        # household named after them for future use.
        if first:
            ensure_legacy_household(conn, user_id)
        else:
            create_household(conn, f"{username}s Sammlung", user_id)

        # email verification token (local mode: logged / shown in UI)
        token = create_email_verification(conn, user_id)
        sent = mail.send_email(
            email,
            "SWU Card Manager — E-Mail bestätigen",
            f"Hallo {username}!\n\nBitte bestätige deine E-Mail:\n"
            f"https://{'localhost:8765'}/verify-email?token={token}\n\n"
            f"(Lokaler Modus: Token auch im Server-Log.)",
        )

        session_token = create_session(conn, user_id, remember=False)
        session[SESSION_COOKIE] = session_token
        return jsonify({
            "ok": True,
            "platform_admin": first,
            "email_sent": sent,
            "verify_token_local": None if sent else token,
        })
    finally:
        conn.close()


@bp.route("/verify-email", methods=["GET"])
def verify_email_page():
    token = request.args.get("token", "")
    conn = connect()
    try:
        user_id = consume_email_verification(conn, token) if token else None
        if user_id:
            mark_email_verified(conn, user_id)
            return render_template("verify_email.html", ok=True)
        return render_template("verify_email.html", ok=False)
    finally:
        conn.close()


@bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = session.pop(SESSION_COOKIE, None)
    session.pop("active_household_id", None)
    if token:
        conn = connect()
        try:
            destroy_session(conn, token)
        finally:
            conn.close()
    return jsonify({"ok": True})


@bp.route("/api/auth/reset", methods=["POST"])
def api_reset():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Ungültige E-Mail-Adresse"}), 400
    conn = connect()
    try:
        user = get_user_by_login(conn, email)
        # Do not reveal whether the account exists (user enumeration guard)
        if user:
            token = create_password_reset(conn, user["user_id"])
            sent = mail.send_email(
                email,
                "SWU Card Manager — Passwort zurücksetzen",
                f"Passwort zurücksetzen (30 Min gültig):\n"
                f"https://localhost:8765/reset?token={token}\n\n"
                f"(Lokaler Modus: Token auch im Server-Log.)",
            )
            return jsonify({
                "ok": True,
                "email_sent": sent,
                # local mode: give the token back so reset can be tested
                # without SMTP. In production (SMTP configured) this is None.
                "reset_token_local": None if sent else token,
            })
        return jsonify({"ok": True})
    finally:
        conn.close()


@bp.route("/api/auth/reset/confirm", methods=["POST"])
def api_reset_confirm():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    if len(password) < 8:
        return jsonify({"error": "Passwort: mindestens 8 Zeichen"}), 400
    conn = connect()
    try:
        user_id = consume_password_reset(conn, token)
        if user_id is None:
            return jsonify({"error": "Token ungültig oder abgelaufen"}), 400
        set_user_password(conn, user_id, password)
        # invalidate all sessions of that user
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Household API
# ---------------------------------------------------------------------------


@bp.route("/api/auth/me", methods=["GET"])
def api_me():
    if g.get("user") is None:
        return jsonify({"user": None})
    households = []
    conn = connect()
    try:
        households = get_households_for_user(conn, g.user["user_id"])
    finally:
        conn.close()
    return jsonify({
        "user": {
            "username": g.user["username"],
            "display_name": g.user.get("display_name"),
            "is_platform_admin": bool(g.user.get("is_platform_admin")),
            "email_verified": bool(g.user.get("email_verified")),
        },
        "households": households,
        "active_household_id": g.household["household_id"] if g.get("household") else None,
        "can_edit": can_edit(),
    })


@bp.route("/api/auth/household/switch", methods=["POST"])
@login_required
def api_household_switch():
    body = request.get_json(silent=True) or {}
    household_id = body.get("household_id")
    conn = connect()
    try:
        households = get_households_for_user(conn, g.user["user_id"])
        if household_id not in {h["household_id"] for h in households}:
            return jsonify({"error": "Kein Mitglied dieser Sammlung"}), 403
        session["active_household_id"] = household_id
        return jsonify({"ok": True, "household_id": household_id})
    finally:
        conn.close()


@bp.route("/api/auth/household/create", methods=["POST"])
@login_required
def api_household_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) > 60:
        return jsonify({"error": "Name erforderlich (max 60 Zeichen)"}), 400
    conn = connect()
    try:
        household_id = create_household(conn, name, g.user["user_id"])
        session["active_household_id"] = household_id
        return jsonify({"ok": True, "household_id": household_id})
    finally:
        conn.close()


@bp.route("/api/auth/household/join", methods=["POST"])
@login_required
def api_household_join():
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    conn = connect()
    try:
        result = join_household_by_code(conn, code, g.user["user_id"])
        if result.get("ok"):
            session["active_household_id"] = result["household_id"]
        return jsonify(result), 200 if result.get("ok") else 400
    finally:
        conn.close()


@bp.route("/api/auth/household/members", methods=["GET"])
@login_required
def api_household_members():
    if g.get("household") is None:
        return jsonify({"error": "Keine aktive Sammlung"}), 400
    conn = connect()
    try:
        members = get_members(conn, g.household["household_id"])
        return jsonify({"members": members})
    finally:
        conn.close()


@bp.route("/api/auth/household/invite-code", methods=["GET"])
@login_required
def api_household_invite_code():
    if g.get("household") is None or (g.membership or {}).get("role") != "owner":
        return jsonify({"error": "Nur der Owner kann den Code einsehen"}), 403
    conn = connect()
    try:
        household = get_household(conn, g.household["household_id"])
        return jsonify({"code": household["invite_code"]})
    finally:
        conn.close()


@bp.route("/api/auth/household/leave", methods=["POST"])
@login_required
def api_household_leave():
    if g.get("household") is None:
        return jsonify({"error": "Keine aktive Sammlung"}), 400
    conn = connect()
    try:
        result = leave_household(conn, g.household["household_id"], g.user["user_id"])
        if result.get("ok"):
            session.pop("active_household_id", None)
        return jsonify(result), 200 if result.get("ok") else 400
    finally:
        conn.close()
