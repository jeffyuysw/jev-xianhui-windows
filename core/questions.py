"""The Jev question set for message triage.

Ported 1:1 from 先回 (Android) `jev/PriorityQuestions.kt`, which in turn
follows the noul / choice / score contract of
https://github.com/jev-chat/jev-chat-jarvis

Jev only answers choice / score / true-false — it never writes prose. So the
four questions below have to carry the whole judgment: how urgent, whether it
needs a reply now, what kind of message it is, and why it is pressing.

Instructions and criteria stay in English (Jev's main training language);
the message text stays in its original Chinese.

This is the single file to edit when tuning judgment quality.
"""

from __future__ import annotations

from typing import Any

from .msg_item import MsgItem

# Shared tail: state fields are given context, not off-topic noise.
_NOTE = " Facts given in the state are provided context, not off-topic."


def _noul(instructions: str, true_desc: str, false_desc: str) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": instructions + _NOTE,
        "criteria": {"true": true_desc, "false": false_desc},
    }


def _choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": instructions + _NOTE,
        "criteria": criteria,
    }


def _score(instructions: str, levels: list[str]) -> dict[str, Any]:
    return {
        "type": "score",
        "instructions": instructions + _NOTE,
        "criteria": levels,
    }


def state(item: MsgItem) -> dict[str, Any]:
    return {
        "app": item.app_name,
        "conversation": item.sender,
        "message": item.text,
    }


def questions() -> dict[str, Any]:
    """All four questions in one request.

    Jev answers them together in about a second, which is what makes
    per-message triage cheap enough to run on every captured message.
    """
    return {
        "urgency_level": _score(
            "How urgently does this message need the recipient's attention right now? "
            "Judge only what this message actually shows: its content, any deadline or time "
            "pressure it contains, and whether another person is waiting on the recipient. "
            "Do not invent context that is not present.",
            [
                "Pure noise: advertising, marketing pushes, subscription notices, verification codes, "
                "or system alerts. No human is waiting for a reply.",
                "Low priority: casual chatter, emoji reactions, memes, or broadcast content "
                "nobody expects an answer to.",
                "Informational: news, sharing, or general updates. No action is needed today.",
                "Routine: everyday coordination that can comfortably be answered within several hours.",
                "Worth attention: someone asks something concrete and reasonably expects a reply today, "
                "but nothing breaks if it waits a few hours.",
                "Time-sensitive: contains a specific time, place, or deadline today, or someone's "
                "next step depends on this answer.",
                "Urgent: an explicit deadline within hours, another person is blocked waiting, "
                "or a commitment already made is at risk.",
                "Very urgent: money, payments, account or security issues, health, travel, or a broken "
                "commitment. Delay causes real loss or real damage to trust.",
                "Emergency: accident, medical, safety, or a crisis that must be handled immediately.",
            ],
        ),
        "needs_reply_now": _noul(
            "Does this message require the recipient to reply or act within the next few minutes?",
            "Someone is actively waiting for an answer, there is a live deadline, another person's "
            "work or plan is blocked until this is answered, or the sender's tone clearly demands "
            "prompt acknowledgement.",
            "It can safely wait hours or until tomorrow; nobody is blocked; it is informational, "
            "promotional, automated, or a one-way broadcast.",
        ),
        "category": _choice(
            "What kind of message is this? Choose the single best fit.",
            {
                "work_blocking": "Work or business: a task, approval, deliverable, or decision "
                "that another person is waiting on.",
                "personal": "Personal: from family, a partner, or a close friend. Carries emotional "
                "weight or relationship meaning.",
                "logistics": "Logistics: arranging a time, place, meeting, delivery, or plan.",
                "info_only": "Information only: an FYI, announcement, or share. No reply is expected.",
                "promotion": "Promotion: advertising, marketing, subscription, or shopping pushes.",
                "system": "System: verification codes, app alerts, delivery updates, or automated notices.",
                "money": "Money: payment, transfer, invoice, refund, or anything financial.",
            },
        ),
        "why_urgent": _choice(
            "What makes this message pressing, if anything? "
            "Choose none when there is no real time pressure.",
            {
                "deadline": "It carries an explicit time limit or deadline.",
                "someone_waiting": "Another person cannot proceed until the recipient answers.",
                "emotional": "The sender is upset, anxious, or needs to be acknowledged emotionally.",
                "money_risk": "Money, an account, security, health, or safety is at risk.",
                "none": "No real pressure. It can wait without consequence.",
            },
        ),
    }
