# Independent review

Review the exact head and its diff against the intended base. Give a reviewer who did not
implement the change the requirements, repository, base and head, and access to the relevant
files and tests. Do not seed the review with the implementer's conclusions. A human reviewer
or an available read-only agent can do this; no model family is required.

Read the repository's data-sharing restrictions before selecting a reviewer. A ban on
external services applies to every provider; use a reviewer within the allowed boundary or
report review unavailable. Include that restriction in delegated review instructions.

Check correctness, security, acceptance criteria and regressions. Use adversarial design
questions when the change alters a public contract or a difficult invariant. Findings need
file locations, a concrete failure case and severity. Record the reviewer, reviewed SHA,
coverage, findings, disposition and unresolved limitations.

A successful tool exit without a review is NOT VERIFIED. Missing access, an empty result,
a timeout or unavailable reviewer is INCONCLUSIVE: report the cause, use another authorized
reviewer if available, or leave review pending. Retry only after a concrete transient cause
has been addressed; do not loop on a vendor outage, change credentials or weaken permissions.
A changed head requires review of the changed diff and fresh relevant checks.

For the optional orchestration ledger, use `independent-review`, `review:<reviewer>`,
`verifier:<reviewer>` or `live:<feature>` as applicable. The source describes the evidence,
not proof of independence by itself. Never record the implementer's own opinion as an
independent verdict.
