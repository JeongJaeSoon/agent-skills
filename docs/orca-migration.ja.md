# オーケストレーションの状態を Orca へ移す（2026-11-01）

このリリースから、カードの状態はモデルが手で書いた行ではなく Orca から読みます。何が移ったか、リリース前に始めたセッションがどう動き続けるか、切り替えの順序と元に戻す方法をまとめます。

## 移ったもの

| 以前 | これから |
|---|---|
| カードごとにオーケストレーターが書く board の doing 行 | カードの Orca worktree の欄（`workspaceStatus`、`linkedPR`、`linkedItems`）と、あれば Run の Task。doing 行はカードのない待ちにだけ使う |
| リードごとの `orch board lead add` | リードの行はセッションから作る。`lead add` は fleet から見えないリードにだけ使い、見えるリードには拒否される（2026-11-04） |
| カードごとの `orch record spawned` | 台帳は Orca の Task・Dispatch を読む。`spawned` は下位単位のチケットにだけ使う |
| カードごとの `orch sent add` | Sent 表は Run の Task から作る。`orch sent` には Orca を通らない経路だけを記録する |
| `orca orchestration send`・`reply` ごとの `orch sent add`(2026-11-02) | Sent 表は Orca の inbox を読む。heartbeat だけは除く |
| 画面から待ちを判定 | まず Orca の `agentWait` を見て、Orca が見ていないときだけ画面を読む |
| 監督するカードも普通の worktree | `REPORT` が `run:<id>` のカードは `skills-sync card` が `worker-start` で立ち上げる |
| カードのコメントに書いていたチケット・PR のリンク | `--reference <URL>` で Orca のリンクとして保存する |
| 同じ問いに決定の記録と gate の両方 | Task を止める決定なら gate、そうでなければ `orch decide` |

重い作業、保留、依頼、段階表、やること・いまのリスト、ブリーフのレンダラー、heartbeat、コレクターの副作用は orch に残ります。

## 以前のセッションと並べて動かすとき

- **リリース前に立ち上げたカード**は、読み込んだコードのまま動き続けます。そのカードの doing 行は、`orch migrate --apply` か tidy が閉じるまで開いたままです。tidy はカードを表示名か worktree のディレクトリ名から大文字小文字を区別せずに探し、Orca の `linkedPR` が merged であることもマージの根拠として受け付けます。チケット ID だけやブランチ名はキーにしないので、親のチケットを書いた後続の行は開いたまま残ります。
- **リリース前に立ち上げたリード**は、まだ `orch sent add` を使うことがあります。board が `agent-skills.db` に移ったあとは `orch board lead add` が拒否され、代わりに行をつなぐ `orch board set` の行がメッセージに出ます。
- **`term_` のアドレスに報告するブリーフ**は、新しい `skills-sync card` が拒否します（終了コード 2）。Run の外のターミナルには `worker_done` が届かないためです。その行を `run:<id>` に変えるか、消してカード自身のチャットに報告させます。
- **`agentWait` を返さない Orca** では、これまでどおり画面を読みます。
- 古いコードと新しいコードは同じファイルを読み書きします。`orch migrate` は通常の tidy 行を足すだけなので、古いコードもその行を読めます。

## 切り替えの順序

マージやカードの立ち上げが進んでいないときに行います。どの手順も、動いているカードには触れません。

1. 読み込まれる main のチェックアウトをリリースまで fast-forward します（[platform.md](platform.md)、韓国語）。
2. オーケストレーター、次に各リードで、ターンの区切りに `/reload-plugins`。動いているカードは何もしなくてかまいません。
3. オーケストレーターで `orch doctor`。`이 터미널은 Run에 묶여 있지 않다`(この端末はどの Run にもバインドされていない)と答えたときだけ、以前のブリーフが報告していた Run で `orca orchestration run-use --id <id>`。Run が一つもないときだけ新しく作ります。`orca orchestration run-current --json` を直接読むときは、`result.runId` か `result.run.id` があればバインド済みです。
4. `orch migrate`（dry-run）。最初のリスト（カード一つを指す doing 行）を確認します。人を待つ行、オーナーの行、オーナーが開き直した行、カードはマージ済みでも残りの手順（リリース、デプロイ）を書いた行は入っていません。最後のものは別に一覧され、そのまま残ります。
5. `orch migrate --apply`。表示されるバックアップのディレクトリと `orch board reopen` の行を控えておきます。
6. ダッシュボードを確認します。Sessions 表に Orca から来た Ticket・PR の列があり、board の開いた doing リストには残った待ちだけがあります。

