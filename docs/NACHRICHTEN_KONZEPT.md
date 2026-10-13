# Konzept: Internes Postfach „✉️ Nachrichten" (Stand 2026-10-13)

**Recherche-Basis:** 226 Quellen aus 3 parallelen Tiefenrecherchen — (1) TCG-/Community-Plattform-Postfächer (Cardmarket, TCGplayer, Deckbox, CollX, Dragon Shield, phpBB, XenForo, Nextcloud, Discord, Steam, Mastodon, Matrix; 91 Quellen), (2) Datenmodell + Sicherheit (GitLab Pajamas, Gmail/Exchange-Modelle, Slack-Read-Cursor, DSGVO, SQLCipher/AES-GCM; 62 Quellen), (3) UI/UX + Tests (Apple Mail HIG, Material 3, NN/g, WhatsApp/Teams-Broadcasts, Playwright-Strategien; 73 Quellen).

## 1. Zielbild

Ein internes, server-seitig verschlüsseltes Postfach für die lokal gehostete SWU-Community:

- **1:1-Nachrichten** zwischen allen registrierten Usern (flacher Chat-Verlauf je Person, WhatsApp-Muster — kein Threading, Microsoft-Research-Fund: Thread-Bäume erzeugen bei Chats Repräsentationsprobleme)
- **Admin-Broadcasts** (nur `is_platform_admin`): Rundrufe an alle User — z. B. App-Updates, Wartung, Community-News — als eigenes, nicht antwortbares Format mit „Verstanden"-Bestätigung
- **System-Nachrichten** (automatisch): Preis-Alarme der Merkliste und künftige App-Events landen zusätzlich im Postfach — schließt die Lücke „ohne SMTP keine Benachrichtigung" (der zentrale Nutzen bei lokalem Hosting: E-Mail ist optional, das Postfach ist der kanonische In-App-Kanal)
- **Markt-Differenzierung:** Die Recherche bestätigt — Dragon Shield (500K+ Downloads) hat KEIN Postfach; kein Sammler-Tracker ohne Marktplatz hat eines. Für SWU ist es ein echtes Alleinstellungsmerkmal.

## 2. Datenmodell (SQLite, Schema v6)

Kern-Entscheidungen aus der Recherche:

- **Broadcast ≠ Fan-out-Kopien.** XenForo, Nextcloud Announcement Center, Steam Events, GitLab Broadcast Messages: ein Inhaltsobjekt + per-User-Statuszeilen. N Kopien (phpBB-Mass-PM-Muster) skalieren schlecht und machen Unread-Counts teuer. → eigene `broadcasts`-Tabelle + `broadcast_receipts` (read/dismissed pro User) — exakt das bewährte Muster unserer `wishlist_alerts.acknowledged`.
- **Direktnachrichten mit `read_at`-Timestamp statt is_read-Flag** (Empfehlung Datenmodell-Recherche): Unread = `read_at IS NULL`, „Als ungelesen markieren" = `read_at = NULL` zurück (Teams-Selbst-Erinnerungsmuster), keine Zeilen-Updates beim Lesen von Historie.
- **Soft-Delete je Empfänger** (Discord/Slack-Muster, DSGVO-tauglich): `deleted_at` pro Zeile — ein Teilnehmer löscht, ohne den Verlauf des anderen zu zerstören.

```sql
-- 1:1-Nachrichten (flacher Verlauf je (sender, receiver)-Paar)
CREATE TABLE IF NOT EXISTS messages (
    message_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id     INTEGER NOT NULL REFERENCES users(user_id),
    receiver_id   INTEGER NOT NULL REFERENCES users(user_id),
    betreff       TEXT,                        -- optional bei Chat-Verlauf
    body          BLOB NOT NULL,               -- AES-GCM-verschlüsselt (s. §4)
    body_iv       BLOB NOT NULL,               -- Initialisierungsvektor je Nachricht
    created_at    TEXT DEFAULT (datetime('now')),
    read_at       TEXT,                        -- NULL = ungelesen
    archived      INTEGER DEFAULT 0,          -- Empfänger-seitig
    deleted_at    TEXT                         -- Soft-Delete (Empfänger-Sicht)
);
CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages (receiver_id, read_at);
CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages (sender_id, receiver_id, created_at);

-- Admin-Broadcasts: EIN Objekt, keine Kopien
CREATE TABLE IF NOT EXISTS broadcasts (
    broadcast_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    typ           TEXT NOT NULL DEFAULT 'announcement',  -- announcement|update|wartung
    betreff       TEXT NOT NULL,
    body          BLOB NOT NULL,               -- AES-GCM-verschlüsselt
    body_iv       BLOB NOT NULL,
    created_by    INTEGER REFERENCES users(user_id),
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Per-User-Status je Broadcast (read/dismissed, XenForo-Notices-Muster)
CREATE TABLE IF NOT EXISTS broadcast_receipts (
    broadcast_id  INTEGER NOT NULL REFERENCES broadcasts(broadcast_id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(user_id),
    read_at       TEXT,
    acknowledged INTEGER DEFAULT 0,            -- "Verstanden"-Button
    PRIMARY KEY (broadcast_id, user_id)
);
```

