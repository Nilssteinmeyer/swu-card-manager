"""
Flask backend for the SWU Card Manager web UI.

Exposes a REST API consumed by the static HTML/JS frontend.
All endpoints return JSON. Listens on 0.0.0.0:8765 by default
so the app is reachable from a phone on the same WLAN.

Run with:
    python -m app.web
    python -m app.web --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

# Ensure project root is importable when running as a script
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask_cors import CORS

from app.core.config import AppConfig
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.schema import connect, init_database

log = get_logger("web")

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(config: AppConfig | None = None) -> Flask:
    """Create and configure the Flask application."""
    cfg = config or AppConfig.load()
    init_database()

    template_dir = str(Path(__file__).parent / "templates")
    static_dir = str(Path(__file__).parent / "static")
    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )
    app.config["JSON_SORT_KEYS"] = False
    # -- Hardening ------------------------------------------------------------
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB upload cap (base64 images)
    # CORS: same-origin for the UI; LAN hosts are allowed explicitly (scanner
    # runs on the phone in the same network). No wildcard in production.
    _cors_origins = [
        "https://localhost:8765",
        "https://127.0.0.1:8765",
        f"https://{cfg.get('lan_ip', '192.168.178.74')}:8765",
    ]
    CORS(app, resources={r"/api/*": {"origins": _cors_origins}})

    # Lazily-initialised singletons (heavy objects)
    app.config["_SWU_CFG"] = cfg
    app.config["_ENGINE"] = None
    app.config["_UPDATE_MGR"] = None

    _register_routes(app)

    # -- Global error handlers -------------------------------------------------
    @app.errorhandler(413)
    def _too_large(e):
        return jsonify({"error": "Payload zu groß (max 12 MB)"}), 413

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "Nicht gefunden"}), 404

    @app.errorhandler(500)
    def _server_error(e):
        log.error(f"Unhandled server error: {e}", exc_info=True)
        return jsonify({"error": "Interner Serverfehler"}), 500

    return app


def _get_engine(app: Flask):
    """Lazily create the CLIP RecognitionEngine singleton."""
    if app.config["_ENGINE"] is None:
        try:
            from app.recognition.clip_engine import RecognitionEngine

            app.config["_ENGINE"] = RecognitionEngine(app.config["_SWU_CFG"])
            log.info("CLIP RecognitionEngine initialised")
        except Exception as e:
            log.error(f"Failed to initialise CLIP RecognitionEngine: {e}")
            app.config["_ENGINE"] = False  # mark as unavailable
    return app.config["_ENGINE"]


def _get_update_manager(app: Flask):
    """Lazily create the UpdateManager singleton."""
    if app.config["_UPDATE_MGR"] is None:
        try:
            from app.providers.update_manager import UpdateManager

            app.config["_UPDATE_MGR"] = UpdateManager(app.config["_SWU_CFG"])
            log.info("UpdateManager initialised")
        except Exception as e:
            log.error(f"Failed to initialise UpdateManager: {e}")
            app.config["_UPDATE_MGR"] = False
    return app.config["_UPDATE_MGR"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_list(raw: str | None) -> list:
    """Safely parse a JSON-encoded list from a DB string column."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [v]
    except (json.JSONDecodeError, TypeError):
        return []


def _card_to_dict(card: dict[str, Any]) -> dict[str, Any]:
    """Normalise a card row for JSON output."""
    return {
        "card_id": card.get("card_id", ""),
        "set_id": card.get("set_id", ""),
        "card_number": card.get("card_number", ""),
        "name": card.get("name", ""),
        "subtitle": card.get("subtitle", ""),
        "type": card.get("type", ""),
        "rarity": card.get("rarity", ""),
        "aspects": _parse_json_list(card.get("aspects")),
        "traits": _parse_json_list(card.get("traits")),
        "arenas": _parse_json_list(card.get("arenas")),
        "cost": card.get("cost", ""),
        "power": card.get("power", ""),
        "hp": card.get("hp", ""),
        "front_art_url": card.get("front_art_url", ""),
        "front_art_path": card.get("front_art_path", ""),
        "unique_card": bool(card.get("unique_card", 0)),
        "double_sided": bool(card.get("double_sided", 0)),
        "variant_type": card.get("variant_type", "Normal"),
        "market_price": card.get("market_price", ""),
        "low_price": card.get("low_price", ""),
    }