## 元に戻す

- **状態：** `orch migrate --restore <バックアップのディレクトリ>` は、その実行が閉じた行を開き直し、そのあとオーナーが変えたものはそのままにします。古いコードが読み込まれているときは、代わりに `--apply` が出力した `orch board reopen` の行を実行します。バックアップには実行前の `agent-skills.db` のコピーが 0600 のファイルとして入っています。
- **コード：** 読み込まれるチェックアウトを前のリリースのタグに戻し、もう一度 `/reload-plugins`。
- **Orca の値：** `workspaceStatus` はカードが自分の欄にだけ書き（PR を開くと `in-review`、閉じるときに `completed`）、リンクは新しいコードが立ち上げたカードにだけ付きます。状態を戻すには `orca worktree set --worktree <sel> --workspace-status in-progress`。

# 決定を agent-skills.db へ移す（2026-11-04）

このリリースから、`orch decide` とダッシュボードの回答は、決定を `decisions.json` ではなく fleet の状態ディレクトリ（`$ORCH_FLEET_STATE`）にある SQLite ファイル `agent-skills.db` に置きます。board と tidy のファイルは、次の節で同じファイルへ移します。

## 変わること

- 閉じた決定を 7 日後に消しません。テーブルに残り、読む側が状態で絞り込みます。
- 決定を変えるたびに、変更前と変更後の値を同じトランザクションで `event` テーブルに残します。
- ファイルを作るのは `orch db import` だけです。それまでは `orch decide` が `orch db cutover-plan 4a` を案内して止まり、decision hook は何もせず、brief の headline に `이행 대기: 결정 DB` が出ます。
- 表を見るときは `orch db dump [table]`（JSON 出力）を使います。ファイルを直接開くときは `sqlite3 -readonly` だけにします。
- DB のコピーは `cp` ではなく `orch db backup <file>` で作ります。`-wal` なしでコピーしたファイルは最後の書き込みを失うことがあります。状態ディレクトリは同期フォルダではなくローカルディスクに置きます。

## 切り替えの順序

古いコードはまだ `decisions.json` に書くので、データを移している間は動いていてはいけません。`orch db cutover-plan 4a` が次の手順を、この PC で打つコマンドとして出力します。このコマンド自体は何も実行しません。

1. ダッシュボードのサーバーを止めます（ページの回答を届けるのはここです）。macOS ではその LaunchAgent を `launchctl bootout` します。
2. 読み込まれるメインのチェックアウトを fast-forward します。
3. 動いている Claude セッションをすべて終了し、新しいプロセスとして起動し直します（`/reload-plugins` も `/restart` も pid と開始時刻が変わらないので通りません）。各セッションは自分が読み込んだ decision hook を呼ぶからです。
4. `orch db import --dry-run` で、移る決定の数と `--apply` が拒否するかどうかを確かめます。
5. `orch db import --apply`。次のどれか一つでもあれば拒否します。別のチェックアウトのスクリプト・ラッパー・hook を実行しているプロセス、または fast-forward 前からそれらを実行し続けているプロセス、どの Claude home でもそれより前に始まった生きているセッション、別のコミットのコピーを読み込む plugin や `--plugin-dir` で別のチェックアウトを読み込んだセッション、まだ読み込まれている LaunchAgent（pid がなくても）のうち、このシェルの label のダッシュボードか、`~/Library/LaunchAgents` で agent-skills のスクリプト・ラッパー・hook を実行するもの、それらを実行するコメントアウトされていない crontab の行（更新したチェックアウトを指していても該当するので、切り替えの間はコメントアウトします）。確かめられないときも拒否します。`ps` や `crontab -l` が失敗したとき（crontab のないユーザーや `crontab` コマンドがない場合は問題ありません）、プロセスのディレクトリやチェックアウトの更新時刻を読めないときです。通れば board・tidy・decision のロックを取り、`decisions.json` を sha256 と一緒に `backup-db-<時刻>/`（0700、ファイルは 0600）にコピーし、一時的な名前で DB を作って検査してから所定の場所へ rename し、`decisions.json` の場所に空のディレクトリを置いて古いコードの書き込みを失敗させます。
6. ダッシュボードのサーバーをもう一度起動します（macOS では `orch-dash install-agent`）。