**Bewusst verzichtet** (Recherche-Fund): nutzerdefinierte Ordner (phpBB-Legacy — TCGplayer, CollX, Mastodon kommen mit einem Posteingang + Filtern aus), Message-Requests/Eingangsfilter für unbekannte Absender (bei lokalem Hosting mit überschaubaren Usern over-engineered), E2E-Verschlüsselung (s. §4).

## 3. API (Flask)

| Methode | Route | Zweck | Rechte |
|---|---|---|---|
| GET | `/api/messages?filter=alle\|ungelesen\|archiv` | Inbox-Liste (gruppiert: Broadcasts oben, dann DMs) | eingeloggt |
| GET | `/api/messages/thread/<user_id>` | Verlauf mit einem User (beidseitig, chronologisch) | eingeloggt |
| POST | `/api/messages` | DM senden `{receiver_id, body, betreff?}` | eingeloggt; receiver muss existieren |
| POST | `/api/messages/read` | DMs als gelesen markieren `{message_ids[] \| thread_user_id}` | Empfänger |
| POST | `/api/messages/archive` | archivieren/wiederherstellen | Empfänger |
| DELETE | `/api/messages/<id>` | Soft-Delete (nur eigene Ansicht) | Sender/Empfänger |
| GET | `/api/messages/unread_count` | Badge-Zahl (DMs + unsDismissed Broadcasts) | eingeloggt |
| POST | `/api/messages/draft` | Draft persistieren (localStorage-Fallback serverseitig) | eingeloggt |
| GET | `/api/broadcasts` | alle Broadcasts des Users + Status | eingeloggt |
| POST | `/api/broadcasts` | Broadcast anlegen `{typ, betreff, body}` → erzeugt N receipts | **Admin only** |
| POST | `/api/broadcasts/<id>/ack` | „Verstanden" / dismiss (nur eigene Zeile) | eingeloggt |
| GET | `/api/broadcasts/<id>/stats` | Lesestatistik (Steam-Muster: gelesen/bestätigt pro User) | **Admin only** |

**System-Einbindung:** `wishlist_watch.py` schreibt bei Act-now/Treffer zusätzlich eine System-DM an den Haushalts-Owner (Absender = erster Admin bzw. System-Alias) — E-Mail bleibt optionaler Zusatzkanal, das Postfach ist der Pflichtkanal.

## 4. Sicherheitsarchitektur (Threat-Modell: lokales Hosting)

Die Recherche (Signal, Matrix, Mastodon, CollX) liefert ein klares Urteil für unser Setup:

