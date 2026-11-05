# ADR-009: SQLite MVP Şeması ve PostgreSQL Taşınabilirliği

- **Durum:** Onaylandı (M3A bağlantı temeli ve M3B ilk 4 ORM modeli + Alembic göç sistemi uygulandı; değerlendirme tabloları M7'de eklenecek)

## Bağlam

Yerel geliştirme için sıfır kurulumlu bir veritabanı gerekirken, ileride çok kullanıcılı bulut dağıtımında PostgreSQL kullanılacaktır.

## Karar

MVP'de SQLAlchemy 2.0 Async ve Alembic ile SQLite (WAL modu) kullanılır. Tüm modellerde lehçeden bağımsız tipler (`Uuid`, `JSON`, `Numeric`, `UTCDateTime` sarmalayıcısıyla `DateTime(timezone=True)`, metin tabanlı durum alanları ve açık `NAMING_CONVENTION` ile adlandırılmış `CHECK` / `UNIQUE` kısıtları) kullanılır; SQLite'a özgü ham şema tiplerinden kaçınılır.

### Milestone 3B Uygulama Notları

- **UTC Zaman Damgaları:** SQLite sürücüsünden gelen naif zaman damgaları `UTCDateTime` (`TypeDecorator`) ile okuma/yazma sırasında UTC `tzinfo` korunacak şekilde normalize edilir; PostgreSQL'de doğrudan `TIMESTAMP WITH TIME ZONE` sütununa eşlenir.
- **SQLite Batch ve Döngüsel Yabancı Anahtar Yönetimi:** `migrations/env.py` yalnızca SQLite lehçesinde `render_as_batch=True` etkinleştirir. `workflow_runs.final_version_id` ile `prompt_versions.id` arasındaki döngüsel yabancı anahtar, `upgrade()` sırasında `use_alter=True` + `batch_alter_table("workflow_runs")` ile eklenir; `downgrade()` sırasında `PRAGMA foreign_keys=ON` kapatılmadan önce `final_version_id` değerleri `NULL` yapılır ve PostgreSQL gibi katalog tabanlı lehçelerde döngüsel FK kısıtı düşürülerek tablolar güvenle kaldırılır.
- **Göç Politikası:** Uygulama başlangıcında otomatik göç veya `Base.metadata.create_all()` çalıştırılmaz; tüm şema değişiklikleri Alembic revizyonlarıyla (`0001_initial_schema`) yönetilir.

## Sonuçlar

- Yerel kurulum tek komutla çalışır; PostgreSQL'e geçişte yalnızca bağlantı dizesi (`postgresql+asyncpg://...`) ve `alembic upgrade head` çalıştırması yeterli olur.

## Amendment — M5E-2: populated migrations and durability prerequisites

`0002_workflow_persistence` adds the fifth application table and additive workflow/
operation fields. Populated circular-FK tests showed ordinary SQLite batch rebuilding
cannot drop referenced tables with FK enforcement active. Dedicated SQLite migration
connections now isolate FK switching outside an explicit BEGIN IMMEDIATE transaction,
include DDL and the version stamp atomically, check foreign_key_check before commit,
and restore their original FK state. Active caller transactions are rejected safely.
Application session/PRAGMA behavior is unchanged; no global FK switch is introduced.

Downgrade refuses new persistent data or null telemetry that the old schema cannot
represent; safe legacy-only downgrade preserves existing rows. Before M5E-3 enables
concurrent durable writes, verify a patched SQLite runtime, explicit service BEGIN
control and the chosen NORMAL/FULL power-loss durability policy. Current verification
runtime 3.45.3 requires the documented WAL-reset fix. PostgreSQL DDL compiles, but
server migration/concurrency testing remains future work. [Schema and migration notes](../workflow-persistence-schema.md).