`--apply` は何度実行しても安全です。import はコミットされたのに空のディレクトリをまだ置けていなければ、再実行は `decisions.json` の sha256 が import 時と同じときだけ空のディレクトリを置きます。その間にファイルが変わっていれば、衝突を示して止まり、何も置き換えません。切り替えのあと空のディレクトリの場所にファイルができると、brief に `옛 코드가 쓴 흔적` が出ます。

## 元に戻す（決定）

1. 切り替えの手順 1 と同じく書き手を止め、`orch db backup <file>`。古い形式に置き場のないイベント履歴まで、すべてが残ります。
2. `orch db export --legacy --target pre-4a <dir>` が古いコードの読む `decisions.json` を書き、WAL の checkpoint が済んだかを知らせます。busy と出たらまだ開いている接続があるので、先に止めます。
3. `agent-skills.db` を `-wal`・`-shm` と一緒に状態ディレクトリの外へ移し、空の `decisions.json` ディレクトリを消して、`<dir>/decisions.json` をその場所に置きます。
4. 前のリリースに戻して fast-forward し、書き手をもう一度起動します。

別の PC へ移すときは、元の PC で `orch db backup <file>`、新しい PC で `orch db restore <file>`。`restore` も `--apply` と同じ検査をし、新しい状態ディレクトリに `decisions.json`・`board.json`・`tidy.jsonl` のファイルがあれば拒否します。

# board を agent-skills.db へ移す（2026-11-04）

このリリースから、board、やること・保留のリスト、リード、すべての閉じた記録も、`board.json`・`tidy.jsonl` ではなく `agent-skills.db` に置きます。決定をまだ移していない PC は、一度の実行で両方の段階を済ませます。

## 変わること

- doing・保留・やること・いまのリストの項目には、それぞれ安定した id `b<N>` が付きます。言い回しを変えてもリストを移っても変わらず、ほかの仕事に再び使われることはありません。古い `doing:`・`later:`・`do:`・`todo:` の id は、import のときに指していた項目を引き続き指します。
- リストから外した項目は行が `dropped` として残り、最後の値は `event` に残ります。
- `board.prev.json` と `tidy.jsonl.1` はなくなります。変更前後の値は `event` にあり、二つのファイルは import がバックアップへ移します。
- 生きているカードや subagent を指すタイトルの `orch board doing add` は拒否します。それらには自分の行があるからです。`--force --why <理由>` を付けると追加し、理由をイベントに残します。
- カードにつながった行への `orch board set --pr` と、別のカードを指す `--worktree` は拒否します。行はつながりを保ち、Orca の PR を表示します。つながっていない行は `--worktree` で一度だけつなげます。
- Orca が知っているリードへの `orch board lead add` は拒否します。行をつなぐには `orch board set <lead> --kind lead --repo <repo> --worktree <worktree>` を使います。Orca から見えないリードには、これまでどおり `lead add` を使います。
- 受信箱の項目を閉じても `inbox-external.jsonl` には書きません。閉じた記録が見ていた行を覚えているので、そのキーの新しい行ができればその行が決め、開き直すのに新しい行は要りません。ファイルが書き直されてその行がなくなると、最後の行が決め、ブリーフの headline に `인박스 기록 어긋남` が出ます。
- `orch db import` の前は、board のコマンドが `orch db migrate`（メインチェックアウトの絶対パス）を案内して止まり、`orch board open` も同じ行を stderr に出し、ブリーフの headline に `이행 대기: 보드·결정 DB` が出て、行はすべて ❔ になります。

## 1 本のコマンドで切り替える（`orch db migrate`）

オーナーが普通のターミナルで、メインチェックアウトの `bin/orch db migrate` を打ちます（絶対パスは board のコマンドと `orch db cutover-plan 4b` の案内に出ます）。Claude セッションの中では拒否します。サービスを止めて戻すのはオーナーの仕事だからです。流れは次のとおりです。

