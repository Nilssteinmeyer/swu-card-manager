"""Internes Postfach: Nachrichten-Repository + AES-GCM-Verschlüsselung.

Architektur (NACHRICHTEN_KONZEPT.md, 226-Quellen-Recherche):
- 1:1-Nachrichten (messages): flacher Verlauf je Paar, read_at-Timestamp
  statt is_read-Flag, Soft-Delete je Empfänger (Discord/Slack-Muster)
- Broadcasts: EIN Objekt + broadcast_receipts je User (XenForo/Nextcloud/
  Steam-Muster) - nie Fan-out-Kopien
- Bodies AES-256-GCM-verschlüsselt at rest (Threat: DB-Datei-Kopie),
  Schlüssel per HKDF aus app.secret_key abgeleitet (SQLCipher-Envelope-
  Muster); Metadaten klartext für Indexe/Unread-Counts
"""
from __future__ import annotations

import os
import sqlite3

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ---------------------------------------------------------------------------
# AES-GCM Envelope (at rest)
# ---------------------------------------------------------------------------

def derive_msg_key(secret_key: str) -> bytes:
    """Leitet den 256-bit Nachrichten-Schlüssel per HKDF ab (getrennt vom
    Session-Secret: Kompromittierung eines Keys nie beide Welten)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"swu-nachrichten-v1",
    ).derive(secret_key.encode("utf-8"))


def encrypt_body(plain: str, key: bytes) -> tuple[bytes, bytes]:
    """Returns (ciphertext, iv). AES-GCM: iv 96-bit, Auth-Tag inklusive."""
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)
    return ct, iv


def decrypt_body(ct: bytes, iv: bytes, key: bytes) -> str:
    """Wirft InvalidTag bei falschem Schlüssel/manipuliertem Ciphertext."""
    return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")


class NachrichtenRepository:
    """Alle DB-Operationen für Postfach + Broadcasts."""

    def __init__(self, conn: sqlite3.Connection, secret_key: str):
        self.conn = conn
        self.key = derive_msg_key(secret_key)

    # -- 1:1 --------------------------------------------------------------

    def send_dm(self, sender_id: int, receiver_id: int, body: str,
                betreff: str | None = None) -> int:
        if not body or not body.strip():
            raise ValueError("Nachricht ist leer")
        if len(body) > 8000:
            raise ValueError("Nachricht zu lang (max 8000 Zeichen)")
        if sender_id == receiver_id:
            raise ValueError("Selbst-Nachrichten sind nicht vorgesehen")
        row = self.conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (receiver_id,),
        ).fetchone()
        if not row:
            raise ValueError("Empfänger nicht gefunden")
        ct, iv = encrypt_body(body, self.key)
        cur = self.conn.execute(
            """INSERT INTO messages (sender_id, receiver_id, betreff, body, body_iv)
               VALUES (?, ?, ?, ?, ?)""",
            (sender_id, receiver_id, betreff, ct, iv),
        )
        self.conn.commit()
        return cur.lastrowid

    def _decrypt_row(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["body"] = decrypt_body(d["body"], d["body_iv"], self.key)
        d.pop("body_iv", None)
        return d

    def inbox(self, user_id: int, filter_: str = "alle", limit: int = 100) -> list[dict]:
        """Inbox: neueste Nachricht je Gesprächspartner + offene Broadcasts."""
        clauses = [
            "(m.receiver_id = ? OR m.sender_id = ?)",
            "((m.receiver_id = ? AND m.receiver_deleted_at IS NULL) OR (m.sender_id = ? AND m.sender_deleted_at IS NULL))",
        ]
        params: list = [user_id, user_id, user_id, user_id]
        if filter_ == "ungelesen":
            clauses.append("m.read_at IS NULL AND m.receiver_id = ?")
            params.append(user_id)
        elif filter_ == "archiv":
            clauses.append("m.archived = 1")
        else:
            clauses.append("m.archived = 0")
        rows = self.conn.execute(
            f"""SELECT m.* FROM messages m
                WHERE {' AND '.join(clauses)}
                ORDER BY m.created_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        out = [self._decrypt_row(r) for r in rows]
        for o in out:
            partner = o["sender_id"] if o["receiver_id"] == user_id else o["receiver_id"]
            p = self.conn.execute(
                "SELECT username, display_name FROM users WHERE user_id = ?", (partner,),
            ).fetchone()
            o["partner_name"] = (p["display_name"] or p["username"]) if p else "?"
            o["partner_id"] = partner
            o["ungelesen"] = o["read_at"] is None and o["receiver_id"] == user_id
        return out

    def thread(self, user_id: int, partner_id: int, limit: int = 200) -> list[dict]:
        """Beidseitiger Verlauf, chronologisch (flach, WhatsApp-Muster)."""
        rows = self.conn.execute(
            """SELECT * FROM messages
               WHERE ((sender_id = ? AND receiver_id = ? AND sender_deleted_at IS NULL)
                   OR (sender_id = ? AND receiver_id = ? AND receiver_deleted_at IS NULL))
               ORDER BY created_at DESC, message_id DESC LIMIT ?""",
            (user_id, partner_id, partner_id, user_id, limit),
        ).fetchall()
        out = [self._decrypt_row(r) for r in reversed(rows)]
        return out

    def mark_read(self, user_id: int, message_ids: list[int] | None = None,
                  partner_id: int | None = None) -> int:
        """Read-Receipt: read_at setzen (nur eigene, ungelesene)."""
        if message_ids:
            qs = ",".join("?" * len(message_ids))
            cur = self.conn.execute(
                f"""UPDATE messages SET read_at = datetime('now')
                    WHERE message_id IN ({qs}) AND receiver_id = ? AND read_at IS NULL""",
                (*message_ids, user_id),
            )
        elif partner_id is not None:
            cur = self.conn.execute(
                """UPDATE messages SET read_at = datetime('now')
                   WHERE sender_id = ? AND receiver_id = ? AND read_at IS NULL""",
                (partner_id, user_id),
            )
        else:
            return 0
        self.conn.commit()
        return cur.rowcount

    def mark_unread(self, user_id: int, message_id: int) -> bool:
        cur = self.conn.execute(
            """UPDATE messages SET read_at = NULL
               WHERE message_id = ? AND receiver_id = ?""",
            (message_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def archive(self, user_id: int, message_id: int, archived: bool = True) -> bool:
        cur = self.conn.execute(
            """UPDATE messages SET archived = ?
               WHERE message_id = ? AND receiver_id = ?""",
            (1 if archived else 0, message_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def soft_delete(self, user_id: int, message_id: int) -> bool:
        """Soft-Delete: NUR die eigene Sicht (Discord/Slack-Muster, DSGVO).

        Sender und Empfänger haben getrennte delete-Flags - loescht der
        Empfaenger, sieht der Sender die Nachricht weiterhin (SO-
        MESSAGE_READ_STATE-Muster: read und deleted sind je Teilnehmer
        eigenstaendig).
        """
        cur = self.conn.execute(
            """UPDATE messages
               SET sender_deleted_at = CASE WHEN sender_id = ? THEN datetime('now') ELSE sender_deleted_at END,
                   receiver_deleted_at = CASE WHEN receiver_id = ? THEN datetime('now') ELSE receiver_deleted_at END
               WHERE message_id = ? AND (receiver_id = ? OR sender_id = ?)""",
            (user_id, user_id, message_id, user_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def unread_count(self, user_id: int) -> int:
        """Badge-Zahl: ungelesene DMs + neue (unacknowledged) Broadcasts."""
        dms = self.conn.execute(
            """SELECT COUNT(*) FROM messages
               WHERE receiver_id = ? AND read_at IS NULL AND receiver_deleted_at IS NULL""",
            (user_id,),
        ).fetchone()[0]
        broadcasts = self.conn.execute(
            """SELECT COUNT(*) FROM broadcasts b
               WHERE NOT EXISTS (SELECT 1 FROM broadcast_receipts r
                                 WHERE r.broadcast_id = b.broadcast_id AND r.user_id = ?
                                   AND r.acknowledged = 1)""",
            (user_id,),
        ).fetchone()[0]
        return dms + broadcasts

    # -- Broadcasts ---------------------------------------------------------

    def create_broadcast(self, admin_id: int, typ: str, betreff: str,
                         body: str) -> int:
        if typ not in ("announcement", "update", "wartung"):
            raise ValueError("Ungültiger Broadcast-Typ")
        if not betreff.strip():
            raise ValueError("Betreff erforderlich")
        if not body.strip():
            raise ValueError("Nachricht ist leer")
        if len(body) > 8000:
            raise ValueError("Nachricht zu lang (max 8000 Zeichen)")
        ct, iv = encrypt_body(body, self.key)
        cur = self.conn.execute(
            """INSERT INTO broadcasts (typ, betreff, body, body_iv, created_by)
               VALUES (?, ?, ?, ?, ?)""",
            (typ, betreff.strip(), ct, iv, admin_id),
        )
        broadcast_id = cur.lastrowid
        # Receipt je User - Admin selbst inclusive (Steam-seen-Muster)
        users = self.conn.execute("SELECT user_id FROM users").fetchall()
        for u in users:
            self.conn.execute(
                "INSERT OR IGNORE INTO broadcast_receipts (broadcast_id, user_id) VALUES (?, ?)",
                (broadcast_id, u["user_id"]),
            )
        self.conn.commit()
        return broadcast_id

    def broadcasts_for_user(self, user_id: int, only_unread: bool = False) -> list[dict]:
        sql = """SELECT b.*, r.read_at, r.acknowledged
                 FROM broadcasts b
                 JOIN broadcast_receipts r ON r.broadcast_id = b.broadcast_id
                 WHERE r.user_id = ?"""
        params: list = [user_id]
        if only_unread:
            sql += " AND r.acknowledged = 0"
        sql += " ORDER BY b.created_at DESC LIMIT 50"
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["body"] = decrypt_body(d["body"], d["body_iv"], self.key)
            d.pop("body_iv", None)
            out.append(d)
        return out

    def ack_broadcast(self, user_id: int, broadcast_id: int) -> bool:
        """'Verstanden': dismissed + read in EINEM Schritt (nur eigene Zeile)."""
        cur = self.conn.execute(
            """UPDATE broadcast_receipts
               SET acknowledged = 1, read_at = datetime('now')
               WHERE broadcast_id = ? AND user_id = ?""",
            (broadcast_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def broadcast_stats(self, broadcast_id: int) -> dict:
        """Admin-Lesestatistik (Steam-Muster: pro User gelesen/bestätigt)."""
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN acknowledged = 1 THEN 1 ELSE 0 END) AS acked,
                      SUM(CASE WHEN read_at IS NOT NULL THEN 1 ELSE 0 END) AS read_cnt
               FROM broadcast_receipts WHERE broadcast_id = ?""",
            (broadcast_id,),
        ).fetchone()
        return {
            "empfaenger": row["total"],
            "bestaetigt": row["acked"] or 0,
            "gelesen": row["read_cnt"] or 0,
        }

    # -- System-Hook (Preis-Alarm -> Postfach) ------------------------------

    def send_system_dm(self, receiver_id: int, betreff: str, body: str) -> int:
        """System-Absender = erster Platform-Admin (fallback sender=receiver
        ist verboten -> Admin existiert per Definition, Registrierung macht
        ersten User zum Admin)."""
        admin = self.conn.execute(
            "SELECT user_id FROM users WHERE is_platform_admin = 1 ORDER BY user_id LIMIT 1"
        ).fetchone()
        if not admin:
            return 0
        return self.send_dm(admin["user_id"], receiver_id, body, betreff=betreff)
