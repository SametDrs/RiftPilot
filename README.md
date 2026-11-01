# RiftPilot 🎮🚀

**RiftPilot**, League of Legends (LoL) istemcisini uzaktan yönetmek, istemci olaylarını dinlemek ve otomatikleştirilebilir uzaktan kontrol arayüzleri sunmak üzere tasarlanan modern bir mühendislik projesidir.

---

## 🎯 Proje Hedefleri ve Felsefesi

RiftPilot projesi, yalnızca çalışan bir araç üretmekle kalmayıp modern yazılım mühendisliği prensiplerini uçtan uca uygulamayı hedefler:

- **Clean Architecture & KISS:** Gereksiz karmaşıklıktan arındırılmış, sorumlulukları ayrıştırılmış (Separation of Concerns) katmanlı yapı.
- **Shift-Left Security & Testing:** Güvenlik tehdit modellemesi ve test süreçlerinin tasarım aşamasından itibaren yürütülmesi.
- **Riot Games & Vanguard Uyumluluğu:** Oyun içi belleğe müdahale etmeyen, Riot'un resmi LCU (League Client Update) protokol ve sözleşmelerine sadık güvenli entegrasyon.
- **Agentic AI Orchestration:** Yapay zeka ajanlarının uzmanlaşmış roller (Architect, QA, Security, Reviewer) ile profesyonel bir ekip gibi orkestre edilmesi.

---

## 📁 Dizin Yapısı

Proje, bağlam kirliliğini önlemek ve modülerliği sağlamak amacıyla aşağıdaki dizin yapısını takip eder:

```text
RiftPilot/
├── .github/                # GitHub Actions CI/CD iş akışları ve PR şablonları
├── docs/                   # Modüler teknik dokümantasyon
│   ├── adr/                # Mimari Karar Kayıtları (Architecture Decision Records)
│   ├── architecture/       # Sistem mimarisi ve protokol detayları
│   └── workflows/          # Geliştirme standartları, Git ve test rehberleri
├── src/                    # Uygulama kaynak kodları (mimari kararlar sonrası yapılandırılacak)
├── tests/                  # Birim, entegrasyon ve sözleşme testleri
├── .gitignore              # Sürüm kontrolünden hariç tutulan dosya kuralları
└── README.md               # Proje genel bakışı ve hızlı başlangıç
```

---

## 📚 Dokümantasyon ve Mimari Kararlar

Projenin teknik omurgası Mimari Karar Kayıtları (ADR) ile belgelenir:

- [ADR Dizini](docs/adr/)
- [ADR-0001: Architecture Decision Records Adoption](docs/adr/0001-record-architecture-decisions.md)
- [Git & Geliştirme İş Akışı](docs/workflows/git-workflow.md)

---

## 🚦 Mevcut Durum

- **Aşama:** `Phase 0 - Engineering Foundation & Architectural Inception`
- **Lisans:** MIT (Henüz belirlenmedi)
