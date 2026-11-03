# agent-skills

[English](README.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · 简体中文

一套 Claude Code 技能,把一张工单一路带到合并的 PR,由你决定在哪些环节需要人来批准。

[![License: MIT](https://img.shields.io/github/license/JeongJaeSoon/agent-skills)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/JeongJaeSoon/agent-skills)](https://github.com/JeongJaeSoon/agent-skills/releases/latest)

## 这是什么

agent-skills 是一个 Claude Code 插件,包含 41 个技能、4 个 agent 定义、8 个钩子(hook)和若干命令。它覆盖两条流程:一条把单张工单从第一次修改一直推进到 PR 合并、工单关闭;另一条让多个 Orca worker 同时运行整个项目,每个 worker 负责一张工单,并按顺序合并它们的 PR。

它面向这样的开发者:把 GitHub 仓库里的工单工作交给 Claude Code,并希望逐个环节地选择,是由人批准每一步(human in the loop,HITL),还是由 agent 先往前走、人在一旁盯着、只在出现异常时介入(human on the loop,HOTL)。写入你的用户设置,以及不可撤销的操作,始终会等你确认。

主要运行环境是装有 [Orca](https://github.com/stablyai/orca) 的 macOS。单工单技能在任何能运行 Claude Code 的地方都能用。多会话技能、仪表盘和定时轮次需要 Orca,部分自动化需要 macOS。

## 介绍视频

4 分钟概览:各个组成部分、两条流程,以及你始终掌握决定权的地方。

https://github.com/user-attachments/assets/1e240200-0d28-4f8b-b13c-ae25a68984f9

字幕:[English (.srt)](docs/video/agent-skills-explainer-en.srt)。视频没有中文版,另有[日语版](https://github.com/user-attachments/assets/206c4624-dca8-448d-8179-69c08ecc511c)和[韩语版](https://github.com/user-attachments/assets/4989f7f2-54d3-493a-8692-e0a4157199b0)。

## 你能得到什么

- **一张工单,从头做到尾。** `deliver-ticket` 负责制定计划、编写测试、请作者之外的人评审、创建 PR、等待 CI、合并,并在关闭工单时为每条验收标准附上证据。
- **一个项目,多个 worker。** `orchestrate` 给每个 Orca worker 分配一张工单,并维护一份程序台账。PR 只能通过 `orch land` 关口或你批准的合并来合入,修改了共享文件的 PR 会逐个合并。
- **有地方可以盯着。** `brief` 会汇报哪些在进行中、哪些在等别人、哪些在等你、哪些被搁置。`orch-dash` 仪表盘会显示待决事项、权限提示和收件箱。
- **被拒绝的就是被拒绝了。** 被权限提示、auto mode 分类器、钩子或人拒绝的操作,只会上报,不会换个形式再试一次。
- **没有配置,就不猜。** 笔记、经验教训台账、个人文件夹和聊天频道,只会在你的配置指明的地方使用。缺少某个配置项时,对应功能会关闭,或者由技能先来问你。
- **保持机器干净。** `reap-resources` 会找出已结束的会话遗留下来的进程、worktree 和容器,并且只清理它列出来的那些。

## 环境要求

必需:

- Claude Code
- `git`
- `python3` 3.9 或更高版本(macOS 自带的 `/usr/bin/python3` 即可)

仅完整安装需要:

- `gh`,并已登录 github.com

可选:

- [Orca](https://github.com/stablyai/orca):用于多会话技能和定时轮次
- Linear 或 Jira:用于工单跟踪类技能(未另行配置时使用 Linear)
- Obsidian 或普通的 Markdown 文件夹:用于笔记类技能
- tailscale 和 caddy:用于 `preview-watch` 和 `share-server`

在 macOS 上,完整安装会把一个同步任务和默认的自动化作为 LaunchAgent 装好。在 Linux 上,它会打印一行 cron 配置,由你自己添加。

## 安装

### 让你的 agent 来装

把下面这段话粘贴到 Claude Code 会话里:

```text
Set up the Claude Code plugin at https://github.com/JeongJaeSoon/agent-skills for me. Read https://raw.githubusercontent.com/JeongJaeSoon/agent-skills/main/INSTALL.md and follow it: check my environment first, ask me before each choice, and give me any command I have to run myself. Don't edit my settings files directly.
```

[INSTALL.md](INSTALL.md)(英文)是 agent 所遵循的流程。它先检查你的环境,然后询问你:只装市场(marketplace)里的插件,还是做完整安装;要设置哪些值(回复语言、checkout 路径、个人配置仓库);并就它要运行的两条命令征求你的批准。

### 自己动手

1. 在 Claude Code 会话中,添加 marketplace 并安装插件:

   ```text
   /plugin marketplace add JeongJaeSoon/agent-skills
   /plugin install agent-skills@jeongjaesoon
   ```

   安装对话框会询问回复语言、checkout 路径(默认 `~/src/agent-skills`)和个人配置仓库的 URL。三项都可以留空。
2. 开启一个新会话。插件在会话启动时加载,所以这里仅靠 `/reload-plugins` 是不够的。
3. 在你接受或拒绝之前,每个新会话都会提示 `setup-pc`。拒绝后,该 profile 不再提示。要做完整安装就接受:agent 会克隆 checkout,并为每个设置文件展示一份 diff,它自己从不写入这些文件。你读完 diff,把它给出的那行 `! python3 ... --apply <hash>` 粘贴执行即可。
4. 批准那条用于开启同步任务和自动化的权限提示,然后重启会话。

完整指南(包括只用 shell 的安装方式、更新、全部批准点的汇总表,以及卸载)见 [docs/install.md](docs/install.md)(英文)。

## 快速上手

当情境与技能 description 中描述的相符时,技能会自动触发。你也可以用 `/agent-skills:<name>` 按名称调用。

- 说"完成 ABC-1",会运行 `deliver-ticket`,从计划一直做到合并和关闭工单。这需要一个工单跟踪系统:默认是 Linear,也可以设置 `tracker.adapter`(见[配置](#配置))。
- 说"给我一份状态报告",会运行 `brief`。Claude Code 内置了 `/brief`,所以要直接调用这个技能,请用 `/agent-skills:brief`。
- 在 Orca 中,用 `claude --agent agent-skills:orchestrator --name orchestrator` 启动一个顶层会话,然后让它接管所有会话。orchestrator 把每个项目交给一个 lead 会话,每个 lead 再去带它的 worker。

## 你始终掌握决定权的地方

- **你的设置。** `setup-pc`、`tune-automode` 和 `skills-config` 只生成 diff 和一条 `!` 命令,由你来运行。这一点无法关闭。
- **提示与拒绝。** 权限提示会等你回应。当 auto mode 分类器拒绝某个操作时,agent 会上报并停下。若要让某一类操作此后可以通过,你需要添加一条 `permissions.allow` 条目,或者一条由 `tune-automode` 起草的 autoMode 规则。
- **不可撤销的操作。** 破坏性迁移、对共享分支的 force push、生产环境部署,以及向外部发送任何内容,始终会停下来询问。
- **合并。** 只有当某条 autoMode 规则允许在该仓库中合并时,`orchestrate` 和 `deliver-ticket` 才会自行合并就绪的 PR;没有这条规则就会询问。采用 `human-gate` 策略,或者你说明自己要先看一眼,它们就会在每次合并前停下。
- **定时清理。** `janitor` 和 `pressure-watch` 会按计划清理已结束的 agent 留下的东西。用 `skills-sync setup disable <name>` 可以关闭其中任何一个。在 `watch-prs` 中自动批准或自动合并你自己的 PR,在你配置之前保持关闭。

每个批准点及其默认值、修改方法,见 [docs/install.md](docs/install.md#safety-rails)(英文)。

## 技能

每个技能何时触发、做什么、附带哪些文件,见[技能目录](docs/skills.en.md)(英文)。

| 分组 | 技能 | 作用 |
|---|---|---|
| [单张工单](docs/skills.en.md#one-ticket) | `write-ticket`, `deliver-ticket`, `ui-review`, `handoff-ticket`, `dispatch-card`, `end-session`, `record-progress` | 写工单、交付工单、把下一张交给新的 card、结束会话、记录进展 |
| [单个项目](docs/skills.en.md#one-project-several-orca-workers) | `orchestrate`, `measure-delivery` | 用 Orca worker 并行落地多张工单,事后衡量实际效果 |
| [单台机器](docs/skills.en.md#one-machine) | `reap-resources`, `secure-fill`, `tune-automode`, `skills-config`, `setup-pc` | 清理遗留物、填写 agent 看不到的密钥、起草 autoMode 规则、修改配置项、安装与更新 |
| [聊天与评审](docs/skills.en.md#chat-and-reviews) | `watch-mentions`, `watch-prs` | 把与你相关的聊天和 PR 转成待办工作 |
| [适配器](docs/skills.en.md#adapters) | `use-tracker`, `use-notes` | Linear 和 Jira 工单,Obsidian 和 Markdown 笔记 |
| [写作](docs/skills.en.md#writing) | `brief`, `stage`, `todo`, `map-features`, `write-plainly`, `prune-comments`, `write-skill` | 状态报告、阶段表、待办与搁置队列、功能地图、平实行文、注释清理、技能编写检查 |
| [pstack](docs/skills.en.md#pstack-skills-lauren-tan-mit) | `architect`, `arena`, `blast-radius`, `create-verification-skill`, `figure-it-out`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `recall`, `reflect`, `show-me-your-work`, `swarm`, `tdd`, `teach`, `why` | 设计、对比、影响分析、验证、讲解、复盘 |

pstack 这一组来自 Lauren Tan 的 [pstack](https://github.com/cursor/plugins)(MIT),沿用上游的名称,以便跟进上游的变更。在多会话场景下,worker 之上还有两层:orchestrator(一个顶层会话)和 lead(每个项目一个)。参见 [orchestrate 中的 Roles 表](skills/orchestrate/SKILL.md#roles)(英文)。

## 配置

没有任何必填项。技能从 `~/.claude/agent-skills.json` 中读取各自的配置项(每个技能一个段落),列表和密钥则从 `~/.config/agent-skills/` 读取。缺少某个配置项时,对应功能会关闭,或者技能在行动前先问你。除非 `tracker.adapter` 另有指定,工单跟踪适配器就是 Linear。`skills-config` 技能会为你生成写入配置项的脚本,由你自己运行。各配置项及其默认值见 [docs/configuration-migration.md](docs/configuration-migration.md)(韩文)。

## 文档

- [docs/install.md](docs/install.md)(英文):安装、更新、批准点、卸载、故障排查
- [INSTALL.md](INSTALL.md)(英文):agent 所遵循的安装流程
- [docs/skills.en.md](docs/skills.en.md)(英文):技能目录
- [docs/operations.md](docs/operations.md)(韩文):术语、定时自动化、使用场景、安全护栏与钩子、如何修改技能
- [docs/new-pc.md](docs/new-pc.md)(韩文):新电脑的详细设置
- [docs/automation.md](docs/automation.md)(韩文):哪种机制运行哪项任务
- [docs/platform.md](docs/platform.md)(韩文):checkout 如何加载与同步
- [docs/orca-migration.md](docs/orca-migration.md)(英文):2026-11-01 从 orch 迁移到 Orca 的内容、与旧会话并行运行、切换顺序以及如何回退
- [docs/token-usage.md](docs/token-usage.md)(韩文):如何衡量本插件给上下文增加的 token,以及各项功能用掉的 token

## 贡献与安全

搭建 checkout 的方法以及这个公开仓库的规则,见 [CONTRIBUTING.md](CONTRIBUTING.md)(英文)。在仓库中工作的 agent 还会阅读 [AGENTS.md](AGENTS.md)。请按 [SECURITY.md](SECURITY.md)(英文)中的说明私下报告安全漏洞,不要发公开 issue。

## 测试

没有 CI。提交改动之前,请运行所有测试文件:

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/measure-delivery/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_cards.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/ui-review/scripts/test_ui_measure.py hooks/test_*.py scripts/test_sync.py scripts/test_card.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py scripts/test_skill_tokens.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 许可证

MIT。`vendor/pstack` 以及取自它的技能沿用原作者的 MIT 许可证(`vendor/pstack/LICENSE`)。
