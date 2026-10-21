"""Synthetic confirmation adapters. Not a model client."""

from __future__ import annotations

import json


class FakeConfirmationAdapter:
    seen: list[dict] = []

    def confirm_related_work(self, packet: dict) -> dict:
        FakeConfirmationAdapter.seen.append(packet)
        return {
            "confirmations": [
                {
                    "external_id": item.get("external_id", ""),
                    "title": item.get("title", ""),
                    "confirmed": True,
                    "reason": "Same task and temporal fusion on a sequence-level holdout.",
                }
                for item in packet.get("candidates", [])
            ]
        }


class RecordingModelSession:
    judgments: list[str] = []
    prompts: list[str] = []

    def __init__(self, **_kwargs: object) -> None:
        return None

    def _exchange(self, judgment: str, messages: list[dict], *, attempt: int) -> str:
        RecordingModelSession.judgments.append(judgment)
        user = messages[-1]["content"]
        RecordingModelSession.prompts.append(user if isinstance(user, str) else "")
        self.attempt = attempt
        return json.dumps(
            {
                "confirmations": [
                    {
                        "title": "Ribbon Fuse Estimator",
                        "external_id": "arxiv:0000.00077",
                        "confirmed": True,
                        "reason": "Named as the closest method.",
                    }
                ]
            }
        )

    def write_index(self) -> None:
        return None


class BareConfirmationAdapter:
    """No confirm_related_work method, so ModelSession on this module is used."""


ModelSession = RecordingModelSession
