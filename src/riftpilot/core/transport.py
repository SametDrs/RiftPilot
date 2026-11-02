"""RiftPilot Core Domain — Transport-Agnostic Messaging & WAN Tunneling (ADR-0003).

Defines:
- `MessageEnvelope`: Standard payload envelope bridging LAN, WAN relay, and WebRTC.
- `TransportProtocol`: Supported transport protocol layers.
- `TunnelStatus`: Operational lifecycle states for WAN relay tunnels.
- `TunnelInfo`: Active WAN public URL, provider, and diagnostic telemetry.
- `ITunnelPort`: Abstract port contract for tunnel providers.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class TransportProtocol(str, Enum):
    """Transport protocol classification."""

    LAN_HTTP_WS = "LAN_HTTP_WS"   # Local LAN direct HTTP + WebSocket
    WAN_RELAY = "WAN_RELAY"       # Internet WAN secure reverse tunnel / relay
    WEBRTC = "WEBRTC"             # Direct P2P WebRTC data channel


class TunnelStatus(str, Enum):
    """WAN tunnel runtime lifecycle status."""

    DISCONNECTED = "Disconnected"
    STARTING = "Starting"
    CONNECTED = "Connected"
    FAILED = "Failed"
    STOPPED = "Stopped"


class MessageEnvelope(BaseModel):
    """Transport-agnostic message wrapper (ADR-0003)."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(..., description="Action or event type identifier")
    payload: dict[str, Any] = Field(default_factory=dict, description="Command or event data")
    token: str = Field("", description="Zero-Trust authentication session token")
    client_id: str = Field("", description="Client identifier")
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of message creation")
    protocol: TransportProtocol = Field(default=TransportProtocol.LAN_HTTP_WS, description="Originating transport protocol")


class TunnelInfo(BaseModel):
    """Information and connection diagnostics of an active WAN tunnel."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field("Cloudflare", description="Tunnel provider name (e.g. Cloudflare, LocalTunnel, Mock)")
    status: TunnelStatus = Field(default=TunnelStatus.DISCONNECTED, description="Current tunnel status")
    public_url: str = Field("", description="Public HTTPS endpoint URL accessible from WAN")
    is_active: bool = Field(False, description="True if tunnel is currently forwarding traffic")
    error_message: str = Field("", description="Failure reason if status is FAILED")
    latency_ms: float = Field(0.0, description="Round-trip ping latency to relay endpoint")


class ITunnelPort(ABC):
    """Outbound port abstract base class for WAN tunnel providers (ADR-0003)."""

    @abstractmethod
    async def start_tunnel(self, local_port: int) -> TunnelInfo:
        """Start a secure reverse tunnel pointing to the local port."""
        ...

    @abstractmethod
    async def stop_tunnel(self) -> bool:
        """Gracefully stop and tear down the active reverse tunnel."""
        ...

    @abstractmethod
    async def get_tunnel_info(self) -> TunnelInfo:
        """Return the current tunnel connection status and public URL."""
        ...


__all__ = [
    "TransportProtocol",
    "TunnelStatus",
    "MessageEnvelope",
    "TunnelInfo",
    "ITunnelPort",
]
