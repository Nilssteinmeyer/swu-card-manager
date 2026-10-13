"""Unit-Tests: Nachrichten-Repository + AES-GCM-Verschlüsselung (Phase 1)."""
import sqlite3

import pytest
from cryptography.exceptions import InvalidTag

from app.db.schema import init_database
from app.nachrichten import (
    NachrichtenRepository,
    decrypt_body,
    derive_msg_key,
    encrypt_body,
)


SECRET = "test-secret-key-for-messages-1234"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.schema.get_db_path", lambda: tmp_path / "msg.db")
    c = sqlite3.connect(tmp_path / "msg.db")
    c.row_factory = sqlite3.Row
    init_database(c)
    yield c
    c.close()


@pytest.fixture
def users(conn):
    """Admin + 2 normale User."""
    from app.auth import create_user

    admin = create_user(conn, "admin1", "admin1@x.de", "pw123456", is_platform_admin=True)
    u2 = create_user(conn, "user2", "user2@x.de", "pw123456")
    u3 = create_user(conn, "user3", "user3@x.de", "pw123456")
    return {"admin": admin, "u2": u2, "u3": u3}


@pytest.fixture
def repo(conn):
    return NachrichtenRepository(conn, SECRET)


class TestCrypto:
    def test_roundtrip(self):
        key = derive_msg_key(SECRET)
        ct, iv = encrypt_body("Hallo, das ist eine Nachricht mit ümläuten 🎴", key)
        assert ct != b"Hallo"
        assert decrypt_body(ct, iv, key) == "Hallo, das ist eine Nachricht mit ümläuten 🎴"

    def test_iv_ist_jedes_mal_anders(self):
        key = derive_msg_key(SECRET)
        ct1, iv1 = encrypt_body("gleich", key)
        ct2, iv2 = encrypt_body("gleich", key)
        assert iv1 != iv2, "IV muss je Nachricht frisch sein"
        assert ct1 != ct2

    def test_falscher_schluessel_wirft_invalidtag(self):
        ct, iv = encrypt_body("geheim", derive_msg_key(SECRET))
        with pytest.raises(InvalidTag):
            decrypt_body(ct, iv, derive_msg_key("anderer-key"))

    def test_manipulierter_ciphertext_wirft(self):
        key = derive_msg_key(SECRET)
        ct, iv = encrypt_body("original", key)
        bad = bytearray(ct)
        bad[0] ^= 0xFF
        with pytest.raises(InvalidTag):
            decrypt_body(bytes(bad), iv, key)

    def test_schluessel_deterministisch(self):
        assert derive_msg_key(SECRET) == derive_msg_key(SECRET)
        assert derive_msg_key(SECRET) != derive_msg_key("anders")


class TestDM:
    def test_senden_und_inbox(self, repo, users):
        mid = repo.send_dm(users["u2"], users["u3"], "Hey, tauschst du den Maul?")
        assert mid > 0
        inbox = repo.inbox(users["u3"])
        assert len(inbox) == 1
        assert inbox[0]["body"] == "Hey, tauschst du den Maul?"
        assert inbox[0]["ungelesen"] is True
        assert inbox[0]["partner_id"] == users["u2"]

    def test_body_liegt_verschluesselt_in_db(self, repo, conn, users):
        repo.send_dm(users["u2"], users["u3"], "Klartext der nicht in der DB stehen darf")
        row = conn.execute("SELECT body FROM messages").fetchone()
        assert b"Klartext" not in row["body"], "Body muss verschluesselt gespeichert sein"

    def test_leere_nachricht_verboten(self, repo, users):
        with pytest.raises(ValueError, match="leer"):
            repo.send_dm(users["u2"], users["u3"], "   ")

    def test_selbst_nachricht_verboten(self, repo, users):
        with pytest.raises(ValueError, match="Selbst"):
            repo.send_dm(users["u2"], users["u2"], "notiz")

    def test_unbekannter_empfaenger(self, repo, users):
        with pytest.raises(ValueError, match="Empfänger"):
            repo.send_dm(users["u2"], 99999, "an niemanden")

    def test_zu_lange_nachricht(self, repo, users):
        with pytest.raises(ValueError, match="8000"):
            repo.send_dm(users["u2"], users["u3"], "x" * 8001)

    def test_thread_beidseitig_chronologisch(self, repo, users):
        repo.send_dm(users["u2"], users["u3"], "erste")
        repo.send_dm(users["u3"], users["u2"], "zweite")
        repo.send_dm(users["u2"], users["u3"], "dritte")
        t_u2 = repo.thread(users["u2"], users["u3"])
        assert [m["body"] for m in t_u2] == ["erste", "zweite", "dritte"]
        t_u3 = repo.thread(users["u3"], users["u2"])
        assert [m["body"] for m in t_u3] == ["erste", "zweite", "dritte"]

    def test_mark_read_nur_eigene(self, repo, users):
        mid = repo.send_dm(users["u2"], users["u3"], "hallo")
        # Fremder kann nicht als gelesen markieren
        assert repo.mark_read(users["u2"], message_ids=[mid]) == 0
        assert repo.mark_read(users["u3"], message_ids=[mid]) == 1
        assert repo.mark_read(users["u3"], message_ids=[mid]) == 0  # idempotent

    def test_mark_read_ganzen_thread(self, repo, users):
        repo.send_dm(users["u2"], users["u3"], "a")
        repo.send_dm(users["u2"], users["u3"], "b")
        n = repo.mark_read(users["u3"], partner_id=users["u2"])
        assert n == 2

    def test_mark_unread(self, repo, users):
        mid = repo.send_dm(users["u2"], users["u3"], "wichtig")
        repo.mark_read(users["u3"], message_ids=[mid])
        assert repo.mark_unread(users["u3"], mid) is True
        assert repo.inbox(users["u3"])[0]["ungelesen"] is True

    def test_soft_delete_isoliert(self, repo, users):
        mid = repo.send_dm(users["u2"], users["u3"], "loesch mich")
        repo.send_dm(users["u2"], users["u3"], "bleibe")
        # u3 loescht NUR die erste
        repo.soft_delete(users["u3"], mid)
        t_u3 = repo.thread(users["u3"], users["u2"])
        assert [m["body"] for m in t_u3] == ["bleibe"], "geloeschte Nachricht weg"
        t_u2 = repo.thread(users["u2"], users["u3"])
        assert len(t_u2) == 2, "Sender sieht die Nachricht weiterhin"

    def test_archiv_filter(self, repo, users):
        mid = repo.send_dm(users["u2"], users["u3"], "archivier mich")
        repo.archive(users["u3"], mid)
        assert len(repo.inbox(users["u3"], "alle")) == 0
        assert len(repo.inbox(users["u3"], "archiv")) == 1
        repo.archive(users["u3"], mid, archived=False)
        assert len(repo.inbox(users["u3"], "alle")) == 1

    def test_unread_count(self, repo, users):
        assert repo.unread_count(users["u3"]) == 0
        repo.send_dm(users["u2"], users["u3"], "neu 1")
        repo.send_dm(users["u2"], users["u3"], "neu 2")
        assert repo.unread_count(users["u3"]) == 2
        repo.mark_read(users["u3"], partner_id=users["u2"])
        assert repo.unread_count(users["u3"]) == 0

    def test_gesendete_zaehlen_nicht_als_unread(self, repo, users):
        repo.send_dm(users["u3"], users["u2"], "von mir")
        assert repo.unread_count(users["u3"]) == 0, "eigene gesendete sind gelesen"


