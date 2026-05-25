from __future__ import annotations

from typing import Protocol

from server.models import PlaybackResource


class DeviceController(Protocol):
    async def play_audio_resource(self, resource: PlaybackResource) -> bool:
        ...


class InMemoryDeviceController:
    def __init__(self) -> None:
        self.played: list[PlaybackResource] = []

    async def play_audio_resource(self, resource: PlaybackResource) -> bool:
        self.played.append(resource)
        return True
