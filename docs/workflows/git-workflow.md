# Git & GitHub Geliştirme İş Akışı

Bu rehber, RiftPilot projesinde kod kalitesini, izlenebilirliği ve ekip disiplinini korumak için takip edilecek Git standartlarını tanımlar.

---

## 1. Branch (Dal) Stratejisi

Projemizde **GitHub Flow** modeli uygulanır.

- **`main` Dalı:** Üretim kalitesinde, her an çalışır ve kararlı (stable) durumda olan ana daldır. `main` dalına asla doğrudan `push` yapılmaz.
- **Özellik ve Görev Dalları:** Her yeni çalışma `main` dalından türetilen kısa ömürlü bir dalda yürütülür:
  - `feat/<ozellik-kisa-adi>`: Yeni işlevler (örn: `feat/lcu-connector`)
  - `fix/<hata-kisa-adi>`: Hata düzeltmeleri (örn: `fix/websocket-heartbeat`)
  - `docs/<dokuman-adi>`: Dokümantasyon eklemeleri (örn: `docs/adr-0002-stack`)
  - `test/<test-alani>`: Test suiteleri veya mock altyapısı (örn: `test/lcu-mock`)
  - `refactor/<kapsam>`: Davranış değişikliği içermeyen yapısal düzenlemeler

---

## 2. Commit Mesaj Standartları (Conventional Commits)

Commit geçmişimizin temiz, anlaşılır ve otomatik araçlarca taranabilir olması için **Conventional Commits 1.0.0** standardı benimsenmiştir.

### Format
```text
<tip>(<kapsam>): <kısa ve net açıklama (emir kipiyle)>

[İsteğe bağlı detaylı açıklama gövdesi]

[İsteğe bağlı footer / Issue referansı, örn: Closes #12]
```

### Tipler
- **`feat`**: Yeni bir özelliğin eklenmesi.
- **`fix`**: Bir hatanın düzeltilmesi.
- **`docs`**: Yalnızca dokümantasyon değişikliği.
- **`test`**: Test eklenmesi veya mevcut testlerin düzeltilmesi.
- **`refactor`**: Ne bir hata düzelten ne de yeni özellik ekleyen kod değişikliği.
- **`chore`**: Yapılandırma, bağımlılık güncellemesi veya rutin işler.
- **`ci`**: CI/CD konfigürasyon değişiklikleri (GitHub Actions vb.).

### Örnekler
- `feat(lcu): parse lockfile and extract local auth credentials`
- `fix(network): handle socket reconnection with exponential backoff`
- `docs(adr): add ADR-0002 evaluating tech stack options`
- `test(auth): add unit tests for token validation edge cases`

---

## 3. Pull Request (PR) & Code Review Süreci

1. **Branch Oluşturma:** `git checkout -b feat/my-feature`
2. **Düzenli & Anlamlı Commitler:** Değişiklikleri mantıksal küçük adımlarla commit'le.
3. **Lokal Doğrulama:** Lint, tip kontrolü ve testleri lokalde çalıştır.
4. **Push ve PR Açma:**
   - Dalı GitHub'a gönder: `git push -u origin feat/my-feature`
   - PR başlığında ve gövdesinde neyin değiştiğini, nasıl doğrulandığını açıkla.
5. **Code Review:** Code Reviewer rolü ve ekip arkadaşı kodu inceler; gerekirse revizyon talep eder.
6. **Merge:** Onaylanan ve CI kontrolleri yeşil olan PR, `Squash and Merge` (veya `Rebase and Merge`) yöntemiyle `main` dalına dahil edilir.

---

## 4. Semantik Sürümleme (SemVer)

Sürümlerimiz `vMAJOR.MINOR.PATCH` formatında etiketlenecektir:
- **`MAJOR`**: Geriye dönük uyumsuz (breaking change) mimari/API değişiklikleri.
- **`MINOR`**: Geriye dönük uyumlu yeni işlevler.
- **`PATCH`**: Geriye dönük uyumlu hata düzeltmeleri.
