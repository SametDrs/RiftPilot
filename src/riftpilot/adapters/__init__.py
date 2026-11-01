"""RiftPilot Adapters — Inbound and Outbound infrastructure adapters."""

from riftpilot.adapters.lcu_discovery import (
    LcuCredentials,
    LcuDiscoverer,
    parse_cmdline_args,
    parse_lockfile,
)
from riftpilot.adapters.lcu_stream import (
    LcuEventStream,
    parse_lcu_event,
)
from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.adapters.web_hub import (
    INDEX_HTML,
    PairRequest,
    create_app,
)

__all__ = [
    "INDEX_HTML",
    "LcuCredentials",
    "LcuDiscoverer",
    "LcuEventStream",
    "MockLcuAdapter",
    "PairRequest",
    "RiotLcuAdapter",
    "create_app",
    "parse_cmdline_args",
    "parse_lcu_event",
    "parse_lockfile",
]