class TestBroadcast:
    def test_fanout_erzeugt_receipts(self, repo, conn, users):
        # weiterer User nach Broadcast-Muster
        from app.auth import create_user
        u4 = create_user(conn, "user4", "user4@x.de", "pw123456")
        bid = repo.create_broadcast(users["admin"], "update", "Neues Update v1.1",
                                    "Der Scanner ist jetzt 30% schneller.")
        n = conn.execute("SELECT COUNT(*) FROM broadcast_receipts WHERE broadcast_id = ?",
                         (bid,)).fetchone()[0]
        assert n == 4, "Receipt je User (Admin + u2 + u3 + u4)"

    def test_body_verschluesselt(self, repo, conn, users):
        repo.create_broadcast(users["admin"], "announcement", "Community-Rundruf",
                               "Geheimer Broadcast-Body")
        row = conn.execute("SELECT body FROM broadcasts").fetchone()
        assert b"Geheimer Broadcast-Body" not in row["body"]

    def test_leere_broadcasts_verboten(self, repo, users):
        with pytest.raises(ValueError, match="Betreff"):
            repo.create_broadcast(users["admin"], "update", "  ", "Body")
        with pytest.raises(ValueError, match="leer"):
            repo.create_broadcast(users["admin"], "update", "Titel", "  ")

    def test_typ_validierung(self, repo, users):
        with pytest.raises(ValueError, match="Typ"):
            repo.create_broadcast(users["admin"], "spam", "x", "y")

    def test_ack_nur_eigene_zeile(self, repo, users):
        bid = repo.create_broadcast(users["admin"], "update", "Titel", "Body")
        assert repo.ack_broadcast(users["u2"], bid) is True
        # u3 unberuehrt
        rows = repo.broadcasts_for_user(users["u3"], only_unread=True)
        assert any(r["broadcast_id"] == bid for r in rows)
        rows2 = repo.broadcasts_for_user(users["u2"], only_unread=True)
        assert not any(r["broadcast_id"] == bid for r in rows2), "ack entfernt aus unread"

    def test_unread_count_inkludiert_broadcasts(self, repo, users):
        repo.create_broadcast(users["admin"], "announcement", "Hallo alle", "Text")
        assert repo.unread_count(users["u3"]) == 1
        bid = repo.broadcasts_for_user(users["u3"])[0]["broadcast_id"]
        repo.ack_broadcast(users["u3"], bid)
        assert repo.unread_count(users["u3"]) == 0

    def test_stats(self, repo, users):
        bid = repo.create_broadcast(users["admin"], "update", "T", "B")
        repo.ack_broadcast(users["u2"], bid)
        s = repo.broadcast_stats(bid)
        assert s["empfaenger"] == 3
        assert s["bestaetigt"] == 1
        assert s["gelesen"] == 1

    def test_system_dm_ueber_admin_absender(self, repo, users):
        mid = repo.send_system_dm(users["u3"], "Preis-Alarm", "Maul unter Zielpreis!")
        assert mid > 0
        t = repo.thread(users["u3"], users["admin"])
        assert t[-1]["body"] == "Maul unter Zielpreis!"
