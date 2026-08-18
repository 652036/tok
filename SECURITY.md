# Security policy

`tok` reads **local** CLI usage logs (token counts, model names, timestamps).
It must never need conversation transcripts, API keys, or credentials.

## Please do not include sensitive data in issues

When filing a bug or discussing a vulnerability, **do not** attach or paste:

- Conversation / chat transcripts or message content
- API keys, tokens, cookies, or other credentials
- Raw CLI logs or unredacted `usage.json` dumps from your machine

Redact paths, account ids, and any payload that is not usage metadata.

## Reporting a vulnerability

Please report security issues privately via a
[GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/creating-a-repository-security-advisory)
on this repository (Security → Advisories → New draft security advisory).

Do not open a public issue for an unfixed vulnerability.
