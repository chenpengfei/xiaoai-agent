from __future__ import annotations

from typing import Protocol

from server.models import HermesResponse, HermesTurn


class HermesConnector(Protocol):
    async def ask(self, turn: HermesTurn) -> HermesResponse:
        ...


class StaticHermesConnector:
    def __init__(self, text: str) -> None:
        self.text = text
        self.turns: list[HermesTurn] = []

    async def ask(self, turn: HermesTurn) -> HermesResponse:
        self.turns.append(turn)
        return HermesResponse(text=self.text, should_speak=True, model="static")


class EchoHermesConnector:
    async def ask(self, turn: HermesTurn) -> HermesResponse:
        return HermesResponse(text=f"收到：{turn.user_text}", should_speak=True, model="echo")
