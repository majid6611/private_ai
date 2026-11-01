# Permission contract (identity/project subset implemented)

Default deny. An installation administrator manages accounts and memberships but does
not implicitly bypass document ACLs. Initial project roles are reader, editor and
approver; capabilities compose explicitly. A document may restrict project access
further but may not grant access to a non-member. History is a separate capability.

| Operation | Required human capability | Delegated AI |
|---|---|---|
| List/read/search/download | Membership and document/version read | Task scope intersected with current user access |
| Upload/create draft | Project create; document edit for new version | Only explicit task scope |
| Read history | Document read and history | Explicit task scope |
| Request approval | Document edit and workflow request | Explicit task scope |
| Approve | Current document read and approval capability; interactive session | Never |
| Publish | Current publish capability and valid human approval | Not exposed in initial tool allowlist |
| Change membership/ACL | Explicit administration capability | Never |
| Rollback | Publish capability and new approval bound to historical target/current pointer | Never |

Initial publication uses expected_active_version_id=null. Approval is one-use,
version/hash-bound and invalidated by relevant permission/policy changes. A candidate
must belong to the document. Published bytes are immutable. Rollback changes the
active pointer through the same approval transaction; it never mutates history.
Pilot policy requires an approver different from the candidate author/requester.
Concurrent ACL changes and publication use a common locking protocol. No domain
endpoint is implemented until its permission checks are present.


Current API uses explicit project membership plus independent edit/approval/publish/
history booleans, document ACLs and grants, human approval and publication/rollback.
Read-only AI delegation is implemented as user-bound expiring tasks revalidated against
session, project ACL revision and publication revision; AI never approves or publishes.
No installation-admin bypass is applied to project list/detail or documents.
