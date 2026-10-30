# agent-skills のインストール

[English](install.md) · 日本語 · [한국어](install.ko.md)

人が手でインストールするときの手順です。エージェントに任せる場合は [INSTALL.md](../INSTALL.md) を渡してください。同じ手順に、エージェントが守るルールを添えたファイルです。

入り方は 3 つあります。

- **A. マーケットプレイスのみ**: 公開されているプラグインをそのまま使います。スキルを直さずに使うだけならこれで足ります。
- **B. セッションからフルセットアップ**: このリポジトリのチェックアウトを直接読み込み、推奨ユーザー設定、同期ジョブ、既定の自動化まで入れます。A から始めて、次のセッションで仕上げます。
- **C. シェルからフルセットアップ**: B と同じ結果を、Claude Code のセッションを使わずに得ます。

載せている出力は、`HOME`・`CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_PLUGIN_DIRS` を一時ディレクトリに向けた環境で Claude Code 2.1.314 を使って実行したときのものです。実行したのは A、マーケットプレイス版のアンインストール、C の `--settings-only --apply` までです。同期ジョブと自動化を入れる段階（`--actions-only`、フルの `--apply`）とアンインストールの `launchctl`・自動化の行は実行していません。launchd は実際のユーザーと共有されるためです。

## 前提

| | 何が | なぜ |
|---|---|---|
| 必須 | Claude Code（2.1.314 で確認） | プラグインが読み込まれるのはここだけ |
| 必須 | `git` | インストール、チェックアウト、同期 |
| 必須 | `python3` 3.9 以上 | すべてのスクリプト。macOS の `/usr/bin/python3`（3.9.6）で動きます |
| B・C で必須 | github.com にログイン済みの `gh` | 自分のリポジトリに関する推奨ルールと、private な個人設定リポジトリの clone |
| B・C | macOS | 同期ジョブと一部の自動化は LaunchAgent です。Linux ではセットアップが crontab の行を表示し、それらの自動化は `unsupported` になります |
| 任意 | Orca | 複数セッションを扱うスキル（`orchestrate`、`dispatch-card`、`handoff-ticket`）と定期ラウンド |
| 任意 | Linear か Jira | トラッカー系スキル。`tracker.adapter` がなければ Linear |
| 任意 | Obsidian か Markdown フォルダ | ノート系スキル。`notes.adapter` を決めるまでは無効 |
| 任意 | tailscale、caddy | `preview-watch` と `share-server`。どちらも既定で無効 |

## A. マーケットプレイスのみ

Claude Code のセッションで:

```
/plugin marketplace add JeongJaeSoon/agent-skills
/plugin install agent-skills@jeongjaesoon
```

インストール画面で値を 3 つ聞かれます。すべて空欄でかまいません。

- `language`: 応答の言語。
- `checkout`: B でこのリポジトリを clone する場所（既定 `~/src/agent-skills`）。
- `personal_repo`: 自分の `agent-skills.json` を置いた private リポジトリ。URL にトークンを入れないでください。この値は `settings.json` にそのまま残ります。

