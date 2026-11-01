# ADR-0002: Teknoloji Yığını Seçimi (Python 3.14, PySide6 & Asenkron Web Çekirdeği)

## Durum
Kabul Edildi (Accepted)

## Tarih
2026-11-01

## Bağlam (Context)
RiftPilot, kullanıcının Windows masaüstünde çalışan, sistem tepsisine (system tray) yerleşebilen, QR kod üreterek ekranda gösterebilen ve arka planda hem League of Legends istemcisi (LCU) ile hem de yerel ağdaki mobil cihazlarla asenkron olarak haberleşen bir masaüstü uygulamasıdır.

Sistemin temel gereksinimleri:
1. Windows 11 ortamında modern, hafif ve duyarlı (responsive) bir masaüstü kullanıcı arayüzü (GUI).
2. Arka planda çalışan yüksek performanslı yerel HTTP ve WebSocket sunucusu (telefona web panelini sunmak ve çift yönlü olay akışı sağlamak için).
3. LCU REST API'sine (self-signed SSL) ve WebSocket olay akışına kesintisiz asenkron erişim.
4. Geliştirme ortamında güçlü tip desteği (Pydantic, Type Hints) ve doğrulanmış paket ekosistemi.

## Karar (Decision)
RiftPilot masaüstü ve arka plan çekirdeği için **Python 3.14** ve **PySide6 (Qt for Python)** teknolojileri seçilmiştir:

1. **Masaüstü Kullanıcı Arayüzü (GUI):**
   - **PySide6 (Qt 6):** Windows yerel pencere yönetimi, sistem tepsisi (system tray) entegrasyonu, QR kod görselleştirmesi ve eşleşen cihaz listesinin yönetimi için kullanılacaktır.
2. **Yerel Ağ HTTP & WebSocket Sunucusu:**
   - **FastAPI / Starlette + Uvicorn:** Telefona mobil uyumlu tek sayfa uygulamasını (PWA/SPA) sunmak, QR kod eşleştirme uç noktalarını barındırmak ve anlık durum güncellemelerini WebSocket üzerinden iletmek için gömülü (embedded) asenkron sunucu olarak çalıştırılacaktır.
3. **Veri Sözleşmeleri ve Doğrulama:**
   - **Pydantic v2:** Komutlar, durum olayları ve eşleştirme veri paketlerinin katı tip doğrulaması için kullanılacaktır.
4. **LCU Entegrasyonu:**
   - **HTTPX / aiohttp:** Asenkron HTTP istemcisi (Riot'un self-signed SSL sertifikası desteği ile).
   - **Websockets:** LCU olaylarını dinlemek için asenkron WebSocket istemcisi.
5. **Mobil Web İstemcisi:**
   - Telefona harici bir mobil uygulama yükletmeksizin, Python sunucusu tarafından yerel ağa servis edilen modern, dokunmatik uyumlu responsive web paneli (HTML5/CSS3/JavaScript).

## Değerlendirilen Alternatifler

### 1. Electron + TypeScript / React
- **Artıları:** Web teknolojileriyle hızlı arayüz geliştirme, zengin hazır bileşenler.
- **Eksileri:** Çok yüksek bellek (RAM) ve CPU tüketimi (her pencere için ayrı Chromium süreci). Yalnızca sistem tepsisinde ve QR kod göstermek için çalışan hafif bir araç için aşırı ağır ve hantaldır.
- **Karar:** KISS prensibi gereği elendi.

### 2. Go (Golang) + Wails / Fyne
- **Artıları:** Tek bir bağımsız `.exe` binary çıktısı, düşük bellek kullanımı, yüksek eşzamanlılık.
- **Eksileri:** Masaüstü GUI kütüphanelerinin (Fyne/Wails) Qt kadar olgun olmaması, LCU ve otomasyon kütüphanelerinin Python ekosistemine kıyasla daha kısıtlı olması.
- **Karar:** GUI esnekliği ve geliştirme hızı nedeniyle elendi.

### 3. C# / .NET MAUI
- **Artıları:** Windows yerel sistemlerine mükemmel entegrasyon.
- **Eksileri:** Çapraz platform ve açık kaynak araç zincirinde bağımsız geliştirme esnekliği Python kadar çevik değildir.

## Sonuçlar (Consequences)

### Olumlu Kazanımlar:
- **Yerel Performans:** PySide6 ile Windows üzerinde akıcı, bellek dostu ve modern bir yerel GUI elde edilir.
- **Zengin Ekosistem:** PySide6, FastAPI, Uvicorn, HTTPX ve Pydantic geliştirme ortamımızda doğrulanmış ve hazır durumdadır.
- **Bütünleşik Çekirdek:** Ağ iletişimi, LCU istemcisi ve masaüstü arayüzü aynı dil ekosisteminde tutarlı bir şekilde geliştirilebilir.

### Kabul Edilen Tavizler & Mimari Dikkat Noktaları:
- **Qt ve Asyncio Olay Döngüsü Entegrasyonu:** Qt'nin C++ tabanlı `QEventLoop` döngüsü ile Python'un `asyncio` döngüsünün birbirini bloke etmeden (GUI donmalarını engelleyerek) çalışması gerekir. Bu durum işçi iş parçacıkları (Worker Threads) ve Qt Signal/Slot mekanizması ile çözülecektir.
- **Dağıtım (Packaging):** Son kullanıcıya dağıtımda Python yorumlayıcısının paketlenmesi gerekecektir (`Nuitka` veya `PyInstaller` kullanılacaktır).
