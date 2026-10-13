"""Tests: Merkliste v4 Phase 4 — Zielpreis-Alarme, Anti-Spam, Digest."""
import pytest

from app.db import repository as repo
from app.wishlist_watch import (
    RESET_ABOVE_PCT,
    check_wishlist_targets,
    collect_digest_entries,
    run,
)


class TestWishlistWatch:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "wl_watch.db")
        from app.db.schema import connect as _connect, init_database

        conn = _connect(tmp_path / "wl_watch.db")
        init_database(conn)
        conn.execute("INSERT OR IGNORE INTO sets (set_id, full_name) VALUES ('SOR', 'Spark of Rebellion')")
        conn.execute(
            "INSERT OR IGNORE INTO cards (card_id, set_id, card_number, name, name_de, type, rarity, aspects) "
            "VALUES ('SOR-001', 'SOR', '001', 'Darth Vader', 'Darth Vader', 'Leader', 'Special', '[\"Aggression\"]')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, password_hash, is_platform_admin, email_verified) "
            "VALUES (1, 'owner1', 'owner@example.com', 'x', 0, 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO households (household_id, name, owner_user_id) VALUES (1, 'Testhaus', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO household_members (household_id, user_id, role) VALUES (1, 1, 'owner')"
        )
        conn.commit()
        yield conn
        conn.close()

    def _set_tcg_price(self, db, market):
        db.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, updated_at) "
            "VALUES ('SOR-001', 999, ?, '2026-10-13')",
            (market,),
        )
        db.commit()

    def test_target_hit_creates_alert_once(self, db):
        """Erster Treffer: target_reached_at gesetzt + Alert-Eintrag; zweiter Lauf: STILL."""
        self._set_tcg_price(db, 0.50)
        repo.add_to_wishlist(db, "SOR-001", target_price=0.80, household_id=1)
        db.commit()

        r1 = check_wishlist_targets(db, 1)
        assert len(r1["hits"]) == 1
        assert r1["hits"][0]["price"] == 0.50
        # Anti-Spam: zweiter Lauf feuert NICHT erneut
        r2 = check_wishlist_targets(db, 1)
        assert len(r2["hits"]) == 0
        alerts = repo.get_wishlist_alerts(db, 1)
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "target_hit"

    def test_target_reset_after_rise(self, db):
        """Preis wieder >20% ueber Ziel -> Alarm neu scharf -> neuer Treffer moeglich."""
        self._set_tcg_price(db, 0.50)
        repo.add_to_wishlist(db, "SOR-001", target_price=0.80, household_id=1)
        db.commit()
        check_wishlist_targets(db, 1)  # Treffer 1
        # Preis steigt auf 1.20 (50% ueber 0.80-Ziel) -> Reset
        self._set_tcg_price(db, 1.20)
        r = check_wishlist_targets(db, 1)
        assert r["reset"] == 1
        # Preis faellt wieder -> zweiter Act-now
        self._set_tcg_price(db, 0.40)
        r2 = check_wishlist_targets(db, 1)
        assert len(r2["hits"]) == 1
        assert len(repo.get_wishlist_alerts(db, 1)) == 2

    def test_no_hit_above_target(self, db):
        """Preis ueber Ziel -> kein Alert."""
        self._set_tcg_price(db, 2.00)
        repo.add_to_wishlist(db, "SOR-001", target_price=0.80, household_id=1)
        db.commit()
        r = check_wishlist_targets(db, 1)
        assert len(r["hits"]) == 0
        assert len(repo.get_wishlist_alerts(db, 1)) == 0

    def test_digest_collects_big_drops(self, db):
        """-10% seit gemerkt (price_at_add) -> Digest-Eintrag."""
        # Preis beim Merken: 1.00; jetzt 0.50 -> -50%
        db.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, updated_at) "
            "VALUES ('SOR-001', 999, 1.00, '2026-10-13')"
        )
        db.commit()
        repo.add_to_wishlist(db, "SOR-001", household_id=1)  # price_at_add=1.00 (tcg)
        db.commit()
        self._set_tcg_price(db, 0.50)
        entries = collect_digest_entries(db, 1)
        assert len(entries) == 1
        assert entries[0]["delta"] == -50.0

    def test_run_sends_no_mail_unverified(self, db, monkeypatch):
        """Mail nur an verifizierte E-Mails; run() crasht nicht ohne SMTP."""
        self._set_tcg_price(db, 0.50)
        repo.add_to_wishlist(db, "SOR-001", target_price=0.80, household_id=1)
        db.commit()
        sent = []
        monkeypatch.setattr("app.wishlist_watch.notify_household_owners",
                            lambda conn, hh, subj, body: sent.append(subj) or 1)
        r = run(db, 1, send_mails=True)
        assert len(r["hits"]) == 1
        assert len(sent) == 1, "Act-now-Mail genau einmal"
        assert "Zielpreis" in sent[0]

    def test_run_digest_only_when_no_hits(self, db, monkeypatch):
        """Digest-Mail nur wenn KEINE Act-now-Mail am selben Tag (Steam-Prinzip)."""
        db.execute(
            "INSERT OR REPLACE INTO tcg_prices (card_id, product_id, market_normal, updated_at) "
            "VALUES ('SOR-001', 999, 1.00, '2026-10-13')"
        )
        db.commit()
        repo.add_to_wishlist(db, "SOR-001", household_id=1)  # kein Ziel -> -50% Digest
        db.commit()
        self._set_tcg_price(db, 0.50)
        sent = []
        monkeypatch.setattr("app.wishlist_watch.notify_household_owners",
                            lambda conn, hh, subj, body: sent.append(subj) or 1)
        r = run(db, 1, send_mails=True)
        assert len(r["hits"]) == 0
        assert r["digest_count"] == 1
        assert len(sent) == 1, "Genau die Digest-Mail"
