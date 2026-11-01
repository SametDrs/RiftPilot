"""RiftPilot — Desktop GUI Launcher (Masaüstü Arayüz Başlatıcı).

PySide6 masaüstü penceresini yerel ağ IP'si ve kriptografik eşleştirme motoruyla
birlikte ayağa kaldırır.
"""

from __future__ import annotations

import sys
from pathlib import Path

# src dizinini modül arama yoluna dahil et
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
for p in [str(ROOT_DIR), str(SRC_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication

import threading
import uvicorn

from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import MainWindow
from scripts.network_spike import app as fastapi_app, detect_lan_ip


def run_fastapi_server(host: str, port: int) -> None:
    """Arka planda yerel HTTP/WebSocket sunucusunu çalıştırır."""
    config = uvicorn.Config(fastapi_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    ott_manager = OTTManager()
    session_manager = SessionManager()

    # Aktif yerel ağ IP'si tespiti
    lan_ip = detect_lan_ip()
    port = 8765

    # 1. Arka plan FastAPI / Uvicorn sunucusunu daemon thread'de başlat
    server_thread = threading.Thread(
        target=run_fastapi_server,
        args=("0.0.0.0", port),
        daemon=True,
    )
    server_thread.start()

    # Başlangıçta örnek bir bağlı cihaz ekleyelim (arayüz deneyimi için)
    session_manager.create_session("iPhone 15 Pro (Örnek Cihaz)", "sample-device-001")

    # 2. PySide6 masaüstü penceresini ana iş parçacığında aç
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip=lan_ip,
        port=port,
    )
    window.show()

    print("\n" + "=" * 50)
    print(" 🎮 RiftPilot Masaüstü Arayüzü ve Web Sunucusu Başlatıldı!")
    print(f" Yerel Ağ Adresi: http://{lan_ip}:{port}/")
    print(" Telefon kamerasıyla QR kodu tarayabilirsin.")
    print(" Pencereyi kapatmak için arayüzden 'X' butonuna basabilirsin.")
    print("=" * 50 + "\n")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
