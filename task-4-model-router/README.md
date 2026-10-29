# Task 4: Rate limiter and model router

Status: pending. This folder contains the requirements, not an implementation.

## What to build

A model router with a per-tenant token limit, provider fallback, and an on-disk SQLite database.

## Logic

1. Check the tenant's token use in the last 60 seconds.
2. Reserve tokens before sending the request. Concurrent requests must not exceed the limit.
3. Call the primary provider.
4. If it returns HTTP 429 or takes longer than 3000 ms, try the backup.
5. Update token use based on the defined accounting policy.
6. Return a standard gateway error if the request fails. Do not expose provider stack traces.

Default limit: 50,000 tokens per minute per tenant API key. Use SQLite transactions to protect updates and evict expired entries. Do not store raw API keys in the database.

## Tests to add

- Token use is tracked separately for each tenant.
- Old entries stop counting after 60 seconds.
- Concurrent requests cannot bypass the limit.
- HTTP 429 and a 3000 ms timeout trigger fallback.
- The timed-out primary request is cancelled.
- Provider failures return safe, consistent errors.
- Rate-limit state survives a process restart.
