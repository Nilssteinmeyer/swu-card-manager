"""TCGplayer-Preis-Sync als eigenstaendiges Skript (fuer Task/Startup).

Laedt Produkte+Preise aller TCGplayer-SWU-Gruppen und matcht sie auf
unsere Karten (tcgcsv.sync_all_sets). Idempotent; Dauer ~30-60 s.

Exit-Codes: 0 = ok, 1 = Fehler (Log).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Projekt-Root auf sys.path, damit 'app' importierbar ist (auch als Task/absolut)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("swu_manager.tcg_sync")


def run_sync() -> int:
    from app.db.schema import connect
    from app.db import repository as repo
    from app.integrations import tcgcsv

    conn = connect()
    try:
        cards = repo.get_all_cards(conn)
        set_name_lookup = {
            r["set_id"]: (r["full_name"] or "")
            for r in conn.execute("SELECT set_id, full_name FROM sets").fetchall()
        }
    finally:
        conn.close()

    log.info("Starte TCGplayer-Sync (%d Karten, %d Sets)...", len(cards), len(set_name_lookup))
    result = tcgcsv.sync_all_sets(cards, set_name_lookup)

    prices = result.get("prices", {})
    stats = result.get("stats", {})
    if prices:
        conn = connect()
        try:
            for card_id, price in prices.items():
                repo.upsert_tcg_price(conn, card_id, price)
            conn.commit()
        finally:
            conn.close()

    log.info(
        "TCG-Sync fertig: %d Karten-Preise (Gruppen: %s gesehene / %s gematchte, Produkte: %s)",
        len(prices),
        stats.get("groups_seen"),
        stats.get("groups_matched"),
        stats.get("products"),
    )
    return 0


def run_wishlist_watch() -> int:
    """Nach dem Preis-Sync: Zielpreise pruefen + Alarme (Phase 4)."""
    from app.db.schema import connect
    from app.wishlist_watch import run_all_households

    conn = connect()
    try:
        results = run_all_households(conn)
        hits = sum(len(r["hits"]) for r in results)
        log.info("Merkliste-Watch: %s Zielpreis-Treffer", hits)
        return 0
    except Exception as e:  # noqa: BLE001
        log.error("Merkliste-Watch fehlgeschlagen: %s", e)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        code = run_sync()
        # Beobachtung nur nach erfolgreichem Sync (Fehler blockieren nicht)
        try:
            run_wishlist_watch()
        except Exception as e:  # noqa: BLE001
            log.error("Watch nach Sync fehlgeschlagen: %s", e)
        sys.exit(code)
    except Exception as e:  # noqa: BLE001
        log.error("TCG-Sync fehlgeschlagen: %s", e)
        sys.exit(1)
