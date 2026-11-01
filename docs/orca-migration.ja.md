# オーケストレーションの状態を Orca へ移す（2026-11-01）

このリリースから、カードの状態はモデルが手で書いた行ではなく Orca から読みます。何が移ったか、リリース前に始めたセッションがどう動き続けるか、切り替えの順序と元に戻す方法をまとめます。

## 移ったもの

| 以前 | これから |
|---|---|
| カードごとにオーケストレーターが書く board の doing 行 | カードの Orca worktree の欄（`workspaceStatus`、`linkedPR`、`linkedItems`）と、あれば Run の Task。doing 行はカードのない待ちにだけ使う |
| リードごとの `orch board lead add` | リードの行はセッションから作る。`lead add` は fleet から見えないリードにだけ使う |
| カードごとの `orch record spawned` | 台帳は Orca の Task・Dispatch を読む。`spawned` は下位単位のチケットにだけ使う |
| カードごとの `orch sent add` | Sent 表は Run の Task から作る。`orch sent` には Orca を通らない経路だけを記録する |
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

- **状態：** `orch migrate --restore <バックアップのディレクトリ>` は、その実行が閉じた行を開き直し、そのあとオーナーが変えたものはそのままにします。古いコードが読み込まれているときは、代わりに `--apply` が出力した `orch board reopen` の行を実行します。バックアップには実行前の `board.json`、`tidy.jsonl`、`decisions.json` が 0600 のファイルとして入っています。
- **コード：** 読み込まれるチェックアウトを前のリリースのタグに戻し、もう一度 `/reload-plugins`。
- **Orca の値：** `workspaceStatus` はカードが自分の欄にだけ書き（PR を開くと `in-review`、閉じるときに `completed`）、リンクは新しいコードが立ち上げたカードにだけ付きます。状態を戻すには `orca worktree set --worktree <sel> --workspace-status in-progress`。
