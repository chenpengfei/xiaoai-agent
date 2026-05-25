from __future__ import annotations

from voice_gateway.models import DialogueState
from voice_gateway.observability.events import EventLogger, JsonLineEventLogger


class DialogueStateMachine:
    _ALLOWED = {
        DialogueState.IDLE: {DialogueState.LISTENING},
        DialogueState.LISTENING: {DialogueState.ENDPOINTING, DialogueState.THINKING, DialogueState.IDLE},
        DialogueState.ENDPOINTING: {DialogueState.THINKING, DialogueState.IDLE},
        DialogueState.THINKING: {DialogueState.LISTENING, DialogueState.SPEAKING, DialogueState.IDLE},
        DialogueState.SPEAKING: {DialogueState.FOLLOWUP_WAIT, DialogueState.IDLE},
        DialogueState.FOLLOWUP_WAIT: {DialogueState.LISTENING, DialogueState.IDLE},
    }

    def __init__(self, events: EventLogger = JsonLineEventLogger()) -> None:
        self.state = DialogueState.IDLE
        self.events = events

    def transition(self, target: DialogueState, *, reason: str, **fields: object) -> None:
        if target == self.state:
            return
        allowed = self._ALLOWED.get(self.state, set())
        if target not in allowed:
            raise ValueError(f"invalid dialogue transition: {self.state.value} -> {target.value}")
        previous = self.state
        self.state = target
        self.events.emit(
            "dialogue.transition",
            **fields,
            **{"from": previous.value, "to": target.value, "reason": reason},
        )
