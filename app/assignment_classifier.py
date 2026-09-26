"""Explainable academic-assignment scoring; generic keywords never suffice."""

from dataclasses import dataclass
from email.utils import parseaddr
from fnmatch import fnmatchcase
import re

from app.assignment_parser import ParsedEmail, sender_allowed
from app.config import GmailSettings

# All weights and caps are centralized. Evidence counts once, not per occurrence.
STRONG_WORD_WEIGHT = 6
STRONG_PHRASE_WEIGHT = 8
SUBJECT_BONUS = 1
ACTION_BONUS = 2
NUMBERED_TASK_BONUS = 2
WEAK_WEIGHT = 1
WEAK_CAP = 3
TRUST_BONUS = 2
UNKNOWN_SENDER_MARGIN = 2
NEGATIVE_SUBJECT_WEIGHT = 5
NEGATIVE_BODY_WEIGHT = 1
NEGATIVE_CAP = 15

STRONG_WORDS = ("assignment", "homework", "coursework")
STRONG_PHRASES = (
    "new assignment", "assignment uploaded", "assignment posted", "assignment deadline",
    "assignment due", "submit assignment", "quiz uploaded", "quiz available",
    "LMS assignment", "assessment uploaded", "graded task",
)
WEAK_SIGNALS = (
    "course", "class", "lecture", "semester", "instructor", "teacher", "faculty",
    "section", "submission portal", "marks", "grade", "lab",
)
NEGATIVE_SIGNALS = {
    "fee terminology": r"fees?", "payment terminology": r"payment(?:\s+due)?",
    "invoice terminology": r"invoices?", "tuition terminology": r"tuition",
    "bank terminology": r"bank", "transaction terminology": r"transactions?",
    "billing terminology": r"billing", "subscription terminology": r"subscriptions?",
    "promotion terminology": r"promotions?", "sale terminology": r"sales?",
    "discount terminology": r"discounts?", "newsletter terminology": r"newsletters?",
    "unsubscribe terminology": r"unsubscribe", "shipping terminology": r"shipping",
    "order terminology": r"orders?", "reviews terminology": r"reviews?",
    "marketing terminology": r"marketing", "offer terminology": r"offers?",
    "purchase terminology": r"purchases?",
}
TASK = r"(?:assignment|homework|coursework|quiz|assessment)"
ACTION = re.compile(rf"\b(?:{TASK}(?:\s+(?:\#?\d+|is|has|been|was))*\s+(?:uploaded|posted|available|due|deadline)|submit\s+(?:your\s+)?{TASK}|new\s+{TASK})\b", re.I)
NUMBERED_TASK = re.compile(rf"\b{TASK}\s*\#?\d+\b", re.I)


@dataclass(frozen=True)
class ClassificationResult:
    classification: str
    score: int
    reasons: list[str]
    threshold: int
    likely_candidate: bool


def _contains(text: str, phrase: str) -> bool:
    pattern = r"\s+".join(re.escape(word) for word in phrase.split())
    return bool(re.search(r"(?<!\w)" + pattern + r"(?!\w)", text, re.I))


def trusted_sender(sender: str, settings: GmailSettings) -> bool:
    address = parseaddr(sender)[1].lower()
    if "@" not in address:
        return False
    domain = address.rpartition("@")[2]
    return any(fnmatchcase(address, pattern.lower()) for pattern in settings.trusted_senders) or any(
        fnmatchcase(domain, pattern.lower().lstrip("@"))
        or (not any(char in pattern for char in "*?[") and domain.endswith("." + pattern.lower().lstrip("@")))
        for pattern in settings.trusted_domains
    )


def classify_email(email: ParsedEmail, settings: GmailSettings) -> ClassificationResult:
    text = email.subject + "\n" + email.body
    reasons = []
    score = 0
    phrase = next((value for value in STRONG_PHRASES if _contains(text, value)), None)
    word = next((value for value in STRONG_WORDS if _contains(text, value)), None)
    # Numbered quiz/assessment announcements have equivalent specific evidence.
    structured = ACTION.search(text)
    strong = bool(phrase or word or structured)
    if phrase or word or structured:
        label = phrase or word or "task announcement"
        weight = STRONG_PHRASE_WEIGHT if phrase else STRONG_WORD_WEIGHT
        score += weight
        reasons.append(f"strong phrase: {label} (+{weight})")
    else:
        reasons.append("no assignment-specific phrase (required)")
    subject_strong = any(_contains(email.subject, value) for value in (*STRONG_WORDS, *STRONG_PHRASES)) or bool(ACTION.search(email.subject))
    if subject_strong:
        score += SUBJECT_BONUS
        reasons.append(f"assignment evidence in subject (+{SUBJECT_BONUS})")
    if structured:
        score += ACTION_BONUS
        reasons.append(f"assignment-specific posting/submission/deadline action (+{ACTION_BONUS})")
    if NUMBERED_TASK.search(text):
        score += NUMBERED_TASK_BONUS
        reasons.append(f"numbered academic task (+{NUMBERED_TASK_BONUS})")
    weak = [value for value in WEAK_SIGNALS if _contains(text, value)]
    if weak:
        points = min(len(weak) * WEAK_WEIGHT, WEAK_CAP)
        score += points
        reasons.append(f"academic context: {', '.join(weak)} (+{points}, capped)")
    trusted = trusted_sender(email.sender, settings)
    threshold = settings.score_threshold + (0 if trusted else UNKNOWN_SENDER_MARGIN)
    if trusted:
        score += TRUST_BONUS
        reasons.append(f"trusted academic sender (+{TRUST_BONUS})")
    else:
        reasons.append(f"unknown sender: stronger evidence required (threshold {threshold})")
    penalty = 0
    for label, pattern in NEGATIVE_SIGNALS.items():
        expression = r"\b(?:" + pattern + r")\b"
        in_subject = bool(re.search(expression, email.subject, re.I))
        if in_subject or re.search(expression, email.body, re.I):
            amount = min(NEGATIVE_SUBJECT_WEIGHT if in_subject else NEGATIVE_BODY_WEIGHT, NEGATIVE_CAP - penalty)
            penalty += amount
            reasons.append(f"{label} ({'subject' if in_subject else 'body'}, -{amount})")
    score -= penalty
    allowed = sender_allowed(email.sender, settings)
    if not allowed:
        reasons.append("excluded by configured sender filter")
    accepted = allowed and strong and score >= threshold
    if strong and score < threshold:
        reasons.append(f"score below required threshold {threshold}")
    hints = any(_contains(text, value) for value in settings.keywords)
    # Keywords are diagnostic candidate hints only, never an acceptance shortcut.
    return ClassificationResult("ASSIGNMENT" if accepted else "REJECTED", score, reasons,
                                threshold, bool(strong or weak or penalty or hints))
