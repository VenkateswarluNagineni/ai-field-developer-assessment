"""Hold unfinished words/numbers so sensitive prefixes cannot escape early."""

import re
import unicodedata


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


def scan_view(text: str) -> tuple[str, list[int]]:
    # Ignore format controls for detection, but retain positions in the original.
    positions = [index for index, char in enumerate(text) if unicodedata.category(char) != "Cf"]
    return "".join(text[index] for index in positions), positions


def redact_complete(text: str) -> str:
    visible, positions = scan_view(text)
    spans = [(match.start(), match.end()) for match in WORD.finditer(visible) if "@" in match[0]]
    for match in NUMBER.finditer(visible):
        value = match[0]
        digits = sum("0" <= char <= "9" for char in value)
        if digits >= 13 or SSN.search(value):
            # Keep separators after the last digit so adjacent words stay readable.
            spans.append((match.start(), match.start() + len(value.rstrip(" -"))))

    # An email-like token and number may overlap. Mask their combined span once.
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))

    result = []
    cursor = 0
    for start, end in merged:
        raw_start, raw_end = positions[start], positions[end - 1] + 1
        result.extend((text[cursor:raw_start], REPLACEMENT))
        cursor = raw_end
    result.append(text[cursor:])
    return "".join(result)


class Redactor:
    def __init__(self):
        self.pending = ""
        self.peak_pending = 0

    def feed(self, text: str) -> str:
        ready = []
        # Small slices bound working memory even when a delta is much longer.
        for offset in range(0, len(text), 64):
            combined = self.pending + text[offset:offset + 64]
            visible, positions = scan_view(combined)
            cut = len(visible)
            for pattern in (WORD_TAIL, NUMBER_TAIL):
                match = pattern.search(visible)
                if match:
                    cut = min(cut, match.start())
            # A trailing word can overlap the last group of a spaced card number.
            # Move the cut back until neither kind of candidate is split.
            spans = [(match.start(), match.end()) for pattern in (WORD, NUMBER)
                     for match in pattern.finditer(visible)]
            for start, end in sorted(spans, reverse=True):
                if start < cut < end:
                    cut = start
            # Map the safe boundary back before releasing any original characters.
            cut = positions[cut] if cut < len(positions) else len(combined)
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
