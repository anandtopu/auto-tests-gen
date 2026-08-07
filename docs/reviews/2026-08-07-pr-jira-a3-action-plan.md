# A3 Plan-first from PR - Review action plan

| ID | Priority | Status | Finding | Resolution/evidence |
| --- | --- | --- | --- | --- |
| A3-R1 | P1 | Fixed | PR plan buttons were rendered while `AIQE_PR_PLAN=0`, violating default-preserving rollout. | Server-rendered controls and release-queue option now follow the resolved flag; on/off render test. |
| A3-R2 | P1 | Fixed | Dynamic queue rows displayed a PR plan as the repo name because JavaScript only recognized mode `pr`. | Client `keyOf` recognizes `plan` plus PR number; source/queue tests pin `PR-<repo>-<pr>`. |
| A3-R3 | P1 | Fixed | A resumed plan could lose ticket delivery if revalidation was temporarily unavailable. | Stored validated ticket is the delivery fallback; discovery still wins when available. |
| A3-R4 | P2 | Fixed | Direct pipeline and persisted target validation accepted a wider PR-number domain than queue intake. | All boundaries accept only canonical 1-9 digit positive PR numbers. |

Release recommendation: A3 is acceptable behind `AIQE_PR_PLAN=0`. Enable it
per estate only after SCM/JIRA comment permissions and one representative
plan/approve/resume journey are validated.
