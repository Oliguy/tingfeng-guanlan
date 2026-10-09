"""Released ETF schema compatibility bounds; changes require an explicit migration."""
CURRENT_VERSION = 4
READ_MIN_VERSION = 3

class SchemaCompatibilityError(RuntimeError):

    def __init__(self, code, version=None):
        self.code, self.version = (code, version)
        super().__init__(f'{code}: ETF schema {version!r}')

def version_of(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone():
        return 0
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise SchemaCompatibilityError('etf_schema_invalid', row[0]) from None

def require_schema(conn, *, writable=False):
    version = version_of(conn)
    if version > CURRENT_VERSION:
        raise SchemaCompatibilityError('etf_schema_future', version)
    if version < (CURRENT_VERSION if writable else READ_MIN_VERSION):
        raise SchemaCompatibilityError('etf_schema_migration_required', version)
    return version
