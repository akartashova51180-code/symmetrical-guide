#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${BRAIN_DEPLOY_DIR:?Set BRAIN_DEPLOY_DIR in the systemd environment file}"
: "${BRAIN_DATA_ROOT:?Set BRAIN_DATA_ROOT in the systemd environment file}"
: "${BRAIN_BACKUP_DIR:?Set BRAIN_BACKUP_DIR in the systemd environment file}"
: "${AGE_RECIPIENT:?Set AGE_RECIPIENT to the public age recipient}"

for command in docker age tar; do
  command -v "$command" >/dev/null || { echo "Required command is missing: $command" >&2; exit 1; }
done

stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
mkdir -p "$BRAIN_BACKUP_DIR" "$BRAIN_DATA_ROOT/assets"
chmod 700 "$BRAIN_BACKUP_DIR"
compose=(docker compose --env-file "$BRAIN_DEPLOY_DIR/.env" -f "$BRAIN_DEPLOY_DIR/compose.yml")

"${compose[@]}" exec -T db sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  | age -r "$AGE_RECIPIENT" -o "$BRAIN_BACKUP_DIR/$stamp-postgres.dump.age" -

tar -C "$BRAIN_DATA_ROOT" -czf - assets \
  | age -r "$AGE_RECIPIENT" -o "$BRAIN_BACKUP_DIR/$stamp-assets.tar.gz.age" -

find "$BRAIN_BACKUP_DIR" -maxdepth 1 -type f -name '*.age' -mtime +30 -delete
echo "Encrypted database and media backups created: $stamp"
