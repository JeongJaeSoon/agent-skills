# `skills-sync card` exit codes

A nonzero exit is a failing check, not a refusal: fix what it names and run the same command again.

| Exit | Meaning | Next step |
|---|---|---|
| 2 | A missing line (named in the message), a `term_` report address, a `path:` parent, a form `--agent codex` does not support, or an APPROVED id the store does not hold as answered | Add or fix the line in the brief |
| 3 | The pressure gate's reason | Wait as it says, then run the same command |
| 4 | Orca's create or `worker-start` error, with Orca's whole receipt on stderr | See below |
| 5 | The card exists, but its link, log or name failed | Do that step by hand; do not create the card again |
| 6 | Nothing was created: the Run or the parent did not check out | Pick one of the printed choices, never `--no-parent` or `--card` just to get past it |

Exit 4. Orca can fail a launch whose outcome it could not confirm. Before running it again, follow
the receipt's recovery commands, and for a `worker-start` the `request-show` line the helper prints.
`completed` or `pending` there means it took or may still take effect. `absent` is no proof that
nothing started: check `orca worktree ps` and `orca orchestration worker-list` too. A 4 for a
refused parent or depth lists the choices instead, and nothing was created.
