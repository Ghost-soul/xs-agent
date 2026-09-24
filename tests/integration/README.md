# Integration tests

Tests requiring PostgreSQL belong here and must use an explicit marker.

Run the isolated PostgreSQL workflow gate from the repository root:

```powershell
./scripts/test.ps1 -Integration
```

The gate only targets `novel_writer_test`, migrates it to Alembic head, and cleans application data before and after each test. It fails closed if the dedicated test database is unavailable or any integration test is skipped.
