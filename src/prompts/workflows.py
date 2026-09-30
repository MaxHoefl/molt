"""The two orchestration scripts, as prompt text.

A prompt is the only place in an MCP server where the *order* of tool calls can be
stated, and `audit_and_upgrade` is where the human checkpoint stops being a hope and
becomes an instruction. It is deliberately blunt about the one rule that matters —
apply_plan needs a proposal and named packages — because a model that improvises
here edits someone's repository.

`license_check` exists to prove the tools are not pipeline stages pretending to be
tools: the same eleven functions compose into a much smaller workflow that never
triages and never proposes a diff.
"""

AUDIT_AND_UPGRADE = """\
You are running a Molt dependency audit for {project_path} (distribution model:
{distribution}). Follow this workflow strictly.

1. Call recall_project_context for {project_path}. Read the procedural rules it
   returns and take them into account for the rest of the audit — they are what this
   user has actually decided before. Mention any rule you are acting on.
2. Call scan_project on {project_path}, then enrich_dependencies with the full
   package list from its manifest. Enrichment is the only network-heavy step; do it
   once and reuse its output.
3. Call assess_security, assess_license and assess_maintenance on the enriched
   packages. They are independent of each other — issue the three calls together
   rather than waiting for one before starting the next. Pass the project's declared
   license and '{distribution}' to assess_license.
4. Call triage_dependencies with all three verdict lists and the project path. For
   every entry routed to REPLACE, call find_replacement with the package name, the
   reason from the entry's rationale, and — if you can tell from the codebase — how
   the project uses it.
5. Call propose_upgrade_plan with the triage plan, the project path, and one
   replacement entry per REPLACE package you resolved. Present the returned diff and
   the per-package rationale to the user verbatim. Do not summarize away the
   unresolved items: those are the ones that need them, not you.
6. You may call apply_plan only with the proposal_id from step 5, and only with
   packages the user has explicitly approved. If the user approved nothing, do not
   call apply_plan at all. If the proposal came back 'pending_explicit_apply', ask
   the user package by package first and pass exactly what they name.
7. If the user resolved any open item in conversation — "legal cleared chardet",
   "we accept that CVE" — record it with record_decision before you finish, with
   their reason in their own words.
8. Close with a short summary: what was applied, what was rejected, what is still
   open, and what Molt learned for next time.

Never edit a file yourself. apply_plan is the only tool that writes, and it will
refuse anything that was not reviewed first.
"""

LICENSE_CHECK = """\
Check only the license compatibility of {project_path} (distribution model:
{distribution}). Do not triage, do not propose a diff, and do not call apply_plan.

1. Call scan_project on {project_path}, then enrich_dependencies with its packages.
2. Call assess_license with the enriched packages, the project's declared license
   from the scan, and '{distribution}'.
3. Present every verdict that is not 'compatible', with the conflicting clause the
   agent cited, grouped by license. Say plainly how many packages were cleared.
4. For each 'requires_human_review' finding, ask the user to choose one of:
   (a) record a resolution — then call record_decision with action
       'resolved_human_review' and their reason,
   (b) look for replacements — then call find_replacement for that package,
   (c) leave it open — then do nothing and say it remains open.

A license verdict is not legal advice, and this workflow never resolves ambiguity on
the user's behalf. Escalating is the correct outcome, not a failure.
"""
