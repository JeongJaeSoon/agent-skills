# Plain Korean

Patterns that make Korean prose padded, translated, or machine-written, each with a rewrite. They apply to tickets, PR bodies, commit messages, notes, worklogs, and reports to the user.

Identifiers, commands, logs, and error text stay as they are. Technical terms the team says out loud in English (PR, worktree, merge, flake, stack) stay in English. Don't coin a Korean word for them.

## Register

Docs, tickets, PR bodies, commit messages, and notes use the plain declarative "~다" style. The present tense states what the code or the rule does, and the past tense states what this change did.

| Before | After |
|---|---|
| 캐시를 삭제합니다. | 캐시를 지운다. |
| 재시도 로직 추가함 | 재시도를 세 번까지 한다. |
| 커밋 제목 `재시도 로직 추가` | 커밋 제목 `fix(sync): 실패한 요청을 세 번까지 다시 보낸다` |

A chat reply to the user may use "~합니다". Pick one register per text and keep it. Don't mix in "~함", "~음" endings.

## Stock phrases

These words sound like content and say nothing. Replace each with the thing it stands for, or delete it.

| Before | After |
|---|---|
| 다양한 트래커를 지원한다. | Linear와 Jira를 지원한다. |
| 요청을 효율적으로 처리한다. | 요청 100개를 API 호출 한 번으로 묶는다. |
| 원활한 리뷰를 위해 diff를 나눴다. | 리뷰어가 파일 하나씩 볼 수 있게 diff를 셋으로 나눴다. |
| `orch land`가 핵심적인 역할을 한다. | `orch land`만 PR을 머지한다. |
| 스크립트를 통해 결과를 확인한다. | 스크립트로 결과를 확인한다. |
| 테스트를 먼저 돌리는 것이 중요합니다. | 테스트를 먼저 돌린다. 실패 이유를 보기 전에 고치면 원인을 놓친다. |
| 이번 변경 사항을 살펴보겠습니다. | (삭제하고 변경 내용부터 쓴다.) |
| 리뷰를 진행했다. | 리뷰했다. |
| 변경이 이루어졌다. | `guard.py`의 허용 목록을 바꿨다. |
| 기존 헬퍼를 활용한다. | 기존 헬퍼를 쓴다. |
| 해당 파일을 수정한다. | `tracker.py`를 고친다. |

"진행하다", "수행하다", "이루어지다" wrapped around a noun hide the verb. Use the verb itself. "해당" usually points at something the reader has to go find, so name it.

## Translationese

Constructions carried over from English syntax. Korean says the same thing with a particle or a plain verb.

| Before | After |
|---|---|
| 설정에 대해 설명한다. | 설정을 설명한다. |
| 버그에 대한 수정 | 버그 수정 |
| 이 함수는 세 개의 인자를 가진다. | 이 함수는 인자를 세 개 받는다. |
| 파일이 생성되어진다. | `init`이 파일을 만든다. |
| 결과가 보여진다. | 결과가 보인다. |
| 배포에 있어서 주의할 점 | 배포할 때 주의할 점 |
| 타임아웃으로 인해 실패했다. | 요청이 30초를 넘겨 실패했다. |
| Linear의 경우 템플릿을 쓴다. | Linear는 템플릿을 쓴다. |
| 스크립트에 의해 생성된다. | 스크립트가 만든다. |
| 이 명령은 캐시를 지울 수 있다. (항상 지운다) | 이 명령은 캐시를 지운다. |
| 모든 파일들을 검사한다. | 모든 파일을 검사한다. |

"~할 수 있다" is right only for an option or a possibility. For something that always happens, state it.

## Hedges

A hedge hides whether you checked. State what you measured, or label the claim as a guess and say what would settle it.

| Before | After |
|---|---|
| 이 쿼리가 병목이라고 할 수 있다. | 이 쿼리가 요청 시간의 70%를 쓴다(`EXPLAIN ANALYZE`로 측정). |
| 캐시 문제인 것 같습니다. | 캐시 문제로 짐작한다. 캐시를 끄고 재현해 보지는 않았다. |
| 다소 느려질 수도 있을 것 같습니다. | 빌드가 느려질 수 있다. 얼마나 느려지는지는 재지 않았다. |
| 비교적 빠르다. | p95가 120ms다. |

