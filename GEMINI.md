# RiftPilot — AI Agent & Engineering Instructions

Bu belge, **RiftPilot** projesinde görev alan tüm yapay zeka ajanları (ve geliştiriciler) için bağlayıcı mühendislik rehberi ve kural kümesidir. Antigravity CLI tarafından kök dizin kuralı olarak otomatik olarak yüklenir.

---

## 1. Proje Amacı ve Genel Mimari

RiftPilot; League of Legends (LoL) istemcisini (LCU) yerel ağ (LAN) üzerinden mobil cihazlarla (QR kod ile eşleşen web paneli) uzaktan yöneten, gelecekte ise internet (WAN) üzerinden erişime imkan tanıyan hafif bir masaüstü orkestrasyon sistemidir.

- **Mimari Model:** Hexagonal Architecture (Ports and Adapters).
- **Temel İlke:** LAN-First, Remote-Ready. İş mantığı taşıyıcı protokolden (HTTP, WS, WebRTC) tamamen izoledir.
- **Detaylı Mimari Dokümanı:** [ADR-0003: Hexagonal Mimari](docs/adr/0003-hexagonal-transport-agnostic-architecture.md)

---

## 2. Onaylanmış Teknoloji Yığını

- **Masaüstü Kullanıcı Arayüzü (GUI):** Python 3.14 + PySide6 (Qt 6).
- **Yerel Ağ HTTP & WebSocket Sunucusu:** FastAPI / Starlette + Uvicorn (Gömülü asenkron servis).
- **Veri Doğrulama ve Sözleşmeler:** Pydantic v2.
- **LCU İstemcisi:** HTTPX (asenkron HTTPS + self-signed SSL) ve `websockets`.
- **Mobil Arayüz:** Python tarafından servis edilen dokunmatik uyumlu Responsive Web Panel.
- **Test Çatısı:** Pytest (`pytest-asyncio`).
- **Detaylı Teknoloji Kararı:** [ADR-0002: Teknoloji Yığını](docs/adr/0002-technology-stack-pyside6-python.md)

---

## 3. Kodlama ve Mimari Standartları

1. **SOLID & Clean Code:** Her sınıf/modül tek bir sorumluluğa sahip olmalıdır.
2. **KISS & YAGNI:** Fazladan soyutlama katmanı ekleme; ihtiyacımız olan kadar yalın, değişime açık kod yaz.
3. **Tip Güvenliği (Type Annotations):** Tüm fonksiyon, metot ve modellerde katı tip ipuçları (`type hints`) zorunludur.
4. **Asenkron / UI İzolasyonu:** PySide6 UI ana iş parçacığı (`QEventLoop`) asla bloklanmamalıdır. Ağ işlemleri ve LCU dinleyicileri asenkron işçilerde (`QThread` veya `asyncio`) yürütülmelidir.
5. **Kesinleşmemiş Detaylar:** Mobil yerel SSL vs HTTP davranışları henüz Spike aşamasındadır; kesin kabul edilmemelidir.

---

## 4. Güvenlik Kuralları (Shift-Left Security)

1. **Kriptografik OTT:** QR kod içindeki eşleştirme tokenı (One-Time Token) tek kullanımlıktır; okunduğu anda sunucuda yakılır (burn-on-read).
2. **Sıfır Güven (Zero-Trust) Komut Denetimi:** Geçerli bir `SessionToken` taşımayan hiçbir yerel ağ komutu işlenmez.
3. **Masaüstü Yetki İptali:** Kullanıcı masaüstü arayüzünden bağlı cihazların yetkisini tek tuşla sonlandırabilmelidir.
4. **Riot Hizmet Şartları (TOS):** Oyun içi belleğe müdahale edilmez (bellek okuma/yazma yasaktır). Yalnızca Riot'un resmi LCU REST/WebSocket arayüzü kullanılır.
5. **Gizlilik:** `.env`, yerel oturum tokenları veya kullanıcı bilgileri asla versiyon kontrolüne commit edilmez.
6. **Detaylı Güvenlik Modeli:** [ADR-0004: Güvenlik ve Eşleştirme](docs/adr/0004-qr-pairing-and-device-session-security.md)

---

## 5. Git, Branch ve PR Kuralları

- **Model:** GitHub Flow + Conventional Commits.
- **Dallar:** `main` dalına doğrudan kod gönderilmez. İşler `feat/*`, `fix/*`, `docs/*`, `test/*`, `refactor/*` dallarında yapılır.
- **Commit Formatı:** `<tip>(<kapsam>): <açıklama>` (örn: `feat(pairing): generate single-use qr token`).
- **Onay Zorunluluğu:** Kullanıcı açık onay vermeden dosya oluşturulmaz, commit atılmaz ve remote repoya push yapılmaz.
- **Detaylı İş Akışı:** [Git & GitHub Geliştirme İş Akışı](docs/workflows/git-workflow.md)

---

## 6. Test Zorunlulukları

- **Test-First / Shift-Left:** Her iş mantığı birim testiyle doğrulanabilir olmalıdır.
- **LCU Mocking:** Gerçek League of Legends istemcisi açık olmadan tüm testler mock LCU sunucusuyla çalışabilmelidir.
- **Birim & Entegrasyon Testleri:** Testleri geçmeyen hiçbir kod parçası `main` dalına birleştirilemez.

---

## 7. Ajan Rolleri, Yetki Sınırları ve Token Tasarrufu

### Token Tasarrufu Kuralları
- **Gerektiği Kadar Yükle:** Her görevde yalnızca ilgili dosyayı oku; projedeki tüm dokümanları aynı anda bağlama yükleme.
- **Modüler Okuma:** Yalnızca o anki görevin gerektirdiği ADR veya rehberi incele.
- **Büyük Çıktılardan Kaçın:** Yanıtlarda kodun tamamını kopyalamak yerine diff veya hedeflenen blokları sun.

### Ajan Rolleri ve Yetki Matrisi
- **Architect:** Yalnızca `docs/adr/` ve mimari sözleşmeleri tasarlar. Kod yazmaz.
- **Backend / Core Engineer:** Yalnızca `src/core/`, `src/adapters/` ve ilgili backend testlerini yazar.
- **Frontend / Mobile Engineer:** Yalnızca mobil web paneli ve statik arayüz varlıklarını yazar.
- **QA Engineer:** Yalnızca `tests/` dizininde test yazar; üretim kodunu doğrudan değiştirmez.
- **Security Engineer:** Tehdit analizi ve kod denetimi yapar; açık tespit ederse revizyon talep eder.
- **Code Reviewer:** Kod standartlarını denetler; PR onaylar veya düzeltme ister.

---

## 8. Dokümantasyon İndeksi

- [README.md](README.md) - Proje genel tanıtımı
- [docs/adr/0001-record-architecture-decisions.md](docs/adr/0001-record-architecture-decisions.md) - ADR Karar Süreci
- [docs/adr/0002-technology-stack-pyside6-python.md](docs/adr/0002-technology-stack-pyside6-python.md) - Python 3.14 + PySide6
- [docs/adr/0003-hexagonal-transport-agnostic-architecture.md](docs/adr/0003-hexagonal-transport-agnostic-architecture.md) - Hexagonal Mimari
- [docs/adr/0004-qr-pairing-and-device-session-security.md](docs/adr/0004-qr-pairing-and-device-session-security.md) - QR Eşleştirme & Güvenlik
- [docs/workflows/git-workflow.md](docs/workflows/git-workflow.md) - Git & PR Standartları
