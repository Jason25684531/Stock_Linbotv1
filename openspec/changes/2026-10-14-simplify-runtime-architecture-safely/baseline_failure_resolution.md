# 2026-10-14 LINE baseline prerequisite

The existing facade patch contract replaces app.ApiClient, app.MessagingApi and app.ReplyMessageRequest. app.line_bot.reply_message previously bypassed that seam through direct imports. The single shared reply owner now resolves all three through the already-existing services composition seam. Callback, postback, diagnosis and command replies retain normalization and the real SDK request validation; no test is excluded and no new framework is introduced. The unused imports are removed; app retains the canonical SDK imports.

Before: the isolated test reproduced pydantic ValidationError on a string dummy message. After: rerun the identical test and the original 126-case suite; exact results recorded in phase0.md. This is a separately documented correctness prerequisite, before cleanup source changes.