シェルからは、同じ値を `--config` で渡します。

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon --config language=Japanese --config checkout=$HOME/src/agent-skills
```

```
✔ Successfully added marketplace: jeongjaesoon (declared in user settings)
✔ Successfully installed plugin: agent-skills@jeongjaesoon (scope: user)
1 userConfig option not yet set — run /plugin configure agent-skills@jeongjaesoon in Claude Code, or pass --config KEY=VALUE.
```

最後の行は、値を 1 つ空欄にしたというだけの意味です。プラグインはセッションの開始時に読み込まれるので、新しいセッションを開きます。新しいセッションが一度だけ `setup-pc` を提案します。B に進むなら受け入れ、断ればそのプロファイルでは二度と聞かれません。

## B. セッションからフルセットアップ

1. A のあと新しいセッションを開き、`setup-pc` の提案を受け入れます（「新しい PC に agent-skills をセットアップして」と頼んでもかまいません）。
2. `gh` がログインしていなければ `! gh auth login`。エージェントが代わりにログインすることはありません。
3. エージェントがチェックアウトと個人設定リポジトリを clone し、dry run でファイルごとの diff を見せます。まだ何も書いていません。diff を読みます。
4. 渡された 1 行 `! python3 -I <チェックアウト>/scripts/setup.py --settings-only … --apply <ハッシュ>` を貼り付けます。ハッシュは計画全体のものなので、diff のあとで何かが変わっていれば拒否されます。その場合は dry run からやり直します。最後にファイルごとの戻し方の行が出ます。
5. エージェントが `setup.py --actions-only` を実行すると、権限ダイアログが出ます。同期ジョブを入れて既定の自動化を有効にする段階で、Claude の設定は書きません。承認します。
6. セッションを再起動します。ここで `/reload-plugins` は使いません。適用前から動いていたセッションでは、マーケットプレイス版だけが外れてチェックアウトは読み込まれず、再起動するまで agent-skills がまるごと使えなくなります。

## C. シェルからフルセットアップ

```bash
git clone https://github.com/JeongJaeSoon/agent-skills ~/src/agent-skills
python3 ~/src/agent-skills/scripts/setup.py --language Japanese       # dry run: ファイルごとの diff と --apply の行
python3 -I ~/src/agent-skills/scripts/setup.py --language Japanese --apply <ハッシュ>
```

- チェックアウトは `main` ブランチのメインチェックアウトである必要があります。worktree や別のブランチでは拒否されます。
- dry run は何も書きません。1 行目の `target <パス>` が書き込む `settings.json`、最後の行が実行する `--apply` コマンドです。この行は Claude Code のセッションに貼るための `! ` で始まるので、シェルでは `!` を外します。
- `--settings-only` を付けない `--apply` は、同期ジョブの設置、1 回の同期、既定の自動化の有効化まで行い、点検表を表示します。
- マーケットプレイス版が入っていれば、diff はそれを無効にします（`enabledPlugins` を `false` に）。両方が有効だと hook が 2 回ずつ動くためです。セットアップが既存の値を変えるのはこの 1 つだけです。

オプション、個人設定リポジトリの形、推奨設定それぞれの意味は [new-pc.md](new-pc.md)（韓国語）にあります。

## 最小構成

必須の設定はありません。`~/.claude/agent-skills.json` がなければ、機能ごとに無効になるか、初めて必要になったときに聞いてきます。書き込み先を推測することはありません。B・C はキーを 2 つ（`reflect.skill_repository`、`reap.user_folders: []`）入れます。

トラッカーとノートのフォルダを決める例:

```json
{
  "tracker": {"adapter": "jira"},
  "notes": {"adapter": "markdown", "markdown": {"root": "~/notes"}}
}
```

`tracker.adapter` がなければトラッカー系スキルは Linear を使います。点検表は未設定の `tracker.adapter` を `info` として出しますが、エラーではありません。キーの一覧と既定の動きは [configuration-migration.md](configuration-migration.md)（韓国語）と各スキルの設定の節にあり、変更は `skills-config` スキルが行います。

## 確認

A のあとの `claude plugin list`:

```
  ❯ agent-skills@jeongjaesoon
    Version: <コミット>
    Scope: user
    Status: ✔ enabled
```

B・C のあとは、チェックアウトが読み込まれ、マーケットプレイス版は無効になります。

```
  ❯ agent-skills@jeongjaesoon
    Status: ✘ disabled

Session-only plugins (--plugin-dir / --plugin-url):
  ❯ agent-skills@inline
    Path: <チェックアウト>
    Status: ✔ loaded
