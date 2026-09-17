"""The sync client: pushing this installation's data to a sync service (#79).

Pulling shared bundles (#47) needs nothing but a static file host, and that
is why it exists. Contributing the other way — getting a survey from this
machine into the shared repo — was a clone-and-open-a-PR job, which is a
fine contribution path for a maintainer and no path at all for a driver who
just finished mapping a circuit. The sync service is the "somewhere to push
to": the logger uploads a track bundle whenever it autosaves, the service
files it, and a job in the data repo merges what people sent and opens the
pull requests. GitHub stays the source of truth; this package only ever
sends.

The client is one **transport** (the connection string, bearer auth, the
server's capabilities, error classification) plus one **adapter per data
type**, each of which decides what to send and when. Only the `tracks`
adapter exists so far; `sessions` and `live` arrive with their server halves
and reuse everything under `SyncClient`. The rules every adapter lives by:

- nothing is ever sent for a type whose toggle is off;
- nothing blocks the recorder or the UI — every upload is a background task
  with retry and backoff;
- the token is a secret: parsed once from the connection string, stored on
  its own, sent only as a header, masked in the UI, and never logged.
"""

from app.sync.client import SyncClient
from app.sync.connection import (
    BadConnectionString,
    Connection,
    check_token,
    mask_token,
    normalise_server,
    parse_connection_string,
)
from app.sync.transport import SyncError, Transport

__all__ = [
    "BadConnectionString",
    "Connection",
    "SyncClient",
    "SyncError",
    "Transport",
    "check_token",
    "mask_token",
    "normalise_server",
    "parse_connection_string",
]
