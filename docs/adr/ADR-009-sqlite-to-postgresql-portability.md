# ADR-009: SQLite MVP Şeması ve PostgreSQL Taşınabilirliği

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 3)

## Bağlam

Yerel geliştirme için sıfır kurulumlu bir veritabanı gerekirken, ileride çok kullanıcılı bulut dağıtımında PostgreSQL kullanılacaktır.

## Karar

MVP'de SQLAlchemy 2.0 Async ve Alembic ile SQLite (WAL modu) kullanılır. Tüm modellerde lehçeden bağımsız tipler (`Uuid`, `JSON`, `DateTime(timezone=True)`, metin tabanlı durum alanları ve `CHECK` kısıtları) kullanılır; SQLite'a özgü ham SQL ifadelerinden kaçınılır.

## Sonuçlar

- Yerel kurulum tek komutla çalışır; PostgreSQL'e geçişte yalnızca bağlantı dizesi ve göç çalıştırması yeterli olur.

