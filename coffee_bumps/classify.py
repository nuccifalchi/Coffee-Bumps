"""TypeSafe judgments about an email, and the policy that turns them into an action."""

from dataclasses import dataclass

from typesafe_sdk import Choice, Noul, TypeSafeClient

from coffee_bumps.config import Config
from coffee_bumps.mail import Email

QUESTIONS = {
    "category": Choice(
        instructions="Which kind of email is `email`? Judge by who sent it and what it is for, not by single keywords.",
        criteria={
            "personal": "Written by a real person to the recipient: friends, family, colleagues, or anyone writing to them individually.",
            "receipts": "Order confirmations, receipts, invoices for purchases already made, shipping and delivery updates, returns.",
            "finance": "Banking, credit cards, bills and statements, payments due, taxes, insurance, investments.",
            "newsletters": "Marketing, promotions, sales, newsletters, digests, and other bulk mail the recipient subscribed to.",
            "notifications": "Automated account messages: sign-in and security alerts, password resets, app and social media notifications, service updates.",
            "travel": "Flight, train, hotel, rental car, or event bookings, itineraries, check-in reminders, and tickets.",
            "other": "None of the other categories clearly fits.",
        },
    ),
    "needs_action": Noul(
        instructions=(
            "Does `email` ask the recipient, personally, to do something soon, such as reply, confirm, decide, "
            "pay a bill that is due, or meet a deadline? Generic marketing calls to action do not count."
        ),
        criteria={
            "true": "The recipient is expected to respond or act on this specific message.",
            "false": "The message is informational, or any call to action is generic promotion.",
        },
    ),
}


@dataclass(frozen=True)
class Judgment:
    category: str
    category_confidence: float
    needs_action: float


@dataclass(frozen=True)
class Action:
    """What to do with one email: move it to `folder`, or leave it in the inbox (`folder` is None)."""

    folder: str | None
    flag: bool
    reason: str


def email_state(message: Email) -> dict:
    return {
        "email": {
            "from": message.sender,
            "to": message.to,
            "date": message.date,
            "subject": message.subject,
            "has_unsubscribe_link": message.has_unsubscribe_link,
            "body": message.body,
        }
    }


def judge(client: TypeSafeClient, message: Email) -> Judgment:
    response = client.system_one(state=email_state(message), questions=QUESTIONS)
    category = response.choices["category"]
    return Judgment(
        category=category.choice,
        category_confidence=category.confidence,
        needs_action=response.nouls["needs_action"].noul,
    )


def decide(judgment: Judgment, config: Config) -> Action:
    if judgment.needs_action >= config.needs_action_threshold:
        return Action(folder=None, flag=True, reason=f"needs action ({judgment.needs_action:.0%} sure)")
    folder = config.folders.get(judgment.category)
    if folder is None:
        return Action(folder=None, flag=False, reason=f"{judgment.category} stays in inbox")
    if judgment.category_confidence < config.min_category_confidence:
        return Action(
            folder=None,
            flag=False,
            reason=f"unsure: maybe {judgment.category} ({judgment.category_confidence:.0%} sure)",
        )
    return Action(folder=folder, flag=False, reason=f"{judgment.category} ({judgment.category_confidence:.0%} sure)")
