# RiftPilot 🎮⚡

**RiftPilot**, League of Legends (LoL) istemcisini yerel ağ (LAN) üzerinden mobil cihazlarla güvenli şekilde uzaktan yöneten, istemci olaylarını gerçek zamanlı dinleyen ve şampiyon seçim süreçlerini otomatikleştiren modern bir masaüstü orkestrasyon sistemidir.

Tasarımında **LeagueCompanion** ve Riot'un Hextech görsel dilini örnek alan RiftPilot; oyuncunun bilgisayar başında olmadığı anlarda bile maç kabul etmesini, şampiyon seçip yasaklamasını ve istemcinin sunduğu resmi rün sayfalarını tek dokunuşla uygulamasını sağlar.

---

## 🌟 Öne Çıkan Özellikler

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
  - ARAM'da rastgele atanan şampiyon, yedek kulübesi ve zar (reroll) durumu gösterilir; yanlış seçim engellenir.
  - Desteklenmeyen modlarda kullanıcıyı korumak için **Güvenli Genel Durum Ekranı** devreye girer.
- **Akıllı Buton Etkinliği:** Sıra oyuncuda değilse, süre dolmuşsa veya şampiyon yasaklıysa sunucu işlemi reddeder. Çift kilit istekleri asenkron kilit mekanizmasıyla önlenir.
- **Yasaklı Şampiyon Koruması:** Yasaklanan şampiyonlar ızgarada ve aramada açıkça işaretlenir, seçilemez.

### 4. 🔮 Oyun Moduna Özel Sihirdar Büyüleri & 3 Resmi Rün Önerisi
- **Moda Özel Büyü Havuzu:** ARAM'da Kartopu ve Berraklık gösterilirken Işınlan ve Çarp devre dışı bırakılır; modda geçersiz büyülerin atanması sunucu tarafında engellenir.
- **Resmi 3 Rün Sayfası:** İstemcinin (`/lol-perks/v1/recommended-pages/...`) o şampiyon ve koridor için sunduğu 3 resmi rün sayfası listelenir.
- **Sahte Veri Yoktur:** Uygulama kendi kafasına göre rün üretmez veya kullanıcı adına gizlice uygulamaz. Kullanıcı 3 seçenekten dilediğini seçip onaylayarak istemciye aktarır.

### 5. 💎 Modernize Edilmiş Masaüstü ve Mobil Arayüz
- **Windows Masaüstü (PySide6):**
  - **Genel Bakış (Dashboard):** LCU bağlantı portu, canlı oyun durumu hero kartı, LAN Hub erişim adresi.
  - **Mobil Eşleştirme:** Hextech çerçeveli QR kod alanı, bağlı cihaz listesi ve yetki kaldırma butonları.
  - **Sistem & Ayarlar:** Ağ, güvenlik ve LCU yapılandırma detayları.
- **Mobil Web Arayüzü (FastAPI / Responsive):**
  - Tek elle kullanıma uygun alt navigasyon barı (🎮 Oyun, ⚡ Rün & Büyü, 📡 İstasyon).
  - Şampiyon rol filtre hapları (`Tümü`, `Dövüşçü`, `Büyücü`, `Suikastçı`, vb.).
  - 48px+ dokunmatik ergonomik alanlar, karanlık tema, altın/arkane cam efektleri ve mikro animasyonlar.

---

## 🏛️ Mimari Model: Hexagonal Architecture (ADR-0003)

RiftPilot; iş mantığını dış dünyadan, ağ protokollerinden ve kullanıcı arayüzlerinden tamamen izole eden **Hexagonal Mimari (Ports & Adapters)** prensibiyle inşa edilmiştir:

```text
                  +---------------------------------------------------+
                  |                 RIFTPILOT CORE                    |
                  |                                                   |
                  |   • GameModeContext & SelectionLayoutKind         |
                  |   • ChampSelectSession & ActionEligibility        |
                  |   • RecommendedRunePage & SummonerSpell           |
                  |   • OTTManager & SessionManager (Zero-Trust)      |
                  +---------------------------------------------------+
                       ^                                 ^
                       | (Inbound Port)                  | (Outbound Port: ILcuPort)
        +--------------+--------------+   +--------------+--------------+
        |                             |   |                             |
  [ Web Hub Adapter ]         [ Desktop UI ]  [ Riot LCU HTTPX Adapter ]  [ Mock LCU Adapter ]
  FastAPI / WebSockets        PySide6 GUI     Canlı LCU REST/WS           Deterministik Testler
  Mobil İstemci Paneli        Windows Masüstü (Self-Signed SSL)           (League Client Gereksiz)
```

