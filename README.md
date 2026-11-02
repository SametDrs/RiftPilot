# RiftPilot 🎮⚡

**RiftPilot**, League of Legends (LoL) istemcisini yerel ağ (LAN) ve güvenli uzaktan erişim tünelleri (WAN) üzerinden mobil cihazlarla yöneten, istemci olaylarını gerçek zamanlı dinleyen ve lobi/seçim süreçlerini otomatikleştiren modern bir masaüstü orkestrasyon sistemidir.

Tasarımında **LeagueCompanion** ve Riot'un Hextech görsel dilini örnek alan RiftPilot; oyuncunun bilgisayar başında olmadığı anlarda bile lobi kurmasını, sıraya girmesini, maç kabul etmesini, şampiyon seçip yasaklamasını, ARAM yedek kulübesi takaslarını yapmasını ve maç sonu skorlarını inceleyip takım arkadaşlarını onurlandırmasını sağlar.

---

## 🌟 Öne Çıkan Yetenekler (Milestone 1 — 7)

### 1. ⚔️ Otomatik & Uzaktan Maç Kabulü (Ready Check)
- LoL istemcisiyle canlı WebSocket akışı üzerinden milisaniyelik olay senkronizasyonu.
- Maç bulunduğunda mobil ekranda sesli, titreşimli ve görsel uyarı.
- Tek dokunuşla maçı kabul etme (`ACCEPT_READY_CHECK`).

### 2. 🛡️ Sıfır Güven (Zero-Trust) & Tek Kullanımlık QR Eşleştirme (OTT)
- Masaüstü ekranındaki QR kod, tek kullanımlık kriptografik One-Time Token (`OTT`) içerir.
- **Burn-on-Read:** QR kod mobil cihaz tarafından okunduğu an sunucuda imha edilir, tekrar kullanılamaz.
- Her mobil komut, geçerli bir `SessionToken` taşımak zorundadır; masaüstü arayüzünden bağlı cihazların yetkisi tek tuşla anında iptal edilebilir (`Revocation`).
- **Riot Hizmet Şartları (%100 TOS Uyumluluğu):** Oyun içi belleğe asla müdahale edilmez (bellek okuma/yazma yasaktır). Yalnızca Riot'un resmi LCU REST/WebSocket arayüzü kullanılır.

### 3. 🎯 Oyun Moduna Duyarlı Şampiyon Seçimi & Yasaklama
- **Mod Algılama:** Dereceli Sıralı Seçim, Normal Kapalı Seçim, ARAM ve ARAM Şamata (Brawl) modlarını otomatik tanır.
- **Dinamik Ekranlar:**
  - Kapalı seçimde ban arayüzü gizlenir.
  - ARAM'da rastgele atanan şampiyon, yedek kulübesi ve zar durumu gösterilir.
  - Desteklenmeyen modlarda kullanıcıyı korumak için **Güvenli Genel Durum Ekranı** devreye girer.
- **Akıllı Buton Etkinliği:** Sıra oyuncuda değilse, süre dolmuşsa veya şampiyon yasaklıysa sunucu işlemi reddeder. Çift kilit istekleri asenkron kilit mekanizmasıyla önlenir.
- **Yasaklı Şampiyon Koruması:** Yasaklanan şampiyonlar ızgarada ve aramada açıkça işaretlenir, seçilemez.

### 4. 🔮 Oyun Moduna Özel Sihirdar Büyüleri & 3 Resmi Rün Önerisi
- **Moda Özel Büyü Havuzu:** ARAM'da Kartopu ve Berraklık gösterilirken Işınlan ve Çarp devre dışı bırakılır; modda geçersiz büyülerin atanması sunucu tarafında engellenir.
- **Resmi 3 Rün Sayfası:** İstemcinin (`/lol-perks/v1/recommended-pages/...`) o şampiyon ve koridor için sunduğu 3 resmi rün sayfası listelenir.
- **Sahte Veri Yoktur:** Uygulama yapay rün üretmez veya kullanıcı adına gizlice uygulamaz. Kullanıcı 3 seçenekten dilediğini seçip onaylayarak istemciye aktarır.