1. すでに移行済みなら「already migrated」と言って何も変えません。何度実行しても同じです。移行後に古いコードがレガシーファイルを置き直していれば、そう伝えて止まります。
2. 状態を書くものを止めます。dashboard の LaunchAgent、agent-skills-sync、`ensure` で起動した dashboard サーバー、janitor と watch-prs の automation です。最初に止める前に、戻す手順を `cutover-recover.json` に書きます。LaunchAgent の plist にある `ORCH_*` と `PROGRAMS_HOME` は読むだけで（シェルに無いときだけ実行中の環境変数として使い、plist は書き換えません）、シェルの値と違えば拒否します。
3. 拒否の理由が残っていれば（古いコードで動くセッション・プロセス・cron の行）、表にして示し、何も移行せずに止めたサービスを戻します。セッションは `/reload-plugins` でも `/restart` でも通りません。終了して新しく始めたものだけが通ります。
4. `orch db import` と同じ移行を行い、`migration` の各ステップと `orch board show` で確かめます（`orch board open` は移行前でも正常終了するので確認になりません）。
5. 終わっても、失敗しても、途中で強制終了されても、止めたものを戻します。LaunchAgent は `install-agent` で plist を書き直さず、既存の plist をそのまま `launchctl bootstrap` します。止まっていた automation は起動しません。戻せなかったものは `cutover-recover.json` に残り、次の実行が先に戻します。
6. 実行中は `cutover.lock` を握っているので、再起動したセッションの `orch wait` や `ensure` が dashboard サーバーを戻すことはできません。

`orch db migrate --check` は、何を止めるかと何が妨げているかを示し、何も書きません。ほかの PC は main を取り込むと（agent-skills-sync が 15 分以内に取り込みます）、board のコマンドとブリーフがこの 1 行を案内します。新しい PC は `skills-sync setup` が `orch db init` を実行して空の DB を作るので、移行は要りません。すでに状態のある PC では何もしません。

## 手で切り替える順序

`orch db cutover-plan 4b` が、以下の手順をこの PC で打つコマンドとして出力します。このコマンド自体は何も実行しません。上の 1 本のコマンドがこの順序の代わりをします。

1. ダッシュボードのサーバーを止め、janitor を切ります。macOS では LaunchAgent を `launchctl bootout` し、`skills-sync setup disable janitor` を実行します。
2. 読み込まれるメインのチェックアウトを fast-forward します。
3. 動いている Claude セッションをすべて終了し、新しいプロセスとして起動し直します（`/reload-plugins` も `/restart` も通りません）。
4. `orch db import --dry-run` で移す内容を確認します。リストごとの項目数、閉じた記録、行とリード、Orca が持っているので外れるリードと行の PR、そして `--apply` が拒否するかどうかです。
5. `orch db import --apply`。決定の段階と同じ検査をします。board のバックアップは `backup-db-*` に入り、ファイルごとの sha256 は `migration` テーブルに残ります。`board.json`・`tidy.jsonl` の場所には空のディレクトリを置き、決定のリリースより古いコードの書き込みを失敗させます。決定のリリースのコードは `4b-board` の行を見て止まります。
6. ダッシュボードのサーバーと janitor をもう一度起動します。

`--apply` は決定のときと同じく、もう一度実行しても安全です。ファイルごとに import のときの sha256 のままであるときだけ空のディレクトリを置き、変わったファイルがあれば衝突を示して止まります。import が失敗しても何も変わりません。段階全体が一つのトランザクションだからです。

## 元に戻す（board）

1. 切り替えの手順 1 と同じく書き手を止め、`orch db backup <file>`。
2. `orch db export --legacy --target 4a <dir>` が `board.json`、`tidy.jsonl`、閉じた記録を行として書き出した `inbox-external.jsonl`、`export.json` を書きます。同じ言葉が二つの状態にあると古い形式では表せないので、export がその項目を示して止まります。片方を閉じるか言い回しを変えてから実行し直します。
3. `orch db rollback 4b <dir>` が export のあと DB と受信箱が変わっていないことを確かめ、空のディレクトリの場所に三つのファイルを置き、最後に `4b-board` の行を消します。途中で落ちたら、もう一度実行します。
4. 前のリリースに戻して fast-forward し、書き手をもう一度起動します。

あとで import すれば、ファイルをもう一度取り込みます。テーブルは置き換わり、番号は続きから振られるので、ロールバック前の `b<N>` が再び使われることはありません。決定の段階まで戻すときは、この節を終えてから「元に戻す（決定）」に従います。
