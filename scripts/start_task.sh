#!/usr/bin/env bash
# Create an isolated branch + worktree for a task (AGENTS.md §1.6).
# Usage: scripts/start_task.sh T-005 order-state [base-ref]
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "usage: $0 T-xxx short-description [base-ref]" >&2
  exit 2
fi
task="$1"; slug="$2"; base="${3:-origin/main}"
[[ "$task" =~ ^T-[0-9]{3}$ ]] || { echo "task id must look like T-005" >&2; exit 2; }
[[ "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "description: lowercase letters, digits, dashes" >&2; exit 2; }
root="$(git rev-parse --show-toplevel)"
branch="agent/${task}-${slug}"
target="$(dirname "$root")/wt-${task}"
if git -C "$root" show-ref --verify --quiet "refs/heads/${branch}"; then
  echo "branch ${branch} already exists – another agent may own this task; check the issue first" >&2
  exit 1
fi
[[ -e "$target" ]] && { echo "${target} already exists" >&2; exit 1; }
git -C "$root" worktree add -b "$branch" "$target" "$base"
mkdir -p "$target/.tmp"
echo "worktree: $target"
echo "branch:   $branch"
echo "next: claim the issue, create docs/handoffs/${task}.md from the template, run make check"