Stacked hedges ("~할 수도 있을 것 같습니다") never help. One label is enough.

## Name the actor

Korean drops the subject when the previous sentence already names it. That is fine. Dropping it also hides who acted, and in a report or a PR body the actor is often the point.

| Before | After |
|---|---|
| 배포 후 롤백했다. | main 가디언이 배포 20분 뒤 롤백했다. |
| 확인했다. | `pytest` 42개가 통과하는 것을 확인했다. |
| 자동으로 처리된다. | `orch land`가 머지한다. |
| 캐시가 삭제된다. | `clean`이 캐시를 지운다. |

If nobody checked, say so ("직접 확인하지 않았다"). "확인했다" with no actor and no method reads as a check that happened.

## Long noun chains

A chain of nouns packs a sentence into a label and leaves the reader to rebuild the verbs. Write the sentence.

| Before | After |
|---|---|
| 검증 스크립트 실행 결과 확인 | 검증 스크립트를 돌리고 결과를 확인한다. |
| 티켓 상태 변경 자동화 기능 추가 | 티켓 상태를 자동으로 바꾼다. |
| 대시보드 메트릭 로딩 실패 시 처리 로직 개선 필요 | 대시보드가 메트릭을 못 읽으면 빈 화면 대신 마지막 값을 보여 줘야 한다. |

A title or heading may end in a noun. A sentence in the body may not stop at a noun chain.

## Say what it does

| Before | After |
|---|---|
| 안정성을 높였다. | 재시도를 세 번으로 제한해 무한 루프를 막았다. |
| 성능이 크게 향상되었다. | 빌드가 4분 10초에서 1분 50초로 줄었다. |
| 사용성이 개선될 것으로 기대된다. | 설정 파일이 없어도 기본값으로 동작한다. |

## Rhythm without compression

Don't chain clauses with "~하고", "~하며", "~하여" into one sentence the reader has to hold in their head. Split it where the subject changes.

Don't compress either. Keep the particles and the verbs.

| Before | After |
|---|---|
| 파서 거부 → exit 2, 쓰기 없음 | 파서가 잘못된 날짜를 거부하면 코드 2로 끝나고 아무것도 쓰지 않는다. |
| 설정 로드 후 검증하고 실패 시 기본값 사용하며 경고 로그 남김 | 설정을 읽은 뒤 검증한다. 검증에 실패하면 기본값을 쓰고 경고를 남긴다. |

## Chatbot phrases

Delete these. They add a line and tell the reader nothing.

- "좋은 질문입니다!", "말씀하신 대로입니다!"
- "도움이 되셨길 바랍니다.", "추가로 궁금한 점이 있으시면 말씀해 주세요."
- "결론적으로", "요약하자면" in front of a summary nobody needed.

## Punctuation and formatting

These apply in any language.

- No em dashes (—), and no en dash or "--" standing in for one. End the sentence or use a comma.
- No colon as a mid-sentence connector. "원인: 캐시 만료" inside a sentence becomes "원인은 캐시 만료다." A colon before a list or a code block is fine.
- No bold label plus colon that restates the line, as in "**성능:** 성능이 좋아졌다". Write the item as a sentence. A bold lead-in that ends in a period and is followed by new detail is fine. So are the field labels of a fixed template or a machine-read line such as `파생: <ID> · 원인: <분류>`.
- No decorative emoji in headings or bullets. Headings that a template defines, such as a ticket skeleton's, keep their emoji.
- Bold only what the reader must not miss, not every term.

## Worked example

Before:

> 이번 PR에서는 티켓 착지 과정에 대해 다양한 개선이 이루어졌습니다. 특히 원활한 머지를 위해 `orch land`를 통해 착지 순서가 효율적으로 관리되도록 하였으며, 이로 인해 충돌이 크게 줄어들 것으로 기대됩니다 — 이는 매우 중요한 변화라고 할 수 있습니다.

After:

> `orch land`가 착지 순서를 정하고, 앞 PR이 머지될 때까지 뒤 PR을 기다리게 한다. 지난주 프로그램에서 손으로 머지한 12건 중 5건이 충돌했다. 이 방식으로는 아직 프로그램을 돌려 보지 않아 충돌이 줄어드는지는 모른다.
