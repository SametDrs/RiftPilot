"""RiftPilot — Milestone 1: LAN & Mobile Web Network Spike

Bu betik, telefon ile bilgisayar arasındaki yerel ağ (LAN) iletişimini,
mobil tarayıcıların HTTP/WebSocket davranışlarını ve QR kod ile eşleşme
kabiliyetini sahada doğrulamak amacıyla hazırlanmış bir teknik araştırma (Spike) testidir.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
from datetime import datetime
from typing import Any

import psutil
import qrcode
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# Windows terminal UTF-8 uyumluluğu (cp1254 Türkçe Windows karakter eşleme hatasını önler)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("RiftPilot.Spike")

app = FastAPI(title="RiftPilot Spike Server")


def detect_lan_ip() -> str:
    """Aktif yerel ağ IPv4 adresini güvenli bir şekilde tespit eder."""
    # 1. Yöntem: Dış rotaya yönelen soket (en güvenilir yöntem)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            candidate = s.getsockname()[0]
            if not candidate.startswith("127."):
                return candidate
    except Exception:
        pass

    # 2. Yöntem: psutil ile ağ arayüzlerini tara
    try:
        for interface_name, addresses in psutil.net_if_addrs().items():
            for addr in addresses:
                if (
                    addr.family == socket.AF_INET
                    and not addr.address.startswith("127.")
                    and not addr.address.startswith("169.254.")
                ):
                    return addr.address
    except Exception:
        pass

    # Yedek: localhost
    return "127.0.0.1"


# Mobil cihazın açacağı modern, hafif ve duyarlı (responsive) test arayüzü
MOBILE_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>RiftPilot — LAN Spike Testi</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: #0b0e14;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
    }
    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 24px;
      width: 100%;
      max-width: 420px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    }
    h1 {
      font-size: 1.4rem;
      font-weight: 700;
      color: #58a6ff;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    p.subtitle {
      font-size: 0.85rem;
      color: #8b949e;
      margin-bottom: 20px;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 0.85rem;
      font-weight: 600;
      margin-bottom: 16px;
    }
    .status-online { background: rgba(46, 160, 67, 0.15); color: #3fb950; border: 1px solid #2ea043; }
    .status-offline { background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid #f85149; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
    .info-box {
      background: #0d1117;
      border: 1px solid #21262d;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 16px;
      font-size: 0.8rem;
      line-height: 1.5;
    }
    .info-row { display: flex; justify-content: space-between; margin-bottom: 4px; }
    .info-label { color: #8b949e; }
    .info-val { color: #c9d1d9; font-family: monospace; }
    .btn {
      width: 100%;
      padding: 12px;
      margin-bottom: 10px;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      font-size: 0.95rem;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .btn-primary { background: #238636; color: white; }
    .btn-primary:active { background: #2ea043; transform: scale(0.98); }
    .btn-secondary { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
    .btn-secondary:active { background: #30363d; }
    .log-box {
      background: #05070a;
      border: 1px solid #21262d;
      border-radius: 8px;
      padding: 10px;
      height: 140px;
      overflow-y: auto;
      font-family: monospace;
      font-size: 0.75rem;
      color: #8b949e;
      margin-top: 12px;
      display: flex;
      flex-direction: column-reverse;
    }
    .log-item { margin-bottom: 4px; }
    .log-time { color: #58a6ff; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🎮 RiftPilot Spike</h1>
    <p class="subtitle">Yerel Ağ (LAN) Mobil İletişim Doğrulama Testi</p>

    <div id="statusBadge" class="status-badge status-offline">
      <span class="dot"></span>
      <span id="statusText">WebSocket Bağlanıyor...</span>
    </div>

    <div class="info-box">
      <div class="info-row"><span class="info-label">Sunucu IP:</span><span class="info-val" id="serverIp">-</span></div>
      <div class="info-row"><span class="info-label">Protokol:</span><span class="info-val" id="proto">-</span></div>
      <div class="info-row"><span class="info-label">Tarayıcı:</span><span class="info-val" id="browserInfo">-</span></div>
      <div class="info-row"><span class="info-label">Gecikme (RTT):</span><span class="info-val" id="pingVal">- ms</span></div>
    </div>

    <button id="pingBtn" class="btn btn-primary" onclick="sendPing()">⚡ Canlı Ping Gönder</button>
    <button class="btn btn-secondary" onclick="testHaptic()">📳 Titreşim (Haptic) Testi</button>

    <div class="log-box" id="logBox">
      <div class="log-item">Test paneli başlatıldı.</div>
    </div>
  </div>

  <script>
    const serverHost = window.location.host;
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProto}//${serverHost}/ws`;
    let ws = null;
    let pingStartTime = 0;

    document.getElementById('serverIp').textContent = window.location.hostname;
    document.getElementById('proto').textContent = window.location.protocol.toUpperCase();
    document.getElementById('browserInfo').textContent = navigator.userAgent.includes('iPhone') ? 'iOS Safari' : (navigator.userAgent.includes('Android') ? 'Android' : 'Tarayıcı');

    function log(msg) {
      const box = document.getElementById('logBox');
      const time = new Date().toLocaleTimeString();
      const div = document.createElement('div');
      div.className = 'log-item';
      div.innerHTML = `<span class="log-time">[${time}]</span> ${msg}`;
      box.prepend(div);
    }

    function connectWs() {
      log(`Bağlanıyor: ${wsUrl}`);
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        document.getElementById('statusBadge').className = 'status-badge status-online';
        document.getElementById('statusText').textContent = 'WebSocket Bağlandı (LAN)';
        log('✅ WebSocket el sıkışması başarılı!');
        sendPing();
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'PONG') {
            const rtt = Date.now() - pingStartTime;
            document.getElementById('pingVal').textContent = `${rtt} ms`;
            log(`⚡ Pong alındı! RTT: ${rtt}ms (Sunucu saati: ${data.time})`);
          } else {
            log(`📩 Sunucu: ${event.data}`);
          }
        } catch(e) {
          log(`📩 Metin: ${event.data}`);
        }
      };

      ws.onclose = () => {
        document.getElementById('statusBadge').className = 'status-badge status-offline';
        document.getElementById('statusText').textContent = 'Bağlantı Kesildi';
        log('❌ WebSocket kapandı. 3 sn içinde tekrar denenecek...');
        setTimeout(connectWs, 3000);
      };

      ws.onerror = (err) => {
        log('⚠️ WebSocket hatası oluştu.');
      };
    }

    function sendPing() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        pingStartTime = Date.now();
        ws.send(JSON.stringify({ type: 'PING', clientTime: pingStartTime, userAgent: navigator.userAgent }));
        log('📤 Ping paketi gönderildi...');
      } else {
        log('⚠️ WebSocket hazır değil.');
      }
    }

    function testHaptic() {
      if ('vibrate' in navigator) {
        navigator.vibrate([100, 50, 100]);
        log('📳 Titreşim tetiklendi (Destekleniyor).');
      } else {
        log('ℹ️ Bu mobil tarayıcıda titreşim API kısıtlı (HTTPS gerekebilir).');
      }
    }

    connectWs();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def get_index() -> HTMLResponse:
    """Telefona duyarlı test arayüzünü döner."""
    return HTMLResponse(content=MOBILE_HTML)


@app.get("/api/health")
async def get_health() -> dict[str, Any]:
    """Sunucu sağlık kontrolü."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Çift yönlü WebSocket test kanalı."""
    await websocket.accept()
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info("📱 Yeni mobil cihaz bağlandı: %s", client_host)

    try:
        await websocket.send_json(
            {
                "type": "WELCOME",
                "message": "RiftPilot LAN Hub ile bağlantı kuruldu.",
                "serverTime": datetime.now().strftime("%H:%M:%S"),
            }
        )
        while True:
            text_data = await websocket.receive_text()
            try:
                data = json.loads(text_data)
                if isinstance(data, dict) and data.get("type") == "PING":
                    await websocket.send_json(
                        {
                            "type": "PONG",
                            "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                            "clientReported": data.get("clientTime"),
                        }
                    )
                else:
                    await websocket.send_json({"type": "ECHO", "received": data})
            except json.JSONDecodeError:
                await websocket.send_text(f"ECHO: {text_data}")
    except (WebSocketDisconnect, ConnectionResetError):
        logger.info("📱 Mobil cihaz ayrıldı: %s", client_host)


def print_terminal_qr(url: str) -> None:
    """Terminale yüksek kontrastlı taranabilir QR kod basar."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    print("\n" + "=" * 50)
    print(" 🎮 RIFTPILOT LAN SPIKE — TELEFON TARAMA EKRANI")
    print("=" * 50)
    print(f"\nBağlantı Adresi: {url}\n")
    print("Telefonunun kamerasıyla aşağıdaki QR kodu tara:\n")

    # Terminal için ASCII QR Kod
    qr.print_ascii(invert=True)

    print("\n" + "-" * 50)
    print("💡 İpucu: Telefonunun bilgisayarla AYNI Wi-Fi ağında olduğundan emin ol.")
    print("🛑 Durdurmak için: Ctrl + C")
    print("-" * 50 + "\n")


def main() -> None:
    port = 8765
    lan_ip = detect_lan_ip()
    target_url = f"http://{lan_ip}:{port}/"

    print_terminal_qr(target_url)

    logger.info("FastAPI Uvicorn sunucusu başlatılıyor: %s", target_url)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
