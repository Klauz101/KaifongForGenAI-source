"""Strip identifiers before any text leaves for an external LLM (SPEC 7.3)."""

import re
from dataclasses import dataclass, field

HKID = re.compile(r"\b[A-Z]{1,2}\d{6}\(?[0-9A]\)?")
NAME_TOKEN = "[ELDER]"
PHONE_TOKEN = "[PHONE]"
ID_TOKEN = "[ID]"
ADDRESS_TOKEN = "[ADDRESS]"
# Typical Hong Kong address fragments: "Flat 12B, 3/F", "Block 4", "12樓B室", "X邨/苑/花園/大廈", "123 Nathan Road".
ADDRESS = re.compile(
    r"(?i)\b(?:flat|room|rm|unit|block|blk)\s*[a-z0-9\-/]+(?:[\s,]+(?:\d+\s*/?\s*f(?:loor)?|floor\s*\d+))?"
    r"|\b\d{1,4}\s+[A-Za-z][A-Za-z ]{1,25}\s(?:road|street|st|avenue|lane|drive)\b"
    r"|[0-9一二三四五六七八九十]+\s*[座樓楼室號号](?:\s*[A-Za-z]?\s*[室座])?"
    r"|[\u4e00-\u9fff]{1,6}(?:邨|苑|花園|大廈|閣|樓)"
)


@dataclass
class Pseudonymizer:
    full_name: str
    phone: str
    display_names: tuple[str, ...] = ()
    _patterns: list[tuple[re.Pattern, str]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        shown = " ".join(self.display_names).lower()
        names = [self.full_name.strip()] + [
            t
            for t in re.split(r"\s+", self.full_name.strip())
            if len(t) >= 3 and t.lower() not in shown
        ]
        for n in sorted({x for x in names if x}, key=len, reverse=True):
            self._patterns.append((re.compile(re.escape(n), re.IGNORECASE), NAME_TOKEN))
        digits = re.sub(r"\D", "", self.phone)
        if len(digits) >= 6:
            sep = r"[\s\-]?"
            self._patterns.append((re.compile(sep.join(re.escape(d) for d in digits)), PHONE_TOKEN))

    def scrub(self, text: str) -> str:
        for pattern, token in self._patterns:
            text = pattern.sub(token, text)
        text = HKID.sub(ID_TOKEN, text)
        return ADDRESS.sub(ADDRESS_TOKEN, text)

    def rehydrate(self, text: str, display_name: str) -> str:
        """Put the display name back; drop any other placeholder the model echoed."""
        text = text.replace(NAME_TOKEN, display_name)
        return text.replace(PHONE_TOKEN, "").replace(ID_TOKEN, "").replace(ADDRESS_TOKEN, "")
