# オーケストレーションの状態を Orca へ移す（2026-11-01）

このリリースから、カードの状態はモデルが手で書いた行ではなく Orca から読みます。何が移ったか、リリース前に始めたセッションがどう動き続けるか、切り替えの順序と元に戻す方法をまとめます。

## 移ったもの

| 以前 | これから |
|---|---|
| カードごとにオーケストレーターが書く board の doing 行 | カードの Orca worktree の欄（`workspaceStatus`、`linkedPR`、`linkedItems`）と、あれば Run の Task。doing 行はカードのない待ちにだけ使う |
| リードごとの `orch board lead add` | リードの行はセッションから作る。`lead add` は fleet から見えないリードにだけ使う |
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
- **リリース前に立ち上げたリード**は、まだ `orch board lead add` と `orch sent add` を使うことがあります。fleet は board のリードとそのセッションをターミナルの handle でまとめるので、リードは一度だけ表示されます。
- **`term_` のアドレスに報告するブリーフ**は、新しい `skills-sync card` が拒否します（終了コード 2）。Run の外のターミナルには `worker_done` が届かないためです。その行を `run:<id>` に変えるか、消してカード自身のチャットに報告させます。
- **`agentWait` を返さない Orca** では、これまでどおり画面を読みます。
- 古いコードと新しいコードは同じファイルを読み書きします。`orch migrate` は通常の tidy 行を足すだけなので、古いコードもその行を読めます。

## 切り替えの順序

マージやカードの立ち上げが進んでいないときに行います。どの手順も、動いているカードには触れません。

1. 読み込まれる main のチェックアウトをリリースまで fast-forward します（[platform.md](platform.md)、韓国語）。
2. オーケストレーター、次に各リードで、ターンの区切りに `/reload-plugins`。動いているカードは何もしなくてかまいません。
3. オーケストレーターで `orca orchestration run-current --json`。null なら、以前のブリーフが報告していた Run で `orca orchestration run-use <id>`。Run が一つもないときだけ新しく作ります。
4. `orch migrate`（dry-run）。最初のリスト（カード一つを指す doing 行）を確認します。人を待つ行、オーナーの行、オーナーが開き直した行、カードはマージ済みでも残りの手順（リリース、デプロイ）を書いた行は入っていません。最後のものは別に一覧され、そのまま残ります。
5. `orch migrate --apply`。表示されるバックアップのディレクトリと `orch board reopen` の行を控えておきます。
6. ダッシュボードを確認します。Sessions 表に Orca から来た Ticket・PR の列があり、board の開いた doing リストには残った待ちだけがあります。

## 元に戻す

- **状態：** `orch migrate --restore <バックアップのディレクトリ>` は、その実行が閉じた行を開き直し、そのあとオーナーが変えたものはそのままにします。古いコードが読み込まれているときは、代わりに `--apply` が出力した `orch board reopen` の行を実行します。バックアップには実行前の `board.json`・`tidy.jsonl`、まだファイルのうちは `decisions.json`、決定が移ったあとは `agent-skills.db` のコピーが、どれも 0600 のファイルとして入っています。
- **コード：** 読み込まれるチェックアウトを前のリリースのタグに戻し、もう一度 `/reload-plugins`。
- **Orca の値：** `workspaceStatus` はカードが自分の欄にだけ書き（PR を開くと `in-review`、閉じるときに `completed`）、リンクは新しいコードが立ち上げたカードにだけ付きます。状態を戻すには `orca worktree set --worktree <sel> --workspace-status in-progress`。

# 決定を agent-skills.db へ移す（2026-11-04）

このリリースから、`orch decide` とダッシュボードの回答は、決定を `decisions.json` ではなく fleet の状態ディレクトリ（`$ORCH_FLEET_STATE`）にある SQLite ファイル `agent-skills.db` に置きます。board と tidy のファイルはまだそのままで、後のリリースで同じファイルへ移します。

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
3. 動いている Claude セッションをすべて再起動するか `/reload-plugins` します。各セッションは自分が読み込んだ decision hook を呼ぶからです。
4. `orch db import --dry-run` で、移る決定の数と `--apply` が拒否するかどうかを確かめます。
5. `orch db import --apply`。次のどれか一つでもあれば拒否します。別のチェックアウトのスクリプト・ラッパー・hook を実行しているプロセス、または fast-forward 前からそれらを実行し続けているプロセス、どの Claude home でもそれより前に始まった生きているセッション、別のコミットのコピーを読み込む plugin や `--plugin-dir` で別のチェックアウトを読み込んだセッション、まだ読み込まれている LaunchAgent（pid がなくても）のうち、このシェルの label のダッシュボードか、`~/Library/LaunchAgents` で agent-skills のスクリプト・ラッパー・hook を実行するもの、それらを実行するコメントアウトされていない crontab の行（更新したチェックアウトを指していても該当するので、切り替えの間はコメントアウトします）。確かめられないときも拒否します。`ps` や `crontab -l` が失敗したとき（crontab のないユーザーや `crontab` コマンドがない場合は問題ありません）、プロセスのディレクトリやチェックアウトの更新時刻を読めないときです。通れば board・tidy・decision のロックを取り、`decisions.json` を sha256 と一緒に `backup-db-<時刻>/`（0700、ファイルは 0600）にコピーし、一時的な名前で DB を作って検査してから所定の場所へ rename し、`decisions.json` の場所に空のディレクトリを置いて古いコードの書き込みを失敗させます。
6. ダッシュボードのサーバーをもう一度起動します（macOS では `orch-dash install-agent`）。

`--apply` は何度実行しても安全です。import はコミットされたのに空のディレクトリをまだ置けていなければ、再実行は `decisions.json` の sha256 が import 時と同じときだけ空のディレクトリを置きます。その間にファイルが変わっていれば、衝突を示して止まり、何も置き換えません。切り替えのあと空のディレクトリの場所にファイルができると、brief に `옛 코드가 쓴 흔적` が出ます。

## 元に戻す（決定）

1. 切り替えの手順 1 と同じく書き手を止め、`orch db backup <file>`。古い形式に置き場のないイベント履歴まで、すべてが残ります。
2. `orch db export --legacy --target pre-4a <dir>` が古いコードの読む `decisions.json` を書き、WAL の checkpoint が済んだかを知らせます。busy と出たらまだ開いている接続があるので、先に止めます。
3. `agent-skills.db` を `-wal`・`-shm` と一緒に状態ディレクトリの外へ移し、空の `decisions.json` ディレクトリを消して、`<dir>/decisions.json` をその場所に置きます。
4. 前のリリースに戻して fast-forward し、書き手をもう一度起動します。

別の PC へ移すときは、元の PC で `orch db backup <file>`、新しい PC で `orch db restore <file>`。`restore` も `--apply` と同じ検査をし、新しい状態ディレクトリに `decisions.json` のファイルがあれば拒否します。