### 5. 🏠 Lobi Yönetimi, Rol Tercihleri & ARAM Etkileşimleri (Milestone 4)
- **Lobi Kontrolü:** Mobil ekrandan tek dokunuşla sıraya girme (`POST /lol-lobby/v2/lobby/matchmaking/search`) veya sırayı iptal etme.
- **Rol / Konum Belirleme:** Dereceli ve sıralı seçim için Birincil (1.) ve İkincil (2.) rol tercihlerini (Üst, Orman, Orta, Alt, Destek, Doldur) anında güncelleme.
- **ARAM Yedek Kulübesi (Bench Swap):** Havuzdaki şampiyonları anlık görüp tek dokunuşla takas yapma (`POST /lol-champ-select/v1/session/bench/swap/{id}`).
- **ARAM Zar Atma (Reroll):** Kalan zar haklarını görme ve uzaktan zar atma.

### 6. 📊 Canlı Oyun Takibi, Maç Sonu Skor Tablosu & Onurlandırma (Milestone 5)
- **Oyun Sonu Ekranı:** Maç bittiğinde detaylı Hextech Zafer/Bozgun özeti (`/lol-end-of-game/v1/eog-stats-block`).
- **Detaylı İstatistikler:** KDA oranı, verilen hasar, kazanılan altın, minyon (CS), kullanılan eşyalar ve takımın MVP'si.
- **Takım Arkadaşını Onurlandırma (Honor):** Maç sonunda takım arkadaşlarını listeleme ve kategorisine göre (❤️ İyi Kalpli, 🧠 Harika Lider, ❄️ Sakin Kaldı) uzaktan onurlandırma oyu verme.

### 7. 🌐 WAN / Remote-Ready Uzaktan Erişim (Milestone 6 - ADR-0003 Faz 2)
- **Hücresel Veri (4G/5G) Desteği:** Ev ağında (Wi-Fi) olma zorunluluğunu ortadan kaldıran güvenli ters tünelleme mimarisi (`CloudflareTunnelAdapter` & `MockTunnelAdapter`).
- **Taşıyıcıdan Bağımsız Zarf (`MessageEnvelope`):** İletişim protokolünden (LAN HTTP/WS, WAN Relay, WebRTC) bağımsız standart mesajlaşma.
- **WAN QR Kodu:** Dışarıdayken de tek taramayla güvenli uçtan uca şifreli oturum kurma.

### 8. 📦 Sistem Tepsisi (Tray), Windows Paketleme (.exe) & CI/CD (Milestone 7)
- **Sistem Tepsisi (`QSystemTrayIcon`):** Pencere kapatıldığında uygulamanın arka planda sessizce çalışmaya devam etmesi ve mobil bağlantının kopmaması.
- **Yerel Windows Bildirimleri:** Sıra geldiğinde veya şampiyon seçim sıranız başladığında masaüstü toast/balon bildirimi.
- **Tek Dosya / Taşınabilir Paket:** PyInstaller ile tek tıkla çalıştırılabilir `.exe` oluşturma scripti (`scripts/build_exe.py` ve `riftpilot.spec`).
- **GitHub Actions CI/CD:** Python 3.12, 3.13 ve 3.14 üzerinde Windows ortamında otomatik test ve derleme hattı (`.github/workflows/ci.yml`).

---

## 🏛️ Mimari Model: Hexagonal Architecture (ADR-0003)

RiftPilot; iş mantığını dış dünyadan, ağ protokollerinden ve kullanıcı arayüzlerinden tamamen izole eden **Hexagonal Mimari (Ports & Adapters)** prensibiyle inşa edilmiştir:

```text
                  +-------------------------------------------------------------+
                  |                       RIFTPILOT CORE                        |
                  |                                                             |
                  |   • GameModeContext & SelectionLayoutKind                   |
                  |   • ChampSelectSession & ActionEligibility                  |
                  |   • LobbyData, MatchmakingSearchState & BenchChampion        |
                  |   • EndOfGameStats & HonorBallot                            |
                  |   • MessageEnvelope & TransportProtocol                     |
                  |   • OTTManager & SessionManager (Zero-Trust Security)       |
                  +-------------------------------------------------------------+
                       ^                                       ^
                       | (Inbound Ports)                       | (Outbound Ports)
        +--------------+--------------+         +--------------+--------------+
        |                             |         |                             |
  [ Web Hub Adapter ]         [ Desktop UI ]    [ Riot LCU HTTPX Adapter ]  [ Mock LCU Adapter ]
  FastAPI / WebSockets        PySide6 GUI       Canlı LCU REST/WS           391 Deterministik Test
  LAN & WAN Relay Hub         Sistem Tepsisi    (Self-Signed SSL)           (League Client Gereksiz)
```

---

## 📁 Dizin Yapısı

