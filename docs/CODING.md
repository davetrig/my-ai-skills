## Ruby Style

DRY code is a priority. Whenever possible, extract duplicated code to a common function.

Use hash value omission (punning) when the local variable name matches the keyword argument or hash key name — e.g., `{ apns:, fcm: }` instead of `{ apns: apns, fcm: fcm }`.

## Code comments
- Do not add comments that merely restate obvious code behavior or are excessively detailed.
- Reserve comments for non-obvious behavior or important, non-trivial context.
- Comments must be self-contained. Never reference Jira ticket IDs or other external issue IDs.