def _collection_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "collection_id": row.get("collection_id"),
        "card_id": row.get("card_id", ""),
        "count": row.get("count", 1),
        "condition": row.get("condition", "NM"),
        "language": row.get("language", "en"),
        "variant": row.get("variant", "Normal"),
        "location": row.get("location"),
        "acquired_at": row.get("acquired_at", ""),
        "source": row.get("source", "scan"),
        "name": row.get("name", ""),
        "subtitle": row.get("subtitle", ""),
        "set_id": row.get("set_id", ""),
        "card_number": row.get("card_number", ""),
        "rarity": row.get("rarity", ""),
        "type": row.get("type", ""),
        "front_art_path": row.get("front_art_path", ""),
    }


def _image_to_cv2(data_uri: str):
    """Decode a base64 data-URI (or raw base64) into an OpenCV BGR image."""
    import cv2

    if "," in data_uri:
        data_uri = data_uri.split(",", 1)[1]
    raw = base64.b64decode(data_uri)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _register_routes(app: Flask) -> None:

    # -- Pages -------------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/scanner")
    def scanner_page():
        return render_template("scanner.html")

    @app.route("/collection")
    def collection_page():
        return render_template("collection.html")

    @app.route("/sets")
    def sets_page():
        return render_template("sets.html")

    @app.route("/system")
    def system_page():
        return render_template("system.html")

    @app.route("/settings")
    def settings_page():
        return render_template("settings.html")

    # -- Dashboard ---------------------------------------------------------
    @app.route("/api/dashboard")
    def api_dashboard():
        conn = connect()
        try:
            # Dashboard shows ONLY collection stats — no training/reference data
            collection_total = repo.get_collection_count(conn)
            collection_unique = repo.get_unique_collection_count(conn)
            scans_today = repo.get_scan_count_today(conn)
            recognition_rate = repo.get_recognition_rate(conn)

            # Rarity breakdown — from COLLECTION only
            cur = conn.execute(
                """SELECT cards.rarity, SUM(col.count) as c
                   FROM collection col JOIN cards ON col.card_id = cards.card_id
                   WHERE cards.rarity IS NOT NULL AND cards.rarity != ''
                   GROUP BY cards.rarity ORDER BY c DESC"""
            )
            rarity_breakdown = [{"rarity": r["rarity"], "count": r["c"]} for r in cur.fetchall()]

            # Type breakdown — from COLLECTION only
            cur = conn.execute(
                """SELECT cards.type, SUM(col.count) as c
                   FROM collection col JOIN cards ON col.card_id = cards.card_id
                   WHERE cards.type IS NOT NULL AND cards.type != ''
                   GROUP BY cards.type ORDER BY c DESC"""
            )
            type_breakdown = [{"type": r["type"], "count": r["c"]} for r in cur.fetchall()]

            # Set breakdown — from COLLECTION only
            cur = conn.execute(
                """SELECT cards.set_id, SUM(col.count) as c
                   FROM collection col JOIN cards ON col.card_id = cards.card_id
                   GROUP BY cards.set_id ORDER BY c DESC"""
            )
            set_breakdown = [{"set_id": r["set_id"], "count": r["c"]} for r in cur.fetchall()]

            # Recent scans (from scan history)
            recent_scans = repo.get_recent_scans(conn, limit=10)

            # Foil count
            cur = conn.execute("SELECT COALESCE(SUM(count), 0) FROM collection WHERE is_foil = 1")
            foil_count = cur.fetchone()[0]

            return jsonify({
                "collection_total": collection_total,
                "collection_unique": collection_unique,
                "scans_today": scans_today,
                "scans_total": len(recent_scans),
                "recognition_rate": round(recognition_rate, 4),
                "foil_count": foil_count,
                "rarity_breakdown": rarity_breakdown,
                "type_breakdown": type_breakdown,
                "set_breakdown": set_breakdown,
            })
        finally:
            conn.close()

    # -- Camera ------------------------------------------------------------
    @app.route("/api/camera/start")
    def api_camera_start():
        # Camera is handled entirely in the browser via getUserMedia.
        # This endpoint reports backend readiness.
        engine = _get_engine(app)
        ready = bool(engine)
        return jsonify({
            "camera": "browser",
            "backend_ready": ready,
            "capture_interval_ms": app.config["_SWU_CFG"].get("camera.capture_interval_ms", 2000),
        })

    # -- Scan --------------------------------------------------------------
    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        body = request.get_json(silent=True) or {}
        image_data = body.get("image")
        if not image_data:
            return jsonify({"error": "No image provided"}), 400

        engine = _get_engine(app)
        if not engine:
            return jsonify({"error": "Recognition engine unavailable"}), 503

        try:
            img = _image_to_cv2(image_data)
            if img is None:
                return jsonify({"error": "Could not decode image"}), 400

            # Save scan image temporarily
            images_dir = app.config["_SWU_CFG"].path("images_dir")
            scans_dir = images_dir.parent / "scans"
            scans_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_path = scans_dir / f"scan_{ts}.png"
            import cv2
            cv2.imwrite(str(save_path), img)

            conn = connect()
            try:
                result = engine.recognize(img, conn=conn, save_path=str(save_path))

                # Record the scan
                scan_id = repo.record_scan(
                    conn,
                    image_path=str(save_path),
                    recognized_card_id=result.card_id,
                    confidence=result.confidence,
                    method=result.method,
                    ocr_text=result.ocr_text,
                    processing_time_ms=result.processing_time_ms,
                )
                if result.candidates:
                    repo.record_scan_candidates(conn, scan_id, result.candidates)

                return jsonify({
                    "scan_id": scan_id,
                    "card_id": result.card_id,
                    "card_name": result.card_name,
                    "confidence": round(result.confidence, 4),
                    "candidates": result.candidates[:5],
                    "ocr_text": result.ocr_text,
                    "processing_time_ms": result.processing_time_ms,
                    "auto_accepted": result.card_id is not None,
                })
            finally:
                conn.close()

        except Exception as e:
            log.error(f"Scan error: {e}\n{traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

    # -- Scan confirm ------------------------------------------------------
    @app.route("/api/scan/confirm", methods=["POST"])
    def api_scan_confirm():
        body = request.get_json(silent=True) or {}
        scan_id = body.get("scan_id")
        confirmed = body.get("confirmed")  # bool
        card_id = body.get("card_id")  # the accepted card_id
        original_card_id = body.get("original_card_id", "")  # what was originally recognised
        add_to_collection = body.get("add_to_collection", True)
        image_path = body.get("image_path", "")
        is_foil = body.get("is_foil", False)
        photo_data = body.get("photo")  # base64 photo from camera
        quantity = body.get("quantity", 1)

        if scan_id is None:
            return jsonify({"error": "scan_id required"}), 400

        conn = connect()
        try:
            engine = _get_engine(app)
            if confirmed and card_id:
                # If a different card than originally recognised was chosen,
                # record the correction for ML learning.
                repo.correct_scan(conn, scan_id, card_id)
                if original_card_id and original_card_id != card_id and engine:
                    engine.learn_from_correction(conn, scan_id, original_card_id, card_id, image_path)

                if add_to_collection:
                    variant = "Foil" if is_foil else "Normal"
                    repo.add_to_collection(
                        conn,
                        card_id=card_id,
                        count=quantity,
                        condition="NM",
                        language="en",
                        variant=variant,
                        source="scan",
                    )
                    # Set is_foil flag on the newly added entry
                    if is_foil:
                        cur = conn.execute(
                            "SELECT MAX(collection_id) FROM collection WHERE card_id=?",
                            (card_id,),
                        )
                        last_id = cur.fetchone()[0]
                        if last_id:
                            conn.execute(
                                "UPDATE collection SET is_foil=1 WHERE collection_id=?",
                                (last_id,),
                            )
                            conn.commit()

                # Save the photo for training ONLY on confirm
                saved_photo_path = ""
                if photo_data:
                    import base64 as b64mod
                    import time as _time
                    images_dir = app.config["_SWU_CFG"].path("images_dir")
                    train_dir = images_dir.parent / "scans" / "confirmed"
                    train_dir.mkdir(parents=True, exist_ok=True)
                    ts = _time.strftime("%Y%m%d_%H%M%S")
                    photo_name = f"{card_id.replace('/', '_')}_{ts}.jpg"
                    photo_path = train_dir / photo_name
                    try:
                        raw = photo_data.split(",", 1)[1] if "," in photo_data else photo_data
                        with open(photo_path, "wb") as f:
                            f.write(b64mod.b64decode(raw))
                        saved_photo_path = str(photo_path)
                        log.info(f"Training photo saved: {photo_path}",
                                 extra={"event": "photo_saved", "card_id": card_id})
                    except Exception as e:
                        log.warning(f"Failed to save photo: {e}")

                return jsonify({
                    "confirmed": True,
                    "card_id": card_id,
                    "added_to_collection": add_to_collection,
                    "is_foil": is_foil,
                    "photo_saved": bool(saved_photo_path),
                    "learned": original_card_id != card_id,
                })
            else:
                # Rejection — delete scan photo, mark as rejected
                conn.execute(
                    "UPDATE scans SET error_status='rejected' WHERE scan_id=?",
                    (scan_id,),
                )
                conn.commit()
                # Delete the scan image file if it exists
                cur = conn.execute("SELECT image_path FROM scans WHERE scan_id=?", (scan_id,))
                row = cur.fetchone()
                if row and row["image_path"]:
                    try:
                        Path(row["image_path"]).unlink(missing_ok=True)
                    except Exception:
                        pass
                return jsonify({"confirmed": False, "card_id": None, "photo_deleted": True})
        finally:
            conn.close()

    # -- Delete collection entry (for false recognition) -------------------
    @app.route("/api/collection/delete", methods=["POST"])
    def api_collection_delete():
        body = request.get_json(silent=True) or {}
        collection_id = body.get("collection_id")
        reason = body.get("reason", "manual")  # "false_recognition" or "manual"
        scan_id = body.get("scan_id")  # optional: which scan was the false recognition

        if collection_id is None:
            return jsonify({"error": "collection_id required"}), 400

        conn = connect()
        try:
            # Get the entry before deleting
            cur = conn.execute("SELECT * FROM collection WHERE collection_id=?", (collection_id,))
            entry = cur.fetchone()
            if not entry:
                return jsonify({"error": "entry not found"}), 404

            card_id = entry["card_id"]

            # If reason is false_recognition, only delete the MOST RECENT training photo
            if reason == "false_recognition":
                images_dir = app.config["_SWU_CFG"].path("images_dir")
                train_dir = images_dir.parent / "scans" / "confirmed"
                if train_dir.exists():
                    pattern = f"{card_id.replace('/', '_')}_*.jpg"
                    matching = sorted(train_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
                    if matching:
                        try:
                            matching[0].unlink()
                            log.info(f"Deleted most recent training photo: {matching[0]}", extra={"event": "photo_deleted", "card_id": card_id})
                        except Exception:
                            pass

            # Delete the collection entry
            conn.execute("DELETE FROM collection WHERE collection_id=?", (collection_id,))
            conn.commit()
            return jsonify({"deleted": True, "card_id": card_id, "reason": reason})
        finally:
            conn.close()

    # -- Collection --------------------------------------------------------
    @app.route("/api/collection")
    def api_collection():
        conn = connect()
        try:
            # Query parameters for filtering
            search = request.args.get("search", "").strip()
            set_id = request.args.get("set", "").strip()
            rarity = request.args.get("rarity", "").strip()
            card_type = request.args.get("type", "").strip()
            aspect = request.args.get("aspect", "").strip()
            trait = request.args.get("trait", "").strip()
            min_cost = request.args.get("min_cost", "").strip()
            max_cost = request.args.get("max_cost", "").strip()
            min_power = request.args.get("min_power", "").strip()
            min_hp = request.args.get("min_hp", "").strip()
            sort_by = request.args.get("sort", "name").strip()
            sort_dir = request.args.get("dir", "asc").strip().lower()
            limit = request.args.get("limit", "500").strip()

            query = """
                SELECT col.*, cards.name, cards.subtitle, cards.set_id,
                       cards.card_number, cards.rarity, cards.type,
                       cards.aspects, cards.traits, cards.cost, cards.power,
                       cards.hp, cards.front_art_path
                FROM collection col
                JOIN cards ON col.card_id = cards.card_id
                WHERE 1=1
            """
            params: list[Any] = []
            if search:
                query += " AND (cards.name LIKE ? OR cards.subtitle LIKE ? OR col.card_id LIKE ?)"
                like = f"%{search}%"
                params.extend([like, like, like])
            if set_id:
                query += " AND cards.set_id = ?"
                params.append(set_id)
            if rarity:
                query += " AND cards.rarity = ?"
                params.append(rarity)
            if card_type:
                query += " AND cards.type = ?"
                params.append(card_type)
            if aspect:
                query += " AND cards.aspects LIKE ?"
                params.append(f'%"{aspect}"%')
            if trait:
                query += " AND cards.traits LIKE ?"
                params.append(f'%"{trait}"%')
            if min_cost:
                try:
                    query += " AND CAST(cards.cost AS INTEGER) >= ?"
                    params.append(int(min_cost))
                except ValueError:
                    pass
            if max_cost:
                try:
                    query += " AND CAST(cards.cost AS INTEGER) <= ?"
                    params.append(int(max_cost))
                except ValueError:
                    pass
            if min_power:
                try:
                    query += " AND CAST(cards.power AS INTEGER) >= ?"
                    params.append(int(min_power))
                except ValueError:
                    pass
            if min_hp:
                try:
                    query += " AND CAST(cards.hp AS INTEGER) >= ?"
                    params.append(int(min_hp))
                except ValueError:
                    pass

            # Sorting
            allowed_sort = {
                "name": "cards.name",
                "set": "cards.set_id",
                "number": "cards.card_number",
                "rarity": "cards.rarity",
                "cost": "cards.cost",
                "power": "cards.power",
                "hp": "cards.hp",
                "count": "col.count",
                "acquired": "col.acquired_at",
            }
            sort_col = allowed_sort.get(sort_by, "cards.name")
            direction = "DESC" if sort_dir == "desc" else "ASC"
            query += f" ORDER BY {sort_col} {direction}"

            try:
                limit_int = int(limit)
            except ValueError:
                limit_int = 500
            query += " LIMIT ?"
            params.append(limit_int)

            cur = conn.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            items = [_collection_to_dict(r) for r in rows]
            return jsonify({"items": items, "count": len(items)})
        finally:
            conn.close()

    # -- Collection stats --------------------------------------------------
    @app.route("/api/collection/stats")
    def api_collection_stats():
        conn = connect()
        try:
            total = repo.get_collection_count(conn)
            unique = repo.get_unique_collection_count(conn)
            cur = conn.execute(
                """SELECT cards.rarity, COUNT(*) as c
                   FROM collection col JOIN cards ON col.card_id = cards.card_id
                   WHERE cards.rarity IS NOT NULL AND cards.rarity != ''
                   GROUP BY cards.rarity ORDER BY c DESC"""
            )
            by_rarity = [{"rarity": r["rarity"], "count": r["c"]} for r in cur.fetchall()]
            cur = conn.execute(
                """SELECT cards.set_id, COUNT(*) as c
                   FROM collection col JOIN cards ON col.card_id = cards.card_id
                   GROUP BY cards.set_id ORDER BY c DESC LIMIT 10"""
            )
            by_set = [{"set_id": r["set_id"], "count": r["c"]} for r in cur.fetchall()]
            return jsonify({"total": total, "unique": unique, "by_rarity": by_rarity, "by_set": by_set})
        finally:
            conn.close()

    # -- Sets --------------------------------------------------------------
    @app.route("/api/sets")
    def api_sets():
        conn = connect()
        try:
            sets = repo.get_all_sets(conn)
            # Count cards per set
            result = []
            for s in sets:
                sid = s["set_id"]
                cur = conn.execute("SELECT COUNT(*) FROM cards WHERE set_id = ?", (sid,))
                card_count = cur.fetchone()[0]
                result.append({
                    "set_id": sid,
                    "full_name": s.get("full_name", ""),
                    "parent_set_id": s.get("parent_set_id", ""),
                    "number_cards": s.get("number_cards", 0),
                    "max_element": s.get("max_element", ""),
                    "is_base_set": bool(s.get("is_base_set", 0)),
                    "release_date": s.get("release_date", ""),
                    "imported": bool(s.get("imported", 0)),
                    "imported_at": s.get("imported_at", ""),
                    "card_count_actual": s.get("card_count_actual", 0),
                    "cards_in_db": card_count,
                })
            return jsonify({"sets": result, "count": len(result)})
        finally:
            conn.close()

    @app.route("/api/sets/import", methods=["POST"])
    def api_sets_import():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        body = request.get_json(silent=True) or {}
        set_id = body.get("set_id", "").strip()
        download_images = body.get("download_images", True)
        if not set_id:
            return jsonify({"error": "set_id required"}), 400

        mgr = _get_update_manager(app)
        if not mgr:
            return jsonify({"error": "Update manager unavailable"}), 503

        try:
            result = mgr.import_set(set_id, download_images=download_images)
            return jsonify(result)
        except Exception as e:
            log.error(f"Set import failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sets/sync", methods=["POST"])
    def api_sets_sync():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        mgr = _get_update_manager(app)
        if not mgr:
            return jsonify({"error": "Update manager unavailable"}), 503
        try:
            result = mgr.sync_sets()
            return jsonify(result)
        except Exception as e:
            log.error(f"Set sync failed: {e}")
            return jsonify({"error": str(e)}), 500

    # -- Cards (for filter dropdowns) --------------------------------------
    @app.route("/api/cards/filters")
    def api_cards_filters():
        conn = connect()
        try:
            cur = conn.execute("SELECT DISTINCT rarity FROM cards WHERE rarity IS NOT NULL AND rarity != '' ORDER BY rarity")
            rarities = [r["rarity"] for r in cur.fetchall()]
            cur = conn.execute("SELECT DISTINCT type FROM cards WHERE type IS NOT NULL AND type != '' ORDER BY type")
            types = [r["type"] for r in cur.fetchall()]
            cur = conn.execute("SELECT DISTINCT set_id FROM cards ORDER BY set_id")
            sets = [r["set_id"] for r in cur.fetchall()]
            # Aspects and traits are JSON arrays; extract unique values
            aspects_set: set[str] = set()
            traits_set: set[str] = set()
            cur = conn.execute("SELECT aspects, traits FROM cards")
            for row in cur.fetchall():
                for a in _parse_json_list(row["aspects"]):
                    if a:
                        aspects_set.add(a)
                for t in _parse_json_list(row["traits"]):
                    if t:
                        traits_set.add(t)
            return jsonify({
                "rarities": sorted(rarities),
                "types": sorted(types),
                "sets": sets,
                "aspects": sorted(aspects_set),
                "traits": sorted(traits_set),
            })
        finally:
            conn.close()

    # -- Card image --------------------------------------------------------
    @app.route("/api/card/image/<card_id>")
    def api_card_image(card_id: str):
        conn = connect()
        try:
            card = repo.get_card(conn, card_id)
            if not card:
                return jsonify({"error": "card not found"}), 404
            # Prefer local path, fall back to URL
            front_path = card.get("front_art_path", "")
            if front_path and Path(front_path).exists():
                return send_file(front_path, mimetype="image/png")
            # No local image — redirect to URL
            url = card.get("front_art_url", "")
            if url:
                from flask import redirect
                return redirect(url)
            return jsonify({"error": "no image available"}), 404
        finally:
            conn.close()

    # -- System ------------------------------------------------------------
    @app.route("/api/system")
    def api_system():
        cfg = app.config["_SWU_CFG"]
        conn = connect()
        try:
            db_path = cfg.path("database")
            db_size = db_path.stat().st_size if db_path.exists() else 0
            scans_today = repo.get_scan_count_today(conn)
            card_count = repo.get_card_count(conn)
            collection_count = repo.get_collection_count(conn)
            recent_scans = repo.get_recent_scans(conn, limit=10)
            return jsonify({
                "app_name": cfg.get("app.name", "SWU Card Manager"),
                "app_version": cfg.get("app.version", "1.0.0"),
                "python_version": sys.version.split()[0],
                "db_path": str(db_path),
                "db_size_bytes": db_size,
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "cards_in_db": card_count,
                "collection_count": collection_count,
                "scans_today": scans_today,
                "recent_scans": [
                    {
                        "scan_id": s.get("scan_id"),
                        "timestamp": s.get("timestamp", ""),
                        "card_name": s.get("name", ""),
                        "confidence": s.get("confidence", 0),
                        "method": s.get("method", ""),
                        "manual_correction": bool(s.get("manual_correction", 0)),
                    }
                    for s in recent_scans
                ],
                "config": {
                    "auto_accept_threshold": cfg.get("recognition.auto_accept_threshold", 0.85),
                    "candidate_threshold": cfg.get("recognition.candidate_threshold", 0.50),
                    "ocr_language": cfg.get("ocr.language", "eng"),
                    "capture_interval_ms": cfg.get("camera.capture_interval_ms", 2000),
                },
            })
        finally:
            conn.close()

    @app.route("/api/system/backup", methods=["POST"])
    def api_system_backup():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        try:
            from app.core.backup import BackupManager

            bm = BackupManager(app.config["_SWU_CFG"])
            path = bm.create_backup()
            return jsonify({"ok": True, "backup": str(path), "name": path.name})
        except Exception as e:
            log.error(f"Backup failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system/backups")
    def api_system_backups():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        try:
            from app.core.backup import BackupManager

            bm = BackupManager(app.config["_SWU_CFG"])
            backups = bm.list_backups()
            return jsonify({"backups": backups})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system/doctor", methods=["POST"])
    def api_system_doctor():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        try:
            from app.core.doctor import Doctor

            doctor = Doctor(app.config["_SWU_CFG"])
            result = doctor.run_all()
            return jsonify(result)
        except Exception as e:
            log.error(f"Doctor failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system/logs")
    def api_system_logs():
        if not _is_admin():
            return jsonify({"error": "Admin-Rechte erforderlich"}), 403
        try:
            logs_dir = app.config["_SWU_CFG"].path("logs_dir")
            lines: list[str] = []
            for log_file in sorted(logs_dir.glob("*.log"), reverse=True):
                if log_file.stat().st_size > 200_000:
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        # Read last 200 lines
                        content = f.readlines()[-200:]
                        lines.extend(content)
                else:
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        lines.extend(f.readlines())
                if len(lines) > 500:
                    lines = lines[-500:]
                    break
            return jsonify({"logs": "".join(lines[-500:])})
        except Exception as e:
            return jsonify({"error": str(e), "logs": ""})

    # -- SSE for live updates (dashboard refresh) -------------------------
    @app.route("/api/events")
    def api_events():
        def stream():
            yield "event: connected\ndata: {}\n\n"
            while True:
                conn = connect()
                try:
                    data = {
                        "scans_today": repo.get_scan_count_today(conn),
                        "collection_total": repo.get_collection_count(conn),
                        "timestamp": time.time(),
                    }
                finally:
                    conn.close()
                yield f"event: stats\ndata: {json.dumps(data)}\n\n"
                time.sleep(5)

        return Response(stream(), mimetype="text/event-stream")

    # -- Settings -----------------------------------------------------------
    _DEFAULT_SETTINGS = {
        "scan_interval_ms": "800", "capture_resolution": "1200", "jpeg_quality": "90",
        "auto_scan": "true", "camera_facing": "environment", "freeze_camera_during_scan": "true",
        "stabilization_delay_ms": "1000",
        "auto_accept_threshold": "0.70", "candidate_threshold": "0.25", "max_candidates": "5",
        "show_confidence_bar": "true", "show_processing_time": "false",
        "haptic_on_scan": "true", "haptic_on_confirm": "true", "haptic_on_reject": "true",
        "haptic_pattern_confirm": "50,30,80",
        "card_image_size": "40", "theme": "dark", "language": "de",
        "save_photos_on_confirm": "true", "auto_retrain_threshold": "50",
        "admin_mode": "true",
    }

    def _is_admin() -> bool:
        """Check whether admin mode is enabled in settings."""
        conn = connect()
        try:
            cur = conn.execute("SELECT value FROM settings WHERE key='admin_mode'")
            row = cur.fetchone()
            if row is None:
                return True  # default: admin on
            return row["value"] == "true"
        finally:
            conn.close()

    @app.route("/api/admin/status")
    def api_admin_status():
        return jsonify({"admin": _is_admin()})

    @app.route("/api/settings")
    def api_get_settings():
        conn = connect()
        try:
            settings = dict(_DEFAULT_SETTINGS)
            cur = conn.execute("SELECT key, value FROM settings")
            for row in cur.fetchall():
                settings[row["key"]] = row["value"]
            return jsonify(settings)
        finally:
            conn.close()

    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        body = request.get_json(silent=True) or {}
        conn = connect()
        try:
            for key, value in body.items():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                    (key, str(value)),
                )
            conn.commit()
            return jsonify({"saved": True, "count": len(body)})
        finally:
            conn.close()

    @app.route("/api/settings/reset", methods=["POST"])
    def api_reset_settings():
        conn = connect()
        try:
            conn.execute("DELETE FROM settings")
            conn.commit()
            return jsonify({"reset": True})
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SWU Card Manager Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS (camera won't work on iOS)")
    args = parser.parse_args()

    app = create_app()

    # Determine SSL cert paths
    project_root = Path(__file__).resolve().parent.parent.parent
    cert_path = project_root / "ssl" / "cert.pem"
    key_path = project_root / "ssl" / "key.pem"

    use_ssl = not args.no_ssl and cert_path.exists() and key_path.exists()

    protocol = "https" if use_ssl else "http"
    print(f"\n{'=' * 60}")
    print(f"  SWU Card Manager — Web UI")
    print(f"  Listening on {protocol}://{args.host}:{args.port}")
    if use_ssl:
        print(f"  HTTPS enabled — camera works on iOS/Safari")
        print(f"  ⚠  On iPhone: accept the certificate warning to continue")
    else:
        print(f"  ⚠  HTTP mode — camera won't work on iOS (use --ssl or localhost)")
    print(f"  Phone: {protocol}://192.168.178.74:{args.port}")
    print(f"  Local: {protocol}://localhost:{args.port}")
    print(f"{'=' * 60}\n")

    if use_ssl:
        # gevent pywsgi: production WSGI server with native TLS support.
        # (monkey-patching already happened in __main__.py before all imports)
        from gevent.pywsgi import WSGIServer

        import ssl as _ssl

        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        _ctx.load_cert_chain(str(cert_path), str(key_path))
        server = WSGIServer((args.host, args.port), app, ssl_context=_ctx)
        print(f"  Serving via gevent/pywsgi (HTTPS, production WSGI)")
        server.serve_forever()
    else:
        from gevent.pywsgi import WSGIServer

        print(f"  Serving via gevent/pywsgi (HTTP, production WSGI)")
        server = WSGIServer((args.host, args.port), app)
        server.serve_forever()
    print("Server beendet.")


if __name__ == "__main__":
    main()
