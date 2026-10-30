"""Hold unfinished words/numbers so sensitive prefixes cannot escape early."""

import re


REPLACEMENT = "[REDACTED]"
MAX_PENDING = 512
# Unquoted ASCII email characters, plus @. Keep them all before seeing the @.
WORD = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-]+")
WORD_TAIL = re.compile(WORD.pattern + "$")
NUMBER = re.compile(r"[0-9][0-9 -]*")
NUMBER_TAIL = re.compile(r"[0-9][0-9 -]*$")
SSN = re.compile(r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])")


class BufferLimitError(ValueError):
    pass


def redact_complete(text: str) -> str:
    # Mask the whole ASCII token containing @, including incomplete email-like text.
    text = WORD.sub(lambda match: REPLACEMENT if "@" in match[0] else match[0], text)

    def number(match):
        value = match[0]
        digits = sum("0" <= char <= "9" for char in value)
        if digits >= 13 or SSN.search(value):
            # Keep separators after the last digit so adjacent words stay readable.
            ending = value[len(value.rstrip(" -")):]
            return REPLACEMENT + ending
        return value

    return NUMBER.sub(number, text)


class Redactor:
    def __init__(self):
        self.pending = ""
        self.peak_pending = 0

    def feed(self, text: str) -> str:
        ready = []
        # Small slices bound working memory even when a delta is much longer.
        for offset in range(0, len(text), 64):
            combined = self.pending + text[offset:offset + 64]
            cut = len(combined)
            for pattern in (WORD_TAIL, NUMBER_TAIL):
                match = pattern.search(combined)
                if match:
                    cut = min(cut, match.start())
            # A trailing word can overlap the last group of a spaced card number.
            # Move the cut back until neither kind of candidate is split.
            spans = [(match.start(), match.end()) for pattern in (WORD, NUMBER)
                     for match in pattern.finditer(combined)]
            for start, end in sorted(spans, reverse=True):
                if start < cut < end:
                    cut = start
            unfinished = combined[cut:]
            if len(unfinished) > MAX_PENDING:
                raise BufferLimitError("Unfinished text limit exceeded")
            ready.append(redact_complete(combined[:cut]))
            self.pending = unfinished
            self.peak_pending = max(self.peak_pending, len(self.pending))
        return "".join(ready)

    def finish(self) -> str:
        # Only call this after the provider's explicit successful done event.
        result = redact_complete(self.pending)
        self.pending = ""
        return result
