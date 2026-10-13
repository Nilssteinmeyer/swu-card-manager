"""Merkliste: Preis-Beobachtung und Alarme (Phase 4).

Zwei-Alarm-Modell (Steam/Vinted/Discogs-Muster):
- Act-now: Zielpreis erreicht -> sofort Mail + Alert-Eintrag.
  Anti-Spam: nur der ERSTE Treffer pro Eintrag alarmiert (Cardmarket);
  Reset, wenn der Preis wieder >20% ueber dem Ziel liegt.
- Digest: signifikante Preisbewegungen (>= Schwellwert) werden gesammelt
  und als eine Tages-Zusammenfassung verschickt (1 Mail statt N).

Der Job laeuft nach dem taeglichen Preis-Sync (tcgcsv ~20:00 UTC).
Aufruf: python -m app.wishlist_watch  oder wishlist_watch.run(conn).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.auth import mail as _mail
from app.db import repository as repo

# Einstellbare Schwellen (spaeter via settings-Tabelle)
DIGEST_DROP_PCT = 10.0      # Preis -10% seit gemerkt -> Digest-Eintrag
RESET_ABOVE_PCT = 20.0       # Preis wieder 20% ueber Ziel -> Alarm neu scharf


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def check_wishlist_targets(conn: sqlite3.Connection, household_id: int | None = None) -> dict:
    """Einmal taeglich: Zielpreise pruefen, Act-now-Alerts ausloesen.

    Returns: {'hits': [(wishlist_id, card_name, price, target)], 'reset': int}
    """
    items = repo.get_wishlist(conn, household_id or 1)
    hits: list[dict] = []
    reset_count = 0
    for it in items:
        target = it.get("target_price")
        price = it.get("price")
        if target is None or price is None:
            continue
        reached_before = bool(it.get("target_reached_at"))
        under = price <= float(target)
        if under and not reached_before:
            # ERSTER Treffer -> Act-now (Anti-Spam, Cardmarket-Muster)
            repo.add_wishlist_alert(conn, it["wishlist_id"], "target_hit",
                                    old_price=it.get("price_at_add"), new_price=price)
            conn.execute(
                "UPDATE wishlist SET target_reached_at = ? WHERE wishlist_id = ?",
                (_now(), it["wishlist_id"]),
            )
            conn.commit()
            hits.append({"wishlist_id": it["wishlist_id"], "name": it.get("name", "?"),
                         "price": price, "target": float(target), "currency": it.get("currency")})
        elif not under and reached_before:
            # Preis wieder deutlich ueber Ziel -> Alarm neu scharf schalten
            if float(target) > 0 and price > float(target) * (1 + RESET_ABOVE_PCT / 100):
                conn.execute(
                    "UPDATE wishlist SET target_reached_at = NULL WHERE wishlist_id = ?",
                    (it["wishlist_id"],),
                )
                conn.commit()
                reset_count += 1
    return {"hits": hits, "reset": reset_count}


def collect_digest_entries(conn: sqlite3.Connection, household_id: int = 1) -> list[dict]:
    """Signifikante Preisbewegungen seit dem Merken (fuer Tages-Digest)."""
    entries = []
    for it in repo.get_wishlist(conn, household_id):
        d = it.get("price_delta_pct")
        if d is not None and d <= -DIGEST_DROP_PCT and it.get("price") is not None:
            entries.append({"name": it.get("name", "?"), "price": it["price"],
                            "currency": it.get("currency"), "delta": d})
    return entries


def _fmt(v: float, cur: str | None) -> str:
    return f"{v:.2f} {cur or ''}".strip()


def notify_household_owners(conn: sqlite3.Connection, household_id: int, subject: str, body: str) -> int:
    """Mail an alle Haushalts-Mitglieder mit verifizierter E-Mail (Owner zuerst)."""
    rows = conn.execute(
        """SELECT u.email FROM users u
           JOIN household_members m ON m.user_id = u.user_id
           WHERE m.household_id = ?
             AND u.email IS NOT NULL
             AND u.email NOT LIKE '%@local.invalid'
             AND u.email_verified = 1
           ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END""",
        (household_id,),
    ).fetchall()
    sent = 0
    for r in rows:
        if _mail.send_email(r["email"], subject, body):
            sent += 1
    return sent


def run(conn: sqlite3.Connection, household_id: int = 1, send_mails: bool = True) -> dict:
    """Voller Beobachtungs-Lauf fuer einen Haushalt: Act-now + Digest."""
    result = check_wishlist_targets(conn, household_id)

    # Act-now: je Treffer EINEN Treffer-Mail (in der Praxis sammeln wir alle
    # Treffer eines Tages in EINER Mail - Steam bündelt ebenfalls)
    hits = result["hits"]
    if hits and send_mails:
        lines = ["Deine Merkliste: Zielpreis erreicht! 🎯", ""]
        for h in hits:
            lines.append(f"• {h['name']} — jetzt {_fmt(h['price'], h['currency'])} "
                         f"(Ziel {_fmt(h['target'], h['currency'])})")
        lines += ["", "Zur Merkliste: https://localhost:8765/wishlist"]
        notify_household_owners(conn, household_id,
                                f"SWU Merkliste: {len(hits)} Karte(n) unter Zielpreis",
                                "\n".join(lines))

    # Digest: Preisbewegungen (nur wenn es keine Act-now-Mail gab, sonst
    # waeren es 2 Mails am selben Tag - Steam-Digest-Prinzip)
    digest = collect_digest_entries(conn, household_id)
    if digest and not hits and send_mails:
        lines = ["Deine Merkliste: Preisbewegungen heute", ""]
        for e in digest:
            lines.append(f"• {e['name']} — {_fmt(e['price'], e['currency'])} "
                         f"({e['delta']:+.0f} % seit gemerkt)")
        notify_household_owners(conn, household_id,
                                f"SWU Merkliste: {len(digest)} Preisbewegung(en)",
                                "\n".join(lines))

    result["digest_count"] = len(digest)
    return result


def run_all_households(conn: sqlite3.Connection, send_mails: bool = True) -> list[dict]:
    """Beobachtung fuer alle Haushalte (fuer den Taeglich-Job)."""
    rows = conn.execute("SELECT household_id FROM households").fetchall()
    out = []
    for r in rows:
        out.append({"household_id": r["household_id"],
                    **run(conn, r["household_id"], send_mails=send_mails)})
    return out


if __name__ == "__main__":
    from app.db.schema import connect

    conn = connect()
    try:
        results = run_all_households(conn)
        for r in results:
            print(f"Haushalt {r['household_id']}: {len(r['hits'])} Treffer, "
                  f"{r['reset']} Resets, {r['digest_count']} Digest")
    finally:
        conn.close()
