from __future__ import annotations

import re


MAX_RECIPIENTS = 50
_EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


def normalize_recipients(value: str, limit: int = MAX_RECIPIENTS) -> list[str]:
    """Validate and deduplicate a user-entered recipient list."""
    recipients: list[str] = []
    seen: set[str] = set()
    for raw_item in re.split(r"[,，;；\r\n]+", value or ""):
        address = raw_item.strip()
        if not address:
            continue
        if not _EMAIL_PATTERN.fullmatch(address):
            raise ValueError(f"收件邮箱格式无效：{address[:80]}")
        identity = address.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        recipients.append(address)
        if len(recipients) > limit:
            raise ValueError(f"收件邮箱最多可填写 {limit} 个。")
    if not recipients:
        raise ValueError("请至少填写一个有效的收件邮箱。")
    return recipients
