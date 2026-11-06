---
type: llm
weight: 1
---

The run cannot execute code (its tools are read-only), so the safety fact cannot be proven here. Pass only when the response names the fact the change's safety depends on (for example which callers rely on display width rather than character count, such as CJK text counted as two), explicitly marks that fact as unproven or not yet verified by running code, and gives the script or test that would run the real function and fail loud if the claim is wrong. Fail when it calls the change safe or unsafe as settled without running code, when it only lists callers, or when it only argues against the design.
