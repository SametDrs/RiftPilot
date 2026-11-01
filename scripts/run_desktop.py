"""RiftPilot — Desktop GUI Launcher (Masaüstü Arayüz Başlatıcı).

PySide6 masaüstü penceresini yerel ağ IP'si, kriptografik eşleştirme motoru ve
Riot LCU istemci adaptörüyle birlikte ayağa kaldırır.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import threading
from typing import Any

# src dizinini modül arama yoluna dahil et
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
for p in [str(ROOT_DIR), str(SRC_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication
import uvicorn

from riftpilot.adapters.lcu_discovery import LcuDiscoverer
from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.lcu_port import GameflowPhase, ILcuPort, LcuConnectionStatus
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import MainWindow
from scripts.network_spike import detect_lan_ip


def run_fastapi_server(app: Any, host: str, port: int) -> None:
    """Arka planda yerel HTTP/WebSocket sunucusunu çalıştırır."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="RiftPilot Desktop GUI & Web Hub")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Gerçek LoL istemcisi yerine MockLcuAdapter kullan (çevrimdışı test)",
    )
    args, _ = parser.parse_known_args()

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    ott_manager = OTTManager()
    session_manager = SessionManager()

    # LCU Adaptörü Yapılandırması
    lcu_adapter: ILcuPort
    if args.mock:
        print("🔧 Mod: Mock LCU Adaptörü aktif (Çevrimdışı test modu)")
        mock_adapter = MockLcuAdapter()
        lcu_adapter = mock_adapter
    else:
        print("🔍 Mod: Gerçek Riot LCU Adaptörü başlatılıyor...")
        lcu_adapter = RiotLcuAdapter()

    # Aktif yerel ağ IP'si tespiti
    lan_ip = detect_lan_ip()
    port = 8765

    # 1. PySide6 masaüstü penceresini oluştur (ana iş parçacığı)
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip=lan_ip,
        port=port,
        lcu_adapter=lcu_adapter,
    )

    # 2. FastAPI Web Hub uygulaması oluştur (UI bridge ve aktif izleyici ile)
    web_app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=lcu_adapter,
        ui_bridge=window.bridge,
        enable_monitor=True,
    )

    # 3. Arka plan FastAPI / Uvicorn sunucusunu daemon thread'de başlat
    server_thread = threading.Thread(
        target=run_fastapi_server,
        args=(web_app, "0.0.0.0", port),
        daemon=True,
    )
    server_thread.start()

    # 4. Pencereyi göster
    window.show()

    # Eğer mock moddaysa, test kolaylığı için 3 saniye sonra ReadyCheck simüle eden bir thread ekleyelim
    if args.mock and isinstance(lcu_adapter, MockLcuAdapter):
        def _schedule_mock_ready_check():
            import time
            time.sleep(3)
            print("⏱️ [Mock LCU] 3 saniye sonra otomatik maç bulundu (ReadyCheck) simüle ediliyor...")
            asyncio.run(lcu_adapter.simulate_connect())
            asyncio.run(lcu_adapter.simulate_ready_check(timer=15.0))

        threading.Thread(target=_schedule_mock_ready_check, daemon=True).start()

    print("\n" + "=" * 55)
    print(" 🎮 RiftPilot Masaüstü Arayüzü ve Web Hub Başlatıldı!")
    print(f" Yerel Ağ Adresi: http://{lan_ip}:{port}/")
    print(" Telefon kamerasıyla QR kodu tarayabilirsin.")
    if args.mock:
        print(" 🧪 Mock Modu: 3 saniye sonra telefona 'MAÇ BULUNDU!' düşecek.")
    print(" Pencereyi kapatmak için arayüzden 'X' butonuna basabilirsin.")
    print("=" * 55 + "\n")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
