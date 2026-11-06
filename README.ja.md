# agent-skills

[English](README.md) · 日本語 · [한국어](README.ko.md)

チケット1件をマージ済みの PR まで運ぶ Claude Code のスキル集です。どこで人が承認するかは自分で決められます。

[![License: MIT](https://img.shields.io/github/license/JeongJaeSoon/agent-skills)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/JeongJaeSoon/agent-skills)](https://github.com/JeongJaeSoon/agent-skills/releases/latest)

## これは何か

agent-skills は Claude Code のプラグインで、スキル 41 個、エージェント定義 4 個、フック 16 個といくつかのコマンドでできています。扱う流れは二つあります。一つはチケット1件を最初の修正からマージ、チケットのクローズまで進める流れ。もう一つは Orca のワーカーを複数同時に動かし、1ワーカーに1チケットずつ渡して、PR を順番にマージしていく流れです。

GitHub リポジトリのチケット作業を Claude Code に任せたい開発者向けです。各ステップを人が承認する方式(Human-in-the-loop、HITL)と、エージェントが進めて人は見守り、例外のときだけ割って入る方式(Human-on-the-loop、HOTL)を、地点ごとに選べます。ユーザー設定への書き込みと取り消せない操作は、常に人の承認を待ちます。

主な動作環境は macOS と [Orca](https://github.com/stablyai/orca) です。チケット1件のスキルは Claude Code が動く環境ならどこでも使えます。複数セッションのスキル、ダッシュボード、定期実行には Orca が、一部の自動化には macOS が必要です。

## 紹介動画

構成要素、二つの流れ、人が決定権を持つ地点を約4分で紹介します。

https://github.com/user-attachments/assets/206c4624-dca8-448d-8179-69c08ecc511c

字幕: [日本語 (.srt)](docs/video/agent-skills-explainer-ja.srt)。[英語版](https://github.com/user-attachments/assets/1e240200-0d28-4f8b-b13c-ae25a68984f9)と[韓国語版](https://github.com/user-attachments/assets/4989f7f2-54d3-493a-8692-e0a4157199b0)もあります。

## できること

- **チケット1件を最後まで。** `deliver-ticket` が計画、テスト、作成者以外による独立レビュー、PR、CI の確認、マージ、受け入れ条件ごとの根拠を添えたチケットのクローズまで進めます。
- **複数ワーカーでプロジェクト1件。** `orchestrate` は Orca のワーカーごとにチケットを1件渡し、プログラム台帳を持ちます。PR は `orch land` のゲートを通すか人が承認しない限りマージされません。共有ファイルを変えた PR は1件ずつマージされます。
- **見る場所がある。** `brief` が進行中、他の人待ち、自分の判断待ち、保留に分けて報告します。ダッシュボード `orch-dash` には未決の判断、権限ダイアログ、受信箱が出ます。
- **拒否された操作のやり直しは一度だけ。** 権限ダイアログ、auto mode の分類器、フック、人が拒否した操作は報告します。そのセッションのチャットで人が許可するかルールを追加したとき、またはエージェントが分類器やフックの拒否理由に応える根拠を加えたときだけ、同じ形で一度やり直します。人の拒否と本番 sync は根拠では越えられません。再び拒否されたら止まり、別のセッションやスクリプト、ツールで代わりに行うことはしません。
- **設定がなければ推測しない。** ノート、振り返り台帳、個人フォルダ、チャットのチャンネルは、設定に書かれた場所だけを使います。キーがなければその機能はオフになるか、スキルが先に聞きます。
- **マシンを片付ける。** `reap-resources` が、終わったセッションの残したプロセス、worktree、コンテナを洗い出し、一覧に載せたものだけを消します。

## 必要なもの

必須:

- Claude Code
- `git`
- `python3` 3.9 以上(macOS の `/usr/bin/python3` で動きます)

フルセットアップのみ:

- `gh`(github.com にログイン済み)

任意:

- [Orca](https://github.com/stablyai/orca): 複数セッションのスキルと定期実行
- Linear または Jira: トラッカーのスキル(設定がなければ Linear)
- Obsidian またはただの Markdown フォルダ: ノートのスキル
- tailscale と caddy: `preview-watch` と `share-server`

フルセットアップは同期ジョブと既定の自動化を Orca automation として有効にします。LaunchAgent は作りません。Orca アプリを開いている間に動きます。以前のセットアップが LaunchAgent を残している場合は、`setup-pc` が知らせるので、自分で削除します。

## インストール

### エージェントに任せる

Claude Code のセッションに次の依頼文を貼り付けます。

```text
https://github.com/JeongJaeSoon/agent-skills の Claude Code プラグインをセットアップしてください。https://raw.githubusercontent.com/JeongJaeSoon/agent-skills/main/INSTALL.md を読んでその手順に従い、まず環境を確認し、選ぶところでは毎回私に聞き、私が自分で実行すべきコマンドはそのまま渡してください。設定ファイルを直接編集しないでください。
```

エージェントが従う手順は [INSTALL.md](INSTALL.md)(英語)です。環境を確認したあと、マーケットプレイスのプラグインだけか、フルセットアップか、設定する値(応答言語、チェックアウトの場所、個人設定リポジトリ)を聞き、実行する二つのコマンドについて承認を求めます。

### 自分でやる

1. Claude Code のセッションでマーケットプレイスを追加し、プラグインを入れます。

   ```text
   /plugin marketplace add JeongJaeSoon/agent-skills
   /plugin install agent-skills@jeongjaesoon
   ```

   インストール画面で応答言語、チェックアウトの場所(既定は `~/src/agent-skills`)、個人設定リポジトリの URL を聞かれます。三つとも空のままで構いません。
2. 新しいセッションを開きます。プラグインはセッション開始時に読み込まれるので、ここでは `/reload-plugins` では足りません。
3. 新しいセッションは、受け入れるか断るまで毎回 `setup-pc` を提案します。断るとそのプロファイルでは提案が止まります。フルセットアップにするなら受け入れます。エージェントはチェックアウトを clone し、設定ファイルごとの diff を見せます。設定ファイルを自分で書くことはありません。diff を読んだら、渡された `! python3 ... --apply <hash>` の行を貼り付けて実行します。
4. 同期ジョブと自動化を有効にする権限ダイアログを承認し、セッションを再起動します。

シェルだけで入れる方法、更新、承認地点の全一覧、アンインストールは [docs/install.ja.md](docs/install.ja.md) にあります。

## 最初の一歩

スキルは、description に書かれた状況になるとモデルが自分で呼び出します。`/agent-skills:<名前>` で直接呼ぶこともできます。

- 「ABC-1 を終わらせて」と頼むと、`deliver-ticket` が計画からマージ、チケットのクローズまで進めます。トラッカーが必要です(既定は Linear。変えるには `tracker.adapter`、[設定](#設定)を参照)。
- 「状況を報告して」と頼むと `brief` が動きます。Claude Code には組み込みの `/brief` があるので、直接呼ぶときは `/agent-skills:brief` です。`/brief-pane` でチャットの横にパネルを開くと、開いている間だけ 30 秒ごとに同じ報告を更新します(110 列以上なら横に固定、それより狭いとプロンプトの上に出ます)。
- Orca の中で `claude --agent agent-skills:orchestrator --name orchestrator` を実行して最上位セッションを立て、全セッションの管理を頼みます。オーケストレーターはプロジェクトごとにリードのセッションを立て、リードがワーカーを動かします。オーケストレーターのセッションが自動コンパクトを 3 回経た、コンパクトしてもコンテキストが縮まない、または 2 回が 30 分以内に続いた(`orchestrate.compact_restart`、既定でオン。`restart_at` はトークン上限で、指定したときだけ効く)うえで、待っている用事がなければ、プロンプトの上の帯に「いま `/clear` して大丈夫」と出ます。`restart_at` の既定値 130k はなくなったので、その既定値に頼っていた設定は、今後はコンパクトの履歴で案内されます。次のセッションは役割と保存済みの handoff を引き継ぎます。リードは自動コンパクトに任せるので帯は出ません。リードの `orch handoff save` は `lead:<slug>` に保存され、オーケストレーターのキーへの保存は拒否されます。`/clear` を打つのは人です。`orchestrate.auto_clear: true` は上限を超えたとき、`orchestrate.compact_restart.auto_clear: true` はコンパクトの規則に当たったときに、セッションが自分で打ちます。

## 人が決定権を持つ地点

- **自分の設定。** `setup-pc`、`tune-automode`、`skills-config` は diff と `!` の行を作るだけで、実行するのは人です。この動作は変えられません。
- **権限ダイアログと拒否。** 権限ダイアログは人の応答を待ちます。auto mode の分類器が操作を拒否すると、エージェントは報告して止まります。ある種類の操作を以後通すには、`permissions.allow` の項目か、`tune-automode` が下書きする autoMode ルールを人が追加します。
- **取り消せない操作。** 破壊的なマイグレーション、共有ブランチへの force push、本番デプロイ、外部への送信は、常に止まって聞きます。
- **カードの作業習慣。** Dispatch で起動したカードが、Bash でファイルを書く(`sed -i`、リダイレクト)、コミットや `rm` を他のコマンドと 1 回の呼び出しにまとめる、worktree の外や main checkout を編集する、auto-memory に書く、テストを実行せずに push する、のいずれかをすると、警告が 1 行出て `turn-warnings.jsonl` に記録されます。止めはしません。`docs/skills.ja.md` の `worker.*` キーで、チェックごとにオフにするか `deny` に上げられます。
- **マージ。** `orchestrate` のカードは、最初のプロンプトがマージを承認していれば `orch land` で自分の PR をマージし、自分で閉じます。CI のないリポジトリは先に `orchestrate.no_ci_repos` に入れます。プログラムの外の `deliver-ticket` が自分でマージするのは、そのリポジトリでのマージを許す autoMode ルールがあるときだけで、なければ聞きます。`human-gate` ポリシーにするか、先に自分で見ると伝えると、毎回マージの前で止まります。
- **定期的な片付け。** `janitor` と `pressure-watch` は、終わったエージェントの後始末を定期的に行います。止めるには `skills-sync setup disable <名前>` を使います。`watch-prs` で自分の PR を自動承認、自動マージする機能は、設定するまでオフです。

全地点の既定値と変え方は [docs/install.ja.md](docs/install.ja.md#安全装置) にあります。

## スキル

各スキルがいつ動き、何をし、どのファイルを同梱しているかは[スキルカタログ](docs/skills.ja.md)にあります。

| グループ | スキル | 役割 |
|---|---|---|
| [チケット1件](docs/skills.ja.md#チケット1件) | `write-ticket`, `deliver-ticket`, `ui-review`, `dispatch-card`, `end-session` | チケットを書き、届け、次のチケットを新しいカードに渡し、進捗を記録してセッションを閉じる |
| [プロジェクト1件](docs/skills.ja.md#プロジェクト1件orca-ワーカー複数) | `orchestrate`, `reboot` | 複数のチケットを Orca のワーカーで並行してマージし、再起動で落ちた待機やバインドを戻し、終わったあと実際どうだったかを測る(`reflect` の測定モード) |
| [マシン1台](docs/skills.ja.md#マシン1台) | `reap-resources`, `secure-fill`, `tune-automode`, `skills-config`, `setup-pc` | 残り物の片付け、エージェントが見ない秘密値の入力、autoMode ルールの下書き、設定キーの変更、インストールと更新 |
| [チャットとレビュー](docs/skills.ja.md#チャットとレビュー) | `watch-mentions`, `watch-prs` | 自分に関係するチャットと PR を作業として集める |
| [アダプター](docs/skills.ja.md#アダプター) | `use-tracker`, `use-notes` | Linear と Jira のチケット、Obsidian と Markdown のノート |
| [文章](docs/skills.ja.md#文章) | `brief`, `stage`, `todo`, `map-features`, `write-plainly`, `prune-comments`, `write-skill` | 状況報告、段階表、やること・保留のキュー、機能マップ、文体、コメント整理、スキル作成のチェック |
| [pstack](docs/skills.ja.md#pstack-スキルlauren-tanmit) | `architect`, `challenge`, `fan-out`, `figure-it-out`, `how`, `principles`, `recall`, `reflect`, `show-me-your-work`, `tdd`, `teach`, `verification-skill`, `why` | 設計、比較、影響範囲、検証、説明、振り返り |

pstack グループは Lauren Tan の [pstack](https://github.com/cursor/plugins)(MIT)から取り込んだもので、いまはこのリポジトリで独自に手入れしています。元のファイルは upstream のまま `vendor/pstack` に残し、upstream の変更は任意で有効にする日次ジョブが知らせるので、必要なものだけ手で取り込みます。複数のセッションで働くとき、ワーカーの上にはオーケストレーター(最上位のセッション1つ)とリード(プロジェクトごとに1つ)の2層があります。詳しくは [orchestrate の Roles 表](skills/orchestrate/SKILL.md#roles)(英語)を見てください。

## 名前が変わったスキル

いくつかのスキルは別のスキルのモードにまとめました。古い名前はもうないので、`/agent-skills:<古い名前>` は何も実行しません。新しいコマンドを使ってください。

| 古いコマンド | 新しいコマンド / モード |
|---|---|
| `/agent-skills:measure-delivery` | `/agent-skills:reflect` の測定モード |
| `/agent-skills:swarm` | `/agent-skills:fan-out`、sweep モード |
| `/agent-skills:arena` | `/agent-skills:fan-out`、compete モード |
| `/agent-skills:handoff-ticket` | `/agent-skills:dispatch-card` の次のチケットモード |
| `/agent-skills:create-verification-skill` | `/agent-skills:verification-skill` の create モード |
| `/agent-skills:maintain-verification-skill` | `/agent-skills:verification-skill` の maintain モード |
| `/agent-skills:blast-radius` | `/agent-skills:challenge` の blast-radius モード |
| `/agent-skills:interrogate` | `/agent-skills:challenge` の interrogate モード |

## 設定

必須の設定はありません。スキルは `~/.claude/agent-skills.json` のスキルごとのセクションからキーを読み、一覧と秘密値は `~/.config/agent-skills/` から読みます。キーがなければ、その機能はオフになるか、スキルが動く前に聞きます。トラッカーのアダプターは `tracker.adapter` がなければ Linear です。`skills-config` スキルに頼むと、キーを書き込むスクリプトを作ってくれるので、それを自分で実行します。キーと既定値は [docs/configuration-migration.md](docs/configuration-migration.md)(韓国語)にあります。

## ドキュメント

- [docs/install.ja.md](docs/install.ja.md): インストール、更新、承認地点、アンインストール、トラブルシューティング
- [INSTALL.md](INSTALL.md)(英語): エージェントが従うインストール手順
- [docs/skills.ja.md](docs/skills.ja.md): スキルカタログ
- [docs/operations.md](docs/operations.md)(韓国語): 用語、定期自動化、利用シナリオ、安全装置とフック、スキルの直し方
- [docs/new-pc.md](docs/new-pc.md)(韓国語): 新しい PC のセットアップの詳細
- [docs/automation.md](docs/automation.md)(韓国語): どの仕事をどの仕組みで回すか
- [docs/platform.md](docs/platform.md)(韓国語): チェックアウトの読み込みと同期の設計
- [docs/orca-migration.ja.md](docs/orca-migration.ja.md): 2026-11-01 に orch から Orca へ移したもの、以前のセッションと並べて動かす方法、切り替えの順序と元に戻す方法
- [docs/token-usage.md](docs/token-usage.md)(韓国語): このプラグインがコンテキストに足すトークンと、機能ごとに使ったトークンの測り方

## 貢献とセキュリティ

チェックアウトの準備と、この公開リポジトリのルールは [CONTRIBUTING.md](CONTRIBUTING.md)(英語)にあります。リポジトリで作業するエージェントは [AGENTS.md](AGENTS.md) も読みます。脆弱性は公開 issue ではなく、[SECURITY.md](SECURITY.md)(英語)の方法で非公開に報告してください。

## テスト

CI はありません。変更を送る前に、全テストファイルを実行します。

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/reflect/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_cards.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/deliver-ticket/scripts/test_review_pick.py skills/ui-review/scripts/test_ui_measure.py skills/reboot/scripts/test_reboot.py hooks/test_*.py scripts/test_sync.py scripts/test_card.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py scripts/test_skill_tokens.py scripts/test_allowed_tools.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## ライセンス

MIT。`vendor/pstack` とそこから取り込んだスキルは、原作者の MIT ライセンスに従います(`vendor/pstack/LICENSE`)。
