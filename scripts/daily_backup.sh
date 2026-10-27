#!/usr/bin/env bash
# Root-owned installed copy: stop one server, run the existing backup as its user,
# then restart even when create/verify fails. No cloud calls or deletion/rotation.
set -euo pipefail
umask 077

usage() {
  echo 'usage: daily_backup.sh --repo-dir PATH --protocol-data-dir PATH --workspace-data-dir PATH --report-database PATH --backup-dir PATH [--service NAME] [--user NAME]'
}
repo_dir= protocol_dir= workspace_dir= report_db= backup_dir=
service_name=voineylab.service
service_user=voineylab
while (( $# )); do
  case "$1" in
    --help) usage; exit 0 ;;
    --repo-dir|--protocol-data-dir|--workspace-data-dir|--report-database|--backup-dir|--service|--user)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      case "$1" in
        --repo-dir) repo_dir=$2 ;;
        --protocol-data-dir) protocol_dir=$2 ;;
        --workspace-data-dir) workspace_dir=$2 ;;
        --report-database) report_db=$2 ;;
        --backup-dir) backup_dir=$2 ;;
        --service) service_name=$2 ;;
        --user) service_user=$2 ;;
      esac
      shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
for path_name in repo_dir protocol_dir workspace_dir report_db backup_dir; do
  selected_path=${!path_name}
  resolved_path=$(realpath -m -- "$selected_path" 2>/dev/null) || exit 2
  [[ "$selected_path" == /* && "$selected_path" != / && "$selected_path" == "$resolved_path" ]] || {
    echo 'ERROR: canonical absolute non-root paths required (no links or dot components)' >&2; exit 2;
  }
done
for protected_path in "$repo_dir" "$protocol_dir" "$workspace_dir"; do
  if [[ "$backup_dir" == "$protected_path" || "$backup_dir" == "$protected_path/"* || "$protected_path" == "$backup_dir/"* ]]; then
    echo 'ERROR: backup directory must be separate from the server and data trees' >&2; exit 2
  fi
done
[[ "$service_name" =~ ^[a-zA-Z0-9_-]+\.service$ && "$service_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 2
[[ -x "$repo_dir/.venv/bin/python" && -f "$repo_dir/scripts/pilot_state_backup.py" ]] || exit 2
# The daily pilot bundle must contain all four authorities, never an empty subset.
for database in "$protocol_dir/protocol_workspace.sqlite" "$workspace_dir/accounts.sqlite" \
                "$workspace_dir/commercial_workspace.sqlite" "$report_db"; do
  [[ -f "$database" && ! -L "$database" ]] || { echo 'ERROR: required store missing' >&2; exit 1; }
done
install -d -m 700 -o "$service_user" "$backup_dir"
exec 9>"$backup_dir/.daily.lock"
flock -n 9 || { echo 'ERROR: backup already running' >&2; exit 1; }
# An intentional maintenance stop must remain stopped.
systemctl is-active --quiet "$service_name" || { echo 'ERROR: server is not active; backup skipped' >&2; exit 1; }
restart_required=true
restart_server() {
  result=$?
  trap - EXIT
  if [[ "$restart_required" == true ]]; then
    if ! systemctl start "$service_name"; then
      echo 'ERROR: server restart failed; operator action required' >&2
      result=1
    fi
  fi
  exit "$result"
}
trap restart_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
systemctl stop "$service_name"
archive="$backup_dir/pilot-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
runuser -u "$service_user" -- "$repo_dir/.venv/bin/python" "$repo_dir/scripts/pilot_state_backup.py" create \
  --protocol-data-dir "$protocol_dir" --workspace-data-dir "$workspace_dir" \
  --report-database "$report_db" --output "$archive"
runuser -u "$service_user" -- "$repo_dir/.venv/bin/python" "$repo_dir/scripts/pilot_state_backup.py" verify "$archive"
echo 'OK: daily backup created and verified'