- **Kein E2EE** — Server und Datenbank gehören derselben Partei (dir); E2E brächte Key-Management + Gerät-Bindung, schützt aber nicht vor dem realen Angriffsvektor. Mastodon löst das ehrlich mit einem festen Hinweis im Composer („Nachrichten auf Mastodon sind nicht Ende-zu-Ende-verschlüsselt") — übernehmen wir 1:1.
- **Aber: AES-GCM-Verschlüsselung at rest für Bodies** — die konkrete lokale Bedrohung ist die **DB-Datei selbst** (Backup-Kopie, Fehlkonfiguration eines späteren Go-Live, versehentlicher Git-Kommit von `cards.db`). Nachrichten-Bodies werden mit einem vom `config/secret_key` abgeleiteten Schlüssel (HKDF) AES-256-GCM-verschlüsselt in der DB gespeichert (body IV je Nachricht). Metadaten (Absender, Empfänger, Zeitstempel, read-Status) bleiben unverschlüsselt — nötig für Indexe/Unread-Counts, standard bei Envelope-Modellen. Betreff unverschlüsselt (Listen-Suche), Body verschlüsselt — pragmatischer Mittelweg der Recherche-Empfehlung.
- **Soft-Delete statt Hard-Delete** für DSGVO Art. 17-Konformität: „für dich gelöscht" entfernt die Zeile aus jeder Sicht; Hard-Purge erst beim nächsten Wartungslauf (beidseitig gelöschte > 90 Tage).
- **Rate-Limit** auf `POST /api/messages` (Anti-Spam, 30/min wie /api/scan-Muster).

## 5. UI-Konzept (iPhone-first, Styleguide-konform)

**Platzierung:** ✉️-Icon mit rotem Zahlen-Badge (Cap 99+) in der Topbar (rechts neben Account) + Eintrag „✉️ Nachrichten" im Mehr-Sheet + Desktop-Topbar-Nav. KEIN 6. Tab (Tab-Bar bleibt 5 Items, Apple HIG).

**Inbox (`/messages`):**
- Listen-Item nach Apple-Mail-Kanon + Material-3-4-Slot-Modell: **Avatar 40px | Betreff (fett=ungelesen) + 2-Zeilen-Preview (CSS line-clamp) | relative Zeit | blauer Unread-Dot** — Item ≥ 44px
- Filter-Tabs (Segmented): **Alle / Ungelesen / Archiviert** (WhatsApp-Listen-Muster, keine nutzerdefinierten Ordner)
- Broadcasts als **eigenes visuelles Format**: Akzent-Banner-Karte, 📣-Icon, Absender „SWU Team" mit App-Logo, oben gepinnt; im Detail Banner-Stil, **Reply-Feld komplett ausgeblendet** (nicht deaktiviert — GitLab/Teams-Fund), stattdessen „✓ Verstanden"-Button
- Swipe-Aktionen: voller Swipe = Gelesen/Ungelesen-Toggle, halber Swipe legt Archiv/Löschen frei (Apple-Mail-Swipe-Muster), Aktionen mit Toast statt sofortigem Löschen
- „Alle als gelesen"-Button im Listenkopf (Discord/Teams-Muster)
- **Empty State** mit Illustration + „Noch keine Nachrichten — schreibe Mitgliedern direkt oder empfange Ankündigungen" + CTA „✉️ Neue Nachricht" (44px) — je Filter eigener Zustand

**Verfassen (Bottom-Sheet, iOS-HIG):**
- „An:"-Feld als **Chip-Container mit Kontaktautocomplete** (Avatar + Name + Rolle, 44px-Targets, X-Remove je Chip — Gmail/M3-Chips-Muster); Admin bekommt den Chip **„Alle (Broadcast)"** als Kurzbefehl → wechselt in den Broadcast-Modus (Typ-Picker: Ankündigung/Update/Wartung)
- Body-Textarea; Composer zeigt festen Privatsphäre-Hinweis (Mastodon-Wortlaut): „Nachrichten sind serverseitig verschlüsselt, aber nicht Ende-zu-Ende"
- **Draft-Autosave** (Zulip-Muster): Swipe-Close speichert still (Toast „Entwurf gespeichert"), Wiederherstellen beim erneuten Öffnen, Löschen nach Versand
- Senden → Toast „Nachricht gesendet an <Name>" / „Broadcast an N Nutzer" (Fluent-2-Muster, ~3 s)

**Thread-Detail:** flacher Bubble-Stream (eigene rechts in Akzentfarbe, Tag-Marker „Heute"), Sticky-Header mit Avatar+Name, Swipe-Zurück zur Liste. `color-scheme: dark` für native Picker (bekannter iOS-Pitfall).

**Badge-Disziplin** (NN/g Alert-Fatigue-Fund): genau EIN Badge pro Bereich — Postfach-Badge (DMs + neue Broadcasts) bleibt getrennt vom bestehenden Merklisten-Glocken-Badge; Badge nie alleiniges Signal (Fett+Dot in der Liste), aria-live-Update des Zählers, `aria-label` „N ungelesene Nachrichten".

## 6. Test-Strategie

**Unit/Integration (pytest, vor E2E — Testing-Trophy):**
- Repository: send/receive, read_at-Übergang, Unread-Count-Arithmetik, Soft-Delete-Isolation (A löscht, B sieht weiter), Broadcast-Fanout erzeugt N receipts, Ack nur eigene Zeile, AES-GCM Roundtrip (verschlüsselt speichern, entschlüsselt lesen, falscher Key → Fehler), Rate-Limit
- API: Rechte (Nicht-Admin kann keinen Broadcast, Fremde receipts nicht ackbar), Validierung (leerer Body, unbekannter Empfänger)

**E2E (Playwright, echte Taps, echte Touch-Swipes — das lernen wir):**
1. **Sende-Roundtrip (2 Browser-Kontexte):** A wählt B per Chip-Picker, sendet → Toast; B: Badge steigt, Item fett+Dot; B öffnet → Dot weg, Badge sinkt; Zustand überlebt Reload (web-first assertions, keine sleeps)
2. **Badge-Arithmetik:** 3 Unread in 2 Threads, nacheinander lesen → Badge dekrementiert exakt
3. **Broadcast an ALLE:** Admin sendet, 2 User-Kontexte sehen ihn, beide Badges +1; nur User A liest → nur Badge A sinkt (Empfänger-Isolation); kein Reply-Element vorhanden (assert absence); „Verstanden" nur für den eine:n
4. **Draft-Roundtrip:** Text eingeben, Sheet per Swipe schließen, neu öffnen → Draft da; nach Senden weg
5. **Sichtbarkeitsprobe** nach dem Sheet-z-Index-Debakel: Composer-Sheet und Broadcast-Detail müssen `elementFromPoint`-Sichtbarkeit bestehen, Swipe-Close registriert (DYN_CLOSE-Tabelle!)
6. **Swipe-Aktionen:** Listen-Item voll ausswischen = Read-Toggle, halber Swipe zeigt Archiv
7. **A11y:** Badge-aria-label, Unread-Sr-only-Text, 44px-Messung, Kontrast Dark/Light (WCAG ≥ 4.5)
8. **System-Nachricht:** Wishlist-Act-now erzeugt Postfach-Eintrag beim Owner

**Selbst-Optimierung:** 3 Durchläufe mit Screenshot + OCR-Forensik (Badge-Zustände 0/1/99+, Broadcast-Karte, Empty State) in Dark + Light — Befundzähler je Durchlauf.

## 7. Phasenplan (je Phase 1 Commit, Suite grün als Gate, Live-Verifikation)

| Phase | Inhalt | Tests |
|---|---|---|
| **1** | Schema v6 (messages, broadcasts, receipts) + AES-GCM-Crypto-Modul (`app/nachrichten.py`) + Repository + System-Hook (Preis-Alarm → Postfach) | ~14 Unit |
| **2** | API komplett (11 Routen, Rechte, Rate-Limit) | ~10 Unit/API |
| **3** | Inbox-UI: `/messages`-Seite, Listen-Item, Filter-Tabs, Badge in Topbar/Mehr-Sheet, Swipe-Aktionen, Empty States | JS-Checks + Live |
| **4** | Composer-Sheet (Chip-Picker, Draft-Autosave, Privatsphäre-Hinweis) + Thread-Detail (Bubbles) | JS-Checks + Live |
| **5** | Broadcast-Format (Banner-Karte, pinned, nicht antwortbar, Verstanden, Admin-Stats in system.html) | JS-Checks + Live |
| **6** | E2E-Suite (8 Szenarien) + 3 Selbst-Optimierungs-Durchläufe + Vollläufe + Live-Verifikation auf Produktiv-DB | 31→39 E2E |

## 8. Nicht-Ziele (bewusst draußen)

- E2E-Verschlüsselung (Threat-Modell lokal, s. §4) — dokumentiert statt simuliert
- Nutzergenerierte Ordner/Labels (phpBB-Legacy; Filter-Tabs decken den Bedarf)
- Message-Requests/Eingangsfilter (lokal sind alle User registriert und sichtbar)
- Anhänge/Bilder in Nachrichten (CollX-Fund: eingebettete Bilder verschwinden bei Cardmarket — Anhänge sind ein eigenes, wartungsintensives Feature; evtl. Backlog)
- Push-Benachrichtigungen (Web-Push ist ein separater Baukasten; Badge + ggf. PWA-Polling reichen)
- Gruppen-DMs/Threads (flacher 1:1-Verlauf ist der belegte Standard für kleine Communities)
