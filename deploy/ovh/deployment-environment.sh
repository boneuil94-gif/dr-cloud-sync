#!/usr/bin/env bash

# Source this file before any command which reads docker-compose.yml.  The
# orchestrator is the authoritative initializer; direct invocations of the
# operational scripts use the same repository-anchored default.
deployment_script_dir="$(cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment_repo="$(git -C "$deployment_script_dir" rev-parse --show-toplevel)"
deployment_state_dir="${DRCLOUD_DEPLOYMENT_STATE_DIR:-$deployment_script_dir/.deployment-state}"
[[ "$deployment_state_dir" == /* ]] || deployment_state_dir="$deployment_repo/$deployment_state_dir"
[[ -d "$deployment_state_dir" ]] || {
  echo "ERREUR: préparer $deployment_state_dir avec prepare-deployment-state.sh." >&2
  return 1 2>/dev/null || exit 1
}
deployment_state_dir="$(cd -P -- "$deployment_state_dir" && pwd)"
export DRCLOUD_DEPLOYMENT_STATE_DIR="$deployment_state_dir"
unset deployment_script_dir deployment_repo deployment_state_dir