```

続けて `python3 <チェックアウト>/skills/setup-pc/scripts/setup_check.py check`。読むだけです。`missing`・`differs`・`warn` の行が残りの作業で、最後の列にやり方があります。コミット署名と `gh auth login` は常に人が行います。

セッションで `/agent-skills:brief` が状況報告で答えれば、スキルは読み込まれています。`/brief` だけだと Claude Code の組み込みコマンドになります。

## 更新

- **マーケットプレイス版:** `claude plugin marketplace update jeongjaesoon`、`claude plugin update agent-skills@jeongjaesoon` のあと、動いているセッションで `/reload-plugins`。
- **チェックアウト:** 同期ジョブが 15 分ごとに fast-forward するので、スキルは自動で最新になります。新しい推奨設定は `python3 <チェックアウト>/scripts/setup.py --update`（dry run）が表示する `--apply` の行で入れるか、セッションに「agent-skills を更新して」と頼みます。個人設定リポジトリがあれば先に `git -C ~/.config/agent-skills/personal pull --ff-only`。ほかにこのフォルダを pull するものはありません。

## 安全装置

場面ごとに、既定が人が承認する HITL（Human-in-the-loop）か、エージェントが進めて人は見守る HOTL（Human-on-the-loop）か、そしてその変え方です。

| 場面 | 既定 | 変え方 |
|---|---|---|
| ユーザー設定の適用（`setup-pc`、`tune-automode`、`skills-config`） | HITL: エージェントは diff と `!` の 1 行を作るだけで、実行は人 | 変えられない |
| `setup.py --actions-only`（同期・自動化の有効化） | HITL: `guard.py` が auto mode でも権限ダイアログを出す | 変えられない |
| そのほかの権限ダイアログ | HITL: 人が承認する | `permissions.allow` に項目を足すと、そのコマンドは聞かずに進む（ユーザー設定の memory パスのルールと MCP ツール名は `skills-config`、Bash のルールとプロジェクト設定は `update-config`） |
| auto mode 分類器による拒否 | HITL: エージェントは報告して止まる | `tune-automode` が作った autoMode ルールを人が適用すると、その種類は進む。判定のない拒否（`no verdict`）はルールにできない |
| 取り消せない操作（破壊的な migration、共有ブランチへの force push、本番デプロイ、外部への送信） | HITL: 止まって聞く | 変えられない |
| `orchestrate` のマージ方針 | HOTL: `autonomous`。ワーカーが `orch land` でマージする（そのリポジトリでのマージを許す autoMode ルールがあるとき。なければ聞く） | `human-gate` にすると、ワーカーは READY で止まり、人がゲートを外す |
| `deliver-ticket` が単独のカードで行うマージ | HOTL: 準備のできた PR を聞かずにマージする（そのマージを許す autoMode ルールがあるとき。なければ聞く） | hold をかけるか自分で見ると伝えると、マージ前に聞く。失敗を繰り返すチェックや取り消せない migration のような例外でも聞く |
| `watch-prs` の `ownergate`（ボット PR の承認） | 無効。`prs.kinds` に入れて `prs.ownergate` にゲート項目を書くと HITL: 人が `!` の行で承認する | 本番でなく autoMode の allow ルールが覆っていれば、ラウンドが実行する（HOTL） |
| `watch-prs` の `ready_to_merge` | 無効 | `prs.kinds` に足すと、`merge_repos` にある自分の PR のうち全ゲートを通ったものをマージする（HOTL） |
| `secure-fill` による秘密値の入力 | HITL: 毎回 Touch ID で承認 | 許可リストの項目を `"approval": "none"` にすると Touch ID なしで入力する（`-T ""` で作った項目なら Keychain の確認は残る） |
| `janitor`、`pressure-watch` | HOTL: エージェントが残した終了済みのリソースを定期的に片付ける | `<チェックアウト>/bin/skills-sync setup disable <名前>` |

HOTL のとき人が見るのは、`brief` の状況報告と、ダッシュボードの決定・権限ダイアログ・受信箱です。ダッシュボード、`janitor`、watch のラウンドは Orca を前提にしています。詳しくは [operations.md](operations.md#안전-경계)（韓国語）。

## アンインストール

マーケットプレイス版:

```bash
claude plugin uninstall agent-skills@jeongjaesoon
claude plugin marketplace remove jeongjaesoon
```

`enabledPlugins`、`extraKnownMarketplaces`、`pluginConfigs` の該当項目も消えます。

フルセットアップ（B・C）では、さらに次を戻します。

| セットアップが変えたもの | 戻し方 |
|---|---|
| `settings.json` の `env.CLAUDE_CODE_PLUGIN_DIRS` とほかの推奨設定 | `--apply` が表示した戻しの行（`cp settings.json.bak-setup-<時刻> settings.json`）を実行するか、不要なキーを消す。動いているセッションは再起動する |
| 同期ジョブ（macOS） | `launchctl bootout gui/$(id -u)/io.github.jeongjaesoon.agent-skills-sync` のあと `~/Library/LaunchAgents/io.github.jeongjaesoon.agent-skills-sync.plist` を削除 |
| 自動化 | 1 つずつ `<チェックアウト>/bin/skills-sync setup disable <名前>`。LaunchAgent のものはその agent も止める。手で入れた `bin/reap-hard` とダッシュボードの agent は `<チェックアウト>/bin/reap-hard uninstall`、`<チェックアウト>/bin/orch-dash uninstall-agent` |
| 状態ファイル | `~/.local/state/agent-skills/` を削除 |

残るものが 2 つあります。Orca の automation 登録は `disable` で止まるだけで削除はされません。`~/.claude/agent-skills.json` は何も消しません。

## トラブルシューティング

- **`/reload-plugins` のあとスキルが消えた。** フルセットアップの適用前から動いていたセッションです。再起動してください。
- **セットアップが、自分のセッションとは別の `settings.json` に書いた。** Claude Code は `CLAUDE_CONFIG_DIR` があれば `$CLAUDE_CONFIG_DIR/settings.json` を、なければ `~/.claude/settings.json` を読みます（Orca はプロファイルごとに別の値を渡します）。dry run の 1 行目 `target <パス>` がセットアップの対象で、`--settings <ファイル>` で別のものを選べます。プロファイルが複数あれば、プロファイルごとに 1 回ずつ行います。`agent-skills.json` は全プロファイル共通の 1 ファイルです。
- **`refused: … is a worktree` や `… is on 'feature'` が出る。** `main` のメインチェックアウトから実行します。
- **hook が 2 回動く。** マーケットプレイス版とチェックアウトの両方が有効です。セットアップの dry run を実行すると、その diff がマーケットプレイス版を無効にします。
- **Linux。** セットアップが表示した crontab の行を追加します。LaunchAgent の自動化には対応していません。
