# Shared by every script under bench/adapters/: source it, do not run it.
#
#     source "$(dirname "${BASH_SOURCE[0]}")/../_env.sh"      # from bench/adapters/<system>/<script>.sh
#
# It resolves the places a script needs, from its own location, so nothing here depends on one machine:
#
#   FMG_REPO          the repository root. Default: two levels above this file, in the drive-letter form
#                     (`cygpath -m`) when cygpath exists, because docker on Windows needs that form for -v.
#   FMG_BENCH_HOME    scratch and third-party space OUTSIDE the repository. Default: a folder called
#                     feiyu-memgate-bench next to the repository. Build contexts live in
#                     $FMG_BENCH_HOME/work/<system>, sources you fetched yourself may sit in
#                     $FMG_BENCH_HOME/vendor/<name>.
#   FMG_LLM_ENV_FILE  an env file that defines DEEPSEEK_API_KEY, only for the two paid modes (mem0 with LLM
#                     extraction, graphiti). Default: $FMG_BENCH_HOME/deepseek.env. Never printed or copied.
#
# Every third-party source has ONE variable with no default, pointing at the folder where you unpacked it
# (see bench/README.md, section "复现"):
#
#   MEOW_MEMORY_PKG   MNEMON_SRC   LINGSHU_PKG   MEMSEARCH_SRC   SEEKMEMORY_SRC
#
# `fmg_need_dir VAR "what it is and where to get it"` stops the script with a one-line instruction when the
# variable is unset or not a directory.

_fmg_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_fmg_root="$(cd "$_fmg_here/../.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  _fmg_root="$(cygpath -m "$_fmg_root")"
fi
FMG_REPO="${FMG_REPO:-$_fmg_root}"
FMG_BENCH_HOME="${FMG_BENCH_HOME:-$(dirname "$FMG_REPO")/feiyu-memgate-bench}"
FMG_LLM_ENV_FILE="${FMG_LLM_ENV_FILE:-$FMG_BENCH_HOME/deepseek.env}"
export FMG_REPO FMG_BENCH_HOME FMG_LLM_ENV_FILE
unset _fmg_here _fmg_root

fmg_need_dir() {  # fmg_need_dir VAR "instruction"
  local name=$1 hint=$2 value
  value="${!name:-}"
  if [ -z "$value" ]; then
    echo "error: $name is not set: $hint" >&2
    exit 2
  fi
  if [ ! -d "$value" ]; then
    echo "error: $name=$value is not a directory: $hint" >&2
    exit 2
  fi
}

fmg_need_llm_env() {  # the paid modes only
  if [ ! -f "$FMG_LLM_ENV_FILE" ]; then
    echo "error: FMG_LLM_ENV_FILE=$FMG_LLM_ENV_FILE does not exist: put DEEPSEEK_API_KEY=... in an env file and set FMG_LLM_ENV_FILE (this mode makes paid API calls)" >&2
    exit 2
  fi
}
