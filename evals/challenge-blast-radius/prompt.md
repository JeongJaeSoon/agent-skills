---
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger]
---

scripts/skill_tokens.py의 display_width를 len()으로 바꾸려고 해. 이거 바꾸면 뭐가 깨져?

이 체크아웃에는 그 파일이 없으니 아래 발췌로 판단해줘.

```python
def display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)

# listing(): each skill's description line, measured for the skill listing budget
skills.append({"name": name, "tokens": count(line), "width": display_width(line)})
agents.append({"name": name, "tokens": count(line), "width": display_width(line), "body_tokens": count(body)})
```

```python
# scripts/test_skill_tokens.py
assert st.display_width("ab한") == 4
w = st.display_width(fm.get("description", ""))   # checked against the per-description width cap
```
