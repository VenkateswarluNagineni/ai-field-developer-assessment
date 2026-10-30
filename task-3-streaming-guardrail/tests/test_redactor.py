import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from redactor import MAX_PENDING, BufferLimitError, Redactor, redact_complete


EXAMPLES = [
    ("Email alex@example.com; thanks!", "Email [REDACTED]; thanks!"),
    ("alex+support@example.co.uk", "[REDACTED]"),
    ("o'hara@example.com", "[REDACTED]"),
    ("first!#$%&'*+-/=?^_`{|}~last@example.com", "[REDACTED]"),
    ("contact alex@example.com! ", "contact [REDACTED] "),
    ("SSN: 123-45-6789", "SSN: [REDACTED]"),
    ("Card: 4111111111111111", "Card: [REDACTED]"),
    ("Card: 4111 1111 1111 1111.", "Card: [REDACTED]."),
    ("Card: 4111-1111-1111-1111;", "Card: [REDACTED];"),
    ("Card 1234567890123!", "Card [REDACTED]!"),
    ("Card 1234567890123456789!", "Card [REDACTED]!"),
    ("Long 12345678901234567890.", "Long [REDACTED]."),
    ("Safe text, café, 日本語 and 🙂!", "Safe text, café, 日本語 and 🙂!"),
    ("Order 123456789012, date 2026-10-29.", "Order 123456789012, date 2026-10-29."),
    ("a@b.com; b@c.org; 123-45-6789; 4111 1111 1111 1111.",
     "[REDACTED]; [REDACTED]; [REDACTED]; [REDACTED]."),
    ("unfinished alex@", "unfinished [REDACTED]"),
]


@pytest.mark.parametrize("text,expected", EXAMPLES)
def test_every_possible_two_chunk_split(text, expected):
    for split in range(len(text) + 1):
        redactor = Redactor()
        actual = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.finish()
        assert actual == expected, (split, actual)


@pytest.mark.parametrize("text,expected", EXAMPLES)
def test_one_character_at_a_time(text, expected):
    redactor = Redactor()
    actual = "".join(redactor.feed(char) for char in text) + redactor.finish()
    assert actual == expected


@pytest.mark.parametrize("punctuation", [".", ",", ";", ":", "!", "?", ")", "\n", " ", " done"])
def test_punctuation_cannot_release_part_of_a_number(punctuation):
    text = "4111 1111 1111 1111" + punctuation
    expected = redact_complete(text)
    for split in range(len(text) + 1):
        redactor = Redactor()
        actual = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.finish()
        assert actual == expected
        assert "4111" not in actual


def test_no_sensitive_prefix_is_released_early():
    for pieces in [["alex", "@", "example", ".com"], ["123-", "45-", "6789"],
                   ["4111 ", "1111 ", "1111 ", "1111"]]:
        redactor = Redactor()
        for piece in pieces:
            assert redactor.feed(piece) == ""
        assert redactor.finish().startswith("[REDACTED]")


def test_safe_text_is_available_before_finish():
    redactor = Redactor()
    assert redactor.feed("Hello! ") == "Hello! "
    assert redactor.feed("alex@") == ""
    assert redactor.feed("example.com; ") == "[REDACTED]; "


def test_buffer_limit_fails_closed():
    redactor = Redactor()
    assert redactor.feed("x" * MAX_PENDING) == ""
    with pytest.raises(BufferLimitError):
        redactor.feed("x")
    assert len(redactor.pending) <= MAX_PENDING


def test_long_response_does_not_accumulate():
    redactor = Redactor()
    for _ in range(20_000):
        assert redactor.feed("Safe text! ") == "Safe text! "
        assert len(redactor.pending) <= MAX_PENDING
    assert redactor.finish() == ""
    assert redactor.peak_pending == 0


def test_random_chunking_matches_complete_redaction():
    randomizer = random.Random(17)
    for text, expected in EXAMPLES:
        for _ in range(50):
            redactor = Redactor()
            pieces = []
            position = 0
            while position < len(text):
                length = randomizer.randint(1, 9)
                pieces.append(redactor.feed(text[position:position + length]))
                position += length
            assert "".join(pieces) + redactor.finish() == expected


def test_each_stream_has_its_own_state():
    first, second = Redactor(), Redactor()
    assert first.feed("alex@") == ""
    assert second.feed("Hello! ") == "Hello! "
    assert second.finish() == ""
    assert first.feed("example.com; ") == "[REDACTED]; "


@pytest.mark.parametrize("codepoint", [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x202E, 0x2066, 0x00AD, 0x061C])
@pytest.mark.parametrize("plain", ["4111 1111 1111 1111", "123-45-6789", "alex@example.com"])
def test_format_controls_cannot_split_sensitive_candidates(codepoint, plain):
    text = chr(codepoint).join(plain)
    for split in range(len(text) + 1):
        redactor = Redactor()
        first = redactor.feed(text[:split])
        second = redactor.feed(text[split:])
        assert first == second == "", "A sensitive prefix escaped before completion"
        assert redactor.finish() == "[REDACTED]"
    redactor = Redactor()
    for char in text:
        assert redactor.feed(char) == ""
    assert redactor.finish() == "[REDACTED]"


def test_normal_formatting_and_joined_emoji_are_preserved():
    text = "Hello! \u200bworld; \U0001f469\u200d\U0001f4bb; \u0633\u0644\u0627\u0645\u200c\u062f\u0646\u06cc\u0627;"
    for split in range(len(text) + 1):
        redactor = Redactor()
        result = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.finish()
        assert result == text


def test_format_controls_still_count_toward_the_buffer_limit():
    redactor = Redactor()
    assert redactor.feed("a" + "\u200b" * (MAX_PENDING - 1)) == ""
    assert len(redactor.pending) == MAX_PENDING
    with pytest.raises(BufferLimitError):
        redactor.feed("\u200b")
    assert len(redactor.pending) <= MAX_PENDING


@pytest.mark.parametrize("text,expected", [
    ("count=1234567890123", "count=[REDACTED]"),
    ("request_id=abc1234567890123xyz", "request_id=abc[REDACTED]xyz"),
    ("A@B", "[REDACTED]"),
])
def test_conservative_masking_policy_is_unchanged(text, expected):
    redactor = Redactor()
    assert redactor.feed(text) + redactor.finish() == expected
