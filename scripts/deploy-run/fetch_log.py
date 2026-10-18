"""Fetch the live HA log over the API (the SMB share serves stale pages after a restart)."""

import sys
import types
import urllib.request

sys.path.insert(0, ".")
from scripts.ha_entity_dump import resolve_credentials

url, token = resolve_credentials(types.SimpleNamespace(url=None, token=None))
req = urllib.request.Request(
    f"{url}/api/error_log", headers={"Authorization": f"Bearer {token}"}
)
open(sys.argv[1], "wb").write(urllib.request.urlopen(req, timeout=60).read())
