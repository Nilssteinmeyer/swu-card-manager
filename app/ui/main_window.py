"""
Main GUI application — Star Wars Unlimited Card Manager.
Built with CustomTkinter for a modern, dark-themed desktop UI.

Tabs:
  Dashboard  — overview statistics
  Scanner    — camera capture + recognition
  Collection — browse, search, filter owned cards
  Sets       — set management and import status
  System     — logs, backups, model info, diagnostics
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from app.core.config import AppConfig
from app.core.logging import get_logger, AppLogger
from app.db import repository as repo
from app.db.schema import connect
from app.ml.dataset import DatasetManager
from app.ml.training import ModelRegistry
from app.providers.update_manager import UpdateManager
from app.recognition.engine import RecognitionEngine
from app.vision.camera import CameraManager
from app.vision.detection import preprocess_card_image

log = get_logger("ui")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class SWUApp(ctk.CTk):
    """Main application window."""

    def __init__(self, config: AppConfig | None = None):
        super().__init__()
        self.cfg = config or AppConfig.load()
        AppLogger.setup(self.cfg)

        self.title("Star Wars Unlimited Card Manager")
        w = self.cfg.get("gui.window_width", 1400)
        h = self.cfg.get("gui.window_height", 900)
        self.geometry(f"{w}x{h}")
        self.minsize(1000, 600)

        # Core components
        self.camera = CameraManager(self.cfg)
        self.recognition = RecognitionEngine(self.cfg)
        self.update_mgr = UpdateManager(self.cfg)
        self.dataset = DatasetManager(self.cfg)

        # State
        self._scan_running = False
        self._camera_active = False

        self._build_ui()
        self._refresh_dashboard()

    def _build_ui(self) -> None:
        """Build the main UI with sidebar navigation."""
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.pack(side="left", fill="y", padx=0, pady=0)
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(self.sidebar, text="SWU Manager", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=20)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for tab_name in ["Dashboard", "Scanner", "Sammlung", "Sets", "System"]:
            btn = ctk.CTkButton(
                self.sidebar, text=tab_name,
                command=lambda t=tab_name: self._show_tab(t),
                fg_color="transparent",
                anchor="w",
                height=40,
            )
            btn.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[tab_name] = btn

        # Status bar at bottom of sidebar
        self.status_label = ctk.CTkLabel(self.sidebar, text="● ONLINE", text_color="#2dd4bf", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="bottom", pady=10)

        # Main content area
        self.content = ctk.CTkFrame(self, corner_radius=0)
        self.content.pack(side="right", fill="both", expand=True, padx=0, pady=0)

        # Create all tab frames
        self.tabs: dict[str, ctk.CTkFrame] = {
            "Dashboard": self._build_dashboard_tab(),
            "Scanner": self._build_scanner_tab(),
            "Sammlung": self._build_collection_tab(),
            "Sets": self._build_sets_tab(),
            "System": self._build_system_tab(),
        }

        self._show_tab("Dashboard")

    def _show_tab(self, name: str) -> None:
        for tname, frame in self.tabs.items():
            if tname == name:
                frame.pack(fill="both", expand=True)
                btn = self.nav_buttons.get(tname)
                if btn:
                    btn.configure(fg_color=("gray75", "gray25"))
            else:
                frame.pack_forget()
                btn = self.nav_buttons.get(tname)
                if btn:
                    btn.configure(fg_color="transparent")

        if name == "Dashboard":
            self._refresh_dashboard()
        elif name == "Sammlung":
            self._refresh_collection()
        elif name == "Sets":
            self._refresh_sets()
        elif name == "System":
            self._refresh_system()

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------
    def _build_dashboard_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content)
        ctk.CTkLabel(frame, text="Dashboard", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)

        # Stats grid
        stats_frame = ctk.CTkFrame(frame)
        stats_frame.pack(fill="x", padx=20, pady=10)

        self.dash_labels: dict[str, ctk.CTkLabel] = {}
        stats = [
            ("Karten in DB", "card_count"),
            ("Verschiedene Karten", "unique_cards"),
            ("Sets", "set_count"),
            ("Scans heute", "scans_today"),
            ("Sammlung gesamt", "collection_total"),
            ("Erkennungsrate", "recognition_rate"),
        ]
        for i, (label, key) in enumerate(stats):
            row, col = divmod(i, 3)
            card = ctk.CTkFrame(stats_frame)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="ew")
            stats_frame.grid_columnconfigure(col, weight=1)
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=14)).pack(pady=(10, 0))
            self.dash_labels[key] = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=28, weight="bold"))
            self.dash_labels[key].pack(pady=(0, 10))

        # Recent scans
        ctk.CTkLabel(frame, text="Letzte Scans", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 5))
        self.recent_scans_text = ctk.CTkTextbox(frame, height=200)
        self.recent_scans_text.pack(fill="x", padx=20, pady=5)

        # Refresh button
        ctk.CTkButton(frame, text="Aktualisieren", command=self._refresh_dashboard).pack(pady=10)

        return frame

    def _refresh_dashboard(self) -> None:
        try:
            conn = connect()
            card_count = repo.get_card_count(conn)
            set_count = len(repo.get_all_sets(conn))
            scans_today = repo.get_scan_count_today(conn)
            collection_total = repo.get_collection_count(conn)
            unique_collection = repo.get_unique_collection_count(conn)
            rec_rate = repo.get_recognition_rate(conn)
            recent = repo.get_recent_scans(conn, limit=10)
            conn.close()

            self.dash_labels["card_count"].configure(text=str(card_count))
            self.dash_labels["unique_cards"].configure(text=str(unique_collection))
            self.dash_labels["set_count"].configure(text=str(set_count))
            self.dash_labels["scans_today"].configure(text=str(scans_today))
            self.dash_labels["collection_total"].configure(text=str(collection_total))
            self.dash_labels["recognition_rate"].configure(text=f"{rec_rate:.0%}")

            self.recent_scans_text.delete("1.0", "end")
            if recent:
                for s in recent:
                    name = s.get("name", "Unknown")
                    conf = s.get("confidence", 0)
                    ts = s.get("timestamp", "")
                    self.recent_scans_text.insert("end", f"  {ts}  |  {name}  |  {conf:.0%}\n")
            else:
                self.recent_scans_text.insert("end", "  Noch keine Scans vorhanden.\n")
        except Exception as e:
            log.error(f"Dashboard refresh failed: {e}")

    # -----------------------------------------------------------------------
    # Scanner
    # -----------------------------------------------------------------------
    def _build_scanner_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content)
        ctk.CTkLabel(frame, text="Scanner", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)

        # Camera view
        self.camera_label = ctk.CTkLabel(frame, text="Kamera nicht aktiv", width=640, height=480)
        self.camera_label.pack(pady=10)

        # Controls
        controls = ctk.CTkFrame(frame)
        controls.pack(fill="x", padx=20, pady=5)

        self.btn_camera = ctk.CTkButton(controls, text="Kamera starten", command=self._toggle_camera)
        self.btn_camera.pack(side="left", padx=5, pady=10)

        self.btn_scan = ctk.CTkButton(controls, text="Scan", command=self._do_scan,
                                       state="disabled")
        self.btn_scan.pack(side="left", padx=5, pady=10)

        # Recognition result
        result_frame = ctk.CTkFrame(frame)
        result_frame.pack(fill="x", padx=20, pady=10)

        self.scan_result_label = ctk.CTkLabel(result_frame, text="Erkannte Karte: —",
                                               font=ctk.CTkFont(size=18, weight="bold"))
        self.scan_result_label.pack(pady=5)

        self.scan_confidence_label = ctk.CTkLabel(result_frame, text="Konfidenz: —")
        self.scan_confidence_label.pack(pady=2)

        self.scan_candidates_text = ctk.CTkTextbox(result_frame, height=100)
        self.scan_candidates_text.pack(fill="x", padx=10, pady=5)

        # Add to collection
        add_frame = ctk.CTkFrame(frame)
        add_frame.pack(fill="x", padx=20, pady=5)

        self.btn_add = ctk.CTkButton(add_frame, text="Zur Sammlung hinzufügen",
                                      command=self._add_to_collection, state="disabled")
        self.btn_add.pack(side="left", padx=5, pady=10)

        self.last_recognized_card_id: str | None = None

        return frame

    def _toggle_camera(self) -> None:
        if self._camera_active:
            self._stop_camera()
        else:
            self._start_camera()

    def _start_camera(self) -> None:
        if not self.camera.open():
            self.scan_result_label.configure(text="Fehler: Kamera nicht verfügbar")
            return
        self._camera_active = True
        self.btn_camera.configure(text="Kamera stoppen")
        self.btn_scan.configure(state="normal")
        self._camera_loop()

    def _stop_camera(self) -> None:
        self._camera_active = False
        self.camera.close()
        self.btn_camera.configure(text="Kamera starten")
        self.btn_scan.configure(state="disabled")
        self.camera_label.configure(image=None, text="Kamera gestoppt")

    def _camera_loop(self) -> None:
        if not self._camera_active:
            return
        frame = self.camera.capture()
        if frame is not None:
            # Display the frame
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            # Resize for display
            max_w, max_h = 640, 480
            ratio = min(max_w / img.width, max_h / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
            photo = ImageTk.PhotoImage(img)
            self.camera_label.configure(image=photo, text="")
            self.camera_label.image = photo  # keep reference
        self.after(100, self._camera_loop)

    def _do_scan(self) -> None:
        """Capture and recognise a card."""
        frame = self.camera.capture_stable()
        if frame is None:
            self.scan_result_label.configure(text="Fehler: Bildaufnahme fehlgeschlagen")
            return

        self.btn_scan.configure(state="disabled")
        self.scan_result_label.configure(text="Erkenne Karte...")

        # Run recognition in background thread
        def run():
            try:
                # Save the scan image
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                scan_dir = self.cfg.path("images_dir") / "scans"
                scan_dir.mkdir(parents=True, exist_ok=True)
                img_path = scan_dir / f"scan_{ts}.png"
                cv2.imwrite(str(img_path), frame)

                conn = connect()
                result = self.recognition.recognize(frame, conn=conn, save_path=str(img_path))

                # Record scan in DB
                scan_id = repo.record_scan(
                    conn, str(img_path), result.card_id, result.confidence,
                    result.method, result.ocr_text, "", result.processing_time_ms,
                )
                if result.candidates:
                    repo.record_scan_candidates(conn, scan_id, result.candidates)

                conn.close()

                # Update UI on main thread
                self.after(0, lambda: self._display_scan_result(result))
            except Exception as e:
                log.error(f"Scan failed: {e}")
                self.after(0, lambda: self.scan_result_label.configure(text=f"Fehler: {e}"))
            finally:
                self.after(0, lambda: self.btn_scan.configure(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _display_scan_result(self, result) -> None:
        self.last_recognized_card_id = result.card_id
        if result.card_id:
            self.scan_result_label.configure(text=f"Erkannte Karte: {result.card_name}")
            color = "#2dd4bf" if result.confidence >= 0.85 else "#fbbf24"
            self.scan_confidence_label.configure(
                text=f"Konfidenz: {result.confidence:.1%}", text_color=color
            )
            self.btn_add.configure(state="normal")
        else:
            self.scan_result_label.configure(text=f"Beste Kandidatin: {result.card_name}")
            self.scan_confidence_label.configure(
                text=f"Konfidenz: {result.confidence:.1%} (bitte bestätigen)",
                text_color="#fbbf24",
            )
            if result.candidates:
                self.btn_add.configure(state="normal")
            else:
                self.btn_add.configure(state="disabled")

        self.scan_candidates_text.delete("1.0", "end")
        for c in result.candidates[:5]:
            self.scan_candidates_text.insert(
                "end",
                f"  {c['name']} ({c['set_id']}-{c['card_number']})  Score: {c['score']:.2f}\n"
            )

    def _add_to_collection(self) -> None:
        if not self.last_recognized_card_id:
            return
        conn = connect()
        repo.add_to_collection(conn, self.last_recognized_card_id)
        conn.close()
        self.scan_result_label.configure(text="✓ Zur Sammlung hinzugefügt!")
        self.btn_add.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Collection
    # -----------------------------------------------------------------------
    def _build_collection_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content)
        ctk.CTkLabel(frame, text="Sammlung", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)

        # Search bar
        search_frame = ctk.CTkFrame(frame)
        search_frame.pack(fill="x", padx=20, pady=5)

        self.search_entry = ctk.CTkEntry(search_frame, placeholder_text="Suche nach Name...", width=300)
        self.search_entry.pack(side="left", padx=5, pady=10)
        self.search_entry.bind("<KeyRelease>", lambda e: self._refresh_collection())

        ctk.CTkButton(search_frame, text="Suchen", command=self._refresh_collection).pack(side="left", padx=5)

        # Collection list
        self.collection_text = ctk.CTkTextbox(frame)
        self.collection_text.pack(fill="both", expand=True, padx=20, pady=10)

        return frame

    def _refresh_collection(self) -> None:
        query = self.search_entry.get().strip().lower() if hasattr(self, "search_entry") else ""
        conn = connect()
        items = repo.get_collection(conn)
        conn.close()

        if query:
            items = [i for i in items if query in (i.get("name", "") + " " + i.get("subtitle", "")).lower()]

        self.collection_text.delete("1.0", "end")
        if items:
            self.collection_text.insert("end", f"  {'Name':<40} {'Set':<8} {'Nr':<6} {'Anz':<5} {'Zustand':<8} {'Sprache':<8}\n")
            self.collection_text.insert("end", "  " + "-" * 80 + "\n")
            for item in items:
                name = f"{item.get('name', '')} {item.get('subtitle', '')}".strip()
                self.collection_text.insert(
                    "end",
                    f"  {name:<40} {item.get('set_id',''):<8} {str(item.get('card_number','')):<6} "
                    f"{item.get('count',1):<5} {item.get('condition','NM'):<8} {item.get('language','en'):<8}\n"
                )
        else:
            self.collection_text.insert("end", "  Sammlung ist leer. Scanne Karten um sie hinzuzufügen.\n")

    # -----------------------------------------------------------------------
    # Sets
    # -----------------------------------------------------------------------
    def _build_sets_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content)
        ctk.CTkLabel(frame, text="Sets", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)

        # Controls
        controls = ctk.CTkFrame(frame)
        controls.pack(fill="x", padx=20, pady=5)
        ctk.CTkButton(controls, text="Sets synchronisieren", command=self._sync_sets).pack(side="left", padx=5, pady=10)
        ctk.CTkButton(controls, text="Fehlende Sets importieren", command=self._import_missing_sets).pack(side="left", padx=5)

        self.sets_text = ctk.CTkTextbox(frame)
        self.sets_text.pack(fill="both", expand=True, padx=20, pady=10)

        return frame

    def _refresh_sets(self) -> None:
        conn = connect()
        sets = repo.get_all_sets(conn)
        conn.close()

        self.sets_text.delete("1.0", "end")
        if sets:
            self.sets_text.insert("end", f"  {'Set':<10} {'Name':<45} {'Karten':<8} {'Importiert':<12}\n")
            self.sets_text.insert("end", "  " + "-" * 80 + "\n")
            for s in sets:
                imported = "✓" if s.get("imported") else "—"
                actual = s.get("card_count_actual", 0)
                self.sets_text.insert(
                    "end",
                    f"  {s['set_id']:<10} {s['full_name']:<45} {str(actual):<8} {imported:<12}\n"
                )
        else:
            self.sets_text.insert("end", "  Keine Sets in der Datenbank. Klicke 'Sets synchronisieren' um zu starten.\n")

    def _sync_sets(self) -> None:
        def run():
            try:
                self.update_mgr.sync_sets()
                self.after(0, self._refresh_sets)
            except Exception as e:
                log.error(f"Set sync failed: {e}")
                self.after(0, lambda: self.sets_text.insert("end", f"\n  Fehler: {e}\n"))
        threading.Thread(target=run, daemon=True).start()

    def _import_missing_sets(self) -> None:
        def run():
            try:
                results = self.update_mgr.import_all_missing_sets(download_images=True)
                # Compute phashes for new images
                conn = connect()
                self.recognition.compute_reference_phashes(conn)
                conn.close()
                self.after(0, self._refresh_sets)
                self.after(0, self._refresh_dashboard)
            except Exception as e:
                log.error(f"Import failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    # -----------------------------------------------------------------------
    # System
    # -----------------------------------------------------------------------
    def _build_system_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content)
        ctk.CTkLabel(frame, text="System", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20)

        # System status
        self.system_status_text = ctk.CTkTextbox(frame, height=250)
        self.system_status_text.pack(fill="x", padx=20, pady=10)

        # Controls
        controls = ctk.CTkFrame(frame)
        controls.pack(fill="x", padx=20, pady=5)

        ctk.CTkButton(controls, text="Diagnose ausführen", command=self._run_doctor).pack(side="left", padx=5, pady=10)
        ctk.CTkButton(controls, text="Backup erstellen", command=self._create_backup).pack(side="left", padx=5)
        ctk.CTkButton(controls, text="Aktualisieren", command=self._refresh_system).pack(side="left", padx=5)

        # Logs
        ctk.CTkLabel(frame, text="Letzte Logs", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        self.logs_text = ctk.CTkTextbox(frame)
        self.logs_text.pack(fill="both", expand=True, padx=20, pady=10)

        return frame

    def _refresh_system(self) -> None:
        # System status
        conn = connect()
        active_model = ModelRegistry.get_active_model(conn)
        jobs = conn.execute("SELECT * FROM job_log ORDER BY started_at DESC LIMIT 5").fetchall()
        conn.close()

        self.system_status_text.delete("1.0", "end")
        self.system_status_text.insert("end", "  SYSTEM STATUS\n")
        self.system_status_text.insert("end", "  " + "=" * 50 + "\n")
        self.system_status_text.insert("end", f"  Database:          {'OK' if repo.get_card_count(connect()) >= 0 else 'ERROR'}\n")
        self.system_status_text.insert("end", f"  Cards in DB:       {repo.get_card_count(connect())}\n")
        ds_count = self.dataset.get_sample_count()
        self.system_status_text.insert("end", f"  Dataset samples:   {ds_count}\n")
        self.system_status_text.insert("end", f"  Active model:      {active_model['model_id'] if active_model else 'None (default)'}\n")
        self.system_status_text.insert("end", f"  Last maintenance:  {load_current_state().get('last_maintenance', 'Never')}\n")
        self.system_status_text.insert("end", f"  Backups:           {len(BackupManager(self.cfg).list_backups())}\n")

        # Logs
        log_path = self.cfg.path("logs_dir") / "swu_manager.log"
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                self.logs_text.delete("1.0", "end")
                for line in lines[-50:]:
                    self.logs_text.insert("end", line + "\n")
            except Exception:
                pass

    def _run_doctor(self) -> None:
        from app.core.doctor import Doctor
        def run():
            doctor = Doctor(self.cfg)
            result = doctor.run_all()
            summary = f"\n  Diagnose: {result['passed']}/{result['total']} checks passed\n"
            self.after(0, lambda: self.system_status_text.insert("end", summary))
        threading.Thread(target=run, daemon=True).start()

    def _create_backup(self) -> None:
        def run():
            from app.core.backup import BackupManager
            bm = BackupManager(self.cfg)
            path = bm.create_backup("manual")
            self.after(0, lambda: self.system_status_text.insert("end", f"\n  Backup erstellt: {path.name}\n"))
        threading.Thread(target=run, daemon=True).start()

    def on_closing(self):
        self._stop_camera()
        self.destroy()


def load_current_state():
    from app.core.state import load_current_state as _lcs
    return _lcs()

from app.core.backup import BackupManager  # noqa E402
