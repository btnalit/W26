#!/usr/bin/env bash
set -euo pipefail

/hermesdata/media-publish/scripts/start_cloakbrowser_cdp.sh
/hermesdata/media-publish/scripts/browser_stack_status.sh

BROWSER_HARNESS="${BROWSER_HARNESS:-$(command -v browser-harness 2>/dev/null || true)}"
if [[ -z "${BROWSER_HARNESS}" && -x /root/.local/bin/browser-harness ]]; then
  BROWSER_HARNESS=/root/.local/bin/browser-harness
fi
if [[ -z "${BROWSER_HARNESS}" ]]; then
  echo "error: browser-harness not found on PATH or /root/.local/bin/browser-harness" >&2
  exit 127
fi

"${BROWSER_HARNESS}" <<'PY'
ensure_real_tab()
new_tab("https://example.com")
wait_for_load()
print(page_info())
print(js("document.title"))
PY