```text
RiftPilot/
├── .github/
│   └── workflows/
│       └── ci.yml                # Windows multi-python CI/CD test & build hattı
├── docs/
│   ├── adr/                      # Mimari Karar Kayıtları (ADR 0001 - 0004)
│   └── workflows/                # Git & PR iş akışı kuralları
├── scripts/
│   └── build_exe.py              # Windows .exe derleme scripti
├── src/
│   └── riftpilot/
│       ├── core/                 # Saf iş mantığı ve sözleşmeler (Domain Core)
│       │   ├── champ_select.py   # Şampiyon seçim oturumu ve aksiyon modelleri
│       │   ├── game_mode.py      # Oyun modu tespiti, durum makinesi ve doğrulama
│       │   ├── lobby.py          # Lobi üyeleri, rol tercihleri ve ARAM bench modelleri
│       │   ├── post_game.py      # Maç sonu skor tablosu, KDA ve onurlandırma modelleri
│       │   ├── runes_spells.py   # Büyü havuzu ve 3 resmi rün sayfası modelleri
│       │   ├── transport.py      # Taşıyıcıdan bağımsız mesaj zarfı ve tünel portu
│       │   ├── lcu_port.py       # Çıkış port arayüzü (ILcuPort)
│       │   └── pairing.py        # OTT ve oturum yöneticisi (Zero-Trust)
│       ├── adapters/             # Dış dünya adaptörleri (Inbound/Outbound)
│       │   ├── lcu_discovery.py  # Lockfile / Süreç argümanı LCU keşfi
│       │   ├── lcu_stream.py     # LCU WebSocket gerçek zamanlı olay dinleyicisi
│       │   ├── riot_lcu.py       # HTTPX tabanlı canlı LCU istemcisi
│       │   ├── mock_lcu.py       # Testler için deterministik LCU simülatörü
│       │   ├── tunnel_adapter.py # Cloudflare & Mock WAN ters tünelleme adaptörü
│       │   └── web_hub.py        # FastAPI HTTP & WebSocket mobil sunucusu (INDEX_HTML)
│       └── ui/                   # Masaüstü kullanıcı arayüzü
│           ├── main_window.py    # PySide6 modern Hextech ana pencere & sistem tepsisi
│           └── qr_helper.py      # QR kod pixmap üreticisi
├── tests/                        # 391 birim, entegrasyon ve TDD testleri
├── pyproject.toml                # Bağımlılıklar ve proje yapılandırması
├── riftpilot.spec                # PyInstaller masaüstü paketleme yapılandırması
├── GEMINI.md                     # Yapay zeka ve mühendislik kuralları
└── README.md                     # Proje genel tanıtımı
```

---

## 🚀 Kurulum ve Çalıştırma

### Gereksinimler
- **Python 3.12, 3.13 veya 3.14**
- League of Legends İstemcisi (veya testler için mock ortam)
- Mobil cihaz (aynı Wi-Fi ağında veya WAN tüneliyle internet üzerinden)

### Adım Adım Başlangıç
```powershell
# 1. Depoyu klonlayın
git clone https://github.com/sametozkan/RiftPilot.git
cd RiftPilot

# 2. Sanal ortamı oluşturun ve etkinleştirin
python -m venv .venv
.venv\Scripts\activate

# 3. Bağımlılıkları geliştirici modunda yükleyin
pip install -e ".[dev]"

# 4. Masaüstü uygulamasını başlatın (alternatiflerden biriyle):
python run.py                     # En pratik başlatıcı (Doğrudan çalıştırma)
python -m riftpilot               # Modül olarak çalıştırma
python run.py --mock              # LoL açık değilse simüle test moduyla çalıştırma
python run.py --headless          # Yalnızca mobil web sunucusunu çalıştırma (GUI olmadan)
```

---

## 🧪 Testleri Çalıştırma

RiftPilot, League of Legends istemcisinin açık olmasına gerek kalmadan test edilebilir. 395 testin tamamı mock LCU adaptörleri ve HTTPX mock taşımalarıyla saniyeler içinde koşar:

```powershell
# Tüm birim ve entegrasyon testlerini çalıştırın
python -m pytest

# Ayrıntılı test çıktısı ve süreleri
python -m pytest -v --durations=10
```

---

## 📄 Lisans
Bu proje MIT lisansı ile korunmaktadır. League of Legends ve Riot Games, Riot Games, Inc.'in tescilli ticari markalarıdır. RiftPilot, Riot Games tarafından onaylanmamıştır veya desteklenmemektedir.
