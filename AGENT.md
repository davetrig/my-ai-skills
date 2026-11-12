# Global Preferences

## Git

Do not run `git commit` or `git push` unless explicitly asked. Leave committing and pushing to the user.

## Pull Requests

Do not mention any Jira ticket other than the specific one related to the PR, in the PR title, description, comments or commit messages. That covers URLs and bare keys (for example `FAX-1234`) alike. Linking other Jira tickets causes them to get auto-moved to the `Deployed` state when the app is deployed. Describe related work in words instead (for example "the follow-up webhook story").

## Ruby Style

Use hash value omission (punning) when the local variable name matches the keyword argument or hash key name — e.g., `{ apns:, fcm: }` instead of `{ apns: apns, fcm: fcm }`.
