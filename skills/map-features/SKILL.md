---
name: map-features
description: "Use when people need a map of what a product does today, read from its code at one commit: a feature map for a PRD, a roadmap, onboarding or a status doc — \"feature map 만들어줘\", \"기능 지도 그려줘\", \"지금 뭐가 구현돼 있는지 정리해줘\", \"PRD 쓰기 전에 현재 기능 정리\", \"map the features\". The output is a document for humans: a plain summary, an apps table, and one row per feature with its status and evidence. A map that tells an agent how to drive each feature for verification is create-verification-skill; how one subsystem works is how."
---

# Map features

A product feature map answers one question for a reader who will plan, write or decide: what can a user do with this product today, and how do we know. Every row is read from the code at one commit, and every status carries evidence a reader can open.

This is not the verification map that `create-verification-skill` writes under `.claude/skills/verify-<app>/features/`. That one is for an agent driving the app. When it exists, read it as a source for the feature list, and do not edit it from here.

## 1. Pin the baseline

Name the repo, the ref, its short sha and its commit date in the first line of the map ("기준: `owner/repo` origin/main `abc1234`(2026-10-10)"). Fetch first and read `origin/<default>`, not a stale local branch. Every statement in the map is true as of that line. A product spread over several repos gets one baseline line per repo.

## 2. Find the apps

List the deployable units from what builds and ships them: compose files, Dockerfiles, workspace manifests, CI and deploy config. One row per app: what it does in one line, and its status. Then list, as plain bullets, the apps a reader would expect but which do not exist yet (a web console, a bot), each with what exists in its place, such as a shared package with no app using it or only contract types.

## 3. List the features from the user's side

Read the public surfaces: HTTP routes or the OpenAPI file, CLI commands, UI routes and menus, bot commands, config flags a user sets. One row per thing a user can do, grouped by area (account, sessions, cost, operations). A row names the action, not the module that implements it.

For a large product, split the surfaces across parallel `Explore` agents, one area each, and merge their rows yourself. Each agent returns rows with evidence, not prose.

Add the features that plans and docs promise but the code lacks; those rows are the reason a PRD reader opens the map.

## 4. Status and evidence

Use one fixed status set, with the labels translated into the map's language (a Korean map uses the labels in parentheses):

| Status | Means | Evidence it needs |
|---|---|---|
| works (동작) | A user can do it on the baseline | The handler, command or screen that does it; a test that exercises it when there is one |
| partial (일부) | Some of it works | The working part's evidence, and what is missing |
| documented only (문서만) | Docs or a runbook describe it; nothing has run it | The doc path, and what has not been done |
| absent (없음) | Not in the code | The plan or ticket that mentions it, or "no plan" |
| unknown (확인 못 함) | You could not tell | What you tried |

Open the code behind a row before marking it works; a route or file name alone is not evidence. A row with no evidence is unknown, never works. Evidence is a path, endpoint, env var, flag, or PR as `owner/repo#N`, short enough to fit a table cell.

## 5. Write the map

Write the text per `write-plainly`, in the user's language. Sections in this order:

1. **Baseline line** (§1).
2. **In plain words** (쉽게 말하면): five to eight bullets for a reader who does not code. A metaphor is fine when it maps one-to-one onto the real parts. End with what does not exist yet.
3. **Apps**: the table and the absent list (§2).
4. **Feature map**: area | feature | status | evidence.
5. **Limits to know** (알아 둘 제한): constraints a planner must know (who may use it, quotas, single-tenant, manual steps), each with its source.

Save it where the user's working docs live (`use-notes`), or the path they named. Do not commit it to the product repo unless asked.

## 6. Refresh

To update a map, pin a new baseline, re-check every row against it, and list the rows whose status changed under the baseline line. A row nobody re-checked keeps its old baseline next to it.

## Report

The map's location, the baseline, the row count per status, and the unknown rows with what would settle each.
