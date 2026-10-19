"""Synthetic OpenRouter advisor SSE streams shared by the advisor job tests."""

import json

import httpx


def event(kind, **fields):
    return ("data: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


def final(text="Final advice", status="completed", container=None):
    body = {"status": status, "output": [{"type": "message", "role": "assistant",
            "phase": "final_answer", "status": "completed", "content": [
                {"type": "output_text", "text": text}]}], "usage": {"cost": 0.002}}
    if container:
        body["output"].append({"type": "openrouter:shell", "container_id": container})
    return body


class SyntheticStream(httpx.AsyncByteStream):
    """A scripted advisor stream; ``state['cancelled']`` counts how often it was closed."""

    def __init__(self, state):
        self.state = state
        self.events = None

    def __aiter__(self):
        self.events = self._events()
        return self.events

    async def aclose(self):
        # httpx closes the response stream, wherever the job's cancellation lands.
        if self.events is not None:
            await self.events.aclose()

    async def _events(self):
        try:
            yield event("response.output_item.added", output_index=0, item={
                "type": "message", "role": "assistant", "phase": "final_answer"})
            yield event("response.reasoning_text.delta", delta="SECRET_REASONING")
            yield event("response.output_text.delta", output_index=0, delta=self.state["partial"])
            self.state["entered"].set()
            await self.state["release"].wait()
            if self.state.get("raw"):
                for raw in self.state["raw"]:
                    yield raw
            elif self.state.get("malformed"):
                yield b"data: invalid JSON\n\n"
            elif self.state.get("oversized"):
                yield b"data: " + b"x" * 300_000 + b"\n\n"
            elif not self.state.get("no_final"):
                yield event("response.completed", response=final(
                    self.state["answer"], self.state.get("final_status", "completed"),
                    self.state.get("container")))
        finally:
            self.state["cancelled"] += 1
