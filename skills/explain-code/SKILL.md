---
name: explain-code
description: "Before changing unread code: '이 구조 설명해줘', 'X는 어떻게 동작해', '이건 어디에 둬야 해', '왜 이렇게 됐어', '이 결정 배경이 뭐야', 'teach me this', '이거 제대로 이해하고 싶어', '이 변경 이해시켜줘', 'feature map 만들어줘', '기능 지도 그려줘', '지금 뭐가 구현돼 있는지 정리해줘'. Verify maps: create-verification-skill. An orchestrator dispatches it instead."
---

# Explain code

Four modes read code and explain it. None of them edits code. Pick the mode from the ask, read its reference, and follow it. A caller names the mode in the Skill args (`how: <question>`); without one, pick it from the question and say which you picked.

| Mode | When | Reference |
|---|---|---|
| how | How code works: runtime flow, architecture, where something belongs ("how does X work", "X는 어떻게 동작해", "이 구조 설명해줘", "이건 어디에 둬야 해"). Run it before editing a subsystem no one here has read. | [references/how.md](references/how.md) |
| why | Why code is this way, from history, PRs, tickets and chat ("왜 이렇게 됐어", "이 결정 배경이 뭐야", "why did we do X"). Run it before deleting or loosening code whose reason is not written down, and to find the change behind a regression. Explaining what the code does never answers why. | [references/why.md](references/why.md) |
| teach | Someone new to a body of work wants to understand it ('teach me this', "이거 제대로 이해하고 싶어", "이 변경 이해시켜줘"). It weaves how and why into one account. A single how or why question is that mode. | [references/teach.md](references/teach.md) |
| map | A product feature map read from the code at one commit, for people who plan, write or decide ("feature map 만들어줘", "기능 지도 그려줘", "지금 뭐가 구현돼 있는지 정리해줘"). A map an agent uses to drive the app for verification is `create-verification-skill`. An orchestrator dispatches it instead. | [references/map.md](references/map.md) |
