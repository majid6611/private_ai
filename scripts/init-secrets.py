"""Generate local credentials without overwriting existing files."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / '.secrets'
if root.is_symlink():
    raise SystemExit('Refusing a symlink secret directory')
root.mkdir(mode=0o700, exist_ok=True)
root.chmod(0o700)
for name in ('db_admin_password', 'db_app_password', 'db_migration_password', 'ingestion_key', 'orchestrator_key'):
    path = root / name
    if path.is_symlink():
        raise SystemExit('Refusing a symlink secret file')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        continue
    with os.fdopen(fd, 'w') as output:
        output.write(secrets.token_urlsafe(32))
for name in ('db_admin_password', 'db_app_password', 'db_migration_password', 'ingestion_key', 'orchestrator_key'):
    path = root / name
    if path.is_file():
        path.chmod(0o444)  # Parent directory is private; mounted secrets are readable by container UID.
print('Local secret files are ready; values were not printed.')
