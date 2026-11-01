# ADR-0001: Architecture Decision Records (ADR) Kullanımı

## Durum
Kabul Edildi (Accepted)

## Tarih
2026-11-01

## Bağlam (Context)
RiftPilot projesi; LoL istemcisi (LCU), yerel işletim sistemi süreçleri, güvenlik ve yetkilendirme mekanizmaları ile uzaktan erişim protokollerini içeren çok bileşenli bir mimariye sahip olacaktır.

Geliştirme sürecinde mimari, teknoloji seçimi, protokol ve güvenlik konularında kritik teknik kararlar alınacaktır. Bu kararlar yalnızca kod içinde veya sözlü iletişimde bırakıldığında:
1. Zamanla kararların arkasındaki mühendislik gerekçesi unutulur.
2. Alternatiflerin neden elendiği bilinmediği için aynı hatalar tekrar tartışılır.
3. Yeni bir geliştirici (veya yapay zeka ajanı) projeye dahil olduğunda bağlamı kavramakta zorlanır.

## Karar (Decision)
Tüm önemli mimari ve yapısal kararlar, Michael Nygard'ın önerdiği ve sektör standardı haline gelen **Mimari Karar Kaydı (Architecture Decision Record - ADR)** formatında `docs/adr/` dizini altında versiyon kontrollü olarak saklanacaktır.

Her ADR aşağıdaki yapıyı takip edecektir:
- **Numara ve Başlık:** Sıralı numara ve açıklayıcı başlık (örn: `0001-record-architecture-decisions.md`).
- **Durum:** `Taslak (Draft)`, `Kabul Edildi (Accepted)`, `Reddedildi (Rejected)` veya `Geçersiz Kılındı (Superseded by ADR-XXXX)`.
- **Bağlam ve Problem Tanımı:** Kararın hangi şartlar altında alındığı ve çözülmek istenen problem.
- **Değerlendirilen Alternatifler:** Ele alınan seçeneklerin artı ve eksileri.
- **Karar ve Gerekçe:** Hangi seçeneğin neden tercih edildiği.
- **Sonuçlar (Consequences):** Kararın getirdiği olumlu kazanımlar ve kabul edilen tavizler (trade-offs).

## Değerlendirilen Alternatifler
1. **Sadece Kod İçi Yorumlar & Docstring:** Yetersizdir; kod seviyesindeki yorumlar sistemin genel vizyonunu ve elenen alternatifleri göstermez.
2. **Harici Wiki / Notion:** Kod tabanından kopuk kalır; PR ve Git geçmişi ile eşzamanlı güncellenmesi zordur.
3. **Repository İçi Markdown ADR Dosyaları (Seçilen):** Kod ile aynı PR sürecinde incelenir, versiyonlanır ve doğrudan proje bağlamında yaşar.

## Sonuçlar (Consequences)
- **Olumlu:**
  - Mimari kararlar şeffaf ve geriye dönük izlenebilir hale gelir.
  - Yapay zeka ajanları karar alırken veya kod üretirken ilgili ADR'ı okuyarak mimari ilkelerle çelişmeyen çıktılar üretir.
- **Kabul Edilen Taviz (Trade-off):**
  - Her kritik karar için dokümantasyon yazma disiplini gerektirir (küçük bir başlangıç eforu).
