# Owner-ask gate

Run this before you write a 결정 필요 item, an 오너 할 일 item, an `orch decide add`, or any question to the owner. Skills point here instead of telling an agent to ask the owner (or to call `AskUserQuestion`) on their own terms.

1. **Look for an agent-only route first.** Most questions have one: decide it yourself from the approved scope and the owner's precedent, have it settled after an independent review (a reversible choice within reach), or route it to the lead or card that owns it. Ask only when none is left.
2. **Ask only for one of these**, and name it as the reason tag:
   - `owner-asked`: the owner asked this question, or asked to decide it themselves.
   - `hard-after-review`: a design choice that is still hard after an independent review (Codex when it answers, else a fresh-context Claude reviewer), with the review's verdict and the reviewer kind recorded (`orch verdict`, the PR or the report).
   - `owner-hands-only`: an act only the owner can do or authorize (a passkey, a login, a payment, their budget or permission, a `settings.json` rule, a classifier refusal with its text), after step 1 found no other route.
3. **Write it so it can be answered in one read**: the question in plain words, each option with its cost, one recommendation with its reason.
4. **If no answer, the recommendation stands.** `답이 없으면` is the recommendation itself, or `진행 못 함` when the act needs the owner's own words or hands. Nothing else.
5. **Ask once.** After that the item is carried only in `/brief` (the renderer keeps the same words as the same decision); it is not asked again in chat.

The scripts hold the form, so an item that skips the gate is refused with the missing part named:

- `orch brief render --decision "<question>: <options> → 추천: <X> — <reason> · 답이 없으면 <X | 진행 못 함> · 근거: <tag>"`
- `orch decide add --title T --option "<label>::<its cost or reason>"… --recommend N --if-no-answer <the recommended label | 진행 못 함> --reason <tag>` (both go on the first line of the decision's body, which the dashboard and the report show)