---

## 📁 Dizin Yapısı

```text
RiftPilot/
├── docs/
│   ├── adr/                      # Mimari Karar Kayıtları (ADR 0001 - 0004)
│   └── workflows/                # Git & PR iş akışı kuralları
├── src/
│   └── riftpilot/
│       ├── core/                 # Saf iş mantığı ve sözleşmeler (Domain Core)
│       │   ├── champ_select.py   # Şampiyon seçim oturumu ve aksiyon modelleri
│       │   ├── game_mode.py      # Oyun modu tespiti, durum makinesi ve doğrulama
│       │   ├── runes_spells.py   # Büyü havuzu ve 3 resmi rün sayfası modelleri
│       │   ├── lcu_port.py       # Çıkış port arayüzü (ILcuPort)
│       │   └── pairing.py        # OTT ve oturum yöneticisi (Zero-Trust)
│       ├── adapters/             # Dış dünya adaptörleri (Inbound/Outbound)
│       │   ├── lcu_discovery.py  # Lockfile / Süreç argümanı LCU keşfi
│       │   ├── lcu_stream.py     # LCU WebSocket gerçek zamanlı olay dinleyicisi
│       │   ├── riot_lcu.py       # HTTPX tabanlı canlı LCU istemcisi
│       │   ├── mock_lcu.py       # Testler için deterministik LCU simülatörü
│       │   └── web_hub.py        # FastAPI HTTP & WebSocket mobil sunucusu (INDEX_HTML)
│       └── ui/                   # Masaüstü kullanıcı arayüzü
│           ├── main_window.py    # PySide6 3 sekmeli modern Hextech ana pencere
│           └── qr_helper.py      # QR kod pixmap üreticisi
├── tests/                        # 349 birim, entegrasyon ve TDD testleri
├── pyproject.toml                # Bağımlılıklar ve proje yapılandırması
├── GEMINI.md                     # Yapay zeka ve mühendislik kuralları
└── README.md                     # Proje genel tanıtımı
```

---

## 🚀 Kurulum ve Çalıştırma

### Gereksinimler
- **Python 3.14+**
- League of Legends İstemcisi (veya testler için mock ortam)
- Aynı yerel ağa (Wi-Fi) bağlı bir mobil cihaz

### Adım Adım Başlangıç
```powershell
# 1. Depoyu klonlayın
git clone https://github.com/sametozkan/RiftPilot.git
cd RiftPilot

# 2. Sanal ortamı oluşturun ve etkinleştirin
python -m venv .venv
.venv\Scripts\activate

# 3. Bağımlılıkları yükleyin
pip install -e .

# 4. Masaüstü uygulamasını başlatın
python -m riftpilot
```

Uygulama açıldığında ekrandaki QR kodu telefonunuzun kamerasıyla tarayarak mobil Hextech kontrol paneline bağlanabilirsiniz.

---

## 🧪 Testler

RiftPilot, tüm iş mantığını **gerçek LoL istemcisine ihtiyaç duymadan** `MockLcuAdapter` test çiftiyle doğrulayan kapsamlı bir test paketine sahiptir.

Testleri çalıştırmak için:
```powershell
python -m pytest
```

Güncel test sonucu:
```text
============================= 349 passed in 7.60s =============================
```

---

## 📜 Lisans & Yasal Uyarı

Bu proje MIT lisansı ile lisanslanmıştır.

> **Riot Games / League of Legends Yasal Uyarısı:**  
> RiftPilot, Riot Games tarafından desteklenmemektedir ve Riot Games'in veya League of Legends'ın yapımında ya da yönetiminde resmi olarak yer alan kişilerin görüşlerini yansıtmamaktadır. League of Legends ve Riot Games, Riot Games, Inc.'nin ticari markaları veya tescilli ticari markalarıdır. RiftPilot hiçbir şekilde oyun belleğine (RAM) müdahale etmez; yalnızca resmi LCU arayüzünü kullanır.
