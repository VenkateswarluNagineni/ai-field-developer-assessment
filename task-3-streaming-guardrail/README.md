# Task 3: Streaming guardrail

Status: pending. This folder contains the requirements, not an implementation.

## What to build

A gateway endpoint that streams model text and replaces personal information with `[REDACTED]`.

## Logic

1. Send the generation request to the provider.
2. Read text as it arrives.
3. Keep enough unfinished text to detect sensitive values split across chunks.
4. Redact emails, SSNs, and credit card numbers.
5. Send safe text promptly without storing the full response.
6. Check the remaining text when the stream ends.

## Tests to add

- Sensitive values in one chunk and across chunk boundaries are redacted.
- Incomplete sensitive text is not sent early.
- Safe text keeps streaming.
- Memory use stays bounded.
- Stream errors and client disconnects are handled.
