import { Component as e, createContext as t, createElement as n, memo as r, useCallback as i, useContext as a, useEffect as o, useId as s, useLayoutEffect as c, useMemo as l, useRef as u, useState as d, useSyncExternalStore as f } from "react";
import { Fragment as p, jsx as m, jsxs as h } from "react/jsx-runtime";
import { ChatEmbed as g, useAppApi as _, useNavBadge as v, useNavigate as y } from "@kirocrew/app-sdk";
import { Btn as b, ContentSkeleton as x, EmptyState as S, MarkdownRenderer as C, SearchInput as w, SegmentedControl as T } from "@kirocrew/app-sdk/ui";
import E from "lucide-react";
//#region \0rolldown/runtime.js
var D = Object.defineProperty, O = (e, t) => {
	let n = {};
	for (var r in e) D(n, r, {
		get: e[r],
		enumerable: !0
	});
	return t || D(n, Symbol.toStringTag, { value: "Module" }), n;
}, k = {
	"a11y.queueRow": "{type}, {repo}, {intent}, {stage}, {severity}, waiting {duration}. {primary}",
	"a11y.refreshing": "Evidence is refreshing",
	"a11y.skipToDetail": "Skip to decision",
	"action.budget_stop.headline": "A budget you set stopped this intent before the next turn.",
	"action.circuit_breaker.headline": "Studio stopped dispatching this intent after {count} failures of the same kind.",
	"action.delivery_uncertain.consequence.no_replay": "Studio will not send it again on its own. It is watching AI-DLC's own files for the answer, and will ask you before anything is resent.",
	"action.delivery_uncertain.headline": "Studio cannot tell whether your last decision reached the conversation.",
	"action.failure.consequence.no_dispatch": "Nothing more is dispatched to this intent until you retry or leave it paused.",
	"action.failure.headline": "The AI-DLC engine failed while working on {stage}.",
	"action.force_stop.headline": "Force stop the turn running on {stage}.",
	"action.gate.consequence.approve_unlocks": "Approving lets AI-DLC close {stage} and start the next stage. Requesting changes sends your note back and reopens the gate.",
	"action.gate.consequence.final_stage": "Approving closes {stage}, the last stage in this plan, and completes the workflow.",
	"action.gate.headline": "AI-DLC finished {stage} and is waiting for your approval before it goes on.",
	"action.install_conflict.consequence.nothing_written": "Nothing has been written to your repository, and AI-DLC will not run here until this is resolved.",
	"action.install_conflict.headline": "A file that Studio manages in {engine_dir} does not match what it installed.",
	"action.missing_input.headline": "AI-DLC needs something from you before it can start: {reason}.",
	"action.prepare_commit.headline": "Ask the conversation to prepare a commit for the current changes.",
	"action.question.consequence.advances_turn": "Your answers are sent as one message, and the conversation continues from them.",
	"action.question.consequence.plan_approval": "Your reply approves the recorded plan or requests changes before implementation continues.",
	"action.question.consequence.summary_confirmation": "Your reply confirms the recorded summary or requests changes before the stage continues.",
	"action.question.headline": "{stage} cannot continue until you answer its open questions. {pending} still pending.",
	"action.question.plan_approval.headline": "{stage} is waiting for you to approve its plan.",
	"action.question.summary_confirmation.headline": "{stage} is waiting for you to confirm its summary.",
	"action.reason.dangling_cursor": "the active-intent cursor names a record that has no state file",
	"action.reason.missing_scope": "no scope has been chosen",
	"action.reason.needs_input": "the stage is waiting for input",
	"action.reason.questions": "unanswered questions",
	"action.reason.recovery_details": "review the recorded evidence below",
	"action.reason.recovery_required": "an install recovery is required",
	"action.reason.session_lost_mid_stage": "the bound conversation could not be found",
	"action.recovery.consequence.blocked_until_agree": "Acknowledging dismisses this notice. Resolve the reported cause before continuing.",
	"action.recovery.headline": "This intent needs recovery: {reason}.",
	"action.resume.headline": "Resume this intent from where it was parked.",
	"action.revision.headline": "{stage} is being revised after your feedback. Revision {revision_count}.",
	"action.run.consequence.one_turn": "This runs exactly one turn. It stops at the next gate, question, failure or completion — Studio never continues unattended.",
	"action.run.headline": "Run {stage} to the next point that needs you.",
	"activity.action.cancelled": "Action cancelled before anything was sent.",
	"activity.action.created": "Action created and queued for your decision.",
	"activity.action.delivery": "Delivery outcome recorded.",
	"activity.action.failed": "Dispatch failed.",
	"activity.action.resolved": "Action resolved from observed evidence.",
	"activity.action.retried": "Retry requested.",
	"activity.action.submitted": "Decision submitted into the canonical session.",
	"activity.action.updated": "Action updated.",
	"activity.advisor.auto_requested": "Advisor draft requested automatically, because this repository is set to draft ahead. It reads only; it cannot move the workflow.",
	"activity.advisor.completed": "Advisor draft ready. It is a draft, not a decision.",
	"activity.advisor.failed": "Advisor draft failed.",
	"activity.advisor.requested": "Advisor draft requested. It reads only; it cannot move the workflow.",
	"activity.breaker.opened": "Circuit opened after repeated matching failures. No further dispatch until it is reset.",
	"activity.breaker.reset": "Circuit reset.",
	"activity.calibration.cleared": "Estimate calibration history cleared.",
	"activity.engine.run": "AI-DLC engine command run.",
	"activity.evidence.action": "Action",
	"activity.evidence.at": "Recorded at",
	"activity.evidence.auditEvent": "Audit event",
	"activity.evidence.auditShard": "Audit shard",
	"activity.evidence.commit": "Commit",
	"activity.evidence.copy": "Copy the raw block",
	"activity.evidence.fields": "Recorded values",
	"activity.evidence.fieldsNone": "This event recorded no values.",
	"activity.evidence.kind": "Kind",
	"activity.evidence.location": "File location",
	"activity.evidence.locationNone": "No file location was recorded for this event.",
	"activity.evidence.messageKey": "Message key",
	"activity.evidence.provenance": "Provenance",
	"activity.evidence.rawNone.aidlc": "The raw block was not returned with this row. Raw blocks come with the merged intent timeline — choose one repository and one intent in the scope bar.",
	"activity.evidence.rawNone.studio": "Studio rows carry no audit block, because Studio writes none. What Studio recorded is listed below, verbatim.",
	"activity.evidence.rawTitle": "Raw audit block",
	"activity.evidence.redaction": "Credentials and tokens are removed from every value before it leaves the backend, and paths outside the registered repository are not shown.",
	"activity.evidence.session": "Session",
	"activity.evidence.severity": "Severity",
	"activity.evidence.source.aidlc": "Read from the AI-DLC audit shard named below.",
	"activity.evidence.source.git": "Observed by reading Git. Studio never runs a Git write.",
	"activity.evidence.source.kirocrew": "Observed in the KiroCrew session named below.",
	"activity.evidence.source.slack": "Recorded from a Slack correlation callback. It authorises nothing.",
	"activity.evidence.source.studio": "Recorded by Studio. Studio never writes into the AI-DLC audit trail.",
	"activity.evidence.title": "Evidence drawer",
	"activity.export.discard": "Discard it",
	"activity.export.download": "Download {filename}",
	"activity.export.failed": "The export could not be produced: {message}",
	"activity.export.humanText": "Include prompt bodies and typed feedback",
	"activity.export.humanTextBlocked": "Allow it in Settings first. With the setting off the backend refuses to include human text, whatever the export asks for.",
	"activity.export.humanTextWarn": "The export will then contain the exact text you sent into sessions. Share it with care.",
	"activity.export.includes.actions": "Live actions, leases, recent transactions and circuit breakers.",
	"activity.export.includes.activity": "Recent Activity rows.",
	"activity.export.includes.health": "Health, versions and payload integrity.",
	"activity.export.includes.repos": "Registered repositories and their install state.",
	"activity.export.includes.settings": "Studio settings.",
	"activity.export.includesTitle": "What the export contains",
	"activity.export.lede": "Nothing is produced until you ask. Read what is removed first.",
	"activity.export.produce": "Produce the export",
	"activity.export.producing": "Producing…",
	"activity.export.ready": "Export ready — {size}, generated {when}.",
	"activity.export.redacts.artifacts": "Artifact contents are never included, only their metadata.",
	"activity.export.redacts.credentials": "Credentials, tokens and secrets are removed from every string.",
	"activity.export.redacts.humanText": "Prompt bodies and the feedback you typed are omitted.",
	"activity.export.redacts.paths": "Paths are scrubbed down to the registered repository roots.",
	"activity.export.redacts.unrelated": "Content from repositories you did not register is never read.",
	"activity.export.redactsTitle": "What the export removes",
	"activity.export.region": "Diagnostic export",
	"activity.export.title": "Diagnostic export",
	"activity.filter.allSources": "All sources",
	"activity.filter.anyKind": "Any event",
	"activity.filter.anySeverity": "Any severity",
	"activity.filter.anyStage": "Any stage",
	"activity.filter.anyType": "Any action type",
	"activity.filter.applied_one": "{n} filter applied",
	"activity.filter.applied_other": "{n} filters applied",
	"activity.filter.clear": "Clear filters",
	"activity.filter.kind": "Event",
	"activity.filter.severity": "Severity",
	"activity.filter.since": "From",
	"activity.filter.source": "Source",
	"activity.filter.stage": "Stage",
	"activity.filter.title": "Filters",
	"activity.filter.type": "Action type",
	"activity.filter.typeUnavailable": "Action type filters Studio's action table, which the merged intent timeline does not join.",
	"activity.filter.until": "Until",
	"activity.health.startup": "Studio started.",
	"activity.install.transaction": "Install transaction recorded.",
	"activity.intent.archived": "Intent archived.",
	"activity.intent.created": "Intent created.",
	"activity.intent.force_stop": "Force stop requested on the bound session.",
	"activity.intent.paused": "Intent paused. Nothing is dispatched while it is paused.",
	"activity.intent.restored": "Intent restored from the archive.",
	"activity.intent.resumed": "Intent resumed.",
	"activity.lease.acquired": "Repository execution lease acquired.",
	"activity.lease.reclaimed": "Orphaned lease reclaimed.",
	"activity.lease.released": "Repository execution lease released.",
	"activity.machine_lane.refused": "Unattended dispatch refused. Studio never forges human presence.",
	"activity.migration.applied": "Prototype migration applied.",
	"activity.migration.previewed": "Prototype migration previewed. Nothing was written.",
	"activity.notification.sent": "Notification sent.",
	"activity.page.count_one": "{n} event",
	"activity.page.count_other": "{n} events",
	"activity.page.empty.body": "Widen the time range, or choose All sources. Activity records what has already happened; it never predicts.",
	"activity.page.empty.title": "No events match these filters",
	"activity.page.emptyScope.body": "Register a repository and open an intent. Every action Studio takes is written here with its source.",
	"activity.page.emptyScope.title": "Nothing recorded yet",
	"activity.page.error": "Activity could not be read: {message}",
	"activity.page.lede": "Human-readable timeline. Every event names its source, and a Studio-derived event is never shown as an AI-DLC audit event.",
	"activity.page.mergedNote": "One repository and one intent are in scope, so that intent's AI-DLC audit events are projected live beside Studio's own rows. The server applies the source filter; the rest are applied in the browser to the {n} events read.",
	"activity.page.narrowed": "{shown} shown of {loaded} read",
	"activity.page.newer": "Newer",
	"activity.page.older": "Older",
	"activity.page.pageNumber": "Page {n}",
	"activity.page.provenance": "Studio-derived events are labelled Studio. They are never presented as original AI-DLC audit events.",
	"activity.page.reading": "Reading activity…",
	"activity.page.redaction": "The Evidence drawer exposes raw audit blocks and file locations. Diagnostic export redacts credentials, protected paths and prompt bodies by default.",
	"activity.page.retry": "Read again",
	"activity.page.sourceNeedsScope": "AI-DLC audit events are read from one intent's audit shards. Choose a repository and an intent in the scope bar.",
	"activity.page.studioOnlyNote": "Studio's own records only. Choose one repository and one intent in the scope bar to read that intent's AI-DLC audit events beside them.",
	"activity.page.timeline": "Timeline",
	"activity.page.title": "Activity",
	"activity.repo.added": "Repository registered.",
	"activity.repo.rebound": "Repository path rebound after it moved.",
	"activity.repo.removed": "Repository unregistered. No bytes on disk changed.",
	"activity.repo.rescanned": "Repository rescanned.",
	"activity.repo.updated": "Repository label or archive state updated.",
	"activity.row.at": "Recorded at {when}",
	"activity.row.auditEvent": "AI-DLC audit event",
	"activity.row.commit": "commit {sha}",
	"activity.row.derived": "Studio-derived",
	"activity.row.evidence": "Evidence",
	"activity.row.evidenceFor": "Evidence for the {source} event at {time}",
	"activity.row.mismatch": "This row's source and its message do not agree, so Studio will not name it. The raw record is in the Evidence drawer.",
	"activity.row.openAction": "Open action",
	"activity.row.openActionFor": "Open action {action} in Action Center",
	"activity.row.session": "session {session}",
	"activity.row.shard": "{shard} #{pos}",
	"activity.row.shardNoPos": "{shard}",
	"activity.row.stage": "stage {stage}",
	"activity.row.unknownKind": "An event this version of Studio does not recognise.",
	"activity.session.bound": "Intent bound to a canonical session.",
	"activity.session.takeover": "Canonical session taken over.",
	"activity.session.unbound": "Intent unbound from its canonical session.",
	"activity.settings.updated": "Settings updated.",
	"activity.slack.callback": "Slack correlation recorded. It authorises nothing.",
	"activity.slack.sent": "Slack message sent.",
	"advisor.action.diagnose": "Diagnose with AI",
	"advisor.action.gate_analysis": "Analyze this decision",
	"advisor.action.question_draft": "Draft all answers",
	"advisor.action.question_draft_one": "Draft this",
	"advisor.action.question_explain": "Explain",
	"advisor.action.request_changes_draft": "Draft change-request feedback",
	"advisor.alternatives": "Alternatives",
	"advisor.applyPicks": "Apply the drafted selections",
	"advisor.applyPicksNote": "Nothing is submitted. Approve is never preselected.",
	"advisor.assumptions": "Assumptions",
	"advisor.confidence": "Confidence",
	"advisor.confidenceValue.high": "High",
	"advisor.confidenceValue.low": "Low",
	"advisor.confidenceValue.medium": "Medium",
	"advisor.disabled": "The Advisor is turned off in Settings, so nothing here can be drafted.",
	"advisor.disclaimer": "Advisor activity is recorded in Studio Activity only. It is never written into the AI-DLC audit trail, and it cannot mint a HUMAN_TURN.",
	"advisor.draftNotDecision": "draft, not a decision",
	"advisor.drawer.alternatives": "Alternatives",
	"advisor.drawer.applyAnswers": "Apply the drafted selections",
	"advisor.drawer.applyAnswersHint": "Nothing is submitted. Approve is never preselected.",
	"advisor.drawer.assumptions": "Assumptions",
	"advisor.drawer.badge": "draft, not a decision",
	"advisor.drawer.close": "Close the Advisor drawer",
	"advisor.drawer.confidence": "Confidence",
	"advisor.drawer.confidence.high": "High",
	"advisor.drawer.confidence.low": "Low",
	"advisor.drawer.confidence.medium": "Medium",
	"advisor.drawer.disclaimer": "Advisor activity is recorded in Studio Activity only. It is never written into the AI-DLC audit trail, and it cannot mint a HUMAN_TURN.",
	"advisor.drawer.empty": "The Advisor returned no draft.",
	"advisor.drawer.error": "The draft could not be read: {message}",
	"advisor.drawer.evidence": "Evidence",
	"advisor.drawer.expires": "This draft expires {when}.",
	"advisor.drawer.kind.diagnose": "Diagnosis",
	"advisor.drawer.kind.gate_analysis": "Gate analysis",
	"advisor.drawer.kind.plan_draft": "Plan proposal",
	"advisor.drawer.kind.question_draft": "Drafted answers",
	"advisor.drawer.kind.question_explain": "Question explained",
	"advisor.drawer.kind.request_changes_draft": "Drafted change request",
	"advisor.drawer.needsYourDecision": "Needs your decision",
	"advisor.drawer.needsYourDecisionChip": "unresolvable from evidence",
	"advisor.drawer.status.expired": "This draft expired. Ask again to get a fresh one.",
	"advisor.drawer.status.failed": "The Advisor could not finish: {message}",
	"advisor.drawer.status.queued": "Queued. The Advisor runs in its own read-only session.",
	"advisor.drawer.status.ready": "Ready",
	"advisor.drawer.status.running": "Running…",
	"advisor.drawer.suggested": "Drafted answers",
	"advisor.drawer.suggestedQuestion": "Q{index}",
	"advisor.drawer.summary": "Reading",
	"advisor.drawer.title": "AI Advisor — draft only",
	"advisor.drawer.useFeedback": "Put the drafted feedback in the box",
	"advisor.drawer.useFeedbackHint": "You can edit it before anything is sent.",
	"advisor.error.agent_error": "The Advisor session returned an error.",
	"advisor.error.bad_result": "The Advisor's answer did not match the expected shape, so it was discarded rather than shown.",
	"advisor.error.evidence_mutated": "The evidence changed while the Advisor was reading it, so the draft was discarded.",
	"advisor.error.spawn_failed": "The Advisor session could not be started.",
	"advisor.error.timed_out": "The Advisor did not answer in time.",
	"advisor.error.unknown": "The Advisor could not produce a draft.",
	"advisor.evidence": "Evidence",
	"advisor.evidenceMoved": "The evidence changed while the Advisor was reading it, so this draft describes a situation that no longer exists. Ask again.",
	"advisor.expired": "This draft has expired. Ask again to get a fresh one.",
	"advisor.kind.diagnose": "a diagnosis",
	"advisor.kind.gate_analysis": "a gate analysis",
	"advisor.kind.plan_draft": "a plan proposal",
	"advisor.kind.question_draft": "drafted answers",
	"advisor.kind.question_explain": "an explanation",
	"advisor.kind.request_changes_draft": "drafted feedback",
	"advisor.needsYourDecision": "Needs your decision",
	"advisor.notRun": "Advisor has not run",
	"advisor.notRunCopy": "The Advisor runs only when it is asked — by you, here, or once per card if this repository has been granted drafting ahead in Settings. It uses a separate read-only session, cannot write files or move the workflow, and never produces human-turn evidence.",
	"advisor.prefilled": "Filled in from the Advisor's draft. Nothing has been sent — review and change anything before you submit.",
	"advisor.prefilledClear": "Clear these answers",
	"advisor.running": "Reading the evidence for {kind}…",
	"advisor.title": "AI Advisor",
	"advisor.titleDraft": "AI Advisor — draft only",
	"advisor.unresolvable": "unresolvable from the evidence",
	"advisor.useFeedback": "Put the drafted feedback in the box",
	"advisor.useFeedbackNote": "You can edit it before anything is sent.",
	"advisor.verdict.approve_recommended": "Approving is consistent with the evidence",
	"advisor.verdict.needs_your_decision": "This needs your decision",
	"advisor.verdict.none": "No verdict",
	"advisor.verdict.request_changes_recommended": "Requesting changes is consistent with the evidence",
	"artifact.binary.body": "The bytes are not UTF-8 text, so there is nothing Studio can show without guessing an encoding. The path is below.",
	"artifact.binary.title": "This file is not text",
	"artifact.blank": "This file is empty.",
	"artifact.block.files": "Files in this record",
	"artifact.block.produced": "Produced artifact",
	"artifact.diff.added": "added",
	"artifact.diff.fromGit": "prior version derived from Git",
	"artifact.diff.fromUnknown": "prior version derived from a retained observation",
	"artifact.diff.label": "Changes to {name} since the committed version",
	"artifact.diff.removed": "removed",
	"artifact.diff.showRest": "Show the remaining {n} lines",
	"artifact.diff.title": "Change since the committed version",
	"artifact.diff.unavailable": "Git could not produce a comparison. The artifact above is still the current file on disk.",
	"artifact.diff.unchanged": "No change since the committed version.",
	"artifact.empty": "Studio has not read this file yet.",
	"artifact.empty.body": "Artifacts appear here as soon as a stage writes them. Studio reads them from disk and never creates them.",
	"artifact.empty.title": "No artifacts recorded for this record yet",
	"artifact.kind.artifact": "artifact",
	"artifact.kind.contribution": "contribution",
	"artifact.kind.memory": "memory",
	"artifact.kind.other": "file",
	"artifact.kind.questions": "questions",
	"artifact.kind.review": "review",
	"artifact.kind.traceability": "traceability",
	"artifact.list.filter": "Filter files",
	"artifact.list.missing": "The file this link names is not in this record. The first artifact is shown instead.",
	"artifact.list.noMatch": "No file matches {query}.",
	"artifact.list.noStage": "Not associated with a stage",
	"artifact.list.notRendered": "not rendered",
	"artifact.list.title": "{n} files",
	"artifact.list.truncated": "Studio stopped listing files at its cap. The files shown are real; the list is not complete.",
	"artifact.meta.sha256": "SHA-256",
	"artifact.meta.size": "Size",
	"artifact.meta.stage": "Stage",
	"artifact.meta.unit": "Unit",
	"artifact.meta.updated": "Updated",
	"artifact.notRendered.body": "Studio renders artifacts read-only within the size and file-type range the backend reports, and this one ({size}, {kind}) is outside it. Nothing was truncated, because half an artifact reads as a whole one.",
	"artifact.notRendered.title": "Studio did not render this file",
	"artifact.openInEditor": "Open in editor",
	"artifact.pane.label": "Artifact {name}, read-only",
	"artifact.readOnly": "read-only",
	"artifact.toc.label": "Sections of this artifact",
	"artifact.tooLarge.body": "The file is {size}, over the {cap} this read allows. Studio refuses a partial read rather than showing part of an artifact as if it were all of it.",
	"artifact.tooLarge.title": "This file is too large to render",
	"artifact.truncated": "Studio read only part of this file. The rest is on disk and was not sent to the browser.",
	"audit.ARTIFACT_CREATED": "Artifact created by the stage agent.",
	"audit.ARTIFACT_UPDATED": "Artifact updated by the stage agent.",
	"audit.DECISION_RECORDED": "A decision was recorded.",
	"audit.DEPTH_CHANGED": "Workflow depth changed.",
	"audit.DOCUMENT_INDEXED": "A customer document was indexed into the knowledge base.",
	"audit.DOCUMENT_REMOVED": "An indexed document's original is gone; its record is now a tombstone.",
	"audit.DOCUMENT_UPDATED": "An indexed document's record changed.",
	"audit.ERROR_LOGGED": "An error was logged.",
	"audit.GATE_APPROVED": "Gate approved.",
	"audit.GATE_REJECTED": "Changes were requested at the Gate.",
	"audit.GUARDRAIL_LOADED": "Guardrails loaded.",
	"audit.HEALTH_CHECKED": "Workspace health checked.",
	"audit.HUMAN_TURN": "A human turn was recorded. Only a real user prompt mints this.",
	"audit.MEMORY_EMPTY": "No prior memory was found.",
	"audit.PHASE_COMPLETED": "Phase completed.",
	"audit.PHASE_SKIPPED": "Phase skipped.",
	"audit.PHASE_STARTED": "Phase started.",
	"audit.PHASE_VERIFIED": "Phase verified.",
	"audit.PIPELINE_LINK_COMPLETED": "A declared pipeline link completed in order.",
	"audit.PLAN_APPROVAL_RECORDED": "A Code Generation plan approval was recorded.",
	"audit.QUESTION_ANSWERED": "Questions answered.",
	"audit.RECOMPOSED": "The stage plan was recomposed.",
	"audit.REVIEW_COMPLETED": "Reviewer pass completed.",
	"audit.REVIEW_REQUESTED": "Reviewer pass requested.",
	"audit.RULE_LEARNED": "A rule was learned into memory.",
	"audit.SCOPE_CHANGED": "Scope changed.",
	"audit.SENSOR_FAILED": "A sensor failed.",
	"audit.SENSOR_FIRED": "A sensor fired.",
	"audit.SENSOR_PASSED": "A sensor passed.",
	"audit.SESSION_COMPACTED": "The session transcript was compacted.",
	"audit.SESSION_ENDED": "Session ended.",
	"audit.SESSION_RESUMED": "Session resumed.",
	"audit.SESSION_STARTED": "Session started.",
	"audit.STAGE_AWAITING_APPROVAL": "Stage reached its Gate and is awaiting a human decision.",
	"audit.STAGE_COMPLETED": "Stage completed.",
	"audit.STAGE_JUMPED": "The cursor jumped to another stage.",
	"audit.STAGE_REVISING": "Stage is being revised after requested changes.",
	"audit.STAGE_SKIPPED": "Stage skipped.",
	"audit.STAGE_STARTED": "Stage started.",
	"audit.SUBAGENT_COMPLETED": "A subagent finished.",
	"audit.SUMMARY_CONFIRMATION_RECORDED": "Summary confirmation recorded.",
	"audit.SWARM_SOURCE_MERGED": "A Swarm unit's reviewed source landed in main.",
	"audit.TEST_STRATEGY_CHANGED": "Test strategy changed.",
	"audit.UNIT_GATE_RHYTHM_SET": "The team's unit gate rhythm was set.",
	"audit.UNIT_MERGED": "A unit's pinned content landed in main and its row was folded.",
	"audit.UNIT_OWNERSHIP_SET": "Unit ownership mode was set.",
	"audit.WORKFLOW_COMPLETED": "Workflow completed.",
	"audit.WORKFLOW_PARKED": "Workflow parked.",
	"audit.WORKFLOW_STARTED": "Workflow started.",
	"audit.WORKFLOW_UNPARKED": "Workflow unparked.",
	"audit.WORKSPACE_INITIALISED": "Workspace initialised.",
	"audit.WORKSPACE_SCAFFOLDED": "Workspace scaffolded.",
	"audit.WORKSPACE_SCANNED": "Workspace scanned.",
	"audit.WORKTREE_CREATED": "A Git worktree was created.",
	"audit.unknown_event": "An audit event this version of Studio does not recognise. The raw block is in the Evidence drawer.",
	"common.cancel": "Cancel",
	"common.close": "Close",
	"common.copied": "Copied",
	"common.copy": "Copy",
	"common.durationDays": "{n} d",
	"common.durationHours": "{n} h",
	"common.durationMinutes": "{n} min",
	"common.estimate": "estimate",
	"common.exact": "exact",
	"common.justNow": "just now",
	"common.loading": "Loading…",
	"common.none": "None",
	"common.retry": "Retry",
	"common.unavailable": "Unavailable",
	"confirm.acknowledge.mark_not_delivered": "I have read the delivery evidence above and I am recording that the message never arrived.",
	"confirm.acknowledge.resubmit": "I have read the delivery evidence above and I am asking Studio to send this decision again.",
	"confirm.apply": "Yes, apply this",
	"confirm.atMostOnce": "At most once: after this point Studio reconciles from disk rather than resending.",
	"confirm.blocked.acknowledge": "Confirm that you have read the delivery evidence above.",
	"confirm.blocked.answersIncomplete": "Every question in the group has to be answered before sending.",
	"confirm.blocked.evidenceMissing": "Studio has no evidence digest for this card yet, so it will not ask the server to send it again.",
	"confirm.blocked.feedbackRequired": "Requesting changes needs a note saying what to change.",
	"confirm.blocked.groupedAnswers": "Sending several answers in one message is not verified yet. Answer them one at a time, or use the conversation.",
	"confirm.blocked.inputRequired": "Type the input this stage is missing.",
	"confirm.blocked.noWireText": "There is nothing to send yet.",
	"confirm.blocked.questionsUnavailable": "Answer this question group in the canonical conversation.",
	"confirm.blocked.refreshing": "A file used as evidence is changing right now, so nothing can be submitted against it.",
	"confirm.blocked.sessionUnbound": "A decision is injected into this intent's canonical conversation as a real user turn, and this intent has none bound. Open a KiroCrew chat on this repository with the aidlc agent and bind it to this intent; until then there is nothing for Studio to send into.",
	"confirm.consequence.acknowledge": "Studio closes this incident in its own records. The AI-DLC files are untouched, and the item returns if the evidence still disagrees.",
	"confirm.consequence.force_stop": "KiroCrew is asked to stop the turn that is running in this conversation. Nothing is written to AI-DLC’s files, and no message is added to the conversation.",
	"confirm.consequence.keep_paused": "The intent stays paused. No dispatch happens for it until you unpause it.",
	"confirm.consequence.mark_not_delivered": "Studio records that the conversation never received the message. It re-checks its own proof first and refuses if it cannot show that.",
	"confirm.consequence.pick_intent": "Studio asks the AI-DLC engine to make this the active intent. This is a command, not a prompt: it cannot create human-turn evidence.",
	"confirm.consequence.rebind_session": "Studio binds this intent to a fresh canonical session and re-verifies from the last stable boundary. No message is sent and no AI-DLC file is written.",
	"confirm.consequence.reconcile": "Studio re-reads the AI-DLC files and the conversation now instead of waiting for its next pass. Nothing is sent.",
	"confirm.consequence.resubmit": "Studio queues this decision again as a new item. It refuses if it can show the first attempt was delivered.",
	"confirm.consequence.retry_now": "Studio clears the repeated-failure count for this intent and lets the next dispatch happen. It does not resend anything already sent.",
	"confirm.consequence.run_now": "Studio queues one run for this intent. You confirm the exact command before anything is sent.",
	"confirm.hostControl": "{label} asks KiroCrew to stop the running turn. No message is added to the conversation.",
	"confirm.hostControlHint": "At most once: Studio records the stop request before making it, and reconciles rather than repeating it.",
	"confirm.label": "Confirmation",
	"confirm.labelSends": "Button: {label} → sends: {wire}",
	"confirm.mismatch": "KiroCrew recorded different text from what this page displayed. What was actually sent is below.",
	"confirm.noWireText": "nothing yet",
	"confirm.routing": "Injected as a real user turn into canonical session {session} for {repo}/{intent}. The Kiro userPromptSubmit hook mints the protected HUMAN_TURN; the AI-DLC engine commits the transition. Studio does not call report, edit aidlc-state.md, or set any bypass variable.",
	"confirm.send": "Yes, send this exact text",
	"confirm.studioOnly": "{label} changes only what Studio records. Nothing is sent to the conversation.",
	"confirm.studioOnlyHint": "This sends no message. Studio records your choice and keeps reading the files on disk.",
	"confirm.title.accept_as_is": "Confirm accepting this stage as it stands",
	"confirm.title.acknowledge": "Confirm you have read this incident",
	"confirm.title.answers": "Confirm the answer group sent as one action",
	"confirm.title.approve": "Confirm the exact text sent to the canonical session",
	"confirm.title.approve_plan": "Confirm the plan approval sent to the canonical session",
	"confirm.title.confirm_summary": "Confirm your reply to the summary checkpoint",
	"confirm.title.force_stop": "Confirm stopping the running turn",
	"confirm.title.keep_paused": "Confirm keeping this intent paused",
	"confirm.title.mark_not_delivered": "Confirm recording this decision as never sent",
	"confirm.title.pick_intent": "Confirm switching AI-DLC's active intent",
	"confirm.title.prepare_commit": "Confirm the commit request sent to the canonical session",
	"confirm.title.provide_input": "Confirm the input sent to the canonical session",
	"confirm.title.rebind_session": "Confirm rebinding this intent to another conversation",
	"confirm.title.reconcile": "Confirm re-reading the evidence now",
	"confirm.title.request_changes": "Confirm the change request sent to the canonical session",
	"confirm.title.request_plan_changes": "Confirm the plan change request sent to the canonical session",
	"confirm.title.resubmit": "Confirm sending this decision again",
	"confirm.title.resume": "Confirm the resume command sent to the canonical session",
	"confirm.title.retry_now": "Confirm retrying now",
	"confirm.title.run": "Confirm the run command sent to the canonical session",
	"confirm.title.run_now": "Confirm running this intent now",
	"decision.a11y.bar": "Decisions",
	"decision.accept_as_is.hint": "Accepts the artifact with its findings unresolved. Offered only after the engine has offered it.",
	"decision.accept_as_is.label": "Accept as-is",
	"decision.acknowledge.hint": "Records that you have read the contradiction and are parking it for manual review.",
	"decision.acknowledge.label": "Acknowledge",
	"decision.answers.hint": "Sends every pending answer as one action.",
	"decision.answers.label": "Send answers",
	"decision.approve.hint": "Sends your approval to the canonical session, so the engine advances past this gate.",
	"decision.approve.label": "Approve",
	"decision.approve_plan.hint": "Approves the plan the engine composed, so it may execute it.",
	"decision.approve_plan.label": "Approve plan",
	"decision.bar.advisorDiagnose": "Diagnose with AI",
	"decision.bar.advisorGate": "Ask the Advisor",
	"decision.bar.advisorQuestions": "Draft all answers",
	"decision.bar.showQueue": "Show the queue",
	"decision.bar.submitted": "Submitted as {id}. {status} — this item clears only after AI-DLC state moves.",
	"decision.blocked.acknowledge": "Confirm you have read the delivery evidence first.",
	"decision.blocked.answers_one": "{n} required answer is still missing.",
	"decision.blocked.answers_other": "{n} required answers are still missing.",
	"decision.blocked.choice": "Choose one of the options above. Studio does not pick for you.",
	"decision.blocked.feedback": "A change request needs a note saying what to change.",
	"decision.blocked.grouped": "Sending {n} answers in one message is not verified yet, so answer them in the conversation instead.",
	"decision.blocked.input": "Type what to send first. Studio infers nothing.",
	"decision.blocked.noEvidenceHash": "Studio has no evidence digest for this item, so it cannot prove what you would be acknowledging.",
	"decision.blocked.noQuestions": "There is no pending question on this item.",
	"decision.confirm_summary.changesHint": "Tells the engine its summary is wrong, with your note.",
	"decision.confirm_summary.changesLabel": "Request changes to the summary",
	"decision.confirm_summary.hint": "Confirms the engine's summary of this stage.",
	"decision.confirm_summary.label": "Confirm the summary",
	"decision.force_stop.hint": "Asks KiroCrew to stop the session. The turn already in flight may still finish.",
	"decision.force_stop.label": "Force stop",
	"decision.keep_paused.hint": "Leaves this intent stopped. Nothing is dispatched until you come back.",
	"decision.keep_paused.label": "Keep paused",
	"decision.mark_not_delivered.hint": "Records that the decision never reached the session, so you can send it again.",
	"decision.mark_not_delivered.label": "Record as never sent",
	"decision.nav.conversation": "Open the conversation",
	"decision.nav.intents": "Choose on the Intents page",
	"decision.nav.preview": "Run the upgrade preview",
	"decision.nav.receipt": "Open the receipt",
	"decision.nav.settings": "Open budget settings",
	"decision.pick_intent.hint": "Sets AI-DLC's active intent through an engine operation, from the intents on disk.",
	"decision.pick_intent.label": "Make this the active intent",
	"decision.prepare_commit.hint": "Asks the session to prepare a commit. Nothing is pushed.",
	"decision.prepare_commit.label": "Prepare a commit",
	"decision.provide_input.hint": "Sends what you typed to the canonical session as your turn.",
	"decision.provide_input.label": "Send the input",
	"decision.rebind_session.hint": "Binds this intent to a fresh canonical session and re-verifies from the last stable boundary.",
	"decision.rebind_session.label": "Rebind the conversation",
	"decision.reconcile.hint": "Re-reads state, audit and the session, and resolves this item from what they say.",
	"decision.reconcile.label": "Reconcile from disk",
	"decision.request_changes.hint": "Sends your note to the canonical session. The artifact is not edited by Studio.",
	"decision.request_changes.label": "Request changes",
	"decision.request_plan_changes.hint": "Sends your note about the plan to the canonical session.",
	"decision.request_plan_changes.label": "Request plan changes",
	"decision.resubmit.hint": "Sends the same decision once more. Available only after you have read the delivery evidence.",
	"decision.resubmit.label": "Send it again",
	"decision.resume.hint": "Resumes this intent from where the engine left off on disk.",
	"decision.resume.label": "Resume",
	"decision.retry_now.hint": "Resets the failure count for this intent and dispatches one turn.",
	"decision.retry_now.label": "Retry now",
	"decision.run.hint": "Dispatches one turn, under the repository lease.",
	"decision.run.label": "Run to next checkpoint",
	"decision.run_now.hint": "Runs one turn now, outside the budget that stopped it.",
	"decision.run_now.label": "Run now",
	"delivery.answerNeedsText": "Text still required",
	"delivery.answerNotVerified": "Reply not verified",
	"delivery.answerNotVerifiedBody": "The turn ended and a new gate opened, but the audit receipt did not match the reply sent. The idle lease was released. Review the conversation and the new gate; the earlier reply will not be resent.",
	"delivery.confirmed": "Delivery confirmed — workflow needs reconciliation",
	"delivery.confirmedBody": "The conversation received this message. Whether AI-DLC accepted the answer or changed the workflow is still unverified. This message will not be resent.",
	"delivery.fact.absent": "not found",
	"delivery.fact.bootUnchanged": "Same Studio process throughout",
	"delivery.fact.confirmed": "Delivery later confirmed",
	"delivery.fact.diskUnchanged": "Disk baseline unchanged",
	"delivery.fact.no": "no",
	"delivery.fact.slotRanSince": "Conversation ran since sending",
	"delivery.fact.transcriptRow": "Transcript row",
	"delivery.fact.yes": "yes",
	"delivery.label": "Delivery state",
	"delivery.newGateReview": "Review the new gate",
	"delivery.noTransition": "No workflow state change was recorded.",
	"delivery.previousPlanApproval": "Previous approval recorded",
	"delivery.previousPlanApprovalBody": "The original plan approval was recorded, and that reply is complete. The plan was later reset for review. Its current version still requires approval; no new write authority was granted and the earlier reply will not be resent.",
	"delivery.queuedInSlot": "Waiting in the conversation's queue",
	"delivery.reportFailed": "KiroCrew has the message but Studio could not record the outcome. It will reconcile from disk; the message is never sent a second time.",
	"delivery.sentAt": "sent {at}",
	"delivery.sentText": "Text sent to the canonical session",
	"delivery.state.cancelled": "cancelled",
	"delivery.state.done": "done",
	"delivery.state.failed": "stopped here",
	"delivery.state.future": "not started",
	"delivery.state.now": "in progress",
	"delivery.state.unchanged": "no change recorded",
	"delivery.step.delivered": "Sent",
	"delivery.step.processing": "Agent processing",
	"delivery.step.queued": "Queued",
	"delivery.step.reconciliation": "Reconcile workflow",
	"delivery.step.stateChanged": "State changed",
	"delivery.step.unchanged": "No state change",
	"delivery.uncertain": "Delivery uncertain — nothing was replayed",
	"delivery.uncertainBody": "Studio cannot prove whether the conversation received this message, so it is watching the AI-DLC files on disk and will not send it again. Choose what to do below.",
	"delivery.updatedPlanReview": "Review the updated plan",
	"delivery.watchingDisk": "Studio is watching disk state, not the HTTP receipt",
	"detail.additionalAttemptBlocked": "This additional attempt was blocked. An earlier delivery is recorded below.",
	"detail.bar.closed": "This is a closed record. Open current actions to continue.",
	"detail.bar.hint": "Choosing an action opens a confirmation that shows the exact text Studio will send.",
	"detail.bar.noDecisions": "There is nothing to decide here. Follow the recorded progress above.",
	"detail.bar.refreshing": "A file used as evidence is changing right now, so nothing can be submitted against it.",
	"detail.bar.showQueue": "Show the queue",
	"detail.bar.submitted": "Submitted as {id}. Follow its progress above.",
	"detail.breadcrumb": "Where this decision sits",
	"detail.closedAt": "Closed {at}",
	"detail.closedTitle": "{type} · {status}",
	"detail.conversation.none": "This intent has no conversation bound yet, so there is nothing to show.",
	"detail.conversation.note": "This is KiroCrew’s own transcript. Anything you type here is your turn, sent by your session — Studio does not read or rewrite it.",
	"detail.conversation.title": "Canonical session",
	"detail.copied": "copied",
	"detail.currentActions": "View current actions",
	"detail.deepLink": "deep link",
	"detail.deepLinkTitle": "Copy a link that reopens this decision",
	"detail.gone.body": "The action or its repository was removed. This saved page cannot send a decision. Open current actions to continue.",
	"detail.gone.title": "This action is no longer available",
	"detail.history": "Historical record",
	"detail.label": "Decision detail",
	"detail.noSelection.body": "Pick anything in the queue to see its evidence.",
	"detail.noSelection.title": "Select an item",
	"detail.noTemplate": "This build has no panel for {kind} yet. Nothing is being hidden from you — the panel does not exist.",
	"detail.nothingSent": "Nothing was sent and no decision was recorded.",
	"detail.refreshing": "Evidence refreshing",
	"detail.repoBusy.openOwner": "View the operation holding this repository",
	"detail.repoUnavailable.body": "This card shows a saved decision. Restore access or rebind the repository to its new location before continuing. If this was a disposable test, you can archive its registration in Repos.",
	"detail.repoUnavailable.footer": "Restore the repository before continuing this decision.",
	"detail.repoUnavailable.manage": "Manage repository",
	"detail.repoUnavailable.pathMissing": "The repository directory no longer exists at this path.",
	"detail.repoUnavailable.recordedAt": "Last recorded {at}",
	"detail.repoUnavailable.saved": "Saved decision",
	"detail.repoUnavailable.title": "Repository unavailable",
	"detail.repoUnavailable.unreadable": "Studio cannot read this repository right now.",
	"detail.reviewClass": "{name} review",
	"detail.revision": "revision {n}",
	"detail.staleRefused": "The refreshed evidence is above. Read it and confirm again; your earlier answer was not forwarded.",
	"detail.tab.activity": "Activity",
	"detail.tab.artifacts": "Artifacts",
	"detail.tab.conversation": "Conversation",
	"detail.tab.decision": "Decision",
	"detail.tab.review": "Review",
	"detail.tabsLabel": "Decision detail sections",
	"detail.transitions.empty": "Studio has not changed this item yet.",
	"detail.transitions.generation": "gen {n}",
	"detail.transitions.initial": "created",
	"detail.transitions.row": "{from} → {to}",
	"detail.transitions.title": "Studio's record of this decision",
	"detail.waiting": "waiting {duration}",
	"enum.actionStatus.Cancelled": "Cancelled",
	"enum.actionStatus.Delivered": "Sent",
	"enum.actionStatus.Delivering": "Sending",
	"enum.actionStatus.DeliveryUncertain": "Delivery uncertain",
	"enum.actionStatus.Draft": "Draft",
	"enum.actionStatus.Failed": "Failed",
	"enum.actionStatus.NotDelivered": "Not sent",
	"enum.actionStatus.Processing": "Being processed",
	"enum.actionStatus.Queued": "Queued",
	"enum.actionStatus.ReconciliationRequired": "Needs reconciliation",
	"enum.actionStatus.ResolvedNoTransition": "Answered",
	"enum.actionStatus.StateChanged": "Applied",
	"enum.actionType.budget_stop": "Budget stop",
	"enum.actionType.circuit_breaker": "Repeated failures",
	"enum.actionType.delivery_uncertain": "Delivery uncertain",
	"enum.actionType.failure": "Failure",
	"enum.actionType.force_stop": "Force stop",
	"enum.actionType.gate": "Approval gate",
	"enum.actionType.install_conflict": "Install conflict",
	"enum.actionType.missing_input": "Missing input",
	"enum.actionType.prepare_commit": "Prepare commit",
	"enum.actionType.question": "Question",
	"enum.actionType.recovery": "Recovery",
	"enum.actionType.resume": "Resume",
	"enum.actionType.revision": "Revision in progress",
	"enum.actionType.run": "Run",
	"enum.availability.available": "Available",
	"enum.availability.identity_unprovable": "Identity unprovable",
	"enum.availability.moved": "Moved",
	"enum.availability.permission_denied": "Permission denied",
	"enum.availability.unavailable": "Unavailable",
	"enum.findingSeverity.blocking": "Blocking",
	"enum.findingSeverity.info": "Information",
	"enum.findingSeverity.warn": "Warning",
	"enum.installStatus.drift": "Modified since install",
	"enum.installStatus.installed": "Installed",
	"enum.installStatus.not_installed": "Not installed",
	"enum.installStatus.recovery_required": "Install recovery required",
	"enum.intentState.Archived": "Archived",
	"enum.intentState.CircuitOpen": "Stopped after repeated failures",
	"enum.intentState.Completed": "Completed",
	"enum.intentState.Failed": "Failed",
	"enum.intentState.Idle": "Idle",
	"enum.intentState.Interrupted": "Interrupted",
	"enum.intentState.Parked": "Parked",
	"enum.intentState.Paused": "Paused",
	"enum.intentState.Queued": "Queued",
	"enum.intentState.ReconciliationRequired": "Needs reconciliation",
	"enum.intentState.RetryEligible": "Retry available",
	"enum.intentState.Running": "Running",
	"enum.intentState.WaitingForYou": "Waiting for you",
	"enum.ownership.framework": "Framework file",
	"enum.ownership.framework-mutable": "Framework file the engine rewrites",
	"enum.ownership.merge": "Merge target",
	"enum.ownership.shell": "Workspace shell",
	"enum.phase.construction": "Construction",
	"enum.phase.ideation": "Ideation",
	"enum.phase.inception": "Inception",
	"enum.phase.initialization": "Initialization",
	"enum.phase.operation": "Operation",
	"enum.severity.attention": "Needs attention",
	"enum.severity.blocking": "Blocking",
	"enum.severity.critical": "Critical",
	"enum.severity.info": "Information",
	"enum.source.aidlc": "AI-DLC",
	"enum.source.git": "Git",
	"enum.source.kirocrew": "KiroCrew",
	"enum.source.slack": "Slack",
	"enum.source.studio": "AI-DLC Studio",
	"enum.stageState.awaiting_approval": "Awaiting your approval",
	"enum.stageState.completed": "Completed",
	"enum.stageState.in_progress": "In progress",
	"enum.stageState.not_started": "Not started",
	"enum.stageState.revising": "Being revised",
	"enum.stageState.skipped": "Skipped",
	"enum.stageState.unknown": "Unknown",
	"errors.action_not_found": "That item is no longer in the queue.",
	"errors.action_not_submittable": "This item is not in a state that can be submitted.",
	"errors.action_stale": "The situation changed while you were deciding. Review the refreshed evidence and confirm again.",
	"errors.advisor_unavailable": "The AI Advisor is not available.",
	"errors.already_installed": "AI-DLC is already installed here.",
	"errors.answers_incomplete": "Every question in the group has to be answered before sending.",
	"errors.app_token_forbidden": "An app token cannot make this change.",
	"errors.artifact_not_found": "That artifact is not there.",
	"errors.bad_body": "The request body could not be read.",
	"errors.bad_param": "A parameter is missing or invalid.",
	"errors.bad_path": "That path is not usable.",
	"errors.breaker_open": "This intent stopped after repeated failures. Retry when the cause is fixed.",
	"errors.bun_missing": "bun was not found on PATH or in the usual install locations, so AI-DLC's tools cannot be run. Install bun, or start KiroCrew from a shell whose PATH has bun.",
	"errors.bun_missing_searched": "Studio looked in: {locations}",
	"errors.cancel_not_safe": "Cancelling is not provably safe at this point.",
	"errors.cursor_mismatch": "AI-DLC's active intent is not the one this decision belongs to.",
	"errors.delivery_ack_invalid": "That delivery report does not match the pending send.",
	"errors.draft_not_found": "That draft has expired.",
	"errors.duplicate_identity": "That directory is already registered under another entry.",
	"errors.engine_unavailable": "The AI-DLC engine cannot be invoked here.",
	"errors.feedback_required": "Requesting changes needs a note saying what to change.",
	"errors.git_missing": "git is not available, so repository observation is off.",
	"errors.grouped_answers_unavailable": "Answering several questions in one message is not yet verified, so answer them one at a time or open the conversation.",
	"errors.host_submission_unavailable": "Sending to the conversation is unavailable on this KiroCrew version.",
	"errors.host_unavailable": "KiroCrew's session service is unavailable.",
	"errors.identity_unprovable": "The repository's identity cannot be proven, so it cannot run work.",
	"errors.illegal_transition": "That state change is not permitted.",
	"errors.install_conflict": "A file at a managed path differs from what Studio would install.",
	"errors.install_recovery_required": "This repository needs install recovery before it can run AI-DLC.",
	"errors.intent_archived": "This intent is archived.",
	"errors.intent_not_found": "That intent is not on disk.",
	"errors.intent_paused": "This intent is paused.",
	"errors.internal_error": "Something went wrong inside AI-DLC Studio.",
	"errors.internal_secret_invalid": "That request was not signed by this gateway.",
	"errors.invalid_decision": "That decision is not offered for this item.",
	"errors.invalid_settings": "Those settings are not valid.",
	"errors.lease_lost": "The repository lease changed while the decision was being prepared, so nothing was sent.",
	"errors.legacy_layout": "This repository uses the older AI-DLC layout.",
	"errors.machine_lane_unavailable": "Unattended work is unavailable: no machine lane has been proven.",
	"errors.migration_already_applied": "The migration has already run.",
	"errors.migration_not_applicable": "There is nothing to migrate.",
	"errors.newer_installed": "A newer AI-DLC is installed than the one bundled with Studio.",
	"errors.not_delivered_unproven": "Studio cannot prove the message was never sent, so it will not be resent automatically.",
	"errors.not_installed": "AI-DLC is not installed in this repository.",
	"errors.owner_required": "Only the dashboard owner can do this.",
	"errors.payload_degraded": "Studio's bundled AI-DLC payload failed verification.",
	"errors.plan_invalid": "That plan does not satisfy the stage dependencies.",
	"errors.rate_limited": "Too many requests. Try again shortly.",
	"errors.rebind_not_allowed": "The old location is still available, so it cannot be rebound.",
	"errors.receipt_not_found": "There is no install receipt for this repository.",
	"errors.recompose_not_allowed": "Only pending stages ahead of the cursor can be changed.",
	"errors.repo_busy": "Another operation holds this repository. It will not be interrupted.",
	"errors.repo_not_found": "That repository is not registered.",
	"errors.repo_unavailable": "The repository is not reachable right now.",
	"errors.retry_not_allowed": "This item cannot be retried.",
	"errors.route_not_found": "Unknown endpoint.",
	"errors.run_not_applicable": "This run no longer fits the current workflow. Open the intent's current actions.",
	"errors.same_version_installed": "This repository already has the bundled AI-DLC version.",
	"errors.sensitive_path": "That location is protected and cannot be read.",
	"errors.session_busy": "The conversation is busy with a turn.",
	"errors.session_unbound": "This intent has no conversation bound yet.",
	"errors.slack_unavailable": "Slack is not connected.",
	"errors.slot_busy": "The conversation is busy.",
	"errors.slot_mismatch": "That conversation belongs to a different repository or agent.",
	"errors.stage_not_found": "That stage is not in the installed stage graph.",
	"errors.stale_generation": "Something else changed this record first.",
	"errors.state_inconsistent": "AI-DLC's files disagree with each other, so nothing will be sent until that is resolved.",
	"errors.state_version_migration_unconfirmed": "This repository's AI-DLC state is an older version, and upgrading it is not yet proven safe.",
	"errors.storage_error": "Studio's own storage could not be written.",
	"errors.storage_unavailable": "Studio's storage is not open.",
	"errors.takeover_not_safe": "Taking over is only safe at a stable boundary.",
	"errors.too_large": "That file is too large to show.",
	"errors.too_many_repos": "The repository limit has been reached.",
	"errors.transaction_not_found": "That transaction is not recorded.",
	"errors.unauthorized": "Please sign in to KiroCrew.",
	"errors.unknown_stage": "That stage is not in this intent's plan.",
	"errors.unstable_read": "A file is changing right now, so it cannot be used as evidence.",
	"errors.unsupported_locale": "That language is not available.",
	"estimate.a11y.estimate": "Estimates with source and confidence",
	"estimate.a11y.exact": "Exact counts",
	"estimate.active": "Active execution",
	"estimate.confidence.low": "low confidence",
	"estimate.confidence.medium": "medium confidence",
	"estimate.coverage.item": "{slug} is off, so {artifacts} will not exist.",
	"estimate.coverage.itemNone": "{slug} is off; it declares no artifact.",
	"estimate.coverage.title": "Coverage you have already given up",
	"estimate.credits": "Credits",
	"estimate.credits.value": "Unavailable",
	"estimate.credits.why": "not observable from the available Kiro signals — not zero, not inferred",
	"estimate.dominant.item": "{slug} — about {pct}% of the estimated turns",
	"estimate.dominant.locked": "AI-DLC will not let this stage be turned off: {reason}",
	"estimate.dominant.lost": "Turning it off gives up {artifacts}.",
	"estimate.dominant.lostNone": "It declares no artifact, so turning it off gives up no product.",
	"estimate.dominant.none": "No single stage dominates this estimate.",
	"estimate.dominant.perUnit_one": "counted across {n} unit of work",
	"estimate.dominant.perUnit_other": "counted across {n} units of work",
	"estimate.dominant.title": "What dominates the estimate",
	"estimate.dominant.turns": "{range} turns of the estimate",
	"estimate.elapsed": "Elapsed with your Gates",
	"estimate.elapsed.unavailable": "Studio does not estimate this without your own response times.",
	"estimate.elapsed.why": "depends on how quickly you answer",
	"estimate.estimate.title": "Estimates",
	"estimate.exact.artifacts": "Artifacts produced",
	"estimate.exact.artifactsAssumed_one": "exact for {n} unit of work, which Studio assumed because no unit exists yet",
	"estimate.exact.artifactsAssumed_other": "exact for {n} units of work, which Studio assumed because none exist yet",
	"estimate.exact.artifactsUnits_one": "exact; per-unit stages counted for {n} unit of work",
	"estimate.exact.artifactsUnits_other": "exact; per-unit stages counted for {n} units of work",
	"estimate.exact.artifactsWhy": "exact, summed over the selected stages",
	"estimate.exact.fromGraph": "exact, from the installed graph",
	"estimate.exact.gates": "Gates",
	"estimate.exact.gatesWhy": "exact; every selected stage outside initialization has a Gate",
	"estimate.exact.ofGraph": "exact, {selected} of {known} known stages",
	"estimate.exact.review": "Review intensity",
	"estimate.exact.reviewValue": "{none} none · {advisory} advisory · {adversarial} adversarial",
	"estimate.exact.reviewWhy": "exact, as configured",
	"estimate.exact.stages": "Stages selected",
	"estimate.exact.title": "Exact counts",
	"estimate.qualifier": "{kind} · {source} · {confidence}",
	"estimate.qualifierSamples": "{kind} · {source} · {confidence} · {samples}",
	"estimate.range": "range",
	"estimate.samples.none": "no local history yet",
	"estimate.samples_one": "{n} local sample",
	"estimate.samples_other": "{n} local samples",
	"estimate.source.assumption": "assumption",
	"estimate.source.history_calibrated": "calibrated from local history",
	"estimate.source.rule_band": "rule-based band",
	"estimate.source.unknown": "source not stated",
	"estimate.turns": "Turns",
	"install.action.already_absent": "Already absent",
	"install.action.conflict": "Conflict — a different file is already there",
	"install.action.create": "Create",
	"install.action.engine_modified": "Rewrite — the engine rewrites this file itself",
	"install.action.identical": "Already identical",
	"install.action.merge_conflict": "Conflict — the managed fragment cannot be proven to be Studio's",
	"install.action.merge_create": "Merge — add the managed fragment",
	"install.action.merge_identical": "Merge — the managed fragment is already current",
	"install.action.merge_update": "Merge — update the managed fragment only",
	"install.action.owned_identical": "Rewrite — unchanged since install",
	"install.action.owned_modified": "Conflict — changed since install",
	"install.action.preserve": "Preserve",
	"install.action.recovery_conflict": "Recovery needs attention",
	"install.action.remove": "Remove owned file",
	"install.action.remove_created": "Remove content created by the interrupted transaction",
	"install.action.remove_fragment": "Remove owned fragment",
	"install.action.restore_backup": "Restore the transaction backup",
	"install.action.restore_version": "Restore previous version",
	"install.action.retire": "Retire",
	"install.action.retire_blocked": "Kept — changed since install, so it is not retired",
	"install.action.rollback_conflict": "Cannot restore this content",
	"install.action.shell_create": "Create the workspace seed file",
	"install.action.shell_exists": "Left alone — the workspace file already exists",
	"install.action.uninstall_conflict": "Modified or unverified content",
	"install.blocked": "This cannot run yet.",
	"install.blockers.body": "Studio never overwrites bytes it cannot prove are its own, and v1 has no force option anywhere. Move the file aside yourself, or leave the installation as it is — a complete old version is better than a mixed new one.",
	"install.blockers.diff": "Difference",
	"install.blockers.diffTruncated": "Only the first {n} lines of the difference are shown.",
	"install.blockers.noDiff": "No text difference is available for this path.",
	"install.blockers.title": "Conflicts that stop this",
	"install.bytes": "Bytes to write",
	"install.cancel": "Cancel",
	"install.cancel.explanation": "Cancellation waits for the current file operation to finish, then restores and verifies the transaction's backups.",
	"install.cancel.finished": "Installation cancelled. Review the transaction status for the rollback result.",
	"install.cancel.requested": "Cancellation requested. Waiting for the transaction to restore its changes.",
	"install.cancel.transaction": "Cancel and roll back installation",
	"install.confirm.digestNote": "This confirms plan {digest}. If the repository changed since the preview, the transaction refuses and the preview is re-read; nothing is written on a mismatch.",
	"install.confirm.install": "Install AI-DLC {version}",
	"install.confirm.note": "Studio stages and hashes the payload, takes an exclusive admin lease on this repository, backs up every receipt-owned file, writes, validates, and commits the receipt last. It queues behind a running turn and never interrupts one.",
	"install.confirm.recovery": "Run install recovery",
	"install.confirm.restoreTransaction": "Restore the interrupted transaction",
	"install.confirm.restoreTransactionNote": "Restore this transaction's starting files and receipt from its verified backups. Project data stays in place.",
	"install.confirm.rollback": "Restore engine {version}",
	"install.confirm.rollbackNote": "This restores the reviewed engine and its owned fragments. Project data and unrelated edits stay in place; failures restore the transaction's starting state.",
	"install.confirm.title": "Confirm",
	"install.confirm.uninstall": "Uninstall the reviewed harness",
	"install.confirm.uninstallNote": "Only the reviewed receipt-owned content will be removed. If a step fails, the transaction restores its backups.",
	"install.confirm.upgrade": "Upgrade to AI-DLC {version}",
	"install.counts.title": "What would happen",
	"install.entries.caption": "Every managed path, with its ownership kind and what would happen to it",
	"install.entries.col.action": "What happens",
	"install.entries.col.hash": "Digests",
	"install.entries.col.ownership": "Ownership",
	"install.entries.col.path": "Path",
	"install.entries.fragment": "fragment {key}",
	"install.entries.live": "on disk {sha}",
	"install.entries.none": "No managed path is affected.",
	"install.entries.payload": "bundled {sha}",
	"install.entries.receipt": "receipted {sha}",
	"install.entries.summary_one": "{n} managed path",
	"install.entries.summary_other": "{n} managed paths",
	"install.entries.title": "Managed paths",
	"install.error.nothingWritten": "Nothing was written.",
	"install.lease.required": "An exclusive admin lease is required, so execution in this repository waits for the transaction to finish.",
	"install.noForce": "There is no force-overwrite control here or anywhere else in Studio.",
	"install.payloadDigest": "Payload digest",
	"install.planDigest": "Plan digest",
	"install.preflight.title": "Repository preflight",
	"install.preserved.body": "These entries include changed configurable files, project content, or content Studio cannot prove it introduced.",
	"install.preserved.title": "Content that will remain",
	"install.preview.lede.install": "Nothing has been written. This is every path Studio would create, and who would own it.",
	"install.preview.lede.recovery": "Nothing has been written. This is what a recovery transaction would restore and validate.",
	"install.preview.lede.rollback": "Restore the immediately preceding engine version from its recorded backups. Review the affected files and workflow compatibility before confirming.",
	"install.preview.lede.uninstall": "Review the files and fragments recorded by Studio. Project files, other harnesses and AI-DLC workflow data are preserved.",
	"install.preview.lede.upgrade": "Nothing has been written. This is every path Studio would change or retire, and every file it refuses to touch.",
	"install.preview.refresh": "Re-read the repository",
	"install.preview.running": "Reading the repository…",
	"install.preview.title.install": "Install preview",
	"install.preview.title.recovery": "Install recovery preview",
	"install.preview.title.rollback": "Roll back the AI-DLC engine",
	"install.preview.title.uninstall": "Uninstall AI-DLC harness",
	"install.preview.title.upgrade": "Upgrade preview",
	"install.reason.already_absent": "This file is already absent.",
	"install.reason.backup_digest_mismatch": "The backup does not match its receipt.",
	"install.reason.backup_missing": "The required old-version backup is missing.",
	"install.reason.bad_path": "The path changed or contains a symbolic link.",
	"install.reason.engine_mutable_changed": "This configurable file changed after installation. It will remain with its current contents.",
	"install.reason.install_conflict": "The content differs from its receipt. Preserve your changes before trying again.",
	"install.reason.legacy_fragment_ownership_unknown": "The older receipt does not identify the exact content Studio added.",
	"install.reason.preexisting_fragment": "This content existed before Studio installed the harness.",
	"install.reason.protected_path": "This path is outside the harness content Studio can remove.",
	"install.reason.recovery_backup_invalid": "The recovery backup is missing or does not match the recorded bytes.",
	"install.reason.recovery_evidence_invalid": "The recovery journal is missing, changed or unreadable.",
	"install.reason.state_version_unsupported": "The older engine does not support this repository's workflow state.",
	"install.reason.target_compatibility_unknown": "The older receipt does not record which workflow state versions it supports.",
	"install.reason.unreadable_backup": "The backup could not be read safely.",
	"install.reason.unreadable_fragment": "Studio could not safely read this fragment.",
	"install.reason.user_changed_recovery_target": "This file changed after the interrupted transaction. Preserve the new work before recovery.",
	"install.receiptStatus.current": "current",
	"install.receiptStatus.rolled_back": "rolled back",
	"install.receiptStatus.superseded": "superseded",
	"install.receiptStatus.uninstalled": "Uninstalled",
	"install.recovery.blocked": "AI-DLC execution is blocked in this repository until this clears.",
	"install.recovery.body": "A rollback did not finish, so this repository may hold a mixed installation. Clearing it is a separate recovery transaction with its own preview, rollback evidence and post-recovery validation.",
	"install.recovery.noPath": "No evidence path was recorded for the failed transaction.",
	"install.recovery.pathLabel": "Failed transaction evidence",
	"install.recovery.pathNote": "The failed candidate and its transaction record are kept outside the repository, at this path.",
	"install.recovery.restoreTransaction": "Restore interrupted {kind} transaction {id} to its starting state. The same transaction will finish as rolled back.",
	"install.recovery.start": "Preview the recovery",
	"install.recovery.title": "Install recovery required",
	"install.retire.body": "A file the previous version owned is removed only when its bytes still match the receipt. A retired file you changed is kept and reported instead of deleted.",
	"install.retire.title": "What would be retired",
	"install.stale.body": "Nothing was written. The preview has been re-read from disk; check it again before you confirm.",
	"install.stale.title": "The repository changed while you were reading",
	"install.stateBlocked.body": "This repository holds AI-DLC state at version {found}, and this payload writes version {supported}. Migrating that state is not proven safe, so Studio leaves the repository readable and refuses to write. A complete old version is better than a mixed new one.",
	"install.stateBlocked.title": "AI-DLC state is at a version this payload cannot write",
	"install.stateBlocked.unreadable_one": "{n} state file could not be read at all, and is treated as blocked.",
	"install.stateBlocked.unreadable_other": "{n} state files could not be read at all, and are treated as blocked.",
	"install.step.acquire_admin_lease": "Acquire the admin lease",
	"install.step.backup": "Back up receipt-owned files",
	"install.step.commit_receipt": "Commit the receipt",
	"install.step.commit_rollback": "Record the completed version rollback",
	"install.step.commit_uninstall": "Record the completed uninstall",
	"install.step.confirm_recovery": "Confirm restoration of the interrupted transaction",
	"install.step.confirm_rollback": "Confirm the reviewed version rollback",
	"install.step.confirm_uninstall": "Confirm the reviewed uninstall",
	"install.step.delete_created": "Delete the files it created",
	"install.step.merge_fragments": "Merge managed fragments",
	"install.step.post_rollback_validate": "Verify the restored version and receipt",
	"install.step.post_uninstall_validate": "Verify removed content and preserved files",
	"install.step.post_write_validate": "Validate after writing",
	"install.step.release_lease": "Release the lease",
	"install.step.remove_files": "Remove owned harness files",
	"install.step.remove_fragments": "Remove owned fragments",
	"install.step.restore_backups": "Restore the backups",
	"install.step.restore_version": "Restore the reviewed engine version",
	"install.step.reverify_old_receipt": "Re-verify the previous receipt",
	"install.step.stage_payload": "Stage the payload",
	"install.step.verify_staging": "Verify the staged payload",
	"install.step.write_files": "Write the files",
	"install.stepState.failed": "failed",
	"install.stepState.ok": "done",
	"install.stepState.running": "running",
	"install.tx.close": "Close",
	"install.tx.committed": "Committed. The receipt for {version} is now the current one.",
	"install.tx.error": "Error",
	"install.tx.failedDir": "Evidence kept at",
	"install.tx.finished": "Finished",
	"install.tx.kind": "Operation",
	"install.tx.kind.install": "Install",
	"install.tx.kind.recovery": "Recovery",
	"install.tx.kind.rollback": "Version rollback",
	"install.tx.kind.uninstall": "Uninstall",
	"install.tx.kind.upgrade": "Upgrade",
	"install.tx.open": "Open the transaction",
	"install.tx.progress": "{kind} transaction {id}: {status}",
	"install.tx.rolledBack": "Everything was restored. The repository keeps its previous complete installation and receipt.",
	"install.tx.started": "Started",
	"install.tx.status": "Status",
	"install.tx.steps": "Steps",
	"install.tx.title": "Transaction {id}",
	"install.tx.uninstalled": "Harness removed. Workflow data and the listed preserved content remain.",
	"install.txStatus.backed_up": "Receipt-owned files backed up",
	"install.txStatus.committed": "Committed",
	"install.txStatus.failed": "Failed",
	"install.txStatus.leased": "Admin lease held",
	"install.txStatus.merged": "Fragments merged",
	"install.txStatus.recovery_required": "Install recovery required",
	"install.txStatus.rolled_back": "Rolled back",
	"install.txStatus.rolling_back": "Rolling back",
	"install.txStatus.staged": "Payload staged",
	"install.txStatus.validated": "Validated",
	"install.txStatus.written": "Files written",
	"install.version.from": "Installed engine",
	"install.version.notInstalled": "Not installed",
	"install.version.studio": "Studio version",
	"install.version.to": "Bundled with Studio",
	"install.warnings.title": "Warnings",
	"intents.a11y.list": "Intents",
	"intents.a11y.row": "{intent} in {repo}, {state}, stage {stage}, {waiting}. {primary}",
	"intents.a11y.table": "Intents",
	"intents.action.a11y": "Actions for {intent}",
	"intents.action.archive": "Archive",
	"intents.action.bindSession": "Bind a conversation",
	"intents.action.details": "Workflow Map",
	"intents.action.openAction": "Open decision",
	"intents.action.pause": "Pause after current turn",
	"intents.action.recompose": "Change the plan",
	"intents.action.restore": "Restore",
	"intents.action.resume": "Resume",
	"intents.action.run": "Run to next checkpoint",
	"intents.action.session": "Conversation",
	"intents.action.unpause": "Allow dispatch again",
	"intents.archived.done": "Archived in Studio only. No AI-DLC file changed.",
	"intents.archivedChip": "Archived",
	"intents.blocking_one": "{n} blocking finding",
	"intents.blocking_other": "{n} blocking findings",
	"intents.busy": "Working…",
	"intents.col.actions": "Actions",
	"intents.col.intent": "Intent",
	"intents.col.keepMoving": "Keep moving",
	"intents.col.repo": "Repo",
	"intents.col.stage": "Stage",
	"intents.col.state": "State",
	"intents.confirm.archive.body": "Archiving hides the intent from the default views. It changes no AI-DLC file on disk and can be undone.",
	"intents.confirm.archive.title": "Archive this intent?",
	"intents.confirm.go": "Confirm",
	"intents.confirm.pause.blocked_one": "{n} waiting decision will refuse to send while this intent is paused.",
	"intents.confirm.pause.blocked_other": "{n} waiting decisions will refuse to send while this intent is paused.",
	"intents.confirm.pause.body": "The turn that is running finishes. No further message is sent for this intent until you allow dispatch again.",
	"intents.confirm.pause.title": "Pause after the current turn?",
	"intents.confirm.restore.body": "The intent reappears in the default views. Nothing on disk changes.",
	"intents.confirm.restore.title": "Restore this intent?",
	"intents.confirm.unpause.body": "Waiting decisions become sendable again. Nothing is sent by allowing this.",
	"intents.confirm.unpause.title": "Allow dispatch again?",
	"intents.count_one": "{n} intent",
	"intents.count_other": "{n} intents",
	"intents.cursorChip": "AI-DLC's active intent",
	"intents.empty.body": "AI-DLC keeps its intents inside the repository. Create one and Studio will read it from disk; nothing runs until you say so.",
	"intents.empty.title": "No intent is on disk yet",
	"intents.emptyFiltered.body": "Clear the filters to see every intent Studio read from the registered repositories.",
	"intents.emptyFiltered.clear": "Clear filters",
	"intents.emptyFiltered.title": "No intent matches these filters",
	"intents.emptyNoRepo.body": "Add a repository on the Repos page. Studio never registers directories automatically.",
	"intents.emptyNoRepo.open": "Open Repos",
	"intents.emptyNoRepo.title": "No repository is registered",
	"intents.error.repo": "{repo}: {reason}",
	"intents.error.title": "Some intents could not be read",
	"intents.filter.archived": "Include archived",
	"intents.filter.search": "Filter intents",
	"intents.filter.searchPlaceholder": "slug, title or stage",
	"intents.filter.space": "Space",
	"intents.filter.spaceAll": "Every space",
	"intents.filter.state": "State",
	"intents.filter.stateAll": "Every state",
	"intents.filters": "Filters",
	"intents.keepMoving.breaker": "Repeated failures also stopped dispatch for this intent.",
	"intents.keepMoving.unavailable": "Unavailable",
	"intents.keepMoving.why": "Unattended continuation needs a machine lane that has not been proven, so Keep moving cannot be switched on for any intent.",
	"intents.lede": "Multiple intents may be in flight in one repository. Turns serialize per repository; different repositories run in parallel.",
	"intents.newIntent": "New intent",
	"intents.noSession": "no conversation bound",
	"intents.openActions_one": "{n} decision waiting",
	"intents.openActions_other": "{n} decisions waiting",
	"intents.paused.done_one": "Paused. {n} waiting decision will now refuse to send.",
	"intents.paused.done_other": "Paused. {n} waiting decisions will now refuse to send.",
	"intents.pausedChip": "Paused",
	"intents.restored.done": "Restored. No AI-DLC file changed.",
	"intents.resume.disabled": "Resume is offered only for an intent AI-DLC parked.",
	"intents.run.disabledArchived": "An archived intent does not run. Restore it first.",
	"intents.run.disabledCheckpoint": "Answer the current question or approval before running again.",
	"intents.run.disabledCompleted": "This intent is complete.",
	"intents.run.disabledPaused": "This intent is paused, so dispatch is blocked. Allow dispatch again first.",
	"intents.run.disabledRunning": "The conversation is already running.",
	"intents.run.dismiss": "Dismiss",
	"intents.run.open": "Open the queued command",
	"intents.run.queued": "Studio queued the command and sent nothing. Open it in the Action Center to see the exact text and send it.",
	"intents.session": "session bound",
	"intents.session.a11y": "Canonical conversation for {intent}",
	"intents.session.adopt": "Bind an existing conversation",
	"intents.session.adoptGo": "Bind this one",
	"intents.session.adoptLede": "Open on this repository with agent {agent}. Studio refuses any other conversation, so nothing else is offered here.",
	"intents.session.adoptNone": "No conversation on this repository is running agent {agent} yet. Create one instead.",
	"intents.session.adoptTitle": "Conversations Studio can bind",
	"intents.session.boundLede": "Decisions for this intent are written as your own turn in this conversation. Studio still shows you the exact text before anything is sent.",
	"intents.session.busy": "Step {step} of {total}: {what}…",
	"intents.session.create": "Create and bind the conversation",
	"intents.session.done.bound": "Bound to {slot}.",
	"intents.session.done.moved": "Moved to {slot}.",
	"intents.session.done.unbound": "Unbound. Decisions for this intent will refuse to send until a conversation is bound.",
	"intents.session.fact.agent": "Agent",
	"intents.session.fact.project": "Project",
	"intents.session.fact.repo": "Repository",
	"intents.session.fact.session": "Session",
	"intents.session.fact.slot": "Conversation",
	"intents.session.fact.state": "Right now",
	"intents.session.fact.willCreate": "Conversation to create",
	"intents.session.failedAt": "Step {step} of {total} was refused ({what}):",
	"intents.session.idle": "Not running",
	"intents.session.lede": "Studio delivers a decision by writing it as your own turn in one KiroCrew conversation bound to this intent: agent {agent}, project this repository. Until one is bound, every decision here is refused.",
	"intents.session.move": "Move to another conversation",
	"intents.session.moveGo": "Move it here",
	"intents.session.moveLede": "Found by project and by name. Moving is refused while a decision for this intent may still be on the wire.",
	"intents.session.moveNone": "No other conversation belongs to this repository or to this intent.",
	"intents.session.moveTitle": "Conversations this intent can move to",
	"intents.session.reason.current_binding": "Currently bound",
	"intents.session.reason.name_match": "Named for this intent",
	"intents.session.reason.project_match": "Open on this repository",
	"intents.session.running": "A turn is running",
	"intents.session.serverSaid": "The server said:",
	"intents.session.step.bind": "bind it to this intent",
	"intents.session.step.create": "create the conversation",
	"intents.session.step.project": "point it at this repository",
	"intents.session.step.title": "title it",
	"intents.session.title": "Canonical conversation — {intent}",
	"intents.session.unbind": "Unbind",
	"intents.session.unbindWhy": "Unbinding only forgets which conversation this intent uses — the conversation and every AI-DLC file stay as they are. Decisions refuse to send until something is bound again.",
	"intents.showing": "{visible} of {total}",
	"intents.stage.next": "next {stage}",
	"intents.stage.none": "No stage recorded yet",
	"intents.stage.progress": "{done} of {total} stages",
	"intents.title": "Intents",
	"intents.unpaused.done": "Dispatch is allowed again. Nothing was sent.",
	"intents.unstable": "Files are changing right now",
	"intents.unstableWhy": "A file changed while Studio was reading it, so this row is not evidence a decision can rest on. It settles on its own.",
	"intents.waiting": "waiting {duration}",
	"intents.warn_one": "{n} warning",
	"intents.warn_other": "{n} warnings",
	"maintenance.completed": "Directories removed: {count}. Transaction history was retained.",
	"maintenance.confirm": "Delete the reviewed directories",
	"maintenance.confirmNote": "Delete {count} directories ({size}). Deleted backups and diagnostic files cannot be restored; older rollback points may no longer be available.",
	"maintenance.description": "Select old backups or failed-attempt files to remove. Active transactions and required recovery evidence are protected. Transaction history stays in Studio.",
	"maintenance.empty": "No transaction directories are available for this repository.",
	"maintenance.partial": "Removed {count} directories; some files remain. Review a fresh preview before retrying.",
	"maintenance.path": "Transaction directory",
	"maintenance.preview": "Preview selected cleanup",
	"maintenance.reason.active_transaction": "Protected: transaction is still active",
	"maintenance.reason.current_receipt": "Protected: required by the current installation",
	"maintenance.reason.delete_failed": "Some files could not be deleted",
	"maintenance.reason.evidence_limit": "Protected: diagnostic metadata exceeds the size limit",
	"maintenance.reason.filesystem_boundary": "Protected: the directory crosses a filesystem boundary",
	"maintenance.reason.identity_mismatch": "Protected: repository identity changed",
	"maintenance.reason.metadata_changed": "The transaction changed during cleanup",
	"maintenance.reason.not_directory": "Protected: this path is not a directory",
	"maintenance.reason.older_restore_point": "Older restore point: deleting this backup removes that restore point. Current rollback backups and receipt history are retained.",
	"maintenance.reason.path_changed": "The directory changed after it was reviewed",
	"maintenance.reason.path_mismatch": "Protected: the directory differs from its transaction record",
	"maintenance.reason.receipt_reference_missing": "Protected: a referenced receipt is missing",
	"maintenance.reason.receipt_restoration": "Protected: required for recovery",
	"maintenance.reason.recovery_required": "Protected: this transaction needs recovery",
	"maintenance.reason.repo_recovery_required": "Protected: the repository needs recovery",
	"maintenance.reason.scan_limit": "Protected: the directory exceeds the scan limit",
	"maintenance.reason.symlink": "Protected: symbolic links cannot be cleaned up",
	"maintenance.reason.terminal_backup": "Finished transaction: backup files can be removed; transaction history is retained.",
	"maintenance.reason.terminal_failure_evidence": "Finished transaction: archived files can be removed; transaction history and top-level diagnostic JSON are retained.",
	"maintenance.reason.unfinished_transaction": "Protected: completion was not recorded",
	"maintenance.reason.unreadable": "Protected: the directory could not be read",
	"maintenance.refresh": "Refresh directories",
	"maintenance.select": "Select",
	"maintenance.size": "Size",
	"maintenance.state": "State or protection",
	"maintenance.title": "Clean up transaction files",
	"map.a11y.agent": "Agent {agent}",
	"map.a11y.canvas": "Workflow map swimlanes",
	"map.a11y.elapsed": "Elapsed {duration}",
	"map.a11y.lane": "{phase} phase",
	"map.a11y.reason": "Reason: {reason}",
	"map.a11y.selected": "Stage {number} {slug} selected. Its evidence is in the stage inspector.",
	"map.a11y.stage": "Stage {number} {slug}, {phase} phase, {state}, {repo} / {intent}. {facts}",
	"map.a11y.tableCaption": "Visible stages of {intent}, in phase order: phase, number, stage, state, agent, Gate, review, elapsed time, files and notes.",
	"map.a11y.unit": "Unit {unit} of stage {number} {slug}, {state}, {repo} / {intent}. {facts}",
	"map.a11y.unitSelected": "Unit {unit} of stage {number} {slug} selected. Its evidence is in the stage inspector.",
	"map.accordion.note": "Phases are accordions on a narrow screen. The canvas is not shrunk to fit, so no stage card becomes unreadable.",
	"map.alert.openAction": "Open the action",
	"map.alert.recovery": "Recovery is required for this intent. Until the evidence agrees, treat the map as a reading of disk, not as the plan's state.",
	"map.alert.unstable": "The record changed while Studio was reading it. This map is one snapshot and may already be out of date.",
	"map.artifactKind.artifact": "Artifact",
	"map.artifactKind.contribution": "Contribution",
	"map.artifactKind.memory": "Memory",
	"map.artifactKind.other": "Other",
	"map.artifactKind.questions": "Questions",
	"map.artifactKind.review": "Review",
	"map.artifactKind.traceability": "Traceability",
	"map.bar.density": "Density",
	"map.bar.engine": "Engine {version}",
	"map.bar.expandUnits": "Expand unit sub-lanes",
	"map.bar.gates_one": "{n} Gate · exact",
	"map.bar.gates_other": "{n} Gates · exact",
	"map.bar.hideUnits": "Hide unit sub-lanes",
	"map.bar.layout": "Layout",
	"map.bar.scope": "{repo} / {intent}",
	"map.bar.showAllStages": "Show all stages",
	"map.bar.showCanvas": "Show as swimlanes",
	"map.bar.showPlanOnly": "Show current plan only",
	"map.bar.showTable": "Show as table",
	"map.bar.stagesKnown_one": "{n} stage known · exact",
	"map.bar.stagesKnown_other": "{n} stages known · exact",
	"map.bar.stagesSelected_one": "{n} selected · exact",
	"map.bar.stagesSelected_other": "{n} selected · exact",
	"map.chip.agentTitle": "Lead agent: {agent}",
	"map.chip.artifacts_one": "{n} file",
	"map.chip.artifacts_other": "{n} files",
	"map.chip.conditional": "Conditional",
	"map.chip.current": "Executing now",
	"map.chip.directive": "Active directive",
	"map.chip.elapsedTitle": "Elapsed on the latest attempt: {duration}",
	"map.chip.gate": "Gate",
	"map.chip.modeTitle": "Execution mode: {mode}",
	"map.chip.noReview": "No review",
	"map.chip.review": "Review {class}",
	"map.chip.reviewerTitle": "Reviewer: {reviewer}",
	"map.col.agent": "Agent",
	"map.col.artifacts": "Files",
	"map.col.elapsed": "Elapsed",
	"map.col.gate": "Gate",
	"map.col.notes": "Notes",
	"map.col.number": "No.",
	"map.col.phase": "Phase",
	"map.col.review": "Review",
	"map.col.stage": "Stage",
	"map.col.state": "State",
	"map.consequence": "The current plan is shown by default. Show all stages to inspect excluded stages and their reasons. Stage numbers stay unchanged, and dependency links follow the visible stages.",
	"map.density.dependencies": "Dependencies",
	"map.density.detailed": "Detailed",
	"map.density.overview": "Overview",
	"map.empty.action": "Choose an intent",
	"map.empty.body": "The map draws one intent's stage graph against what its record actually contains. Choose an intent and Studio reads it from disk.",
	"map.empty.noRepo": "Pick a repository in the scope bar first. Studio only ever reads repositories you registered.",
	"map.empty.plan": "No stages are selected in this plan. Show all stages to inspect the full workflow.",
	"map.empty.title": "No intent is selected",
	"map.error.title": "The map could not be read",
	"map.hiddenSelection": "The linked stage is outside this plan. Show all stages to inspect it.",
	"map.inspector.artifacts": "Files",
	"map.inspector.artifactsEmpty": "No file is recorded for this stage yet.",
	"map.inspector.artifactsNote": "Studio reads these files. It never writes them.",
	"map.inspector.audit": "Audit",
	"map.inspector.auditEmpty": "No recent audit event names this stage.",
	"map.inspector.auditNote": "The record's last events, filtered to this stage. Field names and values are AI-DLC's own.",
	"map.inspector.close": "Clear the stage selection",
	"map.inspector.consumes": "Consumes",
	"map.inspector.downstream": "Downstream",
	"map.inspector.noOperation": "No eligible operation at this stage",
	"map.inspector.none": "None recorded",
	"map.inspector.notSelected": "This stage is not selected in the current plan.",
	"map.inspector.open": "Open the {type} in Action Center",
	"map.inspector.openArtifact": "Open {name}",
	"map.inspector.operation": "Eligible operation",
	"map.inspector.pick": "Select a stage to see its files, review contract, audit trail and the one operation it permits.",
	"map.inspector.produces": "Produces",
	"map.inspector.reasonAhead": "This stage is ahead of the cursor. The engine reaches it on its own.",
	"map.inspector.reasonArchived": "The intent is archived.",
	"map.inspector.reasonDone": "This stage is finished. Nothing here is waiting on you.",
	"map.inspector.reasonPaused": "The intent is paused, so Studio dispatches nothing for it.",
	"map.inspector.reasonSkipped": "This stage is not executing in this plan.",
	"map.inspector.relationships": "Relationships",
	"map.inspector.review": "Review",
	"map.inspector.reviewContract": "Configured review: {class} by {reviewer}.",
	"map.inspector.reviewContractNoReviewer": "Configured review: {class}.",
	"map.inspector.reviewEmpty": "No review finding is recorded for this stage.",
	"map.inspector.reviewNone": "This stage is configured without a reviewer.",
	"map.inspector.reviewOtherStage": "The record's latest review belongs to stage {stage}, not to this one.",
	"map.inspector.reviewRevisions_one": "{n} revision",
	"map.inspector.reviewRevisions_other": "{n} revisions",
	"map.inspector.reviewVerdict": "Verdict: {verdict}",
	"map.inspector.summaryConfirmation": "Summary confirmation: {value}",
	"map.inspector.title": "Stage inspector",
	"map.inspector.units": "Units",
	"map.inspector.upstream": "Upstream",
	"map.inspector.why": "Why it is not executing.",
	"map.inspector.whyUnknown": "The record does not say why. Its position is kept so the plan stays readable.",
	"map.lane.counts_one": "{n} stage · {skipped} skipped",
	"map.lane.counts_other": "{n} stages · {skipped} skipped",
	"map.phase.statusTitle": "Phase status recorded on disk: {status}",
	"map.phase.unknown": "Unrecognised phase",
	"map.rel.downstream": "downstream",
	"map.rel.upstream": "upstream",
	"map.review.level.advisory": "Advisory",
	"map.review.level.blocker": "Blocker",
	"map.review.level.resolved": "Answered",
	"map.review.level.unknown": "Unclassified",
	"map.review.more": "{n} more",
	"map.state.excluded": "Not selected",
	"map.status.executing": "Executing {number} {slug}",
	"map.status.idle": "No stage is executing",
	"map.status.progress": "{done} of {total} stages recorded as done",
	"map.units.note": "Per-unit state is presentational. The record keeps one row per stage, so a unit card never authorises a decision.",
	"map.units.row": "{number} {slug} — per unit",
	"migration.applied.at": "Applied",
	"migration.applied.backup": "A copy of the prototype registry is kept at {path}",
	"migration.applied.noBackup": "No backup path was recorded.",
	"migration.applied.status": "Status",
	"migration.applied.summary": "Counts the backend validated",
	"migration.applied.title": "Migration applied",
	"migration.apply": "Apply the migration once",
	"migration.applyBlocked": "Studio could not hash the prototype registry, so it will not apply a migration it cannot verify.",
	"migration.applyHint": "The migration runs once. Studio copies the prototype registry aside first, so it stays readable if anything goes wrong.",
	"migration.applyNeedsPreview": "Read the preview first. Studio applies only the plan you have seen.",
	"migration.applying": "Applying…",
	"migration.both.body": "aidlc-console is installed and enabled. Migrate its registry, then disable it in KiroCrew, so exactly one App is a control surface for these repositories.",
	"migration.both.title": "Two Apps can control the same repositories",
	"migration.consoleState.absent": "Not installed",
	"migration.consoleState.disabled": "Installed, disabled",
	"migration.consoleState.enabled": "Installed and enabled",
	"migration.consoleState.title": "Prototype App",
	"migration.counts": "{rowsIn} rows read, {rowsOut} will be registered",
	"migration.failed": "The migration did not complete: {message}",
	"migration.keeps.actions": "Action history. A migrated repository starts with an empty queue.",
	"migration.keeps.credentials": "Credentials of any kind.",
	"migration.keeps.repoData": "Anything inside your repositories. AI-DLC's own files are read from disk, never copied.",
	"migration.keeps.secret": "The prototype's app secret. Studio never reads it.",
	"migration.keeps.title": "What never moves",
	"migration.lede": "The prototype App aidlc-console is present on this machine. Its repository registry can move here once.",
	"migration.moves.archive": "Archive flags and Studio's own preferences.",
	"migration.moves.ids": "Internal repository ids are rewritten inside one transaction and the row count is validated before it commits.",
	"migration.moves.registry": "The repository registry: path, label and the date it was added.",
	"migration.moves.title": "What moves",
	"migration.nextSteps.desc": "Studio does not enable, disable or uninstall Apps. These two steps are yours, on the App's own KiroCrew page.",
	"migration.nextSteps.disable_console": "Disable aidlc-console, so only one App controls these repositories.",
	"migration.nextSteps.other": "Step reported by the backend: {step}",
	"migration.nextSteps.title": "Finish in KiroCrew",
	"migration.nextSteps.uninstall_console_keep_data": "Then uninstall aidlc-console, keeping its data as the rollback source.",
	"migration.notApplicable.already_applied": "The migration already ran.",
	"migration.notApplicable.malformed": "The prototype registry could not be read as a registry, so nothing will be copied from it.",
	"migration.notApplicable.not_found": "No aidlc-console registry was found on this machine.",
	"migration.notApplicable.other": "Reason reported by the backend: {reason}",
	"migration.notApplicable.title": "Nothing to migrate",
	"migration.openConsole": "Open the aidlc-console App page",
	"migration.preview": "Preview what moves",
	"migration.previewAgain": "Read the preview again",
	"migration.previewFailed": "The preview could not be read: {message}",
	"migration.previewNothing": "Nothing is written by a preview.",
	"migration.previewing": "Reading the prototype registry…",
	"migration.region": "Prototype migration",
	"migration.resolution.already_registered": "Already registered as {of} — skipped",
	"migration.resolution.duplicate": "Duplicate of {of} — skipped",
	"migration.resolution.migrate": "Will be registered",
	"migration.resolution.other": "Result reported by the backend: {raw}",
	"migration.resolution.unavailable": "Not reachable — registered as unavailable",
	"migration.rows.added": "Added",
	"migration.rows.detail": "Detail",
	"migration.rows.label": "Repository",
	"migration.rows.none": "The prototype registry has no rows.",
	"migration.rows.path": "Path",
	"migration.rows.resolution": "Result",
	"migration.rows.title": "Rows in the prototype registry",
	"migration.source": "Source",
	"migration.sourceDigest": "Digest",
	"migration.stale": "The prototype registry changed while you were reading this preview, so nothing was applied. The preview has been read again — check it before applying.",
	"migration.title": "Migrate from the AI-DLC console prototype",
	"nav.actions": "Action Center",
	"nav.activity": "Activity",
	"nav.intents": "Intents",
	"nav.map": "Workflow Map",
	"nav.newIntent": "New intent",
	"nav.repos": "Repos",
	"nav.settings": "Settings",
	"plan.busy": "Recomputing…",
	"plan.diff.consequences": "Downstream consequences",
	"plan.diff.consequencesNone": "No product of the selected stages changes.",
	"plan.diff.gained": "{artifact} is produced again.",
	"plan.diff.lost": "{artifact} is no longer produced.",
	"plan.diff.lostConsumers": "Declared as an input by {stages}.",
	"plan.diff.lostNoConsumers": "No selected stage declares it as an input.",
	"plan.diff.none": "No changes — this is the unmodified {scope} preset.",
	"plan.diff.noneNoScope": "No changes to the preset.",
	"plan.diff.off": "− {slug} turned off by you",
	"plan.diff.off.advisor": "− {slug} turned off by the Advisor's proposal",
	"plan.diff.on": "+ {slug} turned on by you",
	"plan.diff.on.advisor": "+ {slug} turned on by the Advisor's proposal",
	"plan.diff.title": "Plan diff against the preset default",
	"plan.issue.behind_cursor": "{stage} is at or behind AI-DLC's cursor, so its plan cannot change.",
	"plan.issue.dependency_missing": "Nothing selected would produce {artifact}, which {stage} declares as a required input, so AI-DLC rejects this combination.",
	"plan.issue.depth_invalid": "A configuration value is not one AI-DLC accepts, so Studio left it unset. AI-DLC allows: {allowed}.",
	"plan.issue.frozen_stage": "{stage} has already started, finished or been skipped, so its plan cannot change.",
	"plan.issue.recompose_not_allowed": "AI-DLC recomposes only an intent whose Status is {required_status}; this one reports {status}.",
	"plan.issue.required_stage_disabled": "{stage} is required by the selected plan, so the change was not applied.",
	"plan.issue.scope_unknown": "This repository defines no scope named {scope}. It knows: {known}.",
	"plan.issue.skeleton_anchor": "This change would move the walking-skeleton anchor from {anchor_before} to {anchor_after}, which AI-DLC refuses.",
	"plan.issue.unknown": "AI-DLC refuses this plan: {code}",
	"plan.issue.unknown_stage": "{stage} is not in the installed stage graph.",
	"plan.issues.none": "AI-DLC accepts this plan as described.",
	"plan.issues.title": "Why AI-DLC will not accept this plan",
	"plan.lock.always": "AI-DLC marks this stage as always executed, so it cannot be turned off.",
	"plan.lock.at_gate": "This stage is waiting for your decision, so its plan is fixed.",
	"plan.lock.behind_cursor": "This stage is at or behind AI-DLC's cursor, so its plan is fixed.",
	"plan.lock.completed": "AI-DLC has already finished or skipped this stage, so its plan is fixed.",
	"plan.lock.current": "This is the stage AI-DLC is working on now.",
	"plan.lock.not_in_graph": "This stage is in the intent's state file but not in the installed stage graph, so Studio cannot reason about it.",
	"plan.lock.required_by": "{slug} is selected and declares this stage as required, so it cannot be turned off.",
	"plan.lock.short.always": "always",
	"plan.lock.short.at_gate": "at its Gate",
	"plan.lock.short.behind_cursor": "behind the cursor",
	"plan.lock.short.completed": "already done",
	"plan.lock.short.current": "running now",
	"plan.lock.short.not_in_graph": "not in the graph",
	"plan.lock.short.required_by": "needed by {slug}",
	"plan.lock.short.unknown": "fixed by AI-DLC",
	"plan.lock.unknown": "AI-DLC does not allow this stage to change: {reason}",
	"plan.matrix.a11y": "Stage matrix",
	"plan.phase.gates_one": "{n} Gate",
	"plan.phase.gates_other": "{n} Gates",
	"plan.phase.on": "{on} of {total} on",
	"plan.phase.other": "Not in the installed graph",
	"plan.recompose.add_one": "{n} stage to turn on",
	"plan.recompose.add_other": "{n} stages to turn on",
	"plan.recompose.applied": "AI-DLC applied the change to the plan.",
	"plan.recompose.appliedFailed": "AI-DLC ran but reported a failure, so the plan on disk may be unchanged. Recheck it before running the intent.",
	"plan.recompose.apply": "Apply the change",
	"plan.recompose.argv": "What AI-DLC will run",
	"plan.recompose.close": "Close",
	"plan.recompose.confirmBody": "Studio runs AI-DLC's own recompose verb under a repository admin lease. It rewrites the plan rows of pending stages only, it sends no message to the conversation, and it never touches a completed stage.",
	"plan.recompose.confirmGo": "Run it",
	"plan.recompose.confirmTitle": "Confirm the recompose",
	"plan.recompose.current": "AI-DLC's cursor is at {stage}",
	"plan.recompose.currentUnknown": "AI-DLC has not recorded a current stage for this intent.",
	"plan.recompose.error": "The change was not applied.",
	"plan.recompose.lede": "Only pending stages ahead of AI-DLC's cursor can change. Everything else is listed with the reason it is fixed.",
	"plan.recompose.none": "Nothing is selected to change yet, so there is nothing to apply.",
	"plan.recompose.refused": "AI-DLC will not apply this change",
	"plan.recompose.reload": "Recheck the plan",
	"plan.recompose.reset": "Discard these selections",
	"plan.recompose.skip_one": "{n} stage to turn off",
	"plan.recompose.skip_other": "{n} stages to turn off",
	"plan.recompose.title": "Change the plan of {intent}",
	"plan.stage.a11y": "{number} {slug}",
	"plan.stage.always": "always runs",
	"plan.stage.conditional": "conditional",
	"plan.stage.conditionalOn": "conditional on {condition}",
	"plan.stage.consumes": "consumes {artifacts}",
	"plan.stage.excluded": "excluded by this scope",
	"plan.stage.gate": "Gate",
	"plan.stage.locked": "Locked",
	"plan.stage.off": "not selected",
	"plan.stage.on": "selected",
	"plan.stage.perUnit": "runs once per unit of work",
	"plan.stage.produces": "produces {artifacts}",
	"plan.stage.producesNone": "declares no artifact",
	"plan.stage.review": "{class} review",
	"plan.stage.reviewer": "reviewer {reviewer}",
	"plan.stage.state": "on disk: {state}",
	"queue.count": "{visible} of {total}",
	"queue.empty.action": "Watch the Workflow Map",
	"queue.empty.body": "Every registered intent is running, finished, or waiting on something that is not a decision. The next stop that needs a person appears here.",
	"queue.empty.title": "Nothing needs your judgment",
	"queue.filterLabel": "Filter the queue",
	"queue.filterPlaceholder": "Filter by repo, intent, stage",
	"queue.group.attention": "Circuit breakers, failures, and install conflicts",
	"queue.group.blocking": "Blocking Gates and questions",
	"queue.group.info": "Budget stops and pauses",
	"queue.group.oldest": "Oldest first",
	"queue.group.recovery": "Recovery required and delivery uncertain",
	"queue.group.repo": "Repository",
	"queue.label": "Action queue",
	"queue.more": "{n} more below. Keep scrolling to load them.",
	"queue.noMatch.body": "Clear the filter to see everything that is waiting.",
	"queue.noMatch.title": "No item matches that filter",
	"queue.noPrimary": "Nothing to decide yet",
	"queue.organize.oldest": "Oldest",
	"queue.organize.priority": "Priority",
	"queue.organize.repo": "Repo",
	"queue.organize.type": "Type",
	"queue.organizeLabel": "Queue organization",
	"queue.refreshing": "Refreshing",
	"queue.title": "Needs you",
	"repos.a11y.blocked": "Install recovery required in {repo}. AI-DLC execution is blocked in this repository until it clears.",
	"repos.a11y.repoRow": "{label}, {path}, {install}, {availability}, {intents}, {queue}",
	"repos.action.archive": "Archive repository",
	"repos.action.details": "Details",
	"repos.action.doctor": "Run AI-DLC doctor",
	"repos.action.doctorDone": "AI-DLC doctor finished. Exit code {code}.",
	"repos.action.doctorFailed": "AI-DLC doctor reported a problem. Exit code {code}.",
	"repos.action.doctorNote": "Doctor is AI-DLC's own health check. It appends HEALTH_CHECKED and GUARDRAIL_LOADED rows to the repository's audit trail, so Studio runs it only when you ask for it.",
	"repos.action.doctorRunning": "Running doctor…",
	"repos.action.install": "Install AI-DLC",
	"repos.action.rebind": "Rebind to a new path",
	"repos.action.recover": "Install recovery",
	"repos.action.remove": "Unregister",
	"repos.action.rename": "Rename repository",
	"repos.action.rescan": "Rescan",
	"repos.action.rescanning": "Rescanning…",
	"repos.action.rollback": "Roll back engine version",
	"repos.action.unarchive": "Restore repository",
	"repos.action.uninstall": "Uninstall harness",
	"repos.action.upgrade": "Upgrade AI-DLC",
	"repos.add.duplicate.body": "That path resolves to the same repository as {label}. Studio refuses a second registration for one identity: leases are keyed on the identity, and two rows for one repository would let it run twice.",
	"repos.add.duplicate.open": "Open {label}",
	"repos.add.duplicate.title": "Already registered",
	"repos.add.labelHelp": "Shown in the queue and the scope bar. Left empty, Studio uses the directory name.",
	"repos.add.labelLabel": "Label (optional)",
	"repos.add.needPreflight": "Registration stays disabled until the read-only preflight says the path can be registered.",
	"repos.add.open": "Add repository",
	"repos.add.pathHelp": "The absolute path of the repository root, for example /Users/you/work/checkout-web. Studio expands a leading ~, resolves symlinks, and writes nothing while looking.",
	"repos.add.pathLabel": "Absolute path",
	"repos.add.pathNotAbsolute": "The path has to be absolute: it must start with / or ~.",
	"repos.add.pathRequired": "An absolute path is required.",
	"repos.add.preflight": "Run preflight",
	"repos.add.preflightRunning": "Reading the directory…",
	"repos.add.register": "Register this path",
	"repos.add.registering": "Registering…",
	"repos.add.title": "Add a repository",
	"repos.availability.title": "This repository needs attention",
	"repos.card.findings": "Findings",
	"repos.card.harness": "Harness directories",
	"repos.card.harness.noUtility": "no utility script",
	"repos.card.harness.none": "No AI-DLC harness directory was found in this repository.",
	"repos.card.harness.utility": "utility script present",
	"repos.card.identity": "Identity",
	"repos.card.identity.added": "Registered",
	"repos.card.identity.gitCommonDir": "Git common dir identity",
	"repos.card.identity.lastSeen": "Last seen",
	"repos.card.identity.path": "Canonical path",
	"repos.card.identity.platform": "Platform",
	"repos.card.identity.resolved": "Resolved identity",
	"repos.card.identity.scanned": "Last scan",
	"repos.card.install": "AI-DLC installation",
	"repos.card.install.engineDir": "Engine directory",
	"repos.card.install.otherHarness": "AI-DLC {version} is installed in this repository under {dir}, a harness Studio does not manage. Studio's own Kiro harness ({own}) is not installed here, so there is nothing of Studio's to upgrade.",
	"repos.card.install.otherHarnessInstall": "Installing adds {own} beside it: no file under {dir} is written, changed or removed, and the existing aidlc/ workspace stays exactly as it is — both harnesses read that same workspace.",
	"repos.card.install.stages": "Stages",
	"repos.card.install.stateVersion": "State version",
	"repos.card.intents": "Intents",
	"repos.card.intents.none": "No intents are on disk in this repository.",
	"repos.card.intents.open": "Open the Intents page",
	"repos.card.lease.admin": "Admin lease",
	"repos.card.lease.execution": "Execution lease",
	"repos.card.lease.none": "None held",
	"repos.card.lease.orphaned": "orphaned — no heartbeat",
	"repos.card.lease.since": "held since {when}",
	"repos.card.leases": "Leases",
	"repos.card.queue": "Repo queue",
	"repos.card.queue.none": "Nothing in this repository is waiting for you.",
	"repos.card.queue.open": "Open the Action Center",
	"repos.card.receipt": "Install receipt",
	"repos.card.receipt.files_one": "{n} managed file",
	"repos.card.receipt.files_other": "{n} managed files",
	"repos.card.receipt.none": "There is no install receipt. Studio has installed nothing in this repository.",
	"repos.card.receipt.written": "written {when}",
	"repos.card.transactions": "Install history",
	"repos.card.transactions.none": "No install transaction has run for this repository.",
	"repos.col.actions": "Actions",
	"repos.col.aidlc": "AI-DLC",
	"repos.col.engine": "Engine",
	"repos.col.git": "Git",
	"repos.col.intents": "Intents",
	"repos.col.queue": "Queue",
	"repos.col.repository": "Repository",
	"repos.counts.blocking_one": "{n} blocking finding",
	"repos.counts.blocking_other": "{n} blocking findings",
	"repos.counts.inFlight_one": "{n} in flight",
	"repos.counts.inFlight_other": "{n} in flight",
	"repos.counts.intents_one": "{n} intent",
	"repos.counts.intents_other": "{n} intents",
	"repos.counts.open_one": "{n} waiting for you",
	"repos.counts.open_other": "{n} waiting for you",
	"repos.desktopOnly.body": "Choosing an absolute path, reading a preflight and confirming an installation need a wider screen than this one. Open Studio on a desktop to register a repository or to install, upgrade or recover AI-DLC. Everything already registered stays readable here.",
	"repos.desktopOnly.title": "Registration, installation and upgrade are desktop-only",
	"repos.detail.back": "All repositories",
	"repos.directory.help": "Choose a directory on the machine running KiroCrew.",
	"repos.directory.list": "Directories",
	"repos.directory.open": "Browse directories",
	"repos.directory.parent": "Parent directory",
	"repos.directory.select": "Use this directory",
	"repos.directory.truncated": "This list is limited. Enter a more specific path to open a directory that is not shown.",
	"repos.doctor.duration": "Duration: {ms} ms",
	"repos.doctor.output": "Check output",
	"repos.doctor.result": "Doctor result",
	"repos.doctor.stderr": "Error output",
	"repos.drift_one": "{n} managed file changed since install",
	"repos.drift_other": "{n} managed files changed since install",
	"repos.empty.body": "Add a repository by entering its path or choosing a directory. Studio registers only the directory you confirm.",
	"repos.empty.title": "No repository is registered",
	"repos.engine.bundled": "bundled {version}",
	"repos.engine.newerInstalled": "Installed engine is newer than the one Studio bundles",
	"repos.engine.otherHarness": "Installed under {dir}, which Studio does not manage",
	"repos.engine.stages_one": "{n} stage",
	"repos.engine.stages_other": "{n} stages",
	"repos.engine.stateBlocked": "State version {version} — writing it is not proven safe",
	"repos.engine.stateVersion": "state version {version}",
	"repos.engine.upgradeAvailable": "Upgrade available",
	"repos.error.unchanged": "Nothing changed in the repository.",
	"repos.finding.fallback": "Finding {code}",
	"repos.footer": "Maintenance upgrades live here and in notifications; they never enter the blocking workflow queue. Unregistering a repository changes no bytes on disk.",
	"repos.git.ahead_one": "{n} ahead",
	"repos.git.ahead_other": "{n} ahead",
	"repos.git.behind_one": "{n} behind",
	"repos.git.behind_other": "{n} behind",
	"repos.git.clean": "clean",
	"repos.git.detached": "detached HEAD",
	"repos.git.dirty_one": "{n} changed file",
	"repos.git.dirty_other": "{n} changed files",
	"repos.git.head": "HEAD {sha}",
	"repos.git.noUpstream": "no upstream branch",
	"repos.git.observedAt": "observed {when}",
	"repos.git.ownedDirty.body": "Studio installed these files. A local change here stops an upgrade until you resolve it, because Studio never overwrites bytes it cannot prove are its own.",
	"repos.git.ownedDirty.none": "No receipt-owned file has a local change.",
	"repos.git.ownedDirty.title": "Receipt-owned files with local changes",
	"repos.git.readOnly": "Studio only reads Git. It never commits, pushes, checks out, stashes or resets.",
	"repos.git.title": "Git observation",
	"repos.git.unavailable": "Git observation is off",
	"repos.git.unrelatedDirty_one": "{n} unrelated file has a local change, which does not block an upgrade.",
	"repos.git.unrelatedDirty_other": "{n} unrelated files have local changes, which do not block an upgrade.",
	"repos.git.upstream": "tracking {upstream}",
	"repos.lede": "Every repository here was added deliberately. Browse directories or enter a path; Studio never registers repositories automatically.",
	"repos.maintenance.note": "Maintenance work appears here and in notifications. It never enters the blocking workflow queue.",
	"repos.maintenance.title": "Maintenance",
	"repos.metadata.archiveBody": "Hide this repository from the active registry. Its files, session bindings and history are preserved. Restore it from “Show archived repositories” at any time.",
	"repos.metadata.archived": "Archived",
	"repos.metadata.label": "Repository label",
	"repos.metadata.save": "Save label",
	"repos.metadata.showArchived": "Show archived repositories",
	"repos.preflight.aidlc": "AI-DLC on disk",
	"repos.preflight.bun": "bun",
	"repos.preflight.bunFound": "{version} at {path}",
	"repos.preflight.bunMissing": "not found",
	"repos.preflight.canInstall": "AI-DLC can be installed here.",
	"repos.preflight.canRegister": "This path can be registered.",
	"repos.preflight.cannotInstall": "AI-DLC cannot be installed here yet.",
	"repos.preflight.cannotRegister": "This path cannot be registered.",
	"repos.preflight.canonical": "Resolved path",
	"repos.preflight.freeSpace": "Free space",
	"repos.preflight.git": "Git",
	"repos.preflight.gitClean": "clean",
	"repos.preflight.gitDirty": "uncommitted changes",
	"repos.preflight.gitNotRepo": "not a Git repository",
	"repos.preflight.gitRepo": "repository on {branch}",
	"repos.preflight.harness": "Existing harnesses",
	"repos.preflight.identity": "Identity",
	"repos.preflight.identityProvable": "provable",
	"repos.preflight.identityUnprovable": "not provable — execution and installation stay disabled",
	"repos.preflight.inode": "device {dev} · inode {ino}",
	"repos.preflight.input": "You typed",
	"repos.preflight.intents_one": "{n} intent",
	"repos.preflight.intents_other": "{n} intents",
	"repos.preflight.layoutLegacy": "older single-state layout",
	"repos.preflight.layoutNone": "no AI-DLC state",
	"repos.preflight.layoutSpaces": "spaces layout",
	"repos.preflight.none": "none",
	"repos.preflight.notDirectory": "That path is not a directory.",
	"repos.preflight.notWritable": "not writable — an installation would fail",
	"repos.preflight.readOnly": "Read-only. Studio ran nothing from the directory and wrote nothing to it.",
	"repos.preflight.receipt": "Existing receipt",
	"repos.preflight.sensitive": "That location is protected. Studio never reads credential or dashboard-owned directories.",
	"repos.preflight.spaces_one": "{n} space",
	"repos.preflight.spaces_other": "{n} spaces",
	"repos.preflight.stateVersions": "state version {versions}",
	"repos.preflight.symlinks": "Symlinks at managed paths",
	"repos.preflight.symlinksBody": "A symlink where Studio would write is refused: writing through it would put bytes outside the repository.",
	"repos.preflight.title": "Preflight",
	"repos.preflight.warnings": "Warnings",
	"repos.preflight.writable": "writable",
	"repos.rebind.body": "Give the new absolute path. The repository id survives the rebind, so its intents, actions and history stay attached to it.",
	"repos.rebind.confirm": "Rebind",
	"repos.rebind.title": "Rebind {label}",
	"repos.registered": "{label} is registered. Nothing has been installed in it yet.",
	"repos.remedy.identity_unprovable": "Studio cannot prove this directory is a distinct repository, so no lease can be keyed on it. It stays readable; execution and installation stay disabled until the duplicate is resolved.",
	"repos.remedy.moved": "The directory is not where Studio recorded it. Rebind this registration to the new absolute path, so its intents, actions and history stay attached; or unregister it, which changes no bytes on disk.",
	"repos.remedy.permission_denied": "Studio cannot read this directory. Restore read access and rescan. Nothing is retried automatically.",
	"repos.remedy.unavailable": "The directory cannot be read right now. Rescan when it is back, or unregister it — unregistering changes no bytes on disk.",
	"repos.remove.body": "Studio forgets this registration, its session bindings and its action history. Nothing inside the directory changes: no file is deleted, edited or moved.",
	"repos.remove.confirm": "Unregister",
	"repos.remove.title": "Unregister {label}?",
	"repos.title": "Repos",
	"repos.totals": "{repos} registered · {unavailable} unavailable · {open} waiting for you",
	"review.block.contract": "Review contract",
	"review.block.findings": "Reviewer findings",
	"review.block.receipts": "Review receipts",
	"review.contract.class": "Review class",
	"review.contract.classSub": "Configured for this stage",
	"review.contract.none": "Not declared",
	"review.contract.reviewer": "Reviewer",
	"review.contract.reviewerSub": "A review-only agent. It cannot author artifacts.",
	"review.contract.revisions": "Revisions",
	"review.contract.revisionsSub": "Each revision re-ran the reviewer.",
	"review.empty.body": "This stage either declares no reviewer or the reviewer has not run yet. Studio shows findings only when the stage review block contains them.",
	"review.empty.title": "No reviewer findings for this stage",
	"review.evidence.conflict": "conflicts with another source",
	"review.evidence.noRaw": "This source had nothing recorded.",
	"review.evidence.none": "There is no evidence to show for this action.",
	"review.evidence.policy": "Raw blocks are shown as recorded, subject to the redaction and access policy. Studio never edits them.",
	"review.evidence.redacted": "redacted",
	"review.evidence.source.aidlc_audit": "AI-DLC audit",
	"review.evidence.source.aidlc_state": "AI-DLC state",
	"review.evidence.source.git": "Git observation",
	"review.evidence.source.kirocrew_session": "KiroCrew session",
	"review.evidence.source.studio_action": "Studio action",
	"review.evidence.source.turn_marker": "Turn marker",
	"review.evidence.title": "Evidence drawer",
	"review.group.advisory": "Advisory — {n}",
	"review.group.blocker": "Open blockers — {n}",
	"review.group.resolved": "Answered in this revision — {n}",
	"review.group.unknown": "Level not stated by the reviewer — {n}",
	"review.inArtifact": "in artifact",
	"review.iteration": "iteration {n}",
	"review.level.advisory": "Advisory",
	"review.level.blocker": "Blocker",
	"review.level.resolved": "Answered",
	"review.level.unknown": "Level not stated",
	"review.noAnchor": "The reviewer recorded no section for this finding, so it is listed here rather than anchored in the artifact.",
	"review.openBlockers": "{n} open",
	"review.pane.label": "Reviewer findings for {name}",
	"review.pane.title": "reviewer findings",
	"review.quotedVerbatim": "Quoted verbatim from the stage review block.",
	"review.receipt.at": "At",
	"review.receipt.event": "Event",
	"review.receipt.iteration": "Iteration",
	"review.receipt.note": "These rows are AI-DLC's own audit events. Studio reads them and never writes them.",
	"review.receipt.verdict": "Verdict",
	"review.stage": "Stage",
	"review.verdict": "Verdict",
	"scope.allRepos": "All repos",
	"scope.crumbNoScan": "Registered repositories only — registration is always explicit",
	"scope.registered_one": "{n} registered",
	"scope.registered_other": "{n} registered",
	"scope.selectLabel": "Scope",
	"scope.unavailable_one": "{n} repo unavailable",
	"scope.unavailable_other": "{n} repos unavailable",
	"settings.about.boot": "Boot id",
	"settings.about.desc": "The app version and the bundled AI-DLC version move independently. A repository may also run an older installed engine; Repos shows that as maintenance.",
	"settings.about.engineVersion": "Bundled AI-DLC version",
	"settings.about.host": "KiroCrew version",
	"settings.about.hostDetached": "Not attached",
	"settings.about.issues_one": "{n} issue",
	"settings.about.issues_other": "{n} issues",
	"settings.about.minHost": "Minimum KiroCrew version",
	"settings.about.payload": "Bundled payload",
	"settings.about.payloadBad_one": "{n} mismatch",
	"settings.about.payloadBad_other": "{n} mismatches",
	"settings.about.payloadOk_one": "{n} file verified",
	"settings.about.payloadOk_other": "{n} files verified",
	"settings.about.platform": "Platform",
	"settings.about.reconciler": "Reconciler",
	"settings.about.reconcilerRunning": "Running, last pass {when}",
	"settings.about.reconcilerStopped": "Not running",
	"settings.about.status": "Status",
	"settings.about.status.degraded": "Degraded",
	"settings.about.status.error": "Error",
	"settings.about.status.healthy": "Healthy",
	"settings.about.storage": "Storage",
	"settings.about.storageBad": "Schema {schema}, integrity error",
	"settings.about.storageOk": "Schema {schema}, integrity ok",
	"settings.about.studioVersion": "Studio app version",
	"settings.about.title": "About this App",
	"settings.about.toolFound": "{name} {version}",
	"settings.about.toolMissing": "{name} not found",
	"settings.about.tools": "Tools",
	"settings.about.updateChip": "Managed by KiroCrew",
	"settings.about.updateDesc": "KiroCrew installs and updates Apps. Studio does not check for its own updates.",
	"settings.about.updateState": "Update state",
	"settings.advisor.autoDraft.count_one": "Drafting ahead in {n} repository",
	"settings.advisor.autoDraft.count_other": "Drafting ahead in {n} repositories",
	"settings.advisor.autoDraft.desc": "For a repository ticked here, Studio asks the Advisor for a draft as soon as a question or a gate appears — before you open it — so the recommendation is already waiting instead of making you wait about three minutes for it. It still only reads: it cannot answer, approve or submit anything, and on a question it fills its answers into the form for you to read and change. It spends real model usage on every card, which is why it is granted per repository and off until you grant it. Unticking stops future drafts; drafts already made are left alone.",
	"settings.advisor.autoDraft.label": "Draft ahead in {repo}",
	"settings.advisor.autoDraft.none": "No repositories are registered yet.",
	"settings.advisor.autoDraft.orphan": "{id} may draft ahead but is no longer registered.",
	"settings.advisor.autoDraft.title": "Draft ahead, per repository",
	"settings.advisor.desc": "The Advisor drafts and explains on request. It cannot write files, move the workflow, or mint a HUMAN_TURN.",
	"settings.advisor.label": "Offer the Advisor on decisions",
	"settings.advisor.model.chip": "Inherited",
	"settings.advisor.model.desc": "Inherited through KiroCrew role resolution. Studio hardcodes no model id and shows none.",
	"settings.advisor.model.title": "Advisor model",
	"settings.advisor.title": "Advisor availability",
	"settings.bun.auto": "Use automatic detection",
	"settings.bun.checking": "Checking Bun…",
	"settings.bun.desc": "Choose the Bun executable on the gateway computer, or detect an installation again. Changes take effect without restarting Studio.",
	"settings.bun.missing": "Bun was not found. Set its path or install it, then detect again.",
	"settings.bun.path": "Absolute path to Bun",
	"settings.bun.probe": "Detect Bun again",
	"settings.bun.ready": "Bun is ready.",
	"settings.bun.save": "Save and verify path",
	"settings.bun.searched": "Checked: {paths}",
	"settings.bun.title": "Bun executable",
	"settings.concurrency.desc": "How many repositories may run a turn at the same time. Per-repository execution is always one and cannot be raised.",
	"settings.concurrency.label": "Repositories running at once",
	"settings.concurrency.title": "Global concurrency",
	"settings.concurrency.value_one": "{n} repository",
	"settings.concurrency.value_other": "{n} repositories",
	"settings.creditCap.desc": "Credit consumption is not observable from the available signals, so a cap cannot be enforced and is disabled rather than estimated.",
	"settings.creditCap.title": "Credit cap",
	"settings.dashboard.desc": "The count on the KiroCrew rail and the in-page banners.",
	"settings.dashboard.label": "Show dashboard notifications",
	"settings.dashboard.title": "Dashboard notifications",
	"settings.density.comfortable": "Comfortable",
	"settings.density.compact": "Compact",
	"settings.density.desc": "Compact is the default. Comfortable adds vertical space without changing what is shown.",
	"settings.density.label": "Density",
	"settings.density.title": "Display density",
	"settings.diagnostics.humanRetention.desc": "How long the exact text you submitted is kept at all. One to thirty days.",
	"settings.diagnostics.humanRetention.label": "Days the submitted text is kept",
	"settings.diagnostics.humanRetention.title": "Prompt body retention",
	"settings.diagnostics.humanText.desc": "Off by default. While it is off the backend refuses to put the text you submitted into any export, whatever the export asks for.",
	"settings.diagnostics.humanText.label": "Allow prompt bodies in a diagnostic export",
	"settings.diagnostics.humanText.title": "Prompt bodies in exports",
	"settings.diagnostics.retention.desc": "How long Studio keeps its own activity rows and transaction records.",
	"settings.diagnostics.retention.label": "Days of history",
	"settings.diagnostics.retention.title": "Diagnostic retention",
	"settings.diagnostics.retention.value_one": "{n} day",
	"settings.diagnostics.retention.value_other": "{n} days",
	"settings.doctor.desc": "Runs the AI-DLC workspace doctor once after an install or upgrade commits. It reads; it writes nothing.",
	"settings.doctor.label": "Run doctor after an install or upgrade",
	"settings.doctor.title": "Run doctor after install",
	"settings.locale.auto": "Follow the dashboard",
	"settings.locale.desc": "English and Simplified Chinese ship complete and parity-tested. AI-DLC's own words — stage slugs, questions, artifacts, findings and audit fields — are never translated.",
	"settings.locale.en-US": "English (en-US)",
	"settings.locale.following": "Following the dashboard — now {locale}.",
	"settings.locale.label": "Interface language",
	"settings.locale.overridden": "Overriding the dashboard for Studio only.",
	"settings.locale.title": "Language",
	"settings.locale.zh-CN": "简体中文 (zh-CN)",
	"settings.night.desc": "Unattended continuation is not available. Every KiroCrew path into the agent mints a protected HUMAN_TURN, so a machine dispatch would forge human presence in the AI-DLC audit trail.",
	"settings.night.end": "End",
	"settings.night.start": "Start",
	"settings.night.storedOnly": "The window is stored so it is ready if a trusted machine lane is ever proven. Nothing acts on it today.",
	"settings.night.title": "Night work window",
	"settings.night.window": "{start} – {end} local",
	"settings.page.error": "Settings could not be read: {message}",
	"settings.page.lede": "Studio-owned preferences. Nothing here changes AI-DLC files.",
	"settings.page.reading": "Reading settings…",
	"settings.page.retry": "Read again",
	"settings.page.saveFailed": "That change was not saved: {message}",
	"settings.page.saveFailedKey": "That change was not saved. The backend rejected {key}: {message}",
	"settings.page.saved": "Saved.",
	"settings.page.saving": "Saving…",
	"settings.page.status": "Settings status",
	"settings.page.title": "Settings",
	"settings.queue.desc": "How the Action Center orders what is waiting. Priority keeps recovery and delivery uncertainty first.",
	"settings.queue.label": "Order the queue by",
	"settings.queue.localNote": "This choice is also kept in this browser, so a new tab opens the way you left it.",
	"settings.queue.oldest": "Oldest first",
	"settings.queue.priority": "Priority",
	"settings.queue.repo": "Repository",
	"settings.queue.title": "Queue organization",
	"settings.queue.type": "Action type",
	"settings.reason.credits_unobservable": "credit use is not observable",
	"settings.reason.host_capability_unavailable": "the host reports it unavailable",
	"settings.reason.host_capability_unknown": "the host does not report this capability",
	"settings.reason.host_not_attached": "Studio is not attached to the dashboard",
	"settings.reason.host_seam_unavailable": "the host offers no seam for it",
	"settings.reason.machine_lane_unavailable": "no trusted machine lane is proven",
	"settings.reason.other": "{raw}",
	"settings.reason.s12_unproven": "no trusted machine lane is proven",
	"settings.reason.s1_s2_unverified": "the grouped-answer transport is unverified",
	"settings.section.about": "About",
	"settings.section.advisor": "Advisor",
	"settings.section.automation": "Automation and budgets",
	"settings.section.diagnostics": "Diagnostics",
	"settings.section.locale": "Language and display",
	"settings.section.notifications": "Notifications",
	"settings.section.queue": "Action queue",
	"settings.slack.desc": "Blocking events only: Gates, questions, execution failures, delivery uncertainty and the completion digest. Delivery is the configured KiroCrew owner DM.",
	"settings.slack.label": "Send Slack notifications",
	"settings.slack.mute.count_one": "{n} repository muted",
	"settings.slack.mute.count_other": "{n} repositories muted",
	"settings.slack.mute.desc": "A muted repository still queues its actions here; it just sends no Slack message.",
	"settings.slack.mute.label": "Mute {repo}",
	"settings.slack.mute.none": "No repositories are registered yet.",
	"settings.slack.mute.orphan": "{id} is muted but no longer registered.",
	"settings.slack.mute.title": "Muted repositories",
	"settings.slack.quickActions.desc": "Deciding from Slack needs a host seam that does not exist, so every Slack message deep-links back here instead.",
	"settings.slack.quickActions.title": "Slack quick actions",
	"settings.slack.title": "Slack notifications",
	"settings.turnCap.desc": "Checked before dispatch and never interrupting a running turn. Stored, but inert while the window is unavailable.",
	"settings.turnCap.label": "Turns per window",
	"settings.turnCap.title": "Turn cap per window",
	"settings.unavailable": "Unavailable",
	"settings.unavailableWhy": "Unavailable — {reason}",
	"shell.a11y.primaryNav": "Primary",
	"shell.a11y.queueCount_one": "{n} item needs you",
	"shell.a11y.queueCount_other": "{n} items need you",
	"shell.backToQueue": "Back to queue",
	"shell.banner.degraded_one": "Studio reports {n} issue with its own environment. Workflow state is still read from disk.",
	"shell.banner.degraded_other": "Studio reports {n} issues with its own environment. Workflow state is still read from disk.",
	"shell.banner.openSettings": "Open Settings",
	"shell.banner.reload": "Reload",
	"shell.banner.sessionExpired": "Your dashboard session has expired. Nothing was sent. Reload the page to sign in again.",
	"shell.brand": "AI-DLC Studio",
	"shell.error.nothingSent": "Nothing was sent and no decision was recorded. Retry re-renders this view; the evidence on disk is unchanged.",
	"shell.error.title": "{where} could not be rendered",
	"shell.format.bytes": "{n} B",
	"shell.format.kb": "{n} KB",
	"shell.format.listJoin": ", ",
	"shell.format.mb": "{n} MB",
	"shell.format.range": "{low} – {high}",
	"shell.format.ratio": "{used} / {cap}",
	"shell.notBuilt.body": "This part of Studio is not in this build yet. Nothing is being hidden from you — the view does not exist.",
	"shell.notBuilt.title": "{view} is not built yet",
	"shell.strip.alerts_one": "{n} alert",
	"shell.strip.alerts_other": "{n} alerts",
	"shell.strip.circuits_one": "{n} circuit open",
	"shell.strip.circuits_other": "{n} circuits open",
	"shell.strip.clear": "Nothing needs attention",
	"shell.strip.collapse": "Hide execution status",
	"shell.strip.critical_one": "{n} recovery decision",
	"shell.strip.critical_other": "{n} recovery decisions",
	"shell.strip.expand": "Show execution status",
	"shell.strip.leases_one": "{n} repo lease held",
	"shell.strip.leases_other": "{n} repo leases held",
	"shell.strip.nightOff": "Night window off",
	"shell.strip.nightOn": "Night window {start} – {end}",
	"shell.strip.polling": "Polling for updates",
	"shell.strip.pollingWhy": "The live event stream is not connected, so Studio is re-reading on a timer. Nothing is missed; updates are slower.",
	"shell.strip.running_one": "{n} turn running",
	"shell.strip.running_other": "{n} turns running",
	"template.a11y.decision": "{type} decision. {repo}, {intent}, stage {stage}. State: {state}.",
	"template.budget.choices": "What you can do",
	"template.budget.creditNote": "Credit consumption is not observable from the available Kiro signals, so a credit cap cannot be enforced. It is disabled rather than estimated.",
	"template.budget.credits": "Credits",
	"template.budget.effect": "What this blocks",
	"template.budget.effectBody": "The intent stays queued. Raising the cap, or running it manually now, is the only way it advances.",
	"template.budget.localTime": "local time",
	"template.budget.neverInterrupts": "a running turn is never interrupted",
	"template.budget.noBudget": "This item carries no budget figures. Studio will not infer them from anything else.",
	"template.budget.noSession": "no session bound",
	"template.budget.notObservable": "not observable",
	"template.budget.notOnCard": "not recorded on this item",
	"template.budget.openSettings": "Open budget settings",
	"template.budget.queueDepth": "queue {n}",
	"template.budget.state": "Budget state",
	"template.budget.turns": "Turns used",
	"template.budget.window": "Window",
	"template.command.choices": "What you can do",
	"template.command.dispatch": "What will be dispatched",
	"template.command.lease": "one turn per repository at a time",
	"template.command.noSession": "No session bound",
	"template.command.noSessionSub": "Studio binds one before it dispatches, and refuses if it cannot.",
	"template.command.note": "Studio dispatches one turn and then watches disk state. It does not chain turns on its own.",
	"template.command.session": "Canonical session",
	"template.command.sessionSub": "{state} · last turn {turn}",
	"template.command.stage": "Stage",
	"template.command.stageSub": "Where the engine will resume from, as recorded on disk.",
	"template.command.superseded": "This unsent run is no longer needed: the workflow reached a checkpoint or moved on. Open current actions to see what needs attention.",
	"template.common.brief": "Decision brief",
	"template.common.closedBody": "This action is closed. Use current actions to see what this intent needs now.",
	"template.common.closedTitle": "Historical record",
	"template.common.conflict": "conflict",
	"template.common.consequenceFallback": "The consequence of this decision is not available in this build.",
	"template.common.findingFallback": "AI-DLC's files disagree here. The evidence below names the files.",
	"template.common.headlineFallback": "This item is waiting for you. Its summary is not available in this build.",
	"template.common.met": "met",
	"template.common.nextConsequence": "Next consequence.",
	"template.common.noAutoChoice": "Studio applies exactly the option you choose in the bar below. It never picks one to make the evidence agree.",
	"template.common.notMet": "not met",
	"template.common.originalNotice": "Notice when this record was created",
	"template.common.refreshing": "The evidence under this item changed while it was being read, so nothing can be sent until Studio re-reads it.",
	"template.common.tooLong": "This is longer than Studio will send ({max} characters). Shorten it before confirming.",
	"template.common.unknownMet": "not evaluated by Studio",
	"template.failure.breakerClosed": "Breaker closed",
	"template.failure.breakerOpen": "Breaker open — no further dispatch",
	"template.failure.choices": "What you can do",
	"template.failure.colAt": "At",
	"template.failure.colAttempt": "#",
	"template.failure.colBackoff": "Backoff",
	"template.failure.colOutcome": "Outcome",
	"template.failure.count": "{n} matching failures",
	"template.failure.findings": "What else disagrees",
	"template.failure.fingerprint": "fingerprint {fingerprint} · class {cls}",
	"template.failure.history": "Retry and backoff history",
	"template.failure.historyCaption": "Each attempt, when it ran, how long Studio waited, and what happened.",
	"template.failure.log": "Session log excerpt",
	"template.failure.logNote": "This is the engine's own output as Studio captured it. Studio does not interpret it and does not send it anywhere.",
	"template.failure.logPane": "engine stderr",
	"template.failure.normalized": "Normalized error",
	"template.failure.session": "Session state",
	"template.failure.sessionSub": "{state} · stop state {stop} · last turn {turn}",
	"template.failure.summaryUnknown": "The engine failed without a classified summary. The excerpt below is what Studio captured.",
	"template.gate.compare": "Artifact and reviewer",
	"template.gate.criteria": "Stage acceptance criteria — {met} of {total} met",
	"template.gate.criteriaUnknown": "Stage acceptance criteria — {total} listed, none evaluated by Studio",
	"template.gate.feedback": "Feedback for a change request",
	"template.gate.feedbackLabel": "Change request feedback",
	"template.gate.feedbackPlaceholder": "Say what has to change. A change request cannot be sent blank.",
	"template.gate.feedbackRouting": "Studio sends this to the canonical session as your turn. It never edits the artifact.",
	"template.gate.history": "Prior revisions",
	"template.gate.noArtifact": "No produced artifact",
	"template.gate.noArtifactBody": "This stage recorded no produced artifact. That is itself part of the evidence.",
	"template.gate.noBlockers": "no open blockers",
	"template.gate.noFindings": "The reviewer recorded no findings for this stage.",
	"template.gate.noReview": "No reviewer ran for this stage, so there are no findings to quote.",
	"template.gate.reviewPasses": "Review passes",
	"template.gate.reviewPassesSub": "Each revision re-ran the reviewer.",
	"template.gate.reviewer": "reviewer findings",
	"template.gate.revisions": "Revisions",
	"template.gate.revisionsSub": "Recorded in the state file for this intent.",
	"template.gate.risks": "Unresolved risks",
	"template.install.action.conflict": "conflicts with a local file",
	"template.install.action.create": "would be created",
	"template.install.action.engine_modified": "rewritten by the engine",
	"template.install.action.identical": "identical",
	"template.install.action.merge_conflict": "merge target conflicts",
	"template.install.action.merge_create": "merge target would be created",
	"template.install.action.merge_identical": "merge target unchanged",
	"template.install.action.merge_update": "merge target would be updated",
	"template.install.action.owned_identical": "unchanged since install",
	"template.install.action.owned_modified": "modified locally since install",
	"template.install.action.retire": "would be retired",
	"template.install.action.retire_blocked": "cannot be retired",
	"template.install.action.shell_create": "workspace file would be created",
	"template.install.action.shell_exists": "workspace file already exists",
	"template.install.bundled": "Bundled with Studio",
	"template.install.bundledSub": "The version this Studio build would install.",
	"template.install.colHash": "Hashes",
	"template.install.colOwnership": "Ownership",
	"template.install.colPath": "Path",
	"template.install.colState": "State",
	"template.install.diffLabel": "Difference for {path}",
	"template.install.drift": "Managed-file drift",
	"template.install.driftCaption": "Every managed file, its owner, the comparison result and its hashes.",
	"template.install.fixNoMerge": "Studio does not merge these for you. An installer that resolved this silently would break the receipt that makes recovery possible.",
	"template.install.fixRevert": "Revert the listed files to their recorded content, then run the upgrade preview again.",
	"template.install.fixStay": "Or keep the local edits and stay on the installed version; the intent keeps running against it.",
	"template.install.hashLive": "on disk {hash}",
	"template.install.hashPayload": "bundled {hash}",
	"template.install.hashReceipt": "receipt {hash}",
	"template.install.installed": "Installed engine",
	"template.install.managed": "Managed files",
	"template.install.managedSub": "Each file is compared against its recorded hash, not against its timestamp.",
	"template.install.managedValue": "{n} compared · {conflicts} in conflict",
	"template.install.noDrift": "No managed file differs from its recorded content.",
	"template.install.noReceipt": "No receipt covers this installation, so Studio cannot prove which files it owns.",
	"template.install.openRepo": "Open this repository on Repos",
	"template.install.ownership": "Receipt ownership",
	"template.install.receiptSub": "Recorded by receipt {id}, with a version, path and SHA-256 per file.",
	"template.install.remediation": "How to move forward",
	"template.install.showDiff": "Show the difference for {path}",
	"template.install.stoppedBody": "The repository keeps its complete installation and its receipt. A partial upgrade is worse than an old one.",
	"template.install.stoppedLabel": "Nothing was written.",
	"template.install.why": "Why this is a conflict",
	"template.install.whyBody": "These paths belong to the recorded installation, and their content on disk no longer matches the hash the receipt recorded. Studio cannot tell whether the local edit or the bundled version is the one you want.",
	"template.missingInput.adminLane": "admin lease",
	"template.missingInput.blocked": "Every dispatch for this intent is refused. Nothing is queued behind it and nothing is assumed.",
	"template.missingInput.blockedLabel": "Until then.",
	"template.missingInput.choices": "What you can do",
	"template.missingInput.cursorSrc": "Active intent",
	"template.missingInput.cursorSub": "space {space}",
	"template.missingInput.findings": "What else disagrees",
	"template.missingInput.input.free_text": "What to send",
	"template.missingInput.input.scope": "The scope to send",
	"template.missingInput.openIntents": "Open the Intents page",
	"template.missingInput.pickBody": "The active intent is chosen through an AI-DLC engine operation under the repository admin lease, from the intents that exist on disk. Studio will not guess one, so pick it on the Intents page.",
	"template.missingInput.pickTitle": "Choosing the active intent",
	"template.missingInput.placeholder.free_text": "What the engine asked you for.",
	"template.missingInput.placeholder.scope": "One line describing what this intent covers.",
	"template.missingInput.reason.dangling_cursor": "AI-DLC's active intent points at a directory that is not there, so no dispatch can be aimed at it.",
	"template.missingInput.reason.missing_scope": "This intent's state file has no Scope, and the engine needs one before it can plan or run.",
	"template.missingInput.reason.no_cursor": "AI-DLC has no active intent in this space, so there is nothing for a dispatch to move.",
	"template.missingInput.reasonUnknown": "Studio needs something from you before this intent can advance.",
	"template.missingInput.routing": "Studio sends this to the canonical session as your turn. It does not write the state file itself.",
	"template.missingInput.source": "Where Studio looked",
	"template.missingInput.stateSub": "current stage {stage} · read {at}",
	"template.missingInput.what": "What is missing",
	"template.questions.answered": "answered",
	"template.questions.auditSource": "This checkpoint is recorded in the AI-DLC audit trail. Your reply will be sent verbatim to the bound conversation.",
	"template.questions.checkpoint.plan_approval": "Plan approval",
	"template.questions.checkpoint.plan_approval.body": "The engine is asking you to approve the plan it composed before it executes it.",
	"template.questions.checkpoint.summary_confirmation": "Summary confirmation",
	"template.questions.checkpoint.summary_confirmation.body": "The engine is asking whether its summary of this stage is correct before it moves on.",
	"template.questions.checkpointAnswered": "Recorded answer: {answer}",
	"template.questions.degradedBody": "Studio cannot safely submit this question group as a form. The question format may be unsupported, or the conversation may be waiting on a native question. Answer in the canonical conversation.",
	"template.questions.degradedLabel": "Degraded mode.",
	"template.questions.degradedTitle": "Structured answering is unavailable",
	"template.questions.drafted": "ready to send",
	"template.questions.feedback": "Feedback for a change request",
	"template.questions.feedbackLabel": "Change request feedback for this checkpoint",
	"template.questions.fileSource": "Choose an answer for each question below. Studio reads these options from the saved question file and sends your confirmed answers together to the bound conversation.",
	"template.questions.freeText": "Free text",
	"template.questions.group": "Question group — answered together",
	"template.questions.looksCorrect": "The summary is correct",
	"template.questions.looksCorrectHint": "Confirms the engine’s summary as written, so it moves on.",
	"template.questions.neverEdits": "Studio never edits {path} or its digest. The group stays visible until delivery and state evidence prove it was accepted.",
	"template.questions.none": "This card carries no questions. The group may already have been answered.",
	"template.questions.openConversation": "Open the conversation",
	"template.questions.otherDesc": "Free text, preserved exactly as you type it.",
	"template.questions.otherLabel": "Your own answer to question {index}",
	"template.questions.otherPlaceholder": "Your answer",
	"template.questions.pending": "{n} of {total} unanswered",
	"template.questions.recorded": "Recorded answer",
	"template.questions.required": "required",
	"template.questions.selectAny": "select any",
	"template.questions.selectOne": "select one",
	"template.questions.summaryChanges": "The summary needs changes",
	"template.questions.summaryChangesHint": "Sends your note instead of a confirmation. The note below is required.",
	"template.questions.summaryChoiceLabel": "Your reply to this checkpoint",
	"template.questions.textPlaceholder": "Enter the note you want to record, in your own words.",
	"template.questions.textStillRequired": "Only the input label was sent; no note was recorded. This attempt is closed. Open current actions to enter the note text.",
	"template.questions.unsupportedBody": "This file contains unanswered sections that Studio cannot safely map to form questions. Read and answer them in the canonical conversation.",
	"template.questions.unsupportedPending": "{n} additional answer fields pending",
	"template.recovery.auditSub": "{shards} audit shards · {complete}",
	"template.recovery.bootRestarted": "The gateway restarted, so an in-flight send could have been lost.",
	"template.recovery.bootRestartedConfirmed": "The gateway restarted. Message delivery is confirmed; workflow reconciliation is still pending.",
	"template.recovery.bootSame": "The gateway did not restart.",
	"template.recovery.bootUnknown": "Whether the gateway restarted is not recorded.",
	"template.recovery.boundary": "Last stable boundary",
	"template.recovery.boundarySrc": "Boundary",
	"template.recovery.boundarySub": "Recorded at {at}. Context can be reconstructed from disk at this point.",
	"template.recovery.boundaryUnknown": "No stable boundary was recorded, so context cannot be reconstructed from one.",
	"template.recovery.choices": "Recovery choices",
	"template.recovery.complete": "read completely",
	"template.recovery.confirmedAlert": "Delivery is confirmed. This intent still needs workflow reconciliation. Answer acceptance has not been verified; nothing is replayed automatically.",
	"template.recovery.confirmedBody": "The message reached the conversation. The AI-DLC audit, turn marker and cursor still need to establish how the workflow handled it. The same message will not be resent.",
	"template.recovery.confirmedTitle": "Workflow reconciliation",
	"template.recovery.contradiction": "The contradiction",
	"template.recovery.contradictionBody": "Studio asked the host to deliver your decision and never got an answer it can trust. Approving twice would advance the stage twice, so nothing is retried on its own.",
	"template.recovery.contradictionChecks": "{row} {disk} {boot}",
	"template.recovery.cursorMismatch": "The active intent differs: {fields}",
	"template.recovery.cursorOk": "The active intent is the one this decision belongs to.",
	"template.recovery.deliverySub": "Confirmed in the conversation: {confirmed} · {at}",
	"template.recovery.directiveDiffers": "Does not match the state file.",
	"template.recovery.directiveMatches": "Matches the state file.",
	"template.recovery.diskChanged": "The files Studio recorded before sending have changed.",
	"template.recovery.diskUnchanged": "The files Studio recorded before sending are unchanged.",
	"template.recovery.diskUnknown": "Whether those files changed is not recorded.",
	"template.recovery.evidence": "Evidence from every source",
	"template.recovery.findings": "What disagrees",
	"template.recovery.gitClean": "Working tree clean. No Git write was performed.",
	"template.recovery.gitDirty": "{n} files changed in the working tree. No Git write was performed.",
	"template.recovery.incomplete": "read incompletely",
	"template.recovery.manageConversation": "Manage conversation in Intents",
	"template.recovery.markerSub": "Last human turn {at} · presence {presence}",
	"template.recovery.no": "no",
	"template.recovery.outcome.confirmed": "the conversation received the message",
	"template.recovery.outcome.delivered": "the host accepted it",
	"template.recovery.outcome.not_delivered": "the host refused it",
	"template.recovery.outcome.uncertain": "the send was never confirmed",
	"template.recovery.presenceFailed": "not as expected",
	"template.recovery.presenceOk": "as expected",
	"template.recovery.presenceUnknown": "not compared yet",
	"template.recovery.revisions": "{n} revisions recorded",
	"template.recovery.risk.admin": "holds the repository admin lease",
	"template.recovery.risk.host_control": "controls the KiroCrew session",
	"template.recovery.risk.human_lane": "sends your turn to the canonical session",
	"template.recovery.risk.read": "read-only decision",
	"template.recovery.risk.studio_only": "changes Studio's own records only",
	"template.recovery.rowAbsent": "No matching turn was found in the conversation.",
	"template.recovery.rowFound": "A matching turn exists in the conversation at {at}.",
	"template.recovery.sessionIdle": "idle",
	"template.recovery.sessionMissing": "The recorded conversation was unavailable when this notice was created.",
	"template.recovery.sessionRepair": "Open the intent's conversation settings and bind a new or existing aidlc conversation. Acknowledging only dismisses this notice.",
	"template.recovery.sessionRunning": "running",
	"template.recovery.sessionSub": "{state} · queue {queue} · busy: {reasons}",
	"template.recovery.src.audit": "AI-DLC audit",
	"template.recovery.src.cursor": "Cursor read-back",
	"template.recovery.src.delivery": "Studio delivery",
	"template.recovery.src.directive": "AI-DLC directive",
	"template.recovery.src.git": "Git observation",
	"template.recovery.src.marker": "Turn marker",
	"template.recovery.src.session": "KiroCrew session",
	"template.recovery.src.state": "AI-DLC state",
	"template.recovery.turnCounter": "turn {n}",
	"template.recovery.uncertainAlert": "This delivery cannot be proved either way. Nothing is replayed automatically, and this intent stays blocked until the evidence agrees.",
	"template.recovery.yes": "yes",
	"unavailable.machineLane": "Unavailable — no machine lane has been proven (S12)",
	"wizard.a11y.stepper": "New intent steps",
	"wizard.advisor.applied": "Filled in from the Advisor's proposal. Nothing has been created — review and change anything before you press Create.",
	"wizard.advisor.ask": "Let the Advisor propose these settings",
	"wizard.advisor.askAgain": "Ask again",
	"wizard.advisor.clear": "Clear the proposal",
	"wizard.advisor.dropped_one": "{n} stage change the engine refused was left out; the refusal is shown in the proposal's stage changes above.",
	"wizard.advisor.dropped_other": "{n} stage changes the engine refused were left out; the refusals are shown in the proposal's stage changes above.",
	"wizard.advisor.keep": "not proposed — keeps {value}",
	"wizard.advisor.lede": "Tired of the questionnaire? The Advisor can read your objective and this repository and propose the scope, depth, test strategy, review cap and stage set. Nothing is applied until you say so.",
	"wizard.advisor.needObjective": "Write the objective on the Work step first — it is what the Advisor reads.",
	"wizard.advisor.proposal": "What the Advisor proposes",
	"wizard.advisor.stages": "Stage changes against that scope's own selection",
	"wizard.advisor.stale.objective": "The objective changed after this proposal was drafted. Ask again for one that reads the new objective.",
	"wizard.advisor.stale.scope": "The scope changed after this proposal was drafted; its stage changes were computed against {scope}. Pick {scope} again or ask again.",
	"wizard.advisor.unresolved_one": "{n} answer could not be mapped to a setting and was left as it is.",
	"wizard.advisor.unresolved_other": "{n} answers could not be mapped to a setting and were left as they are.",
	"wizard.advisor.use": "Use this proposal",
	"wizard.advisor.useNote": "It fills the fields below. Change any of them; nothing is created until you press Create.",
	"wizard.created.again": "Create another intent",
	"wizard.created.body": "Nothing has run. AI-DLC wrote the intent record and Studio re-read the disk to confirm it exists.",
	"wizard.created.intent": "{intent} in {repo}",
	"wizard.created.next": "Open Intents to run it",
	"wizard.created.title": "Intent created and left paused",
	"wizard.created.transaction": "Transaction {id}",
	"wizard.created.unverified": "Studio could not confirm every part of the record on disk. Open the intent before running it.",
	"wizard.created.verified": "Verified on disk",
	"wizard.creating": "Creating the intent…",
	"wizard.error.create": "The intent was not created.",
	"wizard.error.preview": "The plan could not be computed.",
	"wizard.error.retry": "Try again",
	"wizard.lede": "Four steps. Nothing is written to the repository until you press Create, and Create never starts execution.",
	"wizard.nav.back": "Back",
	"wizard.nav.continue": "Continue",
	"wizard.nav.create": "Create the intent, paused",
	"wizard.partial.activate": "Retrying activates this intent so the engine compiles the correct runtime graph.",
	"wizard.partial.body": "{intent} already exists. Retry compilation for this intent instead of creating another one. No workflow turn was started.",
	"wizard.partial.open": "Open the existing intent",
	"wizard.partial.planComposition.body": "{intent} already exists, but the selected stage changes were not applied. Open this intent to review and correct its plan before compiling or running it.",
	"wizard.partial.planComposition.open": "Open intent to review its plan",
	"wizard.partial.planComposition.title": "Intent created; plan needs correction",
	"wizard.partial.repairing": "Compiling runtime graph…",
	"wizard.partial.retry": "Retry runtime compilation",
	"wizard.partial.title": "Intent created; runtime graph needs repair",
	"wizard.plan.lead": "Every known stage is listed, including the ones your preset excludes. Stages the engine will not let you change are disabled with the reason.",
	"wizard.plan.summary": "{stages} stages, {gates} Gates, {artifacts} artifacts",
	"wizard.preset.depth": "Depth",
	"wizard.preset.depth.Comprehensive": "More questions and longer artifacts.",
	"wizard.preset.depth.Minimal": "One pass per stage, fewer questions.",
	"wizard.preset.depth.Standard": "Questions where the engine needs your input.",
	"wizard.preset.depthHelp": "Controls question volume and artifact detail.",
	"wizard.preset.fromScope": "From the scope",
	"wizard.preset.fromScopeDepth": "The scope does not say, so {value} is used.",
	"wizard.preset.fromScopeUnset": "The scope does not say, so AI-DLC's own default is used.",
	"wizard.preset.fromScopeValue": "The scope asks for {value}.",
	"wizard.preset.review": "Review cap",
	"wizard.preset.review.adversarial": "Reviewer findings must be answered before a Gate can pass.",
	"wizard.preset.review.advisory": "Reviewer comments; they do not block.",
	"wizard.preset.review.none": "No reviewer pass. Gates still require you.",
	"wizard.preset.reviewHelp": "The strongest review class any stage may use. It can only ever reduce review work.",
	"wizard.preset.scope": "Scope",
	"wizard.preset.scopeHelp": "AI-DLC's scope decides which of the known stages are eligible. Studio reads the list from this repository.",
	"wizard.preset.scopeMetaDepth": "depth {value}",
	"wizard.preset.scopeMetaProjectOwned": "defined by this project",
	"wizard.preset.scopeMetaReview": "review cap {value}",
	"wizard.preset.scopeMetaTest": "tests {value}",
	"wizard.preset.scopeResets": "Changing the scope resets the stage matrix to that scope's own selection.",
	"wizard.preset.scopeSelected": "{on} of {total} stages selected",
	"wizard.preset.scopeUnreadable": "Studio could not read the scope list from this repository, so no preset can be offered.",
	"wizard.preset.scopeUnselected": "Select it to read the exact stage count from the installed graph.",
	"wizard.preset.test": "Test strategy",
	"wizard.preset.test.Comprehensive": "Adds performance validation in operation.",
	"wizard.preset.test.Minimal": "Smoke coverage only.",
	"wizard.preset.test.Standard": "Unit plus integration for generated units.",
	"wizard.preset.testHelp": "Controls test volume and whether performance validation is eligible.",
	"wizard.preview.busy": "Recomputing the plan…",
	"wizard.preview.none": "The plan has not been computed yet.",
	"wizard.preview.stale": "Showing the previous plan while Studio recomputes it.",
	"wizard.review.blockedFields": "The Work step is incomplete.",
	"wizard.review.blockedFindings_one": "This repository reports {n} blocking finding, so nothing will be created until it is resolved.",
	"wizard.review.blockedFindings_other": "This repository reports {n} blocking findings, so nothing will be created until they are resolved.",
	"wizard.review.blockedInvalid": "AI-DLC will not accept this plan as described. The reasons are listed on the Plan step.",
	"wizard.review.consequence": "Create does not run anything. The intent is created paused; you start it with Run to next checkpoint. Keep moving, budgets and Advisor drafts are never inherited from another intent.",
	"wizard.review.estimates": "Estimated, with source and confidence",
	"wizard.review.exact": "Exact, from the installed graph",
	"wizard.review.plan": "The plan Studio will confirm",
	"wizard.review.planDigest": "Plan fingerprint {digest}",
	"wizard.review.planDigestWhy": "Create sends this fingerprint. If the installed graph or the scope changed since this preview, AI-DLC refuses rather than creating a plan you did not see.",
	"wizard.review.products": "Products this plan will create",
	"wizard.review.productsNone": "No stage in this plan declares an artifact, which usually means nothing is selected.",
	"wizard.step.a11y": "Step {n}, {label}",
	"wizard.step.done": "done",
	"wizard.step.of": "Step {n} of 4",
	"wizard.step.plan": "Plan",
	"wizard.step.preset": "Preset",
	"wizard.step.review": "Review",
	"wizard.step.work": "Work",
	"wizard.title": "New intent",
	"wizard.work.context": "Context the engine should know",
	"wizard.work.contextHelp": "Optional. Sent after the objective, separated by a blank line.",
	"wizard.work.contextPlaceholder": "Existing cart service is v3. Payment provider retries once. No account creation is allowed in this flow.",
	"wizard.work.label": "Label",
	"wizard.work.labelDerived": "Left empty, AI-DLC derives {label} from the objective.",
	"wizard.work.labelHelp": "At most three lowercase words joined by hyphens. AI-DLC turns it into the intent's directory name, and it cannot be renamed afterwards.",
	"wizard.work.labelInvalid": "At most three lowercase words joined by hyphens, for example guest-checkout.",
	"wizard.work.labelPlaceholder": "guest-checkout",
	"wizard.work.labelUndeducible": "The objective produces no label AI-DLC can use, so type one.",
	"wizard.work.objective": "Objective",
	"wizard.work.objectiveHelp": "One sentence. The engine reads it as this intent's input.",
	"wizard.work.objectivePlaceholder": "Let signed-out shoppers complete a purchase",
	"wizard.work.objectiveRequired": "An objective is required: it is what the engine is asked to work on.",
	"wizard.work.projectType": "Project type",
	"wizard.work.projectTypeHelp": "Passed to the engine, which uses it to decide whether conditional stages apply. Leave it unset if you are not sure.",
	"wizard.work.projectTypeUnset": "Not specified",
	"wizard.work.repo": "Repository",
	"wizard.work.repoBlocked": "This repository cannot host a new intent: {reason}",
	"wizard.work.repoBlockedOpen": "Open Repos",
	"wizard.work.repoHelp": "Only repositories you explicitly register appear here.",
	"wizard.work.repoNone": "No repository is registered yet, so there is nothing to create an intent in.",
	"wizard.work.repoNotInstalled": "AI-DLC is not installed here",
	"wizard.work.repoPick": "Choose a repository",
	"wizard.work.repoRecovery": "the installation needs recovery",
	"wizard.work.repoUnusable": "{label} — {reason}",
	"wizard.work.signal.drift": "files modified since install",
	"wizard.work.signal.engine": "AI-DLC {version} installed",
	"wizard.work.signal.engineUnknown": "Installed AI-DLC version unknown",
	"wizard.work.signal.gitBranch": "branch {branch}",
	"wizard.work.signal.gitClean": "clean",
	"wizard.work.signal.gitDirty_one": "{n} uncommitted file",
	"wizard.work.signal.gitDirty_other": "{n} uncommitted files",
	"wizard.work.signal.gitUnavailable": "Git observation unavailable",
	"wizard.work.signal.intents_one": "{n} intent already here",
	"wizard.work.signal.intents_other": "{n} intents already here",
	"wizard.work.signal.stages": "{n} stages in the installed graph",
	"wizard.work.signals": "What Studio observed in this repository",
	"wizard.work.signalsHelp": "Read from disk, never edited here.",
	"wizard.work.space": "Space",
	"wizard.work.spaceActive": "{space} — active",
	"wizard.work.spaceHelp": "AI-DLC creates an intent in its active space only. Other spaces are listed but cannot be chosen here.",
	"wizard.work.spaceInactive": "{space} — not active",
	"wizard.work.spaceUnknown": "Studio has not read this repository's active space yet.",
	"workspace.depth.Comprehensive": "Comprehensive",
	"workspace.depth.Minimal": "Minimal",
	"workspace.depth.Standard": "Standard",
	"workspace.refusal.autonomous": "Scope cannot change during autonomous construction.",
	"workspace.refusal.current_stage_removed": "This scope would remove the current stage. Finish that stage before changing scope.",
	"workspace.refusal.no_changes": "Choose different settings to preview a change.",
	"workspace.refusal.open_boundary": "Resolve open stage approvals or revisions before changing scope. Depth and test strategy can still be changed.",
	"workspace.refusal.unknown_stages": "The recorded stages do not match the installed engine graph.",
	"workspace.settings.after": "After",
	"workspace.settings.applied": "Settings verified on disk.",
	"workspace.settings.apply": "Apply settings",
	"workspace.settings.before": "Before",
	"workspace.settings.command": "Engine command",
	"workspace.settings.confirmBody": "This activates the selected intent, updates the harness context, and changes its settings and engine audit. If the plan has moved, refresh the preview before confirming again.",
	"workspace.settings.confirmTitle": "Apply this preview?",
	"workspace.settings.depth": "Depth",
	"workspace.settings.description": "Preview changes to this intent's workflow and effort. Completed stages keep their recorded status and artifacts.",
	"workspace.settings.execute": "Execute",
	"workspace.settings.preserved": "{n} completed stages retain their history.",
	"workspace.settings.preview": "Preview changes",
	"workspace.settings.review": "Review and confirm",
	"workspace.settings.scope": "Scope",
	"workspace.settings.selection": "This makes {space} / {intent} active so the settings and audit refer to the same intent.",
	"workspace.settings.skip": "Skip",
	"workspace.settings.stage": "Stage",
	"workspace.settings.stageChanges": "Stage selection changes",
	"workspace.settings.test_strategy": "Test strategy",
	"workspace.settings.title": "Scope and depth",
	"workspace.spaces.active": "Active space: {name}",
	"workspace.spaces.confirm": "Confirm",
	"workspace.spaces.confirmCreate": "Create {name} with fresh team memory seeded from the default space? Your active space stays the same.",
	"workspace.spaces.confirmSwitch": "Switch the repository and harness context to {name}? An active execution will block this change.",
	"workspace.spaces.confirmTitle": "Confirm space change",
	"workspace.spaces.create": "Create space",
	"workspace.spaces.created": "Space created. The active space is unchanged.",
	"workspace.spaces.description": "Spaces keep separate intents and shared memory. Switching changes the repository's active space and harness context.",
	"workspace.spaces.name": "New space name",
	"workspace.spaces.nameHint": "Use 1–48 lowercase letters, numbers and single hyphens, starting with a letter. Choose a unique name; commands such as help, list and create are reserved.",
	"workspace.spaces.refresh": "Refresh spaces",
	"workspace.spaces.switch": "Switch space",
	"workspace.spaces.switched": "Active space verified on disk.",
	"workspace.spaces.target": "Target space",
	"workspace.spaces.title": "Spaces"
}, A = {
	"a11y.queueRow": "{type}，{repo}，{intent}，{stage}，{severity}，已等待 {duration}。{primary}",
	"a11y.refreshing": "证据正在刷新",
	"a11y.skipToDetail": "跳到决策区",
	"action.budget_stop.headline": "你设置的预算在下一轮开始前停止了该意图。",
	"action.circuit_breaker.headline": "在连续 {count} 次同类失败后，Studio 已停止向该意图派发工作。",
	"action.delivery_uncertain.consequence.no_replay": "Studio 不会自行重发。它正在观察 AI-DLC 自己的文件以确认结果，重发前一定会先征求你的同意。",
	"action.delivery_uncertain.headline": "Studio 无法确定你上一个决定是否已送达会话。",
	"action.failure.consequence.no_dispatch": "在你重试或保持暂停之前，不会再向该意图派发工作。",
	"action.failure.headline": "AI-DLC 引擎在处理 {stage} 时失败。",
	"action.force_stop.headline": "强制停止正在运行的 {stage} 这一轮。",
	"action.gate.consequence.approve_unlocks": "批准后 AI-DLC 会关闭 {stage} 并开始下一个阶段。请求修改会把你的说明送回并重新打开关口。",
	"action.gate.consequence.final_stage": "批准后将关闭本计划的最后一个阶段 {stage} 并完成整个流程。",
	"action.gate.headline": "AI-DLC 已完成 {stage}，在继续之前等待你的批准。",
	"action.install_conflict.consequence.nothing_written": "尚未向你的仓库写入任何内容；在此问题解决前，AI-DLC 不会在这里运行。",
	"action.install_conflict.headline": "{engine_dir} 中由 Studio 管理的文件与其安装的内容不一致。",
	"action.missing_input.headline": "AI-DLC 需要你提供信息才能开始：{reason}。",
	"action.prepare_commit.headline": "请会话为当前改动准备一次提交。",
	"action.question.consequence.advances_turn": "你的回答会作为一条消息发送，会话将据此继续。",
	"action.question.consequence.plan_approval": "你的回复会批准已记录的计划或要求修改，然后再继续实施。",
	"action.question.consequence.summary_confirmation": "你的回复会确认已记录的汇总或要求修改，然后再继续此阶段。",
	"action.question.headline": "{stage} 需要你回答其待解问题后才能继续。仍有 {pending} 个待回答。",
	"action.question.plan_approval.headline": "{stage} 正在等待你批准计划。",
	"action.question.summary_confirmation.headline": "{stage} 正在等待你确认汇总。",
	"action.reason.dangling_cursor": "活动意图游标指向的记录没有状态文件",
	"action.reason.missing_scope": "尚未选择范围",
	"action.reason.needs_input": "该阶段正在等待输入",
	"action.reason.questions": "有未回答的问题",
	"action.reason.recovery_details": "请检查下方记录的证据",
	"action.reason.recovery_required": "需要恢复安装",
	"action.reason.session_lost_mid_stage": "绑定的会话已不存在",
	"action.recovery.consequence.blocked_until_agree": "确认仅关闭这条通知。请先修复所报告的原因，再继续任务。",
	"action.recovery.headline": "该任务需要恢复处理：{reason}。",
	"action.resume.headline": "从搁置处继续该意图。",
	"action.revision.headline": "{stage} 正在根据你的反馈修订。第 {revision_count} 次修订。",
	"action.run.consequence.one_turn": "这只会运行一轮，在下一个关口、提问、失败或完成处停止 —— Studio 不会无人值守地继续。",
	"action.run.headline": "运行 {stage}，直到下一个需要你处理的节点。",
	"activity.action.cancelled": "在发送任何内容之前已取消操作。",
	"activity.action.created": "已创建操作并排队等待你的决定。",
	"activity.action.delivery": "已记录投递结果。",
	"activity.action.failed": "派发失败。",
	"activity.action.resolved": "已依据观察到的证据结清操作。",
	"activity.action.retried": "已请求重试。",
	"activity.action.submitted": "决定已提交至规范会话。",
	"activity.action.updated": "操作已更新。",
	"activity.advisor.auto_requested": "已自动请求顾问草稿，因为该仓库设置了提前起草。它只读取，无法推动工作流。",
	"activity.advisor.completed": "顾问草稿已就绪。它是草稿，不是决定。",
	"activity.advisor.failed": "顾问草稿失败。",
	"activity.advisor.requested": "已请求顾问草稿。它只读取，无法推动工作流。",
	"activity.breaker.opened": "在重复出现同类失败后熔断器已打开。在重置之前不再派发。",
	"activity.breaker.reset": "熔断器已重置。",
	"activity.calibration.cleared": "已清除估算校准历史。",
	"activity.engine.run": "已运行 AI-DLC 引擎命令。",
	"activity.evidence.action": "操作",
	"activity.evidence.at": "记录时间",
	"activity.evidence.auditEvent": "审计事件",
	"activity.evidence.auditShard": "审计分片",
	"activity.evidence.commit": "提交",
	"activity.evidence.copy": "复制原始块",
	"activity.evidence.fields": "记录的值",
	"activity.evidence.fieldsNone": "该事件没有记录任何值。",
	"activity.evidence.kind": "类别",
	"activity.evidence.location": "文件位置",
	"activity.evidence.locationNone": "该事件没有记录文件位置。",
	"activity.evidence.messageKey": "消息键",
	"activity.evidence.provenance": "来源",
	"activity.evidence.rawNone.aidlc": "这一行没有随附原始块。原始块只随合并后的 intent 时间线返回——请在范围栏中选定单一仓库与单一 intent。",
	"activity.evidence.rawNone.studio": "Studio 的记录不含审计块，因为 Studio 不写审计块。Studio 记录的内容原样列在下方。",
	"activity.evidence.rawTitle": "原始审计块",
	"activity.evidence.redaction": "所有值在离开后端前都会移除凭据与令牌，注册仓库之外的路径不会显示。",
	"activity.evidence.session": "会话",
	"activity.evidence.severity": "严重程度",
	"activity.evidence.source.aidlc": "读取自下方列出的 AI-DLC 审计分片。",
	"activity.evidence.source.git": "通过读取 Git 观察到。Studio 从不执行 Git 写操作。",
	"activity.evidence.source.kirocrew": "在下方列出的 KiroCrew 会话中观察到。",
	"activity.evidence.source.slack": "来自 Slack 关联回调的记录。它不授权任何操作。",
	"activity.evidence.source.studio": "由 Studio 记录。Studio 从不写入 AI-DLC 审计轨迹。",
	"activity.evidence.title": "证据抽屉",
	"activity.export.discard": "丢弃",
	"activity.export.download": "下载 {filename}",
	"activity.export.failed": "无法生成导出：{message}",
	"activity.export.humanText": "包含提示正文与输入的反馈",
	"activity.export.humanTextBlocked": "请先在“设置”中允许。该设置关闭时，无论导出如何请求，后端都会拒绝包含人类文本。",
	"activity.export.humanTextWarn": "开启后，导出将包含你发送进会话的原文。请谨慎分享。",
	"activity.export.includes.actions": "在途操作、租约、近期事务与熔断器。",
	"activity.export.includes.activity": "近期活动记录。",
	"activity.export.includes.health": "健康状况、版本与载荷完整性。",
	"activity.export.includes.repos": "已注册仓库及其安装状态。",
	"activity.export.includes.settings": "Studio 设置。",
	"activity.export.includesTitle": "导出包含的内容",
	"activity.export.lede": "在你要求之前不会生成任何内容。请先阅读会被移除的内容。",
	"activity.export.produce": "生成导出",
	"activity.export.producing": "正在生成…",
	"activity.export.ready": "导出已就绪 —— {size}，生成于 {when}。",
	"activity.export.redacts.artifacts": "从不包含产物内容，仅包含其元数据。",
	"activity.export.redacts.credentials": "所有字符串中的凭据、令牌与密钥都会被移除。",
	"activity.export.redacts.humanText": "提示正文与你输入的反馈会被省略。",
	"activity.export.redacts.paths": "路径会被清洗到已注册仓库的根目录范围内。",
	"activity.export.redacts.unrelated": "从不读取你未注册的仓库中的内容。",
	"activity.export.redactsTitle": "导出会移除的内容",
	"activity.export.region": "诊断导出",
	"activity.export.title": "诊断导出",
	"activity.filter.allSources": "全部来源",
	"activity.filter.anyKind": "任意事件",
	"activity.filter.anySeverity": "任意严重程度",
	"activity.filter.anyStage": "任意阶段",
	"activity.filter.anyType": "任意操作类型",
	"activity.filter.applied_one": "已应用 {n} 个筛选",
	"activity.filter.applied_other": "已应用 {n} 个筛选",
	"activity.filter.clear": "清除筛选",
	"activity.filter.kind": "事件",
	"activity.filter.severity": "严重程度",
	"activity.filter.since": "起",
	"activity.filter.source": "来源",
	"activity.filter.stage": "阶段",
	"activity.filter.title": "筛选",
	"activity.filter.type": "操作类型",
	"activity.filter.typeUnavailable": "操作类型筛选依赖 Studio 的操作表，而合并后的 intent 时间线并未连接该表。",
	"activity.filter.until": "止",
	"activity.health.startup": "Studio 已启动。",
	"activity.install.transaction": "已记录安装事务。",
	"activity.intent.archived": "intent 已归档。",
	"activity.intent.created": "已创建 intent。",
	"activity.intent.force_stop": "已对绑定会话请求强制停止。",
	"activity.intent.paused": "intent 已暂停。暂停期间不会派发任何内容。",
	"activity.intent.restored": "intent 已从归档恢复。",
	"activity.intent.resumed": "intent 已恢复。",
	"activity.lease.acquired": "已获取仓库执行租约。",
	"activity.lease.reclaimed": "已回收孤立租约。",
	"activity.lease.released": "已释放仓库执行租约。",
	"activity.machine_lane.refused": "已拒绝无人值守派发。Studio 绝不伪造人类在场。",
	"activity.migration.applied": "已应用原型迁移。",
	"activity.migration.previewed": "已预览原型迁移。未写入任何内容。",
	"activity.notification.sent": "已发送通知。",
	"activity.page.count_one": "{n} 条事件",
	"activity.page.count_other": "{n} 条事件",
	"activity.page.empty.body": "放宽时间范围，或选择“全部来源”。活动只记录已经发生的事，从不做预测。",
	"activity.page.empty.title": "没有事件符合当前筛选",
	"activity.page.emptyScope.body": "先注册一个仓库并打开一个 intent。Studio 的每一次操作都会连同来源写在这里。",
	"activity.page.emptyScope.title": "尚无记录",
	"activity.page.error": "无法读取活动：{message}",
	"activity.page.lede": "可读的时间线。每一条事件都标明来源，Studio 自己派生的事件绝不会被当作 AI-DLC 审计事件展示。",
	"activity.page.mergedNote": "当前范围是单一仓库与单一 intent，因此该 intent 的 AI-DLC 审计事件会实时投影在 Studio 自身记录旁边。来源筛选由服务端执行，其余筛选在浏览器内对已读取的 {n} 条事件执行。",
	"activity.page.narrowed": "已读取 {loaded} 条，显示 {shown} 条",
	"activity.page.newer": "更近",
	"activity.page.older": "更早",
	"activity.page.pageNumber": "第 {n} 页",
	"activity.page.provenance": "Studio 派生的事件标注为 Studio，绝不会被呈现为原始的 AI-DLC 审计事件。",
	"activity.page.reading": "正在读取活动…",
	"activity.page.redaction": "证据抽屉会展示原始审计块与文件位置。诊断导出默认会脱敏凭据、受保护路径与提示正文。",
	"activity.page.retry": "重新读取",
	"activity.page.sourceNeedsScope": "AI-DLC 审计事件来自某个 intent 的审计分片。请在范围栏中选定仓库与 intent。",
	"activity.page.studioOnlyNote": "此处仅为 Studio 自身的记录。在范围栏中选定一个仓库和一个 intent，即可同时读取该 intent 的 AI-DLC 审计事件。",
	"activity.page.timeline": "时间线",
	"activity.page.title": "活动",
	"activity.repo.added": "已注册仓库。",
	"activity.repo.rebound": "仓库移动后已重新绑定路径。",
	"activity.repo.removed": "已取消注册仓库。磁盘上的字节没有变化。",
	"activity.repo.rescanned": "已重新扫描仓库。",
	"activity.repo.updated": "已更新仓库标签或归档状态。",
	"activity.row.at": "记录于 {when}",
	"activity.row.auditEvent": "AI-DLC 审计事件",
	"activity.row.commit": "提交 {sha}",
	"activity.row.derived": "Studio 派生",
	"activity.row.evidence": "证据",
	"activity.row.evidenceFor": "查看 {time} 的 {source} 事件证据",
	"activity.row.mismatch": "这一行的来源与其消息不一致，Studio 不会为它命名。原始记录在证据抽屉中。",
	"activity.row.openAction": "打开操作",
	"activity.row.openActionFor": "在操作中心打开操作 {action}",
	"activity.row.session": "会话 {session}",
	"activity.row.shard": "{shard} #{pos}",
	"activity.row.shardNoPos": "{shard}",
	"activity.row.stage": "阶段 {stage}",
	"activity.row.unknownKind": "当前版本的 Studio 不认识这个事件。",
	"activity.session.bound": "intent 已绑定到规范会话。",
	"activity.session.takeover": "已接管规范会话。",
	"activity.session.unbound": "intent 已从规范会话解绑。",
	"activity.settings.updated": "设置已更新。",
	"activity.slack.callback": "已记录 Slack 关联。它不授权任何操作。",
	"activity.slack.sent": "已发送 Slack 消息。",
	"advisor.action.diagnose": "用 AI 诊断",
	"advisor.action.gate_analysis": "分析这项决策",
	"advisor.action.question_draft": "起草全部答案",
	"advisor.action.question_draft_one": "起草本题",
	"advisor.action.question_explain": "解释",
	"advisor.action.request_changes_draft": "起草修改要求说明",
	"advisor.alternatives": "备选方案",
	"advisor.applyPicks": "套用草稿中的选择",
	"advisor.applyPicksNote": "不会提交任何内容。「批准」永远不会被预先选中。",
	"advisor.assumptions": "假设",
	"advisor.confidence": "置信度",
	"advisor.confidenceValue.high": "高",
	"advisor.confidenceValue.low": "低",
	"advisor.confidenceValue.medium": "中",
	"advisor.disabled": "Advisor 在设置中已关闭，因此这里无法生成任何草稿。",
	"advisor.disclaimer": "Advisor 的活动只记录在 Studio 的活动记录中。它绝不会写入 AI-DLC 审计轨迹，也无法产生 HUMAN_TURN。",
	"advisor.draftNotDecision": "草稿，不是决策",
	"advisor.drawer.alternatives": "备选",
	"advisor.drawer.applyAnswers": "应用草拟的选项",
	"advisor.drawer.applyAnswersHint": "不会提交任何内容。绝不会预先选中“批准”。",
	"advisor.drawer.assumptions": "假设",
	"advisor.drawer.badge": "草稿，不是决定",
	"advisor.drawer.close": "关闭顾问抽屉",
	"advisor.drawer.confidence": "置信度",
	"advisor.drawer.confidence.high": "高",
	"advisor.drawer.confidence.low": "低",
	"advisor.drawer.confidence.medium": "中",
	"advisor.drawer.disclaimer": "顾问活动仅记录在 Studio 活动中。它绝不写入 AI-DLC 审计轨迹，也无法产生 HUMAN_TURN。",
	"advisor.drawer.empty": "顾问没有返回草稿。",
	"advisor.drawer.error": "无法读取草稿：{message}",
	"advisor.drawer.evidence": "证据",
	"advisor.drawer.expires": "该草稿将于 {when} 过期。",
	"advisor.drawer.kind.diagnose": "诊断",
	"advisor.drawer.kind.gate_analysis": "关卡分析",
	"advisor.drawer.kind.plan_draft": "计划建议",
	"advisor.drawer.kind.question_draft": "答案草稿",
	"advisor.drawer.kind.question_explain": "问题解释",
	"advisor.drawer.kind.request_changes_draft": "变更请求草稿",
	"advisor.drawer.needsYourDecision": "需要你来决定",
	"advisor.drawer.needsYourDecisionChip": "无法由证据得出",
	"advisor.drawer.status.expired": "该草稿已过期。可重新请求。",
	"advisor.drawer.status.failed": "顾问未能完成：{message}",
	"advisor.drawer.status.queued": "已排队。顾问在自己的只读会话中运行。",
	"advisor.drawer.status.ready": "已就绪",
	"advisor.drawer.status.running": "正在运行…",
	"advisor.drawer.suggested": "草拟的答案",
	"advisor.drawer.suggestedQuestion": "第 {index} 题",
	"advisor.drawer.summary": "解读",
	"advisor.drawer.title": "AI 顾问 —— 仅为草稿",
	"advisor.drawer.useFeedback": "把草稿反馈放进输入框",
	"advisor.drawer.useFeedbackHint": "在发送之前你可以随意修改。",
	"advisor.error.agent_error": "Advisor 会话返回了错误。",
	"advisor.error.bad_result": "Advisor 的答复不符合预期结构，因此被丢弃而不是展示出来。",
	"advisor.error.evidence_mutated": "Advisor 读取期间证据发生了变化，因此草稿被丢弃。",
	"advisor.error.spawn_failed": "无法启动 Advisor 会话。",
	"advisor.error.timed_out": "Advisor 未能及时给出答复。",
	"advisor.error.unknown": "Advisor 未能生成草稿。",
	"advisor.evidence": "证据",
	"advisor.evidenceMoved": "Advisor 读取期间证据发生了变化，这份草稿描述的情况已不存在。请重新询问。",
	"advisor.expired": "这份草稿已过期。重新询问可获得新的草稿。",
	"advisor.kind.diagnose": "诊断",
	"advisor.kind.gate_analysis": "关卡分析",
	"advisor.kind.plan_draft": "计划建议",
	"advisor.kind.question_draft": "答案草稿",
	"advisor.kind.question_explain": "解释",
	"advisor.kind.request_changes_draft": "修改说明草稿",
	"advisor.needsYourDecision": "需要你来决定",
	"advisor.notRun": "Advisor 尚未运行",
	"advisor.notRunCopy": "Advisor 只在被要求时才运行——你在这里要求，或者该仓库已在设置里获得“提前起草”授权，每张卡片自动请求一次。它使用独立的只读会话，不能写文件、不能推动工作流，也绝不会产生人类发言证据。",
	"advisor.prefilled": "这些答案来自 Advisor 的草稿，已为你预先填好。尚未提交任何内容——提交前请逐项核对，可以随意修改。",
	"advisor.prefilledClear": "清除这些答案",
	"advisor.running": "正在读取证据以生成{kind}…",
	"advisor.title": "AI Advisor",
	"advisor.titleDraft": "AI Advisor — 仅为草稿",
	"advisor.unresolvable": "无法从证据中得出结论",
	"advisor.useFeedback": "把草稿填入说明框",
	"advisor.useFeedbackNote": "在发送之前你可以随意修改。",
	"advisor.verdict.approve_recommended": "批准与现有证据一致",
	"advisor.verdict.needs_your_decision": "这需要你来决定",
	"advisor.verdict.none": "没有结论",
	"advisor.verdict.request_changes_recommended": "要求修改与现有证据一致",
	"artifact.binary.body": "这些字节不是 UTF-8 文本，Studio 无法在不猜测编码的前提下展示内容。文件路径见下方。",
	"artifact.binary.title": "该文件不是文本",
	"artifact.blank": "该文件为空。",
	"artifact.block.files": "本记录中的文件",
	"artifact.block.produced": "产出的工件",
	"artifact.diff.added": "新增",
	"artifact.diff.fromGit": "上一版本来自 Git",
	"artifact.diff.fromUnknown": "上一版本来自已保留的观测",
	"artifact.diff.label": "{name} 相对已提交版本的变更",
	"artifact.diff.removed": "删除",
	"artifact.diff.showRest": "显示其余 {n} 行",
	"artifact.diff.title": "相对已提交版本的变更",
	"artifact.diff.unavailable": "Git 无法生成对比。上方的工件仍然是磁盘上的当前文件。",
	"artifact.diff.unchanged": "与已提交版本相比没有变化。",
	"artifact.empty": "Studio 尚未读取该文件。",
	"artifact.empty.body": "阶段写出工件后，它们会出现在这里。Studio 只从磁盘读取，从不创建工件。",
	"artifact.empty.title": "该记录尚未记录任何工件",
	"artifact.kind.artifact": "工件",
	"artifact.kind.contribution": "贡献记录",
	"artifact.kind.memory": "记忆",
	"artifact.kind.other": "文件",
	"artifact.kind.questions": "问题清单",
	"artifact.kind.review": "评审",
	"artifact.kind.traceability": "可追溯性",
	"artifact.list.filter": "筛选文件",
	"artifact.list.missing": "该链接指向的文件不在本记录中。这里显示的是第一个工件。",
	"artifact.list.noMatch": "没有文件匹配 {query}。",
	"artifact.list.noStage": "未关联到任何阶段",
	"artifact.list.notRendered": "未渲染",
	"artifact.list.title": "{n} 个文件",
	"artifact.list.truncated": "Studio 在达到上限后停止列举文件。已显示的文件都是真实的，但列表并不完整。",
	"artifact.meta.sha256": "SHA-256",
	"artifact.meta.size": "大小",
	"artifact.meta.stage": "阶段",
	"artifact.meta.unit": "单元",
	"artifact.meta.updated": "更新时间",
	"artifact.notRendered.body": "Studio 只在后端报告的大小与文件类型范围内以只读方式渲染工件，而该文件（{size}，{kind}）超出了这个范围。Studio 没有做截断读取，因为半个工件看起来和完整工件一样。",
	"artifact.notRendered.title": "Studio 没有渲染该文件",
	"artifact.openInEditor": "在编辑器中打开",
	"artifact.pane.label": "工件 {name}，只读",
	"artifact.readOnly": "只读",
	"artifact.toc.label": "该工件的章节",
	"artifact.tooLarge.body": "该文件为 {size}，超过本次读取允许的 {cap}。Studio 拒绝做部分读取，而不是把工件的一部分当成全部展示。",
	"artifact.tooLarge.title": "该文件过大，无法渲染",
	"artifact.truncated": "Studio 只读取了该文件的一部分。其余内容仍在磁盘上，没有传到浏览器。",
	"audit.ARTIFACT_CREATED": "阶段智能体创建了产物。",
	"audit.ARTIFACT_UPDATED": "阶段智能体更新了产物。",
	"audit.DECISION_RECORDED": "记录了一项决定。",
	"audit.DEPTH_CHANGED": "工作流深度已变更。",
	"audit.DOCUMENT_INDEXED": "一篇客户文档已建入知识库索引。",
	"audit.DOCUMENT_REMOVED": "已索引文档的原件已不存在，其记录成为墓碑。",
	"audit.DOCUMENT_UPDATED": "已索引文档的记录发生了变化。",
	"audit.ERROR_LOGGED": "记录了一条错误。",
	"audit.GATE_APPROVED": "关卡已批准。",
	"audit.GATE_REJECTED": "在关卡处请求了变更。",
	"audit.GUARDRAIL_LOADED": "已加载护栏。",
	"audit.HEALTH_CHECKED": "已检查工作区健康状况。",
	"audit.HUMAN_TURN": "记录了一次人类回合。只有真实的用户提示才会产生它。",
	"audit.MEMORY_EMPTY": "未找到先前的记忆。",
	"audit.PHASE_COMPLETED": "阶段群已完成。",
	"audit.PHASE_SKIPPED": "阶段群已跳过。",
	"audit.PHASE_STARTED": "阶段群已开始。",
	"audit.PHASE_VERIFIED": "阶段群已验证。",
	"audit.PIPELINE_LINK_COMPLETED": "一个声明过的流水线环节已按序完成。",
	"audit.PLAN_APPROVAL_RECORDED": "已记录代码生成阶段的计划批准。",
	"audit.QUESTION_ANSWERED": "问题已回答。",
	"audit.RECOMPOSED": "阶段计划已重新编排。",
	"audit.REVIEW_COMPLETED": "评审已完成。",
	"audit.REVIEW_REQUESTED": "已请求评审。",
	"audit.RULE_LEARNED": "已将一条规则写入记忆。",
	"audit.SCOPE_CHANGED": "范围已变更。",
	"audit.SENSOR_FAILED": "一个传感器失败。",
	"audit.SENSOR_FIRED": "一个传感器已触发。",
	"audit.SENSOR_PASSED": "一个传感器已通过。",
	"audit.SESSION_COMPACTED": "会话记录已压缩。",
	"audit.SESSION_ENDED": "会话已结束。",
	"audit.SESSION_RESUMED": "会话已恢复。",
	"audit.SESSION_STARTED": "会话已开始。",
	"audit.STAGE_AWAITING_APPROVAL": "阶段已到达关卡，正在等待人的决定。",
	"audit.STAGE_COMPLETED": "阶段已完成。",
	"audit.STAGE_JUMPED": "游标跳转到了另一个阶段。",
	"audit.STAGE_REVISING": "在请求变更后，阶段正在修订。",
	"audit.STAGE_SKIPPED": "阶段已跳过。",
	"audit.STAGE_STARTED": "阶段已开始。",
	"audit.SUBAGENT_COMPLETED": "一个子智能体已完成。",
	"audit.SUMMARY_CONFIRMATION_RECORDED": "已记录摘要确认。",
	"audit.SWARM_SOURCE_MERGED": "一个 Swarm 单元评审过的源码已合入主干。",
	"audit.TEST_STRATEGY_CHANGED": "测试策略已变更。",
	"audit.UNIT_GATE_RHYTHM_SET": "已设置团队的单元关卡节奏。",
	"audit.UNIT_MERGED": "一个单元锁定的内容已合入主干，其行已折叠。",
	"audit.UNIT_OWNERSHIP_SET": "已设置单元归属模式。",
	"audit.WORKFLOW_COMPLETED": "工作流已完成。",
	"audit.WORKFLOW_PARKED": "工作流已挂起。",
	"audit.WORKFLOW_STARTED": "工作流已开始。",
	"audit.WORKFLOW_UNPARKED": "工作流已解除挂起。",
	"audit.WORKSPACE_INITIALISED": "工作区已初始化。",
	"audit.WORKSPACE_SCAFFOLDED": "工作区脚手架已生成。",
	"audit.WORKSPACE_SCANNED": "工作区已扫描。",
	"audit.WORKTREE_CREATED": "已创建一个 Git worktree。",
	"audit.unknown_event": "当前版本的 Studio 不认识这个审计事件。原始块在证据抽屉中。",
	"common.cancel": "取消",
	"common.close": "关闭",
	"common.copied": "已复制",
	"common.copy": "复制",
	"common.durationDays": "{n} 天",
	"common.durationHours": "{n} 小时",
	"common.durationMinutes": "{n} 分钟",
	"common.estimate": "估算",
	"common.exact": "精确",
	"common.justNow": "刚刚",
	"common.loading": "加载中…",
	"common.none": "无",
	"common.retry": "重试",
	"common.unavailable": "不可用",
	"confirm.acknowledge.mark_not_delivered": "我已阅读上方的投递证据，并记录该消息从未送达。",
	"confirm.acknowledge.resubmit": "我已阅读上方的投递证据，并要求 Studio 再次发送这个决定。",
	"confirm.apply": "确认，执行此操作",
	"confirm.atMostOnce": "至多一次：越过这一步之后，Studio 只会依据磁盘对账，而不会重发。",
	"confirm.blocked.acknowledge": "请确认你已阅读上方的投递证据。",
	"confirm.blocked.answersIncomplete": "发送前必须回答该组中的每一个问题。",
	"confirm.blocked.evidenceMissing": "Studio 还没有这张卡片的证据摘要，因此不会请求服务端再次发送。",
	"confirm.blocked.feedbackRequired": "要求修改时必须写明需要改什么。",
	"confirm.blocked.groupedAnswers": "把多个答案合并在一条消息里尚未验证。请逐个回答，或改用会话。",
	"confirm.blocked.inputRequired": "请填写该阶段缺少的输入。",
	"confirm.blocked.noWireText": "目前还没有可发送的内容。",
	"confirm.blocked.questionsUnavailable": "请在原会话中回答此题组。",
	"confirm.blocked.refreshing": "作为证据的文件正在变化，因此现在不能基于它提交任何内容。",
	"confirm.blocked.sessionUnbound": "决定会作为真实的用户回合注入到该意图的规范会话，而该意图尚未绑定任何会话。请在 KiroCrew 中为该仓库打开一个使用 aidlc agent 的会话，并把它绑定到该意图；在此之前 Studio 没有可以注入的目标。",
	"confirm.consequence.acknowledge": "Studio 会在自己的记录中关闭该事件。AI-DLC 的文件不会被改动；如果证据仍然矛盾，条目会再次出现。",
	"confirm.consequence.force_stop": "将请求 KiroCrew 停止该会话中正在运行的回合。不会写入 AI-DLC 的任何文件，也不会向会话追加消息。",
	"confirm.consequence.keep_paused": "该意图保持暂停。在你取消暂停之前，不会为它派发任何工作。",
	"confirm.consequence.mark_not_delivered": "Studio 会记录会话从未收到该消息。它会先复核自己的证明，若无法证明则拒绝。",
	"confirm.consequence.pick_intent": "Studio 会请求 AI-DLC 引擎把它设为活动意图。这是一条命令而不是提示，不会产生 human-turn 证据。",
	"confirm.consequence.rebind_session": "Studio 会把该意图绑定到一个新的规范会话，并从最后一个稳定边界重新校验。不发送任何消息，也不写入任何 AI-DLC 文件。",
	"confirm.consequence.reconcile": "Studio 会立即重读 AI-DLC 文件与会话，而不是等到下一轮扫描。不发送任何内容。",
	"confirm.consequence.resubmit": "Studio 会把这个决定作为新条目重新排队。如果它能证明第一次已经投递，则会拒绝。",
	"confirm.consequence.retry_now": "Studio 会清除该意图的重复失败计数，允许下一次派发。已经发送过的内容不会重发。",
	"confirm.consequence.run_now": "Studio 会为该意图排入一次运行。在发送任何内容之前，你需要确认原文指令。",
	"confirm.hostControl": "{label} 会请求 KiroCrew 停止正在运行的回合，不会向会话追加任何消息。",
	"confirm.hostControlHint": "至多一次：Studio 会在发出停止请求之前先记录它，之后只做对账而不会重复请求。",
	"confirm.label": "确认",
	"confirm.labelSends": "按钮：{label} → 发送：{wire}",
	"confirm.mismatch": "KiroCrew 记录的文本与本页显示的不一致。下面是实际发送的内容。",
	"confirm.noWireText": "暂无内容",
	"confirm.routing": "将作为真实的用户回合注入到 {repo}/{intent} 的规范会话 {session}。Kiro 的 userPromptSubmit 钩子会生成受保护的 HUMAN_TURN；由 AI-DLC 引擎提交状态迁移。Studio 不会调用 report、不会修改 aidlc-state.md，也不会设置任何绕过开关。",
	"confirm.send": "确认，发送这段原文",
	"confirm.studioOnly": "{label} 只会改变 Studio 自己的记录，不会向会话发送任何内容。",
	"confirm.studioOnlyHint": "这不会发送消息。Studio 会记录你的选择，并继续读取磁盘上的文件。",
	"confirm.title.accept_as_is": "确认按现状接受该阶段",
	"confirm.title.acknowledge": "确认你已阅读该事件",
	"confirm.title.answers": "确认作为一次操作发送的答案组",
	"confirm.title.approve": "确认发送到规范会话的原文",
	"confirm.title.approve_plan": "确认发送到规范会话的计划批准",
	"confirm.title.confirm_summary": "确认你对摘要检查点的回复",
	"confirm.title.force_stop": "确认停止正在运行的回合",
	"confirm.title.keep_paused": "确认让该意图保持暂停",
	"confirm.title.mark_not_delivered": "确认把这个决定记录为从未发送",
	"confirm.title.pick_intent": "确认切换 AI-DLC 的活动意图",
	"confirm.title.prepare_commit": "确认发送到规范会话的提交请求",
	"confirm.title.provide_input": "确认发送到规范会话的补充输入",
	"confirm.title.rebind_session": "确认把该意图重新绑定到另一个会话",
	"confirm.title.reconcile": "确认立即重读证据",
	"confirm.title.request_changes": "确认发送到规范会话的修改要求",
	"confirm.title.request_plan_changes": "确认发送到规范会话的计划修改要求",
	"confirm.title.resubmit": "确认再次发送这个决定",
	"confirm.title.resume": "确认发送到规范会话的继续指令",
	"confirm.title.retry_now": "确认立即重试",
	"confirm.title.run": "确认发送到规范会话的运行指令",
	"confirm.title.run_now": "确认立即运行该意图",
	"decision.a11y.bar": "决策操作",
	"decision.accept_as_is.hint": "在发现尚未解决的情况下接受产出物。只有引擎主动给出该选项时才会出现。",
	"decision.accept_as_is.label": "按现状接受",
	"decision.acknowledge.hint": "记录你已阅读该矛盾，并将其搁置以便人工处理。",
	"decision.acknowledge.label": "确认已知悉",
	"decision.answers.hint": "把所有待答问题作为一次操作发送。",
	"decision.answers.label": "发送答案",
	"decision.approve.hint": "把你的批准发送到规范会话，引擎据此通过这道关卡。",
	"decision.approve.label": "批准",
	"decision.approve_plan.hint": "批准引擎编排出的计划，允许它执行。",
	"decision.approve_plan.label": "批准计划",
	"decision.bar.advisorDiagnose": "用 AI 诊断",
	"decision.bar.advisorGate": "咨询 Advisor",
	"decision.bar.advisorQuestions": "起草全部答案",
	"decision.bar.showQueue": "回到队列",
	"decision.bar.submitted": "已作为 {id} 提交。{status} — 只有 AI-DLC 状态发生变化后，该事项才会消失。",
	"decision.blocked.acknowledge": "请先确认你已阅读送达证据。",
	"decision.blocked.answers_one": "还有 {n} 道必答题没有作答。",
	"decision.blocked.answers_other": "还有 {n} 道必答题没有作答。",
	"decision.blocked.choice": "请在上面选择一项。Studio 不会替你选择。",
	"decision.blocked.feedback": "要求修改需要写明需要改什么。",
	"decision.blocked.grouped": "一条消息里发送 {n} 个答案尚未验证，请改在会话中作答。",
	"decision.blocked.input": "请先输入要发送的内容。Studio 不做任何推断。",
	"decision.blocked.noEvidenceHash": "Studio 没有该事项的证据摘要值，无法证明你确认的是什么内容。",
	"decision.blocked.noQuestions": "该事项没有待答的问题。",
	"decision.confirm_summary.changesHint": "附上你的说明，告知引擎它的摘要不正确。",
	"decision.confirm_summary.changesLabel": "要求修改摘要",
	"decision.confirm_summary.hint": "确认引擎对该阶段的摘要。",
	"decision.confirm_summary.label": "确认摘要",
	"decision.force_stop.hint": "请求 KiroCrew 停止该会话。已经在执行的一轮可能仍会跑完。",
	"decision.force_stop.label": "强制停止",
	"decision.keep_paused.hint": "让该 intent 保持停止。在你回来处理之前不会派发任何内容。",
	"decision.keep_paused.label": "保持暂停",
	"decision.mark_not_delivered.hint": "记录该决策从未到达会话，之后你可以重新发送。",
	"decision.mark_not_delivered.label": "记录为从未发送",
	"decision.nav.conversation": "打开会话",
	"decision.nav.intents": "到「Intents」页面选择",
	"decision.nav.preview": "运行升级预览",
	"decision.nav.receipt": "打开安装凭据",
	"decision.nav.settings": "打开预算设置",
	"decision.pick_intent.hint": "通过引擎操作，从磁盘上已有的 intent 中设置 AI-DLC 的活动 intent。",
	"decision.pick_intent.label": "设为当前活动意图",
	"decision.prepare_commit.hint": "请会话准备一个提交，不会执行推送。",
	"decision.prepare_commit.label": "准备提交",
	"decision.provide_input.hint": "把你输入的内容作为你的发言发送到规范会话。",
	"decision.provide_input.label": "发送补充输入",
	"decision.rebind_session.hint": "把该 intent 绑定到新的规范会话，并从最后一个稳定边界重新校验。",
	"decision.rebind_session.label": "重新绑定会话",
	"decision.reconcile.hint": "重新读取状态、审计和会话，并按它们的内容结束该事项。",
	"decision.reconcile.label": "依据磁盘对账",
	"decision.request_changes.hint": "把你的说明发送到规范会话。Studio 不会改动产出物。",
	"decision.request_changes.label": "要求修改",
	"decision.request_plan_changes.hint": "把你对计划的说明发送到规范会话。",
	"decision.request_plan_changes.label": "要求修改计划",
	"decision.resubmit.hint": "再次发送同一决策。只有在你阅读送达证据之后才可用。",
	"decision.resubmit.label": "再次发送",
	"decision.resume.hint": "从磁盘记录的引擎中断位置继续该 intent。",
	"decision.resume.label": "继续",
	"decision.retry_now.hint": "重置该 intent 的失败计数并派发一轮。",
	"decision.retry_now.label": "立即重试",
	"decision.run.hint": "在仓库租约下派发一轮。",
	"decision.run.label": "运行到下一个检查点",
	"decision.run_now.hint": "越过限制它的预算，现在运行一轮。",
	"decision.run_now.label": "立即运行",
	"delivery.answerNeedsText": "仍需补充正文",
	"delivery.answerNotVerified": "回复未验证",
	"delivery.answerNotVerifiedBody": "回合已结束并出现新的 Gate，但审计回执与发送的回复不一致。空闲租约已释放。请核对会话和新的 Gate；此前的回复不会被重新发送。",
	"delivery.confirmed": "消息已送达，工作流仍需核对",
	"delivery.confirmedBody": "会话已收到这条消息。AI-DLC 是否接受了回答、工作流状态是否变化，仍未得到验证。这条消息不会被重新发送。",
	"delivery.fact.absent": "未找到",
	"delivery.fact.bootUnchanged": "全程为同一个 Studio 进程",
	"delivery.fact.confirmed": "事后确认已投递",
	"delivery.fact.diskUnchanged": "磁盘基线未变化",
	"delivery.fact.no": "否",
	"delivery.fact.slotRanSince": "发送后会话是否运行过",
	"delivery.fact.transcriptRow": "会话记录行",
	"delivery.fact.yes": "是",
	"delivery.label": "投递状态",
	"delivery.newGateReview": "复核新的 Gate",
	"delivery.noTransition": "未记录到工作流状态变更。",
	"delivery.previousPlanApproval": "此前审批已记录",
	"delivery.previousPlanApprovalBody": "原计划的审批已记录，此次回复已完成。计划随后被重置为待审，当前版本仍需审批；本次回收没有授予新的写权限，也不会重发此前的回复。",
	"delivery.queuedInSlot": "在会话队列中等待",
	"delivery.reportFailed": "KiroCrew 已收到该消息，但 Studio 未能记录结果。它会从磁盘对账；消息绝不会被再发一次。",
	"delivery.sentAt": "发送于 {at}",
	"delivery.sentText": "发送到规范会话的原文",
	"delivery.state.cancelled": "已取消",
	"delivery.state.done": "已完成",
	"delivery.state.failed": "在此中断",
	"delivery.state.future": "尚未开始",
	"delivery.state.now": "进行中",
	"delivery.state.unchanged": "未记录到变更",
	"delivery.step.delivered": "已发送",
	"delivery.step.processing": "智能体处理中",
	"delivery.step.queued": "已排队",
	"delivery.step.reconciliation": "核对工作流",
	"delivery.step.stateChanged": "状态已变更",
	"delivery.step.unchanged": "状态未变更",
	"delivery.uncertain": "投递结果不确定——没有重放任何内容",
	"delivery.uncertainBody": "Studio 无法证明会话是否收到了这条消息，因此它会持续观察磁盘上的 AI-DLC 文件，并且不会再次发送。请在下方选择如何处理。",
	"delivery.updatedPlanReview": "复核更新后的计划",
	"delivery.watchingDisk": "Studio 依据磁盘状态判断，而不是 HTTP 回执",
	"detail.additionalAttemptBlocked": "本次再次提交被阻止。此前已发送的内容见下方记录。",
	"detail.bar.closed": "这是一条已关闭的记录。请打开当前待办继续。",
	"detail.bar.hint": "选择一个操作后会打开确认面板，显示 Studio 将要发送的原文。",
	"detail.bar.noDecisions": "这里没有需要决定的事，可查看上方记录的进度。",
	"detail.bar.refreshing": "作为证据的文件正在变化，因此现在不能基于它提交任何内容。",
	"detail.bar.showQueue": "返回队列",
	"detail.bar.submitted": "已提交为 {id}，进度见上方。",
	"detail.breadcrumb": "这个决定所属的位置",
	"detail.closedAt": "关闭于 {at}",
	"detail.closedTitle": "{type} · {status}",
	"detail.conversation.none": "该意图还没有绑定会话，因此没有内容可显示。",
	"detail.conversation.note": "这是 KiroCrew 自己的会话记录。你在这里输入的内容属于你自己的回合，由你的会话发送——Studio 不会读取或改写它。",
	"detail.conversation.title": "规范会话",
	"detail.copied": "已复制",
	"detail.currentActions": "查看当前待办",
	"detail.deepLink": "深链",
	"detail.deepLinkTitle": "复制可重新打开该决定的链接",
	"detail.gone.body": "该操作或所属仓库已移除。旧页面无法继续发送决定，请打开当前待办。",
	"detail.gone.title": "此操作已不可用",
	"detail.history": "历史记录",
	"detail.label": "决定详情",
	"detail.noSelection.body": "在队列里选择任意一项，即可查看它的证据。",
	"detail.noSelection.title": "请选择一项",
	"detail.noTemplate": "此版本还没有 {kind} 的面板。不是对你隐藏了什么——这个面板尚未实现。",
	"detail.nothingSent": "没有发送任何内容，也没有记录任何决定。",
	"detail.refreshing": "证据正在重读",
	"detail.repoBusy.openOwner": "查看占用此仓库的操作",
	"detail.repoUnavailable.body": "此卡片来自已保存的记录。请恢复目录访问，或将仓库重新关联到新路径后再继续。若它是已结束的临时测试，可在仓库页面归档。",
	"detail.repoUnavailable.footer": "恢复仓库后才能继续此操作。",
	"detail.repoUnavailable.manage": "管理仓库",
	"detail.repoUnavailable.pathMissing": "此路径下的仓库目录已不存在。",
	"detail.repoUnavailable.recordedAt": "最近记录于 {at}",
	"detail.repoUnavailable.saved": "已保存的操作记录",
	"detail.repoUnavailable.title": "仓库不可用",
	"detail.repoUnavailable.unreadable": "Studio 目前无法读取此仓库。",
	"detail.reviewClass": "{name} 评审",
	"detail.revision": "第 {n} 次修订",
	"detail.staleRefused": "上方是刷新后的证据。请阅读后重新确认；你先前的回答没有被转发。",
	"detail.tab.activity": "动态",
	"detail.tab.artifacts": "产物",
	"detail.tab.conversation": "会话",
	"detail.tab.decision": "决定",
	"detail.tab.review": "评审",
	"detail.tabsLabel": "决定详情分区",
	"detail.transitions.empty": "Studio 还没有改动过这个条目。",
	"detail.transitions.generation": "第 {n} 代",
	"detail.transitions.initial": "已创建",
	"detail.transitions.row": "{from} → {to}",
	"detail.transitions.title": "Studio 对这个决定的记录",
	"detail.waiting": "已等待 {duration}",
	"enum.actionStatus.Cancelled": "已取消",
	"enum.actionStatus.Delivered": "已发送",
	"enum.actionStatus.Delivering": "发送中",
	"enum.actionStatus.DeliveryUncertain": "送达状态未知",
	"enum.actionStatus.Draft": "草稿",
	"enum.actionStatus.Failed": "失败",
	"enum.actionStatus.NotDelivered": "未发送",
	"enum.actionStatus.Processing": "处理中",
	"enum.actionStatus.Queued": "已排队",
	"enum.actionStatus.ReconciliationRequired": "需要核对",
	"enum.actionStatus.ResolvedNoTransition": "已答复",
	"enum.actionStatus.StateChanged": "已生效",
	"enum.actionType.budget_stop": "预算停止",
	"enum.actionType.circuit_breaker": "连续失败",
	"enum.actionType.delivery_uncertain": "送达状态未知",
	"enum.actionType.failure": "失败",
	"enum.actionType.force_stop": "强制停止",
	"enum.actionType.gate": "审批关口",
	"enum.actionType.install_conflict": "安装冲突",
	"enum.actionType.missing_input": "缺少输入",
	"enum.actionType.prepare_commit": "准备提交",
	"enum.actionType.question": "提问",
	"enum.actionType.recovery": "恢复",
	"enum.actionType.resume": "继续",
	"enum.actionType.revision": "修订中",
	"enum.actionType.run": "运行",
	"enum.availability.available": "可用",
	"enum.availability.identity_unprovable": "无法确认身份",
	"enum.availability.moved": "已移动",
	"enum.availability.permission_denied": "无访问权限",
	"enum.availability.unavailable": "不可用",
	"enum.findingSeverity.blocking": "阻塞",
	"enum.findingSeverity.info": "信息",
	"enum.findingSeverity.warn": "警告",
	"enum.installStatus.drift": "安装后被修改",
	"enum.installStatus.installed": "已安装",
	"enum.installStatus.not_installed": "未安装",
	"enum.installStatus.recovery_required": "需要恢复安装",
	"enum.intentState.Archived": "已归档",
	"enum.intentState.CircuitOpen": "连续失败已熔断",
	"enum.intentState.Completed": "已完成",
	"enum.intentState.Failed": "失败",
	"enum.intentState.Idle": "空闲",
	"enum.intentState.Interrupted": "已中断",
	"enum.intentState.Parked": "已搁置",
	"enum.intentState.Paused": "已暂停",
	"enum.intentState.Queued": "已排队",
	"enum.intentState.ReconciliationRequired": "需要核对",
	"enum.intentState.RetryEligible": "可重试",
	"enum.intentState.Running": "运行中",
	"enum.intentState.WaitingForYou": "等待你处理",
	"enum.ownership.framework": "框架文件",
	"enum.ownership.framework-mutable": "引擎会改写的框架文件",
	"enum.ownership.merge": "合并目标",
	"enum.ownership.shell": "工作区骨架",
	"enum.phase.construction": "构建",
	"enum.phase.ideation": "构想",
	"enum.phase.inception": "立项",
	"enum.phase.initialization": "初始化",
	"enum.phase.operation": "运营",
	"enum.severity.attention": "需要关注",
	"enum.severity.blocking": "阻塞",
	"enum.severity.critical": "严重",
	"enum.severity.info": "信息",
	"enum.source.aidlc": "AI-DLC",
	"enum.source.git": "Git",
	"enum.source.kirocrew": "KiroCrew",
	"enum.source.slack": "Slack",
	"enum.source.studio": "AI-DLC Studio",
	"enum.stageState.awaiting_approval": "等待你审批",
	"enum.stageState.completed": "已完成",
	"enum.stageState.in_progress": "进行中",
	"enum.stageState.not_started": "未开始",
	"enum.stageState.revising": "修订中",
	"enum.stageState.skipped": "已跳过",
	"enum.stageState.unknown": "未知",
	"errors.action_not_found": "该条目已不在队列中。",
	"errors.action_not_submittable": "该条目当前状态无法提交。",
	"errors.action_stale": "你决定期间情况已变化。请查看刷新后的证据并重新确认。",
	"errors.advisor_unavailable": "AI 顾问不可用。",
	"errors.already_installed": "此处已安装 AI-DLC。",
	"errors.answers_incomplete": "该组问题必须全部作答后才能发送。",
	"errors.app_token_forbidden": "应用令牌不能执行此变更。",
	"errors.artifact_not_found": "该产物不存在。",
	"errors.bad_body": "无法解析请求内容。",
	"errors.bad_param": "参数缺失或无效。",
	"errors.bad_path": "该路径不可用。",
	"errors.breaker_open": "该意图因连续失败已停止。修复原因后可重试。",
	"errors.bun_missing": "在 PATH 和常见安装位置中都没有找到 bun，AI-DLC 的工具无法运行。请安装 bun，或者从 PATH 中已有 bun 的 shell 启动 KiroCrew。",
	"errors.bun_missing_searched": "Studio 查找过：{locations}",
	"errors.cancel_not_safe": "此时取消无法确保安全。",
	"errors.cursor_mismatch": "AI-DLC 当前的活动意图与该决定不符。",
	"errors.delivery_ack_invalid": "该送达报告与待发送记录不匹配。",
	"errors.draft_not_found": "该草稿已过期。",
	"errors.duplicate_identity": "该目录已在另一条记录中注册。",
	"errors.engine_unavailable": "此处无法调用 AI-DLC 引擎。",
	"errors.feedback_required": "请求修改时必须说明要改什么。",
	"errors.git_missing": "git 不可用，仓库观察已关闭。",
	"errors.grouped_answers_unavailable": "一次回答多个问题的方式尚未验证，请逐个作答或打开会话。",
	"errors.host_submission_unavailable": "当前 KiroCrew 版本无法向会话发送内容。",
	"errors.host_unavailable": "KiroCrew 会话服务不可用。",
	"errors.identity_unprovable": "无法确认仓库身份，因此不能执行工作。",
	"errors.illegal_transition": "不允许该状态变更。",
	"errors.install_conflict": "受管路径上的文件与将要安装的内容不一致。",
	"errors.install_recovery_required": "该仓库需要先恢复安装才能运行 AI-DLC。",
	"errors.intent_archived": "该意图已归档。",
	"errors.intent_not_found": "磁盘上没有该意图。",
	"errors.intent_paused": "该意图已暂停。",
	"errors.internal_error": "AI-DLC Studio 内部出错。",
	"errors.internal_secret_invalid": "该请求未由本网关签名。",
	"errors.invalid_decision": "该操作不适用于此条目。",
	"errors.invalid_settings": "设置无效。",
	"errors.lease_lost": "准备决定期间仓库租约发生变化，因此未发送任何内容。",
	"errors.legacy_layout": "该仓库使用较旧的 AI-DLC 布局。",
	"errors.machine_lane_unavailable": "无人值守执行不可用：尚未验证机器通道。",
	"errors.migration_already_applied": "迁移已执行过。",
	"errors.migration_not_applicable": "没有需要迁移的内容。",
	"errors.newer_installed": "已安装的 AI-DLC 比 Studio 内置版本更新。",
	"errors.not_delivered_unproven": "Studio 无法证明消息未发送，因此不会自动重发。",
	"errors.not_installed": "该仓库未安装 AI-DLC。",
	"errors.owner_required": "只有面板所有者可以执行此操作。",
	"errors.payload_degraded": "Studio 内置的 AI-DLC 载荷校验失败。",
	"errors.plan_invalid": "该计划不满足阶段依赖。",
	"errors.rate_limited": "请求过于频繁，请稍后再试。",
	"errors.rebind_not_allowed": "旧位置仍可用，不能重新绑定。",
	"errors.receipt_not_found": "该仓库没有安装凭据。",
	"errors.recompose_not_allowed": "只能修改游标之后待执行的阶段。",
	"errors.repo_busy": "另一操作正占用该仓库，不会被打断。",
	"errors.repo_not_found": "该仓库未注册。",
	"errors.repo_unavailable": "当前无法访问该仓库。",
	"errors.retry_not_allowed": "该条目不可重试。",
	"errors.route_not_found": "未知接口。",
	"errors.run_not_applicable": "此运行请求已不适用于当前流程，请查看该任务的当前待办。",
	"errors.same_version_installed": "该仓库已是内置的 AI-DLC 版本。",
	"errors.sensitive_path": "该位置受保护，无法读取。",
	"errors.session_busy": "会话正在处理一轮对话。",
	"errors.session_unbound": "该意图尚未绑定会话。",
	"errors.slack_unavailable": "Slack 未连接。",
	"errors.slot_busy": "会话正忙。",
	"errors.slot_mismatch": "该会话属于其他仓库或代理。",
	"errors.stage_not_found": "已安装的阶段图中没有该阶段。",
	"errors.stale_generation": "该记录已被其他操作先行修改。",
	"errors.state_inconsistent": "AI-DLC 的文件互相矛盾，解决之前不会发送任何内容。",
	"errors.state_version_migration_unconfirmed": "该仓库的 AI-DLC 状态为旧版本，升级尚未被证明安全。",
	"errors.storage_error": "无法写入 Studio 自身的存储。",
	"errors.storage_unavailable": "Studio 存储未打开。",
	"errors.takeover_not_safe": "只有在稳定边界处接管才安全。",
	"errors.too_large": "文件过大，无法显示。",
	"errors.too_many_repos": "已达到仓库数量上限。",
	"errors.transaction_not_found": "没有该事务记录。",
	"errors.unauthorized": "请先登录 KiroCrew。",
	"errors.unknown_stage": "该阶段不在此意图的计划中。",
	"errors.unstable_read": "文件正在变化，不能作为证据使用。",
	"errors.unsupported_locale": "不支持该语言。",
	"estimate.a11y.estimate": "附来源与置信度的估算",
	"estimate.a11y.exact": "精确计数",
	"estimate.active": "实际执行时长",
	"estimate.confidence.low": "低置信度",
	"estimate.confidence.medium": "中等置信度",
	"estimate.coverage.item": "{slug} 已关闭，因此不会有 {artifacts}。",
	"estimate.coverage.itemNone": "{slug} 已关闭；它没有声明产物。",
	"estimate.coverage.title": "你已经放弃的覆盖范围",
	"estimate.credits": "额度消耗",
	"estimate.credits.value": "不可用",
	"estimate.credits.why": "现有 Kiro 信号无法观测 — 既不是零，也不做推断",
	"estimate.dominant.item": "{slug} — 约占估算回合数的 {pct}%",
	"estimate.dominant.locked": "AI-DLC 不允许关闭这个阶段：{reason}",
	"estimate.dominant.lost": "关掉它就放弃了 {artifacts}。",
	"estimate.dominant.lostNone": "它没有声明产物，关掉它不会放弃任何产物。",
	"estimate.dominant.none": "没有哪一个阶段主导这次估算。",
	"estimate.dominant.perUnit_one": "按 {n} 个工作单元累计",
	"estimate.dominant.perUnit_other": "按 {n} 个工作单元累计",
	"estimate.dominant.title": "估算主要由什么决定",
	"estimate.dominant.turns": "估算中的 {range} 个回合",
	"estimate.elapsed": "含你决策的总体时长",
	"estimate.elapsed.unavailable": "在不知道你的响应速度之前，Studio 不估算这一项。",
	"estimate.elapsed.why": "取决于你多快做出回应",
	"estimate.estimate.title": "估算",
	"estimate.exact.artifacts": "将产出的产物",
	"estimate.exact.artifactsAssumed_one": "按 {n} 个工作单元计算的精确值；因为尚不存在工作单元，这是 Studio 的假设",
	"estimate.exact.artifactsAssumed_other": "按 {n} 个工作单元计算的精确值；因为尚不存在工作单元，这是 Studio 的假设",
	"estimate.exact.artifactsUnits_one": "精确值；按 {n} 个工作单元计算逐单元阶段",
	"estimate.exact.artifactsUnits_other": "精确值；按 {n} 个工作单元计算逐单元阶段",
	"estimate.exact.artifactsWhy": "精确值，按已选阶段累加",
	"estimate.exact.fromGraph": "精确值，来自已安装的阶段图",
	"estimate.exact.gates": "Gate 数",
	"estimate.exact.gatesWhy": "精确值；initialization 之外的每个已选阶段都有一个 Gate",
	"estimate.exact.ofGraph": "精确值，已知 {known} 个阶段中选了 {selected} 个",
	"estimate.exact.review": "评审强度",
	"estimate.exact.reviewValue": "{none} 个 none · {advisory} 个 advisory · {adversarial} 个 adversarial",
	"estimate.exact.reviewWhy": "精确值，按当前配置",
	"estimate.exact.stages": "已选阶段",
	"estimate.exact.title": "精确计数",
	"estimate.qualifier": "{kind} · {source} · {confidence}",
	"estimate.qualifierSamples": "{kind} · {source} · {confidence} · {samples}",
	"estimate.range": "区间",
	"estimate.samples.none": "还没有本地历史",
	"estimate.samples_one": "{n} 个本地样本",
	"estimate.samples_other": "{n} 个本地样本",
	"estimate.source.assumption": "假设",
	"estimate.source.history_calibrated": "由本地历史校准",
	"estimate.source.rule_band": "基于规则的区间",
	"estimate.source.unknown": "未说明来源",
	"estimate.turns": "回合数",
	"install.action.already_absent": "已经不存在",
	"install.action.conflict": "冲突 — 该位置已存在另一个文件",
	"install.action.create": "创建",
	"install.action.engine_modified": "重写 — 该文件由引擎自己重写",
	"install.action.identical": "内容已完全一致",
	"install.action.merge_conflict": "冲突 — 无法证明受管片段仍属于 Studio",
	"install.action.merge_create": "合并 — 添加受管片段",
	"install.action.merge_identical": "合并 — 受管片段已是最新",
	"install.action.merge_update": "合并 — 只更新受管片段",
	"install.action.owned_identical": "重写 — 安装后未被改动",
	"install.action.owned_modified": "冲突 — 安装后被改动",
	"install.action.preserve": "保留",
	"install.action.recovery_conflict": "恢复需要处理",
	"install.action.remove": "删除自有文件",
	"install.action.remove_created": "移除中断事务新建的内容",
	"install.action.remove_fragment": "移除自有片段",
	"install.action.restore_backup": "恢复事务备份",
	"install.action.restore_version": "恢复上一版本",
	"install.action.retire": "退役",
	"install.action.retire_blocked": "保留 — 安装后被改动，因此不退役",
	"install.action.rollback_conflict": "无法恢复此内容",
	"install.action.shell_create": "创建工作区初始文件",
	"install.action.shell_exists": "保持原样 — 工作区文件已存在",
	"install.action.uninstall_conflict": "内容已修改或无法验证",
	"install.blocked": "目前还不能执行。",
	"install.blockers.body": "Studio 从不覆盖它无法证明属于自己的字节，v1 中任何位置都没有强制覆盖选项。请你自己把文件移开，或者保持当前安装不变 — 一个完整的旧版本胜过一个混合的新版本。",
	"install.blockers.diff": "差异",
	"install.blockers.diffTruncated": "只显示差异的前 {n} 行。",
	"install.blockers.noDiff": "该路径没有可用的文本差异。",
	"install.blockers.title": "阻止本次操作的冲突",
	"install.bytes": "将写入的字节数",
	"install.cancel": "取消",
	"install.cancel.explanation": "取消会等待当前文件操作完成，再从事务备份恢复并验证。",
	"install.cancel.finished": "安装已取消，请查看事务状态确认回滚结果。",
	"install.cancel.requested": "已请求取消，正在等待事务恢复其改动。",
	"install.cancel.transaction": "取消安装并回滚",
	"install.confirm.digestNote": "本次确认对应计划 {digest}。如果仓库在预览之后发生了变化，事务会拒绝执行并重新读取预览；不匹配时不会写入任何内容。",
	"install.confirm.install": "安装 AI-DLC {version}",
	"install.confirm.note": "Studio 会先暂存并哈希载荷，再取得该仓库的独占管理租约，备份每一个凭据所属文件，然后写入、校验，最后才提交凭据。它会排在正在进行的 turn 之后，绝不打断它。",
	"install.confirm.recovery": "执行安装恢复",
	"install.confirm.restoreTransaction": "恢复中断事务",
	"install.confirm.restoreTransactionNote": "使用已核验的备份恢复此事务开始前的文件和凭据。项目数据保持原样。",
	"install.confirm.rollback": "恢复引擎 {version}",
	"install.confirm.rollbackNote": "恢复已核对的引擎及自有片段，保留项目数据与无关改动；若失败，恢复到本次事务开始前的状态。",
	"install.confirm.title": "确认",
	"install.confirm.uninstall": "卸载已核对的 harness",
	"install.confirm.uninstallNote": "只移除本次核对且有凭据的内容。若步骤失败，事务会从备份恢复。",
	"install.confirm.upgrade": "升级到 AI-DLC {version}",
	"install.counts.title": "将会发生什么",
	"install.entries.caption": "每一个受管路径，以及它的归属类别和将要发生的操作",
	"install.entries.col.action": "将要发生",
	"install.entries.col.hash": "摘要",
	"install.entries.col.ownership": "归属",
	"install.entries.col.path": "路径",
	"install.entries.fragment": "片段 {key}",
	"install.entries.live": "磁盘上 {sha}",
	"install.entries.none": "没有受管路径受影响。",
	"install.entries.payload": "随附 {sha}",
	"install.entries.receipt": "凭据记录 {sha}",
	"install.entries.summary_one": "{n} 个受管路径",
	"install.entries.summary_other": "{n} 个受管路径",
	"install.entries.title": "受管路径",
	"install.error.nothingWritten": "没有写入任何内容。",
	"install.lease.required": "需要独占的管理租约，因此该仓库中的执行会等待事务完成。",
	"install.noForce": "这里以及 Studio 的任何地方都没有强制覆盖选项。",
	"install.payloadDigest": "载荷摘要",
	"install.planDigest": "计划摘要",
	"install.preflight.title": "仓库预检",
	"install.preserved.body": "以下内容包括已修改的可变配置、项目文件，以及 Studio 无法证明由它添加的内容。",
	"install.preserved.title": "将保留的内容",
	"install.preview.lede.install": "尚未写入任何内容。以下是 Studio 会创建的每一个路径，以及它们的归属。",
	"install.preview.lede.recovery": "尚未写入任何内容。以下是恢复事务会还原并校验的内容。",
	"install.preview.lede.rollback": "从已记录的备份恢复紧邻的上一引擎版本。确认前请核对文件变化与工作流兼容性。",
	"install.preview.lede.uninstall": "核对 Studio 安装凭据记录的文件与片段。项目文件、其他 harness 及 AI-DLC 工作流数据都会保留。",
	"install.preview.lede.upgrade": "尚未写入任何内容。以下是 Studio 会改动或退役的每一个路径，以及它拒绝触碰的每一个文件。",
	"install.preview.refresh": "重新读取仓库",
	"install.preview.running": "正在读取仓库…",
	"install.preview.title.install": "安装预览",
	"install.preview.title.recovery": "安装恢复预览",
	"install.preview.title.rollback": "回退 AI-DLC 引擎版本",
	"install.preview.title.uninstall": "卸载 AI-DLC harness",
	"install.preview.title.upgrade": "升级预览",
	"install.reason.already_absent": "此文件已经不存在。",
	"install.reason.backup_digest_mismatch": "备份与安装凭据不一致。",
	"install.reason.backup_missing": "缺少所需的旧版本备份。",
	"install.reason.bad_path": "路径已改变或包含符号链接。",
	"install.reason.engine_mutable_changed": "此可变配置在安装后发生了变化，将保留当前内容。",
	"install.reason.install_conflict": "内容与安装凭据不一致。请先保留你的改动，再重试。",
	"install.reason.legacy_fragment_ownership_unknown": "旧版安装凭据未记录 Studio 添加的精确内容。",
	"install.reason.preexisting_fragment": "此内容在 Studio 安装 harness 之前就已存在。",
	"install.reason.protected_path": "此路径不属于 Studio 可以移除的 harness 内容。",
	"install.reason.recovery_backup_invalid": "恢复备份缺失，或与记录的字节不一致。",
	"install.reason.recovery_evidence_invalid": "恢复日志缺失、发生更改或无法读取。",
	"install.reason.state_version_unsupported": "旧引擎不支持此仓库的工作流状态版本。",
	"install.reason.target_compatibility_unknown": "旧版安装凭据未记录其支持的工作流状态版本。",
	"install.reason.unreadable_backup": "无法安全读取备份。",
	"install.reason.unreadable_fragment": "Studio 无法安全读取此片段。",
	"install.reason.user_changed_recovery_target": "此文件在事务中断后发生了更改。请先保留新内容再恢复。",
	"install.receiptStatus.current": "当前",
	"install.receiptStatus.rolled_back": "已回滚",
	"install.receiptStatus.superseded": "已被取代",
	"install.receiptStatus.uninstalled": "已卸载",
	"install.recovery.blocked": "在此问题清除之前，该仓库中的 AI-DLC 执行被阻止。",
	"install.recovery.body": "一次回滚没有完成，因此该仓库中可能存在混合安装。清除它需要一次独立的恢复事务，带有自己的预览、回滚证据和恢复后校验。",
	"install.recovery.noPath": "失败事务没有记录证据路径。",
	"install.recovery.pathLabel": "失败事务的证据",
	"install.recovery.pathNote": "失败的候选内容及其事务记录保存在仓库之外的这个路径下。",
	"install.recovery.restoreTransaction": "将中断的「{kind}」事务 {id} 恢复到开始前的状态。原事务最终会标为已回滚。",
	"install.recovery.start": "预览恢复操作",
	"install.recovery.title": "需要安装恢复",
	"install.retire.body": "只有当字节仍与凭据一致时，旧版本拥有的文件才会被移除。被你改动过的待退役文件会被保留并报告，而不是删除。",
	"install.retire.title": "将被退役的内容",
	"install.stale.body": "没有写入任何内容。预览已从磁盘重新读取；确认之前请再核对一遍。",
	"install.stale.title": "在你阅读期间仓库发生了变化",
	"install.stateBlocked.body": "该仓库的 AI-DLC 状态是版本 {found}，而本载荷写入版本 {supported}。迁移该状态尚未被证明安全，因此 Studio 保持仓库可读并拒绝写入。一个完整的旧版本胜过一个混合的新版本。",
	"install.stateBlocked.title": "AI-DLC 状态版本超出本载荷的写入范围",
	"install.stateBlocked.unreadable_one": "有 {n} 个状态文件完全无法读取，按阻塞处理。",
	"install.stateBlocked.unreadable_other": "有 {n} 个状态文件完全无法读取，按阻塞处理。",
	"install.step.acquire_admin_lease": "取得管理租约",
	"install.step.backup": "备份凭据所属文件",
	"install.step.commit_receipt": "提交凭据",
	"install.step.commit_rollback": "记录已完成的版本回退",
	"install.step.commit_uninstall": "记录已完成的卸载",
	"install.step.confirm_recovery": "确认恢复中断的事务",
	"install.step.confirm_rollback": "确认已预览的版本回退",
	"install.step.confirm_uninstall": "确认已预览的卸载",
	"install.step.delete_created": "删除它创建的文件",
	"install.step.merge_fragments": "合并受管片段",
	"install.step.post_rollback_validate": "校验恢复后的版本与凭据",
	"install.step.post_uninstall_validate": "验证移除结果与保留文件",
	"install.step.post_write_validate": "写入后校验",
	"install.step.release_lease": "释放租约",
	"install.step.remove_files": "移除自有 harness 文件",
	"install.step.remove_fragments": "移除自有片段",
	"install.step.restore_backups": "还原备份",
	"install.step.restore_version": "恢复已核对的引擎版本",
	"install.step.reverify_old_receipt": "重新校验此前的凭据",
	"install.step.stage_payload": "暂存载荷",
	"install.step.verify_staging": "校验暂存的载荷",
	"install.step.write_files": "写入文件",
	"install.stepState.failed": "已失败",
	"install.stepState.ok": "已完成",
	"install.stepState.running": "进行中",
	"install.tx.close": "关闭",
	"install.tx.committed": "已提交。{version} 的凭据现在是当前凭据。",
	"install.tx.error": "错误",
	"install.tx.failedDir": "证据保存在",
	"install.tx.finished": "结束于",
	"install.tx.kind": "操作",
	"install.tx.kind.install": "安装",
	"install.tx.kind.recovery": "恢复",
	"install.tx.kind.rollback": "版本回退",
	"install.tx.kind.uninstall": "卸载",
	"install.tx.kind.upgrade": "升级",
	"install.tx.open": "打开该事务",
	"install.tx.progress": "{kind}事务 {id}：{status}",
	"install.tx.rolledBack": "所有内容都已还原。该仓库保留此前完整的安装与凭据。",
	"install.tx.started": "开始于",
	"install.tx.status": "状态",
	"install.tx.steps": "步骤",
	"install.tx.title": "事务 {id}",
	"install.tx.uninstalled": "Harness 已移除，工作流数据与已列出的保留内容仍在。",
	"install.txStatus.backed_up": "已备份凭据所属文件",
	"install.txStatus.committed": "已提交",
	"install.txStatus.failed": "已失败",
	"install.txStatus.leased": "已持有管理租约",
	"install.txStatus.merged": "已合并片段",
	"install.txStatus.recovery_required": "需要安装恢复",
	"install.txStatus.rolled_back": "已回滚",
	"install.txStatus.rolling_back": "正在回滚",
	"install.txStatus.staged": "已暂存载荷",
	"install.txStatus.validated": "已校验",
	"install.txStatus.written": "已写入文件",
	"install.version.from": "已安装的引擎",
	"install.version.notInstalled": "未安装",
	"install.version.studio": "Studio 版本",
	"install.version.to": "Studio 随附版本",
	"install.warnings.title": "警告",
	"intents.a11y.list": "意图",
	"intents.a11y.row": "{repo} 中的 {intent}，{state}，阶段 {stage}，{waiting}。{primary}",
	"intents.a11y.table": "意图",
	"intents.action.a11y": "{intent} 的操作",
	"intents.action.archive": "归档",
	"intents.action.bindSession": "绑定会话",
	"intents.action.details": "工作流地图",
	"intents.action.openAction": "打开决策",
	"intents.action.pause": "当前回合结束后暂停",
	"intents.action.recompose": "调整计划",
	"intents.action.restore": "恢复",
	"intents.action.resume": "继续",
	"intents.action.run": "运行到下一个检查点",
	"intents.action.session": "会话",
	"intents.action.unpause": "重新允许派发",
	"intents.archived.done": "只在 Studio 中归档。没有改动任何 AI-DLC 文件。",
	"intents.archivedChip": "已归档",
	"intents.blocking_one": "{n} 条阻塞发现",
	"intents.blocking_other": "{n} 条阻塞发现",
	"intents.busy": "处理中…",
	"intents.col.actions": "操作",
	"intents.col.intent": "意图",
	"intents.col.keepMoving": "Keep moving",
	"intents.col.repo": "仓库",
	"intents.col.stage": "阶段",
	"intents.col.state": "状态",
	"intents.confirm.archive.body": "归档只是在默认视图里隐藏它，不会改动磁盘上任何 AI-DLC 文件，并且可以撤销。",
	"intents.confirm.archive.title": "归档这个意图？",
	"intents.confirm.go": "确认",
	"intents.confirm.pause.blocked_one": "暂停期间，{n} 个等待中的决策将拒绝发送。",
	"intents.confirm.pause.blocked_other": "暂停期间，{n} 个等待中的决策将拒绝发送。",
	"intents.confirm.pause.body": "正在运行的回合会跑完。在你重新允许派发之前，这个意图不会再发送任何消息。",
	"intents.confirm.pause.title": "在当前回合结束后暂停？",
	"intents.confirm.restore.body": "意图会重新出现在默认视图里。磁盘上没有任何变化。",
	"intents.confirm.restore.title": "恢复这个意图？",
	"intents.confirm.unpause.body": "等待中的决策会重新变为可发送。这个操作本身不会发送任何内容。",
	"intents.confirm.unpause.title": "重新允许派发？",
	"intents.count_one": "{n} 个意图",
	"intents.count_other": "{n} 个意图",
	"intents.cursorChip": "AI-DLC 的当前意图",
	"intents.empty.body": "AI-DLC 把意图保存在仓库里。创建一个之后 Studio 会从磁盘读取；在你下令之前什么都不会运行。",
	"intents.empty.title": "磁盘上还没有意图",
	"intents.emptyFiltered.body": "清除筛选即可看到 Studio 从已注册仓库读到的全部意图。",
	"intents.emptyFiltered.clear": "清除筛选",
	"intents.emptyFiltered.title": "没有意图符合当前筛选",
	"intents.emptyNoRepo.body": "请在“仓库”页面添加一个仓库。Studio 不会自动注册目录。",
	"intents.emptyNoRepo.open": "打开仓库页面",
	"intents.emptyNoRepo.title": "还没有注册任何仓库",
	"intents.error.repo": "{repo}：{reason}",
	"intents.error.title": "有些意图无法读取",
	"intents.filter.archived": "包含已归档",
	"intents.filter.search": "筛选意图",
	"intents.filter.searchPlaceholder": "标识、标题或阶段",
	"intents.filter.space": "空间",
	"intents.filter.spaceAll": "全部空间",
	"intents.filter.state": "状态",
	"intents.filter.stateAll": "全部状态",
	"intents.filters": "筛选",
	"intents.keepMoving.breaker": "连续失败也已经停止了这个意图的派发。",
	"intents.keepMoving.unavailable": "不可用",
	"intents.keepMoving.why": "无人值守的继续执行需要一条尚未验证的机器通道，因此任何意图都无法开启 Keep moving。",
	"intents.lede": "一个仓库里可以同时有多个意图在推进。同一仓库的回合串行执行，不同仓库并行执行。",
	"intents.newIntent": "新建意图",
	"intents.noSession": "未绑定会话",
	"intents.openActions_one": "{n} 个决策等待你处理",
	"intents.openActions_other": "{n} 个决策等待你处理",
	"intents.paused.done_one": "已暂停。{n} 个等待中的决策现在会拒绝发送。",
	"intents.paused.done_other": "已暂停。{n} 个等待中的决策现在会拒绝发送。",
	"intents.pausedChip": "已暂停",
	"intents.restored.done": "已恢复。没有改动任何 AI-DLC 文件。",
	"intents.resume.disabled": "只有被 AI-DLC 挂起（Parked）的意图才提供“继续”。",
	"intents.run.disabledArchived": "已归档的意图不会运行。请先恢复它。",
	"intents.run.disabledCheckpoint": "请先处理当前问题或审批。",
	"intents.run.disabledCompleted": "该任务已完成。",
	"intents.run.disabledPaused": "这个意图已暂停，派发被阻止。请先重新允许派发。",
	"intents.run.disabledRunning": "会话正在执行中。",
	"intents.run.dismiss": "关闭",
	"intents.run.open": "打开排队的命令",
	"intents.run.queued": "Studio 已把命令排入队列，并未发送任何内容。到行动中心查看将要发送的原文后再发送。",
	"intents.session": "已绑定会话",
	"intents.session.a11y": "{intent} 的规范会话",
	"intents.session.adopt": "绑定已有会话",
	"intents.session.adoptGo": "绑定这个",
	"intents.session.adoptLede": "在本仓库上打开、且代理为 {agent} 的会话。Studio 会拒绝其他会话，因此这里不会列出它们。",
	"intents.session.adoptNone": "本仓库上还没有代理为 {agent} 的会话。请改为新建一个。",
	"intents.session.adoptTitle": "Studio 可以绑定的会话",
	"intents.session.boundLede": "这个意图的决策会作为你本人的发言写入该会话。发送之前，Studio 仍会先把原文给你看。",
	"intents.session.busy": "第 {step} 步（共 {total} 步）：{what}…",
	"intents.session.create": "创建并绑定会话",
	"intents.session.done.bound": "已绑定到 {slot}。",
	"intents.session.done.moved": "已改绑到 {slot}。",
	"intents.session.done.unbound": "已解除绑定。在绑定会话之前，这个意图的决策会拒绝发送。",
	"intents.session.fact.agent": "代理",
	"intents.session.fact.project": "项目",
	"intents.session.fact.repo": "仓库",
	"intents.session.fact.session": "会话标识",
	"intents.session.fact.slot": "会话",
	"intents.session.fact.state": "当前",
	"intents.session.fact.willCreate": "将创建的会话",
	"intents.session.failedAt": "第 {step} 步（共 {total} 步）被拒绝（{what}）：",
	"intents.session.idle": "未在运行",
	"intents.session.lede": "Studio 是把决策作为你本人的发言写入这个意图所绑定的那一个 KiroCrew 会话来投递的：代理 {agent}，项目为本仓库。在绑定之前，这里的每个决策都会被拒绝。",
	"intents.session.move": "改绑到另一个会话",
	"intents.session.moveGo": "改绑到这里",
	"intents.session.moveLede": "按项目和名称找到的会话。只要这个意图还可能有决策在途，改绑就会被拒绝。",
	"intents.session.moveNone": "没有其他属于本仓库或这个意图的会话。",
	"intents.session.moveTitle": "这个意图可以改绑到的会话",
	"intents.session.reason.current_binding": "当前已绑定",
	"intents.session.reason.name_match": "以这个意图命名",
	"intents.session.reason.project_match": "在本仓库上打开",
	"intents.session.running": "正在进行一个回合",
	"intents.session.serverSaid": "服务端原文：",
	"intents.session.step.bind": "把它绑定到这个意图",
	"intents.session.step.create": "创建会话",
	"intents.session.step.project": "把它指向本仓库",
	"intents.session.step.title": "设置标题",
	"intents.session.title": "规范会话 — {intent}",
	"intents.session.unbind": "解除绑定",
	"intents.session.unbindWhy": "解除绑定只是忘记这个意图使用哪个会话——会话本身和所有 AI-DLC 文件都不会改动。在重新绑定之前，决策会拒绝发送。",
	"intents.showing": "{visible} / {total}",
	"intents.stage.next": "下一个 {stage}",
	"intents.stage.none": "尚未记录阶段",
	"intents.stage.progress": "{total} 个阶段中已完成 {done} 个",
	"intents.title": "意图",
	"intents.unpaused.done": "已重新允许派发。没有发送任何内容。",
	"intents.unstable": "文件正在变化",
	"intents.unstableWhy": "Studio 读取时文件正在被写入，因此这一行不能作为决策依据。它会自行稳定下来。",
	"intents.waiting": "已等待 {duration}",
	"intents.warn_one": "{n} 条警告",
	"intents.warn_other": "{n} 条警告",
	"maintenance.completed": "已清理 {count} 个目录，事务历史已保留。",
	"maintenance.confirm": "删除已核对的目录",
	"maintenance.confirmNote": "将删除 {count} 个目录（{size}）。已删除的备份和诊断文件无法恢复，较早的版本回退点可能不再可用。",
	"maintenance.description": "选择要清理的旧备份或失败事务文件。活跃事务与恢复所需证据受到保护，Studio 会保留事务历史。",
	"maintenance.empty": "此仓库没有可展示的事务目录。",
	"maintenance.partial": "已清理 {count} 个目录，仍有文件未删除。请重新预览后再重试。",
	"maintenance.path": "事务目录",
	"maintenance.preview": "预览选中的清理项",
	"maintenance.reason.active_transaction": "已保护：事务仍在进行",
	"maintenance.reason.current_receipt": "已保护：当前安装需要",
	"maintenance.reason.delete_failed": "部分文件无法删除",
	"maintenance.reason.evidence_limit": "已保护：诊断元数据超出大小上限",
	"maintenance.reason.filesystem_boundary": "已保护：目录跨越文件系统边界",
	"maintenance.reason.identity_mismatch": "已保护：仓库身份已改变",
	"maintenance.reason.metadata_changed": "事务记录在清理期间发生变化",
	"maintenance.reason.not_directory": "已保护：此路径不是目录",
	"maintenance.reason.older_restore_point": "较早的恢复点：删除后无法使用此备份恢复。当前回退所需备份和凭据历史会保留。",
	"maintenance.reason.path_changed": "目录在预览后发生变化",
	"maintenance.reason.path_mismatch": "已保护：目录与事务记录不符",
	"maintenance.reason.receipt_reference_missing": "已保护：引用的安装凭据缺失",
	"maintenance.reason.receipt_restoration": "已保护：恢复过程需要",
	"maintenance.reason.recovery_required": "已保护：此事务需要恢复",
	"maintenance.reason.repo_recovery_required": "已保护：仓库需要恢复",
	"maintenance.reason.scan_limit": "已保护：目录超出扫描数量上限",
	"maintenance.reason.symlink": "已保护：不能清理符号链接",
	"maintenance.reason.terminal_backup": "已结束事务：可删除备份文件，事务历史会保留。",
	"maintenance.reason.terminal_failure_evidence": "已结束事务：可删除失败归档，事务历史与顶层诊断 JSON 会保留。",
	"maintenance.reason.unfinished_transaction": "已保护：尚未记录完成状态",
	"maintenance.reason.unreadable": "已保护：无法读取目录",
	"maintenance.refresh": "刷新目录",
	"maintenance.select": "选择",
	"maintenance.size": "大小",
	"maintenance.state": "状态或保护原因",
	"maintenance.title": "清理事务文件",
	"map.a11y.agent": "Agent {agent}",
	"map.a11y.canvas": "工作流图泳道",
	"map.a11y.elapsed": "耗时 {duration}",
	"map.a11y.lane": "{phase} 阶段",
	"map.a11y.reason": "原因：{reason}",
	"map.a11y.selected": "已选中阶段 {number} {slug}。其证据显示在阶段检视面板中。",
	"map.a11y.stage": "阶段 {number} {slug}，{phase} 阶段，{state}，{repo} / {intent}。{facts}",
	"map.a11y.tableCaption": "{intent} 当前可见的阶段，按阶段顺序排列：分组、编号、阶段、状态、agent、Gate、评审、耗时、文件与备注。",
	"map.a11y.unit": "单元 {unit}，属于阶段 {number} {slug}，{state}，{repo} / {intent}。{facts}",
	"map.a11y.unitSelected": "已选中阶段 {number} {slug} 的单元 {unit}。其证据显示在阶段检视面板中。",
	"map.accordion.note": "窄屏下各阶段以折叠面板展示。画布不会被压缩适配，因此不会有阶段卡片变得无法阅读。",
	"map.alert.openAction": "打开该事项",
	"map.alert.recovery": "该意图需要先完成核对。在证据一致之前，请把这张图视为对磁盘的一次读取，而不是计划的状态。",
	"map.alert.unstable": "Studio 读取期间记录发生了变化。这张图只是一次快照，可能已经过时。",
	"map.artifactKind.artifact": "工件",
	"map.artifactKind.contribution": "贡献",
	"map.artifactKind.memory": "记忆",
	"map.artifactKind.other": "其他",
	"map.artifactKind.questions": "问题清单",
	"map.artifactKind.review": "评审",
	"map.artifactKind.traceability": "追溯",
	"map.bar.density": "信息密度",
	"map.bar.engine": "引擎 {version}",
	"map.bar.expandUnits": "展开单元子泳道",
	"map.bar.gates_one": "{n} 个 Gate · 精确",
	"map.bar.gates_other": "{n} 个 Gate · 精确",
	"map.bar.hideUnits": "收起单元子泳道",
	"map.bar.layout": "布局",
	"map.bar.scope": "{repo} / {intent}",
	"map.bar.showAllStages": "查看所有阶段",
	"map.bar.showCanvas": "以泳道显示",
	"map.bar.showPlanOnly": "只看当前计划",
	"map.bar.showTable": "以表格显示",
	"map.bar.stagesKnown_one": "已知 {n} 个阶段 · 精确",
	"map.bar.stagesKnown_other": "已知 {n} 个阶段 · 精确",
	"map.bar.stagesSelected_one": "已选 {n} 个 · 精确",
	"map.bar.stagesSelected_other": "已选 {n} 个 · 精确",
	"map.chip.agentTitle": "主导 agent：{agent}",
	"map.chip.artifacts_one": "{n} 个文件",
	"map.chip.artifacts_other": "{n} 个文件",
	"map.chip.conditional": "条件执行",
	"map.chip.current": "正在执行",
	"map.chip.directive": "当前指令",
	"map.chip.elapsedTitle": "最近一次尝试的耗时：{duration}",
	"map.chip.gate": "Gate",
	"map.chip.modeTitle": "执行模式：{mode}",
	"map.chip.noReview": "无评审",
	"map.chip.review": "评审 {class}",
	"map.chip.reviewerTitle": "评审者：{reviewer}",
	"map.col.agent": "Agent",
	"map.col.artifacts": "文件",
	"map.col.elapsed": "耗时",
	"map.col.gate": "Gate",
	"map.col.notes": "备注",
	"map.col.number": "编号",
	"map.col.phase": "分组",
	"map.col.review": "评审",
	"map.col.stage": "阶段",
	"map.col.state": "状态",
	"map.consequence": "默认显示当前计划。查看所有阶段时，可查看未选阶段及其原因。阶段编号保持不变，依赖关系仅连接当前可见的阶段。",
	"map.density.dependencies": "依赖",
	"map.density.detailed": "详细",
	"map.density.overview": "概览",
	"map.empty.action": "选择一个意图",
	"map.empty.body": "工作流图把某个意图的阶段图与其记录中实际存在的内容对照呈现。选定一个意图后，Studio 会从磁盘读取它。",
	"map.empty.noRepo": "请先在范围栏中选择一个仓库。Studio 只读取你已注册的仓库。",
	"map.empty.plan": "当前计划没有选中的阶段。可查看所有阶段以了解完整流程。",
	"map.empty.title": "尚未选择意图",
	"map.error.title": "无法读取工作流图",
	"map.hiddenSelection": "链接中的阶段不在当前计划内。可查看所有阶段以了解其详情。",
	"map.inspector.artifacts": "文件",
	"map.inspector.artifactsEmpty": "该阶段尚无任何文件记录。",
	"map.inspector.artifactsNote": "Studio 只读取这些文件，从不写入。",
	"map.inspector.audit": "审计",
	"map.inspector.auditEmpty": "最近的审计事件都没有提到该阶段。",
	"map.inspector.auditNote": "记录中最近的事件，已按该阶段过滤。字段名与取值均为 AI-DLC 自身的原文。",
	"map.inspector.close": "清除阶段选择",
	"map.inspector.consumes": "消费",
	"map.inspector.downstream": "下游",
	"map.inspector.noOperation": "该阶段没有可执行的操作",
	"map.inspector.none": "无记录",
	"map.inspector.notSelected": "当前计划未选中这个阶段。",
	"map.inspector.open": "在 Action Center 中打开该 {type}",
	"map.inspector.openArtifact": "打开 {name}",
	"map.inspector.operation": "可执行的操作",
	"map.inspector.pick": "选择一个阶段，即可查看它的文件、评审约定、审计记录，以及它所允许的那一项操作。",
	"map.inspector.produces": "产出",
	"map.inspector.reasonAhead": "该阶段位于游标之前方，引擎会自行推进到它。",
	"map.inspector.reasonArchived": "该意图已归档。",
	"map.inspector.reasonDone": "该阶段已完成，这里没有等待你处理的事项。",
	"map.inspector.reasonPaused": "该意图已暂停，Studio 不会为它下发任何内容。",
	"map.inspector.reasonSkipped": "在本次计划中该阶段不执行。",
	"map.inspector.relationships": "关系",
	"map.inspector.review": "评审",
	"map.inspector.reviewContract": "配置的评审：{class}，评审者 {reviewer}。",
	"map.inspector.reviewContractNoReviewer": "配置的评审：{class}。",
	"map.inspector.reviewEmpty": "该阶段没有评审结论记录。",
	"map.inspector.reviewNone": "该阶段未配置评审者。",
	"map.inspector.reviewOtherStage": "记录中最近一次评审属于阶段 {stage}，不属于当前阶段。",
	"map.inspector.reviewRevisions_one": "{n} 次返工",
	"map.inspector.reviewRevisions_other": "{n} 次返工",
	"map.inspector.reviewVerdict": "结论：{verdict}",
	"map.inspector.summaryConfirmation": "摘要确认：{value}",
	"map.inspector.title": "阶段检视",
	"map.inspector.units": "单元",
	"map.inspector.upstream": "上游",
	"map.inspector.why": "它未执行的原因。",
	"map.inspector.whyUnknown": "记录中没有说明原因。保留其位置以便完整阅读计划。",
	"map.lane.counts_one": "{n} 个阶段 · 跳过 {skipped} 个",
	"map.lane.counts_other": "{n} 个阶段 · 跳过 {skipped} 个",
	"map.phase.statusTitle": "磁盘上记录的阶段状态：{status}",
	"map.phase.unknown": "未识别的阶段分组",
	"map.rel.downstream": "下游",
	"map.rel.upstream": "上游",
	"map.review.level.advisory": "建议项",
	"map.review.level.blocker": "阻塞项",
	"map.review.level.resolved": "已回应",
	"map.review.level.unknown": "未分类",
	"map.review.more": "另有 {n} 条",
	"map.state.excluded": "未选入",
	"map.status.executing": "正在执行 {number} {slug}",
	"map.status.idle": "当前没有阶段在执行",
	"map.status.progress": "已记录完成 {done} / {total} 个阶段",
	"map.units.note": "单元状态仅用于呈现。记录中每个阶段只有一行，因此单元卡片不构成任何决策依据。",
	"map.units.row": "{number} {slug} — 按单元执行",
	"migration.applied.at": "应用时间",
	"migration.applied.backup": "原型注册表的副本保存在 {path}",
	"migration.applied.noBackup": "没有记录备份路径。",
	"migration.applied.status": "状态",
	"migration.applied.summary": "后端校验的计数",
	"migration.applied.title": "迁移已完成",
	"migration.apply": "一次性应用迁移",
	"migration.applyBlocked": "Studio 无法计算原型注册表的哈希，因此不会应用一个无法校验的迁移。",
	"migration.applyHint": "迁移只执行一次。Studio 会先把原型注册表复制到一旁，出问题时它仍然可读。",
	"migration.applyNeedsPreview": "请先读取预览。Studio 只会应用你已经看过的计划。",
	"migration.applying": "正在应用…",
	"migration.both.body": "aidlc-console 已安装并启用。请先迁移它的注册表，然后在 KiroCrew 中停用它，使这些仓库只有一个控制界面。",
	"migration.both.title": "两个 App 可能控制同一批仓库",
	"migration.consoleState.absent": "未安装",
	"migration.consoleState.disabled": "已安装，已停用",
	"migration.consoleState.enabled": "已安装并启用",
	"migration.consoleState.title": "原型 App",
	"migration.counts": "已读取 {rowsIn} 条，将注册 {rowsOut} 条",
	"migration.failed": "迁移未完成：{message}",
	"migration.keeps.actions": "操作历史。迁移后的仓库以空队列开始。",
	"migration.keeps.credentials": "任何形式的凭据。",
	"migration.keeps.repoData": "你仓库内部的任何内容。AI-DLC 自己的文件从磁盘读取，绝不复制。",
	"migration.keeps.secret": "原型的 app secret。Studio 从不读取它。",
	"migration.keeps.title": "绝不迁移的内容",
	"migration.lede": "这台机器上存在原型 App aidlc-console。它的仓库注册表可以一次性迁移到这里。",
	"migration.moves.archive": "归档标记以及 Studio 自己的偏好设置。",
	"migration.moves.ids": "内部仓库 id 会在单个事务内重写，并在提交前校验行数。",
	"migration.moves.registry": "仓库注册表：路径、标签以及添加日期。",
	"migration.moves.title": "将迁移的内容",
	"migration.nextSteps.desc": "Studio 不会启用、停用或卸载 App。这两步需要你在该 App 的 KiroCrew 页面上完成。",
	"migration.nextSteps.disable_console": "停用 aidlc-console，使这些仓库只由一个 App 控制。",
	"migration.nextSteps.other": "后端给出的步骤：{step}",
	"migration.nextSteps.title": "在 KiroCrew 中完成",
	"migration.nextSteps.uninstall_console_keep_data": "然后卸载 aidlc-console，保留其数据作为回滚来源。",
	"migration.notApplicable.already_applied": "迁移已经执行过。",
	"migration.notApplicable.malformed": "原型注册表无法作为注册表读取，因此不会从中复制任何内容。",
	"migration.notApplicable.not_found": "这台机器上没有找到 aidlc-console 的注册表。",
	"migration.notApplicable.other": "后端给出的原因：{reason}",
	"migration.notApplicable.title": "没有可迁移的内容",
	"migration.openConsole": "打开 aidlc-console 的 App 页面",
	"migration.preview": "预览将迁移的内容",
	"migration.previewAgain": "重新读取预览",
	"migration.previewFailed": "无法读取预览：{message}",
	"migration.previewNothing": "预览不会写入任何内容。",
	"migration.previewing": "正在读取原型注册表…",
	"migration.region": "原型迁移",
	"migration.resolution.already_registered": "已注册为 {of} —— 跳过",
	"migration.resolution.duplicate": "与 {of} 重复 —— 跳过",
	"migration.resolution.migrate": "将被注册",
	"migration.resolution.other": "后端给出的结果：{raw}",
	"migration.resolution.unavailable": "无法访问 —— 将注册为不可用",
	"migration.rows.added": "添加时间",
	"migration.rows.detail": "详情",
	"migration.rows.label": "仓库",
	"migration.rows.none": "原型注册表中没有记录。",
	"migration.rows.path": "路径",
	"migration.rows.resolution": "结果",
	"migration.rows.title": "原型注册表中的记录",
	"migration.source": "来源",
	"migration.sourceDigest": "摘要",
	"migration.stale": "在你阅读预览期间原型注册表发生了变化，因此没有应用任何内容。预览已重新读取——请在应用前再确认一遍。",
	"migration.title": "从 AI-DLC console 原型迁移",
	"nav.actions": "待办中心",
	"nav.activity": "活动",
	"nav.intents": "意图",
	"nav.map": "流程图",
	"nav.newIntent": "新建意图",
	"nav.repos": "仓库",
	"nav.settings": "设置",
	"plan.busy": "正在重新计算…",
	"plan.diff.consequences": "下游影响",
	"plan.diff.consequencesNone": "所选阶段的产物没有变化。",
	"plan.diff.gained": "重新产出 {artifact}。",
	"plan.diff.lost": "不再产出 {artifact}。",
	"plan.diff.lostConsumers": "{stages} 把它声明为输入。",
	"plan.diff.lostNoConsumers": "所选阶段中没有任何一个把它声明为输入。",
	"plan.diff.none": "没有改动 — 这就是未经修改的 {scope} 预设。",
	"plan.diff.noneNoScope": "没有改动预设。",
	"plan.diff.off": "− 你关闭了 {slug}",
	"plan.diff.off.advisor": "− {slug} 由 Advisor 的建议关闭",
	"plan.diff.on": "+ 你开启了 {slug}",
	"plan.diff.on.advisor": "+ {slug} 由 Advisor 的建议开启",
	"plan.diff.title": "与预设默认值的差异",
	"plan.issue.behind_cursor": "{stage} 位于 AI-DLC 游标处或之前，它的计划不能改动。",
	"plan.issue.dependency_missing": "所选阶段中没有任何一个会产出 {artifact}，而 {stage} 声明它是必需输入，因此 AI-DLC 拒绝这种组合。",
	"plan.issue.depth_invalid": "有一项配置值不是 AI-DLC 接受的取值，Studio 因此没有设置它。AI-DLC 允许：{allowed}。",
	"plan.issue.frozen_stage": "{stage} 已经开始、完成或被跳过，它的计划不能改动。",
	"plan.issue.recompose_not_allowed": "AI-DLC 只对 Status 为 {required_status} 的意图执行 recompose；这个意图报告的是 {status}。",
	"plan.issue.required_stage_disabled": "{stage} 是当前计划所必需的，因此这次改动没有生效。",
	"plan.issue.scope_unknown": "这个仓库没有名为 {scope} 的范围。它已知的是：{known}。",
	"plan.issue.skeleton_anchor": "这次改动会把 walking-skeleton 锚点从 {anchor_before} 移到 {anchor_after}，AI-DLC 拒绝这样做。",
	"plan.issue.unknown": "AI-DLC 拒绝这个计划：{code}",
	"plan.issue.unknown_stage": "{stage} 不在已安装的阶段图中。",
	"plan.issues.none": "AI-DLC 接受当前描述的计划。",
	"plan.issues.title": "AI-DLC 不接受这个计划的原因",
	"plan.lock.always": "AI-DLC 把这个阶段标记为总是执行，因此不能关闭。",
	"plan.lock.at_gate": "这个阶段正在等你决定，它的计划已固定。",
	"plan.lock.behind_cursor": "这个阶段位于 AI-DLC 游标处或之前，它的计划已固定。",
	"plan.lock.completed": "AI-DLC 已经完成或跳过了这个阶段，它的计划已固定。",
	"plan.lock.current": "这是 AI-DLC 正在处理的阶段。",
	"plan.lock.not_in_graph": "这个阶段出现在意图的状态文件里，但不在已安装的阶段图中，Studio 无法对它做判断。",
	"plan.lock.required_by": "{slug} 已选中，并声明需要这个阶段，因此不能关闭。",
	"plan.lock.short.always": "总是执行",
	"plan.lock.short.at_gate": "停在 Gate",
	"plan.lock.short.behind_cursor": "在游标之前",
	"plan.lock.short.completed": "已完成",
	"plan.lock.short.current": "正在执行",
	"plan.lock.short.not_in_graph": "不在阶段图中",
	"plan.lock.short.required_by": "{slug} 需要它",
	"plan.lock.short.unknown": "由 AI-DLC 固定",
	"plan.lock.unknown": "AI-DLC 不允许改动这个阶段：{reason}",
	"plan.matrix.a11y": "阶段矩阵",
	"plan.phase.gates_one": "{n} 个 Gate",
	"plan.phase.gates_other": "{n} 个 Gate",
	"plan.phase.on": "{total} 个中已开启 {on} 个",
	"plan.phase.other": "不在已安装的阶段图中",
	"plan.recompose.add_one": "将开启 {n} 个阶段",
	"plan.recompose.add_other": "将开启 {n} 个阶段",
	"plan.recompose.applied": "AI-DLC 已应用计划改动。",
	"plan.recompose.appliedFailed": "AI-DLC 执行了，但报告了失败，磁盘上的计划可能没有变化。运行这个意图前请重新检查。",
	"plan.recompose.apply": "应用改动",
	"plan.recompose.argv": "AI-DLC 将要执行的命令",
	"plan.recompose.close": "关闭",
	"plan.recompose.confirmBody": "Studio 会在仓库管理租约下执行 AI-DLC 自己的 recompose 动作。它只重写尚未开始的阶段行，不会向会话发送任何消息，也不会触碰已完成的阶段。",
	"plan.recompose.confirmGo": "执行",
	"plan.recompose.confirmTitle": "确认 recompose",
	"plan.recompose.current": "AI-DLC 的游标在 {stage}",
	"plan.recompose.currentUnknown": "AI-DLC 没有为这个意图记录当前阶段。",
	"plan.recompose.error": "改动没有被应用。",
	"plan.recompose.lede": "只有位于 AI-DLC 游标之后、尚未开始的阶段可以改动。其余阶段都会列出，并说明为什么已固定。",
	"plan.recompose.none": "还没有选择任何改动，因此没有可应用的内容。",
	"plan.recompose.refused": "AI-DLC 不会应用这次改动",
	"plan.recompose.reload": "重新检查计划",
	"plan.recompose.reset": "放弃这些选择",
	"plan.recompose.skip_one": "将关闭 {n} 个阶段",
	"plan.recompose.skip_other": "将关闭 {n} 个阶段",
	"plan.recompose.title": "调整 {intent} 的计划",
	"plan.stage.a11y": "{number} {slug}",
	"plan.stage.always": "总是执行",
	"plan.stage.conditional": "条件执行",
	"plan.stage.conditionalOn": "条件：{condition}",
	"plan.stage.consumes": "消费 {artifacts}",
	"plan.stage.excluded": "被该范围排除",
	"plan.stage.gate": "Gate",
	"plan.stage.locked": "已锁定",
	"plan.stage.off": "未选中",
	"plan.stage.on": "已选中",
	"plan.stage.perUnit": "每个工作单元执行一次",
	"plan.stage.produces": "产出 {artifacts}",
	"plan.stage.producesNone": "未声明产物",
	"plan.stage.review": "{class} 级评审",
	"plan.stage.reviewer": "评审者 {reviewer}",
	"plan.stage.state": "磁盘状态：{state}",
	"queue.count": "{visible} / {total}",
	"queue.empty.action": "查看工作流图",
	"queue.empty.body": "已登记的意图都在运行、已完成，或者在等待与决定无关的事。下一个需要人参与的节点会出现在这里。",
	"queue.empty.title": "没有需要你判断的事",
	"queue.filterLabel": "筛选队列",
	"queue.filterPlaceholder": "按仓库、意图、阶段筛选",
	"queue.group.attention": "熔断、失败与安装冲突",
	"queue.group.blocking": "阻塞中的 Gate 与提问",
	"queue.group.info": "预算停止与暂停",
	"queue.group.oldest": "最久等待优先",
	"queue.group.recovery": "需要恢复处理，以及投递结果不确定",
	"queue.group.repo": "仓库",
	"queue.label": "待办队列",
	"queue.more": "下面还有 {n} 项，继续滚动即可加载。",
	"queue.noMatch.body": "清除筛选条件即可看到全部等待中的事项。",
	"queue.noMatch.title": "没有匹配该筛选条件的条目",
	"queue.noPrimary": "暂无可执行的决定",
	"queue.organize.oldest": "最久",
	"queue.organize.priority": "优先级",
	"queue.organize.repo": "仓库",
	"queue.organize.type": "类型",
	"queue.organizeLabel": "队列排序方式",
	"queue.refreshing": "正在重读",
	"queue.title": "等你决定",
	"repos.a11y.blocked": "{repo} 需要安装恢复。在恢复完成之前，该仓库中的 AI-DLC 执行被阻止。",
	"repos.a11y.repoRow": "{label}，{path}，{install}，{availability}，{intents}，{queue}",
	"repos.action.archive": "归档仓库",
	"repos.action.details": "详情",
	"repos.action.doctor": "运行 AI-DLC doctor",
	"repos.action.doctorDone": "AI-DLC doctor 已完成。退出码 {code}。",
	"repos.action.doctorFailed": "AI-DLC doctor 报告了问题。退出码 {code}。",
	"repos.action.doctorNote": "doctor 是 AI-DLC 自己的健康检查。它会向仓库的审计记录追加 HEALTH_CHECKED 与 GUARDRAIL_LOADED 两行，因此 Studio 只在你明确要求时才运行它。",
	"repos.action.doctorRunning": "正在运行 doctor…",
	"repos.action.install": "安装 AI-DLC",
	"repos.action.rebind": "重新绑定到新路径",
	"repos.action.recover": "安装恢复",
	"repos.action.remove": "取消注册",
	"repos.action.rename": "重命名仓库",
	"repos.action.rescan": "重新扫描",
	"repos.action.rescanning": "正在重新扫描…",
	"repos.action.rollback": "回退引擎版本",
	"repos.action.unarchive": "恢复仓库",
	"repos.action.uninstall": "卸载 harness",
	"repos.action.upgrade": "升级 AI-DLC",
	"repos.add.duplicate.body": "该路径解析到的仓库与 {label} 相同。Studio 拒绝为同一身份重复注册：租约是按身份加锁的，同一仓库出现两条记录会让它被并行执行两次。",
	"repos.add.duplicate.open": "打开 {label}",
	"repos.add.duplicate.title": "已经注册过",
	"repos.add.labelHelp": "显示在队列和范围栏中。留空时 Studio 使用目录名。",
	"repos.add.labelLabel": "标签（可选）",
	"repos.add.needPreflight": "在只读预检确认该路径可以注册之前，注册按钮保持禁用。",
	"repos.add.open": "添加仓库",
	"repos.add.pathHelp": "仓库根目录的绝对路径，例如 /Users/you/work/checkout-web。Studio 会展开开头的 ~ 并解析符号链接；查看过程不写入任何内容。",
	"repos.add.pathLabel": "绝对路径",
	"repos.add.pathNotAbsolute": "路径必须是绝对路径：需以 / 或 ~ 开头。",
	"repos.add.pathRequired": "必须填写绝对路径。",
	"repos.add.preflight": "运行预检",
	"repos.add.preflightRunning": "正在读取目录…",
	"repos.add.register": "注册该路径",
	"repos.add.registering": "正在注册…",
	"repos.add.title": "添加仓库",
	"repos.availability.title": "该仓库需要处理",
	"repos.card.findings": "发现项",
	"repos.card.harness": "Harness 目录",
	"repos.card.harness.noUtility": "无 utility 脚本",
	"repos.card.harness.none": "在该仓库中没有找到 AI-DLC harness 目录。",
	"repos.card.harness.utility": "存在 utility 脚本",
	"repos.card.identity": "身份",
	"repos.card.identity.added": "注册时间",
	"repos.card.identity.gitCommonDir": "Git common dir 身份",
	"repos.card.identity.lastSeen": "最后一次可见",
	"repos.card.identity.path": "规范路径",
	"repos.card.identity.platform": "平台",
	"repos.card.identity.resolved": "解析出的身份",
	"repos.card.identity.scanned": "最后一次扫描",
	"repos.card.install": "AI-DLC 安装状态",
	"repos.card.install.engineDir": "引擎目录",
	"repos.card.install.otherHarness": "这个仓库的 AI-DLC {version} 安装在 {dir} 下，Studio 不管理这个 harness。Studio 自己的 Kiro harness（{own}）没有安装在这里，因此没有属于 Studio 的安装可供升级。",
	"repos.card.install.otherHarnessInstall": "安装会在它旁边新增 {own}：{dir} 下的文件不会被写入、改动或删除，已有的 aidlc/ 工作区也完全保持原样 — 两个 harness 读的是同一个工作区。",
	"repos.card.install.stages": "阶段数",
	"repos.card.install.stateVersion": "状态版本",
	"repos.card.intents": "Intent",
	"repos.card.intents.none": "该仓库磁盘上没有 intent。",
	"repos.card.intents.open": "打开 Intents 页",
	"repos.card.lease.admin": "管理租约",
	"repos.card.lease.execution": "执行租约",
	"repos.card.lease.none": "未持有",
	"repos.card.lease.orphaned": "已失联 — 没有心跳",
	"repos.card.lease.since": "自 {when} 起持有",
	"repos.card.leases": "租约",
	"repos.card.queue": "仓库队列",
	"repos.card.queue.none": "该仓库中没有等待你处理的事项。",
	"repos.card.queue.open": "打开 Action Center",
	"repos.card.receipt": "安装凭据",
	"repos.card.receipt.files_one": "{n} 个受管文件",
	"repos.card.receipt.files_other": "{n} 个受管文件",
	"repos.card.receipt.none": "没有安装凭据。Studio 未在该仓库中安装任何内容。",
	"repos.card.receipt.written": "写入于 {when}",
	"repos.card.transactions": "安装历史",
	"repos.card.transactions.none": "该仓库尚未运行过安装事务。",
	"repos.col.actions": "操作",
	"repos.col.aidlc": "AI-DLC",
	"repos.col.engine": "引擎",
	"repos.col.git": "Git",
	"repos.col.intents": "Intent",
	"repos.col.queue": "队列",
	"repos.col.repository": "仓库",
	"repos.counts.blocking_one": "{n} 个阻塞性发现项",
	"repos.counts.blocking_other": "{n} 个阻塞性发现项",
	"repos.counts.inFlight_one": "{n} 个进行中",
	"repos.counts.inFlight_other": "{n} 个进行中",
	"repos.counts.intents_one": "{n} 个 intent",
	"repos.counts.intents_other": "{n} 个 intent",
	"repos.counts.open_one": "{n} 项等待你处理",
	"repos.counts.open_other": "{n} 项等待你处理",
	"repos.desktopOnly.body": "选择绝对路径、阅读预检报告并确认安装，需要比当前更宽的屏幕。请在桌面端打开 Studio 来注册仓库，或安装、升级、恢复 AI-DLC。已经注册的内容在这里仍然可以查看。",
	"repos.desktopOnly.title": "注册、安装与升级仅支持桌面端",
	"repos.detail.back": "全部仓库",
	"repos.directory.help": "选择运行 KiroCrew 的机器上的目录。",
	"repos.directory.list": "目录列表",
	"repos.directory.open": "浏览目录",
	"repos.directory.parent": "上一级目录",
	"repos.directory.select": "使用此目录",
	"repos.directory.truncated": "目录列表已限量显示。可填写更具体的路径，打开未显示的目录。",
	"repos.doctor.duration": "耗时：{ms} 毫秒",
	"repos.doctor.output": "检查输出",
	"repos.doctor.result": "Doctor 检查结果",
	"repos.doctor.stderr": "错误输出",
	"repos.drift_one": "安装后有 {n} 个受管文件被改动",
	"repos.drift_other": "安装后有 {n} 个受管文件被改动",
	"repos.empty.body": "填写仓库路径或浏览并选择目录。Studio 只注册你确认的目录。",
	"repos.empty.title": "还没有注册任何仓库",
	"repos.engine.bundled": "随附 {version}",
	"repos.engine.newerInstalled": "已安装的引擎比 Studio 随附的更新",
	"repos.engine.otherHarness": "安装在 {dir} 下，Studio 不管理这个目录",
	"repos.engine.stages_one": "{n} 个阶段",
	"repos.engine.stages_other": "{n} 个阶段",
	"repos.engine.stateBlocked": "状态版本 {version} — 写入它尚未被证明安全",
	"repos.engine.stateVersion": "状态版本 {version}",
	"repos.engine.upgradeAvailable": "有可用升级",
	"repos.error.unchanged": "仓库中没有任何改动。",
	"repos.finding.fallback": "发现项 {code}",
	"repos.footer": "维护性升级出现在这里和通知中，不会进入阻塞式工作流队列。取消注册仓库不会改动磁盘上的任何字节。",
	"repos.git.ahead_one": "领先 {n} 个提交",
	"repos.git.ahead_other": "领先 {n} 个提交",
	"repos.git.behind_one": "落后 {n} 个提交",
	"repos.git.behind_other": "落后 {n} 个提交",
	"repos.git.clean": "干净",
	"repos.git.detached": "HEAD 处于分离状态",
	"repos.git.dirty_one": "{n} 个文件有改动",
	"repos.git.dirty_other": "{n} 个文件有改动",
	"repos.git.head": "HEAD {sha}",
	"repos.git.noUpstream": "没有上游分支",
	"repos.git.observedAt": "观测于 {when}",
	"repos.git.ownedDirty.body": "这些文件由 Studio 安装。此处的本地改动会阻止升级，直到你自己处理：Studio 从不覆盖它无法证明属于自己的字节。",
	"repos.git.ownedDirty.none": "没有凭据所属文件存在本地改动。",
	"repos.git.ownedDirty.title": "有本地改动的凭据所属文件",
	"repos.git.readOnly": "Studio 只读取 Git，从不执行 commit、push、checkout、stash 或 reset。",
	"repos.git.title": "Git 观测",
	"repos.git.unavailable": "Git 观测未启用",
	"repos.git.unrelatedDirty_one": "有 {n} 个无关文件存在本地改动，它不会阻止升级。",
	"repos.git.unrelatedDirty_other": "有 {n} 个无关文件存在本地改动，它们不会阻止升级。",
	"repos.git.upstream": "跟踪 {upstream}",
	"repos.lede": "这里的每个仓库都由你主动添加。可浏览目录或填写路径，Studio 不会自动注册仓库。",
	"repos.maintenance.note": "维护工作出现在这里和通知中，不会进入阻塞式工作流队列。",
	"repos.maintenance.title": "维护",
	"repos.metadata.archiveBody": "将仓库从活跃列表中隐藏，保留文件、会话绑定与历史。随时勾选“显示已归档仓库”恢复。",
	"repos.metadata.archived": "已归档",
	"repos.metadata.label": "仓库标签",
	"repos.metadata.save": "保存标签",
	"repos.metadata.showArchived": "显示已归档仓库",
	"repos.preflight.aidlc": "磁盘上的 AI-DLC",
	"repos.preflight.bun": "bun",
	"repos.preflight.bunFound": "{version}，位于 {path}",
	"repos.preflight.bunMissing": "未找到",
	"repos.preflight.canInstall": "可以在这里安装 AI-DLC。",
	"repos.preflight.canRegister": "该路径可以注册。",
	"repos.preflight.cannotInstall": "目前还不能在这里安装 AI-DLC。",
	"repos.preflight.cannotRegister": "该路径不能注册。",
	"repos.preflight.canonical": "解析后的路径",
	"repos.preflight.freeSpace": "可用空间",
	"repos.preflight.git": "Git",
	"repos.preflight.gitClean": "干净",
	"repos.preflight.gitDirty": "有未提交的改动",
	"repos.preflight.gitNotRepo": "不是 Git 仓库",
	"repos.preflight.gitRepo": "仓库，当前分支 {branch}",
	"repos.preflight.harness": "已存在的 harness",
	"repos.preflight.identity": "身份",
	"repos.preflight.identityProvable": "可证明",
	"repos.preflight.identityUnprovable": "无法证明 — 执行与安装保持禁用",
	"repos.preflight.inode": "设备 {dev} · inode {ino}",
	"repos.preflight.input": "你输入的",
	"repos.preflight.intents_one": "{n} 个 intent",
	"repos.preflight.intents_other": "{n} 个 intent",
	"repos.preflight.layoutLegacy": "较旧的单状态文件布局",
	"repos.preflight.layoutNone": "没有 AI-DLC 状态",
	"repos.preflight.layoutSpaces": "spaces 布局",
	"repos.preflight.none": "无",
	"repos.preflight.notDirectory": "该路径不是目录。",
	"repos.preflight.notWritable": "不可写 — 安装会失败",
	"repos.preflight.readOnly": "只读。Studio 没有执行该目录中的任何代码，也没有写入任何内容。",
	"repos.preflight.receipt": "已有的凭据",
	"repos.preflight.sensitive": "该位置受保护。Studio 从不读取凭据目录或仪表盘自有目录。",
	"repos.preflight.spaces_one": "{n} 个 space",
	"repos.preflight.spaces_other": "{n} 个 space",
	"repos.preflight.stateVersions": "状态版本 {versions}",
	"repos.preflight.symlinks": "受管路径上的符号链接",
	"repos.preflight.symlinksBody": "Studio 拒绝在符号链接位置写入：那会把字节写到仓库之外。",
	"repos.preflight.title": "预检",
	"repos.preflight.warnings": "警告",
	"repos.preflight.writable": "可写",
	"repos.rebind.body": "填写新的绝对路径。重新绑定后仓库 id 保持不变，它的 intent、动作与历史仍然附着在上面。",
	"repos.rebind.confirm": "重新绑定",
	"repos.rebind.title": "重新绑定 {label}",
	"repos.registered": "{label} 已注册。目前还没有在其中安装任何内容。",
	"repos.remedy.identity_unprovable": "Studio 无法证明该目录是一个独立的仓库，因此无法为它加锁租约。它仍然可读；在重复项被解决之前，执行与安装保持禁用。",
	"repos.remedy.moved": "该目录不在 Studio 记录的位置。把这条注册重新绑定到新的绝对路径，它的 intent、动作与历史就会保留；或者取消注册，这不会改动磁盘上的任何字节。",
	"repos.remedy.permission_denied": "Studio 无法读取该目录。恢复读取权限后再重新扫描。Studio 不会自动重试。",
	"repos.remedy.unavailable": "目前无法读取该目录。等它恢复后重新扫描，或取消注册 — 取消注册不会改动磁盘上的任何字节。",
	"repos.remove.body": "Studio 会忘记这条注册、它的会话绑定和动作历史。目录内部不会发生任何变化：没有文件被删除、修改或移动。",
	"repos.remove.confirm": "取消注册",
	"repos.remove.title": "取消注册 {label}？",
	"repos.title": "仓库",
	"repos.totals": "已注册 {repos} 个 · 不可用 {unavailable} 个 · 等待你处理 {open} 项",
	"review.block.contract": "评审约定",
	"review.block.findings": "评审发现",
	"review.block.receipts": "评审凭证",
	"review.contract.class": "评审类别",
	"review.contract.classSub": "由该阶段配置决定",
	"review.contract.none": "未声明",
	"review.contract.reviewer": "评审者",
	"review.contract.reviewerSub": "只做评审的 agent，不能撰写工件。",
	"review.contract.revisions": "修订次数",
	"review.contract.revisionsSub": "每次修订都会重新运行评审。",
	"review.empty.body": "该阶段或者没有声明评审者，或者评审尚未运行。只有当阶段评审块中确实写有发现时，Studio 才会显示它们。",
	"review.empty.title": "该阶段没有评审发现",
	"review.evidence.conflict": "与其他来源矛盾",
	"review.evidence.noRaw": "该来源没有任何记录。",
	"review.evidence.none": "该操作没有可展示的证据。",
	"review.evidence.policy": "原始内容按记录原样展示，并受脱敏与访问策略约束。Studio 从不修改它们。",
	"review.evidence.redacted": "已脱敏",
	"review.evidence.source.aidlc_audit": "AI-DLC 审计",
	"review.evidence.source.aidlc_state": "AI-DLC 状态",
	"review.evidence.source.git": "Git 观测",
	"review.evidence.source.kirocrew_session": "KiroCrew 会话",
	"review.evidence.source.studio_action": "Studio 操作",
	"review.evidence.source.turn_marker": "轮次标记",
	"review.evidence.title": "证据抽屉",
	"review.group.advisory": "建议 — {n}",
	"review.group.blocker": "未解决的阻塞项 — {n}",
	"review.group.resolved": "本次修订中已回应 — {n}",
	"review.group.unknown": "评审者未标注级别 — {n}",
	"review.inArtifact": "定位到工件",
	"review.iteration": "第 {n} 轮",
	"review.level.advisory": "建议",
	"review.level.blocker": "阻塞项",
	"review.level.resolved": "已回应",
	"review.level.unknown": "未标注级别",
	"review.noAnchor": "评审者没有为这条发现记录所在章节，因此它在此列出，而不是锚定到工件中。",
	"review.openBlockers": "{n} 项未解决",
	"review.pane.label": "{name} 的评审发现",
	"review.pane.title": "评审发现",
	"review.quotedVerbatim": "原文引自阶段评审块。",
	"review.receipt.at": "时间",
	"review.receipt.event": "事件",
	"review.receipt.iteration": "轮次",
	"review.receipt.note": "这些记录是 AI-DLC 自己的审计事件。Studio 只读取，从不写入。",
	"review.receipt.verdict": "结论",
	"review.stage": "阶段",
	"review.verdict": "结论",
	"scope.allRepos": "全部仓库",
	"scope.crumbNoScan": "仅显示已注册的仓库 — 仓库始终由你明确添加",
	"scope.registered_one": "已注册 {n} 个",
	"scope.registered_other": "已注册 {n} 个",
	"scope.selectLabel": "范围",
	"scope.unavailable_one": "{n} 个仓库不可用",
	"scope.unavailable_other": "{n} 个仓库不可用",
	"settings.about.boot": "启动 id",
	"settings.about.desc": "App 版本与内置的 AI-DLC 版本各自独立演进。某个仓库也可能仍在运行更旧的已安装引擎；这会在“仓库”页作为维护项显示。",
	"settings.about.engineVersion": "内置 AI-DLC 版本",
	"settings.about.host": "KiroCrew 版本",
	"settings.about.hostDetached": "未接入",
	"settings.about.issues_one": "{n} 个问题",
	"settings.about.issues_other": "{n} 个问题",
	"settings.about.minHost": "最低 KiroCrew 版本",
	"settings.about.payload": "内置载荷",
	"settings.about.payloadBad_one": "{n} 处不匹配",
	"settings.about.payloadBad_other": "{n} 处不匹配",
	"settings.about.payloadOk_one": "已校验 {n} 个文件",
	"settings.about.payloadOk_other": "已校验 {n} 个文件",
	"settings.about.platform": "平台",
	"settings.about.reconciler": "对账器",
	"settings.about.reconcilerRunning": "运行中，上次检查 {when}",
	"settings.about.reconcilerStopped": "未运行",
	"settings.about.status": "状态",
	"settings.about.status.degraded": "降级",
	"settings.about.status.error": "错误",
	"settings.about.status.healthy": "健康",
	"settings.about.storage": "存储",
	"settings.about.storageBad": "架构 {schema}，完整性异常",
	"settings.about.storageOk": "架构 {schema}，完整性正常",
	"settings.about.studioVersion": "Studio App 版本",
	"settings.about.title": "关于此 App",
	"settings.about.toolFound": "{name} {version}",
	"settings.about.toolMissing": "未找到 {name}",
	"settings.about.tools": "工具",
	"settings.about.updateChip": "由 KiroCrew 管理",
	"settings.about.updateDesc": "App 的安装与更新由 KiroCrew 负责。Studio 不会自行检查更新。",
	"settings.about.updateState": "更新状态",
	"settings.advisor.autoDraft.count_one": "已为 {n} 个仓库开启提前起草",
	"settings.advisor.autoDraft.count_other": "已为 {n} 个仓库开启提前起草",
	"settings.advisor.autoDraft.desc": "对这里勾选的仓库，只要出现问题或关卡，Studio 就立刻请顾问起草——在你打开它之前——建议已经等在那里，不必再等大约三分钟。它依然只读：不能作答、不能批准、也不能提交；遇到问题时，它把答案填进表单，交给你阅读和修改。每张卡片都会真实消耗模型用量，所以这是按仓库授予的，未授予前一直关闭。取消勾选只停止之后的起草，已经生成的草稿不受影响。",
	"settings.advisor.autoDraft.label": "在 {repo} 提前起草",
	"settings.advisor.autoDraft.none": "还没有注册任何仓库。",
	"settings.advisor.autoDraft.orphan": "{id} 已获授权提前起草，但已不在注册列表中。",
	"settings.advisor.autoDraft.title": "按仓库提前起草",
	"settings.advisor.desc": "顾问按需起草与解释。它无法写文件、推动工作流，也无法产生 HUMAN_TURN。",
	"settings.advisor.label": "在决策处提供顾问",
	"settings.advisor.model.chip": "继承",
	"settings.advisor.model.desc": "通过 KiroCrew 的角色解析继承。Studio 不硬编码任何模型 id，也不显示模型 id。",
	"settings.advisor.model.title": "顾问模型",
	"settings.advisor.title": "顾问可用性",
	"settings.bun.auto": "使用自动探测",
	"settings.bun.checking": "正在检查 Bun…",
	"settings.bun.desc": "指定 gateway 所在电脑上的 Bun 路径，或重新探测已安装的 Bun。修改立即生效，无需重启 Studio。",
	"settings.bun.missing": "未找到 Bun。请指定路径，或安装后重新探测。",
	"settings.bun.path": "Bun 的绝对路径",
	"settings.bun.probe": "重新探测 Bun",
	"settings.bun.ready": "Bun 已就绪。",
	"settings.bun.save": "保存并校验路径",
	"settings.bun.searched": "已检查：{paths}",
	"settings.bun.title": "Bun 可执行文件",
	"settings.concurrency.desc": "同时可以运行回合的仓库数量。单个仓库的执行数恒为 1，无法提高。",
	"settings.concurrency.label": "同时运行的仓库数",
	"settings.concurrency.title": "全局并发",
	"settings.concurrency.value_one": "{n} 个仓库",
	"settings.concurrency.value_other": "{n} 个仓库",
	"settings.creditCap.desc": "从现有信号无法观测额度消耗，因此无法执行上限；它被禁用而不是被估算。",
	"settings.creditCap.title": "额度上限",
	"settings.dashboard.desc": "KiroCrew 侧栏上的计数以及页面内的横幅。",
	"settings.dashboard.label": "显示仪表盘通知",
	"settings.dashboard.title": "仪表盘通知",
	"settings.density.comfortable": "宽松",
	"settings.density.compact": "紧凑",
	"settings.density.desc": "默认为紧凑。宽松只增加纵向留白，不改变显示的内容。",
	"settings.density.label": "密度",
	"settings.density.title": "显示密度",
	"settings.diagnostics.humanRetention.desc": "你提交的原文总共保留多久。1 到 30 天。",
	"settings.diagnostics.humanRetention.label": "提交文本的保留天数",
	"settings.diagnostics.humanRetention.title": "提示正文保留期",
	"settings.diagnostics.humanText.desc": "默认关闭。关闭时，无论导出如何请求，后端都会拒绝把你提交的文字放进任何导出。",
	"settings.diagnostics.humanText.label": "允许在诊断导出中包含提示正文",
	"settings.diagnostics.humanText.title": "导出中的提示正文",
	"settings.diagnostics.retention.desc": "Studio 保留自身活动记录与事务记录的时长。",
	"settings.diagnostics.retention.label": "保留天数",
	"settings.diagnostics.retention.title": "诊断保留期",
	"settings.diagnostics.retention.value_one": "{n} 天",
	"settings.diagnostics.retention.value_other": "{n} 天",
	"settings.doctor.desc": "在安装或升级提交后运行一次 AI-DLC 工作区 doctor。它只读取，不写入任何内容。",
	"settings.doctor.label": "在安装或升级后运行 doctor",
	"settings.doctor.title": "安装后运行 doctor",
	"settings.locale.auto": "跟随仪表盘",
	"settings.locale.desc": "英文与简体中文均完整提供并经过一致性测试。AI-DLC 自己的文字——阶段标识、问题、产物、评审结论与审计字段——绝不翻译。",
	"settings.locale.en-US": "English (en-US)",
	"settings.locale.following": "正在跟随仪表盘 —— 当前为 {locale}。",
	"settings.locale.label": "界面语言",
	"settings.locale.overridden": "仅为 Studio 覆盖仪表盘的选择。",
	"settings.locale.title": "语言",
	"settings.locale.zh-CN": "简体中文 (zh-CN)",
	"settings.night.desc": "无人值守的自动推进不可用。KiroCrew 通往智能体的每条路径都会产生受保护的 HUMAN_TURN，因此机器派发会在 AI-DLC 审计轨迹中伪造人类在场。",
	"settings.night.end": "结束",
	"settings.night.start": "开始",
	"settings.night.storedOnly": "该时段会被保存，以便将来证明存在可信机器通道时可直接使用。目前它不会触发任何行为。",
	"settings.night.title": "夜间工作时段",
	"settings.night.window": "本地时间 {start} – {end}",
	"settings.page.error": "无法读取设置：{message}",
	"settings.page.lede": "Studio 自己的偏好设置。这里的任何设置都不会改动 AI-DLC 的文件。",
	"settings.page.reading": "正在读取设置…",
	"settings.page.retry": "重新读取",
	"settings.page.saveFailed": "这项更改未保存：{message}",
	"settings.page.saveFailedKey": "这项更改未保存。后端拒绝了 {key}：{message}",
	"settings.page.saved": "已保存。",
	"settings.page.saving": "正在保存…",
	"settings.page.status": "设置状态",
	"settings.page.title": "设置",
	"settings.queue.desc": "操作中心如何排列等待中的事项。“优先级”会把恢复与投递不确定排在最前。",
	"settings.queue.label": "队列排序方式",
	"settings.queue.localNote": "该选择也会保存在此浏览器中，新标签页会保持你上次的选择。",
	"settings.queue.oldest": "最早优先",
	"settings.queue.priority": "优先级",
	"settings.queue.repo": "仓库",
	"settings.queue.title": "队列组织方式",
	"settings.queue.type": "操作类型",
	"settings.reason.credits_unobservable": "额度消耗无法观测",
	"settings.reason.host_capability_unavailable": "宿主报告其不可用",
	"settings.reason.host_capability_unknown": "宿主没有报告此能力",
	"settings.reason.host_not_attached": "Studio 尚未接入仪表盘",
	"settings.reason.host_seam_unavailable": "宿主没有提供相应接口",
	"settings.reason.machine_lane_unavailable": "尚未证明存在可信的机器通道",
	"settings.reason.other": "{raw}",
	"settings.reason.s12_unproven": "尚未证明存在可信的机器通道",
	"settings.reason.s1_s2_unverified": "分组答案的传输方式尚未验证",
	"settings.section.about": "关于",
	"settings.section.advisor": "顾问",
	"settings.section.automation": "自动化与预算",
	"settings.section.diagnostics": "诊断",
	"settings.section.locale": "语言与显示",
	"settings.section.notifications": "通知",
	"settings.section.queue": "操作队列",
	"settings.slack.desc": "仅在阻塞事件时发送：关卡、问题、执行失败、投递不确定与完成摘要。发送目标是已配置的 KiroCrew 所有者私信。",
	"settings.slack.label": "发送 Slack 通知",
	"settings.slack.mute.count_one": "已静音 {n} 个仓库",
	"settings.slack.mute.count_other": "已静音 {n} 个仓库",
	"settings.slack.mute.desc": "被静音的仓库依然会在这里排队其操作，只是不发送 Slack 消息。",
	"settings.slack.mute.label": "静音 {repo}",
	"settings.slack.mute.none": "还没有注册任何仓库。",
	"settings.slack.mute.orphan": "{id} 已静音，但已不在注册列表中。",
	"settings.slack.mute.title": "已静音的仓库",
	"settings.slack.quickActions.desc": "在 Slack 内做决定需要宿主提供的接口，而该接口并不存在，因此每条 Slack 消息只会深链回到这里。",
	"settings.slack.quickActions.title": "Slack 快捷操作",
	"settings.slack.title": "Slack 通知",
	"settings.turnCap.desc": "在派发前检查，绝不打断正在进行的回合。会被保存，但在时段不可用期间不生效。",
	"settings.turnCap.label": "每个时段的回合数",
	"settings.turnCap.title": "每个时段的回合上限",
	"settings.unavailable": "不可用",
	"settings.unavailableWhy": "不可用 —— {reason}",
	"shell.a11y.primaryNav": "主导航",
	"shell.a11y.queueCount_one": "{n} 项待你处理",
	"shell.a11y.queueCount_other": "{n} 项待你处理",
	"shell.backToQueue": "返回队列",
	"shell.banner.degraded_one": "Studio 报告自身环境存在 {n} 个问题。工作流状态仍然从磁盘读取。",
	"shell.banner.degraded_other": "Studio 报告自身环境存在 {n} 个问题。工作流状态仍然从磁盘读取。",
	"shell.banner.openSettings": "打开设置",
	"shell.banner.reload": "重新加载",
	"shell.banner.sessionExpired": "控制台会话已过期。没有任何内容被发送。请重新加载页面以重新登录。",
	"shell.brand": "AI-DLC Studio",
	"shell.error.nothingSent": "没有任何内容被发送，也没有记录任何决定。重试只会重新渲染此视图；磁盘上的证据没有变化。",
	"shell.error.title": "{where} 无法渲染",
	"shell.format.bytes": "{n} B",
	"shell.format.kb": "{n} KB",
	"shell.format.listJoin": "、",
	"shell.format.mb": "{n} MB",
	"shell.format.range": "{low} – {high}",
	"shell.format.ratio": "{used} / {cap}",
	"shell.notBuilt.body": "此版本尚未包含 Studio 的这一部分。没有任何内容被隐藏 — 该视图还不存在。",
	"shell.notBuilt.title": "{view} 尚未实现",
	"shell.strip.alerts_one": "{n} 项警报",
	"shell.strip.alerts_other": "{n} 项警报",
	"shell.strip.circuits_one": "{n} 个断路器已打开",
	"shell.strip.circuits_other": "{n} 个断路器已打开",
	"shell.strip.clear": "没有需要关注的事项",
	"shell.strip.collapse": "隐藏执行状态",
	"shell.strip.critical_one": "{n} 项恢复决定",
	"shell.strip.critical_other": "{n} 项恢复决定",
	"shell.strip.expand": "显示执行状态",
	"shell.strip.leases_one": "持有 {n} 个仓库租约",
	"shell.strip.leases_other": "持有 {n} 个仓库租约",
	"shell.strip.nightOff": "夜间窗口已关闭",
	"shell.strip.nightOn": "夜间窗口 {start} – {end}",
	"shell.strip.polling": "正在轮询更新",
	"shell.strip.pollingWhy": "实时事件流未连接，Studio 正在按定时重新读取。不会遗漏任何内容，只是更新更慢。",
	"shell.strip.running_one": "{n} 个回合正在运行",
	"shell.strip.running_other": "{n} 个回合正在运行",
	"template.a11y.decision": "{type} 决策。{repo}，{intent}，阶段 {stage}。状态：{state}。",
	"template.budget.choices": "你可以做什么",
	"template.budget.creditNote": "现有的 Kiro 信号无法观测额度消耗，因此无法执行额度上限。它被禁用，而不是被估算。",
	"template.budget.credits": "额度",
	"template.budget.effect": "这会阻塞什么",
	"template.budget.effectBody": "该 intent 保持排队。提高上限，或者现在手动运行它，是让它推进的唯一方式。",
	"template.budget.localTime": "本地时间",
	"template.budget.neverInterrupts": "正在运行的一轮不会被打断",
	"template.budget.noBudget": "该事项没有携带预算数字。Studio 不会从其他地方推断它们。",
	"template.budget.noSession": "未绑定会话",
	"template.budget.notObservable": "无法观测",
	"template.budget.notOnCard": "该事项未记录",
	"template.budget.openSettings": "打开预算设置",
	"template.budget.queueDepth": "队列 {n}",
	"template.budget.state": "预算状态",
	"template.budget.turns": "已用轮次",
	"template.budget.window": "时间窗",
	"template.command.choices": "你可以做什么",
	"template.command.dispatch": "将要派发的内容",
	"template.command.lease": "每个仓库同时只跑一轮",
	"template.command.noSession": "未绑定会话",
	"template.command.noSessionSub": "Studio 会在派发前绑定会话，绑定不成功就拒绝派发。",
	"template.command.note": "Studio 只派发一轮，然后观察磁盘状态，不会自行连续派发。",
	"template.command.session": "规范会话",
	"template.command.sessionSub": "{state} · 最后一轮 {turn}",
	"template.command.stage": "阶段",
	"template.command.stageSub": "引擎将从磁盘记录的这个位置继续。",
	"template.command.superseded": "流程已进入检查点或后续阶段，这条尚未发送的运行请求已关闭。请查看当前待办。",
	"template.common.brief": "决策摘要",
	"template.common.closedBody": "该操作已关闭。请查看当前待办，了解任务现在需要处理的事项。",
	"template.common.closedTitle": "历史记录",
	"template.common.conflict": "冲突",
	"template.common.consequenceFallback": "本版本没有这项决策的后果说明。",
	"template.common.findingFallback": "AI-DLC 的文件在此处互相矛盾。下面的证据列出了相关文件。",
	"template.common.headlineFallback": "该事项在等你处理。本版本没有它的摘要文案。",
	"template.common.met": "已满足",
	"template.common.nextConsequence": "接下来会发生什么。",
	"template.common.noAutoChoice": "Studio 只执行你在下方操作栏中选择的那一项，绝不会为了让证据自圆其说而替你选择。",
	"template.common.notMet": "未满足",
	"template.common.originalNotice": "创建这条记录时的通知",
	"template.common.refreshing": "该事项依据的证据在读取过程中发生了变化，Studio 重新读取之前不能发送任何内容。",
	"template.common.tooLong": "内容超过 Studio 允许发送的长度（{max} 个字符），请先精简。",
	"template.common.unknownMet": "Studio 不做判定",
	"template.failure.breakerClosed": "熔断关闭",
	"template.failure.breakerOpen": "熔断已打开 — 不再派发",
	"template.failure.choices": "你可以做什么",
	"template.failure.colAt": "时间",
	"template.failure.colAttempt": "#",
	"template.failure.colBackoff": "退避",
	"template.failure.colOutcome": "结果",
	"template.failure.count": "{n} 次同类失败",
	"template.failure.findings": "还有哪些地方存在分歧",
	"template.failure.fingerprint": "指纹 {fingerprint} · 类别 {cls}",
	"template.failure.history": "重试与退避历史",
	"template.failure.historyCaption": "每次尝试的时间、Studio 等待的时长以及结果。",
	"template.failure.log": "会话日志片段",
	"template.failure.logNote": "这是 Studio 捕获到的引擎原始输出。Studio 不解释它，也不会把它发送到任何地方。",
	"template.failure.logPane": "引擎 stderr",
	"template.failure.normalized": "归一化后的错误",
	"template.failure.session": "会话状态",
	"template.failure.sessionSub": "{state} · 停止状态 {stop} · 最后一轮 {turn}",
	"template.failure.summaryUnknown": "引擎失败了，但没有归类后的摘要。下面是 Studio 捕获到的输出。",
	"template.gate.compare": "产出物与评审",
	"template.gate.criteria": "阶段验收标准 — 已满足 {met} / {total}",
	"template.gate.criteriaUnknown": "阶段验收标准 — 共 {total} 条，Studio 不对其做判定",
	"template.gate.feedback": "要求修改时的说明",
	"template.gate.feedbackLabel": "修改要求说明",
	"template.gate.feedbackPlaceholder": "写明需要修改什么。说明为空时无法发送修改要求。",
	"template.gate.feedbackRouting": "Studio 会把它作为你的发言发送到规范会话，绝不会去改动产出物本身。",
	"template.gate.history": "历史修订",
	"template.gate.noArtifact": "没有产出物",
	"template.gate.noArtifactBody": "该阶段没有记录任何产出物。这本身就是证据的一部分。",
	"template.gate.noBlockers": "没有未解决的阻塞项",
	"template.gate.noFindings": "评审没有为该阶段记录任何发现。",
	"template.gate.noReview": "该阶段没有评审执行过，因此没有可引用的发现。",
	"template.gate.reviewPasses": "评审轮次",
	"template.gate.reviewPassesSub": "每次修订都会重新执行评审。",
	"template.gate.reviewer": "评审发现",
	"template.gate.revisions": "修订次数",
	"template.gate.revisionsSub": "记录在该 intent 的状态文件中。",
	"template.gate.risks": "未解决的风险",
	"template.install.action.conflict": "与本地文件冲突",
	"template.install.action.create": "将被创建",
	"template.install.action.engine_modified": "由引擎重写",
	"template.install.action.identical": "完全相同",
	"template.install.action.merge_conflict": "合并目标冲突",
	"template.install.action.merge_create": "合并目标将被创建",
	"template.install.action.merge_identical": "合并目标未改动",
	"template.install.action.merge_update": "合并目标将被更新",
	"template.install.action.owned_identical": "安装以来未改动",
	"template.install.action.owned_modified": "安装以来被本地修改",
	"template.install.action.retire": "将被停用",
	"template.install.action.retire_blocked": "无法停用",
	"template.install.action.shell_create": "工作区文件将被创建",
	"template.install.action.shell_exists": "工作区文件已存在",
	"template.install.bundled": "Studio 内置版本",
	"template.install.bundledSub": "当前 Studio 构建会安装的版本。",
	"template.install.colHash": "哈希",
	"template.install.colOwnership": "归属",
	"template.install.colPath": "路径",
	"template.install.colState": "状态",
	"template.install.diffLabel": "{path} 的差异",
	"template.install.drift": "受管文件差异",
	"template.install.driftCaption": "每个受管文件及其归属、比对结果和哈希。",
	"template.install.fixNoMerge": "Studio 不会替你合并。默默解决这种冲突的安装器会破坏使恢复成为可能的凭据契约。",
	"template.install.fixRevert": "把列出的文件恢复为凭据记录的内容，然后重新运行升级预览。",
	"template.install.fixStay": "或者保留本地修改、继续留在已安装的版本上；intent 会继续依据它运行。",
	"template.install.hashLive": "磁盘 {hash}",
	"template.install.hashPayload": "内置 {hash}",
	"template.install.hashReceipt": "凭据 {hash}",
	"template.install.installed": "已安装的引擎",
	"template.install.managed": "受管文件",
	"template.install.managedSub": "每个文件都按记录的哈希比对，而不是按时间戳。",
	"template.install.managedValue": "已比对 {n} 个 · {conflicts} 个冲突",
	"template.install.noDrift": "没有受管文件与记录内容不一致。",
	"template.install.noReceipt": "没有凭据覆盖这次安装，因此 Studio 无法证明哪些文件属于它。",
	"template.install.openRepo": "在「Repos」中打开该仓库",
	"template.install.ownership": "凭据归属",
	"template.install.receiptSub": "由凭据 {id} 记录，逐个文件记录版本、路径和 SHA-256。",
	"template.install.remediation": "接下来可以怎么做",
	"template.install.showDiff": "查看 {path} 的差异",
	"template.install.stoppedBody": "仓库保留完整的安装和凭据。一次不完整的升级比停留在旧版本更糟。",
	"template.install.stoppedLabel": "什么都没有写入。",
	"template.install.why": "为什么这算冲突",
	"template.install.whyBody": "这些路径属于已记录的安装，而磁盘上的内容已经与凭据记录的哈希不一致。Studio 无法判断你想保留本地修改还是内置版本。",
	"template.missingInput.adminLane": "管理租约",
	"template.missingInput.blocked": "该 intent 的所有派发都会被拒绝。后面不会排队，也不会有任何假设。",
	"template.missingInput.blockedLabel": "在此之前。",
	"template.missingInput.choices": "你可以做什么",
	"template.missingInput.cursorSrc": "活动 intent",
	"template.missingInput.cursorSub": "space {space}",
	"template.missingInput.findings": "还有哪些地方存在分歧",
	"template.missingInput.input.free_text": "要发送的内容",
	"template.missingInput.input.scope": "要发送的 scope",
	"template.missingInput.openIntents": "打开 Intents 页面",
	"template.missingInput.pickBody": "活动 intent 通过 AI-DLC 引擎操作、在仓库管理租约下从磁盘上已有的 intent 中选定。Studio 不会替你猜，请在「Intents」页面选择。",
	"template.missingInput.pickTitle": "选择活动 intent",
	"template.missingInput.placeholder.free_text": "引擎向你索取的内容。",
	"template.missingInput.placeholder.scope": "用一行描述该 intent 覆盖的范围。",
	"template.missingInput.reason.dangling_cursor": "AI-DLC 的活动 intent 指向一个不存在的目录，因此任何派发都无法指向它。",
	"template.missingInput.reason.missing_scope": "该 intent 的状态文件没有 Scope，引擎在规划或运行前需要它。",
	"template.missingInput.reason.no_cursor": "该 space 中 AI-DLC 没有活动 intent，派发没有可推进的对象。",
	"template.missingInput.reasonUnknown": "在该 intent 继续之前，Studio 需要你提供一些信息。",
	"template.missingInput.routing": "Studio 会把它作为你的发言发送到规范会话，不会自行写入状态文件。",
	"template.missingInput.source": "Studio 查看过的位置",
	"template.missingInput.stateSub": "当前阶段 {stage} · 读取于 {at}",
	"template.missingInput.what": "缺少什么",
	"template.questions.answered": "已作答",
	"template.questions.auditSource": "此检查点来自 AI-DLC 审计记录。你的回复将按原文发送到已绑定的会话。",
	"template.questions.checkpoint.plan_approval": "计划批准",
	"template.questions.checkpoint.plan_approval.body": "引擎在执行之前，要你批准它编排出的计划。",
	"template.questions.checkpoint.summary_confirmation": "摘要确认",
	"template.questions.checkpoint.summary_confirmation.body": "引擎在继续之前，要确认它对该阶段的摘要是否正确。",
	"template.questions.checkpointAnswered": "已记录的答案：{answer}",
	"template.questions.degradedBody": "此题组暂时无法通过表单安全提交：题单格式可能不受支持，或会话正在等待原生问题的回答。请在原会话中回答。",
	"template.questions.degradedLabel": "降级模式。",
	"template.questions.degradedTitle": "结构化作答不可用",
	"template.questions.drafted": "可以发送",
	"template.questions.feedback": "要求修改时的说明",
	"template.questions.feedbackLabel": "针对该检查点的修改要求说明",
	"template.questions.fileSource": "请在下方逐题选择。选项来自已保存的题单，确认后会整组发送到已绑定的会话。",
	"template.questions.freeText": "输入正文",
	"template.questions.group": "问题组 — 一起作答",
	"template.questions.looksCorrect": "摘要正确",
	"template.questions.looksCorrectHint": "按原文确认引擎的摘要，让它继续往下走。",
	"template.questions.neverEdits": "Studio 绝不会修改 {path} 或它的摘要值。在送达证据与状态证据证明答案被接受之前，这组问题会一直显示。",
	"template.questions.none": "该事项没有携带问题。这组问题可能已经作答过了。",
	"template.questions.openConversation": "打开会话",
	"template.questions.otherDesc": "自由文本，按你输入的内容原样保留。",
	"template.questions.otherLabel": "第 {index} 题的自填答案",
	"template.questions.otherPlaceholder": "你的答案",
	"template.questions.pending": "{total} 题中有 {n} 题未作答",
	"template.questions.recorded": "已记录的答案",
	"template.questions.required": "必答",
	"template.questions.selectAny": "可多选",
	"template.questions.selectOne": "单选",
	"template.questions.summaryChanges": "摘要需要修改",
	"template.questions.summaryChangesHint": "发送你的说明而不是确认。下面的说明为必填。",
	"template.questions.summaryChoiceLabel": "你对该检查点的回应",
	"template.questions.textPlaceholder": "填写你想记录的笔记内容。",
	"template.questions.textStillRequired": "上次只发送了输入标签，没有记录笔记。该次操作已结束，请打开当前待办填写笔记正文。",
	"template.questions.unsupportedBody": "此题单包含尚未作答的内容，Studio 无法将其安全对应到表单问题。请在原会话中阅读并回答。",
	"template.questions.unsupportedPending": "另有 {n} 处回答待填写",
	"template.recovery.auditSub": "{shards} 个审计分片 · {complete}",
	"template.recovery.bootRestarted": "网关重启过，因此发送中的消息可能已丢失。",
	"template.recovery.bootRestartedConfirmed": "网关曾重启。消息送达已确认，工作流仍待核对。",
	"template.recovery.bootSame": "网关没有重启。",
	"template.recovery.bootUnknown": "网关是否重启过没有被记录。",
	"template.recovery.boundary": "最后一个稳定边界",
	"template.recovery.boundarySrc": "边界",
	"template.recovery.boundarySub": "记录于 {at}。上下文可以从磁盘在该点重建。",
	"template.recovery.boundaryUnknown": "没有记录到稳定边界，因此无法据此重建上下文。",
	"template.recovery.choices": "恢复方式",
	"template.recovery.complete": "已完整读取",
	"template.recovery.confirmedAlert": "消息已确认送达。该 intent 的工作流仍需核对，回答是否被接受尚未验证；系统不会自动重放。",
	"template.recovery.confirmedBody": "消息已送达会话。仍需通过 AI-DLC 审计、轮次标记和游标确认工作流如何处理了这条消息。同一条消息不会被重新发送。",
	"template.recovery.confirmedTitle": "工作流核对",
	"template.recovery.contradiction": "矛盾之处",
	"template.recovery.contradictionBody": "Studio 请求宿主投递你的决策，但没有拿到可信的答复。重复批准会让该阶段推进两次，因此系统不会自行重试。",
	"template.recovery.contradictionChecks": "{row} {disk} {boot}",
	"template.recovery.cursorMismatch": "当前活动 intent 不一致：{fields}",
	"template.recovery.cursorOk": "当前活动 intent 正是这项决策所属的 intent。",
	"template.recovery.deliverySub": "会话已确认收到：{confirmed} · {at}",
	"template.recovery.directiveDiffers": "与状态文件不一致。",
	"template.recovery.directiveMatches": "与状态文件一致。",
	"template.recovery.diskChanged": "Studio 发送前记录的文件已经发生变化。",
	"template.recovery.diskUnchanged": "Studio 发送前记录的文件没有变化。",
	"template.recovery.diskUnknown": "那些文件是否变化没有被记录。",
	"template.recovery.evidence": "来自每个来源的证据",
	"template.recovery.findings": "存在分歧的地方",
	"template.recovery.gitClean": "工作区干净。Studio 没有执行任何 Git 写操作。",
	"template.recovery.gitDirty": "工作区有 {n} 个文件被改动。Studio 没有执行任何 Git 写操作。",
	"template.recovery.incomplete": "未能完整读取",
	"template.recovery.manageConversation": "前往 Intents 管理会话",
	"template.recovery.markerSub": "最后一次人类发言 {at} · 在场证据 {presence}",
	"template.recovery.no": "否",
	"template.recovery.outcome.confirmed": "会话已收到消息",
	"template.recovery.outcome.delivered": "宿主已接受",
	"template.recovery.outcome.not_delivered": "宿主已拒绝",
	"template.recovery.outcome.uncertain": "发送始终未被确认",
	"template.recovery.presenceFailed": "与预期不一致",
	"template.recovery.presenceOk": "与预期一致",
	"template.recovery.presenceUnknown": "尚未比对",
	"template.recovery.revisions": "已记录 {n} 次修订",
	"template.recovery.risk.admin": "持有仓库管理租约",
	"template.recovery.risk.host_control": "控制 KiroCrew 会话",
	"template.recovery.risk.human_lane": "把你的发言发送到规范会话",
	"template.recovery.risk.read": "只读决策",
	"template.recovery.risk.studio_only": "只改动 Studio 自己的记录",
	"template.recovery.rowAbsent": "会话中没有找到匹配的发言。",
	"template.recovery.rowFound": "会话中在 {at} 存在一条匹配的发言。",
	"template.recovery.sessionIdle": "空闲",
	"template.recovery.sessionMissing": "创建这条通知时，记录中的会话不可用。",
	"template.recovery.sessionRepair": "请打开任务的会话设置，绑定新的或已有的 aidlc 会话。确认仅关闭这条通知。",
	"template.recovery.sessionRunning": "正在运行",
	"template.recovery.sessionSub": "{state} · 队列 {queue} · 占用原因：{reasons}",
	"template.recovery.src.audit": "AI-DLC 审计",
	"template.recovery.src.cursor": "游标回读",
	"template.recovery.src.delivery": "Studio 送达记录",
	"template.recovery.src.directive": "AI-DLC 指令",
	"template.recovery.src.git": "Git 观测",
	"template.recovery.src.marker": "轮次标记",
	"template.recovery.src.session": "KiroCrew 会话",
	"template.recovery.src.state": "AI-DLC 状态",
	"template.recovery.turnCounter": "第 {n} 轮",
	"template.recovery.uncertainAlert": "这次送达无法证明成功或失败。系统不会自动重放；在证据一致之前，该 intent 保持阻塞。",
	"template.recovery.yes": "是",
	"unavailable.machineLane": "不可用 — 尚未验证机器通道（S12）",
	"wizard.a11y.stepper": "新建意图步骤",
	"wizard.advisor.applied": "已按 Advisor 的建议填好。尚未创建任何内容——按下「Create」之前请逐项核对，可以随意修改。",
	"wizard.advisor.ask": "让 Advisor 推荐这些设置",
	"wizard.advisor.askAgain": "重新询问",
	"wizard.advisor.clear": "清除这份建议",
	"wizard.advisor.dropped_one": "有 {n} 项 stage 改动被引擎拒绝，未被采用；拒绝原因见上方建议的 stage 改动。",
	"wizard.advisor.dropped_other": "有 {n} 项 stage 改动被引擎拒绝，未被采用；拒绝原因见上方建议的 stage 改动。",
	"wizard.advisor.keep": "未给出建议——保持 {value}",
	"wizard.advisor.lede": "不想逐项回答？Advisor 可以阅读你的目标和这个仓库，推荐 scope、depth、测试策略、评审上限和要走的 stage。在你确认之前不会套用任何内容。",
	"wizard.advisor.needObjective": "请先在「Work」一步写下目标——Advisor 读的就是它。",
	"wizard.advisor.proposal": "Advisor 的建议",
	"wizard.advisor.stages": "相对该 scope 默认选择的 stage 改动",
	"wizard.advisor.stale.objective": "这份建议起草之后目标已经改变。请重新询问，让 Advisor 读取新的目标。",
	"wizard.advisor.stale.scope": "这份建议起草之后 scope 已经改变；它的 stage 改动是按 {scope} 计算的。请重新选择 {scope}，或重新询问。",
	"wizard.advisor.unresolved_one": "有 {n} 个回答无法对应到设置，已保持原样。",
	"wizard.advisor.unresolved_other": "有 {n} 个回答无法对应到设置，已保持原样。",
	"wizard.advisor.use": "采用这份建议",
	"wizard.advisor.useNote": "会填入下面的字段。你可以随意修改；按下「Create」之前不会创建任何东西。",
	"wizard.created.again": "再创建一个意图",
	"wizard.created.body": "没有运行任何东西。AI-DLC 写入了意图记录，Studio 又重新读取磁盘确认它确实存在。",
	"wizard.created.intent": "{repo} 中的 {intent}",
	"wizard.created.next": "打开“意图”页面来运行它",
	"wizard.created.title": "意图已创建，并保持暂停",
	"wizard.created.transaction": "事务 {id}",
	"wizard.created.unverified": "Studio 无法核对记录的每一部分。运行之前请先打开这个意图查看。",
	"wizard.created.verified": "已在磁盘上核对",
	"wizard.creating": "正在创建意图…",
	"wizard.error.create": "意图没有被创建。",
	"wizard.error.preview": "无法计算计划。",
	"wizard.error.retry": "重试",
	"wizard.lede": "四个步骤。在你按下“创建”之前，仓库里不会写入任何内容；而“创建”本身也从不启动执行。",
	"wizard.nav.back": "上一步",
	"wizard.nav.continue": "继续",
	"wizard.nav.create": "创建意图（保持暂停）",
	"wizard.partial.activate": "重试会激活此意图，让引擎编译正确的运行图。",
	"wizard.partial.body": "{intent} 已经存在。可为此意图重试编译，无需再创建一个。尚未启动工作流回合。",
	"wizard.partial.open": "打开已创建的意图",
	"wizard.partial.planComposition.body": "{intent} 已经存在，但尚未应用所选的阶段变更。请打开该意图检查并修正计划，再编译运行图或启动工作流。",
	"wizard.partial.planComposition.open": "打开意图检查计划",
	"wizard.partial.planComposition.title": "意图已创建，计划需要修正",
	"wizard.partial.repairing": "正在编译运行图…",
	"wizard.partial.retry": "重试编译运行图",
	"wizard.partial.title": "意图已创建，运行图需要修复",
	"wizard.plan.lead": "这里列出每一个已知阶段，包括被预设排除的阶段。引擎不允许改动的阶段会被禁用，并说明原因。",
	"wizard.plan.summary": "{stages} 个阶段、{gates} 个 Gate、{artifacts} 个产物",
	"wizard.preset.depth": "深度",
	"wizard.preset.depth.Comprehensive": "提问更多，产物更长。",
	"wizard.preset.depth.Minimal": "每个阶段只走一遍，提问较少。",
	"wizard.preset.depth.Standard": "在引擎需要你输入时提问。",
	"wizard.preset.depthHelp": "决定提问数量和产物的详细程度。",
	"wizard.preset.fromScope": "跟随范围",
	"wizard.preset.fromScopeDepth": "该范围没有指定，因此使用 {value}。",
	"wizard.preset.fromScopeUnset": "该范围没有指定，因此使用 AI-DLC 自己的默认值。",
	"wizard.preset.fromScopeValue": "该范围要求 {value}。",
	"wizard.preset.review": "评审上限",
	"wizard.preset.review.adversarial": "Gate 通过之前必须回应评审发现。",
	"wizard.preset.review.advisory": "评审者只给意见，不阻塞。",
	"wizard.preset.review.none": "不做评审。Gate 仍然需要你决定。",
	"wizard.preset.reviewHelp": "任何阶段可用的最强评审级别。它只会减少评审工作量。",
	"wizard.preset.scope": "范围",
	"wizard.preset.scopeHelp": "AI-DLC 的范围决定哪些已知阶段可用。列表由 Studio 从这个仓库读取。",
	"wizard.preset.scopeMetaDepth": "深度 {value}",
	"wizard.preset.scopeMetaProjectOwned": "由本项目定义",
	"wizard.preset.scopeMetaReview": "评审上限 {value}",
	"wizard.preset.scopeMetaTest": "测试 {value}",
	"wizard.preset.scopeResets": "更换范围会把阶段矩阵重置为该范围自身的选择。",
	"wizard.preset.scopeSelected": "已选 {total} 个阶段中的 {on} 个",
	"wizard.preset.scopeUnreadable": "Studio 无法从这个仓库读到范围列表，因此无法提供预设。",
	"wizard.preset.scopeUnselected": "选中它即可从已安装的阶段图读出确切数量。",
	"wizard.preset.test": "测试策略",
	"wizard.preset.test.Comprehensive": "在运营阶段增加性能验证。",
	"wizard.preset.test.Minimal": "只做冒烟覆盖。",
	"wizard.preset.test.Standard": "对生成的工作单元做单元加集成测试。",
	"wizard.preset.testHelp": "决定测试量，以及性能验证是否可用。",
	"wizard.preview.busy": "正在重新计算计划…",
	"wizard.preview.none": "还没有计算计划。",
	"wizard.preview.stale": "重新计算期间显示的是上一次的计划。",
	"wizard.review.blockedFields": "“工作”这一步还没填完。",
	"wizard.review.blockedFindings_one": "这个仓库报告了 {n} 条阻塞发现，解决之前不会创建任何东西。",
	"wizard.review.blockedFindings_other": "这个仓库报告了 {n} 条阻塞发现，解决之前不会创建任何东西。",
	"wizard.review.blockedInvalid": "AI-DLC 不会接受这样的计划。原因列在“计划”步骤里。",
	"wizard.review.consequence": "创建不会运行任何东西。意图创建后保持暂停；由你用“运行到下一个检查点”启动。Keep moving、预算和 Advisor 草稿都不会从别的意图继承过来。",
	"wizard.review.estimates": "估算值，附来源与置信度",
	"wizard.review.exact": "精确值，来自已安装的阶段图",
	"wizard.review.plan": "Studio 将要确认的计划",
	"wizard.review.planDigest": "计划指纹 {digest}",
	"wizard.review.planDigestWhy": "创建时会把这个指纹一起发送。如果已安装的阶段图或范围在这次预览之后发生了变化，AI-DLC 会拒绝，而不会创建一个你没看过的计划。",
	"wizard.review.products": "这个计划将产出的产物",
	"wizard.review.productsNone": "这个计划里没有任何阶段声明产物，通常意味着什么都没选。",
	"wizard.step.a11y": "第 {n} 步，{label}",
	"wizard.step.done": "已完成",
	"wizard.step.of": "第 {n} / 4 步",
	"wizard.step.plan": "计划",
	"wizard.step.preset": "预设",
	"wizard.step.review": "复核",
	"wizard.step.work": "工作",
	"wizard.title": "新建意图",
	"wizard.work.context": "引擎需要知道的背景",
	"wizard.work.contextHelp": "可选。跟在目标之后发送，中间空一行。",
	"wizard.work.contextPlaceholder": "现有购物车服务是 v3。支付服务商会重试一次。此流程中不允许创建账号。",
	"wizard.work.label": "标签",
	"wizard.work.labelDerived": "留空时，AI-DLC 会从目标推导出 {label}。",
	"wizard.work.labelHelp": "最多三个小写单词，用连字符连接。AI-DLC 会用它作为意图的目录名，之后无法重命名。",
	"wizard.work.labelInvalid": "最多三个小写单词，用连字符连接，例如 guest-checkout。",
	"wizard.work.labelPlaceholder": "guest-checkout",
	"wizard.work.labelUndeducible": "这个目标推导不出 AI-DLC 可用的标签，请自己填写一个。",
	"wizard.work.objective": "目标",
	"wizard.work.objectiveHelp": "一句话。引擎会把它作为这个意图的输入来读。",
	"wizard.work.objectivePlaceholder": "让未登录的顾客也能完成购买",
	"wizard.work.objectiveRequired": "必须填写目标：它就是交给引擎去做的事。",
	"wizard.work.projectType": "项目类型",
	"wizard.work.projectTypeHelp": "会传给引擎，用于判断条件阶段是否适用。不确定就留空。",
	"wizard.work.projectTypeUnset": "不指定",
	"wizard.work.repo": "仓库",
	"wizard.work.repoBlocked": "这个仓库无法承载新意图：{reason}",
	"wizard.work.repoBlockedOpen": "打开仓库页面",
	"wizard.work.repoHelp": "这里只列出你明确注册的仓库。",
	"wizard.work.repoNone": "还没有注册任何仓库，因此没有可以创建意图的位置。",
	"wizard.work.repoNotInstalled": "这里没有安装 AI-DLC",
	"wizard.work.repoPick": "选择一个仓库",
	"wizard.work.repoRecovery": "安装需要先修复",
	"wizard.work.repoUnusable": "{label} — {reason}",
	"wizard.work.signal.drift": "安装后有文件被修改",
	"wizard.work.signal.engine": "已安装 AI-DLC {version}",
	"wizard.work.signal.engineUnknown": "已安装的 AI-DLC 版本未知",
	"wizard.work.signal.gitBranch": "分支 {branch}",
	"wizard.work.signal.gitClean": "干净",
	"wizard.work.signal.gitDirty_one": "{n} 个未提交文件",
	"wizard.work.signal.gitDirty_other": "{n} 个未提交文件",
	"wizard.work.signal.gitUnavailable": "无法观察 Git",
	"wizard.work.signal.intents_one": "这里已有 {n} 个意图",
	"wizard.work.signal.intents_other": "这里已有 {n} 个意图",
	"wizard.work.signal.stages": "已安装的阶段图共 {n} 个阶段",
	"wizard.work.signals": "Studio 在这个仓库里观察到的事实",
	"wizard.work.signalsHelp": "从磁盘读取，这里不做修改。",
	"wizard.work.space": "空间",
	"wizard.work.spaceActive": "{space} — 当前",
	"wizard.work.spaceHelp": "AI-DLC 只在其当前空间中创建意图。其他空间会列出，但这里不能选。",
	"wizard.work.spaceInactive": "{space} — 非当前",
	"wizard.work.spaceUnknown": "Studio 还没有读到这个仓库的当前空间。",
	"workspace.depth.Comprehensive": "全面",
	"workspace.depth.Minimal": "最简",
	"workspace.depth.Standard": "标准",
	"workspace.refusal.autonomous": "自主构建期间无法更改范围。",
	"workspace.refusal.current_stage_removed": "此范围会移除当前阶段，请先完成该阶段再更改范围。",
	"workspace.refusal.no_changes": "选择不同设置以预览变更。",
	"workspace.refusal.open_boundary": "更改范围前请先处理待批准或修订的阶段。仍可更改深度和测试策略。",
	"workspace.refusal.unknown_stages": "记录中的阶段与已安装引擎的阶段图不匹配。",
	"workspace.settings.after": "变更后",
	"workspace.settings.applied": "已从磁盘核验设置。",
	"workspace.settings.apply": "应用设置",
	"workspace.settings.before": "变更前",
	"workspace.settings.command": "引擎命令",
	"workspace.settings.confirmBody": "这会激活所选 intent、更新 harness 上下文、修改设置并写入引擎审计。如果计划已变化，请刷新预览后重新确认。",
	"workspace.settings.confirmTitle": "应用此次预览？",
	"workspace.settings.depth": "深度",
	"workspace.settings.description": "预览此 intent 的工作流范围与投入深度变更。已完成阶段保留记录状态和产物。",
	"workspace.settings.execute": "执行",
	"workspace.settings.preserved": "{n} 个已完成阶段保留历史记录。",
	"workspace.settings.preview": "预览变更",
	"workspace.settings.review": "检查并确认",
	"workspace.settings.scope": "范围",
	"workspace.settings.selection": "此操作会激活 {space} / {intent}，确保设置与审计指向同一个 intent。",
	"workspace.settings.skip": "跳过",
	"workspace.settings.stage": "阶段",
	"workspace.settings.stageChanges": "阶段选择变更",
	"workspace.settings.test_strategy": "测试策略",
	"workspace.settings.title": "范围与深度",
	"workspace.spaces.active": "活动空间：{name}",
	"workspace.spaces.confirm": "确认",
	"workspace.spaces.confirmCreate": "以默认空间为基础，为 {name} 创建全新的团队记忆？当前活动空间保持不变。",
	"workspace.spaces.confirmSwitch": "将仓库与 harness 上下文切换到 {name}？有任务正在执行时将拒绝切换。",
	"workspace.spaces.confirmTitle": "确认空间变更",
	"workspace.spaces.create": "新建空间",
	"workspace.spaces.created": "空间已创建，活动空间保持不变。",
	"workspace.spaces.description": "空间分别保存 intent 和共享记忆。切换空间会改变仓库的活动空间与 harness 上下文。",
	"workspace.spaces.name": "新空间名称",
	"workspace.spaces.nameHint": "使用 1–48 个小写字母、数字和单个连字符，以字母开头。名称须唯一；help、list、create 等命令词为保留名称。",
	"workspace.spaces.refresh": "刷新空间",
	"workspace.spaces.switch": "切换空间",
	"workspace.spaces.switched": "已从磁盘核验活动空间。",
	"workspace.spaces.target": "目标空间",
	"workspace.spaces.title": "空间"
}, j = ["en-US", "zh-CN"], M = "en-US", N = "mc-lang", P = "aidlc-studio:locale", F = "aidlc-studio:organize", I = "(max-width: 767px)";
function L(e) {
	return (t) => {
		if (typeof MutationObserver > "u") return () => {};
		let n = new MutationObserver(t);
		return n.observe(document.documentElement, {
			attributes: !0,
			attributeFilter: e
		}), () => n.disconnect();
	};
}
function R() {
	let e = document.documentElement, t = e.dataset.mode;
	return t === "dark" || t === "light" ? t : (e.dataset.theme ?? "dark").includes("dark") ? "dark" : "light";
}
function ee() {
	return f(L(["data-mode", "data-theme"]), R, () => "dark");
}
function z(e) {
	if (!e) return null;
	let t = e.trim().toLowerCase();
	if (!t) return null;
	let n = j.find((e) => e.toLowerCase() === t);
	if (n) return n;
	let r = t.split("-")[0];
	return j.find((e) => e.split("-")[0] === r) ?? null;
}
function B(e) {
	try {
		return localStorage.getItem(e);
	} catch {
		return null;
	}
}
function V() {
	return z(B("aidlc-studio:locale")) ?? z(B(N)) ?? z(typeof document < "u" ? document.documentElement.lang : null) ?? (typeof navigator < "u" ? (navigator.languages ?? [navigator.language]).map(z).find(Boolean) ?? null : null) ?? M;
}
function te() {
	let e = i((e) => {
		let t = L(["lang"])(e);
		return window.addEventListener("storage", e), window.addEventListener("aidlc-studio:locale-changed", e), () => {
			t(), window.removeEventListener("storage", e), window.removeEventListener("aidlc-studio:locale-changed", e);
		};
	}, []);
	return f(e, V, () => M);
}
function ne(e) {
	try {
		e === null ? localStorage.removeItem(P) : localStorage.setItem(P, e);
	} catch {}
	window.dispatchEvent(new CustomEvent("aidlc-studio:locale-changed"));
}
function re() {
	let e = i((e) => {
		if (typeof window.matchMedia != "function") return () => {};
		let t = window.matchMedia(I);
		return t.addEventListener("change", e), () => t.removeEventListener("change", e);
	}, []);
	return f(e, () => typeof window.matchMedia == "function" && window.matchMedia(I).matches, () => !1);
}
function ie() {
	let e = i((e) => {
		if (typeof window.matchMedia != "function") return () => {};
		let t = window.matchMedia("(prefers-reduced-motion: reduce)");
		return t.addEventListener("change", e), () => t.removeEventListener("change", e);
	}, []);
	return f(e, () => typeof window.matchMedia == "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches, () => !1);
}
function ae(e = "/apps/aidlc-studio/ui/dist/style.css", t = "aidlc-studio-css") {
	if (typeof document > "u" || document.getElementById(t)) return;
	let n = document.createElement("link");
	n.id = t, n.rel = "stylesheet", n.href = e, document.head.appendChild(n);
}
//#endregion
//#region src/i18n/index.tsx
var oe = {
	"en-US": k,
	"zh-CN": A
}, se = t(null);
function ce(e, t, n) {
	return t ? e.replace(/\{([a-zA-Z0-9_]+)\}/g, (e, r) => {
		let i = t[r];
		return i === void 0 ? n || e : String(i);
	}) : e;
}
function le(e, t) {
	let n = new Intl.DateTimeFormat(e, { dateStyle: "medium" }), r = new Intl.DateTimeFormat(e, {
		dateStyle: "medium",
		timeStyle: "short"
	}), i = new Intl.NumberFormat(e), a = new Intl.RelativeTimeFormat(e, { numeric: "auto" }), o = new Intl.Collator(e, {
		numeric: !0,
		sensitivity: "base"
	}), s = (e) => {
		let t = new Date(e);
		return Number.isNaN(t.getTime()) ? null : t;
	};
	return {
		date: (e) => {
			let t = s(e);
			return t ? n.format(t) : e;
		},
		dateTime: (e) => {
			let t = s(e);
			return t ? r.format(t) : e;
		},
		duration: (e) => {
			let n = Math.max(0, Math.round(e));
			return n < 60 ? t("common.justNow") : n < 3600 ? t("common.durationMinutes", { n: Math.round(n / 60) }) : n < 86400 ? t("common.durationHours", { n: Math.round(n / 3600) }) : t("common.durationDays", { n: Math.round(n / 86400) });
		},
		since: (e) => {
			let t = s(e);
			if (!t) return e;
			let n = (t.getTime() - Date.now()) / 1e3, r = Math.abs(n);
			return r < 60 ? a.format(Math.round(n), "second") : r < 3600 ? a.format(Math.round(n / 60), "minute") : r < 86400 ? a.format(Math.round(n / 3600), "hour") : a.format(Math.round(n / 86400), "day");
		},
		number: (e) => i.format(e),
		compare: (e, t) => o.compare(e, t)
	};
}
function ue(e) {
	let t = oe[e] ?? oe["en-US"], n = oe["en-US"], r = t["common.unavailable"] ?? n["common.unavailable"] ?? "", i = (e, i) => ce(t[e] ?? n[e] ?? e, i, r);
	return {
		locale: e,
		t: i,
		has: (e) => e in t || e in n,
		fmt: le(e, i)
	};
}
function de({ children: e }) {
	let t = te(), n = l(() => ue(t), [t]);
	return /* @__PURE__ */ m(se.Provider, {
		value: n,
		children: e
	});
}
function H() {
	return a(se) ?? ue("en-US");
}
//#endregion
//#region src/lib/errorCodes.generated.ts
var fe = {
	action_not_found: 404,
	action_not_submittable: 409,
	action_stale: 409,
	advisor_unavailable: 503,
	already_installed: 409,
	answers_incomplete: 400,
	app_token_forbidden: 403,
	artifact_not_found: 404,
	bad_body: 400,
	bad_param: 400,
	bad_path: 400,
	breaker_open: 409,
	bun_missing: 503,
	cancel_not_safe: 409,
	cursor_mismatch: 409,
	delivery_ack_invalid: 409,
	draft_not_found: 404,
	duplicate_identity: 409,
	engine_unavailable: 503,
	feedback_required: 400,
	git_missing: 503,
	grouped_answers_unavailable: 409,
	host_submission_unavailable: 503,
	host_unavailable: 503,
	identity_unprovable: 409,
	illegal_transition: 409,
	install_conflict: 409,
	install_recovery_required: 409,
	intent_archived: 409,
	intent_not_found: 404,
	intent_paused: 409,
	internal_error: 500,
	internal_secret_invalid: 403,
	invalid_decision: 400,
	invalid_settings: 400,
	lease_lost: 409,
	legacy_layout: 409,
	machine_lane_unavailable: 409,
	migration_already_applied: 409,
	migration_not_applicable: 409,
	newer_installed: 409,
	not_delivered_unproven: 409,
	not_installed: 409,
	owner_required: 403,
	payload_degraded: 409,
	plan_invalid: 409,
	rate_limited: 429,
	rebind_not_allowed: 409,
	receipt_not_found: 404,
	recompose_not_allowed: 409,
	repo_busy: 409,
	repo_not_found: 404,
	repo_unavailable: 409,
	retry_not_allowed: 409,
	route_not_found: 404,
	run_not_applicable: 409,
	same_version_installed: 409,
	sensitive_path: 403,
	session_busy: 409,
	session_unbound: 409,
	slack_unavailable: 503,
	slot_busy: 409,
	slot_mismatch: 409,
	stage_not_found: 404,
	stale_generation: 409,
	state_inconsistent: 409,
	state_version_migration_unconfirmed: 409,
	storage_error: 500,
	storage_unavailable: 503,
	takeover_not_safe: 409,
	too_large: 413,
	too_many_repos: 429,
	transaction_not_found: 404,
	unauthorized: 401,
	unknown_stage: 400,
	unstable_read: 409,
	unsupported_locale: 400
}, pe = "/api/apps/aidlc-studio", me = "/api/chat", he = "mc-auth-required", ge = /^API (\d{3}): (.*)$/s, _e = [
	"unauthorized",
	"owner_required",
	"app_token_forbidden"
], U = class extends Error {
	code;
	details;
	status;
	body;
	constructor(e, t, n, r, i = null) {
		super(t), this.name = "StudioApiError", this.code = e, this.details = n, this.status = r, this.body = i;
	}
	get known() {
		return this.code in fe;
	}
	get authRequired() {
		return _e.includes(this.code);
	}
};
function ve(e) {
	return (e instanceof DOMException || e instanceof Error) && e.name === "AbortError";
}
function W(e) {
	if (e instanceof U) return e;
	let t = e instanceof Error ? e.message : String(e), n = ge.exec(t);
	if (!n) return new U("internal_error", t || "request failed", {}, 0);
	let r = Number(n[1]), i = n[2] ?? "";
	try {
		let e = JSON.parse(i);
		if (e && typeof e == "object") {
			let t = e;
			return new U(typeof t.code == "string" ? t.code : ye(r), typeof t.error == "string" ? t.error : i, t.details && typeof t.details == "object" ? t.details : {}, r, e);
		}
	} catch {}
	return new U(ye(r), i || t, {}, r);
}
function ye(e) {
	return e === 401 ? "unauthorized" : e === 403 ? "owner_required" : e === 404 ? "route_not_found" : e === 429 ? "rate_limited" : "internal_error";
}
function be(e) {
	let t = new URLSearchParams();
	for (let [n, r] of Object.entries(e)) r != null && r !== "" && r !== !1 && t.set(n, r === !0 ? "1" : String(r));
	let n = t.toString();
	return n ? `?${n}` : "";
}
function xe() {
	let e = _();
	return l(() => {
		let t = async (t, n) => {
			try {
				return await e.get(`${pe}${t}`, n?.signal ? { signal: n.signal } : void 0);
			} catch (e) {
				throw ve(e) ? e : W(e);
			}
		}, n = async (t, n, r) => {
			try {
				return t === "del" ? await e.del(`${pe}${n}`) : await e[t](`${pe}${n}`, r);
			} catch (e) {
				throw W(e);
			}
		}, r = (e, t) => n("post", e, t), i = (e, t, n = "") => `/repos/${encodeURIComponent(e)}/intents/${encodeURIComponent(t)}${n}`, a = (e, t = "") => `/repos/${encodeURIComponent(e)}${t}`;
		return {
			health: (e) => t("/health", e),
			configureBun: (e) => n("put", "/tools/bun", { path: e }),
			probeBun: () => r("/tools/bun/probe", {}),
			payload: (e, n) => t(`/payload${be({ files: e })}`, n),
			leases: (e) => t("/leases", e),
			diagnostics: (e) => t(`/diagnostics${be({
				export: e?.export,
				include_human_text: e?.includeHumanText
			})}`, e),
			settings: (e) => t("/settings", e),
			putSettings: (e) => n("put", "/settings", e),
			calibration: (e) => t("/calibration", e),
			clearCalibration: () => r("/calibration/clear", { confirm: !0 }),
			migrationStatus: (e) => t("/migration/status", e),
			migrationPreview: () => r("/migration/preview", {}),
			migrationApply: (e) => r("/migration/apply", {
				confirm: !0,
				source_sha256: e
			}),
			pollEvents: (e, n, r) => t(`/events/poll${be({
				cursor: e,
				limit: n
			})}`, r),
			activity: (e, n) => t(`/activity${be({ ...e })}`, n),
			actions: (e, n) => t(`/actions${be({ ...e })}`, n),
			action: (e, n) => t(`/actions/${encodeURIComponent(e)}`, n),
			submitAction: (e, t) => r(`/actions/${encodeURIComponent(e)}/submit`, t),
			reportDelivery: (e, t) => r(`/actions/${encodeURIComponent(e)}/delivery`, t),
			retryAction: (e) => r(`/actions/${encodeURIComponent(e)}/retry`, {}),
			reconcileAction: (e) => r(`/actions/${encodeURIComponent(e)}/reconcile`, {}),
			cancelAction: (e, t) => r(`/actions/${encodeURIComponent(e)}/cancel`, { reason: t ?? null }),
			resolveAction: (e, t) => {
				let { decision: n, ...i } = t;
				return r(`/actions/${encodeURIComponent(e)}/resolve`, {
					decision: n,
					payload: i
				});
			},
			requestDraft: (e) => r("/advisor/draft", e),
			draft: (e, n) => t(`/advisor/drafts/${encodeURIComponent(e)}`, n),
			slackCallback: (e) => r("/slack/actions/callback", e),
			repos: (e, n) => t(`/repos${be({ include_archived: e })}`, n),
			repo: (e, n) => t(a(e), n),
			preflight: (e) => r("/repos/preflight", { path: e }),
			addRepo: (e, t) => r("/repos", {
				path: e,
				label: t ?? null
			}),
			removeRepo: (e) => n("del", a(e)),
			updateRepoMetadata: (e, t) => n("put", a(e, "/metadata"), t),
			directories: (e) => r("/directories", { path: e ?? null }),
			maintenancePreview: (e, t = []) => r(a(e, "/maintenance/preview"), { entry_ids: t }),
			cleanupMaintenance: (e, t, n) => r(a(e, "/maintenance/cleanup"), {
				entry_ids: t,
				plan_digest: n
			}),
			rebindRepo: (e, t) => r(a(e, "/rebind"), { path: t }),
			rescanRepo: (e) => r(a(e, "/rescan"), {}),
			doctorRepo: (e) => r(a(e, "/doctor"), { confirm: !0 }),
			installPreview: (e, t = "install") => r(a(e, `${t === "recovery" ? "/install/recovery" : `/${t}`}/preview`), {}),
			installApply: (e, t, n = "install") => r(a(e, n === "recovery" ? "/install/recovery" : `/${n}`), { plan_digest: t }),
			transaction: (e, n, r) => t(a(e, `/transactions/${encodeURIComponent(n)}`), r),
			cancelTransaction: (e, t) => r(a(e, `/transactions/${encodeURIComponent(t)}/cancel`), {}),
			receipts: (e, n) => t(a(e, "/receipts"), n),
			repoGit: (e, n) => t(a(e, "/git"), n),
			spaces: (e, n) => t(a(e, "/spaces"), n),
			createSpace: (e, t) => r(a(e, "/spaces"), { name: t }),
			switchSpace: (e, t) => r(a(e, "/spaces/switch"), { name: t }),
			intentSettingsPreview: (e, t, n) => r(i(e, t, "/settings/preview"), n),
			changeIntentSettings: (e, t, n) => r(i(e, t, "/settings"), n),
			intents: (e, n, r) => t(a(e, `/intents${be({ ...n })}`), r),
			intent: (e, n, r) => t(i(e, n), r),
			map: (e, n, r, a) => t(i(e, n, `/map${be({ density: r })}`), a),
			artifacts: (e, n, r, a) => t(i(e, n, `/artifacts${be({ ...r })}`), a),
			artifact: (e, n, r, a) => t(i(e, n, `/artifacts/${encodeURIComponent(r)}`), a),
			questions: (e, n, r) => t(i(e, n, "/questions"), r),
			review: (e, n, r) => t(i(e, n, "/review"), r),
			intentGit: (e, n, r) => t(i(e, n, "/git"), r),
			intentActivity: (e, n, r, a) => t(i(e, n, `/activity${be({ limit: r })}`), a),
			planPreview: (e, t) => r(a(e, "/intents/plan/preview"), t),
			requestPlanDraft: (e, t) => r(a(e, "/intents/plan/advise"), t),
			createIntent: (e, t) => r(a(e, "/intents"), t),
			compileRuntime: (e, t) => r(i(e, t, "/runtime/compile"), {}),
			recomposePreview: (e, t, n) => r(i(e, t, "/recompose/preview"), n),
			recompose: (e, t, n) => r(i(e, t, "/recompose"), n),
			run: (e, t) => r(i(e, t, "/run"), {}),
			resume: (e, t) => r(i(e, t, "/resume"), {}),
			prepareCommit: (e, t) => r(i(e, t, "/prepare-commit"), {}),
			pause: (e, t, n) => r(i(e, t, "/pause"), { paused: n }),
			forceStop: (e, t) => r(i(e, t, "/force-stop"), { confirm: !0 }),
			keepMoving: (e, t, n) => r(i(e, t, "/keep-moving"), { enabled: n }),
			bindSession: (e, t, n) => r(i(e, t, "/session/bind"), { slot_key: n }),
			unbindSession: (e, t) => r(i(e, t, "/session/unbind"), {}),
			takeoverPreview: (e, t) => r(i(e, t, "/session/takeover/preview"), {}),
			takeover: (e, t, n) => r(i(e, t, "/session/takeover"), { slot_key: n }),
			archiveIntent: (e, t) => r(i(e, t, "/archive"), {}),
			restoreIntent: (e, t) => r(i(e, t, "/restore"), {}),
			listSlots: async (t) => {
				try {
					return await e.get(`${me}/slots`, t?.signal ? { signal: t.signal } : void 0);
				} catch (e) {
					throw ve(e) ? e : W(e);
				}
			},
			getSlot: async (t, n) => {
				try {
					return await e.get(`${me}/slots/${encodeURIComponent(t)}`, n?.signal ? { signal: n.signal } : void 0);
				} catch (e) {
					throw ve(e) ? e : W(e);
				}
			},
			createSlot: (t, n) => Se(e, `${me}/slots`, {
				name: t,
				agent: n
			}),
			setSlotTitle: async (t, n) => {
				try {
					return await e.patch(`${me}/slots/${encodeURIComponent(t)}/title`, { title: n });
				} catch (e) {
					throw W(e);
				}
			},
			setSlotProject: (t, n) => Se(e, `${me}/slots/${encodeURIComponent(t)}/project`, { project: n }),
			setSlotAgent: (t, n) => Se(e, `${me}/slots/${encodeURIComponent(t)}/agent`, { agent: n }),
			sendToHost: (t, n) => Se(e, t, n)
		};
	}, [e]);
}
async function Se(e, t, n) {
	try {
		return await e.post(t, n);
	} catch (e) {
		throw W(e);
	}
}
//#endregion
//#region src/lib/format.ts
function Ce(e) {
	return typeof e == "number" && Number.isFinite(e);
}
function G(e) {
	return e.t("common.unavailable");
}
function we(e, t) {
	return Ce(t) ? e.fmt.number(t) : G(e);
}
function Te(e, t) {
	return Ce(t) ? e.fmt.duration(t) : G(e);
}
function Ee(e, t, n = Date.now()) {
	if (!t) return G(e);
	let r = Date.parse(t);
	return Number.isFinite(r) ? e.fmt.duration(Math.max(0, (n - r) / 1e3)) : G(e);
}
function De(e, t) {
	return t && Number.isFinite(Date.parse(t)) ? e.fmt.since(t) : G(e);
}
function K(e, t) {
	return t && Number.isFinite(Date.parse(t)) ? e.fmt.dateTime(t) : G(e);
}
function Oe(e, t) {
	if (!t || !Ce(t.low) || !Ce(t.high)) return G(e);
	let [n, r] = t.unit === "secs" ? [e.fmt.duration(t.low), e.fmt.duration(t.high)] : [e.fmt.number(t.low), e.fmt.number(t.high)];
	return e.t("shell.format.range", {
		low: n,
		high: r
	});
}
function ke(e, t, n) {
	return !Ce(t) || !Ce(n) ? G(e) : e.t("shell.format.ratio", {
		used: e.fmt.number(t),
		cap: e.fmt.number(n)
	});
}
var Ae = [
	"shell.format.bytes",
	"shell.format.kb",
	"shell.format.mb"
];
function je(e, t) {
	if (!Ce(t)) return G(e);
	let n = Math.max(0, t), r = 0;
	for (; n >= 1024 && r < Ae.length - 1;) n /= 1024, r += 1;
	let i = Ae[r] ?? Ae[0], a = r === 0 ? Math.round(n) : Math.round(n * 10) / 10;
	return e.t(i, { n: e.fmt.number(a) });
}
function Me(e, t) {
	let n = {};
	if (!t) return n;
	for (let [r, i] of Object.entries(t.params)) n[r] = i == null ? G(e) : Array.isArray(i) ? i.map((e) => String(e)).join(e.t("shell.format.listJoin")) : typeof i == "number" ? Ce(i) ? i : G(e) : String(i);
	return n;
}
function Ne(e, t, n) {
	if (!t) return "";
	let r = Me(e, t);
	if (t.key.startsWith("action.recovery.")) {
		let n = Array.isArray(t.params.codes) ? t.params.codes.filter((e) => typeof e == "string" && e.trim().length > 0) : [], i = n.length > 0 ? n : typeof t.params.reason == "string" && t.params.reason.trim() ? [t.params.reason] : [];
		r.reason = i.length > 0 ? i.map((t) => {
			let n = `action.reason.${t}`;
			return e.has(n) ? e.t(n, r) : t;
		}).join(e.t("shell.format.listJoin")) : e.t("action.reason.recovery_details");
	}
	return e.has(t.key) ? e.t(t.key, r) : n ? e.t(n, r) : t.key;
}
function q(e, t, n, r = {}) {
	let i = `${t}_${Math.abs(n) === 1 ? "one" : "other"}`;
	return e.t(i, {
		...r,
		n: e.fmt.number(n)
	});
}
//#endregion
//#region src/lib/sse.ts
var Pe = [
	"action.created",
	"action.updated",
	"repo.updated",
	"repo.removed",
	"intent.updated",
	"transaction.updated",
	"lease.updated",
	"activity.appended",
	"advisor.updated",
	"settings.updated",
	"health.updated",
	"migration.updated",
	"reset"
], Fe = 1e3, Ie = 3e4, Le = 5e3, Re = t(null), ze = t({
	mode: "offline",
	cursor: null,
	attempts: 0
});
function Be({ poll: e, enabled: t = !0, children: r }) {
	let a = u(/* @__PURE__ */ new Set()), s = u(null), [c, l] = d({
		mode: "connecting",
		cursor: null,
		attempts: 0
	}), f = u({ subscribe: (e) => (a.current.add(e), () => void a.current.delete(e)) }), p = i((e) => {
		e.seq >= 0 && (s.current = e.seq);
		for (let t of [...a.current]) t(e);
	}, []);
	return o(() => {
		if (!t) {
			l((e) => ({
				...e,
				mode: "offline"
			}));
			return;
		}
		let n = !1, r = null, i = null, a = null, o = 0, c = (e) => l({
			mode: e,
			cursor: s.current,
			attempts: o
		}), u = () => {
			a !== null && clearTimeout(a), a = null;
		}, d = async () => {
			if (!n) {
				if (typeof document > "u" || !document.hidden) try {
					let t = await e(s.current);
					if (n) return;
					if (t.reset) s.current = t.cursor, p({
						type: "reset",
						seq: -1,
						payload: {
							oldest_seq: t.oldest_seq,
							cursor: t.cursor
						}
					});
					else {
						for (let e of t.events) p({
							type: e.type,
							seq: e.seq,
							payload: e.payload
						});
						s.current = t.cursor;
					}
				} catch {}
				n || (a = setTimeout(d, Le));
			}
		}, f = () => {
			a === null && (a = setTimeout(d, 0));
		}, m = () => {
			o += 1;
			let e = Math.min(Ie, Fe * 2 ** (o - 1)) * (.75 + Math.random() * .5);
			i = setTimeout(h, e);
		};
		function h() {
			if (n) return;
			if (typeof EventSource > "u") {
				c("polling"), f();
				return;
			}
			let e = s.current === null ? `${pe}/events` : `${pe}/events?cursor=${s.current}`, t;
			try {
				t = new EventSource(e);
			} catch {
				c("polling"), f(), m();
				return;
			}
			r = t, c(o === 0 ? "connecting" : "polling"), t.onopen = () => {
				o = 0, u(), c("live");
			}, t.onerror = () => {
				t.close(), r === t && (r = null), !n && (c("polling"), f(), m());
			};
			let i = (e) => (t) => {
				let n = t, r = Number(n.lastEventId), i = {};
				try {
					let e = JSON.parse(n.data);
					e && typeof e == "object" && (i = e);
				} catch {}
				p({
					type: e,
					seq: Number.isFinite(r) ? r : -1,
					payload: i
				});
			};
			for (let e of Pe) t.addEventListener(e, i(e));
			t.addEventListener("hello", (e) => {
				let t = Number(e.lastEventId);
				Number.isFinite(t) && (s.current = t), p({
					type: "reset",
					seq: -1,
					payload: { hello: !0 }
				});
			});
		}
		return h(), () => {
			n = !0, i !== null && clearTimeout(i), u(), r?.close();
		};
	}, [
		t,
		e,
		p
	]), n(ze.Provider, { value: c }, n(Re.Provider, { value: f.current }, r));
}
function Ve(e, t) {
	let n = a(Re), r = u(e);
	r.current = e;
	let i = t ? t.join(",") : "";
	o(() => {
		if (!n) return;
		let e = i ? new Set(i.split(",")) : null;
		return n.subscribe((t) => {
			(!e || e.has(t.type)) && r.current(t);
		});
	}, [n, i]);
}
function He() {
	return a(ze);
}
//#endregion
//#region src/lib/useResource.ts
var Ue = 2e3, We = 15e3, Ge = 2e4, Ke = /* @__PURE__ */ new Map(), qe = [
	"Delivering",
	"Delivered",
	"Processing"
];
function Je(e) {
	if (!e || typeof e != "object") return !1;
	let t = e;
	if (typeof t.live_execution == "number" && t.live_execution > 0) return !0;
	let n = t.actions;
	if (Array.isArray(n) && n.some((e) => Ye(e)) || Ye(t.action)) return !0;
	let r = t.intents;
	if (Array.isArray(r) && r.some((e) => Xe(e)) || Xe(t.intent) || Xe(t)) return !0;
	let i = t.transaction;
	if (i && typeof i == "object") {
		let e = i.status;
		if (typeof e == "string" && e !== "committed" && e !== "rolled_back" && e !== "failed") return !0;
	}
	return !1;
}
function Ye(e) {
	if (!e || typeof e != "object") return !1;
	let t = e, n = t.status;
	if (typeof n == "string" && qe.includes(n)) return !0;
	let r = t.evidence;
	if (r && typeof r == "object") {
		let e = r.session;
		if (e && typeof e == "object" && e.running === !0) return !0;
	}
	return !1;
}
function Xe(e) {
	if (!e || typeof e != "object") return !1;
	let t = e, n = t.operational_state;
	if (n === "Running" || n === "Queued") return !0;
	let r = t.session;
	return !!r && typeof r == "object" && r.running === !0;
}
function J(e, t, n = {}) {
	let { interval: r, fastInterval: a = Ue, slowInterval: s = We, busy: c, revalidateOn: f, enabled: p = !0 } = n, [m, h] = d(null), [g, _] = d(null), [v, y] = d(0), b = u(t);
	b.current = t;
	let x = u(e), S = u(!0);
	o(() => (S.current = !0, () => {
		S.current = !1;
	}), []), o(() => {
		x.current = e, h(null), _(null);
	}, [e]);
	let C = i(async () => {
		let e = x.current;
		if (e === null || !p) return;
		let t = Ke.get(e);
		if (t) {
			try {
				let n = await t;
				S.current && x.current === e && (h(n), _(null));
			} catch {}
			return;
		}
		let n = new AbortController(), r = setTimeout(() => n.abort(), Ge), i = b.current(n.signal);
		Ke.set(e, i), y((e) => e + 1);
		try {
			let t = await i;
			S.current && x.current === e && (h(t), _(null));
		} catch (t) {
			S.current && x.current === e && !ve(t) && _(t instanceof U ? t : new U("internal_error", String(t), {}, 0));
		} finally {
			clearTimeout(r), Ke.get(e) === i && Ke.delete(e), S.current && y((e) => Math.max(0, e - 1));
		}
	}, [p]);
	o(() => {
		e !== null && p && C();
	}, [
		e,
		p,
		C
	]);
	let w = m !== null && (c ? c(m) : Je(m)), T = r ?? (w ? a : s);
	return o(() => {
		if (e === null || !p || T <= 0) return;
		let t = null, n = async () => {
			(typeof document > "u" || !document.hidden) && await C(), t = setTimeout(n, T);
		};
		t = setTimeout(n, T);
		let r = () => {
			typeof document < "u" && !document.hidden && C();
		};
		return document.addEventListener("visibilitychange", r), () => {
			t !== null && clearTimeout(t), document.removeEventListener("visibilitychange", r);
		};
	}, [
		e,
		p,
		T,
		C
	]), Ve(i(() => {
		C();
	}, [C]), f), l(() => ({
		data: m,
		error: g,
		loading: v > 0 && m === null,
		stale: v > 0,
		refresh: C,
		refetch: C
	}), [
		m,
		g,
		v,
		C
	]);
}
//#endregion
//#region src/shell/Icon.tsx
var Ze = {
	logo: "Workflow",
	inbox: "Inbox",
	repo: "FolderGit2",
	intent: "ListTodo",
	map: "LayoutGrid",
	activity: "Activity",
	settings: "Settings",
	gate: "LockKeyhole",
	question: "CircleQuestionMark",
	missingInput: "FileQuestionMark",
	recovery: "TriangleAlert",
	fail: "CircleX",
	install: "Download",
	check: "Check",
	warn: "CircleAlert",
	info: "Info",
	clock: "Clock",
	doc: "FileText",
	review: "Eye",
	advisor: "Sparkles",
	send: "Send",
	search: "Search",
	chevron: "ChevronRight",
	chevronDown: "ChevronDown",
	back: "ChevronLeft",
	play: "Play",
	pause: "Pause",
	moon: "Moon",
	sun: "Sun",
	slack: "MessageSquare",
	git: "GitBranch",
	lock: "Lock",
	plus: "Plus",
	link: "Link",
	refresh: "RefreshCw",
	close: "X",
	external: "ExternalLink"
}, Qe = E;
function Y({ name: e, size: t = 15, strokeWidth: n = 1.6, className: r, label: i }) {
	let a = Qe[Ze[e]];
	return a ? /* @__PURE__ */ m(a, {
		size: t,
		strokeWidth: n,
		className: r,
		focusable: "false",
		...i ? {
			role: "img",
			"aria-label": i
		} : { "aria-hidden": !0 }
	}) : null;
}
//#endregion
//#region src/shell/Chip.tsx
function X({ children: e, tone: t = "neutral", icon: n, mono: r, title: i, className: a }) {
	let o = ["studio-chip"];
	return r && o.push("studio-mono"), a && o.push(a), /* @__PURE__ */ h("span", {
		className: o.join(" "),
		...t === "neutral" ? {} : { "data-tone": t },
		...i ? { title: i } : {},
		children: [n ? /* @__PURE__ */ m(Y, {
			name: n,
			size: 11,
			strokeWidth: 2
		}) : null, e]
	});
}
//#endregion
//#region src/advisor/AdvisorDrawer.tsx
var $e = 2e3;
function et({ items: e }) {
	return /* @__PURE__ */ m("ul", {
		className: "studio-list",
		children: e.map((e, t) => /* @__PURE__ */ m("li", { children: e }, `${t}-${e.slice(0, 24)}`))
	});
}
function tt({ draftId: e, onClose: t, onUseFeedback: n, onApplyAnswers: r }) {
	let a = H(), { t: s } = a, c = xe(), l = u(null), d = J(`advisor-draft:${e}`, i((t) => c.draft(e, { signal: t }), [c, e]), {
		busy: (e) => e.draft.status === "queued" || e.draft.status === "running",
		fastInterval: $e,
		slowInterval: 0,
		revalidateOn: ["advisor.updated", "reset"]
	});
	o(() => {
		l.current?.focus();
	}, [e]), o(() => {
		let e = (e) => {
			e.key === "Escape" && t();
		};
		return window.addEventListener("keydown", e), () => window.removeEventListener("keydown", e);
	}, [t]);
	let f = d.data?.draft ?? null, g = f?.result ?? null;
	return /* @__PURE__ */ h("aside", {
		className: "studio-evdrawer studio-advisor-drawer",
		"aria-label": s("advisor.drawer.title"),
		children: [/* @__PURE__ */ h("header", {
			className: "studio-evdrawer-head",
			children: [/* @__PURE__ */ h("h2", { children: [
				/* @__PURE__ */ m(Y, {
					name: "advisor",
					size: 15
				}),
				" ",
				s("advisor.drawer.title")
			] }), /* @__PURE__ */ m("button", {
				type: "button",
				ref: l,
				className: "studio-icon-btn",
				onClick: t,
				"aria-label": s("advisor.drawer.close"),
				children: /* @__PURE__ */ m(Y, {
					name: "close",
					size: 15
				})
			})]
		}), /* @__PURE__ */ h("div", {
			className: "studio-evdrawer-body",
			children: [
				/* @__PURE__ */ h("div", {
					role: "status",
					"aria-live": "polite",
					className: "studio-row studio-wrapchips",
					children: [
						/* @__PURE__ */ m(X, {
							tone: "aim",
							icon: "advisor",
							children: s("advisor.drawer.badge")
						}),
						f ? /* @__PURE__ */ m(X, { children: s(`advisor.drawer.kind.${f.kind}`) }) : null,
						f && f.status !== "ready" ? /* @__PURE__ */ m("span", {
							className: "studio-muted",
							children: f.status === "failed" ? s("advisor.drawer.status.failed", { message: f.error ?? s("common.unavailable") }) : s(`advisor.drawer.status.${f.status}`)
						}) : null
					]
				}),
				d.error ? /* @__PURE__ */ m("p", {
					className: "studio-error",
					children: s("advisor.drawer.error", { message: a.has(`errors.${d.error.code}`) ? s(`errors.${d.error.code}`) : d.error.message })
				}) : null,
				d.loading ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: s("common.loading")
				}) : null,
				f && !g && f.status === "ready" ? /* @__PURE__ */ m("p", {
					className: "studio-consequence",
					children: s("advisor.drawer.empty")
				}) : null,
				g ? /* @__PURE__ */ h(p, { children: [
					g.verdict ? /* @__PURE__ */ m("p", {
						className: "studio-advisor-verdict",
						children: a.has(`advisor.verdict.${g.verdict}`) ? s(`advisor.verdict.${g.verdict}`) : g.verdict
					}) : null,
					g.summary ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("h3", {
						className: "studio-subhead",
						children: s("advisor.drawer.summary")
					}), /* @__PURE__ */ m("p", {
						className: "studio-advisor-summary",
						children: g.summary
					})] }) : null,
					/* @__PURE__ */ h("dl", {
						className: "studio-evgrid",
						children: [
							g.evidence.length > 0 ? /* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: s("advisor.drawer.evidence") }), /* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m(et, { items: g.evidence }) })]
							}) : null,
							g.assumptions.length > 0 ? /* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: s("advisor.drawer.assumptions") }), /* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m(et, { items: g.assumptions }) })]
							}) : null,
							g.alternatives.length > 0 ? /* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: s("advisor.drawer.alternatives") }), /* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m(et, { items: g.alternatives }) })]
							}) : null,
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: s("advisor.drawer.confidence") }), /* @__PURE__ */ m("dd", { children: s(`advisor.drawer.confidence.${g.confidence}`) })]
							}),
							g.needs_your_decision.length > 0 ? /* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: s("advisor.drawer.needsYourDecision") }), /* @__PURE__ */ h("dd", { children: [/* @__PURE__ */ m(X, {
									tone: "warn",
									icon: "warn",
									children: s("advisor.drawer.needsYourDecisionChip")
								}), /* @__PURE__ */ m(et, { items: g.needs_your_decision })] })]
							}) : null
						]
					}),
					g.suggested_answers.length > 0 ? /* @__PURE__ */ h("section", {
						className: "studio-block",
						children: [
							/* @__PURE__ */ m("h3", { children: s("advisor.drawer.suggested") }),
							/* @__PURE__ */ m("dl", {
								className: "studio-evgrid",
								children: g.suggested_answers.map((e) => /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", {
										className: "studio-mono",
										children: s("advisor.drawer.suggestedQuestion", { index: e.question_index })
									}), /* @__PURE__ */ m("dd", {
										className: "studio-wrap-any",
										children: e.answer
									})]
								}, e.question_index))
							}),
							r ? /* @__PURE__ */ h("div", {
								className: "studio-col",
								children: [/* @__PURE__ */ h(b, {
									type: "button",
									onClick: () => r(g.suggested_answers),
									children: [/* @__PURE__ */ m(Y, {
										name: "check",
										size: 13
									}), s("advisor.drawer.applyAnswers")]
								}), /* @__PURE__ */ m("span", {
									className: "studio-muted",
									children: s("advisor.drawer.applyAnswersHint")
								})]
							}) : null
						]
					}) : null,
					g.drafted_feedback ? /* @__PURE__ */ h("section", {
						className: "studio-block",
						children: [
							/* @__PURE__ */ m("h3", { children: s("advisor.drawer.useFeedback") }),
							/* @__PURE__ */ m("pre", {
								className: "studio-pre studio-wrap-any",
								children: g.drafted_feedback
							}),
							n ? /* @__PURE__ */ h("div", {
								className: "studio-col",
								children: [/* @__PURE__ */ h(b, {
									type: "button",
									onClick: () => n(g.drafted_feedback ?? ""),
									children: [/* @__PURE__ */ m(Y, {
										name: "doc",
										size: 13
									}), s("advisor.drawer.useFeedback")]
								}), /* @__PURE__ */ m("span", {
									className: "studio-muted",
									children: s("advisor.drawer.useFeedbackHint")
								})]
							}) : null
						]
					}) : null
				] }) : null,
				f ? /* @__PURE__ */ m("p", {
					className: "studio-muted studio-small",
					children: s("advisor.drawer.expires", { when: K(a, f.expires_at) })
				}) : null,
				/* @__PURE__ */ h("p", {
					className: "studio-consequence",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "lock",
							size: 13
						}),
						" ",
						s("advisor.drawer.disclaimer")
					]
				})
			]
		})]
	});
}
//#endregion
//#region src/lib/actionQueue.ts
var nt = /* @__PURE__ */ new Set([
	"StateChanged",
	"ResolvedNoTransition",
	"Cancelled"
]);
function rt(e) {
	return nt.has(e.status);
}
function it(e) {
	return e.repo.availability !== void 0 && e.repo.availability !== "available";
}
var at = /* @__PURE__ */ new Set([
	"Draft",
	"Delivering",
	"Delivered",
	"Processing",
	...nt
]);
function ot(e) {
	let t = e.actions.filter((e) => at.has(e.status) || e.repo.archived === !0);
	if (t.length === 0) return e;
	let n = new Set(t.map((e) => e.action_id)), r = { ...e.counts };
	for (let e of t) r.total = Math.max(0, r.total - 1), r[e.severity] = Math.max(0, r[e.severity] - 1);
	return {
		...e,
		actions: e.actions.filter((e) => !n.has(e.action_id)),
		counts: r,
		groups: e.groups.map((e) => ({
			...e,
			action_ids: e.action_ids.filter((e) => !n.has(e))
		})).filter((e) => e.action_ids.length > 0)
	};
}
//#endregion
//#region src/lib/route.ts
var st = [
	"actions",
	"repos",
	"intents",
	"map",
	"activity",
	"settings",
	"new-intent"
], ct = [
	"decision",
	"artifacts",
	"review",
	"activity",
	"conversation"
], lt = "actions", ut = "decision", dt = "default", ft = "/apps/aidlc-studio", pt = /^[A-Za-z0-9_.~-]{1,160}$/, mt = /^(?:f-\d{1,4}|crit-\d{1,4}|h-[a-z0-9-]{1,120})$/, ht = [
	"view",
	"repo",
	"space",
	"intent",
	"action",
	"tab",
	"stage",
	"unit",
	"artifact",
	"tx",
	"draft"
];
function gt(e) {
	return e && pt.test(e) ? e : "";
}
function _t(e, t) {
	let n = new URLSearchParams(e.startsWith("?") ? e.slice(1) : e), r = n.get("view"), i = gt(n.get("action")), a = st.includes(r) ? r : i ? "actions" : lt, o = n.get("tab"), s = ct.includes(o) ? o : ut, c = gt(n.get("intent")), l = gt(n.get("space")) || (c ? "default" : ""), u = (t.startsWith("#") ? t.slice(1) : t).trim();
	return {
		view: a,
		repo: gt(n.get("repo")),
		space: l,
		intent: c,
		action: i,
		tab: s,
		stage: gt(n.get("stage")),
		unit: gt(n.get("unit")),
		artifact: gt(n.get("artifact")),
		tx: gt(n.get("tx")),
		draft: gt(n.get("draft")),
		anchor: mt.test(u) ? u : ""
	};
}
function vt(e, t) {
	let n = {
		...t ?? yt,
		...e
	}, r = [];
	for (let e of ht) {
		let t = n[e];
		t && (e !== "view" || t !== "actions" || n.action) && (e !== "tab" || t !== "decision") && (e === "space" && t === "default" && n.intent || pt.test(t) && r.push(`${e}=${encodeURIComponent(t)}`));
	}
	let i = r.join("&"), a = mt.test(n.anchor) ? `#${n.anchor}` : "";
	return `${ft}${i ? `?${i}` : ""}${a}`;
}
var yt = {
	view: lt,
	repo: "",
	space: "",
	intent: "",
	action: "",
	tab: ut,
	stage: "",
	unit: "",
	artifact: "",
	tx: "",
	draft: "",
	anchor: ""
};
function bt(e) {
	return e.action ? "detail" : "list";
}
var xt = "aidlc-studio:route", St = null, Ct = null, wt = yt;
function Tt() {
	if (typeof window > "u") return yt;
	let { search: e, hash: t } = window.location;
	return (e !== St || t !== Ct) && (St = e, Ct = t, wt = _t(e, t)), wt;
}
function Et(e) {
	return window.addEventListener("popstate", e), window.addEventListener(xt, e), () => {
		window.removeEventListener("popstate", e), window.removeEventListener(xt, e);
	};
}
function Dt() {
	let e = f(Et, Tt, () => yt), t = y();
	return [e, i((e, n) => {
		let r = vt(e, Tt());
		n?.replace ? window.history.replaceState(null, "", r) : t(r), window.dispatchEvent(new CustomEvent(xt));
	}, [t])];
}
//#endregion
//#region src/shell/ErrorBoundary.tsx
var Ot = class extends e {
	constructor(e) {
		super(e), this.state = {
			error: null,
			resetKey: e.resetKey
		};
	}
	static getDerivedStateFromError(e) {
		return { error: e };
	}
	static getDerivedStateFromProps(e, t) {
		return e.resetKey === t.resetKey ? null : {
			error: null,
			resetKey: e.resetKey
		};
	}
	componentDidCatch(e, t) {
		console.error("[aidlc-studio] render failed in %s", this.props.where, e, t.componentStack);
	}
	retry = () => {
		this.setState({ error: null });
	};
	render() {
		let { error: e } = this.state;
		if (!e) return this.props.children;
		let t = ue(V());
		return /* @__PURE__ */ h("div", {
			className: "studio-page studio-failure",
			role: "alert",
			children: [
				/* @__PURE__ */ h("h1", { children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 18
					}),
					" ",
					t.t("shell.error.title", { where: this.props.where })
				] }),
				/* @__PURE__ */ m("p", {
					className: "studio-failure-detail studio-mono studio-wrap-any",
					children: e.message
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: t.t("shell.error.nothingSent")
				}),
				/* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					onClick: this.retry,
					children: [
						/* @__PURE__ */ m(Y, {
							name: "refresh",
							size: 13
						}),
						" ",
						t.t("common.retry")
					]
				})
			]
		});
	}
};
//#endregion
//#region src/shell/ScopeBar.tsx
function kt({ route: e, go: t, repos: n, registered: r, unavailable: i, children: a }) {
	let o = H(), { t: s } = o, c = n.find((t) => t.repo_id === e.repo) ?? null;
	return /* @__PURE__ */ h("div", {
		className: "studio-scopebar",
		children: [
			/* @__PURE__ */ h("label", {
				className: "studio-scope-select",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "repo",
						size: 13
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-sr",
						children: s("scope.selectLabel")
					}),
					/* @__PURE__ */ h("select", {
						value: c ? c.repo_id : "",
						onChange: (e) => {
							t({
								repo: e.target.value,
								action: "",
								intent: "",
								space: "",
								stage: "",
								unit: "",
								artifact: ""
							});
						},
						children: [/* @__PURE__ */ m("option", {
							value: "",
							children: s("scope.allRepos")
						}), n.map((e) => /* @__PURE__ */ m("option", {
							value: e.repo_id,
							children: e.label
						}, e.repo_id))]
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-scope-count studio-muted studio-mono",
						children: c ? c.canonical_path : r === null ? s("common.unavailable") : q(o, "scope.registered", r)
					})
				]
			}),
			(i ?? 0) > 0 ? /* @__PURE__ */ h("button", {
				type: "button",
				className: "studio-chip",
				"data-tone": "warn",
				onClick: () => t({ view: "repos" }),
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 11,
					strokeWidth: 2
				}), q(o, "scope.unavailable", i ?? 0)]
			}) : null,
			/* @__PURE__ */ m(At, {
				route: e,
				repo: c
			}),
			a
		]
	});
}
function At({ route: e, repo: t }) {
	let { t: n } = H(), [r, i] = jt(e.intent), a = [];
	return t && a.push(t.label), r && r !== "default" && a.push(r), i && a.push(i), e.stage && a.push(e.stage), a.length === 0 ? /* @__PURE__ */ m("span", {
		className: "studio-crumb",
		children: n("scope.crumbNoScan")
	}) : /* @__PURE__ */ m("span", {
		className: "studio-crumb studio-trunc",
		children: a.map((t, n) => /* @__PURE__ */ h("span", { children: [n > 0 ? /* @__PURE__ */ m("span", {
			className: "sep",
			children: "/"
		}) : null, n < a.length - 1 || !e.stage ? /* @__PURE__ */ m("b", { children: t }) : t] }, `${n}-${t}`))
	});
}
function jt(e) {
	if (!e) return ["", ""];
	let t = e.indexOf("~");
	return t < 0 ? [dt, e] : [e.slice(0, t), e.slice(t + 1)];
}
//#endregion
//#region src/shell/StatusStrip.tsx
var Mt = "aidlc-studio:strip";
function Nt() {
	try {
		return localStorage.getItem(Mt) !== "closed";
	} catch {
		return !0;
	}
}
function Pt({ running: e, leases: t, circuits: n, critical: r, nightWindow: a, streamMode: o }) {
	let s = H(), { t: c } = s, [l, u] = d(Nt), f = i(() => {
		u((e) => {
			let t = !e;
			try {
				localStorage.setItem(Mt, t ? "open" : "closed");
			} catch {}
			return t;
		});
	}, []), g = (n ?? 0) + (r ?? 0), _ = (e ?? 0) > 0, v = l ? "" : g > 0 ? q(s, "shell.strip.alerts", g) : c("shell.strip.clear");
	return /* @__PURE__ */ h("div", {
		className: "studio-strip",
		"data-open": l ? "true" : "false",
		children: [
			/* @__PURE__ */ m("button", {
				type: "button",
				className: "studio-icon-btn studio-strip-toggle",
				"aria-expanded": l,
				"aria-label": c(l ? "shell.strip.collapse" : "shell.strip.expand"),
				onClick: f,
				children: /* @__PURE__ */ m(Y, {
					name: l ? "chevronDown" : "chevron",
					size: 14
				})
			}),
			l ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ h(X, {
					tone: _ ? "accent" : "neutral",
					children: [_ ? /* @__PURE__ */ m("span", {
						className: "studio-pulse",
						"aria-hidden": !0
					}) : /* @__PURE__ */ m(Y, {
						name: "pause",
						size: 11,
						strokeWidth: 2
					}), e === null ? c("common.unavailable") : q(s, "shell.strip.running", e)]
				}),
				/* @__PURE__ */ m(X, {
					icon: "lock",
					children: t === null ? c("common.unavailable") : q(s, "shell.strip.leases", t)
				}),
				(n ?? 0) > 0 ? /* @__PURE__ */ m(X, {
					tone: "warn",
					icon: "clock",
					children: q(s, "shell.strip.circuits", n ?? 0)
				}) : null,
				(r ?? 0) > 0 ? /* @__PURE__ */ m(X, {
					tone: "danger",
					icon: "recovery",
					children: q(s, "shell.strip.critical", r ?? 0)
				}) : null,
				/* @__PURE__ */ m(X, {
					icon: "moon",
					title: a?.enabled ? void 0 : c("unavailable.machineLane"),
					children: a?.enabled ? c("shell.strip.nightOn", {
						start: a.start,
						end: a.end
					}) : c("shell.strip.nightOff")
				}),
				o === "live" ? null : /* @__PURE__ */ m(X, {
					tone: "warn",
					icon: "refresh",
					title: c("shell.strip.pollingWhy"),
					children: c("shell.strip.polling")
				})
			] }) : /* @__PURE__ */ m("span", {
				className: "studio-strip-summary",
				children: g > 0 ? /* @__PURE__ */ m(X, {
					tone: "danger",
					icon: "warn",
					children: q(s, "shell.strip.alerts", g)
				}) : /* @__PURE__ */ m(X, {
					icon: "check",
					children: c("shell.strip.clear")
				})
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-sr",
				role: "status",
				"aria-live": "polite",
				children: v
			})
		]
	});
}
//#endregion
//#region src/shell/TopBar.tsx
var Ft = [
	{
		view: "actions",
		icon: "inbox"
	},
	{
		view: "repos",
		icon: "repo"
	},
	{
		view: "intents",
		icon: "intent"
	},
	{
		view: "map",
		icon: "map"
	},
	{
		view: "activity",
		icon: "activity"
	},
	{
		view: "settings",
		icon: "settings"
	}
];
function It({ route: e, go: t, queueCount: n, showBack: r, onBack: i }) {
	let a = H(), { t: o } = a;
	return /* @__PURE__ */ h("header", {
		className: "studio-topbar",
		children: [
			r ? /* @__PURE__ */ m("button", {
				type: "button",
				className: "studio-icon-btn studio-back",
				"aria-label": o("shell.backToQueue"),
				onClick: i,
				children: /* @__PURE__ */ m(Y, {
					name: "back",
					size: 16
				})
			}) : null,
			/* @__PURE__ */ h("span", {
				className: "studio-brand",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-brand-mark",
					"aria-hidden": !0,
					children: /* @__PURE__ */ m(Y, {
						name: "logo",
						size: 15
					})
				}), /* @__PURE__ */ m("span", {
					className: "studio-brand-name",
					children: o("shell.brand")
				})]
			}),
			/* @__PURE__ */ m("nav", {
				className: "studio-nav",
				"aria-label": o("shell.a11y.primaryNav"),
				children: Ft.map(({ view: r, icon: i }) => {
					let s = e.view === r, c = r === "actions" && n !== null && n > 0;
					return /* @__PURE__ */ h("button", {
						type: "button",
						...s ? { "aria-current": "page" } : {},
						onClick: () => t({ view: r }),
						children: [
							/* @__PURE__ */ m(Y, {
								name: i,
								size: 15
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-nav-text",
								children: o(`nav.${r}`)
							}),
							c ? /* @__PURE__ */ m("span", {
								className: "studio-count",
								"aria-hidden": !0,
								children: a.fmt.number(n)
							}) : null,
							c ? /* @__PURE__ */ m("span", {
								className: "studio-sr",
								children: q(a, "shell.a11y.queueCount", n)
							}) : null
						]
					}, r);
				})
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-topbar-right",
				children: /* @__PURE__ */ h(b, {
					onClick: () => t({ view: "new-intent" }),
					children: [/* @__PURE__ */ m(Y, {
						name: "plus",
						size: 13
					}), /* @__PURE__ */ m("span", {
						className: "studio-nav-label",
						children: o("nav.newIntent")
					})]
				})
			})
		]
	});
}
//#endregion
//#region src/lib/sort.ts
var Lt = {
	recovery: 1,
	delivery_uncertain: 1,
	gate: 2,
	question: 2,
	missing_input: 2,
	circuit_breaker: 3,
	failure: 3,
	install_conflict: 3,
	budget_stop: 4,
	revision: 4,
	run: 4,
	resume: 4,
	force_stop: 4,
	prepare_commit: 4
}, Rt = {
	critical: 0,
	blocking: 1,
	attention: 2,
	info: 3
}, zt = Lt, Bt = Rt;
function Vt(e) {
	return zt[e.queue_type] ?? zt[e.type] ?? 4;
}
function Ht(e) {
	return Bt[e.severity] ?? 3;
}
var Ut = (e, t) => e.waiting_since < t.waiting_since ? -1 : e.waiting_since > t.waiting_since ? 1 : e.action_id < t.action_id ? -1 : +(e.action_id > t.action_id);
function Wt(e, t, n) {
	let r = [...e];
	switch (t) {
		case "priority": return r.sort((e, t) => Vt(e) - Vt(t) || Ht(e) - Ht(t) || Ut(e, t));
		case "repo": return r.sort((e, t) => n.compare(e.repo.label, t.repo.label) || e.repo.repo_id.localeCompare(t.repo.repo_id) || Vt(e) - Vt(t) || Ut(e, t));
		case "type": return r.sort((e, t) => Vt(e) - Vt(t) || e.queue_type.localeCompare(t.queue_type) || Ht(e) - Ht(t) || Ut(e, t));
		case "oldest": return r.sort(Ut);
	}
}
function Gt(e, t) {
	switch (t) {
		case "priority": return `group.${Vt(e)}`;
		case "repo": return e.repo.repo_id;
		case "type": return e.queue_type;
		case "oldest": return "oldest";
	}
}
var Kt = "\0none";
function qt(e) {
	let t = [], n = /* @__PURE__ */ new Map();
	for (let r of e) {
		let e = r.stage ?? Kt, i = n.get(e);
		i || (i = {
			stage: r.stage,
			files: []
		}, n.set(e, i), t.push(i)), i.files.push(r);
	}
	return t.sort((e, t) => +(e.stage === null) - (t.stage === null));
}
function Jt({ artifacts: e, selectedId: t, onSelect: n, truncated: r = !1 }) {
	let i = H(), { t: a, fmt: o } = i, [s, c] = d(""), u = l(() => {
		let t = s.trim().toLowerCase();
		return t ? e.filter((e) => `${e.name} ${e.relpath} ${e.stage ?? ""} ${e.unit ?? ""}`.toLowerCase().includes(t)) : e;
	}, [e, s]), f = l(() => qt(u), [u]);
	return /* @__PURE__ */ h("div", {
		className: "studio-artlist",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-artlist-head",
				children: [/* @__PURE__ */ m("h4", { children: a("artifact.list.title", { n: o.number(e.length) }) }), e.length >= 8 ? /* @__PURE__ */ m(w, {
					type: "search",
					value: s,
					"aria-label": a("artifact.list.filter"),
					placeholder: a("artifact.list.filter"),
					onChange: (e) => c(e.currentTarget.value)
				}) : null]
			}),
			r ? /* @__PURE__ */ h("p", {
				className: "studio-muted studio-artlist-note",
				role: "status",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}),
					" ",
					a("artifact.list.truncated")
				]
			}) : null,
			u.length === 0 ? /* @__PURE__ */ m("p", {
				className: "studio-muted studio-artlist-note",
				children: a("artifact.list.noMatch", { query: s.trim() })
			}) : f.map((e) => /* @__PURE__ */ h("section", {
				className: "studio-artgroup",
				children: [/* @__PURE__ */ m("h5", {
					className: "studio-artgroup-h",
					children: e.stage ? /* @__PURE__ */ m("span", {
						className: "studio-mono",
						children: e.stage
					}) : a("artifact.list.noStage")
				}), /* @__PURE__ */ m("ul", { children: e.files.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-artrow",
					"aria-current": e.artifact_id === t ? "true" : void 0,
					onClick: () => n(e.artifact_id),
					children: [
						/* @__PURE__ */ m(Y, {
							name: "doc",
							size: 13
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-artrow-nm studio-mono studio-trunc",
							title: e.relpath,
							children: e.name
						}),
						e.unit ? /* @__PURE__ */ m(X, {
							icon: "intent",
							children: e.unit
						}) : null,
						e.renderable ? null : /* @__PURE__ */ m(X, {
							tone: "warn",
							children: a("artifact.list.notRendered")
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-artrow-size studio-mono",
							children: je(i, e.size)
						})
					]
				}) }, e.artifact_id)) })]
			}, e.stage ?? Kt))
		]
	});
}
var Yt = [
	"diff --git",
	"index ",
	"new file mode",
	"deleted file mode",
	"old mode",
	"new mode",
	"similarity index",
	"rename from",
	"rename to",
	"--- ",
	"+++ ",
	"\\"
];
function Xt(e) {
	if (e.startsWith("@@")) return "hunk";
	for (let t of Yt) if (e.startsWith(t)) return "meta";
	return e.startsWith("+") ? "add" : e.startsWith("-") ? "del" : "context";
}
function Zt(e) {
	let t = e.endsWith("\n") ? e.slice(0, -1) : e;
	return t === "" ? [] : t.split("\n").map((e) => ({
		kind: Xt(e),
		text: e
	}));
}
function Qt({ prior: e, name: t }) {
	let { t: n, fmt: r } = H(), [i, a] = d(400), o = l(() => e.diff ? Zt(e.diff) : [], [e.diff]);
	if (!e.available) return null;
	let s = o.slice(0, i), c = o.length - s.length;
	return /* @__PURE__ */ h("section", {
		className: "studio-diff",
		"aria-label": n("artifact.diff.label", { name: t }),
		children: [/* @__PURE__ */ h("div", {
			className: "studio-diff-head",
			children: [/* @__PURE__ */ m("h5", { children: n("artifact.diff.title") }), /* @__PURE__ */ m(X, {
				tone: "info",
				icon: "git",
				children: e.source === "git" ? n("artifact.diff.fromGit") : n("artifact.diff.fromUnknown")
			})]
		}), e.diff === null ? /* @__PURE__ */ h("p", {
			className: "studio-muted studio-diff-note",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}),
				" ",
				n("artifact.diff.unavailable")
			]
		}) : o.length === 0 ? /* @__PURE__ */ h("p", {
			className: "studio-muted studio-diff-note",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "check",
					size: 13
				}),
				" ",
				n("artifact.diff.unchanged")
			]
		}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("div", {
			className: "studio-difflines",
			children: s.map((e, t) => /* @__PURE__ */ h("div", {
				className: "studio-diffline",
				"data-kind": e.kind,
				children: [e.kind === "add" || e.kind === "del" ? /* @__PURE__ */ m("span", {
					className: "studio-sr",
					children: e.kind === "add" ? n("artifact.diff.added") : n("artifact.diff.removed")
				}) : null, e.text === "" ? "\xA0" : e.text]
			}, `${t}-${e.text}`))
		}), c > 0 ? /* @__PURE__ */ m("button", {
			type: "button",
			className: "studio-btn studio-btn-sm",
			onClick: () => a(o.length),
			children: n("artifact.diff.showRest", { n: r.number(c) })
		}) : null] })]
	});
}
var $t = 2200, en = /\.(md|markdown|mdx)$/i;
function tn(e) {
	return en.test(e);
}
function nn(e) {
	return e.toLowerCase().replace(/[^a-z0-9\- ]/g, "").trim().replace(/ /g, "-");
}
function rn(e) {
	return [...e.querySelectorAll("h1,h2,h3,h4,h5,h6")];
}
function an(e, t) {
	let n = t.startsWith("#") ? t.slice(1) : t;
	if (!n) return null;
	for (let t of e.querySelectorAll("[id]")) if (t.id === n) return t;
	let r = n.startsWith("h-") ? n.slice(2) : n;
	return r ? rn(e).find((e) => nn(e.textContent ?? "") === r) ?? null : null;
}
function on(e, t, n = {}) {
	if (!e || !t) return !1;
	let r = an(e, t) ?? (n.text ? rn(e).find((e) => (e.textContent ?? "").trim() === n.text?.trim()) ?? null : null);
	return r ? (typeof r.scrollIntoView == "function" && r.scrollIntoView({
		block: "center",
		behavior: n.reduced ? "auto" : "smooth"
	}), r.classList.add("studio-hl"), window.setTimeout(() => r.classList.remove("studio-hl"), $t), !0) : !1;
}
function sn(e, t, n) {
	let r = xe(), a = n !== null && n.renderable;
	return J(n ? `artifact:${e}:${t}:${n.artifact_id}` : null, i((i) => r.artifact(e, t, n?.artifact_id ?? "", { signal: i }), [
		r,
		e,
		t,
		n?.artifact_id
	]), {
		enabled: a,
		interval: 0,
		revalidateOn: ["intent.updated", "reset"]
	});
}
function cn({ meta: e, body: t, loading: n = !1, error: r = null, anchor: a, capabilities: s, onOpenInEditor: c, headerExtra: d, children: f }) {
	let p = H(), { t: g, fmt: _ } = p, v = ie(), y = u(null), b = tn(e.relpath), S = t?.content ?? null, w = t !== null && (t.encoding !== "utf-8" || t.content === null), T = r?.code === "too_large", E = !!c && s?.open_in_editor?.available === !0;
	o(() => {
		a && S !== null && on(y.current, a, { reduced: v });
	}, [
		a,
		S,
		v
	]);
	let D = l(() => (t?.toc ?? []).filter((e) => e.text.trim() !== ""), [t?.toc]), O = () => c?.(e.relpath), k = i((e) => c?.(e), [c]);
	return /* @__PURE__ */ h("section", {
		className: "studio-pane studio-artifact",
		"aria-label": g("artifact.pane.label", { name: e.name }),
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-pane-head",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "doc",
						size: 13
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-pane-nm studio-mono studio-trunc",
						title: e.name,
						children: e.name
					}),
					/* @__PURE__ */ m(X, { children: g(`artifact.kind.${e.kind}`) }),
					e.stage ? /* @__PURE__ */ m(X, {
						icon: "map",
						title: g("artifact.meta.stage"),
						children: e.stage
					}) : null,
					e.unit ? /* @__PURE__ */ m(X, {
						icon: "intent",
						title: g("artifact.meta.unit"),
						children: e.unit
					}) : null,
					d
				]
			}),
			/* @__PURE__ */ h("dl", {
				className: "studio-pane-facts",
				children: [
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: g("artifact.meta.size") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: je(p, e.size)
					})] }),
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: g("artifact.meta.updated") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: _.dateTime(e.mtime)
					})] }),
					e.sha256 ? /* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: g("artifact.meta.sha256") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono studio-trunc",
						title: e.sha256,
						children: e.sha256.slice(0, 12)
					})] }) : null
				]
			}),
			D.length >= 2 && b && S !== null ? /* @__PURE__ */ m("nav", {
				className: "studio-toc",
				"aria-label": g("artifact.toc.label"),
				children: D.map((e, t) => /* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-toc-item",
					"data-level": e.level,
					onClick: () => on(y.current, e.anchor, {
						reduced: v,
						text: e.text
					}),
					children: e.text
				}, `${e.anchor}-${t}`))
			}) : null,
			/* @__PURE__ */ h("div", {
				className: "studio-pane-body",
				ref: y,
				children: [
					t?.truncated ? /* @__PURE__ */ h("p", {
						className: "studio-notrendered-t",
						children: [
							/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 13
							}),
							" ",
							g("artifact.truncated")
						]
					}) : null,
					e.renderable ? T ? /* @__PURE__ */ m(ln, {
						title: g("artifact.tooLarge.title"),
						body: g("artifact.tooLarge.body", {
							size: je(p, un(r?.details.size, e.size)),
							cap: je(p, un(r?.details.cap, null))
						})
					}) : r ? /* @__PURE__ */ h("p", {
						className: "studio-artifact-error",
						role: "status",
						children: [
							/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 13
							}),
							" ",
							r.known ? g(`errors.${r.code}`) : r.message
						]
					}) : n && S === null ? /* @__PURE__ */ m(x, { rows: 6 }) : w ? /* @__PURE__ */ m(ln, {
						title: g("artifact.binary.title"),
						body: g("artifact.binary.body")
					}) : S === null ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("artifact.empty")
					}) : S.trim() === "" ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("artifact.blank")
					}) : b ? /* @__PURE__ */ m("div", {
						className: "msg-content studio-md",
						children: /* @__PURE__ */ m(C, {
							content: S,
							...E ? { onFileOpen: k } : {}
						})
					}) : /* @__PURE__ */ m("pre", {
						className: "studio-plain studio-mono",
						children: S
					}) : /* @__PURE__ */ m(ln, {
						title: g("artifact.notRendered.title"),
						body: g("artifact.notRendered.body", {
							size: je(p, e.size),
							kind: g(`artifact.kind.${e.kind}`)
						})
					}),
					f
				]
			}),
			/* @__PURE__ */ h("footer", {
				className: "studio-pane-foot",
				children: [
					/* @__PURE__ */ m("span", {
						className: "studio-mono studio-wrap-any studio-grow",
						children: e.relpath
					}),
					/* @__PURE__ */ m(X, {
						icon: "lock",
						children: g("artifact.readOnly")
					}),
					E ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						onClick: O,
						children: [
							/* @__PURE__ */ m(Y, {
								name: "external",
								size: 13
							}),
							" ",
							g("artifact.openInEditor")
						]
					}) : null
				]
			})
		]
	});
}
function ln({ title: e, body: t }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-notrendered",
		role: "status",
		children: [/* @__PURE__ */ h("p", {
			className: "studio-notrendered-t",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}),
				" ",
				e
			]
		}), /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: t
		})]
	});
}
function un(e, t) {
	return typeof e == "number" && Number.isFinite(e) ? e : t;
}
//#endregion
//#region src/artifacts/FindingList.tsx
var dn = [
	"blocker",
	"advisory",
	"unknown",
	"resolved"
], fn = {
	blocker: "danger",
	advisory: "warn",
	resolved: "ok",
	unknown: "neutral"
}, pn = {
	blocker: "warn",
	advisory: "info",
	resolved: "check",
	unknown: "info"
};
function mn(e) {
	return e.map((e, t) => ({
		finding: e,
		index: t + 1
	}));
}
function hn(e) {
	let t = /* @__PURE__ */ new Map();
	for (let e of dn) t.set(e, []);
	for (let n of mn(e)) {
		let e = dn.includes(n.finding.level) ? n.finding.level : "unknown";
		t.get(e)?.push(n);
	}
	for (let e of dn) t.get(e)?.length === 0 && t.delete(e);
	return t;
}
function gn({ findings: e, grouped: t = !1, onOpenAnchor: n, anchor: r, sourceNote: i }) {
	let { t: a, fmt: s } = H(), c = ie(), d = u(null), f = l(() => t ? hn(e) : null, [t, e]), p = l(() => mn(e), [e]);
	return o(() => {
		r && e.length !== 0 && on(d.current, r, { reduced: c });
	}, [
		r,
		e.length,
		c
	]), e.length === 0 ? null : /* @__PURE__ */ h("div", {
		className: "studio-findings",
		ref: d,
		children: [f ? [...f.entries()].map(([e, t]) => /* @__PURE__ */ h("section", {
			className: "studio-finding-group",
			"aria-labelledby": `fg-${e}`,
			children: [/* @__PURE__ */ h("h4", {
				id: `fg-${e}`,
				children: [/* @__PURE__ */ m(Y, {
					name: pn[e],
					size: 13
				}), a(`review.group.${e}`, { n: s.number(t.length) })]
			}), t.map((e) => /* @__PURE__ */ m(_n, {
				item: e,
				onOpenAnchor: n,
				anchorable: !!n
			}, e.index))]
		}, e)) : p.map((e) => /* @__PURE__ */ m(_n, {
			item: e,
			onOpenAnchor: n,
			anchorable: !!n
		}, e.index)), i ? /* @__PURE__ */ h("p", {
			className: "studio-muted studio-finding-source",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "lock",
					size: 13
				}),
				" ",
				i
			]
		}) : null]
	});
}
function _n({ item: e, onOpenAnchor: t, anchorable: n }) {
	let { t: r, fmt: i } = H(), { finding: a, index: o } = e, s = dn.includes(a.level) ? a.level : "unknown", c = [];
	return a.reviewer && c.push(a.reviewer), a.iteration !== null && c.push(r("review.iteration", { n: i.number(a.iteration) })), /* @__PURE__ */ h("article", {
		className: "studio-finding",
		"data-level": s,
		id: `f-${o}`,
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-finding-head",
				children: [
					/* @__PURE__ */ m(X, {
						tone: fn[s],
						icon: pn[s],
						children: r(`review.level.${s}`)
					}),
					c.length > 0 ? /* @__PURE__ */ m("span", {
						className: "studio-finding-meta studio-mono",
						children: c.join(" · ")
					}) : null,
					a.anchor && t ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-anchor-link",
						onClick: () => t(a.anchor),
						children: [
							/* @__PURE__ */ m(Y, {
								name: "link",
								size: 13
							}),
							" ",
							r("review.inArtifact")
						]
					}) : null
				]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-finding-title",
				children: a.title
			}),
			a.quote ? /* @__PURE__ */ m("p", {
				className: "studio-finding-quote",
				children: a.quote
			}) : null,
			!a.anchor && n ? /* @__PURE__ */ m("p", {
				className: "studio-finding-noanchor studio-muted",
				children: r("review.noAnchor")
			}) : null
		]
	});
}
//#endregion
//#region src/artifacts/ArtifactsTab.tsx
function vn(e) {
	return !!e && /^f-\d+$/.test(e);
}
function yn({ repoId: e, intentKey: t, artifacts: n, stage: r, unit: a, kind: o, artifactId: s, onSelectArtifact: c, findings: u, anchor: f, capabilities: p, onOpenInEditor: g, onOpenAnchor: _, children: v }) {
	let { t: y, fmt: b } = H(), C = xe(), [w, T] = d(""), E = n != null, D = J(E ? null : `artifacts:${e}:${t}:${r ?? ""}:${a ?? ""}:${o ?? ""}`, i((n) => C.artifacts(e, t, {
		stage: r,
		unit: a,
		kind: o
	}, { signal: n }), [
		C,
		e,
		t,
		r,
		a,
		o
	]), {
		interval: 0,
		revalidateOn: ["intent.updated", "reset"]
	}), O = E ? n : D.data?.artifacts ?? [], k = E ? !1 : D.data?.truncated ?? !1, A = c ? s ?? "" : w || (s ?? ""), j = O.find((e) => e.artifact_id === A) ?? null, M = j ?? O[0] ?? null, N = A !== "" && j === null && O.length > 0, P = (e) => {
		c ? c(e) : T(e);
	}, F = sn(e, t, M), I = vn(f) ? f : void 0, L = vn(f) ? void 0 : f, R = u ?? F.data?.review?.findings ?? [], ee = R.filter((e) => e.level === "blocker").length, z = D.error, B = l(() => /* @__PURE__ */ m(Y, {
		name: "doc",
		size: 18
	}), []);
	return !E && D.loading ? /* @__PURE__ */ m(x, { rows: 5 }) : z ? /* @__PURE__ */ h("p", {
		className: "studio-artifact-error",
		role: "status",
		children: [
			/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 13
			}),
			" ",
			z.known ? y(`errors.${z.code}`) : z.message
		]
	}) : O.length === 0 ? /* @__PURE__ */ m(S, {
		icon: B,
		title: y("artifact.empty.title"),
		subtitle: y("artifact.empty.body")
	}) : /* @__PURE__ */ h("div", {
		className: "studio-artifacts",
		children: [
			O.length > 1 ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
					name: "doc",
					size: 13
				}), y("artifact.block.files")] }), /* @__PURE__ */ m(Jt, {
					artifacts: O,
					selectedId: M?.artifact_id,
					onSelect: P,
					truncated: k
				})]
			}) : null,
			N ? /* @__PURE__ */ h("p", {
				className: "studio-muted studio-artlist-note",
				role: "status",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}),
					" ",
					y("artifact.list.missing")
				]
			}) : null,
			M ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
					name: "doc",
					size: 13
				}), y("artifact.block.produced")] }), /* @__PURE__ */ h("div", {
					className: "studio-compare",
					"data-panes": R.length > 0 ? "two" : "one",
					children: [/* @__PURE__ */ m(cn, {
						meta: M,
						body: F.data,
						loading: F.loading,
						error: F.error,
						anchor: L,
						capabilities: p,
						...g ? { onOpenInEditor: g } : {},
						children: F.data ? /* @__PURE__ */ m(Qt, {
							prior: F.data.prior,
							name: M.name
						}) : null
					}), R.length > 0 ? /* @__PURE__ */ h("aside", {
						className: "studio-pane studio-reviewpane",
						"aria-label": y("review.pane.label", { name: M.name }),
						children: [
							/* @__PURE__ */ h("header", {
								className: "studio-pane-head",
								children: [
									/* @__PURE__ */ m(Y, {
										name: "review",
										size: 13
									}),
									/* @__PURE__ */ m("span", {
										className: "studio-pane-nm studio-grow",
										children: y("review.pane.title")
									}),
									ee > 0 ? /* @__PURE__ */ m(X, {
										tone: "danger",
										icon: "warn",
										children: y("review.openBlockers", { n: b.number(ee) })
									}) : null
								]
							}),
							/* @__PURE__ */ m("div", {
								className: "studio-pane-body",
								children: /* @__PURE__ */ m(gn, {
									findings: R,
									..._ ? { onOpenAnchor: _ } : {},
									...I ? { anchor: I } : {}
								})
							}),
							/* @__PURE__ */ m("footer", {
								className: "studio-pane-foot",
								children: /* @__PURE__ */ m("span", {
									className: "studio-muted",
									children: y("review.quotedVerbatim")
								})
							})
						]
					}) : null]
				})]
			}) : null,
			v
		]
	});
}
//#endregion
//#region src/map/ArtifactView.tsx
function bn({ api: e, route: t, go: n }) {
	let { t: r } = H(), { repo: a, intent: o, stage: s, unit: c, artifact: l } = t, u = J(`map-artifacts:${a}:${o}:${s}:${c}`, i((t) => e.artifacts(a, o, {
		stage: s,
		unit: c || void 0
	}, { signal: t }), [
		e,
		a,
		o,
		s,
		c
	]), {
		interval: 0,
		revalidateOn: ["intent.updated", "reset"]
	}), d = u.data?.artifacts.filter((e) => e.stage === s && (e.unit ?? "") === c) ?? [], f = d.find((e) => e.artifact_id === l);
	return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ h("section", {
			className: "studio-page",
			"aria-label": r("artifact.block.files"),
			children: [
				/* @__PURE__ */ h("div", {
					className: "studio-spread",
					children: [/* @__PURE__ */ h("h1", { children: [
						/* @__PURE__ */ m(Y, {
							name: "doc",
							size: 18
						}),
						" ",
						r("artifact.block.files")
					] }), /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => n({
							view: "map",
							action: "",
							artifact: "",
							anchor: ""
						}),
						children: [
							/* @__PURE__ */ m(Y, {
								name: "back",
								size: 14
							}),
							" ",
							r("nav.map")
						]
					})]
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-mono studio-wrap-any",
					children: [
						a,
						o,
						s,
						c
					].filter(Boolean).join(" / ")
				}),
				/* @__PURE__ */ h("p", {
					className: "studio-muted",
					children: [
						/* @__PURE__ */ m(X, {
							icon: "lock",
							children: r("artifact.readOnly")
						}),
						" ",
						r("map.inspector.artifactsNote")
					]
				}),
				u.error ? /* @__PURE__ */ h("div", {
					className: "studio-banner",
					"data-tone": "danger",
					role: "status",
					children: [/* @__PURE__ */ m("span", {
						className: "studio-grow",
						children: u.error.known ? r(`errors.${u.error.code}`) : u.error.message
					}), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => void u.refresh(),
						children: r("common.retry")
					})]
				}) : u.data ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(Jt, {
					artifacts: d,
					selectedId: f?.artifact_id,
					onSelect: (e) => n({
						action: "",
						artifact: e,
						anchor: ""
					}),
					truncated: u.data.truncated
				}), f ? /* @__PURE__ */ m(yn, {
					repoId: a,
					intentKey: o,
					artifacts: [f],
					artifactId: f.artifact_id,
					anchor: t.anchor,
					onOpenAnchor: (e) => n({
						action: "",
						anchor: e
					})
				}, f.artifact_id) : u.data.truncated ? null : /* @__PURE__ */ h("p", {
					className: "studio-artifact-error",
					role: "status",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}),
						" ",
						r("errors.artifact_not_found")
					]
				})] }) : /* @__PURE__ */ m(x, { rows: 5 })
			]
		})
	});
}
//#endregion
//#region src/artifacts/ReviewTab.tsx
function xn({ repoId: e, intentKey: t, review: n, anchor: r, onOpenAnchor: a }) {
	let { t: o, fmt: s } = H(), c = xe(), u = J(`review:${e}:${t}`, i((n) => c.review(e, t, { signal: n }), [
		c,
		e,
		t
	]), {
		interval: 0,
		revalidateOn: ["intent.updated", "reset"]
	}), d = u.data, f = n ?? null, p = f ? f.findings : d?.findings ?? [], g = f ? f.verdict : d?.verdict ?? null, _ = f?.reviewer ?? d?.reviewer ?? null, v = f?.review_class ?? d?.review_class ?? null, y = d?.revisions ?? null, b = d?.receipts ?? [], C = d?.stage ?? null, w = l(() => /* @__PURE__ */ m(Y, {
		name: "review",
		size: 18
	}), []);
	return u.loading && !f ? /* @__PURE__ */ m(x, { rows: 5 }) : p.length === 0 && b.length === 0 && g === null ? /* @__PURE__ */ m(S, {
		icon: w,
		title: o("review.empty.title"),
		subtitle: o("review.empty.body")
	}) : /* @__PURE__ */ h("div", {
		className: "studio-review",
		children: [
			u.error ? /* @__PURE__ */ h("p", {
				className: "studio-artifact-error",
				role: "status",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}),
					" ",
					u.error.known ? o(`errors.${u.error.code}`) : u.error.message
				]
			}) : null,
			g ? /* @__PURE__ */ h("p", {
				className: "studio-review-verdict",
				children: [
					/* @__PURE__ */ m("span", {
						className: "studio-muted",
						children: o("review.verdict")
					}),
					" ",
					/* @__PURE__ */ m(X, {
						icon: "review",
						mono: !0,
						children: g
					}),
					C ? /* @__PURE__ */ m(X, {
						icon: "map",
						mono: !0,
						title: o("review.stage"),
						children: C
					}) : null
				]
			}) : null,
			p.length > 0 ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
					name: "review",
					size: 13
				}), o("review.block.findings")] }), /* @__PURE__ */ m(gn, {
					findings: p,
					grouped: !0,
					sourceNote: o("review.quotedVerbatim"),
					...a ? { onOpenAnchor: a } : {},
					...r ? { anchor: r } : {}
				})]
			}) : null,
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
					name: "lock",
					size: 13
				}), o("review.block.contract")] }), /* @__PURE__ */ h("div", {
					className: "studio-evgrid",
					children: [
						/* @__PURE__ */ m(Sn, {
							label: o("review.contract.class"),
							value: v ?? o("review.contract.none"),
							sub: o("review.contract.classSub")
						}),
						/* @__PURE__ */ m(Sn, {
							label: o("review.contract.reviewer"),
							value: _ ?? o("review.contract.none"),
							sub: o("review.contract.reviewerSub")
						}),
						/* @__PURE__ */ m(Sn, {
							label: o("review.contract.revisions"),
							value: y === null ? o("review.contract.none") : s.number(y),
							sub: o("review.contract.revisionsSub")
						})
					]
				})]
			}),
			b.length > 0 ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
						name: "activity",
						size: 13
					}), o("review.block.receipts")] }),
					/* @__PURE__ */ h("table", {
						className: "studio-tbl",
						children: [/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: o("review.receipt.event")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: o("review.receipt.at")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: o("review.receipt.iteration")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: o("review.receipt.verdict")
							})
						] }) }), /* @__PURE__ */ m("tbody", { children: b.map((e, t) => /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: e.event
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: s.dateTime(e.ts)
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: e.iteration === null ? "" : s.number(e.iteration)
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: e.verdict ?? ""
							})
						] }, `${e.event}-${e.ts}-${t}`)) })]
					}),
					/* @__PURE__ */ h("p", {
						className: "studio-consequence",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}), o("review.receipt.note")]
					})
				]
			}) : null
		]
	});
}
function Sn({ label: e, value: t, sub: n }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-ev",
		children: [
			/* @__PURE__ */ m("span", {
				className: "studio-ev-src",
				children: e
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-ev-val studio-mono",
				children: t
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-ev-sub",
				children: n
			})
		]
	});
}
//#endregion
//#region src/lib/wire.generated.ts
var Cn = {
	APPROVE: "Approve",
	REQUEST_CHANGES_PREFIX: "Request Changes: ",
	ACCEPT_AS_IS: "Accept as-is",
	APPROVE_PLAN: "Approve Plan",
	LOOKS_CORRECT: "Looks correct",
	SUMMARY_REQUEST_CHANGES_PREFIX: "Request changes: ",
	ANSWER_LINE: "Q{index}: {answer}",
	ANSWER_JOINER: "\n",
	MULTI_SELECT_JOINER: ", ",
	RUN: "/aidlc",
	RESUME: "/aidlc --resume",
	SCOPE_PREFIX: "/aidlc --scope ",
	PREPARE_COMMIT: "Please prepare a commit for the current AI-DLC changes. Do not push.",
	GROUPED_ANSWER_SUFFIX: "\n\nStudio grouped-answer delivery: this is one human reply. Apply each Q<n> answer to its matching [Answer] tag. Record all Q<n> answers together in ONE aidlc-log.ts answer --details call, preserving their text; do not record separate answer receipts for each question. Then present the next human checkpoint and wait."
}, wn = 8e3, Tn = 8e3;
function En(e, t, n = !1) {
	let r = new Map(t.options.map((e) => [e.letter, e])), i = e.option_letters;
	if (r.size !== t.options.length || new Set(i).size !== i.length || i.some((e) => !r.has(e)) || i.length > 1 && !t.multi_select) return null;
	let a = (e.free_text ?? "").trim();
	if (n && /^free text(?: note)?$/.test(a.toLowerCase().replace(/-/g, " ").replace(/\s+/g, " ")) || (i.some((e) => r.get(e)?.is_other === !0) || i.length === 0) && !a) return null;
	let o = i.length === 0 ? a : i.map((e) => r.get(e)?.is_other ? a : r.get(e)?.text ?? "").join(Cn.MULTI_SELECT_JOINER);
	return o ? o.length > 8e3 ? null : o : null;
}
function Dn(e) {
	return (e?.questions ?? []).filter((e) => !e.answered);
}
function On(e) {
	let t = e.trim();
	return !t || t.length > 8e3 ? null : t;
}
function kn(e, t, n) {
	if ((t?.unsupported_pending_count ?? 0) > 0 && [
		"answers",
		"confirm_summary",
		"approve_plan",
		"request_plan_changes"
	].includes(e.decision)) return null;
	switch (e.decision) {
		case "approve": return Cn.APPROVE;
		case "accept_as_is": return Cn.ACCEPT_AS_IS;
		case "request_changes":
		case "request_plan_changes": {
			let t = On(e.feedback);
			return t === null ? null : Cn.REQUEST_CHANGES_PREFIX + t;
		}
		case "approve_plan": return Cn.APPROVE_PLAN;
		case "confirm_summary": {
			if (e.choice === "looks_correct") return Cn.LOOKS_CORRECT;
			let t = On(e.feedback);
			return t === null ? null : Cn.SUMMARY_REQUEST_CHANGES_PREFIX + t;
		}
		case "answers": {
			if (t?.mode === "degraded") return null;
			let r = Dn(t);
			if (r.length === 0) return null;
			let i = new Set(r.map((e) => e.index));
			if (i.size !== r.length || r.some((e) => !Number.isInteger(e.index) || e.index < 1) || e.answers.length !== r.length || new Set(e.answers.map((e) => e.index)).size !== r.length || e.answers.some((e) => !i.has(e.index))) return null;
			let a = [];
			for (let n of r) {
				let r = e.answers.find((e) => e.index === n.index), i = r ? En(r, n, t?.origin?.kind === "audit" && n.options.length === 1 && n.options[0]?.is_other === !0) : null;
				if (i === null) return null;
				a.push(i);
			}
			return a.length === 1 ? a[0] ?? null : n ? r.map((e, t) => Cn.ANSWER_LINE.replace("{index}", String(e.index)).replace("{answer}", a[t] ?? "")).join(Cn.ANSWER_JOINER) + Cn.GROUPED_ANSWER_SUFFIX : null;
		}
		case "provide_input":
			if (e.kind === "scope") {
				let t = On(e.scope);
				return t === null ? null : Cn.SCOPE_PREFIX + t;
			}
			return On(e.text);
		case "run": return Cn.RUN;
		case "resume": return Cn.RESUME;
		case "prepare_commit": return Cn.PREPARE_COMMIT;
	}
}
var An = {
	stage: "idle",
	actionId: null,
	receipt: null,
	attempt: null,
	card: null,
	refusal: null,
	staleCard: null
};
function jn(e) {
	return e === "idle" || e === "submitting" || e === "preflight" || e === "refused" || e === "stale";
}
var Mn = /* @__PURE__ */ new Set();
function Nn(e) {
	return Mn.has(e);
}
var Pn = 2e4, Fn = 3, In = 700, Ln = (e) => new Promise((t) => setTimeout(t, e));
function Rn(e) {
	return e.status === 0 || e.status >= 500;
}
function zn(e) {
	return e.body && typeof e.body == "object" && !Array.isArray(e.body) ? e.body : {
		ok: !1,
		error: e.message
	};
}
function Bn(e, t) {
	let n = (e) => e.replace(/\/+$/, "");
	return n(e) === n(t);
}
function Vn(e, t, n, r = !1) {
	let i = e.find((e) => e.key === t);
	return i ? i.project && n && !Bn(i.project, n) || i.agent && i.agent !== "aidlc" ? {
		ok: !1,
		code: "slot_mismatch_preflight"
	} : r && (i.running || i.stopping || i.in_stage_execution || i.pending_approval || i.needs_input || i.queue_depth > 0 || i.subagents_running > 0 || i.deliveries_inflight > 0) ? {
		ok: !1,
		code: "answer_session_busy_preflight"
	} : null : {
		ok: !1,
		code: "slot_missing"
	};
}
async function Hn(e, t) {
	let n = null, r = new Promise((e) => {
		n = setTimeout(() => e({ timedOut: !0 }), t);
	}), i = e.then((e) => ({
		timedOut: !1,
		value: e
	}), (e) => ({
		timedOut: !1,
		error: e
	})), a = await Promise.race([i, r]);
	return n !== null && clearTimeout(n), a.timedOut && e.catch(() => {}), a;
}
function Un(e) {
	let { api: t, onCard: n, onSettled: r, sendTimeoutMs: a = Pn, reportRetries: s = Fn, reportBackoffMs: c = In } = e, [f, p] = d(An), [m, h] = d(!1), g = u({
		api: t,
		onCard: n,
		onSettled: r
	});
	g.current = {
		api: t,
		onCard: n,
		onSettled: r
	};
	let _ = u(!0);
	o(() => (_.current = !0, () => {
		_.current = !1;
	}), []);
	let v = i((e) => {
		_.current && p((t) => ({
			...t,
			...e
		}));
	}, []), y = i((e) => {
		e && g.current.onCard?.(e);
	}, []), b = i(async (e, t, n) => {
		let r = {
			delivery_id: t,
			outcome: n.outcome,
			http_status: n.httpStatus,
			receipt: n.receipt
		}, i = null;
		for (let t = 0; t <= s; t += 1) try {
			let t = await g.current.api.reportDelivery(e, r);
			return y(t.action), {
				...n,
				reported: !0,
				reportError: null,
				receipt: n.receipt
			};
		} catch (e) {
			let n = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
			if (i = n, !Rn(n) || t === s) break;
			c > 0 && await Ln(c * (t + 1));
		}
		return {
			...n,
			reported: !1,
			reportError: i
		};
	}, [
		y,
		c,
		s
	]), x = i(async ({ card: e, payload: t, clientWireText: n, begin: r }) => {
		let i = e.action_id;
		if (!Mn.has(i)) {
			Mn.add(i), h(!0), p({
				...An,
				stage: "submitting",
				actionId: i
			});
			try {
				let o;
				try {
					if (r) o = await r();
					else if (t) o = await g.current.api.submitAction(i, {
						captured: {
							state_hash: e.captured.state_hash,
							boundary_token: e.captured.boundary_token,
							question_digest: e.captured.question_digest,
							stage_attempt: e.captured.stage_attempt,
							evidence_digest: e.captured.evidence_digest,
							is_active: e.captured.is_active
						},
						payload: t,
						client_wire_text: n
					});
					else {
						v({
							stage: "refused",
							refusal: new U("invalid_decision", "no payload to submit", {}, 0)
						}), g.current.onSettled?.();
						return;
					}
				} catch (e) {
					let t = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
					if (t.code === "action_stale") {
						let e = t.details.card, n = e && typeof e == "object" ? e : null;
						y(n), v({
							stage: "stale",
							refusal: t,
							staleCard: n
						});
					} else v({
						stage: "refused",
						refusal: t
					});
					g.current.onSettled?.();
					return;
				}
				v({
					receipt: o,
					stage: "preflight"
				});
				let s = null;
				if (o.lane === "human_lane") try {
					s = Vn(await g.current.api.listSlots(), o.slot_key, e.repo.canonical_path, t?.decision === "answers" && !e.evidence.questions?.origin);
				} catch {
					let e = await b(i, o.delivery_id, {
						outcome: "uncertain",
						httpStatus: null,
						receipt: {
							ok: !1,
							code: "preflight_unavailable"
						}
					});
					v({
						stage: "settled",
						attempt: e
					}), g.current.onSettled?.();
					return;
				}
				if (s) {
					let e = await b(i, o.delivery_id, {
						outcome: s.code === "answer_session_busy_preflight" ? "uncertain" : "not_delivered",
						httpStatus: null,
						receipt: s
					});
					v({
						stage: "settled",
						attempt: e
					}), g.current.onSettled?.();
					return;
				}
				v({ stage: "sending" });
				let c = await Hn(g.current.api.sendToHost(o.host.path, o.host.body), a), l, u = null, d = null;
				if (c.timedOut) l = "uncertain", d = {
					ok: !1,
					code: "send_timeout"
				};
				else if ("error" in c) {
					let e = c.error instanceof U ? c.error : new U("internal_error", String(c.error), {}, 0);
					u = e.status || null, e.status >= 400 && e.status <= 499 ? (l = "not_delivered", d = zn(e)) : (l = "uncertain", d = zn(e));
				} else d = c.value ?? null, l = d?.ok === !0 ? "delivered" : "uncertain";
				v({
					stage: "reporting",
					attempt: {
						outcome: l,
						httpStatus: u,
						receipt: d,
						reported: !1,
						reportError: null
					}
				});
				let f = await b(i, o.delivery_id, {
					outcome: l,
					httpStatus: u,
					receipt: d
				});
				v({
					stage: "settled",
					attempt: f
				}), g.current.onSettled?.();
			} finally {
				Mn.delete(i), _.current && h(!1);
			}
		}
	}, [
		v,
		y,
		b,
		a
	]), S = i(async ({ card: e, payload: t }) => {
		let n = e.action_id;
		if (!Mn.has(n)) {
			Mn.add(n), h(!0), p({
				...An,
				stage: "submitting",
				actionId: n
			});
			try {
				let e = await g.current.api.resolveAction(n, t);
				y(e.action), v({
					stage: "settled",
					card: e.action
				});
			} catch (e) {
				let t = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
				if (t.code === "action_stale") {
					let e = t.details.card, n = e && typeof e == "object" ? e : null;
					y(n), v({
						stage: "stale",
						refusal: t,
						staleCard: n
					});
				} else v({
					stage: "refused",
					refusal: t
				});
			} finally {
				Mn.delete(n), _.current && h(!1), g.current.onSettled?.();
			}
		}
	}, [v, y]), C = i(() => p(An), []);
	return l(() => ({
		state: f,
		busy: m,
		isBusy: Nn,
		submit: x,
		resolve: S,
		reset: C
	}), [
		f,
		m,
		x,
		S,
		C
	]);
}
//#endregion
//#region src/actions/ConfirmPanel.tsx
var Wn = "studio-confirm-panel";
function Gn({ card: e, pending: t, submit: n, busy: r, refreshing: i, acknowledged: a, onAcknowledge: s, onSend: c, onCancel: l, onOpenBlockingAction: d }) {
	let { t: f } = H(), g = u(null), _ = t.spec.decision, v = f(`decision.${_}.label`), y = _ === "resubmit" || _ === "mark_not_delivered", x = t.payload !== null, S = t.spec.lane, C = n.refusal?.code === "repo_busy" ? n.refusal.details.owner : null, w = C && typeof C == "object" && "action_id" in C && typeof C.action_id == "string" ? C.action_id : null;
	o(() => {
		g.current?.focus();
	}, [_]), o(() => {
		let e = (e) => {
			e.key === "Escape" && jn(n.stage) && !r && (e.stopPropagation(), l());
		};
		return window.addEventListener("keydown", e), () => window.removeEventListener("keydown", e);
	}, [
		r,
		l,
		n.stage
	]);
	let T = i ? "confirm.blocked.refreshing" : t.blockedKey ? t.blockedKey : x && t.wire === null ? "confirm.blocked.noWireText" : y && !a ? "confirm.blocked.acknowledge" : null, E = n.stage === "settled" && n.actionId === e.action_id, D = n.receipt?.wire_text ?? null, O = E && x && D !== null && D !== t.wire;
	return /* @__PURE__ */ h("section", {
		className: "studio-confirm",
		id: Wn,
		role: "group",
		"aria-label": f("confirm.label"),
		children: [
			/* @__PURE__ */ h("p", {
				className: "studio-ch",
				ref: g,
				tabIndex: -1,
				children: [/* @__PURE__ */ m(Y, {
					name: "send",
					size: 14
				}), f(`confirm.title.${_}`)]
			}),
			x ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("p", {
				className: "studio-confirm-label",
				children: f("confirm.labelSends", {
					label: v,
					wire: t.wire ?? f("confirm.noWireText")
				})
			}), t.wire === null ? null : /* @__PURE__ */ m("pre", {
				className: "studio-sendtext studio-wrap-any",
				children: t.wire
			})] }) : /* @__PURE__ */ m("p", {
				className: "studio-confirm-label",
				children: f(S === "host_control" ? "confirm.hostControl" : "confirm.studioOnly", { label: v })
			}),
			/* @__PURE__ */ h("p", {
				className: "studio-route studio-muted",
				children: [/* @__PURE__ */ m(Y, {
					name: "info",
					size: 13
				}), x ? f("confirm.routing", {
					session: e.evidence.session?.session_key ?? f("common.unavailable"),
					repo: e.repo.label || e.repo.repo_id,
					intent: e.intent.slug || e.intent.intent_dir
				}) : f(`confirm.consequence.${_}`)]
			}),
			T ? /* @__PURE__ */ h("p", {
				className: "studio-route",
				"data-tone": "danger",
				role: "status",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}), f(T)]
			}) : null,
			w && d ? /* @__PURE__ */ m(b, {
				type: "button",
				onClick: () => d(w),
				children: f("detail.repoBusy.openOwner")
			}) : null,
			y ? /* @__PURE__ */ h("label", {
				className: "studio-ack",
				children: [/* @__PURE__ */ m("input", {
					type: "checkbox",
					checked: a,
					disabled: !jn(n.stage) || r,
					onChange: (e) => s(e.currentTarget.checked)
				}), /* @__PURE__ */ m("span", { children: f(`confirm.acknowledge.${_}`) })]
			}) : null,
			O ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ h("div", {
					className: "studio-grow",
					children: [/* @__PURE__ */ m("p", { children: f("confirm.mismatch") }), /* @__PURE__ */ m("pre", {
						className: "studio-sendtext studio-wrap-any",
						children: D
					})]
				})]
			}) : null,
			/* @__PURE__ */ h("div", {
				className: "studio-confirm-actions",
				children: [
					/* @__PURE__ */ h(b, {
						primary: !0,
						type: "button",
						disabled: T !== null || r || !jn(n.stage),
						onClick: c,
						children: [/* @__PURE__ */ m(Y, {
							name: "check",
							size: 14
						}), f(x ? "confirm.send" : "confirm.apply")]
					}),
					/* @__PURE__ */ m(b, {
						type: "button",
						disabled: r,
						onClick: l,
						children: f("common.cancel")
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-hint studio-muted",
						children: f(x ? "confirm.atMostOnce" : S === "host_control" ? "confirm.hostControlHint" : "confirm.studioOnlyHint")
					})
				]
			})
		]
	});
}
//#endregion
//#region src/actions/ActionBar.tsx
var Kn = [
	"request_changes",
	"request_plan_changes",
	"mark_not_delivered",
	"force_stop"
], qn = {
	approve: "check",
	approve_plan: "check",
	accept_as_is: "check",
	confirm_summary: "check",
	answers: "send",
	provide_input: "send",
	run: "play",
	run_now: "play",
	resume: "play",
	retry_now: "refresh",
	keep_paused: "pause",
	force_stop: "pause",
	prepare_commit: "git",
	reconcile: "refresh",
	resubmit: "send",
	rebind_session: "link",
	acknowledge: "check",
	mark_not_delivered: "warn",
	pick_intent: "intent",
	request_changes: "doc",
	request_plan_changes: "doc"
};
function Jn({ card: e, submit: t, busy: n, refreshing: r, pending: i, onChoose: a, onBackToQueue: o, blockedFor: s }) {
	let { t: c } = H(), l = it(e), u = l ? e.decisions.filter((e) => e.lane !== "human_lane") : e.decisions, d = rt(e), f = e.primary?.decision ?? null, p = [...u.filter((e) => e.decision !== f), ...u.filter((e) => e.decision === f)];
	return t.stage === "settled" && t.actionId === e.action_id && !d ? /* @__PURE__ */ h("div", {
		className: "studio-actionbar",
		children: [/* @__PURE__ */ h("span", {
			className: "studio-hint studio-muted",
			children: [/* @__PURE__ */ m(Y, {
				name: "check",
				size: 13
			}), c("detail.bar.submitted", { id: e.action_id })]
		}), /* @__PURE__ */ h(b, {
			type: "button",
			onClick: o,
			children: [/* @__PURE__ */ m(Y, {
				name: "inbox",
				size: 14
			}), c("detail.bar.showQueue")]
		})]
	}) : /* @__PURE__ */ h("div", {
		className: "studio-actionbar",
		children: [/* @__PURE__ */ h("span", {
			className: "studio-hint studio-muted",
			children: [/* @__PURE__ */ m(Y, {
				name: r ? "refresh" : "info",
				size: 13
			}), l ? c("detail.repoUnavailable.footer") : d ? c("detail.bar.closed") : r ? c("detail.bar.refreshing") : u.length === 0 ? c("detail.bar.noDecisions") : c("detail.bar.hint")]
		}), p.map((e) => {
			let t = s(e), o = i?.spec.decision === e.decision;
			return /* @__PURE__ */ h(b, {
				type: "button",
				primary: e.decision === f,
				danger: Kn.includes(e.decision),
				disabled: n || r && e.lane !== "studio_only" || t !== null && !o,
				"aria-expanded": o,
				"aria-controls": o ? Wn : void 0,
				title: t ? c(t) : void 0,
				onClick: () => a(e),
				children: [qn[e.decision] ? /* @__PURE__ */ m(Y, {
					name: qn[e.decision],
					size: 14
				}) : null, c(e.label_key)]
			}, e.decision);
		})]
	});
}
//#endregion
//#region src/actions/DeliveryStrip.tsx
var Yn = [
	"delivery.step.queued",
	"delivery.step.delivered",
	"delivery.step.processing",
	"delivery.step.stateChanged"
], Xn = [
	"Draft",
	"Queued",
	"Cancelled"
], Zn = ["DeliveryUncertain", "ReconciliationRequired"];
function Qn(e) {
	switch (e) {
		case "Draft":
		case "Queued":
		case "Cancelled": return {
			index: 0,
			failed: !1
		};
		case "Delivering": return {
			index: 1,
			failed: !1
		};
		case "NotDelivered": return {
			index: 1,
			failed: !0
		};
		case "DeliveryUncertain":
		case "ReconciliationRequired": return {
			index: 1,
			failed: !0
		};
		case "Delivered":
		case "Processing": return {
			index: 2,
			failed: !1
		};
		case "Failed": return {
			index: 3,
			failed: !0
		};
		case "ResolvedNoTransition": return {
			index: 3,
			failed: !1
		};
		case "StateChanged": return {
			index: 4,
			failed: !1
		};
	}
}
function $n(e, t, n) {
	return e < t ? "done" : e > t ? "future" : n ? "failed" : "now";
}
var er = {
	done: "check",
	now: "clock",
	future: "clock",
	failed: "warn",
	unchanged: "info",
	cancelled: "close"
};
function tr(e) {
	return e === "StateChanged" ? "ok" : e === "NotDelivered" || e === "Failed" || Zn.includes(e) ? "danger" : e === "Delivering" || e === "Delivered" || e === "Processing" ? "accent" : "neutral";
}
function nr(e, t, n) {
	return t === "ResolvedNoTransition" && n === "answer_not_verified_at_gate" ? e.t("delivery.answerNotVerified") : t === "ResolvedNoTransition" && n === "plan_approval_recorded_before_reset" ? e.t("delivery.previousPlanApproval") : e.t(t === "ResolvedNoTransition" && n === "answer_requires_text" ? "delivery.answerNeedsText" : `enum.actionStatus.${t}`);
}
function rr(e, t) {
	return t === null ? e.t("common.unavailable") : e.t(t ? "delivery.fact.yes" : "delivery.fact.no");
}
function ir({ card: e }) {
	let t = H(), { t: n } = t, { status: r } = e, i = e.delivery, a = Zn.includes(r), o = a && i.delivery_confirmed === !0, { index: s, failed: c } = o ? {
		index: 2,
		failed: !1
	} : Qn(r), l = r === "ResolvedNoTransition", u = l && e.resolution.reason === "answer_not_verified_at_gate", d = l && e.resolution.reason === "plan_approval_recorded_before_reset", f = d ? "delivery.updatedPlanReview" : u ? "delivery.newGateReview" : "delivery.noTransition", p = !Xn.includes(r);
	return /* @__PURE__ */ h("section", {
		className: "studio-delivery",
		"aria-label": n("delivery.label"),
		children: [
			/* @__PURE__ */ m("ol", {
				className: "studio-dsteps",
				children: Yn.map((e, t) => {
					let i = l && t === 3 ? "unchanged" : r === "Cancelled" && t === 0 ? "cancelled" : $n(t, s, c);
					return /* @__PURE__ */ h("li", {
						className: "studio-dstep",
						"data-state": i,
						children: [
							/* @__PURE__ */ m("span", {
								className: "studio-dbullet",
								"aria-hidden": "true",
								children: /* @__PURE__ */ m(Y, {
									name: er[i],
									size: 10,
									strokeWidth: 2.4
								})
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-dstep-label",
								children: n(i === "unchanged" ? d ? "delivery.updatedPlanReview" : u ? "delivery.newGateReview" : "delivery.step.unchanged" : i === "cancelled" ? "enum.actionStatus.Cancelled" : o && t === 2 ? "delivery.step.reconciliation" : e)
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-sr",
								children: n(`delivery.state.${i}`)
							})
						]
					}, e);
				})
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-drow",
				children: [
					/* @__PURE__ */ m(X, {
						tone: tr(r),
						icon: a ? "warn" : void 0,
						children: nr(t, r, e.resolution.reason)
					}),
					i.queued_at ? /* @__PURE__ */ m(X, {
						icon: "clock",
						children: n("delivery.queuedInSlot")
					}) : null,
					l ? /* @__PURE__ */ m(X, {
						icon: "info",
						children: n(f)
					}) : null,
					p && !a ? /* @__PURE__ */ m(X, {
						tone: l ? "neutral" : "ok",
						icon: l ? "info" : "check",
						children: n("delivery.watchingDisk")
					}) : null,
					i.delivered_at ? /* @__PURE__ */ m("span", {
						className: "studio-muted studio-mono studio-dat",
						children: n("delivery.sentAt", { at: K(t, i.delivered_at) })
					}) : null
				]
			}),
			u ? /* @__PURE__ */ h("div", {
				className: "studio-banner studio-dwarn",
				"data-tone": "warn",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ m("p", { children: n("delivery.answerNotVerifiedBody") })]
			}) : null,
			d ? /* @__PURE__ */ h("div", {
				className: "studio-banner studio-dwarn",
				"data-tone": "warn",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ m("p", { children: n("delivery.previousPlanApprovalBody") })]
			}) : null,
			a ? /* @__PURE__ */ h("div", {
				className: "studio-banner studio-dwarn",
				"data-tone": "danger",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ h("div", {
					className: "studio-grow",
					children: [/* @__PURE__ */ m("p", {
						className: "studio-dwarn-title",
						children: n(o ? "delivery.confirmed" : "delivery.uncertain")
					}), /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: n(o ? "delivery.confirmedBody" : "delivery.uncertainBody")
					})]
				})]
			}) : null,
			a ? /* @__PURE__ */ h("dl", {
				className: "studio-dproof",
				children: [
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: n("delivery.fact.transcriptRow") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono studio-wrap-any",
						children: i.transcript_row ? `${i.transcript_row.slot_key} · ${i.transcript_row.ts} · ${i.transcript_row.role}` : n("delivery.fact.absent")
					})] }),
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: n("delivery.fact.slotRanSince") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: rr(t, i.slot_ran_since)
					})] }),
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: n("delivery.fact.diskUnchanged") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: rr(t, i.disk_baseline_unchanged)
					})] }),
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: n("delivery.fact.bootUnchanged") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: rr(t, i.boot_id_unchanged)
					})] }),
					/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("dt", { children: n("delivery.fact.confirmed") }), /* @__PURE__ */ m("dd", {
						className: "studio-mono",
						children: rr(t, i.delivery_confirmed)
					})] })
				]
			}) : null,
			p && i.wire_text ? /* @__PURE__ */ h("div", {
				className: "studio-dsent",
				children: [/* @__PURE__ */ h("p", {
					className: "studio-dsent-head",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "send",
							size: 13
						}),
						" ",
						n("delivery.sentText")
					]
				}), /* @__PURE__ */ m("pre", {
					className: "studio-sendtext studio-wrap-any",
					children: i.wire_text
				})]
			}) : null
		]
	});
}
//#endregion
//#region src/actions/QueueRow.tsx
var ar = {
	gate: "gate",
	question: "question",
	missing_input: "missingInput",
	recovery: "recovery",
	delivery_uncertain: "recovery",
	failure: "fail",
	circuit_breaker: "fail",
	install_conflict: "install",
	budget_stop: "clock",
	revision: "doc",
	run: "play",
	resume: "play",
	force_stop: "pause",
	prepare_commit: "git"
}, or = {
	critical: "danger",
	blocking: "accent",
	attention: "warn",
	info: "neutral"
};
function sr(e) {
	return ar[e.queue_type] ?? ar[e.type] ?? "info";
}
function cr(e) {
	return e.captured.stable === !1 || e.evidence.state?.stable === !1;
}
function lr(e, t) {
	let n = e.stage;
	if (!n) return t;
	let r = [n.number, n.slug || n.name].filter((e) => !!e), i = r.length > 0 ? r.join(" ") : t;
	return n.unit ? `${i} · ${n.unit}` : i;
}
function ur(e) {
	let t = e.repo.label || e.repo.repo_id;
	return e.space && e.space !== "default" ? `${t} · ${e.space}` : t;
}
function dr(e) {
	return e.intent.title || e.intent.slug || e.intent.intent_dir;
}
function fr({ card: e, selected: t, tabbable: n, now: r, onSelect: i }) {
	let a = H(), { t: o } = a, s = cr(e), c = o(`enum.actionType.${e.queue_type}`), l = o(`enum.severity.${e.severity}`), u = it(e), d = u ? o("detail.repoUnavailable.saved") : Ee(a, e.waiting_since, r), f = lr(e, o("common.unavailable")), p = dr(e), g = ur(e), _ = u ? o("detail.repoUnavailable.title") : a.has(e.headline.key) ? Ne(a, e.headline) : c, v = e.primary ? o(e.primary.label_key) : o("queue.noPrimary");
	return /* @__PURE__ */ m("li", {
		className: "studio-qrow",
		children: /* @__PURE__ */ h("button", {
			type: "button",
			className: "studio-qitem",
			"data-severity": e.severity,
			"data-refreshing": s ? "true" : void 0,
			"data-action-id": e.action_id,
			"aria-current": t ? "true" : void 0,
			tabIndex: n ? 0 : -1,
			"aria-label": o("a11y.queueRow", {
				type: c,
				repo: g,
				intent: p,
				stage: f,
				severity: l,
				duration: d,
				primary: v
			}),
			onClick: () => i(e.action_id),
			children: [
				/* @__PURE__ */ h("span", {
					className: "studio-qtop",
					children: [
						/* @__PURE__ */ h("span", {
							className: "studio-qtype",
							children: [/* @__PURE__ */ m(Y, {
								name: sr(e),
								size: 13
							}), c]
						}),
						/* @__PURE__ */ m("span", {
							"aria-hidden": "true",
							className: "studio-qdot",
							children: "·"
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-trunc",
							children: g
						}),
						s ? /* @__PURE__ */ m(X, {
							tone: "warn",
							icon: "refresh",
							className: "studio-qrefresh",
							children: o("queue.refreshing")
						}) : null
					]
				}),
				/* @__PURE__ */ m("span", {
					className: "studio-qtitle",
					children: _
				}),
				/* @__PURE__ */ h("span", {
					className: "studio-qmeta studio-mono",
					children: [
						/* @__PURE__ */ m("span", {
							className: "studio-qwait",
							"data-severity": e.severity,
							children: d
						}),
						/* @__PURE__ */ m(pr, { children: f }),
						/* @__PURE__ */ m(pr, { children: p })
					]
				}),
				/* @__PURE__ */ h("span", {
					className: "studio-qnext",
					children: [/* @__PURE__ */ m(Y, {
						name: "chevron",
						size: 12
					}), v]
				})
			]
		})
	});
}
function pr({ children: e }) {
	return /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("span", {
		"aria-hidden": "true",
		className: "studio-qdot",
		children: "·"
	}), /* @__PURE__ */ m("span", {
		className: "studio-trunc",
		children: e
	})] });
}
var mr = r(fr, (e, t) => e.selected === t.selected && e.tabbable === t.tabbable && e.now === t.now && e.onSelect === t.onSelect && e.card.action_id === t.card.action_id && e.card.updated_at === t.card.updated_at && e.card.status === t.card.status && e.card.repo.availability === t.card.repo.availability && e.card.repo.archived === t.card.repo.archived && e.card.severity === t.card.severity && e.card.queue_type === t.card.queue_type && e.card.waiting_since === t.card.waiting_since && e.card.captured.stable === t.card.captured.stable && e.card.primary?.decision === t.card.primary?.decision);
//#endregion
//#region src/actions/DetailHeader.tsx
function hr({ card: e, onOpenCurrent: t }) {
	let n = H(), { t: r } = n, [a, s] = d(!1), c = u(null);
	o(() => () => {
		c.current !== null && clearTimeout(c.current);
	}, []);
	let l = i(async () => {
		let t = `${window.location.origin}${e.deep_link}`;
		try {
			if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(t);
			else throw Error("no clipboard");
		} catch {
			let e = document.createElement("input");
			e.value = t, e.setAttribute("readonly", "true"), e.style.position = "fixed", e.style.opacity = "0", document.body.appendChild(e), e.select();
			try {
				document.execCommand("copy");
			} catch {
				document.body.removeChild(e);
				return;
			}
			document.body.removeChild(e);
		}
		s(!0), c.current !== null && clearTimeout(c.current), c.current = setTimeout(() => s(!1), 2200);
	}, [e.deep_link]), f = r(`enum.actionType.${e.queue_type}`), p = rt(e), g = it(e), _ = g && !p ? r("detail.repoUnavailable.title") : p ? r("detail.closedTitle", {
		type: f,
		status: nr(n, e.status, e.resolution.reason)
	}) : n.has(e.headline.key) ? Ne(n, e.headline) : f, v = cr(e), y = e.evidence.review?.review_class ?? null, x = e.evidence.state?.revision_count ?? null;
	return /* @__PURE__ */ h("header", {
		className: "studio-detail-head",
		children: [
			/* @__PURE__ */ h("nav", {
				className: "studio-dbreadcrumb studio-mono",
				"aria-label": r("detail.breadcrumb"),
				children: [
					/* @__PURE__ */ h("span", {
						className: "studio-drumb",
						children: [
							/* @__PURE__ */ m(Y, {
								name: sr(e),
								size: 13
							}),
							" ",
							f
						]
					}),
					/* @__PURE__ */ m(gr, { children: e.repo.label || e.repo.repo_id }),
					e.space && e.space !== "default" ? /* @__PURE__ */ m(gr, { children: e.space }) : null,
					/* @__PURE__ */ m(gr, { children: e.intent.slug || e.intent.intent_dir }),
					e.stage ? /* @__PURE__ */ m(gr, { children: lr(e, r("common.unavailable")) }) : null,
					/* @__PURE__ */ h(b, {
						className: "studio-anchor-link",
						onClick: () => void l(),
						title: r("detail.deepLinkTitle"),
						children: [/* @__PURE__ */ m(Y, {
							name: a ? "check" : "link",
							size: 13
						}), r(a ? "detail.copied" : "detail.deepLink")]
					})
				]
			}),
			/* @__PURE__ */ m("h1", {
				className: "studio-dtitle",
				children: _
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-dfacts",
				children: [
					g ? /* @__PURE__ */ m(X, {
						icon: "doc",
						children: r("detail.repoUnavailable.saved")
					}) : p ? /* @__PURE__ */ m(X, {
						icon: "doc",
						children: r("detail.history")
					}) : /* @__PURE__ */ m(X, {
						tone: or[e.severity],
						icon: e.severity === "info" ? "info" : "warn",
						children: r(`enum.severity.${e.severity}`)
					}),
					g ? /* @__PURE__ */ m(X, {
						icon: "clock",
						children: r("detail.repoUnavailable.recordedAt", { at: K(n, e.updated_at) })
					}) : p ? e.resolution.resolved_at ? /* @__PURE__ */ m(X, {
						icon: "clock",
						children: r("detail.closedAt", { at: K(n, e.resolution.resolved_at) })
					}) : null : /* @__PURE__ */ m(X, {
						icon: "clock",
						children: r("detail.waiting", { duration: Ee(n, e.waiting_since) })
					}),
					/* @__PURE__ */ m(X, {
						tone: tr(e.status),
						children: nr(n, e.status, e.resolution.reason)
					}),
					y ? /* @__PURE__ */ m(X, {
						tone: "info",
						icon: "review",
						children: r("detail.reviewClass", { name: y })
					}) : null,
					x !== null && x > 0 ? /* @__PURE__ */ m(X, { children: r("detail.revision", { n: n.fmt.number(x) }) }) : null,
					v ? /* @__PURE__ */ m(X, {
						tone: "warn",
						icon: "refresh",
						children: r("detail.refreshing")
					}) : null,
					/* @__PURE__ */ m(X, {
						mono: !0,
						title: e.action_id,
						children: e.action_id
					}),
					p && t ? /* @__PURE__ */ h(b, {
						onClick: t,
						children: [
							/* @__PURE__ */ m(Y, {
								name: "inbox",
								size: 13
							}),
							" ",
							r("detail.currentActions")
						]
					}) : null
				]
			})
		]
	});
}
function gr({ children: e }) {
	return /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("span", {
		"aria-hidden": "true",
		className: "studio-dsep",
		children: "/"
	}), /* @__PURE__ */ m("span", {
		className: "studio-drumb studio-trunc",
		children: e
	})] });
}
//#endregion
//#region src/actions/DetailTabs.tsx
var _r = {
	decision: "inbox",
	artifacts: "doc",
	review: "review",
	activity: "activity",
	conversation: "slack"
};
function vr(e, t) {
	let n = e.evidence.artifacts.length + (e.install?.drift.length ?? 0), r = e.evidence.review?.findings.length ?? 0, i = t?.transitions.length ?? null, a = [{
		tab: "decision",
		count: null
	}];
	return n > 0 && a.push({
		tab: "artifacts",
		count: n
	}), r > 0 && a.push({
		tab: "review",
		count: r
	}), a.push({
		tab: "activity",
		count: i
	}), e.evidence.session && a.push({
		tab: "conversation",
		count: null
	}), a;
}
function yr({ tabs: e, active: t, onSelect: n }) {
	let { t: r, fmt: a } = H(), o = l(() => e.some((e) => e.tab === t) ? t : e[0]?.tab ?? "decision", [e, t]), s = i((t) => {
		if (t.key !== "ArrowRight" && t.key !== "ArrowLeft" && t.key !== "Home" && t.key !== "End") return;
		let r = e.map((e) => e.tab), i = r.indexOf(o), a;
		a = t.key === "Home" ? 0 : t.key === "End" ? r.length - 1 : t.key === "ArrowRight" ? (i + 1) % r.length : (i - 1 + r.length) % r.length, t.preventDefault();
		let s = r[a];
		s && n(s);
	}, [
		o,
		n,
		e
	]);
	return /* @__PURE__ */ m("div", {
		className: "studio-tabs",
		role: "tablist",
		"aria-label": r("detail.tabsLabel"),
		onKeyDown: s,
		children: ct.filter((t) => e.some((e) => e.tab === t)).map((t) => {
			let i = e.find((e) => e.tab === t), s = t === o;
			return /* @__PURE__ */ h("button", {
				type: "button",
				role: "tab",
				id: `studio-tab-${t}`,
				"aria-selected": s,
				"aria-controls": `studio-panel-${t}`,
				tabIndex: s ? 0 : -1,
				className: "studio-tab",
				onClick: () => n(t),
				children: [
					/* @__PURE__ */ m(Y, {
						name: _r[t],
						size: 13
					}),
					r(`detail.tab.${t}`),
					i?.count !== null && i?.count !== void 0 ? /* @__PURE__ */ m("span", {
						className: "studio-tc studio-mono",
						children: a.number(i.count)
					}) : null
				]
			}, t);
		})
	});
}
//#endregion
//#region src/actions/DetailShell.tsx
var br = {
	feedback: "",
	answers: {},
	scope: "",
	freeText: "",
	inputKind: "free_text",
	summaryChoice: "looks_correct",
	advisorApplied: null
}, xr = "aidlc-studio:draft:";
function Sr(e) {
	try {
		let t = localStorage.getItem(xr + e);
		if (!t) return br;
		let n = JSON.parse(t);
		return !n || typeof n != "object" ? br : {
			...br,
			...n
		};
	} catch {
		return br;
	}
}
function Cr(e, t) {
	try {
		t === br ? localStorage.removeItem(xr + e) : localStorage.setItem(xr + e, JSON.stringify(t));
	} catch {}
}
function wr(e) {
	try {
		localStorage.removeItem(xr + e);
	} catch {}
}
var Tr = /* @__PURE__ */ new Map(), Er = /* @__PURE__ */ new Map();
function Dr(e, t) {
	Tr.set(e, t);
}
function Or(e, t) {
	let n = /* @__PURE__ */ new Map();
	for (let [r, i] of Object.entries(e)) {
		let e = t.exec(r)?.[1];
		if (!e) continue;
		let a = i.default;
		typeof a == "function" && n.set(e, a);
	}
	return n;
}
function kr(e) {
	let t = Or(/* #__PURE__ */ Object.assign({}), /\/templates\/([^/]+)\.tsx$/);
	for (let [e, n] of Or(/* #__PURE__ */ Object.assign({}), /\/templates\/([^/]+)\/index\.tsx$/)) t.set(e, n);
	return Tr.get(e) ?? t.get(e);
}
function Ar(e) {
	let t = Or(/* #__PURE__ */ Object.assign({}), /\/tabs\/([^/]+)\.tsx$/);
	return Er.get(e) ?? t.get(e);
}
function jr(e, t) {
	return Dn(e.evidence.questions).map((e) => {
		let n = t.answers[String(e.index)];
		return {
			index: e.index,
			option_letters: n?.option_letters ?? [],
			free_text: n?.free_text ?? null
		};
	});
}
function Mr(e, t, n) {
	switch (e) {
		case "approve": return { decision: "approve" };
		case "accept_as_is": return { decision: "accept_as_is" };
		case "approve_plan": return { decision: "approve_plan" };
		case "request_changes": return {
			decision: "request_changes",
			feedback: n.feedback
		};
		case "request_plan_changes": return {
			decision: "request_plan_changes",
			feedback: n.feedback
		};
		case "confirm_summary": return n.summaryChoice === "request_changes" || n.feedback.trim() ? {
			decision: "confirm_summary",
			choice: "request_changes",
			feedback: n.feedback
		} : {
			decision: "confirm_summary",
			choice: "looks_correct"
		};
		case "answers": return {
			decision: "answers",
			answers: jr(t, n)
		};
		case "provide_input": return n.inputKind === "scope" ? {
			decision: "provide_input",
			kind: "scope",
			scope: n.scope
		} : {
			decision: "provide_input",
			kind: "free_text",
			text: n.freeText
		};
		case "run": return { decision: "run" };
		case "resume": return { decision: "resume" };
		case "prepare_commit": return { decision: "prepare_commit" };
		default: return null;
	}
}
function Nr(e, t) {
	switch (e) {
		case "resubmit": return {
			decision: "resubmit",
			acknowledged_evidence_sha256: t.acknowledged_evidence_sha256 ?? ""
		};
		case "pick_intent": return {
			decision: "pick_intent",
			intent_dir: t.intent.intent_dir
		};
		case "mark_not_delivered": return { decision: "mark_not_delivered" };
		case "rebind_session":
		case "acknowledge":
		case "reconcile":
		case "retry_now":
		case "keep_paused":
		case "run_now": return { decision: e };
		default: return null;
	}
}
function Pr(e, t) {
	return e ? t ? e.action_id === t.action_id ? e.updated_at === t.updated_at ? e.status_generation >= t.status_generation ? e : t : e.updated_at > t.updated_at ? e : t : t : e : t;
}
var Fr = t(null);
function Ir({ actionId: e, queueCard: t, api: n, route: r, go: a, groupedAnswers: s, onQueueChanged: c }) {
	let { t: u } = H(), [f, p] = d(null), [g, _] = d(null), [v, y] = d(!1), [b, x] = d(br), S = J(e ? `action:${e}` : null, i((t) => n.action(e, { signal: t }), [n, e]), { enabled: !!e });
	o(() => {
		p(null), _(null), y(!1), x(e ? Sr(e) : br);
	}, [e]);
	let C = i((t) => {
		x((n) => {
			let r = {
				...n,
				...t
			};
			return e && Cr(e, r), r;
		});
	}, [e]), w = Un({
		api: n,
		onCard: i((t) => {
			t.action_id === e && p((e) => Pr(e, t));
		}, [e]),
		onSettled: c
	}), { busy: T } = w, E = w.state.actionId === e ? w.state : An, D = S.data?.action.action_id === e ? S.data : null, O = l(() => Pr(f?.action_id === e ? f : null, Pr(D?.action ?? null, t?.action_id === e ? t : null)), [
		f,
		e,
		D,
		t
	]), k = l(() => ({
		draft: b,
		setDraft: C
	}), [b, C]);
	if (o(() => {
		E.stage === "settled" && E.actionId === e && (wr(e), _(null));
	}, [
		E.stage,
		E.actionId,
		e
	]), S.error && [
		"action_not_found",
		"repo_not_found",
		"intent_not_found"
	].includes(S.error.code)) return /* @__PURE__ */ m("section", {
		className: "studio-detail",
		"aria-label": u("detail.label"),
		children: /* @__PURE__ */ h("div", {
			className: "studio-empty studio-detail-empty",
			"data-layout": "stack",
			role: "status",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "inbox",
					size: 18
				}),
				/* @__PURE__ */ m("h1", {
					className: "studio-empty-title",
					children: u("detail.gone.title")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: u("detail.gone.body")
				}),
				/* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-btn",
					onClick: () => a({
						...yt,
						view: "actions"
					}),
					children: u("detail.currentActions")
				})
			]
		})
	});
	if (!e || !O) return /* @__PURE__ */ m("section", {
		className: "studio-detail",
		"aria-label": u("detail.label"),
		children: /* @__PURE__ */ h("div", {
			className: "studio-empty studio-detail-empty",
			"data-layout": "stack",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "inbox",
					size: 18
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-empty-title",
					children: e && S.loading ? u("common.loading") : u("detail.noSelection.title")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: e && S.error ? u(`errors.${S.error.code}`) : u("detail.noSelection.body")
				})
			]
		})
	});
	let A = cr(O), j = vr(O, D), M = j.some((e) => e.tab === r.tab) ? r.tab : ut, N = kr(O.type), P = M === "decision" ? null : Ar(M), F = (e) => a({
		tab: "artifacts",
		anchor: e
	}), I = (e) => {
		if (it(O) && e.lane === "human_lane") return "detail.repoUnavailable.footer";
		if (e.lane === "studio_only") return e.decision === "resubmit" && !O.acknowledged_evidence_sha256 ? "confirm.blocked.evidenceMissing" : null;
		if (A) return "confirm.blocked.refreshing";
		let t = Mr(e.decision, O, b);
		return !t || kn(t, O.evidence.questions, s) !== null ? null : e.decision === "request_changes" || e.decision === "request_plan_changes" || e.decision === "confirm_summary" ? "confirm.blocked.feedbackRequired" : e.decision === "provide_input" ? "confirm.blocked.inputRequired" : e.decision === "answers" ? O.evidence.questions?.mode === "degraded" ? "confirm.blocked.questionsUnavailable" : Dn(O.evidence.questions).length > 1 && !s ? "confirm.blocked.groupedAnswers" : "confirm.blocked.answersIncomplete" : "confirm.blocked.noWireText";
	}, L = (e) => I(e) || (e.lane !== "studio_only" && !O.evidence.session?.slot_key ? "confirm.blocked.sessionUnbound" : E.stage === "refused" && E.actionId === O.action_id && E.refusal ? `errors.${E.refusal.code}` : null), R = g && O.decisions.some((e) => e.decision === g.decision) && !(it(O) && g.lane === "human_lane") ? (() => {
		let e = g.lane === "studio_only" ? null : Mr(g.decision, O, b);
		return {
			spec: g,
			payload: e,
			resolvePayload: g.lane === "studio_only" ? Nr(g.decision, O) : null,
			wire: e ? kn(e, O.evidence.questions, s) : null,
			blockedKey: L(g)
		};
	})() : null, ee = (e) => {
		if (g?.decision === e.decision) {
			_(null);
			return;
		}
		w.reset(), y(!1), _(e);
	}, z = () => {
		if (R) {
			if (R.spec.lane === "studio_only") {
				R.resolvePayload && w.resolve({
					card: O,
					payload: R.resolvePayload
				});
				return;
			}
			if (R.spec.lane === "host_control") {
				w.submit({
					card: O,
					payload: null,
					clientWireText: "",
					begin: () => n.forceStop(O.repo.repo_id, O.intent.intent_key)
				});
				return;
			}
			R.payload && R.wire !== null && w.submit({
				card: O,
				payload: R.payload,
				clientWireText: R.wire
			});
		}
	}, B = E.refusal, V = E.stage === "stale";
	return /* @__PURE__ */ h("section", {
		className: "studio-detail",
		"aria-label": u("detail.label"),
		children: [
			/* @__PURE__ */ m(hr, {
				card: O,
				onOpenCurrent: () => a({
					...yt,
					view: "actions",
					repo: O.repo.repo_id,
					intent: O.intent.intent_key
				})
			}),
			/* @__PURE__ */ m(yr, {
				tabs: j,
				active: M,
				onSelect: (e) => a({ tab: e })
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-detail-body",
				id: `studio-panel-${M}`,
				role: "tabpanel",
				"aria-labelledby": `studio-tab-${M}`,
				tabIndex: -1,
				children: /* @__PURE__ */ h("div", {
					className: "studio-reading",
					children: [
						B ? /* @__PURE__ */ h("div", {
							className: "studio-banner",
							"data-tone": "danger",
							role: V ? "alert" : "status",
							children: [/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 15
							}), /* @__PURE__ */ h("div", {
								className: "studio-grow",
								children: [
									/* @__PURE__ */ m("p", { children: u(`errors.${B.code}`) }),
									/* @__PURE__ */ m("p", {
										className: "studio-muted",
										children: V ? u("detail.staleRefused") : O.delivery.delivered_at || O.delivery.transcript_row ? u("detail.additionalAttemptBlocked") : u("detail.nothingSent")
									}),
									/* @__PURE__ */ m(Lr, {
										refusal: B,
										onOpenAction: (e) => a({
											...yt,
											view: "actions",
											repo: O.repo.repo_id,
											action: e
										})
									})
								]
							})]
						}) : null,
						E.attempt && !E.attempt.reported ? /* @__PURE__ */ h("div", {
							className: "studio-banner",
							"data-tone": "warn",
							role: "alert",
							children: [/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 15
							}), /* @__PURE__ */ m("span", {
								className: "studio-grow",
								children: u("delivery.reportFailed")
							})]
						}) : null,
						!it(O) || O.delivery.delivering_at ? /* @__PURE__ */ m(ir, { card: O }) : null,
						M === "decision" ? it(O) ? /* @__PURE__ */ h("section", {
							className: "studio-block",
							"aria-label": u("detail.repoUnavailable.saved"),
							children: [
								/* @__PURE__ */ m("p", { children: u(O.repo.availability_detail === "path_missing" ? "detail.repoUnavailable.pathMissing" : "detail.repoUnavailable.unreadable") }),
								/* @__PURE__ */ m("p", {
									className: "studio-mono studio-wrap-any",
									children: O.repo.canonical_path
								}),
								/* @__PURE__ */ m("p", {
									className: "studio-muted",
									children: u("detail.repoUnavailable.body")
								}),
								/* @__PURE__ */ h("button", {
									type: "button",
									className: "studio-btn",
									onClick: () => a({
										...yt,
										view: "repos",
										repo: O.repo.repo_id
									}),
									children: [
										/* @__PURE__ */ m(Y, {
											name: "repo",
											size: 14
										}),
										" ",
										u("detail.repoUnavailable.manage")
									]
								})
							]
						}) : N ? /* @__PURE__ */ m(Fr.Provider, {
							value: k,
							children: /* @__PURE__ */ m(N, {
								card: O,
								detail: D,
								draft: b,
								setDraft: C,
								refreshing: A,
								api: n,
								route: r,
								go: a,
								reload: S.refresh
							})
						}) : /* @__PURE__ */ m(Br, { kind: O.type }) : P ? /* @__PURE__ */ m(Fr.Provider, {
							value: k,
							children: /* @__PURE__ */ m(P, {
								card: O,
								detail: D,
								draft: b,
								setDraft: C,
								refreshing: A,
								api: n,
								route: r,
								go: a,
								reload: S.refresh
							})
						}) : M === "artifacts" ? /* @__PURE__ */ m(yn, {
							repoId: O.repo.repo_id,
							intentKey: O.intent.intent_key,
							artifacts: O.evidence.artifacts,
							findings: O.evidence.review?.findings ?? null,
							artifactId: r.artifact,
							onSelectArtifact: (e) => a({ artifact: e }),
							anchor: r.anchor,
							onOpenAnchor: F
						}) : M === "review" ? /* @__PURE__ */ m(xn, {
							repoId: O.repo.repo_id,
							intentKey: O.intent.intent_key,
							review: O.evidence.review,
							anchor: r.anchor,
							onOpenAnchor: F
						}) : M === "activity" ? /* @__PURE__ */ m(Rr, { transitions: D?.transitions ?? [] }) : M === "conversation" ? /* @__PURE__ */ m(zr, { slotKey: O.evidence.session?.slot_key ?? "" }) : /* @__PURE__ */ m(Br, { kind: M })
					]
				})
			}),
			R ? /* @__PURE__ */ m(Gn, {
				card: O,
				pending: R,
				submit: E,
				busy: T,
				refreshing: A && R.spec.lane !== "studio_only",
				acknowledged: v,
				onAcknowledge: y,
				onSend: z,
				onCancel: () => _(null),
				onOpenBlockingAction: (e) => a({
					...yt,
					view: "actions",
					repo: O.repo.repo_id,
					action: e
				})
			}) : null,
			/* @__PURE__ */ m(Jn, {
				card: O,
				submit: E,
				busy: T,
				refreshing: A,
				pending: R,
				onChoose: ee,
				onBackToQueue: () => a({
					action: "",
					artifact: ""
				}),
				blockedFor: I
			})
		]
	});
}
function Lr({ refusal: e, onOpenAction: t }) {
	let { t: n } = H(), r = e.details.owner;
	if (e.code === "repo_busy" && r && typeof r == "object" && "action_id" in r && typeof r.action_id == "string" && r.action_id) return /* @__PURE__ */ m("button", {
		type: "button",
		className: "studio-btn",
		onClick: () => t(r.action_id),
		children: n("detail.repoBusy.openOwner")
	});
	let i = e.details.reasons, a = e.details.findings, o = Array.isArray(i) ? i : Array.isArray(a) ? a : null;
	return !o || o.length === 0 ? null : /* @__PURE__ */ m("ul", {
		className: "studio-refusal-details studio-mono",
		children: o.map((e, t) => /* @__PURE__ */ m("li", { children: typeof e == "string" ? e : JSON.stringify(e) }, t))
	});
}
function Rr({ transitions: e }) {
	let t = H(), { t: n } = t;
	return e.length === 0 ? /* @__PURE__ */ m("div", {
		className: "studio-block",
		children: /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: n("detail.transitions.empty")
		})
	}) : /* @__PURE__ */ h("section", {
		className: "studio-block",
		children: [/* @__PURE__ */ h("h3", { children: [
			/* @__PURE__ */ m(Y, {
				name: "activity",
				size: 13
			}),
			" ",
			n("detail.transitions.title")
		] }), /* @__PURE__ */ m("ol", {
			className: "studio-translist",
			children: e.map((e, r) => /* @__PURE__ */ h("li", { children: [
				/* @__PURE__ */ m("span", {
					className: "studio-mono studio-muted",
					children: K(t, e.at)
				}),
				/* @__PURE__ */ h("span", {
					className: "studio-grow",
					children: [n("detail.transitions.row", {
						from: e.from_status ? n(`enum.actionStatus.${e.from_status}`) : n("detail.transitions.initial"),
						to: nr(t, e.to_status, e.reason)
					}), e.reason ? /* @__PURE__ */ h("span", {
						className: "studio-mono studio-muted",
						children: [" · ", e.reason]
					}) : null]
				}),
				/* @__PURE__ */ m("span", {
					className: "studio-mono studio-muted",
					children: n("detail.transitions.generation", { n: t.fmt.number(e.generation) })
				})
			] }, `${e.at}-${r}`))
		})]
	});
}
function zr({ slotKey: e }) {
	let { t } = H();
	return e ? /* @__PURE__ */ h("section", {
		className: "studio-block",
		children: [
			/* @__PURE__ */ h("h3", { children: [
				/* @__PURE__ */ m(Y, {
					name: "slack",
					size: 13
				}),
				" ",
				t("detail.conversation.title")
			] }),
			/* @__PURE__ */ m("p", {
				className: "studio-muted studio-conv-note",
				children: t("detail.conversation.note")
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-conv",
				children: /* @__PURE__ */ m(g, { slotKey: e })
			})
		]
	}) : /* @__PURE__ */ m("div", {
		className: "studio-block",
		children: /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: t("detail.conversation.none")
		})
	});
}
function Br({ kind: e }) {
	let { t } = H();
	return /* @__PURE__ */ m("div", {
		className: "studio-block",
		children: /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: t("detail.noTemplate", { kind: e })
		})
	});
}
//#endregion
//#region src/actions/QueueFilters.tsx
var Vr = [
	"priority",
	"repo",
	"type",
	"oldest"
];
function Hr() {
	try {
		let e = localStorage.getItem(F);
		return Vr.includes(e) ? e : "priority";
	} catch {
		return "priority";
	}
}
function Ur() {
	let [e, t] = d(Hr);
	return o(() => {
		let e = (e) => {
			(e.key === null || e.key === "aidlc-studio:organize") && t(Hr());
		};
		return window.addEventListener("storage", e), () => window.removeEventListener("storage", e);
	}, []), [e, i((e) => {
		t(e);
		try {
			localStorage.setItem(F, e);
		} catch {}
	}, [])];
}
function Wr({ organize: e, onOrganize: t, query: n, onQuery: r, visible: i, total: a, stale: o }) {
	let { t: s, fmt: c } = H();
	return /* @__PURE__ */ h("div", {
		className: "studio-queue-head",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-queue-title",
				children: [/* @__PURE__ */ m("h2", { children: s("queue.title") }), /* @__PURE__ */ m("span", {
					className: "studio-mono studio-queue-n",
					"aria-live": "polite",
					"data-stale": o ? "true" : void 0,
					children: a === null ? s("common.loading") : s("queue.count", {
						visible: c.number(i),
						total: c.number(a)
					})
				})]
			}),
			/* @__PURE__ */ m(w, {
				className: "studio-queue-search",
				value: n,
				placeholder: s("queue.filterPlaceholder"),
				"aria-label": s("queue.filterLabel"),
				onChange: (e) => r(e.currentTarget.value)
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-queue-organize",
				role: "group",
				"aria-label": s("queue.organizeLabel"),
				children: /* @__PURE__ */ m(T, {
					segments: Vr.map((e) => ({
						key: e,
						label: s(`queue.organize.${e}`)
					})),
					value: e,
					onChange: t,
					layoutId: "aidlc-queue-organize",
					collapse: !1
				})
			})
		]
	});
}
//#endregion
//#region src/actions/QueueGroups.tsx
var Gr = {
	1: "queue.group.recovery",
	2: "queue.group.blocking",
	3: "queue.group.attention",
	4: "queue.group.info"
};
function Kr(e, t, n) {
	switch (t) {
		case "priority": return n.t(Gr[Vt(e)] ?? Gr[4]);
		case "repo": return e.repo.label || e.repo.repo_id;
		case "type": return n.t(`enum.actionType.${e.queue_type}`);
		case "oldest": return n.t("queue.group.oldest");
	}
}
function qr(e, t, n) {
	let r = [];
	for (let i of e) {
		let e = Gt(i, t), a = r[r.length - 1];
		a && a.key === e ? a.cards.push(i) : r.push({
			key: e,
			label: Kr(i, t, n),
			cards: [i]
		});
	}
	return r;
}
function Jr({ group: e, id: t }) {
	return /* @__PURE__ */ h("h3", {
		className: "studio-qgroup",
		id: t,
		children: [/* @__PURE__ */ m("span", {
			className: "studio-grow studio-trunc",
			children: e.label
		}), /* @__PURE__ */ m("span", {
			className: "studio-mono studio-qgroup-n",
			children: e.cards.length
		})]
	});
}
var Yr = 480, Xr = 3e4;
function Zr() {
	let [e, t] = d(() => Date.now());
	return o(() => {
		let e = () => {
			(typeof document > "u" || !document.hidden) && t(Date.now());
		}, n = setInterval(e, Xr);
		return document.addEventListener("visibilitychange", e), () => {
			clearInterval(n), document.removeEventListener("visibilitychange", e);
		};
	}, []), e;
}
function Qr({ cards: e, organize: t, selected: n, onSelect: r, emptyBecauseNothingWaits: a, emptyAction: s }) {
	let { t: f, fmt: p } = H(), g = Zr(), _ = u(null), [v, y] = d(40), b = `${t}:${e.length}:${e[0]?.action_id ?? ""}`;
	o(() => y(40), [b]);
	let x = i(() => {
		let t = _.current;
		t && t.clientHeight !== 0 && (t.scrollHeight - t.scrollTop - t.clientHeight > Yr || y((t) => t >= e.length ? t : t + 40));
	}, [e.length]);
	c(x, [
		x,
		v,
		b
	]);
	let S = l(() => e.slice(0, v), [e, v]), C = $r(S, t), w = l(() => S.some((e) => e.action_id === n) ? n : S[0]?.action_id ?? "", [S, n]), T = i((e) => {
		if (![
			"ArrowDown",
			"ArrowUp",
			"Home",
			"End"
		].includes(e.key)) return;
		let t = _.current;
		if (!t) return;
		let n = [...t.querySelectorAll("button.studio-qitem")];
		if (n.length === 0) return;
		let r = document.activeElement, i = n.findIndex((e) => e === r), a;
		a = e.key === "Home" ? 0 : e.key === "End" ? n.length - 1 : i < 0 ? 0 : e.key === "ArrowDown" ? Math.min(n.length - 1, i + 1) : Math.max(0, i - 1), e.preventDefault(), n[a]?.focus();
	}, []);
	return e.length === 0 ? /* @__PURE__ */ m("div", {
		className: "studio-queue-list studio-queue-empty",
		children: /* @__PURE__ */ h("div", {
			className: "studio-empty",
			"data-layout": "stack",
			children: [
				/* @__PURE__ */ m(Y, {
					name: a ? "check" : "search",
					size: 18
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-empty-title",
					children: f(a ? "queue.empty.title" : "queue.noMatch.title")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: f(a ? "queue.empty.body" : "queue.noMatch.body")
				}),
				a ? s : null
			]
		})
	}) : /* @__PURE__ */ h("div", {
		className: "studio-queue-list",
		ref: _,
		onScroll: x,
		onKeyDown: T,
		children: [C.map((e, t) => {
			let i = `studio-qgroup-${t}-${e.key}`;
			return /* @__PURE__ */ h("section", {
				className: "studio-qsection",
				"aria-labelledby": i,
				children: [/* @__PURE__ */ m(Jr, {
					group: e,
					id: i
				}), /* @__PURE__ */ m("ul", {
					className: "studio-qlist",
					children: e.cards.map((e) => /* @__PURE__ */ m(mr, {
						card: e,
						now: g,
						selected: e.action_id === n,
						tabbable: e.action_id === w,
						onSelect: r
					}, e.action_id))
				})]
			}, `${t}:${e.key}`);
		}), v < e.length ? /* @__PURE__ */ m("p", {
			className: "studio-queue-more studio-muted",
			children: f("queue.more", { n: p.number(e.length - v) })
		}) : null]
	});
}
function $r(e, t) {
	let n = H();
	return l(() => qr(e, t, n), [
		e,
		t,
		n
	]);
}
//#endregion
//#region src/actions/ActionsView.tsx
function ei(e, t, n) {
	return [
		t,
		n,
		ur(e),
		dr(e),
		e.intent.slug,
		e.stage?.slug ?? "",
		e.stage?.number ?? "",
		e.stage?.name ?? "",
		e.stage?.unit ?? "",
		e.action_id
	].join(" ").toLowerCase();
}
function ti({ route: e, go: t }) {
	let n = H(), { t: r } = n, { api: a, actions: s, settings: c, setQueueCount: u } = jl(), [f, p] = Ur(), [g, _] = d(""), v = l(() => s.data ? ot(s.data) : null, [s.data]), y = v?.actions ?? [], x = v?.counts.total ?? null, S = l(() => {
		let e = g.trim().toLowerCase();
		return Wt(e ? y.filter((t) => {
			let i = r(`enum.actionType.${t.queue_type}`);
			return ei(t, i, n.has(t.headline.key) ? r(t.headline.key, {}) : i).includes(e);
		}) : y, f, new Intl.Collator(n.locale, {
			numeric: !0,
			sensitivity: "base"
		}));
	}, [
		y,
		g,
		f,
		n,
		r
	]), C = g.trim().length > 0;
	o(() => (u(C && s.data ? S.length : null), () => u(null)), [
		C,
		S.length,
		s.data,
		u
	]);
	let w = i((e) => {
		t({
			action: e,
			tab: "decision",
			artifact: "",
			anchor: ""
		});
	}, [t]), T = l(() => s.data?.actions.find((t) => t.action_id === e.action) ?? null, [s.data, e.action]), E = c.data?.capabilities.grouped_answers?.available === !0;
	return !e.action && e.tab === "artifacts" && e.artifact && e.repo && e.intent && e.stage ? /* @__PURE__ */ m(bn, {
		api: a,
		route: e,
		go: t
	}, `${e.repo}:${e.intent}:${e.stage}:${e.unit}`) : /* @__PURE__ */ h("div", {
		className: "studio-ac",
		children: [/* @__PURE__ */ h("aside", {
			className: "studio-queue",
			"aria-label": r("queue.label"),
			children: [/* @__PURE__ */ m(Wr, {
				organize: f,
				onOrganize: p,
				query: g,
				onQuery: _,
				visible: S.length,
				total: x,
				stale: s.stale
			}), s.error && !s.data ? /* @__PURE__ */ m("div", {
				className: "studio-queue-list studio-queue-empty",
				children: /* @__PURE__ */ h("div", {
					className: "studio-empty",
					"data-layout": "stack",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 18
						}),
						/* @__PURE__ */ m("p", {
							className: "studio-empty-title",
							children: r(`errors.${s.error.code}`)
						}),
						/* @__PURE__ */ m(b, {
							onClick: () => void s.refresh(),
							children: r("common.retry")
						})
					]
				})
			}) : /* @__PURE__ */ m(Qr, {
				cards: S,
				organize: f,
				selected: e.action,
				onSelect: w,
				emptyBecauseNothingWaits: y.length === 0,
				emptyAction: /* @__PURE__ */ h(b, {
					onClick: () => t({ view: "map" }),
					children: [/* @__PURE__ */ m(Y, {
						name: "map",
						size: 14
					}), r("queue.empty.action")]
				})
			})]
		}), /* @__PURE__ */ m(Ir, {
			actionId: e.action,
			queueCard: T,
			api: a,
			route: e,
			go: t,
			groupedAnswers: E,
			onQueueChanged: s.refresh
		})]
	});
}
//#endregion
//#region src/templates/DecisionControls.tsx
function Z({ title: e, icon: t, id: n, children: r }) {
	return /* @__PURE__ */ h("section", {
		className: "studio-block",
		...n ? { id: n } : {},
		children: [/* @__PURE__ */ h("h3", { children: [t ? /* @__PURE__ */ m(Y, {
			name: t,
			size: 13
		}) : null, /* @__PURE__ */ m("span", { children: e })] }), r]
	});
}
function ni({ tone: e, children: t }) {
	return /* @__PURE__ */ m("div", {
		className: "studio-brief",
		...e ? { "data-tone": e } : {},
		children: t
	});
}
function ri({ icon: e = "info", tone: t, label: n, children: r }) {
	return /* @__PURE__ */ h("p", {
		className: "studio-consequence",
		...t ? { "data-tone": t } : {},
		children: [/* @__PURE__ */ m(Y, {
			name: e,
			size: 13
		}), /* @__PURE__ */ h("span", { children: [n ? /* @__PURE__ */ h("b", { children: [n, " "] }) : null, r] })]
	});
}
function ii({ items: e }) {
	let { t } = H();
	return /* @__PURE__ */ m("ul", {
		className: "studio-crit",
		children: e.map((e, n) => /* @__PURE__ */ h("li", {
			...e.id ? { id: e.id } : {},
			"data-met": String(e.met),
			children: [e.met === !0 ? /* @__PURE__ */ m(Y, {
				name: "check",
				size: 13,
				className: "studio-met",
				label: t("template.common.met")
			}) : e.met === !1 ? /* @__PURE__ */ m(Y, {
				name: "warn",
				size: 13,
				className: "studio-unmet",
				label: t("template.common.notMet")
			}) : /* @__PURE__ */ m(Y, {
				name: "info",
				size: 13,
				className: "studio-unknown",
				label: t("template.common.unknownMet")
			}), /* @__PURE__ */ h("span", {
				className: "studio-grow",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-crit-txt",
					children: e.text
				}), e.why ? /* @__PURE__ */ m("span", {
					className: "studio-crit-why",
					children: e.why
				}) : null]
			})]
		}, e.id ?? n))
	});
}
function ai({ steps: e }) {
	return /* @__PURE__ */ m("ul", {
		className: "studio-steplist",
		children: e.map((e, t) => /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ m(Y, {
			name: "chevron",
			size: 13
		}), /* @__PURE__ */ m("span", { children: e })] }, t))
	});
}
function oi({ children: e }) {
	return /* @__PURE__ */ m("div", {
		className: "studio-evgrid",
		children: e
	});
}
function si({ src: e, icon: t, value: n, sub: r, conflict: i }) {
	let { t: a } = H();
	return /* @__PURE__ */ h("div", {
		className: "studio-ev",
		...i ? { "data-conflict": "true" } : {},
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-ev-src studio-row",
				children: [
					t ? /* @__PURE__ */ m(Y, {
						name: t,
						size: 11,
						strokeWidth: 2
					}) : null,
					/* @__PURE__ */ m("span", { children: e }),
					i ? /* @__PURE__ */ m(X, {
						tone: "danger",
						icon: "warn",
						children: a("template.common.conflict")
					}) : null
				]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-ev-val studio-mono",
				children: n
			}),
			r ? /* @__PURE__ */ m("div", {
				className: "studio-ev-sub",
				children: r
			}) : null
		]
	});
}
function ci({ title: e, icon: t, badge: n, footer: r, children: i }) {
	return /* @__PURE__ */ h("section", {
		className: "studio-pane",
		"aria-label": e,
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-pane-head",
				children: [
					t ? /* @__PURE__ */ m(Y, {
						name: t,
						size: 13
					}) : null,
					/* @__PURE__ */ m("span", {
						className: "studio-pane-nm studio-mono studio-trunc",
						title: e,
						children: e
					}),
					n
				]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-pane-body",
				children: i
			}),
			r ? /* @__PURE__ */ m("footer", {
				className: "studio-pane-foot",
				children: r
			}) : null
		]
	});
}
function li({ card: e }) {
	let t = H(), { t: n } = t, r = Ne(t, e.headline, "template.common.headlineFallback"), i = e.consequence ? Ne(t, e.consequence, "template.common.consequenceFallback") : null;
	return rt(e) ? /* @__PURE__ */ m(Z, {
		title: n("template.common.closedTitle"),
		icon: "doc",
		children: /* @__PURE__ */ h(ni, { children: [/* @__PURE__ */ m("p", { children: n(e.resolution.reason === "answer_requires_text" ? "template.questions.textStillRequired" : e.resolution.reason === "answer_not_verified_at_gate" ? "delivery.answerNotVerifiedBody" : e.resolution.reason === "plan_approval_recorded_before_reset" ? "delivery.previousPlanApprovalBody" : e.resolution.reason === "command_superseded" ? "template.command.superseded" : "template.common.closedBody") }), /* @__PURE__ */ h("details", { children: [
			/* @__PURE__ */ m("summary", { children: n("template.common.originalNotice") }),
			/* @__PURE__ */ m("p", { children: r }),
			i ? /* @__PURE__ */ m("p", { children: i }) : null
		] })] })
	}) : /* @__PURE__ */ m(Z, {
		title: n("template.common.brief"),
		icon: "inbox",
		children: /* @__PURE__ */ h(ni, {
			...e.severity === "critical" ? { tone: "danger" } : {},
			children: [/* @__PURE__ */ m("p", { children: r }), i ? /* @__PURE__ */ m(ri, {
				label: n("template.common.nextConsequence"),
				children: i
			}) : null]
		})
	});
}
function ui({ card: e }) {
	let { t, has: n } = H(), r = e.evidence.acceptance_criteria;
	if (r.length === 0) return null;
	let i = r.filter((e) => e.met !== null), a = r.filter((e) => e.met === !0).length, o = i.length === 0 ? t("template.gate.criteriaUnknown", { total: r.length }) : t("template.gate.criteria", {
		met: a,
		total: r.length
	});
	return /* @__PURE__ */ m(Z, {
		title: o,
		icon: "check",
		children: /* @__PURE__ */ m(ii, { items: r.map((e, r) => ({
			id: `crit-${r + 1}`,
			met: e.met,
			text: e.text,
			why: e.why_key && n(e.why_key) ? t(e.why_key) : null
		})) })
	});
}
function di({ card: e, title: t }) {
	let n = H(), { t: r } = n, i = e.evidence.findings;
	return i.length === 0 ? null : /* @__PURE__ */ m(Z, {
		title: t,
		icon: "warn",
		children: /* @__PURE__ */ m("div", {
			className: "studio-col",
			children: i.map((e, t) => /* @__PURE__ */ h("div", {
				className: "studio-finding",
				"data-severity": e.severity,
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-finding-head",
						children: [/* @__PURE__ */ m(X, {
							tone: e.severity === "blocking" ? "danger" : e.severity === "warn" ? "warn" : "neutral",
							icon: e.severity === "info" ? "info" : "warn",
							children: r(`enum.findingSeverity.${e.severity}`)
						}), /* @__PURE__ */ m("span", {
							className: "studio-mono studio-muted studio-grow studio-trunc",
							children: e.code
						})]
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-finding-title",
						children: Ne(n, {
							key: `finding.${e.code}`,
							params: e.params
						}, "template.common.findingFallback")
					}),
					e.evidence.length > 0 ? /* @__PURE__ */ m("ul", {
						className: "studio-list studio-mono",
						children: e.evidence.map((e, t) => /* @__PURE__ */ h("li", {
							className: "studio-wrap-any",
							children: [
								e.kind,
								": ",
								e.ref,
								e.detail ? ` — ${e.detail}` : ""
							]
						}, t))
					}) : null
				]
			}, `${e.code}-${t}`))
		})
	});
}
function fi(e, t) {
	return e.t("template.a11y.decision", {
		type: e.t(`enum.actionType.${t.type}`),
		repo: t.repo.label,
		intent: t.intent.title ?? t.intent.slug,
		stage: lr(t, e.t("common.none")),
		state: nr(e, t.status, t.resolution.reason)
	});
}
function pi(e) {
	return e.headline.params.kind === "scope" ? "scope" : "free_text";
}
function mi(e, t, n, r = !1) {
	return t.length !== 0 && En({
		index: e.index,
		option_letters: t,
		free_text: n
	}, e, r) !== null;
}
function hi({ draft: e, setDraft: t, label: n, placeholder: r, routing: i, disabled: a }) {
	let o = H(), { t: s } = o, c = e.feedback.length > wn;
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m("textarea", {
			className: "studio-free",
			id: "studio-feedback",
			value: e.feedback,
			rows: 4,
			placeholder: r,
			"aria-label": n,
			"aria-describedby": "studio-feedback-note",
			disabled: a,
			onChange: (e) => t({ feedback: e.target.value })
		}),
		c ? /* @__PURE__ */ m("span", {
			className: "studio-freecount",
			role: "status",
			children: s("template.common.tooLong", { max: o.fmt.number(wn) })
		}) : null,
		/* @__PURE__ */ h("p", {
			id: "studio-feedback-note",
			className: "studio-consequence",
			children: [/* @__PURE__ */ m(Y, {
				name: "info",
				size: 13
			}), /* @__PURE__ */ m("span", { children: i })]
		})
	] });
}
function gi({ card: e, draft: t, setDraft: n, label: r, placeholder: i, routing: a }) {
	let o = H(), { t: s } = o, c = pi(e), l = c === "scope" ? t.scope : t.freeText, u = l.length > Tn;
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m("textarea", {
			className: "studio-free",
			id: "studio-input",
			value: l,
			rows: c === "scope" ? 2 : 4,
			placeholder: i,
			"aria-label": r,
			"aria-describedby": "studio-input-note",
			onChange: (e) => n(c === "scope" ? {
				scope: e.target.value,
				inputKind: "scope"
			} : {
				freeText: e.target.value,
				inputKind: "free_text"
			})
		}),
		u ? /* @__PURE__ */ m("span", {
			className: "studio-freecount",
			role: "status",
			children: s("template.common.tooLong", { max: o.fmt.number(Tn) })
		}) : null,
		/* @__PURE__ */ h("p", {
			id: "studio-input-note",
			className: "studio-consequence",
			children: [/* @__PURE__ */ m(Y, {
				name: "info",
				size: 13
			}), /* @__PURE__ */ m("span", { children: a })]
		})
	] });
}
function _i({ draft: e, setDraft: t, name: n, groupLabel: r, looksCorrectLabel: i, looksCorrectHint: a, changesLabel: o, changesHint: s, chosen: c }) {
	return /* @__PURE__ */ m("div", {
		role: "radiogroup",
		"aria-label": r,
		children: [{
			value: "looks_correct",
			label: i,
			hint: a
		}, {
			value: "request_changes",
			label: o,
			hint: s
		}].map((r) => {
			let i = c && e.summaryChoice === r.value;
			return /* @__PURE__ */ h("label", {
				className: "studio-choicerow",
				"data-selected": String(i),
				children: [/* @__PURE__ */ m("input", {
					type: "radio",
					name: n,
					value: r.value,
					checked: i,
					onChange: () => t({ summaryChoice: r.value })
				}), /* @__PURE__ */ h("span", {
					className: "studio-choice-body",
					children: [/* @__PURE__ */ m("span", {
						className: "studio-choice-label",
						children: r.label
					}), /* @__PURE__ */ m("span", {
						className: "studio-choice-desc",
						children: r.hint
					})]
				})]
			}, r.value);
		})
	});
}
function vi({ card: e, title: t }) {
	let { t: n, has: r } = H(), i = e.decisions;
	return i.length === 0 ? null : /* @__PURE__ */ h(Z, {
		title: t,
		icon: "recovery",
		children: [/* @__PURE__ */ m("ul", {
			className: "studio-choicelist",
			children: i.map((e) => /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ m(Y, {
				name: "chevron",
				size: 13
			}), /* @__PURE__ */ h("span", {
				className: "studio-choice-body",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-choice-label",
					children: n(e.label_key)
				}), /* @__PURE__ */ m("span", {
					className: "studio-choice-desc",
					children: r(`decision.${e.decision}.hint`) ? n(`decision.${e.decision}.hint`) : ""
				})]
			})] }, e.decision))
		}), /* @__PURE__ */ m(ri, {
			icon: "lock",
			children: n("template.common.noAutoChoice")
		})]
	});
}
//#endregion
//#region src/templates/AdvisorBlock.tsx
var yi = 2e3, bi = {
	gate: ["gate_analysis", "request_changes_draft"],
	question: ["question_draft"],
	missing_input: ["diagnose"],
	recovery: ["diagnose"],
	delivery_uncertain: ["diagnose"],
	failure: ["diagnose"],
	circuit_breaker: ["diagnose"],
	install_conflict: ["diagnose"],
	budget_stop: ["diagnose"]
};
function xi(e) {
	return bi[e] ?? [];
}
function Si(e) {
	return !e || e.status !== "ready" || !e.result || e.neutrality?.ok === !1 ? null : e.result;
}
function Ci(e, t, n) {
	let { locale: r } = H(), a = l(() => {
		let t = [...n ?? []].sort((e, t) => e.updated_at < t.updated_at ? -1 : 1);
		for (let e = t.length - 1; e >= 0; --e) {
			let n = t[e];
			if (n && Si(n)) return n.draft_id;
		}
		return t.length > 0 ? t[t.length - 1]?.draft_id ?? null : e.advisor.latest_draft_id;
	}, [n, e.advisor.latest_draft_id]), [o, s] = d(null), [c, u] = d(null), [f, p] = d(null), m = (o && o.action === e.action_id ? o.draft : null) ?? a, h = J(m ? `advisor-draft:${m}` : null, i((e) => t.draft(m ?? "", { signal: e }), [t, m]), {
		enabled: !!m,
		busy: (e) => e.draft.status === "queued" || e.draft.status === "running",
		fastInterval: yi,
		slowInterval: 0,
		revalidateOn: ["advisor.updated", "reset"]
	}), g = i((n, i) => {
		p(null), u(n), t.requestDraft({
			action_id: e.action_id,
			kind: n,
			question_index: i ?? null,
			locale: r
		}).then((t) => s({
			action: e.action_id,
			draft: t.draft.draft_id
		})).catch((e) => p(W(e).code)).finally(() => u(null));
	}, [
		t,
		e.action_id,
		r
	]);
	return {
		draft: h.data?.draft ?? null,
		pending: c,
		error: f,
		request: g
	};
}
function wi({ card: e, advisor: t, onUseFeedback: n, onApplyAnswers: r }) {
	let { t: i, has: a } = H(), o = xi(e.type);
	if (o.length === 0) return null;
	let { draft: s, pending: c, error: l, request: u } = t, d = s?.status === "ready" ? s.result : null, f = s?.neutrality?.ok !== !1, g = Si(s), _ = s?.status === "failed" || s?.status === "expired";
	return /* @__PURE__ */ m(Z, {
		title: i(g ? "advisor.titleDraft" : "advisor.title"),
		icon: "advisor",
		children: /* @__PURE__ */ h("div", {
			className: "studio-advisor",
			children: [
				/* @__PURE__ */ m("div", {
					"aria-live": "polite",
					children: g ? /* @__PURE__ */ h(p, { children: [
						/* @__PURE__ */ h("div", {
							className: "studio-advisor-head",
							children: [/* @__PURE__ */ m("span", {
								className: "studio-advisor-verdict",
								children: g.verdict ? a(`advisor.verdict.${g.verdict}`) ? i(`advisor.verdict.${g.verdict}`) : g.verdict : i("advisor.verdict.none")
							}), /* @__PURE__ */ m(X, {
								tone: "aim",
								icon: "advisor",
								children: i("advisor.draftNotDecision")
							})]
						}),
						g.summary ? /* @__PURE__ */ m("p", {
							className: "studio-advisor-summary",
							children: g.summary
						}) : null,
						/* @__PURE__ */ m(Ti, { result: g }),
						g.drafted_feedback && n ? /* @__PURE__ */ h("div", {
							className: "studio-advisor-apply",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								onClick: () => n(g.drafted_feedback),
								children: [/* @__PURE__ */ m(Y, {
									name: "doc",
									size: 13
								}), i("advisor.useFeedback")]
							}), /* @__PURE__ */ m("span", {
								className: "studio-advisor-note",
								children: i("advisor.useFeedbackNote")
							})]
						}) : null,
						g.suggested_answers.length > 0 && r ? /* @__PURE__ */ h("div", {
							className: "studio-advisor-apply",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								onClick: () => r(g.suggested_answers),
								children: [/* @__PURE__ */ m(Y, {
									name: "check",
									size: 13
								}), i("advisor.applyPicks")]
							}), /* @__PURE__ */ m("span", {
								className: "studio-advisor-note",
								children: i("advisor.applyPicksNote")
							})]
						}) : null
					] }) : d && !f ? /* @__PURE__ */ h("p", {
						className: "studio-advisor-copy",
						"data-tone": "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}), i("advisor.evidenceMoved")]
					}) : c || s?.status === "queued" || s?.status === "running" ? /* @__PURE__ */ h("p", {
						className: "studio-advisor-copy",
						children: [/* @__PURE__ */ m(Y, {
							name: "clock",
							size: 13
						}), i("advisor.running", { kind: i(`advisor.kind.${c ?? s?.kind ?? "diagnose"}`) })]
					}) : _ ? /* @__PURE__ */ h("p", {
						className: "studio-advisor-copy",
						"data-tone": "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}), s?.status === "expired" ? i("advisor.expired") : a(`advisor.error.${s?.error ?? ""}`) ? i(`advisor.error.${s?.error ?? ""}`) : i("advisor.error.unknown")]
					}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("p", {
						className: "studio-advisor-title",
						children: i("advisor.notRun")
					}), /* @__PURE__ */ m("p", {
						className: "studio-advisor-copy",
						children: i("advisor.notRunCopy")
					})] })
				}),
				l ? /* @__PURE__ */ h("p", {
					className: "studio-advisor-copy",
					"data-tone": "warn",
					role: "status",
					children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}), a(`errors.${l}`) ? i(`errors.${l}`) : i("advisor.error.unknown")]
				}) : null,
				/* @__PURE__ */ m("div", {
					className: "studio-advisor-actions",
					children: o.map((e) => /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: c !== null,
						onClick: () => u(e),
						children: [/* @__PURE__ */ m(Y, {
							name: "advisor",
							size: 13
						}), i(`advisor.action.${e}`)]
					}, e))
				}),
				/* @__PURE__ */ h("p", {
					className: "studio-disclaim",
					children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}), /* @__PURE__ */ m("span", { children: i("advisor.disclaimer") })]
				})
			]
		})
	});
}
function Ti({ result: e }) {
	let { t } = H();
	return /* @__PURE__ */ h("dl", {
		className: "studio-advisor-dl",
		children: [
			/* @__PURE__ */ m(Ei, {
				label: t("advisor.evidence"),
				items: e.evidence
			}),
			/* @__PURE__ */ m(Ei, {
				label: t("advisor.assumptions"),
				items: e.assumptions
			}),
			/* @__PURE__ */ m(Ei, {
				label: t("advisor.alternatives"),
				items: e.alternatives
			}),
			/* @__PURE__ */ m("dt", { children: t("advisor.confidence") }),
			/* @__PURE__ */ m("dd", { children: t(`advisor.confidenceValue.${e.confidence}`) }),
			e.needs_your_decision.length > 0 ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("dt", { children: t("advisor.needsYourDecision") }), /* @__PURE__ */ h("dd", { children: [/* @__PURE__ */ m(X, {
				tone: "warn",
				icon: "warn",
				children: t("advisor.unresolvable")
			}), /* @__PURE__ */ m("ul", {
				className: "studio-advisor-ul",
				children: e.needs_your_decision.map((e, t) => /* @__PURE__ */ m("li", { children: e }, t))
			})] })] }) : null
		]
	});
}
function Ei({ label: e, items: t }) {
	return t.length === 0 ? null : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("dt", { children: e }), /* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m("ul", {
		className: "studio-advisor-ul",
		children: t.map((e, t) => /* @__PURE__ */ m("li", { children: e }, t))
	}) })] });
}
//#endregion
//#region src/templates/BudgetStopTemplate.tsx
function Di({ label: e, value: t, qualifier: n }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-stat",
		children: [
			/* @__PURE__ */ m("span", {
				className: "studio-stat-k",
				children: e
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-stat-v studio-mono",
				children: t
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-stat-q",
				children: n
			})
		]
	});
}
function Oi({ card: e, detail: t, api: n, go: r }) {
	let i = H(), { t: a } = i, o = Ci(e, n, t?.drafts), s = e.budget;
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(i, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ h(Z, {
				title: a("template.budget.state"),
				icon: "clock",
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-stats",
						children: [
							/* @__PURE__ */ m(Di, {
								label: a("template.budget.turns"),
								value: s ? ke(i, s.turns_used, s.turn_cap) : a("common.unavailable"),
								qualifier: a(s ? "common.exact" : "template.budget.notOnCard")
							}),
							/* @__PURE__ */ m(Di, {
								label: a("template.budget.window"),
								value: s?.window ? s.window : a("common.unavailable"),
								qualifier: s?.window ? a("template.budget.localTime") : a("template.budget.notOnCard")
							}),
							/* @__PURE__ */ m(Di, {
								label: a("template.budget.credits"),
								value: a("common.unavailable"),
								qualifier: a("template.budget.notObservable")
							})
						]
					}),
					/* @__PURE__ */ m(ri, {
						icon: "info",
						children: a("template.budget.creditNote")
					}),
					s ? null : /* @__PURE__ */ m(ri, {
						icon: "warn",
						tone: "warn",
						children: a("template.budget.noBudget")
					})
				]
			}),
			/* @__PURE__ */ h(Z, {
				title: a("template.budget.effect"),
				icon: "lock",
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-row studio-qmetas",
						children: [
							/* @__PURE__ */ m(X, {
								icon: "clock",
								children: a("template.budget.neverInterrupts")
							}),
							e.evidence.session ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: e.evidence.session.slot_key
							}) : /* @__PURE__ */ m(X, { children: a("template.budget.noSession") }),
							/* @__PURE__ */ m(X, {
								icon: "inbox",
								children: a("template.budget.queueDepth", { n: we(i, e.evidence.session?.queue_depth ?? null) })
							})
						]
					}),
					/* @__PURE__ */ m(ri, {
						icon: "info",
						children: a("template.budget.effectBody")
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => r({ view: "settings" }),
						children: a("template.budget.openSettings")
					})
				]
			}),
			/* @__PURE__ */ m(vi, {
				card: e,
				title: a("template.budget.choices")
			}),
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: o
			})
		]
	});
}
//#endregion
//#region src/templates/FailureTemplate.tsx
function ki({ card: e, detail: t, api: n }) {
	let r = H(), { t: i } = r, a = Ci(e, n, t?.drafts), o = e.failure, s = e.evidence.session;
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(r, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ m(Z, {
				title: i("template.failure.normalized"),
				icon: "fail",
				children: /* @__PURE__ */ h(ni, {
					tone: "danger",
					children: [
						/* @__PURE__ */ m("p", { children: o?.summary_key ? Ne(r, {
							key: o.summary_key,
							params: {}
						}, "template.failure.summaryUnknown") : i("template.failure.summaryUnknown") }),
						o ? /* @__PURE__ */ m("p", {
							className: "studio-fingerprint studio-mono studio-wrap-any",
							children: i("template.failure.fingerprint", {
								fingerprint: o.fingerprint,
								cls: o.fingerprint_class ?? i("common.unavailable")
							})
						}) : null,
						/* @__PURE__ */ h("span", {
							className: "studio-row",
							children: [o?.breaker_open ? /* @__PURE__ */ m(X, {
								tone: "danger",
								icon: "warn",
								children: i("template.failure.breakerOpen")
							}) : /* @__PURE__ */ m(X, {
								icon: "clock",
								children: i("template.failure.breakerClosed")
							}), /* @__PURE__ */ m(X, {
								mono: !0,
								children: i("template.failure.count", { n: o?.count ?? 0 })
							})]
						})
					]
				})
			}),
			o && o.history.length > 0 ? /* @__PURE__ */ m(Z, {
				title: i("template.failure.history"),
				icon: "clock",
				children: /* @__PURE__ */ h("table", {
					className: "studio-tbl",
					children: [
						/* @__PURE__ */ m("caption", {
							className: "studio-sr",
							children: i("template.failure.historyCaption")
						}),
						/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: i("template.failure.colAttempt")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: i("template.failure.colAt")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: i("template.failure.colBackoff")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: i("template.failure.colOutcome")
							})
						] }) }),
						/* @__PURE__ */ m("tbody", { children: o.history.map((e, t) => /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: t + 1
							}),
							/* @__PURE__ */ m("td", { children: K(r, e.at) }),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: Te(r, e.backoff_secs)
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono studio-wrap-any",
								children: e.outcome
							})
						] }, `${e.at}-${t}`)) })
					]
				})
			}) : null,
			o?.stderr_excerpt ? /* @__PURE__ */ h(Z, {
				title: i("template.failure.log"),
				icon: "activity",
				children: [/* @__PURE__ */ m(ci, {
					title: i("template.failure.logPane"),
					icon: "activity",
					children: /* @__PURE__ */ m("pre", {
						className: "studio-log studio-mono studio-wrap-any",
						children: o.stderr_excerpt
					})
				}), /* @__PURE__ */ m(ri, {
					icon: "lock",
					children: i("template.failure.logNote")
				})]
			}) : null,
			s ? /* @__PURE__ */ m(Z, {
				title: i("template.failure.session"),
				icon: "activity",
				children: /* @__PURE__ */ m(oi, { children: /* @__PURE__ */ m(si, {
					src: i("template.recovery.src.session"),
					icon: "activity",
					value: s.slot_key,
					sub: i("template.failure.sessionSub", {
						state: s.running ? i("template.recovery.sessionRunning") : i("template.recovery.sessionIdle"),
						stop: s.stop_state,
						turn: s.last_turn_ts ? K(r, s.last_turn_ts) : i("common.unavailable")
					})
				}) })
			}) : null,
			/* @__PURE__ */ m(di, {
				card: e,
				title: i("template.failure.findings")
			}),
			/* @__PURE__ */ m(vi, {
				card: e,
				title: i("template.failure.choices")
			}),
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: a
			})
		]
	});
}
//#endregion
//#region src/templates/GateTemplate.tsx
function Ai(e) {
	if (/^h-[a-z0-9-]+$/.test(e)) return e;
	let t = e.replace(/^#+/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 118);
	return t ? `h-${t}` : "";
}
function ji({ card: e, detail: t, draft: n, setDraft: r, refreshing: i, api: a, route: o, go: s }) {
	let c = H(), { t: l } = c, u = Ci(e, a, t?.drafts), d = e.evidence.review, f = e.evidence.artifacts, g = d?.findings ?? [], _ = g.filter((e) => e.level === "blocker").length, v = e.decisions.some((e) => e.decision === "request_changes" || e.decision === "request_plan_changes"), y = f.find((e) => e.artifact_id === o.artifact) ?? f[0] ?? null, b = sn(e.repo.repo_id, e.intent.intent_key, y);
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(c, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ m(ui, { card: e }),
			/* @__PURE__ */ m(Z, {
				title: l("template.gate.compare"),
				icon: "review",
				children: /* @__PURE__ */ h("div", {
					className: "studio-compare",
					"data-panes": y && g.length > 0 ? "two" : "one",
					children: [y ? /* @__PURE__ */ m(cn, {
						meta: y,
						body: b.data,
						loading: b.loading,
						error: b.error,
						anchor: o.anchor || void 0,
						headerExtra: e.evidence.state && e.evidence.state.revision_count > 0 ? /* @__PURE__ */ m(X, {
							mono: !0,
							children: l("review.iteration", { n: e.evidence.state.revision_count })
						}) : null,
						children: b.data?.prior ? /* @__PURE__ */ m(Qt, {
							prior: b.data.prior,
							name: y.name
						}) : null
					}) : /* @__PURE__ */ m(ci, {
						title: l("template.gate.noArtifact"),
						icon: "doc",
						children: /* @__PURE__ */ m("p", {
							className: "studio-muted",
							children: l("template.gate.noArtifactBody")
						})
					}), /* @__PURE__ */ m(ci, {
						title: l("template.gate.reviewer"),
						icon: "review",
						badge: d ? _ > 0 ? /* @__PURE__ */ m(X, {
							tone: "danger",
							icon: "warn",
							children: l("review.openBlockers", { n: _ })
						}) : /* @__PURE__ */ m(X, {
							tone: "ok",
							icon: "check",
							children: l("template.gate.noBlockers")
						}) : null,
						footer: /* @__PURE__ */ m("span", { children: l("review.quotedVerbatim") }),
						children: d ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("div", {
							className: "studio-row studio-qmetas",
							children: [
								d.verdict ? /* @__PURE__ */ m(X, {
									mono: !0,
									children: d.verdict
								}) : null,
								d.reviewer ? /* @__PURE__ */ m(X, {
									icon: "review",
									children: d.reviewer
								}) : null,
								d.review_class ? /* @__PURE__ */ m(X, {
									tone: "info",
									children: d.review_class
								}) : null,
								d.iteration === null ? null : /* @__PURE__ */ m(X, { children: l("review.iteration", { n: d.iteration }) })
							]
						}), g.length > 0 ? /* @__PURE__ */ m(gn, {
							findings: g,
							anchor: o.anchor || void 0,
							...y ? { onOpenAnchor: (e) => {
								let t = Ai(e);
								s({
									tab: "artifacts",
									...y ? { artifact: y.artifact_id } : {},
									...t ? { anchor: t } : {}
								});
							} } : {}
						}) : /* @__PURE__ */ m("p", {
							className: "studio-muted",
							children: l("template.gate.noFindings")
						})] }) : /* @__PURE__ */ m("p", {
							className: "studio-muted",
							children: l("template.gate.noReview")
						})
					})]
				})
			}),
			/* @__PURE__ */ m(di, {
				card: e,
				title: l("template.gate.risks")
			}),
			e.evidence.state || d ? /* @__PURE__ */ m(Z, {
				title: l("template.gate.history"),
				icon: "clock",
				children: /* @__PURE__ */ h(oi, { children: [
					/* @__PURE__ */ m(si, {
						src: l("template.gate.revisions"),
						icon: "doc",
						value: String(e.evidence.state?.revision_count ?? 0),
						sub: l("template.gate.revisionsSub")
					}),
					d && d.iteration !== null ? /* @__PURE__ */ m(si, {
						src: l("template.gate.reviewPasses"),
						icon: "review",
						value: String(d.iteration),
						sub: l("template.gate.reviewPassesSub")
					}) : null,
					y ? /* @__PURE__ */ m(si, {
						src: l("artifact.meta.updated"),
						icon: "clock",
						value: K(c, y.mtime),
						sub: je(c, y.size)
					}) : null
				] })
			}) : null,
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: u,
				...v ? { onUseFeedback: (e) => r({ feedback: e }) } : {}
			}),
			v ? /* @__PURE__ */ h(Z, {
				title: l("template.gate.feedback"),
				icon: "doc",
				children: [/* @__PURE__ */ m(hi, {
					draft: n,
					setDraft: r,
					label: l("template.gate.feedbackLabel"),
					placeholder: l("template.gate.feedbackPlaceholder"),
					routing: l("template.gate.feedbackRouting"),
					disabled: i
				}), i ? /* @__PURE__ */ h("p", {
					className: "studio-advisor-copy",
					"data-tone": "warn",
					role: "status",
					children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}), l("template.common.refreshing")]
				}) : null]
			}) : null
		]
	});
}
//#endregion
//#region src/templates/InstallConflictTemplate.tsx
function Mi(e) {
	return e.blocking || e.action === "conflict" || e.action === "owned_modified" || e.action === "merge_conflict" || e.action === "retire_blocked";
}
function Ni(e) {
	return e ? e.slice(0, 12) : "—";
}
function Pi({ card: e, detail: t, api: n, go: r }) {
	let i = H(), { t: a } = i, o = Ci(e, n, t?.drafts), s = e.install, c = s?.drift ?? [], l = c.filter(Mi);
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(i, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ m(Z, {
				title: a("template.install.ownership"),
				icon: "lock",
				children: /* @__PURE__ */ h(oi, { children: [
					/* @__PURE__ */ m(si, {
						src: a("template.install.installed"),
						icon: "install",
						value: s?.engine_version ?? a("common.unavailable"),
						sub: s?.receipt_id ? a("template.install.receiptSub", { id: s.receipt_id }) : a("template.install.noReceipt"),
						conflict: !s?.receipt_id
					}),
					/* @__PURE__ */ m(si, {
						src: a("template.install.bundled"),
						icon: "doc",
						value: s?.bundled_engine_version ?? a("common.unavailable"),
						sub: a("template.install.bundledSub")
					}),
					/* @__PURE__ */ m(si, {
						src: a("template.install.managed"),
						icon: "lock",
						value: a("template.install.managedValue", {
							n: c.length,
							conflicts: l.length
						}),
						sub: a("template.install.managedSub"),
						conflict: l.length > 0
					})
				] })
			}),
			/* @__PURE__ */ m(Z, {
				title: a("template.install.drift"),
				icon: "git",
				children: c.length === 0 ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: a("template.install.noDrift")
				}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("table", {
					className: "studio-tbl",
					children: [
						/* @__PURE__ */ m("caption", {
							className: "studio-sr",
							children: a("template.install.driftCaption")
						}),
						/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: a("template.install.colPath")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: a("template.install.colOwnership")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: a("template.install.colState")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: a("template.install.colHash")
							})
						] }) }),
						/* @__PURE__ */ m("tbody", { children: c.map((e) => /* @__PURE__ */ h("tr", {
							"data-conflict": String(Mi(e)),
							children: [
								/* @__PURE__ */ m("td", {
									className: "studio-mono studio-wrap-any",
									children: e.path
								}),
								/* @__PURE__ */ m("td", { children: a(`enum.ownership.${e.ownership}`) }),
								/* @__PURE__ */ m("td", { children: Mi(e) ? /* @__PURE__ */ m(X, {
									tone: "danger",
									icon: "warn",
									children: a(`template.install.action.${e.action}`)
								}) : /* @__PURE__ */ m(X, { children: a(`template.install.action.${e.action}`) }) }),
								/* @__PURE__ */ h("td", {
									className: "studio-mono studio-wrap-any",
									children: [
										/* @__PURE__ */ m("span", {
											className: "studio-hashline",
											children: a("template.install.hashLive", { hash: Ni(e.live_sha256) })
										}),
										/* @__PURE__ */ m("span", {
											className: "studio-hashline",
											children: a("template.install.hashReceipt", { hash: Ni(e.receipt_sha256) })
										}),
										/* @__PURE__ */ m("span", {
											className: "studio-hashline",
											children: a("template.install.hashPayload", { hash: Ni(e.payload_sha256) })
										}),
										e.size === null ? null : /* @__PURE__ */ m("span", {
											className: "studio-hashline",
											children: je(i, e.size)
										})
									]
								})
							]
						}, e.path)) })
					]
				}), c.filter((e) => e.diff).map((e) => /* @__PURE__ */ h("details", {
					className: "studio-details",
					children: [/* @__PURE__ */ m("summary", { children: a("template.install.showDiff", { path: e.path }) }), /* @__PURE__ */ m("div", {
						className: "studio-difflines",
						"aria-label": a("template.install.diffLabel", { path: e.path }),
						children: Zt(e.diff ?? "").map((e, t) => /* @__PURE__ */ m("div", {
							className: "studio-diffline",
							"data-kind": e.kind,
							children: e.text === "" ? " " : e.text
						}, t))
					})]
				}, `diff-${e.path}`))] })
			}),
			/* @__PURE__ */ m(Z, {
				title: a("template.install.why"),
				icon: "warn",
				children: /* @__PURE__ */ h(ni, {
					tone: "danger",
					children: [/* @__PURE__ */ m("p", { children: a("template.install.whyBody") }), /* @__PURE__ */ m(ri, {
						icon: "lock",
						label: a("template.install.stoppedLabel"),
						children: a("template.install.stoppedBody")
					})]
				})
			}),
			/* @__PURE__ */ h(Z, {
				title: a("template.install.remediation"),
				icon: "check",
				children: [/* @__PURE__ */ m(ai, { steps: [
					a("template.install.fixRevert"),
					a("template.install.fixStay"),
					a("template.install.fixNoMerge")
				] }), /* @__PURE__ */ m("div", {
					className: "studio-row studio-qmetas",
					children: /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => r({
							view: "repos",
							repo: e.repo.repo_id,
							...s?.transaction_id ? { tx: s.transaction_id } : {}
						}),
						children: a("template.install.openRepo")
					})
				})]
			}),
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: o
			})
		]
	});
}
//#endregion
//#region src/templates/MissingInputTemplate.tsx
var Fi = [
	"missing_scope",
	"dangling_cursor",
	"no_cursor"
];
function Ii({ card: e, detail: t, draft: n, setDraft: r, api: i, go: a }) {
	let o = H(), { t: s } = o, c = Ci(e, i, t?.drafts), l = e.headline.params.reason, u = Fi.find((e) => e === l) ?? null, d = e.decisions.some((e) => e.decision === "provide_input"), f = e.decisions.some((e) => e.decision === "pick_intent"), p = pi(e), g = e.headline.params.cursor, _ = e.evidence.state?.mtime_ns ? (/* @__PURE__ */ new Date(e.evidence.state.mtime_ns / 1e6)).toISOString() : null;
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(o, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ m(Z, {
				title: s("template.missingInput.what"),
				icon: "missingInput",
				children: /* @__PURE__ */ h(ni, { children: [/* @__PURE__ */ m("p", { children: s(u ? `template.missingInput.reason.${u}` : "template.missingInput.reasonUnknown") }), /* @__PURE__ */ m(ri, {
					icon: "lock",
					label: s("template.missingInput.blockedLabel"),
					children: s("template.missingInput.blocked")
				})] })
			}),
			/* @__PURE__ */ m(Z, {
				title: s("template.missingInput.source"),
				icon: "doc",
				children: /* @__PURE__ */ h(oi, { children: [e.evidence.state ? /* @__PURE__ */ m(si, {
					src: s("template.recovery.src.state"),
					icon: "doc",
					value: e.evidence.state.relpath,
					sub: s("template.missingInput.stateSub", {
						stage: e.evidence.state.current_stage ?? s("common.unavailable"),
						at: K(o, _)
					})
				}) : null, /* @__PURE__ */ m(si, {
					src: s("template.missingInput.cursorSrc"),
					icon: "intent",
					value: typeof g == "string" && g ? g : s("common.none"),
					sub: s("template.missingInput.cursorSub", { space: e.space }),
					conflict: u === "dangling_cursor"
				})] })
			}),
			d ? /* @__PURE__ */ m(Z, {
				title: s(`template.missingInput.input.${p}`),
				icon: "send",
				children: /* @__PURE__ */ m(gi, {
					card: e,
					draft: n,
					setDraft: r,
					label: s(`template.missingInput.input.${p}`),
					placeholder: s(`template.missingInput.placeholder.${p}`),
					routing: s("template.missingInput.routing")
				})
			}) : null,
			f ? /* @__PURE__ */ m(Z, {
				title: s("template.missingInput.pickTitle"),
				icon: "intent",
				children: /* @__PURE__ */ h(ni, { children: [
					/* @__PURE__ */ m("p", { children: s("template.missingInput.pickBody") }),
					/* @__PURE__ */ h("span", {
						className: "studio-row studio-qmetas",
						children: [/* @__PURE__ */ m(X, {
							icon: "lock",
							children: s("template.missingInput.adminLane")
						}), /* @__PURE__ */ m(X, {
							mono: !0,
							children: e.space
						})]
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => a({
							view: "intents",
							repo: e.repo.repo_id,
							space: e.space
						}),
						children: s("template.missingInput.openIntents")
					})
				] })
			}) : null,
			/* @__PURE__ */ m(di, {
				card: e,
				title: s("template.missingInput.findings")
			}),
			/* @__PURE__ */ m(vi, {
				card: e,
				title: s("template.missingInput.choices")
			}),
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: c
			})
		]
	});
}
//#endregion
//#region src/templates/QuestionsTemplate.tsx
function Li(e, t) {
	let n = t.option_letters.filter((t) => e.options.some((e) => e.letter === t)), r = e.options.find((e) => e.is_other);
	if (n.length > 0) {
		let i = e.multi_select ? n : n.slice(0, 1);
		return {
			option_letters: i,
			free_text: r && i.includes(r.letter) ? t.answer : null
		};
	}
	return r && t.answer.trim() ? {
		option_letters: [r.letter],
		free_text: t.answer
	} : null;
}
function Ri(e, t) {
	return !e || e.free_text !== t.free_text || e.option_letters.length !== t.option_letters.length ? !1 : e.option_letters.every((e, n) => e === t.option_letters[n]);
}
function zi({ card: e, detail: t, draft: n, setDraft: r, refreshing: a, api: s, go: c }) {
	let u = H(), { t: f } = u, g = Ci(e, s, t?.drafts), _ = e.evidence.questions, v = _?.questions ?? [], y = Dn(_), b = _?.unsupported_pending_count ?? 0, x = y.length > 0 || b > 0, S = _?.mode === "degraded" || b > 0, w = [
		"Draft",
		"Queued",
		"NotDelivered"
	].includes(e.status), T = w && !S && !a, [E, D] = d(!1), O = _?.pending_checkpoint === "summary_confirmation" ? _.summary_confirmation : _?.pending_checkpoint === "plan_approval" ? _.plan_approval : null, k = e.decisions.some((e) => e.decision === "request_plan_changes" || e.decision === "confirm_summary"), A = e.decisions.some((e) => e.decision === "confirm_summary"), j = (e, t, i) => r({ answers: {
		...n.answers,
		[String(e)]: {
			option_letters: t,
			free_text: i
		}
	} }), M = (e, t) => {
		let r = n.answers[String(e.index)], i = r?.option_letters ?? [];
		if (!e.multi_select) {
			j(e.index, [t], r?.free_text ?? null);
			return;
		}
		let a = i.includes(t) ? i.filter((e) => e !== t) : [...i, t];
		j(e.index, a, r?.free_text ?? null);
	}, N = i((e, t) => {
		let r = { ...n.answers };
		for (let n of e) {
			let e = v.find((e) => e.index === n.question_index);
			if (!e || e.answered) continue;
			let i = String(e.index);
			if (t && r[i]) continue;
			let a = Li(e, n);
			a && (r[i] = a);
		}
		return r;
	}, [n.answers, v]), P = (e) => r({ answers: N(e, !1) }), F = g.draft?.request.auto === !0 && g.draft.action_id === e.action_id ? Si(g.draft) : null, I = F ? g.draft?.draft_id ?? null : null, L = l(() => {
		let r = n.advisorApplied;
		if (!r) return [];
		let i = g.draft?.draft_id === r ? g.draft : (t?.drafts ?? []).find((e) => e.draft_id === r);
		return !i || i.action_id !== e.action_id || i.request.auto !== !0 ? [] : Si(i)?.suggested_answers ?? [];
	}, [
		n.advisorApplied,
		g.draft,
		t?.drafts,
		e.action_id
	]), R = l(() => L.some((e) => {
		let t = v.find((t) => t.index === e.question_index);
		if (!t) return !1;
		let r = Li(t, e);
		return !!r && Ri(n.answers[String(t.index)], r);
	}), [
		L,
		n.answers,
		v
	]);
	return o(() => {
		if (!F || !I || !T || n.advisorApplied === I) return;
		let e = N(F.suggested_answers, !0);
		Object.keys(e).length !== Object.keys(n.answers).length && r({
			answers: e,
			advisorApplied: I
		});
	}, [
		F,
		I,
		T,
		n.advisorApplied,
		n.answers,
		N,
		r
	]), /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(u, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			_?.origin?.kind === "audit" ? /* @__PURE__ */ m(ri, {
				icon: "doc",
				children: f("template.questions.auditSource")
			}) : _ && !S ? /* @__PURE__ */ m(ri, {
				icon: "doc",
				children: f("template.questions.fileSource")
			}) : null,
			S ? /* @__PURE__ */ h(Z, {
				title: f("template.questions.degradedTitle"),
				icon: "warn",
				children: [/* @__PURE__ */ m(ri, {
					icon: "warn",
					tone: "warn",
					label: f("template.questions.degradedLabel"),
					children: f(b > 0 ? "template.questions.unsupportedBody" : "template.questions.degradedBody")
				}), /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					onClick: () => c({ tab: "conversation" }),
					children: [/* @__PURE__ */ m(Y, {
						name: "activity",
						size: 15
					}), f("template.questions.openConversation")]
				})]
			}) : null,
			/* @__PURE__ */ h(Z, {
				title: f("template.questions.group"),
				icon: "question",
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-qmetas",
						children: [
							/* @__PURE__ */ m(X, {
								tone: x ? "accent" : "ok",
								icon: x ? "question" : "check",
								children: b > 0 ? f("template.questions.unsupportedPending", { n: b }) : f("template.questions.pending", {
									n: y.length,
									total: v.length
								})
							}),
							_ ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: _.stage
							}) : null,
							_?.unit ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: _.unit
							}) : null
						]
					}),
					R && T ? /* @__PURE__ */ h("div", {
						className: "studio-advisor-apply",
						role: "status",
						children: [
							/* @__PURE__ */ m(Y, {
								name: "advisor",
								size: 13
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-advisor-note",
								children: f("advisor.prefilled")
							}),
							/* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn studio-btn-sm studio-btn-ghost",
								onClick: () => {
									let e = { ...n.answers };
									for (let t of L) {
										let n = v.find((e) => e.index === t.question_index);
										if (!n) continue;
										let r = Li(n, t), i = String(n.index);
										r && Ri(e[i], r) && delete e[i];
									}
									r({ answers: e });
								},
								children: f("advisor.prefilledClear")
							})
						]
					}) : null,
					v.length === 0 && b === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: f("template.questions.none")
					}) : null,
					v.map((t) => {
						let r = n.answers[String(t.index)], i = r?.option_letters ?? [], a = r?.free_text ?? "", o = t.options.find((e) => e.is_other), s = _?.origin?.kind === "audit" && t.options.length === 1 && !!o, c = mi(t, i, a, s), l = `${e.action_id}/${t.index}`;
						return /* @__PURE__ */ h("div", {
							className: "studio-q",
							"data-answered": String(t.answered),
							children: [
								/* @__PURE__ */ h("p", {
									className: "studio-q-prompt",
									children: [
										t.index,
										". ",
										t.prompt
									]
								}),
								t.context?.trim() ? /* @__PURE__ */ m("div", {
									className: "msg-content studio-md",
									children: /* @__PURE__ */ m(C, { content: t.context })
								}) : null,
								/* @__PURE__ */ h("p", {
									className: "studio-q-sub",
									children: [
										/* @__PURE__ */ m(X, { children: s ? f("template.questions.freeText") : t.multi_select ? f("template.questions.selectAny") : f("template.questions.selectOne") }),
										/* @__PURE__ */ m(X, {
											tone: "accent",
											children: f("template.questions.required")
										}),
										t.answered ? /* @__PURE__ */ m(X, {
											tone: "ok",
											icon: "check",
											children: f("template.questions.answered")
										}) : c && T ? /* @__PURE__ */ m(X, {
											tone: "aim",
											icon: "check",
											children: f("template.questions.drafted")
										}) : null,
										!t.answered && T ? /* @__PURE__ */ h("span", {
											className: "studio-q-advisor",
											children: [/* @__PURE__ */ h("button", {
												type: "button",
												className: "studio-btn studio-btn-sm studio-btn-ghost",
												disabled: g.pending !== null,
												onClick: () => g.request("question_explain", t.index),
												children: [/* @__PURE__ */ m(Y, {
													name: "advisor",
													size: 13
												}), f("advisor.action.question_explain")]
											}), /* @__PURE__ */ h("button", {
												type: "button",
												className: "studio-btn studio-btn-sm studio-btn-ghost",
												disabled: g.pending !== null,
												onClick: () => g.request("question_draft", t.index),
												children: [/* @__PURE__ */ m(Y, {
													name: "advisor",
													size: 13
												}), f("advisor.action.question_draft_one")]
											})]
										}) : null
									]
								}),
								t.answered ? /* @__PURE__ */ h("p", {
									className: "studio-q-recorded studio-wrap-any",
									children: [/* @__PURE__ */ m("span", {
										className: "studio-q-recorded-label",
										children: f("template.questions.recorded")
									}), /* @__PURE__ */ m("span", {
										className: "studio-mono",
										children: t.answer ?? f("common.unavailable")
									})]
								}) : s ? /* @__PURE__ */ m("textarea", {
									className: "studio-free",
									value: a,
									rows: 4,
									placeholder: f("template.questions.textPlaceholder"),
									"aria-label": t.prompt,
									disabled: !T,
									onChange: (e) => j(t.index, [o.letter], e.target.value)
								}) : /* @__PURE__ */ h(p, { children: [t.options.map((e) => {
									let n = i.includes(e.letter);
									return /* @__PURE__ */ h("label", {
										className: "studio-opt",
										"data-selected": String(n),
										"data-disabled": String(!T),
										children: [/* @__PURE__ */ m("input", {
											type: t.multi_select ? "checkbox" : "radio",
											name: l,
											value: e.letter,
											checked: n,
											disabled: !T,
											onChange: () => M(t, e.letter)
										}), /* @__PURE__ */ h("span", {
											className: "studio-opt-body",
											children: [
												/* @__PURE__ */ m("span", {
													className: "studio-opt-letter",
													children: e.letter
												}),
												/* @__PURE__ */ m("span", {
													className: "studio-opt-label",
													children: e.text
												}),
												e.is_other ? /* @__PURE__ */ m("span", {
													className: "studio-opt-desc",
													children: f("template.questions.otherDesc")
												}) : null
											]
										})]
									}, e.letter);
								}), o && i.includes(o.letter) ? /* @__PURE__ */ m("textarea", {
									className: "studio-free",
									value: a,
									rows: 3,
									placeholder: f("template.questions.otherPlaceholder"),
									"aria-label": f("template.questions.otherLabel", { index: t.index }),
									disabled: !T,
									onChange: (e) => j(t.index, i, e.target.value)
								}) : null] })
							]
						}, t.index);
					}),
					_ ? /* @__PURE__ */ m(ri, {
						icon: "lock",
						children: f("template.questions.neverEdits", { path: _.relpath })
					}) : null
				]
			}),
			O?.present ? /* @__PURE__ */ h(Z, {
				title: f(`template.questions.checkpoint.${O.kind}`),
				icon: "gate",
				children: [
					/* @__PURE__ */ m("p", {
						className: "studio-q-prompt",
						children: f(`template.questions.checkpoint.${O.kind}.body`)
					}),
					O.options.length > 0 ? /* @__PURE__ */ m("ul", {
						className: "studio-optionlist studio-mono",
						children: O.options.map((e, t) => /* @__PURE__ */ m("li", {
							className: "studio-wrap-any",
							children: e
						}, t))
					}) : null,
					O.answered ? /* @__PURE__ */ m(ri, {
						icon: "check",
						children: f("template.questions.checkpointAnswered", { answer: O.answer ?? f("common.unavailable") })
					}) : A && T ? /* @__PURE__ */ m(_i, {
						draft: n,
						setDraft: (e) => {
							D(!0), r(e);
						},
						name: `${e.action_id}/summary`,
						groupLabel: f("template.questions.summaryChoiceLabel"),
						looksCorrectLabel: f("template.questions.looksCorrect"),
						looksCorrectHint: f("template.questions.looksCorrectHint"),
						changesLabel: f("template.questions.summaryChanges"),
						changesHint: f("template.questions.summaryChangesHint"),
						chosen: E
					}) : null
				]
			}) : null,
			y.length > 0 && w ? /* @__PURE__ */ m(wi, {
				card: e,
				advisor: g,
				...T ? { onApplyAnswers: P } : {},
				...k && T ? { onUseFeedback: (e) => r({ feedback: e }) } : {}
			}) : null,
			k && T ? /* @__PURE__ */ m(Z, {
				title: f("template.questions.feedback"),
				icon: "doc",
				children: /* @__PURE__ */ m(hi, {
					draft: n,
					setDraft: r,
					label: f("template.questions.feedbackLabel"),
					placeholder: f("template.gate.feedbackPlaceholder"),
					routing: f("template.gate.feedbackRouting"),
					disabled: a
				})
			}) : null
		]
	});
}
//#endregion
//#region src/templates/RecoveryTemplate.tsx
function Bi(e) {
	return e ? e.slice(0, 12) : "";
}
function Vi(e) {
	return e.unit ?? (Array.isArray(e.units) ? e.units.join(", ") : "");
}
function Hi({ card: e, detail: t, api: n, go: r }) {
	let i = H(), { t: a } = i, o = Ci(e, n, t?.drafts), s = e.evidence, c = e.delivery, l = rt(e), u = e.headline.params.codes, d = Array.isArray(u) && u.includes("session_lost_mid_stage") || e.headline.params.reason === "session_lost_mid_stage", f = c.delivery_confirmed === !0, p = e.status === "DeliveryUncertain" || e.status === "ReconciliationRequired" || c.outcome === "uncertain";
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(i, e),
		children: [
			p ? /* @__PURE__ */ h("p", {
				className: "studio-alert",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ m("span", { children: a(f ? "template.recovery.confirmedAlert" : "template.recovery.uncertainAlert") })]
			}) : null,
			/* @__PURE__ */ m(li, { card: e }),
			d && !l ? /* @__PURE__ */ h("div", {
				className: "studio-consequence",
				children: [/* @__PURE__ */ m(Y, {
					name: "link",
					size: 14
				}), /* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("p", { children: a("template.recovery.sessionRepair") }), /* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-btn",
					onClick: () => r({
						...yt,
						view: "intents",
						repo: e.repo.repo_id,
						intent: e.intent.intent_key
					}),
					children: a("template.recovery.manageConversation")
				})] })]
			}) : null,
			/* @__PURE__ */ m(Z, {
				title: a("template.recovery.boundary"),
				icon: "clock",
				children: /* @__PURE__ */ m(oi, { children: /* @__PURE__ */ m(si, {
					src: a("template.recovery.boundarySrc"),
					icon: "check",
					value: s.audit.boundary_event ? [s.audit.boundary_event.stage, s.audit.boundary_event.type].filter(Boolean).join(" · ") : e.captured.boundary_token ?? a("common.unavailable"),
					sub: s.audit.boundary_event ? a("template.recovery.boundarySub", { at: K(i, s.audit.boundary_event.ts) }) : a("template.recovery.boundaryUnknown"),
					conflict: !s.audit.boundary_event && !e.captured.boundary_token
				}) })
			}),
			/* @__PURE__ */ m(Z, {
				title: a("template.recovery.evidence"),
				icon: "doc",
				children: /* @__PURE__ */ h(oi, { children: [
					s.state ? /* @__PURE__ */ m(si, {
						src: a("template.recovery.src.state"),
						icon: "doc",
						value: `${s.state.current_stage ?? a("common.unavailable")} · ${Bi(s.state.sha256)}`,
						sub: `${s.state.relpath} · ${a("template.recovery.revisions", { n: s.state.revision_count })}`,
						conflict: s.state.stable === !1
					}) : null,
					/* @__PURE__ */ m(si, {
						src: a("template.recovery.src.audit"),
						icon: "doc",
						value: s.audit.last_event ? `${s.audit.last_event.type} · ${K(i, s.audit.last_event.ts)}` : a("common.unavailable"),
						sub: a("template.recovery.auditSub", {
							shards: we(i, s.audit.shards?.length ?? null),
							complete: s.audit.complete ? a("template.recovery.complete") : a("template.recovery.incomplete")
						}),
						conflict: s.audit.complete === !1
					}),
					s.directive ? /* @__PURE__ */ m(si, {
						src: a("template.recovery.src.directive"),
						icon: "gate",
						value: [s.directive.stage, Vi(s.directive)].filter(Boolean).join(" · "),
						sub: s.directive.matches_state ? a("template.recovery.directiveMatches") : a("template.recovery.directiveDiffers"),
						conflict: !s.directive.matches_state
					}) : null,
					s.session ? /* @__PURE__ */ m(si, {
						src: a("template.recovery.src.session"),
						icon: "activity",
						value: s.session.slot_key,
						sub: d ? a("template.recovery.sessionMissing") : a("template.recovery.sessionSub", {
							state: s.session.running ? a("template.recovery.sessionRunning") : a("template.recovery.sessionIdle"),
							queue: s.session.queue_depth,
							reasons: Array.isArray(s.session.busy_reasons) ? s.session.busy_reasons.join(", ") || a("common.none") : a("common.unavailable")
						}),
						conflict: d && !l
					}) : null,
					/* @__PURE__ */ m(si, {
						src: a("template.recovery.src.delivery"),
						icon: "send",
						value: f ? a("template.recovery.outcome.confirmed") : c.outcome ? a(`template.recovery.outcome.${c.outcome}`) : a(`enum.actionStatus.${e.status}`),
						sub: a("template.recovery.deliverySub", {
							confirmed: a(f ? "template.recovery.yes" : "template.recovery.no"),
							at: c.delivered_at ? K(i, c.delivered_at) : a("common.unavailable")
						}),
						conflict: p && !f
					}),
					/* @__PURE__ */ m(si, {
						src: a("template.recovery.src.marker"),
						icon: "clock",
						value: s.markers?.turn_counter == null ? a("common.unavailable") : a("template.recovery.turnCounter", { n: s.markers.turn_counter }),
						sub: a("template.recovery.markerSub", {
							at: s.markers?.human_turn_at ? K(i, s.markers.human_turn_at) : a("common.unavailable"),
							presence: !s.presence || s.presence.ok === null ? a("template.recovery.presenceUnknown") : s.presence.ok ? a("template.recovery.presenceOk") : a("template.recovery.presenceFailed")
						}),
						conflict: s.presence?.ok === !1
					}),
					s.cursor_readback ? /* @__PURE__ */ m(si, {
						src: a("template.recovery.src.cursor"),
						icon: "intent",
						value: `${s.cursor_readback.space} · ${s.cursor_readback.dir_name}`,
						sub: s.cursor_readback.ok ? a("template.recovery.cursorOk") : a("template.recovery.cursorMismatch", { fields: Array.isArray(s.cursor_readback.mismatch) ? s.cursor_readback.mismatch.join(", ") : a("common.unavailable") }),
						conflict: !s.cursor_readback.ok
					}) : null,
					s.git ? /* @__PURE__ */ m(si, {
						src: a("template.recovery.src.git"),
						icon: "git",
						value: `${s.git.branch ?? a("common.unavailable")} · ${Bi(s.git.head)}`,
						sub: s.git.dirty ? a("template.recovery.gitDirty", { n: s.git.dirty_files }) : a("template.recovery.gitClean")
					}) : null
				] })
			}),
			p ? /* @__PURE__ */ m(Z, {
				title: a(f ? "template.recovery.confirmedTitle" : "template.recovery.contradiction"),
				icon: "warn",
				children: /* @__PURE__ */ h(ni, {
					tone: "danger",
					children: [/* @__PURE__ */ m("p", { children: a(f ? "template.recovery.confirmedBody" : "template.recovery.contradictionBody") }), /* @__PURE__ */ m(ri, {
						icon: "warn",
						tone: "warn",
						children: a("template.recovery.contradictionChecks", {
							row: c.transcript_row ? a("template.recovery.rowFound", { at: K(i, c.transcript_row.ts) }) : a("template.recovery.rowAbsent"),
							disk: c.disk_baseline_unchanged === null ? a("template.recovery.diskUnknown") : c.disk_baseline_unchanged ? a("template.recovery.diskUnchanged") : a("template.recovery.diskChanged"),
							boot: c.boot_id_unchanged === null ? a("template.recovery.bootUnknown") : c.boot_id_unchanged ? a("template.recovery.bootSame") : a(f ? "template.recovery.bootRestartedConfirmed" : "template.recovery.bootRestarted")
						})
					})]
				})
			}) : null,
			/* @__PURE__ */ m(di, {
				card: e,
				title: a("template.recovery.findings")
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-qmetas",
				children: [/* @__PURE__ */ m(X, {
					icon: "lock",
					children: a(`template.recovery.risk.${e.risk_class}`)
				}), /* @__PURE__ */ m(X, {
					mono: !0,
					children: e.action_id
				})]
			}),
			/* @__PURE__ */ m(vi, {
				card: e,
				title: a("template.recovery.choices")
			}),
			/* @__PURE__ */ m(wi, {
				card: e,
				advisor: o
			})
		]
	});
}
//#endregion
//#region src/templates/TemplateRegistry.tsx
function Ui({ card: e }) {
	let t = H(), { t: n } = t, r = e.evidence.session;
	return /* @__PURE__ */ h("section", {
		className: "studio-template",
		"data-type": e.type,
		"aria-label": fi(t, e),
		children: [
			/* @__PURE__ */ m(li, { card: e }),
			/* @__PURE__ */ h(Z, {
				title: n("template.command.dispatch"),
				icon: "play",
				children: [
					/* @__PURE__ */ h(oi, { children: [/* @__PURE__ */ m(si, {
						src: n("template.command.session"),
						icon: "activity",
						value: r?.slot_key ?? n("template.command.noSession"),
						sub: r ? n("template.command.sessionSub", {
							state: r.running ? n("template.recovery.sessionRunning") : n("template.recovery.sessionIdle"),
							turn: r.last_turn_ts ? K(t, r.last_turn_ts) : n("common.unavailable")
						}) : n("template.command.noSessionSub"),
						conflict: !r
					}), /* @__PURE__ */ m(si, {
						src: n("template.command.stage"),
						icon: "gate",
						value: e.stage?.slug ?? n("common.none"),
						sub: n("template.command.stageSub")
					})] }),
					/* @__PURE__ */ h("div", {
						className: "studio-row studio-qmetas",
						children: [/* @__PURE__ */ m(X, {
							icon: "lock",
							children: n("template.command.lease")
						}), /* @__PURE__ */ m(X, {
							mono: !0,
							children: e.intent.intent_key
						})]
					}),
					/* @__PURE__ */ m(ri, {
						icon: "info",
						children: n("template.command.note")
					})
				]
			}),
			/* @__PURE__ */ m(vi, {
				card: e,
				title: n("template.command.choices")
			})
		]
	});
}
var Wi = {
	gate: ji,
	revision: ji,
	question: zi,
	missing_input: Ii,
	recovery: Hi,
	delivery_uncertain: Hi,
	failure: ki,
	circuit_breaker: ki,
	install_conflict: Pi,
	budget_stop: Oi,
	run: Ui,
	resume: Ui,
	force_stop: Ui,
	prepare_commit: Ui
};
function Gi(e) {
	return e.queue_type === "delivery_uncertain" && e.type !== "delivery_uncertain" ? Hi : Wi[e.type];
}
function Ki(e) {
	let t = Gi(e.card);
	return /* @__PURE__ */ m(t, { ...e });
}
function qi() {
	for (let e of Object.keys(Wi)) Dr(e, Ki);
}
qi();
//#endregion
//#region src/views/actions/index.tsx
var Ji = /* @__PURE__ */ O({ default: () => Yi }), Yi = ti, Xi = [
	"Idle",
	"Queued",
	"Running",
	"WaitingForYou",
	"Paused",
	"Parked",
	"Interrupted",
	"ReconciliationRequired",
	"RetryEligible",
	"CircuitOpen",
	"Failed",
	"Completed",
	"Archived"
], Zi = [
	"gate",
	"revision",
	"question",
	"missing_input",
	"recovery",
	"delivery_uncertain",
	"failure",
	"circuit_breaker",
	"install_conflict",
	"budget_stop",
	"run",
	"resume",
	"force_stop",
	"prepare_commit"
], Qi = [
	"critical",
	"blocking",
	"attention",
	"info"
], $i = [
	"studio",
	"aidlc",
	"kirocrew",
	"git",
	"slack"
], ea = [
	"initialization",
	"ideation",
	"inception",
	"construction",
	"operation"
], ta = {
	aidlc: "logo",
	studio: "send",
	kirocrew: "activity",
	git: "git",
	slack: "slack"
}, na = {
	critical: "recovery",
	blocking: "gate",
	attention: "warn",
	info: "info"
}, ra = {
	critical: "danger",
	blocking: "accent",
	attention: "warn",
	info: "neutral"
};
function ia(e) {
	let t = e.message_key.startsWith("audit.");
	return e.source === "aidlc" ? t ? "audit" : "mismatch" : t ? "mismatch" : "studio";
}
function aa(e, t) {
	return e.has(t.message_key) ? e.t(t.message_key) : t.message_key.startsWith("audit.") ? e.t("audit.unknown_event") : e.t("activity.row.unknownKind");
}
var oa = /* @__PURE__ */ new Map();
function sa(e, t) {
	if (!Number.isFinite(Date.parse(t))) return t;
	let n = oa.get(e);
	return n || (n = new Intl.DateTimeFormat(e, {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
		hour12: !1
	}), oa.set(e, n)), n.format(new Date(t));
}
function ca(e) {
	return e.id === null ? `at:${e.at}:${e.kind}:${e.message_key}` : `id:${e.id}`;
}
function la({ entry: e, selected: t, onOpenEvidence: n, onOpenAction: r }) {
	let i = H(), { t: a } = i, o = ia(e), s = a(`enum.source.${e.source}`), c = sa(i.locale, e.at), l = typeof e.params.stage == "string" ? e.params.stage : null, u = e.refs.audit, d = e.refs.action_id ?? null;
	return /* @__PURE__ */ h("li", {
		className: "studio-tlrow",
		"data-source": e.source,
		"data-severity": e.severity,
		...t ? { "aria-current": "true" } : {},
		children: [
			/* @__PURE__ */ h("span", {
				className: "studio-tlrow-time studio-mono",
				children: [c, /* @__PURE__ */ h("span", {
					className: "studio-sr",
					children: [" ", a("activity.row.at", { when: i.fmt.dateTime(e.at) })]
				})]
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-tlrow-icon",
				"aria-hidden": !0,
				children: /* @__PURE__ */ m(Y, {
					name: ta[e.source],
					size: 13
				})
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-tlrow-body",
				children: [/* @__PURE__ */ m("p", {
					className: "studio-tlrow-msg",
					children: o === "mismatch" ? a("activity.row.mismatch") : aa(i, e)
				}), /* @__PURE__ */ h("div", {
					className: "studio-tlrow-meta",
					children: [
						/* @__PURE__ */ m(X, {
							icon: ta[e.source],
							children: s
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-tlrow-kindword",
							children: a(o === "audit" ? "activity.row.auditEvent" : "activity.row.derived")
						}),
						e.severity === "info" ? null : /* @__PURE__ */ m(X, {
							tone: ra[e.severity],
							icon: na[e.severity],
							children: a(`enum.severity.${e.severity}`)
						}),
						l ? /* @__PURE__ */ m(X, {
							mono: !0,
							children: a("activity.row.stage", { stage: l })
						}) : null,
						u ? /* @__PURE__ */ m(X, {
							mono: !0,
							icon: "doc",
							children: u.pos === null ? a("activity.row.shardNoPos", { shard: u.shard }) : a("activity.row.shard", {
								shard: u.shard,
								pos: u.pos
							})
						}) : null,
						e.refs.git ? /* @__PURE__ */ m(X, {
							mono: !0,
							icon: "git",
							children: a("activity.row.commit", { sha: e.refs.git.sha.slice(0, 8) })
						}) : null,
						e.refs.session_key ? /* @__PURE__ */ m(X, {
							mono: !0,
							icon: "link",
							children: a("activity.row.session", { session: e.refs.session_key })
						}) : null
					]
				})]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-tlrow-actions",
				children: [d && r ? /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-btn-sm",
					onClick: () => r(d),
					"aria-label": a("activity.row.openActionFor", { action: d }),
					children: [/* @__PURE__ */ m(Y, {
						name: "inbox",
						size: 13
					}), a("activity.row.openAction")]
				}) : null, /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-btn-sm",
					onClick: () => n(e),
					"aria-expanded": t,
					"aria-label": a("activity.row.evidenceFor", {
						source: s,
						time: c
					}),
					children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}), a("activity.row.evidence")]
				})]
			})
		]
	});
}
//#endregion
//#region src/activity/EvidencePanel.tsx
function ua(e) {
	if (e == null) return "";
	if (typeof e == "string") return e;
	if (typeof e == "number" || typeof e == "boolean") return String(e);
	try {
		return JSON.stringify(e);
	} catch {
		return String(e);
	}
}
function da({ entry: e, onClose: t, onOpenAction: n }) {
	let r = H(), { t: i } = r, a = u(null), [s, c] = d(!1), l = ia(e), f = e.refs.audit, g = e.refs.action_id ?? null, _ = !!(f || e.refs.git || g || e.refs.session_key);
	o(() => {
		a.current?.focus();
	}, [e]), o(() => {
		let e = (e) => {
			e.key === "Escape" && t();
		};
		return window.addEventListener("keydown", e), () => window.removeEventListener("keydown", e);
	}, [t]), o(() => {
		c(!1);
	}, [e]);
	let v = async () => {
		if (e.raw) try {
			await navigator.clipboard.writeText(e.raw), c(!0);
		} catch {}
	}, y = Object.entries(e.params).filter(([e]) => e !== "event"), b = f?.event || (typeof e.params.event == "string" ? e.params.event : "");
	return /* @__PURE__ */ h("aside", {
		className: "studio-evdrawer",
		"aria-label": i("activity.evidence.title"),
		children: [/* @__PURE__ */ h("header", {
			className: "studio-evdrawer-head",
			children: [/* @__PURE__ */ h("h2", { children: [
				/* @__PURE__ */ m(Y, {
					name: "lock",
					size: 15
				}),
				" ",
				i("activity.evidence.title")
			] }), /* @__PURE__ */ m("button", {
				type: "button",
				ref: a,
				className: "studio-icon-btn",
				onClick: t,
				"aria-label": i("common.close"),
				children: /* @__PURE__ */ m(Y, {
					name: "close",
					size: 15
				})
			})]
		}), /* @__PURE__ */ h("div", {
			className: "studio-evdrawer-body",
			children: [
				/* @__PURE__ */ h("section", {
					className: "studio-block",
					children: [
						/* @__PURE__ */ m("h3", { children: i("activity.evidence.provenance") }),
						/* @__PURE__ */ h("div", {
							className: "studio-row studio-wrapchips",
							children: [/* @__PURE__ */ m(X, {
								icon: ta[e.source],
								children: i(`enum.source.${e.source}`)
							}), /* @__PURE__ */ m(X, {
								tone: l === "mismatch" ? "danger" : "neutral",
								children: i(l === "audit" ? "activity.row.auditEvent" : l === "studio" ? "activity.row.derived" : "activity.row.mismatch")
							})]
						}),
						/* @__PURE__ */ m("p", {
							className: "studio-consequence",
							children: i(`activity.evidence.source.${e.source}`)
						}),
						/* @__PURE__ */ h("dl", {
							className: "studio-evgrid",
							children: [
								/* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.at") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono",
										children: K(r, e.at)
									})]
								}),
								/* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.kind") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: e.kind
									})]
								}),
								/* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.messageKey") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: e.message_key
									})]
								}),
								/* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.severity") }), /* @__PURE__ */ m("dd", { children: i(`enum.severity.${e.severity}`) })]
								}),
								b ? /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.auditEvent") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: b
									})]
								}) : null
							]
						}),
						l === "mismatch" ? null : /* @__PURE__ */ m("p", {
							className: "studio-muted studio-small",
							children: aa(r, e)
						})
					]
				}),
				/* @__PURE__ */ h("section", {
					className: "studio-block",
					children: [/* @__PURE__ */ m("h3", { children: i("activity.evidence.rawTitle") }), e.raw ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("pre", {
						className: "studio-pre studio-mono studio-wrap-any",
						children: e.raw
					}), /* @__PURE__ */ m("div", {
						className: "studio-row",
						children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							onClick: () => void v(),
							children: [/* @__PURE__ */ m(Y, {
								name: "doc",
								size: 13
							}), i(s ? "common.copied" : "activity.evidence.copy")]
						})
					})] }) : /* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: e.source === "aidlc" ? i("activity.evidence.rawNone.aidlc") : i("activity.evidence.rawNone.studio")
					})]
				}),
				/* @__PURE__ */ h("section", {
					className: "studio-block",
					children: [
						/* @__PURE__ */ m("h3", { children: i("activity.evidence.location") }),
						_ ? /* @__PURE__ */ h("dl", {
							className: "studio-evgrid",
							children: [
								f ? /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.auditShard") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: f.pos === null ? i("activity.row.shardNoPos", { shard: f.shard }) : i("activity.row.shard", {
											shard: f.shard,
											pos: f.pos
										})
									})]
								}) : null,
								e.refs.git ? /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.commit") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: e.refs.git.sha
									})]
								}) : null,
								e.refs.session_key ? /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.session") }), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: e.refs.session_key
									})]
								}) : null,
								g ? /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", { children: i("activity.evidence.action") }), /* @__PURE__ */ h("dd", {
										className: "studio-mono studio-wrap-any",
										children: [g, n ? /* @__PURE__ */ h(p, { children: [" ", /* @__PURE__ */ m("button", {
											type: "button",
											className: "studio-btn studio-btn-sm",
											onClick: () => n(g),
											"aria-label": i("activity.row.openActionFor", { action: g }),
											children: i("activity.row.openAction")
										})] }) : null]
									})]
								}) : null
							]
						}) : /* @__PURE__ */ m("p", {
							className: "studio-consequence",
							children: i("activity.evidence.locationNone")
						}),
						/* @__PURE__ */ h("p", {
							className: "studio-consequence",
							children: [
								/* @__PURE__ */ m(Y, {
									name: "lock",
									size: 13
								}),
								" ",
								i("activity.evidence.redaction")
							]
						})
					]
				}),
				/* @__PURE__ */ h("section", {
					className: "studio-block",
					children: [/* @__PURE__ */ m("h3", { children: i("activity.evidence.fields") }), y.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: i("activity.evidence.fieldsNone")
					}) : /* @__PURE__ */ m("dl", {
						className: "studio-evgrid",
						children: y.map(([e, t]) => /* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", {
								className: "studio-mono",
								children: e
							}), /* @__PURE__ */ m("dd", {
								className: "studio-mono studio-wrap-any",
								children: ua(t)
							})]
						}, e))
					})]
				})
			]
		})]
	});
}
//#endregion
//#region src/activity/ExportPanel.tsx
function fa(e) {
	return `aidlc-studio-diagnostics-${(Number.isFinite(Date.parse(e)) ? new Date(e) : /* @__PURE__ */ new Date()).toISOString().replace(/[:.]/g, "-")}.json`;
}
function pa({ allowHumanText: e, onOpenSettings: t }) {
	let n = H(), { t: r } = n, a = xe(), [s, c] = d(!1), [l, f] = d(!1), [g, _] = d(null), [v, y] = d(null), x = u(null), S = i(() => {
		x.current &&= (URL.revokeObjectURL(x.current), null);
	}, []);
	o(() => S, [S]), o(() => {
		e || c(!1);
	}, [e]);
	let C = async () => {
		f(!0), _(null);
		try {
			let t = await a.diagnostics({
				export: !0,
				includeHumanText: s && e
			}), n = JSON.stringify(t, null, 2), r = new Blob([n], { type: "application/json" });
			S();
			let i = URL.createObjectURL(r);
			x.current = i, y({
				url: i,
				filename: fa(t.generated_at),
				size: r.size,
				generatedAt: t.generated_at
			});
		} catch (e) {
			let t = W(e);
			y(null), _(n.has(`errors.${t.code}`) ? r(`errors.${t.code}`) : t.message);
		} finally {
			f(!1);
		}
	};
	return /* @__PURE__ */ h("section", {
		className: "studio-block",
		"aria-label": r("activity.export.region"),
		children: [
			/* @__PURE__ */ m("h3", { children: r("activity.export.title") }),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: r("activity.export.lede")
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-export-grid",
				children: [/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("h4", {
					className: "studio-subhead",
					children: r("activity.export.redactsTitle")
				}), /* @__PURE__ */ h("ul", {
					className: "studio-list",
					children: [
						/* @__PURE__ */ m("li", { children: r("activity.export.redacts.credentials") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.redacts.paths") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.redacts.humanText") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.redacts.artifacts") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.redacts.unrelated") })
					]
				})] }), /* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("h4", {
					className: "studio-subhead",
					children: r("activity.export.includesTitle")
				}), /* @__PURE__ */ h("ul", {
					className: "studio-list",
					children: [
						/* @__PURE__ */ m("li", { children: r("activity.export.includes.health") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.includes.settings") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.includes.repos") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.includes.actions") }),
						/* @__PURE__ */ m("li", { children: r("activity.export.includes.activity") })
					]
				})] })]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-export-opt",
				children: [/* @__PURE__ */ h("label", {
					className: "studio-check",
					children: [/* @__PURE__ */ m("input", {
						type: "checkbox",
						checked: s,
						disabled: !e,
						onChange: (e) => c(e.target.checked),
						"aria-describedby": "aidlc-export-humantext-note"
					}), /* @__PURE__ */ m("span", { children: r("activity.export.humanText") })]
				}), /* @__PURE__ */ m("p", {
					id: "aidlc-export-humantext-note",
					className: "studio-consequence",
					children: e ? r("activity.export.humanTextWarn") : /* @__PURE__ */ h(p, { children: [
						/* @__PURE__ */ m(Y, {
							name: "lock",
							size: 13
						}),
						" ",
						r("activity.export.humanTextBlocked"),
						t ? /* @__PURE__ */ h(p, { children: [" ", /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							onClick: t,
							children: [/* @__PURE__ */ m(Y, {
								name: "settings",
								size: 13
							}), r("settings.page.title")]
						})] }) : null
					] })
				})]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-row studio-wrapchips",
				children: /* @__PURE__ */ h(b, {
					primary: !0,
					type: "button",
					onClick: () => void C(),
					disabled: l,
					children: [/* @__PURE__ */ m(Y, {
						name: "doc",
						size: 13
					}), r(l ? "activity.export.producing" : "activity.export.produce")]
				})
			}),
			/* @__PURE__ */ m("div", {
				role: "status",
				"aria-live": "polite",
				className: "studio-export-result",
				children: g ? /* @__PURE__ */ m("p", {
					className: "studio-error",
					children: r("activity.export.failed", { message: g })
				}) : v ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("p", { children: r("activity.export.ready", {
					size: je(n, v.size),
					when: K(n, v.generatedAt)
				}) }), /* @__PURE__ */ h("div", {
					className: "studio-row studio-wrapchips",
					children: [/* @__PURE__ */ h("a", {
						className: "studio-btn",
						href: v.url,
						download: v.filename,
						children: [/* @__PURE__ */ m(Y, {
							name: "external",
							size: 13
						}), r("activity.export.download", { filename: v.filename })]
					}), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						onClick: () => {
							S(), y(null);
						},
						children: r("activity.export.discard")
					})]
				})] }) : null
			})
		]
	});
}
//#endregion
//#region src/activity/Timeline.tsx
function ma({ entries: e, selectedKey: t, onOpenEvidence: n, onOpenAction: r, label: i }) {
	let a = H(), o = [];
	for (let t of e) {
		let e = a.fmt.date(t.at), n = o[o.length - 1];
		n && n.day === e ? n.entries.push(t) : o.push({
			day: e,
			entries: [t]
		});
	}
	return /* @__PURE__ */ m("ol", {
		className: "studio-timeline",
		"aria-label": i ?? a.t("activity.page.timeline"),
		children: o.map((e) => /* @__PURE__ */ h("li", {
			className: "studio-timeline-day",
			children: [/* @__PURE__ */ m("h3", {
				className: "studio-timeline-date",
				children: e.day
			}), /* @__PURE__ */ m("ol", {
				className: "studio-timeline-rows",
				children: e.entries.map((e) => {
					let i = ca(e);
					return /* @__PURE__ */ m(la, {
						entry: e,
						selected: i === t,
						onOpenEvidence: n,
						...r ? { onOpenAction: r } : {}
					}, i);
				})
			})]
		}, e.day))
	});
}
//#endregion
//#region src/activity/ActivityView.tsx
var ha = 200, ga = {
	source: "",
	stage: "",
	kind: "",
	type: "",
	severity: "",
	since: "",
	until: ""
};
function _a(e) {
	if (!e) return;
	let t = Date.parse(e);
	return Number.isFinite(t) ? new Date(t).toISOString() : void 0;
}
function va(e, t) {
	let n = t.since ? Date.parse(t.since) : NaN, r = t.until ? Date.parse(t.until) : NaN;
	return e.filter((e) => {
		if (t.source && e.source !== t.source || t.severity && e.severity !== t.severity || t.kind && e.kind !== t.kind || t.stage && (typeof e.params.stage == "string" ? e.params.stage : null) !== t.stage) return !1;
		let i = Date.parse(e.at);
		return !(Number.isFinite(n) && Number.isFinite(i) && i < n || Number.isFinite(r) && Number.isFinite(i) && i > r);
	});
}
function ya({ route: e, go: t }) {
	let n = H(), { t: r } = n, a = xe(), [s, c] = d(ga), [u, f] = d([null]), [p, g] = d(0), [_, v] = d(null), y = !!(e.repo && e.intent), b = u[p] ?? null;
	o(() => {
		f([null]), g(0), v(null);
	}, [
		e.repo,
		e.intent,
		e.space,
		s
	]);
	let x = l(() => {
		let t = { limit: ha };
		e.repo && (t.repo = e.repo), e.intent ? (t.intent = e.intent, t.space = e.space || "default") : e.space && (t.space = e.space), s.source && (t.source = s.source), s.stage && (t.stage = s.stage), s.kind && (t.kind = s.kind), s.type && (t.type = s.type), s.severity && (t.severity = s.severity);
		let n = _a(s.since), r = _a(s.until);
		return n && (t.since = n), r && (t.until = r), b && (t.cursor = b), t;
	}, [
		e.repo,
		e.intent,
		e.space,
		s,
		b
	]), C = J(`activity:${JSON.stringify(x)}`, i((e) => a.activity(x, { signal: e }), [a, x]), { revalidateOn: [
		"activity.appended",
		"action.created",
		"action.updated",
		"reset"
	] }), w = J("settings", i((e) => a.settings({ signal: e }), [a]), { interval: 0 }), T = C.data?.items ?? [], E = y ? va(T, s) : T, D = C.data?.next_cursor ?? null, O = Object.values(s).filter(Boolean).length, k = _ ? ca(_) : null, A = i((e) => t({
		view: "actions",
		action: e
	}), [t]);
	return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ h("div", {
			className: "studio-page studio-activity",
			children: [
				/* @__PURE__ */ h("h1", { children: [
					/* @__PURE__ */ m(Y, {
						name: "activity",
						size: 18
					}),
					" ",
					r("activity.page.title")
				] }),
				/* @__PURE__ */ m("p", {
					className: "studio-lede",
					children: r("activity.page.lede")
				}),
				/* @__PURE__ */ h("section", {
					className: "studio-filters",
					"aria-label": r("activity.filter.title"),
					children: [
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.source") }), /* @__PURE__ */ h("select", {
								value: s.source,
								onChange: (e) => c((t) => ({
									...t,
									source: e.target.value
								})),
								children: [/* @__PURE__ */ m("option", {
									value: "",
									children: r("activity.filter.allSources")
								}), $i.map((e) => /* @__PURE__ */ m("option", {
									value: e,
									children: r(`enum.source.${e}`)
								}, e))]
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.severity") }), /* @__PURE__ */ h("select", {
								value: s.severity,
								onChange: (e) => c((t) => ({
									...t,
									severity: e.target.value
								})),
								children: [/* @__PURE__ */ m("option", {
									value: "",
									children: r("activity.filter.anySeverity")
								}), Qi.map((e) => /* @__PURE__ */ m("option", {
									value: e,
									children: r(`enum.severity.${e}`)
								}, e))]
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.type") }), /* @__PURE__ */ h("select", {
								value: s.type,
								disabled: y,
								"aria-describedby": y ? "aidlc-activity-type-note" : void 0,
								onChange: (e) => c((t) => ({
									...t,
									type: e.target.value
								})),
								children: [/* @__PURE__ */ m("option", {
									value: "",
									children: r("activity.filter.anyType")
								}), Zi.map((e) => /* @__PURE__ */ m("option", {
									value: e,
									children: r(`enum.actionType.${e}`)
								}, e))]
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.stage") }), /* @__PURE__ */ m("input", {
								type: "text",
								className: "studio-mono",
								value: s.stage,
								placeholder: r("activity.filter.anyStage"),
								onChange: (e) => c((t) => ({
									...t,
									stage: e.target.value.trim()
								}))
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.kind") }), /* @__PURE__ */ m("input", {
								type: "text",
								className: "studio-mono",
								value: s.kind,
								placeholder: r("activity.filter.anyKind"),
								onChange: (e) => c((t) => ({
									...t,
									kind: e.target.value.trim()
								}))
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.since") }), /* @__PURE__ */ m("input", {
								type: "datetime-local",
								value: s.since,
								onChange: (e) => c((t) => ({
									...t,
									since: e.target.value
								}))
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-sfield",
							children: [/* @__PURE__ */ m("span", { children: r("activity.filter.until") }), /* @__PURE__ */ m("input", {
								type: "datetime-local",
								value: s.until,
								onChange: (e) => c((t) => ({
									...t,
									until: e.target.value
								}))
							})]
						}),
						/* @__PURE__ */ h("div", {
							className: "studio-filters-foot",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								onClick: () => c(ga),
								disabled: O === 0,
								children: [/* @__PURE__ */ m(Y, {
									name: "close",
									size: 13
								}), r("activity.filter.clear")]
							}), O > 0 ? /* @__PURE__ */ m("span", {
								className: "studio-muted",
								children: q(n, "activity.filter.applied", O)
							}) : null]
						})
					]
				}),
				y ? null : /* @__PURE__ */ m("p", {
					className: "studio-consequence",
					children: r("activity.page.studioOnlyNote")
				}),
				y ? /* @__PURE__ */ m("p", {
					className: "studio-consequence",
					children: r("activity.page.mergedNote", { n: n.fmt.number(T.length) })
				}) : null,
				y ? /* @__PURE__ */ h("p", {
					id: "aidlc-activity-type-note",
					className: "studio-consequence",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}),
						" ",
						r("activity.filter.typeUnavailable")
					]
				}) : null,
				!y && s.source === "aidlc" ? /* @__PURE__ */ h("p", {
					className: "studio-consequence",
					"data-tone": "warn",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}),
						" ",
						r("activity.page.sourceNeedsScope")
					]
				}) : null,
				/* @__PURE__ */ m("p", {
					className: "studio-activity-count",
					role: "status",
					"aria-live": "polite",
					children: C.loading ? r("activity.page.reading") : y && E.length !== T.length ? r("activity.page.narrowed", {
						shown: n.fmt.number(E.length),
						loaded: n.fmt.number(T.length)
					}) : q(n, "activity.page.count", E.length)
				}),
				C.error ? /* @__PURE__ */ h("div", {
					className: "studio-banner",
					"data-tone": "warn",
					role: "status",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 15
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-grow",
							children: r("activity.page.error", { message: n.has(`errors.${C.error.code}`) ? r(`errors.${C.error.code}`) : C.error.message })
						}),
						/* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => void C.refresh(),
							children: [/* @__PURE__ */ m(Y, {
								name: "refresh",
								size: 13
							}), r("activity.page.retry")]
						})
					]
				}) : null,
				/* @__PURE__ */ h("div", {
					className: "studio-activity-body",
					children: [/* @__PURE__ */ h("div", {
						className: "studio-grow",
						children: [
							E.length === 0 && !C.loading ? /* @__PURE__ */ m(S, {
								icon: /* @__PURE__ */ m(Y, {
									name: "activity",
									size: 18
								}),
								title: r(O > 0 ? "activity.page.empty.title" : "activity.page.emptyScope.title"),
								subtitle: r(O > 0 ? "activity.page.empty.body" : "activity.page.emptyScope.body")
							}) : /* @__PURE__ */ m(ma, {
								entries: E,
								selectedKey: k,
								onOpenEvidence: v,
								onOpenAction: A
							}),
							p > 0 || D ? /* @__PURE__ */ h("div", {
								className: "studio-row studio-pager",
								children: [
									/* @__PURE__ */ h("button", {
										type: "button",
										className: "studio-btn studio-btn-sm",
										onClick: () => g((e) => Math.max(0, e - 1)),
										disabled: p === 0,
										children: [/* @__PURE__ */ m(Y, {
											name: "back",
											size: 13
										}), r("activity.page.newer")]
									}),
									/* @__PURE__ */ m("span", {
										className: "studio-muted studio-mono",
										children: r("activity.page.pageNumber", { n: n.fmt.number(p + 1) })
									}),
									/* @__PURE__ */ h("button", {
										type: "button",
										className: "studio-btn studio-btn-sm",
										onClick: () => {
											D && (f((e) => e.length > p + 1 ? e : [...e, D]), g((e) => e + 1));
										},
										disabled: !D,
										children: [r("activity.page.older"), /* @__PURE__ */ m(Y, {
											name: "chevron",
											size: 13
										})]
									})
								]
							}) : null,
							/* @__PURE__ */ h("p", {
								className: "studio-consequence",
								children: [
									/* @__PURE__ */ m(Y, {
										name: "info",
										size: 13
									}),
									" ",
									r("activity.page.provenance")
								]
							}),
							/* @__PURE__ */ h("p", {
								className: "studio-consequence",
								children: [
									/* @__PURE__ */ m(Y, {
										name: "lock",
										size: 13
									}),
									" ",
									r("activity.page.redaction")
								]
							}),
							/* @__PURE__ */ m(pa, {
								allowHumanText: w.data?.settings.diagnostics.export_include_human_text ?? !1,
								onOpenSettings: () => t({ view: "settings" })
							})
						]
					}), _ ? /* @__PURE__ */ m(da, {
						entry: _,
						onClose: () => v(null),
						onOpenAction: A
					}) : null]
				})
			]
		})
	});
}
//#endregion
//#region src/views/activity/index.tsx
var ba = /* @__PURE__ */ O({ default: () => ya }), xa = "required_by:";
function Sa(e, t) {
	if (!t) return null;
	if (t.startsWith(xa)) return e.t("plan.lock.short.required_by", { slug: t.slice(12) });
	let n = `plan.lock.short.${t}`;
	return e.has(n) ? e.t(n) : e.t("plan.lock.short.unknown");
}
function Ca(e, t) {
	if (!t) return null;
	if (t.startsWith(xa)) return e.t("plan.lock.required_by", { slug: t.slice(12) });
	let n = `plan.lock.${t}`;
	return e.has(n) ? e.t(n) : e.t("plan.lock.unknown", { reason: t });
}
function wa({ stage: e, onToggle: t, busy: n, showState: r }) {
	let i = H(), { t: a } = i, o = s(), c = e.locked, l = Sa(i, e.lock_reason), u = Ca(i, e.lock_reason), d = [e.enabled ? a("plan.stage.on") : a("plan.stage.off")];
	return r && e.state && d.push(a("plan.stage.state", { state: a(`enum.stageState.${e.state}`) })), !e.in_grid && !e.enabled && d.push(a("plan.stage.excluded")), e.execution === "CONDITIONAL" && d.push(e.conditional_on ? a("plan.stage.conditionalOn", { condition: e.conditional_on }) : a("plan.stage.conditional")), e.execution === "ALWAYS" && d.push(a("plan.stage.always")), e.gate && d.push(a("plan.stage.gate")), e.per_unit && d.push(a("plan.stage.perUnit")), e.review_class && d.push(a("plan.stage.review", { class: e.review_class })), e.reviewer && d.push(a("plan.stage.reviewer", { reviewer: e.reviewer })), d.push(e.produces.length ? a("plan.stage.produces", { artifacts: e.produces.join(a("shell.format.listJoin")) }) : a("plan.stage.producesNone")), u && d.push(u), /* @__PURE__ */ h("span", {
		className: "studio-plan-ms",
		"data-on": e.enabled ? "true" : "false",
		"data-locked": c ? "true" : "false",
		...u ? { title: u } : {},
		children: [
			/* @__PURE__ */ h("label", {
				className: "studio-plan-ms-label",
				children: [
					/* @__PURE__ */ m("input", {
						type: "checkbox",
						checked: e.enabled,
						disabled: c || !t || n === !0,
						"aria-describedby": o,
						onChange: (n) => t?.(e.slug, n.target.checked)
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-mono studio-plan-ms-n",
						children: e.number || "—"
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-plan-ms-slug",
						children: e.slug
					})
				]
			}),
			e.gate ? /* @__PURE__ */ m(Y, {
				name: "gate",
				size: 11,
				strokeWidth: 2
			}) : null,
			e.per_unit ? /* @__PURE__ */ m(Y, {
				name: "intent",
				size: 11,
				strokeWidth: 2
			}) : null,
			c ? /* @__PURE__ */ m(Y, {
				name: "lock",
				size: 11,
				strokeWidth: 2
			}) : null,
			l ? /* @__PURE__ */ m("span", {
				className: "studio-plan-ms-why",
				children: l
			}) : null,
			/* @__PURE__ */ m("span", {
				id: o,
				className: "studio-sr",
				children: d.join(" ")
			})
		]
	});
}
//#endregion
//#region src/plan/EstimatePanel.tsx
function Ta(e, t, n) {
	let r = `estimate.source.${t.source}`, i = {
		kind: e.t("estimate.range"),
		source: e.has(r) ? e.t(r) : e.t("estimate.source.unknown"),
		confidence: e.t(`estimate.confidence.${t.confidence}`)
	};
	if (n === void 0) return e.t("estimate.qualifier", i);
	let a = n > 0 ? q(e, "estimate.samples", n) : e.t("estimate.samples.none");
	return e.t("estimate.qualifierSamples", {
		...i,
		samples: a
	});
}
function Ea({ icon: e, label: t, value: n, qualifier: r, small: i }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-stat",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-stat-k",
				children: [/* @__PURE__ */ m(Y, {
					name: e,
					size: 13
				}), t]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-stat-v",
				"data-small": i ? "true" : "false",
				children: n
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-stat-q",
				children: r
			})
		]
	});
}
function Da({ plan: e }) {
	let t = H(), { t: n } = t, { exact: r, estimate: i } = e, a = l(() => new Map(e.stages.map((e) => [e.slug, e])), [e.stages]), o = r.assumes_units, s = e.stages.some((e) => e.enabled && e.per_unit), c = o !== null && s ? q(t, "estimate.exact.artifactsAssumed", o) : n("estimate.exact.artifactsWhy");
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ h("section", {
			className: "studio-block",
			"aria-label": n("estimate.a11y.exact"),
			children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
				name: "check",
				size: 13
			}), n("estimate.exact.title")] }), /* @__PURE__ */ h("div", {
				className: "studio-stats",
				children: [
					/* @__PURE__ */ m(Ea, {
						icon: "check",
						label: n("estimate.exact.stages"),
						value: we(t, r.stages),
						qualifier: n("estimate.exact.ofGraph", {
							selected: t.fmt.number(r.stages),
							known: we(t, e.graph_stage_count)
						})
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "gate",
						label: n("estimate.exact.gates"),
						value: we(t, r.gates),
						qualifier: n("estimate.exact.gatesWhy")
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "doc",
						label: n("estimate.exact.artifacts"),
						value: we(t, r.artifacts),
						qualifier: c
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "review",
						label: n("estimate.exact.review"),
						small: !0,
						value: n("estimate.exact.reviewValue", {
							none: t.fmt.number(r.review_intensity.none),
							advisory: t.fmt.number(r.review_intensity.advisory),
							adversarial: t.fmt.number(r.review_intensity.adversarial)
						}),
						qualifier: n("estimate.exact.reviewWhy")
					})
				]
			})]
		}),
		/* @__PURE__ */ h("section", {
			className: "studio-block",
			"aria-label": n("estimate.a11y.estimate"),
			children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
				name: "clock",
				size: 13
			}), n("estimate.estimate.title")] }), /* @__PURE__ */ h("div", {
				className: "studio-stats",
				children: [
					/* @__PURE__ */ m(Ea, {
						icon: "clock",
						label: n("estimate.turns"),
						small: !0,
						value: Oe(t, i.turns),
						qualifier: Ta(t, i.turns, i.samples)
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "clock",
						label: n("estimate.active"),
						small: !0,
						value: Oe(t, i.active_secs),
						qualifier: Ta(t, i.active_secs)
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "clock",
						label: n("estimate.elapsed"),
						small: !0,
						value: i.elapsed_secs ? Oe(t, i.elapsed_secs) : G(t),
						qualifier: i.elapsed_secs ? `${Ta(t, i.elapsed_secs)} · ${n("estimate.elapsed.why")}` : n("estimate.elapsed.unavailable")
					}),
					/* @__PURE__ */ m(Ea, {
						icon: "warn",
						label: n("estimate.credits"),
						small: !0,
						value: n("estimate.credits.value"),
						qualifier: n("estimate.credits.why")
					})
				]
			})]
		}),
		/* @__PURE__ */ h("section", {
			className: "studio-block",
			children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
				name: "info",
				size: 13
			}), n("estimate.dominant.title")] }), i.dominant.length === 0 ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: n("estimate.dominant.none")
			}) : /* @__PURE__ */ m("ul", {
				className: "studio-crit",
				children: i.dominant.map((e) => {
					let r = a.get(e.slug), i = r ? Ca(t, r.lock_reason) : null, s = r?.produces ?? [];
					return /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ m(Y, {
						name: "clock",
						size: 13
					}), /* @__PURE__ */ h("span", {
						className: "studio-crit-txt",
						children: [
							n("estimate.dominant.item", {
								slug: e.slug,
								pct: t.fmt.number(e.share_pct)
							}),
							/* @__PURE__ */ h("span", {
								className: "studio-crit-why",
								children: [n("estimate.dominant.turns", { range: Oe(t, e.turns) }), r?.per_unit && o !== null ? ` · ${q(t, "estimate.dominant.perUnit", o)}` : ""]
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-crit-why",
								children: i ? n("estimate.dominant.locked", { reason: i }) : s.length ? n("estimate.dominant.lost", { artifacts: s.join(n("shell.format.listJoin")) }) : n("estimate.dominant.lostNone")
							})
						]
					})] }, e.slug);
				})
			})]
		}),
		i.coverage_lost.length > 0 ? /* @__PURE__ */ h("section", {
			className: "studio-block",
			children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 13
			}), n("estimate.coverage.title")] }), /* @__PURE__ */ m("ul", {
				className: "studio-crit",
				children: i.coverage_lost.map((e) => /* @__PURE__ */ h("li", {
					"data-tone": "warn",
					children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}), /* @__PURE__ */ m("span", {
						className: "studio-crit-txt",
						children: e.artifacts.length ? n("estimate.coverage.item", {
							slug: e.slug,
							artifacts: e.artifacts.join(n("shell.format.listJoin"))
						}) : n("estimate.coverage.itemNone", { slug: e.slug })
					})]
				}, e.slug))
			})]
		}) : null
	] });
}
//#endregion
//#region src/plan/PlanDiff.tsx
function Oa(e, t) {
	let n = Me(e, {
		key: t.message_key,
		params: t.params
	});
	return e.has(t.message_key) ? e.t(t.message_key, n) : e.t("plan.issue.unknown", {
		...n,
		code: t.code
	});
}
function ka({ issues: e, tone: t = "warn" }) {
	let n = H();
	return e.length === 0 ? null : /* @__PURE__ */ m("ul", {
		className: "studio-crit",
		children: e.map((e, r) => /* @__PURE__ */ h("li", {
			"data-tone": t,
			children: [/* @__PURE__ */ m(Y, {
				name: t === "danger" ? "recovery" : "warn",
				size: 13
			}), /* @__PURE__ */ m("span", {
				className: "studio-crit-txt",
				children: Oa(n, e)
			})]
		}, `${e.code}-${e.slugs.join(",")}-${r}`))
	});
}
function Aa(e) {
	let t = new Map(e.stages.map((e) => [e.slug, e])), n = /* @__PURE__ */ new Set(), r = /* @__PURE__ */ new Set();
	for (let t of e.stages) for (let e of t.produces) t.enabled && n.add(e), t.in_grid && r.add(e);
	let i = [], a = /* @__PURE__ */ new Set();
	for (let o of e.diff) {
		let s = t.get(o.slug);
		if (s) for (let t of s.produces) {
			if (a.has(t)) continue;
			let s = o.to_enabled && n.has(t) && !r.has(t), c = !o.to_enabled && !n.has(t);
			(s || c) && (a.add(t), i.push({
				artifact: t,
				gained: s,
				consumers: e.stages.filter((e) => e.enabled && e.consumes.includes(t)).map((e) => e.slug)
			}));
		}
	}
	return i;
}
function ja({ plan: e, hideEmpty: t, origins: n }) {
	let { t: r } = H(), i = l(() => Aa(e), [e]), a = e.request.scope;
	return e.diff.length === 0 && e.issues.length === 0 && t ? null : /* @__PURE__ */ h("section", {
		className: "studio-block",
		children: [
			/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
				name: "doc",
				size: 13
			}), r("plan.diff.title")] }),
			e.diff.length === 0 ? /* @__PURE__ */ m("span", {
				className: "studio-diffline",
				"data-kind": "add",
				children: a ? r("plan.diff.none", { scope: a }) : r("plan.diff.noneNoScope")
			}) : /* @__PURE__ */ m("div", {
				className: "studio-difflines",
				children: e.diff.map((e) => {
					let t = n?.[e.slug] === "advisor", i = e.to_enabled ? t ? "plan.diff.on.advisor" : "plan.diff.on" : t ? "plan.diff.off.advisor" : "plan.diff.off";
					return /* @__PURE__ */ m("span", {
						className: "studio-diffline",
						"data-kind": e.to_enabled ? "add" : "del",
						"data-origin": t ? "advisor" : void 0,
						children: r(i, { slug: e.slug })
					}, e.slug);
				})
			}),
			e.diff.length > 0 ? /* @__PURE__ */ h("div", {
				className: "studio-subblock",
				children: [/* @__PURE__ */ m("h4", { children: r("plan.diff.consequences") }), i.length === 0 ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: r("plan.diff.consequencesNone")
				}) : /* @__PURE__ */ m("ul", {
					className: "studio-crit",
					children: i.map((e) => /* @__PURE__ */ h("li", {
						"data-tone": e.gained ? "ok" : "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: e.gained ? "check" : "warn",
							size: 13
						}), /* @__PURE__ */ h("span", {
							className: "studio-crit-txt",
							children: [e.gained ? r("plan.diff.gained", { artifact: e.artifact }) : r("plan.diff.lost", { artifact: e.artifact }), /* @__PURE__ */ m("span", {
								className: "studio-crit-why",
								children: e.consumers.length ? r("plan.diff.lostConsumers", { stages: e.consumers.join(r("shell.format.listJoin")) }) : r("plan.diff.lostNoConsumers")
							})]
						})]
					}, e.artifact))
				})]
			}) : null,
			e.issues.length > 0 ? /* @__PURE__ */ h("div", {
				className: "studio-subblock",
				children: [/* @__PURE__ */ m("h4", { children: r("plan.issues.title") }), /* @__PURE__ */ m(ka, {
					issues: e.issues,
					tone: "danger"
				})]
			}) : null
		]
	});
}
//#endregion
//#region src/plan/PlanMatrix.tsx
function Ma(e) {
	let t = ea.map((e) => ({
		phase: e,
		stages: []
	})), n = [];
	for (let r of e) {
		let e = t.find((e) => e.phase === r.phase);
		e ? e.stages.push(r) : n.push(r);
	}
	let r = t.filter((e) => e.stages.length > 0);
	return n.length ? [...r, {
		phase: "",
		stages: n
	}] : r;
}
function Na({ stages: e, onToggle: t, busy: n, showState: r }) {
	let i = H(), { t: a } = i, o = l(() => Ma(e), [e]);
	return /* @__PURE__ */ m("div", {
		className: "studio-plan-matrix",
		role: "group",
		"aria-label": a("plan.matrix.a11y"),
		children: o.map((e) => {
			let o = e.stages.filter((e) => e.enabled).length, s = e.stages.filter((e) => e.enabled && e.gate).length;
			return /* @__PURE__ */ h("fieldset", {
				className: "studio-plan-phase",
				"data-phase": e.phase,
				children: [/* @__PURE__ */ h("legend", {
					className: "studio-plan-phase-h",
					children: [
						/* @__PURE__ */ m("span", { children: e.phase ? a(`enum.phase.${e.phase}`) : a("plan.phase.other") }),
						/* @__PURE__ */ m(X, { children: a("plan.phase.on", {
							on: i.fmt.number(o),
							total: i.fmt.number(e.stages.length)
						}) }),
						s > 0 ? /* @__PURE__ */ m(X, {
							icon: "gate",
							children: q(i, "plan.phase.gates", s)
						}) : null
					]
				}), /* @__PURE__ */ m("div", {
					className: "studio-plan-stages",
					children: e.stages.map((e) => /* @__PURE__ */ m(wa, {
						stage: e,
						...t ? { onToggle: t } : {},
						...n === void 0 ? {} : { busy: n },
						...r === void 0 ? {} : { showState: r }
					}, e.slug))
				})]
			}, e.phase || "other");
		})
	});
}
//#endregion
//#region src/plan/RecomposePanel.tsx
function Pa({ api: e, repoId: t, intentKey: n, intentLabel: r, onClose: a, onApplied: s }) {
	let c = H(), { t: l } = c, [f, g] = d([]), [_, v] = d([]), [y, b] = d(null), [x, S] = d(""), [C, w] = d(!1), [T, E] = d(null), [D, O] = d(!1), [k, A] = d(!1), [j, M] = d(null), N = u(0), P = i(async (r, i) => {
		let a = N.current += 1;
		w(!0);
		try {
			let o = await e.recomposePreview(t, n, {
				skip: r,
				add: i
			});
			if (a !== N.current) return;
			b(o.proposal), S(o.proposal_digest), E(null);
		} catch (e) {
			if (a !== N.current) return;
			E(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
		} finally {
			a === N.current && w(!1);
		}
	}, [
		e,
		t,
		n
	]);
	o(() => {
		P(f, _);
	}, [
		P,
		f,
		_
	]);
	let F = i((e, t) => {
		O(!1), M(null), t ? (g((t) => t.filter((t) => t !== e)), v((t) => t.includes(e) ? t : [...t, e])) : (v((t) => t.filter((t) => t !== e)), g((t) => t.includes(e) ? t : [...t, e]));
	}, []), I = i(async () => {
		if (y && y.allowed && x) {
			A(!0);
			try {
				let r = await e.recompose(t, n, {
					skip: y.skip,
					add: y.add,
					proposal_digest: x
				});
				M({
					ok: r.result.ok,
					result: r.result
				}), E(null), O(!1), g([]), v([]), s?.();
			} catch (e) {
				E(e instanceof U ? e : new U("internal_error", String(e), {}, 0)), O(!1), P(f, _);
			} finally {
				A(!1);
			}
		}
	}, [
		e,
		t,
		n,
		y,
		x,
		s,
		P,
		f,
		_
	]), L = (y?.skip.length ?? 0) + (y?.add.length ?? 0);
	return /* @__PURE__ */ h("section", {
		className: "studio-panel",
		"aria-label": l("plan.recompose.title", { intent: r }),
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-panel-head",
				children: [/* @__PURE__ */ h("h2", { children: [/* @__PURE__ */ m(Y, {
					name: "map",
					size: 15
				}), l("plan.recompose.title", { intent: r })] }), /* @__PURE__ */ h("div", {
					className: "studio-row studio-panel-actions",
					children: [/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => void P(f, _),
						disabled: C || k,
						children: [/* @__PURE__ */ m(Y, {
							name: "refresh",
							size: 13
						}), l("plan.recompose.reload")]
					}), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: a,
						children: l("plan.recompose.close")
					})]
				})]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: l("plan.recompose.lede")
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [
					y?.current_stage ? /* @__PURE__ */ m(X, {
						icon: "play",
						tone: "accent",
						mono: !0,
						children: l("plan.recompose.current", { stage: y.current_stage })
					}) : /* @__PURE__ */ m(X, {
						icon: "info",
						children: l("plan.recompose.currentUnknown")
					}),
					y && y.skip.length > 0 ? /* @__PURE__ */ m(X, {
						icon: "warn",
						tone: "warn",
						children: q(c, "plan.recompose.skip", y.skip.length)
					}) : null,
					y && y.add.length > 0 ? /* @__PURE__ */ m(X, {
						icon: "plus",
						tone: "ok",
						children: q(c, "plan.recompose.add", y.add.length)
					}) : null,
					C ? /* @__PURE__ */ m("span", {
						className: "studio-muted",
						children: l("plan.busy")
					}) : null
				]
			}),
			T ? /* @__PURE__ */ h("p", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ h("span", {
					className: "studio-grow",
					children: [
						l("plan.recompose.error"),
						" ",
						T.known ? l(`errors.${T.code}`) : T.message
					]
				})]
			}) : null,
			j ? /* @__PURE__ */ h("p", {
				className: "studio-banner",
				"data-tone": j.ok ? "ok" : "warn",
				role: "status",
				children: [/* @__PURE__ */ m(Y, {
					name: j.ok ? "check" : "warn",
					size: 15
				}), /* @__PURE__ */ m("span", {
					className: "studio-grow",
					children: j.ok ? l("plan.recompose.applied") : l("plan.recompose.appliedFailed")
				})]
			}) : null,
			y ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m(Na, {
					stages: y.plan.stages,
					onToggle: F,
					busy: C || k,
					showState: !0
				}),
				y.refusals.length > 0 ? /* @__PURE__ */ h("section", {
					className: "studio-block",
					children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}), l("plan.recompose.refused")] }), /* @__PURE__ */ m(ka, {
						issues: y.refusals,
						tone: "danger"
					})]
				}) : null,
				/* @__PURE__ */ m(ja, {
					plan: y.plan,
					hideEmpty: !0
				}),
				/* @__PURE__ */ m(Da, { plan: y.plan }),
				L === 0 ? /* @__PURE__ */ h("p", {
					className: "studio-consequence",
					children: [/* @__PURE__ */ m(Y, {
						name: "info",
						size: 13
					}), /* @__PURE__ */ m("span", { children: l("plan.recompose.none") })]
				}) : null,
				D ? /* @__PURE__ */ h("div", {
					className: "studio-confirm",
					role: "group",
					"aria-label": l("plan.recompose.confirmTitle"),
					children: [
						/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
							name: "lock",
							size: 13
						}), l("plan.recompose.confirmTitle")] }),
						/* @__PURE__ */ m("p", { children: l("plan.recompose.confirmBody") }),
						/* @__PURE__ */ m("p", {
							className: "studio-stat-k",
							children: l("plan.recompose.argv")
						}),
						/* @__PURE__ */ m("pre", {
							className: "studio-sendtext",
							children: y.argv_preview.join(" ")
						}),
						/* @__PURE__ */ h("div", {
							className: "studio-row",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-primary",
								onClick: () => void I(),
								disabled: k,
								children: [/* @__PURE__ */ m(Y, {
									name: "check",
									size: 13
								}), l("plan.recompose.confirmGo")]
							}), /* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								onClick: () => O(!1),
								disabled: k,
								children: l("common.cancel")
							})]
						})
					]
				}) : /* @__PURE__ */ h("div", {
					className: "studio-row studio-wrap",
					children: [/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-primary",
						disabled: !y.allowed || C || k,
						onClick: () => O(!0),
						children: [/* @__PURE__ */ m(Y, {
							name: "check",
							size: 13
						}), l("plan.recompose.apply")]
					}), L > 0 ? /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => {
							g([]), v([]);
						},
						disabled: C || k,
						children: l("plan.recompose.reset")
					}) : null]
				})
			] }) : C ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: l("common.loading")
			}) : null
		]
	});
}
//#endregion
//#region src/plan/IntentSettingsPanel.tsx
var Fa = [
	"Minimal",
	"Standard",
	"Comprehensive"
];
function Ia(e) {
	return /* @__PURE__ */ m(La, { ...e }, `${e.repoId}:${e.intentKey}`);
}
function La({ api: e, repoId: t, intentKey: n, intentLabel: r, onApplied: i, onClose: a }) {
	let { t: s } = H(), [c, l] = d(null), [f, p] = d([]), [g, _] = d(null), [v, y] = d(!0), [b, x] = d(!1), [S, C] = d(!1), [w, T] = d(!1), [E, D] = d(null), O = u(0), k = u(!0), A = u(null);
	o(() => {
		S && A.current?.focus();
	}, [S]);
	async function j(r, i = !1) {
		let a = ++O.current;
		y(!0), _(null), C(!1), D(null);
		try {
			let o = await e.intentSettingsPreview(t, n, r);
			if (!k.current || O.current !== a) return;
			_(o), p(o.proposal.scopes), i && l(o.proposal.after);
		} catch (e) {
			k.current && O.current === a && D(W(e));
		} finally {
			k.current && O.current === a && y(!1);
		}
	}
	o(() => (k.current = !0, j({}, !0), () => {
		k.current = !1, O.current += 1;
	}), [
		e,
		t,
		n
	]);
	function M(e, t) {
		c && (O.current += 1, l({
			...c,
			[e]: t
		}), _(null), C(!1), T(!1), D(null), y(!1));
	}
	async function N() {
		if (!g?.proposal.allowed || b) return;
		let r = g;
		x(!0), D(null);
		try {
			let a = await e.changeIntentSettings(t, n, {
				...r.proposal.after,
				proposal_digest: r.proposal_digest
			});
			if (!k.current) return;
			l(a.verified.settings), T(!0), i?.();
		} catch (e) {
			k.current && D(W(e));
		} finally {
			k.current && (x(!1), C(!1), _(null));
		}
	}
	let P = g?.proposal, F = P?.stages.filter((e) => e.before !== e.after) ?? [], I = E ? E.known ? s(`errors.${E.code}`) : E.message : "";
	return /* @__PURE__ */ h("section", {
		className: "studio-panel studio-workspace-controls",
		"aria-label": s("workspace.settings.title"),
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-panel-head",
				children: [/* @__PURE__ */ h("h2", { children: [s("workspace.settings.title"), r ? ` · ${r}` : ""] }), a ? /* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-btn",
					disabled: b,
					onClick: a,
					children: s("common.close")
				}) : null]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: s("workspace.settings.description")
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-workspace-fields",
				children: [
					/* @__PURE__ */ h("label", {
						className: "studio-field",
						children: [/* @__PURE__ */ m("span", {
							className: "studio-field-label",
							children: s("workspace.settings.scope")
						}), /* @__PURE__ */ h("select", {
							className: "studio-input",
							value: c?.scope ?? "",
							disabled: !c || b,
							onChange: (e) => M("scope", e.target.value),
							children: [c ? null : /* @__PURE__ */ m("option", {
								value: "",
								children: s("common.loading")
							}), f.map((e) => /* @__PURE__ */ m("option", {
								value: e,
								children: e
							}, e))]
						})]
					}),
					["depth", "test_strategy"].map((e) => /* @__PURE__ */ h("label", {
						className: "studio-field",
						children: [/* @__PURE__ */ m("span", {
							className: "studio-field-label",
							children: s(`workspace.settings.${e}`)
						}), /* @__PURE__ */ h("select", {
							className: "studio-input",
							value: c?.[e] ?? "",
							disabled: !c || b,
							onChange: (t) => M(e, t.target.value),
							children: [c ? null : /* @__PURE__ */ m("option", {
								value: "",
								children: s("common.loading")
							}), Fa.map((e) => /* @__PURE__ */ m("option", {
								value: e,
								children: s(`workspace.depth.${e}`)
							}, e))]
						})]
					}, e)),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: v || b,
						onClick: () => void j(c ?? {}, !c),
						children: s("workspace.settings.preview")
					})
				]
			}),
			v ? /* @__PURE__ */ m("p", {
				role: "status",
				children: s("common.loading")
			}) : null,
			E ? /* @__PURE__ */ m("p", {
				className: "studio-failure-detail",
				role: "alert",
				children: I
			}) : null,
			w ? /* @__PURE__ */ m("p", {
				role: "status",
				children: s("workspace.settings.applied")
			}) : null,
			P && !v ? /* @__PURE__ */ h("div", {
				className: "studio-workspace-preview",
				children: [
					/* @__PURE__ */ m("p", {
						className: "studio-wrap-any",
						children: s("workspace.settings.selection", {
							space: P.selection.space,
							intent: P.selection.intent_dir
						})
					}),
					/* @__PURE__ */ m("dl", {
						className: "studio-facts",
						children: [
							"scope",
							"depth",
							"test_strategy"
						].map((e) => /* @__PURE__ */ h("div", {
							className: "studio-fact",
							children: [/* @__PURE__ */ m("dt", { children: s(`workspace.settings.${e}`) }), /* @__PURE__ */ h("dd", { children: [
								P.before[e],
								" → ",
								P.after[e]
							] })]
						}, e))
					}),
					/* @__PURE__ */ m("p", { children: s("workspace.settings.preserved", { n: P.stages.filter((e) => e.state === "completed").length }) }),
					F.length ? /* @__PURE__ */ m("div", {
						className: "studio-workspace-table",
						children: /* @__PURE__ */ h("table", {
							className: "studio-tbl",
							children: [
								/* @__PURE__ */ m("caption", { children: s("workspace.settings.stageChanges") }),
								/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
									/* @__PURE__ */ m("th", { children: s("workspace.settings.stage") }),
									/* @__PURE__ */ m("th", { children: s("workspace.settings.before") }),
									/* @__PURE__ */ m("th", { children: s("workspace.settings.after") })
								] }) }),
								/* @__PURE__ */ m("tbody", { children: F.map((e) => /* @__PURE__ */ h("tr", { children: [
									/* @__PURE__ */ m("th", {
										scope: "row",
										className: "studio-mono studio-wrap-any",
										children: e.slug
									}),
									/* @__PURE__ */ m("td", { children: s(e.before ? "workspace.settings.execute" : "workspace.settings.skip") }),
									/* @__PURE__ */ m("td", { children: s(e.after ? "workspace.settings.execute" : "workspace.settings.skip") })
								] }, e.slug)) })
							]
						})
					}) : null,
					P.refusals.map((e) => /* @__PURE__ */ m("p", {
						role: "status",
						children: s(`workspace.refusal.${e}`)
					}, e)),
					/* @__PURE__ */ h("details", { children: [/* @__PURE__ */ m("summary", { children: s("workspace.settings.command") }), /* @__PURE__ */ m("pre", {
						className: "studio-sendtext",
						children: JSON.stringify(P.argv_preview, null, 2)
					})] }),
					S ? /* @__PURE__ */ h("fieldset", {
						className: "studio-confirm",
						disabled: b,
						children: [
							/* @__PURE__ */ m("legend", {
								className: "studio-field-label",
								children: s("workspace.settings.confirmTitle")
							}),
							/* @__PURE__ */ m("p", { children: s("workspace.settings.confirmBody") }),
							/* @__PURE__ */ h("div", {
								className: "studio-confirm-actions",
								children: [/* @__PURE__ */ m("button", {
									ref: A,
									type: "button",
									className: "studio-btn",
									"data-variant": "primary",
									onClick: () => void N(),
									children: s("workspace.settings.apply")
								}), /* @__PURE__ */ m("button", {
									type: "button",
									className: "studio-btn",
									onClick: () => C(!1),
									children: s("common.cancel")
								})]
							})
						]
					}) : /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						"data-variant": "primary",
						disabled: !P.allowed || b,
						onClick: () => C(!0),
						children: s("workspace.settings.review")
					})
				]
			}) : null
		]
	});
}
//#endregion
//#region src/intents/IntentActions.tsx
function Ra({ api: e, intent: t, onGo: n, onQueued: r, onChanged: a, onRecompose: s, onSession: c, onSettings: l }) {
	let f = H(), { t: p } = f, [g, _] = d(null), [v, y] = d(!1), [b, x] = d(null), S = u(null);
	o(() => {
		g && S.current?.focus();
	}, [g]);
	let C = i((e) => {
		x(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
	}, []), w = i(async (n) => {
		y(!0), x(null);
		try {
			r((n === "run" ? await e.run(t.repo_id, t.intent_key) : await e.resume(t.repo_id, t.intent_key)).action_id, t), a();
		} catch (e) {
			C(e);
		} finally {
			y(!1);
		}
	}, [
		e,
		t,
		r,
		a,
		C
	]), T = i(async () => {
		if (g) {
			y(!0), x(null);
			try {
				if (g === "pause" || g === "unpause") {
					let n = await e.pause(t.repo_id, t.intent_key, g === "pause");
					a(g === "pause" ? q(f, "intents.paused.done", n.blocked_actions.length) : p("intents.unpaused.done"));
				} else g === "archive" ? (await e.archiveIntent(t.repo_id, t.intent_key), a(p("intents.archived.done"))) : (await e.restoreIntent(t.repo_id, t.intent_key), a(p("intents.restored.done")));
				_(null);
			} catch (e) {
				C(e);
			} finally {
				y(!1);
			}
		}
	}, [
		e,
		t,
		g,
		a,
		C,
		f,
		p
	]), E = t.archived ? p("intents.run.disabledArchived") : t.paused ? p("intents.run.disabledPaused") : t.session?.running ? p("intents.run.disabledRunning") : t.counts.awaiting_approval > 0 || t.operational_state === "WaitingForYou" ? p("intents.run.disabledCheckpoint") : t.disk.status?.toLowerCase() === "completed" ? p("intents.run.disabledCompleted") : null, D = !!t.session?.slot_key, O = !!t.disk.parked_at, k = t.disk.status === "Running" && !t.archived, A = k ? null : p("plan.issue.recompose_not_allowed", {
		required_status: "Running",
		status: t.disk.status ?? p("common.unavailable")
	});
	return /* @__PURE__ */ h("div", {
		className: "studio-col studio-intent-actions",
		role: "group",
		"aria-label": p("intents.action.a11y", { intent: t.intent_dir }),
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [
					O ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v || E !== null,
						...E ? { title: E } : {},
						onClick: () => void w("resume"),
						children: [/* @__PURE__ */ m(Y, {
							name: "play",
							size: 13
						}), p("intents.action.resume")]
					}) : /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v || E !== null,
						...E ? { title: E } : {},
						onClick: () => void w("run"),
						children: [/* @__PURE__ */ m(Y, {
							name: "play",
							size: 13
						}), p("intents.action.run")]
					}),
					/* @__PURE__ */ h("button", {
						type: "button",
						className: `studio-btn studio-btn-sm${D ? "" : " studio-btn-primary"}`,
						disabled: v,
						onClick: () => c(t),
						children: [/* @__PURE__ */ m(Y, {
							name: "link",
							size: 13
						}), p(D ? "intents.action.session" : "intents.action.bindSession")]
					}),
					t.open_actions > 0 ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						onClick: () => n({
							view: "actions",
							repo: t.repo_id,
							space: t.space,
							intent: t.intent_key
						}),
						children: [/* @__PURE__ */ m(Y, {
							name: "inbox",
							size: 13
						}), p("intents.action.openAction")]
					}) : null,
					/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						onClick: () => n({
							view: "map",
							repo: t.repo_id,
							space: t.space,
							intent: t.intent_key
						}),
						children: [/* @__PURE__ */ m(Y, {
							name: "map",
							size: 13
						}), p("intents.action.details")]
					})
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [
					/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v || !k,
						...A ? { title: A } : {},
						onClick: () => s(t),
						children: [/* @__PURE__ */ m(Y, {
							name: "doc",
							size: 13
						}), p("intents.action.recompose")]
					}),
					l ? /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v || t.archived,
						onClick: () => l(t),
						children: p("workspace.settings.title")
					}) : null,
					/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v || t.archived,
						onClick: () => _(t.paused ? "unpause" : "pause"),
						children: [/* @__PURE__ */ m(Y, {
							name: t.paused ? "play" : "pause",
							size: 13
						}), t.paused ? p("intents.action.unpause") : p("intents.action.pause")]
					}),
					/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						disabled: v,
						onClick: () => _(t.archived ? "restore" : "archive"),
						children: [/* @__PURE__ */ m(Y, {
							name: "doc",
							size: 13
						}), t.archived ? p("intents.action.restore") : p("intents.action.archive")]
					})
				]
			}),
			v ? /* @__PURE__ */ m("span", {
				className: "studio-muted",
				role: "status",
				children: p("intents.busy")
			}) : null,
			b ? /* @__PURE__ */ h("p", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}), /* @__PURE__ */ m("span", {
					className: "studio-grow",
					children: b.known ? p(`errors.${b.code}`) : b.message
				})]
			}) : null,
			g ? /* @__PURE__ */ h("div", {
				className: "studio-confirm",
				role: "group",
				"aria-label": p(`intents.confirm.${g}.title`),
				onKeyDown: (e) => {
					e.key === "Escape" && _(null);
				},
				children: [
					/* @__PURE__ */ m("h3", { children: p(`intents.confirm.${g}.title`) }),
					/* @__PURE__ */ m("p", { children: p(`intents.confirm.${g}.body`) }),
					g === "pause" && t.open_actions > 0 ? /* @__PURE__ */ h("p", {
						className: "studio-consequence",
						"data-tone": "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}), /* @__PURE__ */ m("span", { children: q(f, "intents.confirm.pause.blocked", t.open_actions) })]
					}) : null,
					/* @__PURE__ */ h("div", {
						className: "studio-row",
						children: [/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-primary studio-btn-sm",
							ref: S,
							disabled: v,
							onClick: () => void T(),
							children: p("intents.confirm.go")
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							disabled: v,
							onClick: () => _(null),
							children: p("common.cancel")
						})]
					})
				]
			}) : null
		]
	});
}
//#endregion
//#region src/intents/IntentRow.tsx
var za = {
	Idle: {
		tone: "neutral",
		icon: "clock"
	},
	Queued: {
		tone: "neutral",
		icon: "clock"
	},
	Running: {
		tone: "ok",
		icon: "play"
	},
	WaitingForYou: {
		tone: "accent",
		icon: "gate"
	},
	Paused: {
		tone: "neutral",
		icon: "pause"
	},
	Parked: {
		tone: "neutral",
		icon: "pause"
	},
	Interrupted: {
		tone: "warn",
		icon: "warn"
	},
	ReconciliationRequired: {
		tone: "danger",
		icon: "recovery"
	},
	RetryEligible: {
		tone: "warn",
		icon: "refresh"
	},
	CircuitOpen: {
		tone: "warn",
		icon: "fail"
	},
	Failed: {
		tone: "danger",
		icon: "fail"
	},
	Completed: {
		tone: "ok",
		icon: "check"
	},
	Archived: {
		tone: "neutral",
		icon: "doc"
	}
};
function Ba(e) {
	return za[e] ?? {
		tone: "neutral",
		icon: "clock"
	};
}
function Va(e, t) {
	return t.disk.current_stage || e.t("intents.stage.none");
}
function Ha(e, t) {
	let n = t.open_actions > 0 ? q(e, "intents.openActions", t.open_actions) : "";
	return e.t("intents.a11y.row", {
		intent: t.intent_dir,
		repo: t.repo_label,
		state: e.t(`enum.intentState.${t.operational_state}`),
		stage: Va(e, t),
		waiting: e.t("intents.waiting", { duration: Ee(e, t.last_activity_at) }),
		primary: n
	});
}
function Ua({ intent: e, layout: t, actions: n }) {
	let r = H(), { t: i } = r, a = Ba(e.operational_state), o = /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m("span", {
			className: "studio-strong studio-mono studio-wrap-any",
			children: e.intent_dir
		}),
		e.title ? /* @__PURE__ */ m("span", {
			className: "studio-row-sub",
			children: e.title
		}) : null,
		/* @__PURE__ */ m("span", {
			className: "studio-sr",
			children: Ha(r, e)
		}),
		/* @__PURE__ */ h("span", {
			className: "studio-row studio-wrap studio-row-chips",
			children: [
				e.space === "default" ? null : /* @__PURE__ */ m(X, {
					mono: !0,
					children: e.space
				}),
				e.is_active_cursor ? /* @__PURE__ */ m(X, {
					icon: "intent",
					tone: "accent",
					children: i("intents.cursorChip")
				}) : null,
				e.open_actions > 0 ? /* @__PURE__ */ m(X, {
					icon: "gate",
					tone: "accent",
					children: q(r, "intents.openActions", e.open_actions)
				}) : null,
				e.blocking_findings > 0 ? /* @__PURE__ */ m(X, {
					icon: "warn",
					tone: "danger",
					children: q(r, "intents.blocking", e.blocking_findings)
				}) : null,
				e.warn_findings > 0 ? /* @__PURE__ */ m(X, {
					icon: "warn",
					tone: "warn",
					children: q(r, "intents.warn", e.warn_findings)
				}) : null,
				e.paused ? /* @__PURE__ */ m(X, {
					icon: "pause",
					children: i("intents.pausedChip")
				}) : null,
				e.archived ? /* @__PURE__ */ m(X, {
					icon: "doc",
					children: i("intents.archivedChip")
				}) : null,
				e.unstable ? /* @__PURE__ */ m(X, {
					icon: "warn",
					tone: "warn",
					title: i("intents.unstableWhy"),
					children: i("intents.unstable")
				}) : null
			]
		})
	] }), s = /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(X, {
		tone: a.tone,
		icon: a.icon,
		children: i(`enum.intentState.${e.operational_state}`)
	}), /* @__PURE__ */ m("span", {
		className: "studio-row-sub studio-mono",
		children: i("intents.waiting", { duration: Ee(r, e.last_activity_at) })
	})] }), c = /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m("span", {
			className: "studio-mono studio-wrap-any",
			children: Va(r, e)
		}),
		/* @__PURE__ */ m("span", {
			className: "studio-row-sub",
			children: i("intents.stage.progress", {
				done: we(r, e.counts.done),
				total: we(r, e.counts.total)
			})
		}),
		e.disk.next_stage ? /* @__PURE__ */ m("span", {
			className: "studio-row-sub studio-mono",
			children: i("intents.stage.next", { stage: e.disk.next_stage })
		}) : null
	] }), l = /* @__PURE__ */ m(X, {
		icon: "warn",
		tone: "warn",
		title: i("intents.keepMoving.why"),
		children: i("intents.keepMoving.unavailable")
	});
	return t === "card" ? /* @__PURE__ */ h("li", {
		className: "studio-intent-card",
		"data-state": e.operational_state,
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-intent-card-head",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-mono studio-muted",
					children: e.repo_label
				}), s]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-col",
				children: o
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-col",
				children: c
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-row-sub",
					children: i("intents.col.keepMoving")
				}), l]
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-row studio-wrap",
				children: n
			})
		]
	}) : /* @__PURE__ */ h("tr", {
		"data-state": e.operational_state,
		children: [
			/* @__PURE__ */ m("td", {
				className: "studio-mono",
				children: e.repo_label
			}),
			/* @__PURE__ */ m("td", { children: o }),
			/* @__PURE__ */ m("td", { children: s }),
			/* @__PURE__ */ m("td", { children: c }),
			/* @__PURE__ */ m("td", { children: l }),
			/* @__PURE__ */ m("td", { children: n })
		]
	});
}
function Wa({ repo: e, message: t, layout: n }) {
	let { t: r } = H(), i = r("intents.error.repo", {
		repo: e,
		reason: t
	});
	return n === "card" ? /* @__PURE__ */ m("li", {
		className: "studio-intent-card",
		"data-tone": "warn",
		children: /* @__PURE__ */ h("span", {
			className: "studio-row",
			children: [/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 13
			}), i]
		})
	}) : /* @__PURE__ */ m("tr", {
		"data-tone": "warn",
		children: /* @__PURE__ */ m("td", {
			colSpan: 6,
			children: /* @__PURE__ */ h("span", {
				className: "studio-row",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}), i]
			})
		})
	});
}
//#endregion
//#region src/intents/IntentList.tsx
var Ga = [
	"ReconciliationRequired",
	"WaitingForYou",
	"CircuitOpen",
	"Failed",
	"RetryEligible",
	"Interrupted",
	"Running",
	"Queued",
	"Parked",
	"Paused",
	"Idle",
	"Completed",
	"Archived"
];
function Ka(e) {
	let t = Ga.indexOf(e);
	return t < 0 ? Ga.length : t;
}
function qa(e, t) {
	return [...e].sort((e, n) => t.fmt.compare(e.repo_label, n.repo_label) || Ka(e.operational_state) - Ka(n.operational_state) || t.fmt.compare(e.intent_dir, n.intent_dir));
}
function Ja({ intents: e, failures: t, narrow: n, renderActions: r }) {
	let i = H(), { t: a } = i, o = l(() => qa(e, i), [e, i]);
	return n ? /* @__PURE__ */ h("ul", {
		className: "studio-intent-cards",
		"aria-label": a("intents.a11y.list"),
		children: [t.map((e) => /* @__PURE__ */ m(Wa, {
			repo: e.repo,
			message: e.message,
			layout: "card"
		}, e.repo)), o.map((e) => /* @__PURE__ */ m(Ua, {
			intent: e,
			layout: "card",
			actions: r(e)
		}, `${e.repo_id}/${e.intent_key}`))]
	}) : /* @__PURE__ */ h("table", {
		className: "studio-tbl studio-intent-table",
		children: [
			/* @__PURE__ */ m("caption", {
				className: "studio-sr",
				children: a("intents.a11y.table")
			}),
			/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.repo")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.intent")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.state")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.stage")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.keepMoving")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: a("intents.col.actions")
				})
			] }) }),
			/* @__PURE__ */ h("tbody", { children: [t.map((e) => /* @__PURE__ */ m(Wa, {
				repo: e.repo,
				message: e.message,
				layout: "table"
			}, e.repo)), o.map((e) => /* @__PURE__ */ m(Ua, {
				intent: e,
				layout: "table",
				actions: r(e)
			}, `${e.repo_id}/${e.intent_key}`))] })
		]
	});
}
//#endregion
//#region src/intents/SessionPanel.tsx
var Ya = "aidlc", Xa = 4, Za = [
	"intents.session.step.create",
	"intents.session.step.title",
	"intents.session.step.project",
	"intents.session.step.bind"
];
function Qa(e, t) {
	return `aidlc-studio-${e}-${t}`;
}
function $a(e, t) {
	let n = (e) => e.replace(/\/+$/, "");
	return n(e) === n(t);
}
function eo(e, t) {
	return e.filter((e) => e.agent === Ya && !!e.project && $a(e.project, t));
}
function to(e) {
	return !e || !e.slot_key ? null : {
		slotKey: e.slot_key,
		sessionKey: e.session_key,
		running: e.running
	};
}
function no({ api: e, repo: t, intent: n, onClose: r, onChanged: a }) {
	let o = H(), { t: s } = o, [c, l] = d(null), [u, f] = d(0), [g, _] = d(0), [v, y] = d(null), [b, x] = d(null), [S, C] = d(null), [w, T] = d(null), E = w ? w.value : to(n.session), D = Qa(n.repo_id, n.intent_dir), O = (e) => s(Za[e - 1] ?? Za[0]), k = i((e, t) => {
		_(t), y(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
	}, []), A = i((e, t) => {
		T({ value: e.slot_key ? {
			slotKey: e.slot_key,
			sessionKey: e.session_key ?? t?.session_key ?? "",
			running: t?.running ?? !1
		} : null });
	}, []), j = i((e) => {
		l(e), y(null), _(0);
	}, []), M = i(async () => {
		j("create");
		let r = 1;
		f(r);
		try {
			let i = (await e.createSlot(D, Ya)).key, o = typeof i == "string" && i ? i : D;
			r = 2, f(r), await e.setSlotTitle(o, `${t.label} / ${n.slug || n.intent_dir}`), r = 3, f(r), await e.setSlotProject(o, t.canonical_path), r = 4, f(r);
			let c = await e.bindSession(n.repo_id, n.intent_key, o);
			A(c.binding, c.slot), x(null), a(s("intents.session.done.bound", { slot: c.binding.slot_key ?? o }));
		} catch (e) {
			k(e, r);
		} finally {
			l(null), f(0);
		}
	}, [
		e,
		D,
		t,
		n,
		A,
		a,
		s,
		k,
		j
	]), N = i(async () => {
		j("list");
		try {
			x(await e.listSlots());
		} catch (e) {
			k(e, 0);
		} finally {
			l(null);
		}
	}, [
		e,
		k,
		j
	]), P = i(async (t) => {
		j("adopt");
		try {
			let r = await e.bindSession(n.repo_id, n.intent_key, t);
			A(r.binding, r.slot), x(null), a(s("intents.session.done.bound", { slot: r.binding.slot_key ?? t }));
		} catch (e) {
			k(e, Xa);
		} finally {
			l(null);
		}
	}, [
		e,
		n,
		A,
		a,
		s,
		k,
		j
	]), F = i(async () => {
		j("unbind");
		try {
			let t = await e.unbindSession(n.repo_id, n.intent_key);
			A(t.binding), C(null), a(s("intents.session.done.unbound"));
		} catch (e) {
			k(e, 0);
		} finally {
			l(null);
		}
	}, [
		e,
		n,
		A,
		a,
		s,
		k,
		j
	]), I = i(async () => {
		j("candidates");
		try {
			let t = await e.takeoverPreview(n.repo_id, n.intent_key);
			C(t.candidates);
		} catch (e) {
			k(e, 0);
		} finally {
			l(null);
		}
	}, [
		e,
		n,
		k,
		j
	]), L = i(async (t) => {
		j("move");
		try {
			let r = await e.takeover(n.repo_id, n.intent_key, t);
			A(r.binding), C(null), a(s("intents.session.done.moved", { slot: r.binding.slot_key ?? t }));
		} catch (e) {
			k(e, 0);
		} finally {
			l(null);
		}
	}, [
		e,
		n,
		A,
		a,
		s,
		k,
		j
	]), R = b ? eo(b, t.canonical_path) : null, ee = v && v.known ? s(`errors.${v.code}`) : null;
	return /* @__PURE__ */ h("section", {
		className: "studio-panel",
		"aria-label": s("intents.session.a11y", { intent: n.intent_dir }),
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-panel-head",
				children: [/* @__PURE__ */ h("h2", { children: [/* @__PURE__ */ m(Y, {
					name: "link",
					size: 15
				}), s("intents.session.title", { intent: n.intent_dir })] }), /* @__PURE__ */ m("div", {
					className: "studio-row studio-panel-actions",
					children: /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: r,
						children: s("common.close")
					})
				})]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: E ? s("intents.session.boundLede") : s("intents.session.lede", { agent: Ya })
			}),
			E ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m(ro, { rows: [
					{
						key: "slot",
						label: s("intents.session.fact.slot"),
						value: E.slotKey,
						mono: !0
					},
					{
						key: "session",
						label: s("intents.session.fact.session"),
						value: E.sessionKey,
						mono: !0
					},
					{
						key: "project",
						label: s("intents.session.fact.project"),
						value: t.canonical_path,
						mono: !0
					},
					{
						key: "state",
						label: s("intents.session.fact.state"),
						value: /* @__PURE__ */ m(X, {
							icon: E.running ? "play" : "clock",
							tone: E.running ? "ok" : "neutral",
							children: E.running ? s("intents.session.running") : s("intents.session.idle")
						})
					}
				] }),
				/* @__PURE__ */ h("div", {
					className: "studio-row studio-wrap",
					children: [/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						disabled: c !== null,
						onClick: () => void F(),
						children: [/* @__PURE__ */ m(Y, {
							name: "close",
							size: 13
						}), s("intents.session.unbind")]
					}), /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						disabled: c !== null,
						onClick: () => void I(),
						children: [/* @__PURE__ */ m(Y, {
							name: "refresh",
							size: 13
						}), s("intents.session.move")]
					})]
				}),
				/* @__PURE__ */ h("p", {
					className: "studio-consequence",
					children: [/* @__PURE__ */ m(Y, {
						name: "info",
						size: 13
					}), /* @__PURE__ */ m("span", { children: s("intents.session.unbindWhy") })]
				})
			] }) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(ro, { rows: [
				{
					key: "repo",
					label: s("intents.session.fact.repo"),
					value: t.label
				},
				{
					key: "project",
					label: s("intents.session.fact.project"),
					value: t.canonical_path,
					mono: !0
				},
				{
					key: "agent",
					label: s("intents.session.fact.agent"),
					value: Ya,
					mono: !0
				},
				{
					key: "slot",
					label: s("intents.session.fact.willCreate"),
					value: D,
					mono: !0
				}
			] }), /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [/* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-btn-primary",
					disabled: c !== null,
					onClick: () => void M(),
					children: [/* @__PURE__ */ m(Y, {
						name: "plus",
						size: 13
					}), s("intents.session.create")]
				}), /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					disabled: c !== null,
					onClick: () => void N(),
					children: [/* @__PURE__ */ m(Y, {
						name: "link",
						size: 13
					}), s("intents.session.adopt")]
				})]
			})] }),
			c === "create" && u > 0 ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				role: "status",
				children: s("intents.session.busy", {
					step: u,
					total: Xa,
					what: O(u)
				})
			}) : c === null ? null : /* @__PURE__ */ m("p", {
				className: "studio-muted",
				role: "status",
				children: s("intents.busy")
			}),
			v ? /* @__PURE__ */ h("p", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ h("span", {
					className: "studio-grow",
					children: [g > 0 ? `${s("intents.session.failedAt", {
						step: g,
						total: Xa,
						what: O(g)
					})} ` : "", ee ?? v.message]
				})]
			}) : null,
			v && ee && v.message && v.message !== ee ? /* @__PURE__ */ h("p", {
				className: "studio-failure-detail",
				role: "status",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "info",
						size: 13
					}),
					" ",
					/* @__PURE__ */ h("span", {
						className: "studio-mono studio-wrap-any",
						children: [
							s("intents.session.serverSaid"),
							" ",
							v.message
						]
					})
				]
			}) : null,
			R ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
						name: "link",
						size: 13
					}), s("intents.session.adoptTitle")] }),
					/* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: s("intents.session.adoptLede", { agent: Ya })
					}),
					R.length === 0 ? /* @__PURE__ */ h("p", {
						className: "studio-consequence",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}), /* @__PURE__ */ m("span", { children: s("intents.session.adoptNone", { agent: Ya }) })]
					}) : /* @__PURE__ */ m("ul", {
						className: "studio-choicelist",
						children: R.map((e) => /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ h("span", {
							className: "studio-choice-body studio-grow",
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-choice-label studio-mono studio-wrap-any",
									children: e.key
								}),
								e.title ? /* @__PURE__ */ m("span", {
									className: "studio-choice-desc",
									children: e.title
								}) : null,
								/* @__PURE__ */ m("span", {
									className: "studio-choice-desc studio-mono studio-wrap-any",
									children: e.project
								})
							]
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							disabled: c !== null,
							onClick: () => void P(e.key),
							children: s("intents.session.adoptGo")
						})] }, e.key))
					})
				]
			}) : null,
			S ? /* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
						name: "refresh",
						size: 13
					}), s("intents.session.moveTitle")] }),
					/* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: s("intents.session.moveLede")
					}),
					S.length === 0 ? /* @__PURE__ */ h("p", {
						className: "studio-consequence",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}), /* @__PURE__ */ m("span", { children: s("intents.session.moveNone") })]
					}) : /* @__PURE__ */ m("ul", {
						className: "studio-choicelist",
						children: S.map((e) => /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ h("span", {
							className: "studio-choice-body studio-grow",
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-choice-label studio-mono studio-wrap-any",
									children: e.slot.key
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-choice-desc",
									children: o.has(`intents.session.reason.${e.reason}`) ? s(`intents.session.reason.${e.reason}`) : e.reason
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-choice-desc studio-mono studio-wrap-any",
									children: e.slot.project
								})
							]
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							disabled: c !== null,
							onClick: () => void L(e.slot.key),
							children: s("intents.session.moveGo")
						})] }, e.slot.key))
					})
				]
			}) : null
		]
	});
}
function ro({ rows: e }) {
	let t = H();
	return /* @__PURE__ */ m("dl", {
		className: "studio-facts",
		children: e.map((e) => /* @__PURE__ */ h("div", {
			className: "studio-fact",
			children: [/* @__PURE__ */ m("dt", { children: e.label }), /* @__PURE__ */ m("dd", {
				className: e.mono ? "studio-mono studio-wrap-any" : void 0,
				children: e.value === null || e.value === void 0 || e.value === "" ? G(t) : e.value
			})]
		}, e.key))
	});
}
//#endregion
//#region src/intents/IntentsView.tsx
var io = {
	space: "",
	state: "",
	q: "",
	archived: !1
};
async function ao(e, t, n, r, i) {
	let a = await Promise.allSettled(t.map((t) => e.intents(t.repo_id, {
		...n.space ? { space: n.space } : {},
		...n.state ? { state: n.state } : {},
		...n.q ? { q: n.q } : {},
		...n.archived ? { include_archived: !0 } : {}
	}, { signal: r }))), o = [], s = /* @__PURE__ */ new Set(), c = [];
	return a.forEach((e, n) => {
		let r = t[n];
		if (r) {
			if (e.status === "fulfilled") {
				o.push(...e.value.intents);
				for (let t of e.value.spaces) s.add(t);
			} else {
				let t = e.reason;
				c.push({
					repo: r.label,
					message: t instanceof U ? i(t) : String(t)
				});
			}
		}
	}), {
		intents: o,
		spaces: [...s].sort(),
		failures: c
	};
}
function oo({ route: e, go: t }) {
	let n = H(), { t: r } = n, { api: a, repos: s } = jl(), c = re(), [u, f] = d(io), [p, g] = d(""), [_, v] = d(null), [y, b] = d(null), [x, S] = d(null), [C, w] = d(null);
	o(() => {
		w(null);
	}, [e.repo]), o(() => {
		let e = setTimeout(() => f((e) => e.q === p ? e : {
			...e,
			q: p
		}), 300);
		return () => clearTimeout(e);
	}, [p]);
	let T = s.data?.repos ?? [], E = l(() => e.repo ? T.filter((t) => t.repo_id === e.repo) : T, [T, e.repo]), D = i((e) => e.known ? r(`errors.${e.code}`) : e.message, [r]), O = J(E.length ? `intents:${E.map((e) => e.repo_id).join("+")}:${u.space}:${u.state}:${u.q}:${+!!u.archived}` : null, i((e) => ao(a, E, u, e, D), [
		a,
		E,
		u,
		D
	]), { busy: (e) => e.intents.some((e) => e.operational_state === "Running" || e.session?.running === !0) }), k = O.data, A = k?.intents ?? [], j = T.reduce((e, t) => e + t.counts.intents, 0), M = l(() => Xi.filter((e) => A.some((t) => t.operational_state === e)), [A]), N = u.space !== "" || u.state !== "" || u.q !== "" || u.archived, P = O.refresh, F = i((e) => /* @__PURE__ */ m(Ra, {
		api: a,
		intent: e,
		onGo: (e) => t(e.view === "actions" ? {
			action: "",
			artifact: "",
			tab: "decision",
			stage: "",
			unit: "",
			anchor: "",
			...e
		} : e),
		onQueued: (e) => v({
			text: r("intents.run.queued"),
			actionId: e
		}),
		onChanged: (e) => {
			e && v({ text: e }), P();
		},
		onRecompose: (e) => {
			v(null), S(null), w(null), b(e);
		},
		onSession: (e) => {
			v(null), b(null), w(null), S(e);
		},
		onSettings: (e) => {
			v(null), b(null), S(null), w(e);
		}
	}), [
		a,
		t,
		P,
		r
	]), I = x ? T.find((e) => e.repo_id === x.repo_id) : void 0, L = x ? A.find((e) => e.repo_id === x.repo_id && e.intent_key === x.intent_key) ?? x : null;
	return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ h("div", {
			className: "studio-page",
			children: [
				/* @__PURE__ */ h("div", {
					className: "studio-spread studio-wrap",
					children: [/* @__PURE__ */ m("h1", { children: r("intents.title") }), /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => t({ view: "new-intent" }),
						children: [/* @__PURE__ */ m(Y, {
							name: "plus",
							size: 13
						}), r("intents.newIntent")]
					})]
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-lede",
					children: r("intents.lede")
				}),
				/* @__PURE__ */ h("div", {
					className: "studio-row studio-wrap studio-filters",
					role: "group",
					"aria-label": r("intents.filters"),
					children: [
						/* @__PURE__ */ h("label", {
							className: "studio-filter",
							children: [/* @__PURE__ */ m("span", { children: r("intents.filter.state") }), /* @__PURE__ */ h("select", {
								value: u.state,
								onChange: (e) => f((t) => ({
									...t,
									state: e.target.value
								})),
								children: [/* @__PURE__ */ m("option", {
									value: "",
									children: r("intents.filter.stateAll")
								}), M.map((e) => /* @__PURE__ */ m("option", {
									value: e,
									children: r(`enum.intentState.${e}`)
								}, e))]
							})]
						}),
						(k?.spaces.length ?? 0) > 1 ? /* @__PURE__ */ h("label", {
							className: "studio-filter",
							children: [/* @__PURE__ */ m("span", { children: r("intents.filter.space") }), /* @__PURE__ */ h("select", {
								value: u.space,
								onChange: (e) => f((t) => ({
									...t,
									space: e.target.value
								})),
								children: [/* @__PURE__ */ m("option", {
									value: "",
									children: r("intents.filter.spaceAll")
								}), (k?.spaces ?? []).map((e) => /* @__PURE__ */ m("option", {
									value: e,
									children: e
								}, e))]
							})]
						}) : null,
						/* @__PURE__ */ h("label", {
							className: "studio-filter",
							children: [/* @__PURE__ */ m("span", { children: r("intents.filter.search") }), /* @__PURE__ */ m("input", {
								type: "search",
								value: p,
								placeholder: r("intents.filter.searchPlaceholder"),
								onChange: (e) => g(e.target.value)
							})]
						}),
						/* @__PURE__ */ h("label", {
							className: "studio-filter studio-filter-check",
							children: [/* @__PURE__ */ m("input", {
								type: "checkbox",
								checked: u.archived,
								onChange: (e) => f((t) => ({
									...t,
									archived: e.target.checked
								}))
							}), /* @__PURE__ */ m("span", { children: r("intents.filter.archived") })]
						}),
						/* @__PURE__ */ m(X, {
							mono: !0,
							children: r("intents.showing", {
								visible: n.fmt.number(A.length),
								total: n.fmt.number(j)
							})
						}),
						O.stale ? /* @__PURE__ */ m("span", {
							className: "studio-muted",
							children: r("common.loading")
						}) : null
					]
				}),
				_ ? /* @__PURE__ */ h("p", {
					className: "studio-banner",
					"data-tone": "info",
					role: "status",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "info",
							size: 15
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-grow",
							children: _.text
						}),
						_.actionId ? /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => t({
								view: "actions",
								action: _.actionId ?? ""
							}),
							children: r("intents.run.open")
						}) : null,
						/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => v(null),
							children: r("intents.run.dismiss")
						})
					]
				}) : null,
				O.error ? /* @__PURE__ */ h("p", {
					className: "studio-banner",
					"data-tone": "danger",
					role: "alert",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 15
						}),
						/* @__PURE__ */ h("span", {
							className: "studio-grow",
							children: [
								r("intents.error.title"),
								" ",
								D(O.error)
							]
						}),
						/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => void O.refresh(),
							children: r("common.retry")
						})
					]
				}) : null,
				L && I ? /* @__PURE__ */ m(no, {
					api: a,
					repo: I,
					intent: L,
					onClose: () => S(null),
					onChanged: (e) => {
						e && v({ text: e }), P();
					}
				}) : null,
				y ? /* @__PURE__ */ m(Pa, {
					api: a,
					repoId: y.repo_id,
					intentKey: y.intent_key,
					intentLabel: y.intent_dir,
					onClose: () => b(null),
					onApplied: () => void O.refresh()
				}) : null,
				C ? /* @__PURE__ */ m(Ia, {
					api: a,
					repoId: C.repo_id,
					intentKey: C.intent_key,
					intentLabel: C.intent_dir,
					onClose: () => w(null),
					onApplied: () => void O.refresh()
				}) : null,
				T.length === 0 ? /* @__PURE__ */ m(so, {
					title: r("intents.emptyNoRepo.title"),
					body: r("intents.emptyNoRepo.body"),
					action: {
						label: r("intents.emptyNoRepo.open"),
						run: () => t({ view: "repos" })
					}
				}) : O.loading ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: r("common.loading")
				}) : A.length === 0 && (k?.failures.length ?? 0) === 0 ? N ? /* @__PURE__ */ m(so, {
					title: r("intents.emptyFiltered.title"),
					body: r("intents.emptyFiltered.body"),
					action: {
						label: r("intents.emptyFiltered.clear"),
						run: () => {
							g(""), f(io);
						}
					}
				}) : /* @__PURE__ */ m(so, {
					title: r("intents.empty.title"),
					body: r("intents.empty.body"),
					action: {
						label: r("intents.newIntent"),
						run: () => t({ view: "new-intent" })
					}
				}) : /* @__PURE__ */ m(Ja, {
					intents: A,
					failures: k?.failures ?? [],
					narrow: c,
					renderActions: F
				}),
				A.length > 0 ? /* @__PURE__ */ h("p", {
					className: "studio-consequence",
					children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}), /* @__PURE__ */ m("span", { children: r("intents.keepMoving.why") })]
				}) : null,
				/* @__PURE__ */ m("p", {
					className: "studio-sr",
					role: "status",
					children: q(n, "intents.count", A.length)
				})
			]
		})
	});
}
function so({ title: e, body: t, action: n }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-empty",
		children: [
			/* @__PURE__ */ m(Y, {
				name: "intent",
				size: 18
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-empty-title",
				children: e
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: t
			}),
			/* @__PURE__ */ m("button", {
				type: "button",
				className: "studio-btn",
				onClick: n.run,
				children: n.label
			})
		]
	});
}
//#endregion
//#region src/views/intents/index.tsx
var co = /* @__PURE__ */ O({ default: () => oo }), lo = [
	"overview",
	"detailed",
	"dependencies"
];
function uo({ value: e, onChange: t }) {
	let { t: n } = H();
	return /* @__PURE__ */ m("div", {
		className: "studio-seg",
		role: "group",
		"aria-label": n("map.bar.density"),
		children: lo.map((r) => /* @__PURE__ */ m("button", {
			type: "button",
			"data-density": r,
			"aria-pressed": r === e,
			onClick: () => t(r),
			children: n(`map.density.${r}`)
		}, r))
	});
}
//#endregion
//#region src/map/DependencyOverlay.tsx
function fo(e, t) {
	return {
		x: e.left - t.left + e.width / 2,
		y: e.top - t.top + e.height / 2
	};
}
function po({ canvas: e, from: t, relations: n, token: r }) {
	let [a, s] = d([]), l = i(() => {
		if (!e || !t || n.size === 0) {
			s([]);
			return;
		}
		let r = e.getBoundingClientRect(), i = /* @__PURE__ */ new Map();
		for (let t of e.querySelectorAll(".studio-stage[data-variant=\"stage\"]")) {
			let e = t.dataset.stage;
			e && !i.has(e) && i.set(e, t);
		}
		let a = (e) => i.get(e) ?? null, o = a(t);
		if (!o) {
			s([]);
			return;
		}
		let c = fo(o.getBoundingClientRect(), r), l = [];
		for (let [e, t] of n) {
			let n = a(e);
			if (!n) continue;
			let i = fo(n.getBoundingClientRect(), r), o = (c.x + i.x) / 2 + (c.y - i.y) * .12, s = (c.y + i.y) / 2 + (i.x - c.x) * .12;
			l.push({
				slug: e,
				relation: t,
				d: `M ${c.x} ${c.y} Q ${o} ${s} ${i.x} ${i.y}`,
				x: i.x,
				y: i.y
			});
		}
		s(l);
	}, [
		e,
		t,
		n
	]);
	return c(l, [l, r]), o(() => {
		if (!e) return;
		if (typeof ResizeObserver > "u") return window.addEventListener("resize", l), () => window.removeEventListener("resize", l);
		let t = new ResizeObserver(() => l());
		return t.observe(e), () => t.disconnect();
	}, [e, l]), a.length === 0 ? null : /* @__PURE__ */ m("svg", {
		className: "studio-map-edges",
		"aria-hidden": !0,
		focusable: "false",
		children: a.map((e) => /* @__PURE__ */ h("g", {
			className: "studio-map-edge",
			"data-relation": e.relation,
			"data-to": e.slug,
			children: [/* @__PURE__ */ m("path", {
				d: e.d,
				fill: "none",
				vectorEffect: "non-scaling-stroke"
			}), /* @__PURE__ */ m("circle", {
				cx: e.x,
				cy: e.y,
				r: 2.5
			})]
		}, `${e.relation}:${e.slug}`))
	});
}
//#endregion
//#region src/map/StageCard.tsx
var mo = {
	completed: {
		tone: "ok",
		icon: "check"
	},
	in_progress: {
		tone: "accent",
		icon: "play"
	},
	awaiting_approval: {
		tone: "warn",
		icon: "gate"
	},
	revising: {
		tone: "aim",
		icon: "refresh"
	},
	not_started: {
		tone: "neutral",
		icon: "clock"
	},
	skipped: {
		tone: "neutral",
		icon: "warn"
	},
	unknown: {
		tone: "neutral",
		icon: "warn"
	},
	excluded: {
		tone: "neutral",
		icon: "warn"
	}
};
function ho(e, t) {
	let n = mo[t] ?? mo.unknown, r = t === "excluded" ? e.t("map.state.excluded") : e.t(`enum.stageState.${t}`);
	return {
		...n,
		label: r
	};
}
function go(e, t) {
	let n = [];
	return t.is_current && n.push(e.t("map.chip.current")), t.gate && n.push(e.t("map.chip.gate")), t.execution === "CONDITIONAL" && n.push(e.t("map.chip.conditional")), t.is_directive && n.push(e.t("map.chip.directive")), t.agent && n.push(e.t("map.a11y.agent", { agent: t.agent })), n.push(t.review_class ? e.t("map.chip.review", { class: t.review_class }) : e.t("map.chip.noReview")), t.elapsed_secs !== null && n.push(e.t("map.a11y.elapsed", { duration: Te(e, t.elapsed_secs) })), n.push(_o(e, t.artifacts.length)), t.skipped_reason && n.push(e.t("map.a11y.reason", { reason: t.skipped_reason })), n;
}
function _o(e, t) {
	return q(e, "map.chip.artifacts", t);
}
function vo(e, t, n) {
	let r = ho(e, t.state);
	return e.t("map.a11y.stage", {
		number: t.number,
		slug: t.slug,
		phase: n.phaseLabel,
		state: r.label,
		repo: n.repoLabel,
		intent: n.intentLabel,
		facts: go(e, t).join(", ")
	}).replace(/\s+/g, " ").trim();
}
function yo({ stage: e, density: t, selected: n, relation: r, scope: i, onSelect: a }) {
	let o = H(), { t: s } = o, c = ho(o, e.state), l = t !== "overview";
	return /* @__PURE__ */ h("button", {
		type: "button",
		className: "studio-stage",
		"data-stage": e.slug,
		"data-variant": "stage",
		"data-state": e.state,
		"data-phase": e.phase,
		...e.is_current ? { "data-current": "true" } : {},
		...r ? { "data-relation": r } : {},
		"aria-pressed": n,
		"aria-label": vo(o, e, i),
		onClick: () => a(e.slug),
		children: [
			r ? /* @__PURE__ */ m("span", {
				className: "studio-stage-rel",
				"data-relation": r,
				"aria-hidden": !0,
				children: s(`map.rel.${r}`)
			}) : null,
			/* @__PURE__ */ m("span", {
				className: "studio-stage-num studio-mono",
				"aria-hidden": !0,
				children: e.number
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-stage-name",
				children: e.slug
			}),
			/* @__PURE__ */ h("span", {
				className: "studio-stage-chips",
				"aria-hidden": !0,
				children: [
					/* @__PURE__ */ m(X, {
						tone: c.tone,
						icon: c.icon,
						children: c.label
					}),
					e.is_current ? /* @__PURE__ */ h(X, {
						tone: "accent",
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pulse",
							"aria-hidden": !0
						}), s("map.chip.current")]
					}) : null,
					e.gate ? /* @__PURE__ */ m(X, {
						tone: "warn",
						icon: "gate",
						children: s("map.chip.gate")
					}) : null,
					l && e.execution === "CONDITIONAL" ? /* @__PURE__ */ m(X, {
						icon: "info",
						children: s("map.chip.conditional")
					}) : null,
					l && e.agent ? /* @__PURE__ */ m(X, {
						icon: "intent",
						mono: !0,
						title: s("map.chip.agentTitle", { agent: e.agent }),
						children: e.agent
					}) : null,
					l && e.review_class ? /* @__PURE__ */ m(X, {
						tone: "info",
						icon: "review",
						mono: !0,
						title: s("map.chip.reviewerTitle", { reviewer: e.reviewer ?? "" }),
						children: e.review_class
					}) : null,
					l && e.elapsed_secs !== null ? /* @__PURE__ */ m(X, {
						icon: "clock",
						mono: !0,
						title: s("map.chip.elapsedTitle", { duration: Te(o, e.elapsed_secs) }),
						children: Te(o, e.elapsed_secs)
					}) : null,
					l ? /* @__PURE__ */ m(X, {
						icon: "doc",
						mono: !0,
						children: o.fmt.number(e.artifacts.length)
					}) : null
				]
			}),
			e.skipped_reason ? /* @__PURE__ */ h("span", {
				className: "studio-stage-why",
				"aria-hidden": !0,
				children: [
					/* @__PURE__ */ m(Y, {
						name: "info",
						size: 11,
						strokeWidth: 2
					}),
					" ",
					e.skipped_reason
				]
			}) : null
		]
	});
}
function bo({ stage: e, unit: t, selected: n, scope: r, onSelect: i }) {
	let a = H(), o = ho(a, t.state), s = [_o(a, t.artifacts.length)];
	return /* @__PURE__ */ h("button", {
		type: "button",
		className: "studio-stage",
		"data-stage": e.slug,
		"data-unit": t.unit,
		"data-variant": "unit",
		"data-state": t.state,
		"aria-pressed": n,
		"aria-label": a.t("map.a11y.unit", {
			unit: t.unit,
			number: e.number,
			slug: e.slug,
			state: o.label,
			repo: r.repoLabel,
			intent: r.intentLabel,
			facts: s.join(", ")
		}).replace(/\s+/g, " ").trim(),
		onClick: () => i(e.slug, t.unit),
		children: [
			/* @__PURE__ */ m("span", {
				className: "studio-stage-num studio-mono",
				"aria-hidden": !0,
				children: e.number
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-stage-name studio-mono",
				children: t.unit
			}),
			/* @__PURE__ */ h("span", {
				className: "studio-stage-chips",
				"aria-hidden": !0,
				children: [/* @__PURE__ */ m(X, {
					tone: o.tone,
					icon: o.icon,
					children: o.label
				}), /* @__PURE__ */ m(X, {
					icon: "doc",
					mono: !0,
					children: a.fmt.number(t.artifacts.length)
				})]
			})
		]
	});
}
//#endregion
//#region src/map/UnitLanes.tsx
function xo({ stages: e, selectedStage: t, selectedUnit: n, scope: r, onSelect: i }) {
	let { t: a } = H(), o = e.filter((e) => e.units.length > 0);
	return o.length === 0 ? null : /* @__PURE__ */ h("div", {
		className: "studio-units",
		children: [/* @__PURE__ */ m("ul", {
			className: "studio-unit-list",
			children: o.map((e) => /* @__PURE__ */ h("li", {
				className: "studio-unit-row",
				children: [/* @__PURE__ */ h("span", {
					className: "studio-unit-label studio-mono",
					children: [/* @__PURE__ */ m(Y, {
						name: "chevron",
						size: 11,
						strokeWidth: 2
					}), a("map.units.row", {
						number: e.number,
						slug: e.slug
					})]
				}), /* @__PURE__ */ m("ul", {
					className: "studio-stages",
					role: "list",
					children: e.units.map((a) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ m(bo, {
						stage: e,
						unit: a,
						selected: t === e.slug && n === a.unit,
						scope: r,
						onSelect: i
					}) }, a.unit))
				})]
			}, e.slug))
		}), /* @__PURE__ */ m("p", {
			className: "studio-unit-note studio-muted",
			children: a("map.units.note")
		})]
	});
}
//#endregion
//#region src/map/PhaseAccordion.tsx
function So(e) {
	return e.stages.some((e) => e.is_current || e.state === "awaiting_approval" || e.state === "in_progress");
}
function Co({ phases: e, density: t, showUnits: n, selectedStage: r, selectedUnit: i, scope: a, phaseLabel: o, onSelectStage: s, onSelectUnit: c, inspector: u }) {
	let f = H(), [p, g] = d({}), _ = l(() => {
		let t = /* @__PURE__ */ new Set();
		for (let n of e) (So(n) || n.stages.some((e) => e.slug === r)) && t.add(n.phase);
		return t.size === 0 && e[0] && t.add(e[0].phase), t;
	}, [e, r]);
	return /* @__PURE__ */ m("div", {
		className: "studio-accordions",
		children: e.map((e) => {
			let l = o(e.phase), d = p[e.phase] ?? _.has(e.phase), v = `studio-phase-${e.phase}`, y = e.stages.some((e) => e.slug === r);
			return /* @__PURE__ */ h("section", {
				className: "studio-accordion",
				"data-phase": e.phase,
				"data-lane": e.phase,
				children: [/* @__PURE__ */ m("h2", {
					className: "studio-accordion-h",
					children: /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-accordion-toggle",
						"aria-expanded": d,
						"aria-controls": v,
						onClick: () => g((t) => ({
							...t,
							[e.phase]: !d
						})),
						children: [
							/* @__PURE__ */ m(Y, {
								name: d ? "chevronDown" : "chevron",
								size: 15
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-grow",
								children: l
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-mono studio-muted studio-accordion-sub",
								children: q(f, "map.lane.counts", e.counts.total, { skipped: f.fmt.number(e.counts.skipped) })
							}),
							e.status ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: e.status
							}) : null
						]
					})
				}), /* @__PURE__ */ h("div", {
					id: v,
					className: "studio-accordion-body",
					hidden: !d,
					children: [
						/* @__PURE__ */ m("ul", {
							className: "studio-stages",
							role: "list",
							"aria-label": f.t("map.a11y.lane", { phase: l }),
							children: e.stages.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ m(yo, {
								stage: e,
								density: t,
								selected: r === e.slug && !i,
								relation: null,
								scope: {
									...a,
									phaseLabel: l
								},
								onSelect: s
							}) }, e.slug))
						}),
						n ? /* @__PURE__ */ m(xo, {
							stages: e.stages.filter((e) => e.per_unit),
							selectedStage: r,
							selectedUnit: i,
							scope: {
								...a,
								phaseLabel: l
							},
							onSelect: c
						}) : null,
						y ? /* @__PURE__ */ m("div", {
							className: "studio-map-sheet",
							role: "complementary",
							"aria-label": f.t("map.inspector.title"),
							children: u
						}) : null
					]
				})]
			}, e.phase);
		})
	});
}
//#endregion
//#region src/map/PhaseLane.tsx
function wo({ phase: e, phaseLabel: t, density: n, showUnits: r, selectedStage: i, selectedUnit: a, relations: o, scope: s, onSelectStage: c, onSelectUnit: l }) {
	let u = H(), { t: d } = u, f = {
		...s,
		phaseLabel: t
	};
	return /* @__PURE__ */ h("li", {
		className: "studio-lane",
		"data-lane": e.phase,
		"data-phase": e.phase,
		children: [/* @__PURE__ */ h("div", {
			className: "studio-lane-h",
			children: [
				/* @__PURE__ */ m("span", {
					className: "studio-lane-name",
					children: t
				}),
				/* @__PURE__ */ m("span", {
					className: "studio-lane-sub studio-mono",
					children: q(u, "map.lane.counts", e.counts.total, { skipped: u.fmt.number(e.counts.skipped) })
				}),
				e.status ? /* @__PURE__ */ m("span", {
					className: "studio-lane-status",
					children: /* @__PURE__ */ m(X, {
						mono: !0,
						title: d("map.phase.statusTitle", { status: e.status }),
						children: e.status
					})
				}) : null
			]
		}), /* @__PURE__ */ h("div", {
			className: "studio-lane-body",
			children: [/* @__PURE__ */ m("ul", {
				className: "studio-stages",
				role: "list",
				"aria-label": d("map.a11y.lane", { phase: t }),
				children: e.stages.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ m(yo, {
					stage: e,
					density: n,
					selected: i === e.slug && !a,
					relation: o.get(e.slug) ?? null,
					scope: f,
					onSelect: c
				}) }, e.slug))
			}), r ? /* @__PURE__ */ m(xo, {
				stages: e.stages.filter((e) => e.per_unit),
				selectedStage: i,
				selectedUnit: a,
				scope: f,
				onSelect: l
			}) : null]
		})]
	});
}
//#endregion
//#region src/map/StageInspector.tsx
var To = /* @__PURE__ */ new Set(["Timestamp", "Event"]), Eo = [
	"blocker",
	"advisory",
	"resolved",
	"unknown"
];
function Do({ stage: e, unit: t, phaseLabel: n, repoLabel: r, intentLabel: i, review: a, audit: o, action: s, blocked: c, onClear: l, onSelectStage: u, onOpenAction: d, onOpenArtifact: f }) {
	let g = H(), { t: _ } = g;
	if (!e) return /* @__PURE__ */ h("div", {
		className: "studio-inspector-body",
		children: [/* @__PURE__ */ m("h2", { children: _("map.inspector.title") }), /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: _("map.inspector.pick")
		})]
	});
	let v = ho(g, t ? t.state : e.state), y = t ? t.artifacts : e.artifacts, b = e.state === "skipped" || e.state === "excluded" || !!e.skipped_reason, x = !!a && a.stage === e.slug;
	return /* @__PURE__ */ h("div", {
		className: "studio-inspector-body",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-spread studio-inspector-head",
				children: [/* @__PURE__ */ h("span", {
					className: "studio-row",
					children: [/* @__PURE__ */ m("span", {
						className: "studio-phase-chip",
						"data-phase": e.phase,
						children: /* @__PURE__ */ m(X, { children: n })
					}), e.number ? /* @__PURE__ */ m(X, {
						mono: !0,
						children: e.number
					}) : null]
				}), /* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-icon-btn",
					onClick: l,
					"aria-label": _("map.inspector.close"),
					children: /* @__PURE__ */ m(Y, {
						name: "close",
						size: 14
					})
				})]
			}),
			/* @__PURE__ */ m("h2", {
				className: "studio-wrap-any",
				children: e.slug
			}),
			t ? /* @__PURE__ */ m("p", {
				className: "studio-mono studio-muted studio-inspector-unit",
				children: t.unit
			}) : null,
			/* @__PURE__ */ m("p", {
				className: "studio-sr",
				children: `${r} / ${i}`
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-inspector-chips",
				children: [
					/* @__PURE__ */ m(X, {
						tone: v.tone,
						icon: v.icon,
						children: v.label
					}),
					e.is_current ? /* @__PURE__ */ h(X, {
						tone: "accent",
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pulse",
							"aria-hidden": !0
						}), _("map.chip.current")]
					}) : null,
					e.is_directive ? /* @__PURE__ */ m(X, {
						tone: "aim",
						icon: "send",
						children: _("map.chip.directive")
					}) : null,
					e.gate ? /* @__PURE__ */ m(X, {
						tone: "warn",
						icon: "gate",
						children: _("map.chip.gate")
					}) : null,
					e.execution === "CONDITIONAL" ? /* @__PURE__ */ m(X, {
						icon: "info",
						children: _("map.chip.conditional")
					}) : null,
					e.agent ? /* @__PURE__ */ m(X, {
						icon: "intent",
						mono: !0,
						title: _("map.chip.agentTitle", { agent: e.agent }),
						children: e.agent
					}) : null,
					e.mode ? /* @__PURE__ */ m(X, {
						mono: !0,
						title: _("map.chip.modeTitle", { mode: e.mode }),
						children: e.mode
					}) : null,
					/* @__PURE__ */ m(X, {
						icon: "clock",
						mono: !0,
						children: Te(g, e.elapsed_secs)
					})
				]
			}),
			e.summary_confirmation ? /* @__PURE__ */ m("p", {
				className: "studio-muted studio-inspector-note",
				children: _("map.inspector.summaryConfirmation", { value: e.summary_confirmation })
			}) : null,
			b ? /* @__PURE__ */ h("p", {
				className: "studio-consequence",
				children: [
					/* @__PURE__ */ m("b", { children: _("map.inspector.why") }),
					" ",
					e.skipped_reason || _(e.state === "excluded" ? "map.inspector.notSelected" : "map.inspector.whyUnknown")
				]
			}) : null,
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ m("h3", { children: _("map.inspector.relationships") }), /* @__PURE__ */ h("div", {
					className: "studio-evgrid",
					children: [
						/* @__PURE__ */ m(ko, {
							title: _("map.inspector.upstream"),
							slugs: e.depends_on,
							onSelect: u,
							empty: _("map.inspector.none")
						}),
						/* @__PURE__ */ m(ko, {
							title: _("map.inspector.downstream"),
							slugs: e.dependents,
							onSelect: u,
							empty: _("map.inspector.none")
						}),
						/* @__PURE__ */ m(Ao, {
							title: _("map.inspector.consumes"),
							names: e.consumes,
							empty: _("map.inspector.none")
						}),
						/* @__PURE__ */ m(Ao, {
							title: _("map.inspector.produces"),
							names: e.produces,
							empty: _("map.inspector.none")
						})
					]
				})]
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ h("h3", { children: [
						_("map.inspector.artifacts"),
						" ",
						/* @__PURE__ */ m("span", {
							className: "studio-muted",
							children: _o(g, y.length)
						})
					] }),
					y.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: _("map.inspector.artifactsEmpty")
					}) : /* @__PURE__ */ m("ul", {
						className: "studio-file-list",
						role: "list",
						children: y.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-file",
							onClick: () => f(e),
							"aria-label": _("map.inspector.openArtifact", { name: e.name }),
							children: [
								/* @__PURE__ */ m(Y, {
									name: "doc",
									size: 13
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-grow studio-trunc studio-mono",
									children: e.name
								}),
								/* @__PURE__ */ m(X, {
									mono: !0,
									children: _(`map.artifactKind.${e.kind}`)
								}),
								/* @__PURE__ */ h("span", {
									className: "studio-muted studio-mono studio-file-meta",
									children: [
										je(g, e.size),
										" · ",
										De(g, e.mtime)
									]
								})
							]
						}) }, e.artifact_id))
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-muted studio-inspector-note",
						children: _("map.inspector.artifactsNote")
					})
				]
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ m("h3", { children: _("map.inspector.review") }),
					/* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: e.review_class ? e.reviewer ? _("map.inspector.reviewContract", {
							class: e.review_class,
							reviewer: e.reviewer
						}) : _("map.inspector.reviewContractNoReviewer", { class: e.review_class }) : _("map.inspector.reviewNone")
					}),
					a && !x && a.stage ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: _("map.inspector.reviewOtherStage", { stage: a.stage })
					}) : null,
					x ? /* @__PURE__ */ m(jo, { review: a }) : null
				]
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ m("h3", { children: _("map.inspector.audit") }),
					o === null ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: _("common.unavailable")
					}) : o.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: _("map.inspector.auditEmpty")
					}) : /* @__PURE__ */ m("ul", {
						className: "studio-audit-list",
						role: "list",
						children: o.map((e) => /* @__PURE__ */ h("li", {
							className: "studio-audit",
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-muted",
									children: K(g, e.timestamp)
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-audit-event",
									children: e.event
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-audit-fields studio-wrap-any",
									children: Object.entries(e.fields).filter(([e]) => !To.has(e)).slice(0, 4).map(([e, t]) => /* @__PURE__ */ h("span", {
										className: "studio-audit-field",
										children: [
											/* @__PURE__ */ m("b", {
												className: "studio-mono",
												children: e
											}),
											" ",
											/* @__PURE__ */ m("span", {
												className: "studio-mono",
												children: t
											})
										]
									}, e))
								})
							]
						}, `${e.shard}:${e.pos}`))
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-muted studio-inspector-note",
						children: _("map.inspector.auditNote")
					})
				]
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ m("h3", { children: _("map.inspector.operation") }), s && !c ? /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-btn-primary studio-full",
					onClick: () => d(s.action_id),
					children: [/* @__PURE__ */ m(Y, {
						name: "inbox",
						size: 15
					}), _("map.inspector.open", { type: _(`enum.actionType.${s.queue_type}`) })]
				}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-full",
					disabled: !0,
					children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 15
					}), _("map.inspector.noOperation")]
				}), /* @__PURE__ */ m("p", {
					className: "studio-muted studio-inspector-note",
					children: Oo(_, e, c)
				})] })]
			})
		]
	});
}
function Oo(e, t, n) {
	return n === "archived" ? e("map.inspector.reasonArchived") : n === "paused" ? e("map.inspector.reasonPaused") : t.state === "skipped" || t.state === "excluded" ? e("map.inspector.reasonSkipped") : t.state === "completed" ? e("map.inspector.reasonDone") : e("map.inspector.reasonAhead");
}
function ko({ title: e, slugs: t, empty: n, onSelect: r }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-ev",
		children: [/* @__PURE__ */ m("span", {
			className: "studio-ev-src",
			children: e
		}), t.length === 0 ? /* @__PURE__ */ m("span", {
			className: "studio-muted",
			children: n
		}) : /* @__PURE__ */ m("span", {
			className: "studio-ev-val",
			children: t.map((e) => /* @__PURE__ */ m("button", {
				type: "button",
				className: "studio-link studio-mono",
				onClick: () => r(e),
				children: e
			}, e))
		})]
	});
}
function Ao({ title: e, names: t, empty: n }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-ev",
		children: [/* @__PURE__ */ m("span", {
			className: "studio-ev-src",
			children: e
		}), t.length === 0 ? /* @__PURE__ */ m("span", {
			className: "studio-muted",
			children: n
		}) : /* @__PURE__ */ m("span", {
			className: "studio-ev-val studio-mono studio-wrap-any",
			children: t.map((e) => /* @__PURE__ */ m("span", {
				className: "studio-ev-name",
				children: e
			}, e))
		})]
	});
}
function jo({ review: e }) {
	let t = H(), { t: n } = t, r = /* @__PURE__ */ new Map();
	for (let t of e.findings) r.set(t.level, (r.get(t.level) ?? 0) + 1);
	let i = e.findings.filter((e) => e.level === "blocker"), a = i.slice(0, 3);
	return e.findings.length === 0 && !e.verdict ? /* @__PURE__ */ m("p", {
		className: "studio-muted",
		children: n("map.inspector.reviewEmpty")
	}) : /* @__PURE__ */ h("div", {
		className: "studio-col studio-review-summary",
		children: [/* @__PURE__ */ h("div", {
			className: "studio-row",
			children: [
				e.verdict ? /* @__PURE__ */ m(X, {
					icon: "review",
					mono: !0,
					children: n("map.inspector.reviewVerdict", { verdict: e.verdict })
				}) : null,
				e.revisions > 0 ? /* @__PURE__ */ m(X, {
					icon: "refresh",
					children: q(t, "map.inspector.reviewRevisions", e.revisions)
				}) : null,
				Eo.filter((e) => (r.get(e) ?? 0) > 0).map((e) => /* @__PURE__ */ m(X, {
					tone: e === "blocker" ? "danger" : e === "advisory" ? "warn" : e === "resolved" ? "ok" : "neutral",
					icon: e === "blocker" ? "warn" : e === "advisory" ? "info" : e === "resolved" ? "check" : "info",
					children: `${n(`map.review.level.${e}`)} ${t.fmt.number(r.get(e) ?? 0)}`
				}, e))
			]
		}), a.length > 0 ? /* @__PURE__ */ h("ul", {
			className: "studio-finding-list",
			role: "list",
			children: [a.map((e, t) => /* @__PURE__ */ m("li", {
				className: "studio-finding",
				"data-severity": "blocking",
				children: /* @__PURE__ */ m("span", {
					className: "studio-wrap-any",
					children: e.title
				})
			}, `${e.title}:${t}`)), i.length > a.length ? /* @__PURE__ */ m("li", {
				className: "studio-muted",
				children: n("map.review.more", { n: t.fmt.number(i.length - a.length) })
			}) : null]
		}) : null]
	});
}
//#endregion
//#region src/map/MapView.tsx
var Mo = "aidlc-studio:mapDensity", No = "(max-width: 900px)", Po = /* @__PURE__ */ new Set(["recovery", "delivery_uncertain"]), Fo = new Map(ea.map((e, t) => [e, t]));
function Io(e) {
	return e === "overview" || e === "detailed" || e === "dependencies";
}
function Lo() {
	try {
		let e = localStorage.getItem(Mo);
		return Io(e) ? e : "overview";
	} catch {
		return "overview";
	}
}
function Ro() {
	let [e, t] = d(Lo);
	return [e, i((e) => {
		t(e);
		try {
			localStorage.setItem(Mo, e);
		} catch {}
	}, [])];
}
function zo() {
	let e = i((e) => {
		if (typeof window.matchMedia != "function") return () => {};
		let t = window.matchMedia(No);
		return t.addEventListener("change", e), () => t.removeEventListener("change", e);
	}, []);
	return f(e, () => typeof window.matchMedia == "function" && window.matchMedia(No).matches, () => !1);
}
function Bo(e, t) {
	return Fo.has(t) ? e.t(`enum.phase.${t}`) : e.t("map.phase.unknown");
}
function Vo({ api: e, route: t, go: n, cards: r }) {
	let a = H(), { t: o } = a, [s, c] = Ro(), [f, g] = d(!1), [_, v] = d(!1), [y, b] = d(null), C = zo(), w = u(null), [T, E] = d(null), D = t.repo, O = t.intent, k = !!D && !!O, A = `${D}:${O}`, j = y === A, M = J(k ? `map:${D}:${O}` : null, i((t) => e.map(D, O, void 0, { signal: t }), [
		e,
		D,
		O
	])), N = J(k ? `map:intent:${D}:${O}` : null, i((t) => e.intent(D, O, { signal: t }), [
		e,
		D,
		O
	])), P = J(k && t.stage ? `map:review:${D}:${O}` : null, i((t) => e.review(D, O, { signal: t }), [
		e,
		D,
		O
	]), { interval: 0 }), F = M.data?.map ?? null, I = N.data?.intent ?? null, L = l(() => F ? [...F.phases].sort((e, t) => (Fo.get(e.phase) ?? ea.length) - (Fo.get(t.phase) ?? ea.length)) : [], [F]), R = l(() => L.flatMap((e) => e.stages), [L]), ee = l(() => R.filter((e) => e.in_scope), [R]), z = l(() => {
		let e = new Set((j ? R : ee).map((e) => e.slug));
		return L.map((t) => {
			let n = t.stages.filter((t) => e.has(t.slug)).map((t) => ({
				...t,
				gate: t.in_scope && t.gate,
				state: !t.in_scope && t.state === "not_started" ? "excluded" : t.state,
				depends_on: t.depends_on.filter((t) => e.has(t)),
				dependents: t.dependents.filter((t) => e.has(t))
			}));
			return {
				...t,
				stages: n,
				counts: {
					total: n.length,
					in_scope: n.filter((e) => e.in_scope).length,
					done: n.filter((e) => e.state === "completed" || e.state === "skipped").length,
					skipped: n.filter((e) => e.state === "skipped" || e.skipped_reason).length
				}
			};
		}).filter((e) => e.stages.length > 0);
	}, [
		L,
		R,
		ee,
		j
	]), B = l(() => z.flatMap((e) => e.stages), [z]), V = l(() => B.find((e) => e.slug === t.stage) ?? null, [B, t.stage]), te = !j && R.some((e) => e.slug === t.stage && !e.in_scope), ne = l(() => V && t.unit ? V.units.find((e) => e.unit === t.unit) ?? null : null, [V, t.unit]), re = l(() => {
		let e = /* @__PURE__ */ new Map();
		if (!V) return e;
		for (let t of V.depends_on) e.set(t, "upstream");
		for (let t of V.dependents) e.has(t) || e.set(t, "downstream");
		return e;
	}, [V]), ie = l(() => r.filter((e) => e.intent.intent_key === O), [r, O]), ae = l(() => V ? ie.find((e) => e.stage?.slug === V.slug) ?? null : null, [ie, V]), oe = l(() => ie.find((e) => Po.has(e.queue_type)) ?? null, [ie]), se = l(() => I ? V ? I.audit_tail.filter((e) => Ho(e, V.slug)).slice(-12).reverse() : [] : null, [I, V]), ce = i((e) => {
		n({
			stage: t.stage === e && !t.unit ? "" : e,
			unit: ""
		});
	}, [
		n,
		t.stage,
		t.unit
	]), le = i((e, r) => {
		n({
			stage: e,
			unit: t.stage === e && t.unit === r ? "" : r
		});
	}, [
		n,
		t.stage,
		t.unit
	]), ue = i(() => n({
		stage: "",
		unit: ""
	}), [n]), de = i((e) => n({
		view: "actions",
		action: e,
		tab: "decision",
		artifact: "",
		anchor: ""
	}), [n]), fe = i((e) => n({
		view: "actions",
		action: "",
		tab: "artifacts",
		stage: e.stage ?? t.stage,
		unit: e.unit ?? "",
		artifact: e.artifact_id,
		anchor: ""
	}), [n, t.stage]), pe = i((e) => {
		if (e.key === "Escape") {
			t.stage && (e.preventDefault(), ue());
			return;
		}
		let n = w.current;
		if (!n) return;
		let r = [...n.querySelectorAll(".studio-stage")], i = r.indexOf(document.activeElement);
		if (i < 0) return;
		let a = -1;
		if (e.key === "ArrowRight") a = Math.min(r.length - 1, i + 1);
		else if (e.key === "ArrowLeft") a = Math.max(0, i - 1);
		else if (e.key === "Home") a = 0;
		else if (e.key === "End") a = r.length - 1;
		else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
			let t = [...n.querySelectorAll("[data-lane]")], o = r[i]?.closest("[data-lane]") ?? null, s = t[(o ? t.indexOf(o) : -1) + (e.key === "ArrowDown" ? 1 : -1)]?.querySelector(".studio-stage") ?? null;
			s && (a = r.indexOf(s));
		} else return;
		let o = r[a];
		o && (e.preventDefault(), o.focus());
	}, [ue, t.stage]);
	if (!k) return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ m("div", {
			className: "studio-page",
			children: /* @__PURE__ */ m(S, {
				icon: /* @__PURE__ */ m(Y, {
					name: "map",
					size: 18
				}),
				title: o("map.empty.title"),
				subtitle: o(D ? "map.empty.body" : "map.empty.noRepo"),
				action: /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					onClick: () => n({ view: "intents" }),
					children: [/* @__PURE__ */ m(Y, {
						name: "intent",
						size: 15
					}), o("map.empty.action")]
				})
			})
		})
	});
	if (!F) return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ m("div", {
			className: "studio-page",
			children: M.error ? /* @__PURE__ */ m(Uo, {
				error: M.error,
				onRetry: () => void M.refresh()
			}) : /* @__PURE__ */ m(x, { rows: 8 })
		})
	});
	let me = ee.find((e) => e.is_current) ?? null, he = ee.filter((e) => e.state === "completed" || e.state === "skipped").length, ge = me ? o("map.status.executing", {
		number: me.number,
		slug: me.slug
	}).replace(/\s+/g, " ").trim() : o("map.status.idle"), _e = I?.slug || O, U = I?.repo_label || D, ve = {
		repoLabel: U,
		intentLabel: _e
	}, W = B.some((e) => e.per_unit && e.units.length > 0), ye = I?.archived ? "archived" : I?.paused ? "paused" : null, be = /* @__PURE__ */ m(Do, {
		stage: V,
		unit: ne,
		phaseLabel: V ? Bo(a, V.phase) : "",
		repoLabel: U,
		intentLabel: _e,
		review: P.data ?? null,
		audit: se,
		action: ae,
		blocked: ye,
		onClear: ue,
		onSelectStage: ce,
		onOpenAction: de,
		onOpenArtifact: fe
	});
	return /* @__PURE__ */ h("div", {
		className: "studio-map",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-map-bar",
				children: [
					/* @__PURE__ */ m(X, {
						tone: "accent",
						icon: "intent",
						mono: !0,
						children: o("map.bar.scope", {
							repo: U,
							intent: _e
						})
					}),
					j ? /* @__PURE__ */ m(X, {
						mono: !0,
						children: q(a, "map.bar.stagesKnown", R.length)
					}) : null,
					/* @__PURE__ */ m(X, {
						mono: !0,
						children: q(a, "map.bar.stagesSelected", ee.length)
					}),
					/* @__PURE__ */ m(X, {
						mono: !0,
						icon: "gate",
						children: q(a, "map.bar.gates", ee.filter((e) => e.gate).length)
					}),
					F.graph_version ? /* @__PURE__ */ m(X, {
						mono: !0,
						icon: "lock",
						children: o("map.bar.engine", { version: F.graph_version })
					}) : null,
					/* @__PURE__ */ h("span", {
						className: "studio-map-exec",
						role: "status",
						"aria-live": "polite",
						children: [/* @__PURE__ */ m(X, {
							tone: me ? "accent" : "neutral",
							icon: me ? "play" : "pause",
							children: ge
						}), /* @__PURE__ */ m(X, {
							icon: "check",
							children: o("map.status.progress", {
								done: a.fmt.number(he),
								total: a.fmt.number(ee.length)
							})
						})]
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-map-bar-controls",
						children: [
							/* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								"aria-pressed": j,
								onClick: () => b(j ? null : A),
								children: o(j ? "map.bar.showPlanOnly" : "map.bar.showAllStages")
							}),
							/* @__PURE__ */ m(uo, {
								value: s,
								onChange: c
							}),
							W ? /* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								"aria-pressed": f,
								onClick: () => g((e) => !e),
								children: [/* @__PURE__ */ m(Y, {
									name: "chevronDown",
									size: 13
								}), o(f ? "map.bar.hideUnits" : "map.bar.expandUnits")]
							}) : null,
							/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								"aria-pressed": _,
								onClick: () => v((e) => !e),
								children: [/* @__PURE__ */ m(Y, {
									name: "doc",
									size: 13
								}), o(_ ? "map.bar.showCanvas" : "map.bar.showTable")]
							})
						]
					})
				]
			}),
			oe ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "recovery",
						size: 15
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-grow",
						children: o("map.alert.recovery")
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => de(oe.action_id),
						children: o("map.alert.openAction")
					})
				]
			}) : null,
			I?.unstable ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "warn",
				role: "status",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ m("span", {
					className: "studio-grow",
					children: o("map.alert.unstable")
				})]
			}) : null,
			M.error ? /* @__PURE__ */ m(Uo, {
				error: M.error,
				onRetry: () => void M.refresh()
			}) : null,
			te ? /* @__PURE__ */ m("p", {
				className: "studio-consequence",
				role: "status",
				children: o("map.hiddenSelection")
			}) : null,
			/* @__PURE__ */ h("div", {
				className: "studio-map-main",
				children: [/* @__PURE__ */ h("div", {
					className: "studio-map-scroll",
					children: [z.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						role: "status",
						children: o("map.empty.plan")
					}) : _ ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(Wo, {
						phases: z,
						intentLabel: _e,
						selectedStage: t.stage,
						onSelect: ce
					}), C && V ? /* @__PURE__ */ m("div", {
						className: "studio-map-sheet",
						role: "complementary",
						"aria-label": o("map.inspector.title"),
						children: be
					}) : null] }) : C ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: o("map.accordion.note")
					}), /* @__PURE__ */ m(Co, {
						phases: z,
						density: s,
						showUnits: f,
						selectedStage: t.stage,
						selectedUnit: t.unit,
						scope: ve,
						onSelectStage: ce,
						onSelectUnit: le,
						phaseLabel: (e) => Bo(a, e),
						inspector: be
					})] }) : /* @__PURE__ */ h("div", {
						className: "studio-map-canvas",
						ref: (e) => {
							w.current = e, E(e);
						},
						onKeyDown: pe,
						children: [/* @__PURE__ */ m("ul", {
							className: "studio-lanes",
							role: "list",
							"aria-label": o("map.a11y.canvas"),
							children: z.map((e) => /* @__PURE__ */ m(wo, {
								phase: e,
								phaseLabel: Bo(a, e.phase),
								density: s,
								showUnits: f,
								selectedStage: t.stage,
								selectedUnit: t.unit,
								relations: re,
								scope: ve,
								onSelectStage: ce,
								onSelectUnit: le
							}, e.phase))
						}), s === "dependencies" && V ? /* @__PURE__ */ m(po, {
							canvas: T,
							from: V.slug,
							relations: re,
							token: `${s}:${f}:${V.slug}:${B.length}`
						}) : null]
					}), /* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: o("map.consequence")
					})]
				}), C ? null : /* @__PURE__ */ m("aside", {
					className: "studio-inspector",
					"aria-label": o("map.inspector.title"),
					children: be
				})]
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-sr",
				role: "status",
				"aria-live": "polite",
				children: V ? ne ? o("map.a11y.unitSelected", {
					unit: ne.unit,
					number: V.number,
					slug: V.slug
				}) : o("map.a11y.selected", {
					number: V.number,
					slug: V.slug
				}) : ""
			})
		]
	});
}
function Ho(e, t) {
	let n = e.fields.Stage;
	return n ? n === t : (e.fields.Context ?? "").split(/\s*>\s*/).includes(t);
}
function Uo({ error: e, onRetry: t }) {
	let { t: n, has: r } = H();
	return /* @__PURE__ */ h("div", {
		className: "studio-banner",
		"data-tone": "danger",
		role: "alert",
		children: [
			/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 15
			}),
			/* @__PURE__ */ h("span", {
				className: "studio-grow",
				children: [
					/* @__PURE__ */ m("b", { children: n("map.error.title") }),
					" ",
					r(`errors.${e.code}`) ? n(`errors.${e.code}`) : e.message
				]
			}),
			/* @__PURE__ */ m("button", {
				type: "button",
				className: "studio-btn",
				onClick: t,
				children: n("common.retry")
			})
		]
	});
}
function Wo({ phases: e, intentLabel: t, selectedStage: n, onSelect: r }) {
	let i = H(), { t: a } = i;
	return /* @__PURE__ */ m("div", {
		className: "studio-map-table",
		children: /* @__PURE__ */ h("table", {
			className: "studio-tbl",
			children: [
				/* @__PURE__ */ m("caption", {
					className: "studio-sr",
					children: a("map.a11y.tableCaption", { intent: t })
				}),
				/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ m("tr", { children: [
					"phase",
					"number",
					"stage",
					"state",
					"agent",
					"gate",
					"review",
					"elapsed",
					"artifacts",
					"notes"
				].map((e) => /* @__PURE__ */ m("th", {
					scope: "col",
					children: a(`map.col.${e}`)
				}, e)) }) }),
				/* @__PURE__ */ m("tbody", { children: e.flatMap((e) => e.stages.map((t) => {
					let o = ho(i, t.state);
					return /* @__PURE__ */ h("tr", {
						"data-selected": t.slug === n ? "true" : void 0,
						children: [
							/* @__PURE__ */ m("td", { children: Bo(i, e.phase) }),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: t.number
							}),
							/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-link studio-mono",
								"aria-pressed": t.slug === n,
								onClick: () => r(t.slug),
								children: t.slug
							}) }),
							/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m(X, {
								tone: o.tone,
								icon: o.icon,
								children: o.label
							}) }),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: t.agent
							}),
							/* @__PURE__ */ m("td", { children: t.gate ? a("map.chip.gate") : a("common.none") }),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: t.review_class ?? a("common.none")
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: Te(i, t.elapsed_secs)
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: _o(i, t.artifacts.length)
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-wrap-any",
								children: [
									t.is_current ? a("map.chip.current") : "",
									t.execution === "CONDITIONAL" ? a("map.chip.conditional") : "",
									t.skipped_reason ?? ""
								].filter(Boolean).join(" · ")
							})
						]
					}, `${e.phase}:${t.slug}`);
				})) })
			]
		})
	});
}
function Go({ route: e, go: t }) {
	let n = jl();
	return /* @__PURE__ */ m(Vo, {
		api: n.api,
		route: e,
		go: t,
		cards: n.actions.data?.actions ?? []
	});
}
//#endregion
//#region src/views/map/index.tsx
var Ko = /* @__PURE__ */ O({ default: () => Go }), qo = new Uint32Array([
	1116352408,
	1899447441,
	3049323471,
	3921009573,
	961987163,
	1508970993,
	2453635748,
	2870763221,
	3624381080,
	310598401,
	607225278,
	1426881987,
	1925078388,
	2162078206,
	2614888103,
	3248222580,
	3835390401,
	4022224774,
	264347078,
	604807628,
	770255983,
	1249150122,
	1555081692,
	1996064986,
	2554220882,
	2821834349,
	2952996808,
	3210313671,
	3336571891,
	3584528711,
	113926993,
	338241895,
	666307205,
	773529912,
	1294757372,
	1396182291,
	1695183700,
	1986661051,
	2177026350,
	2456956037,
	2730485921,
	2820302411,
	3259730800,
	3345764771,
	3516065817,
	3600352804,
	4094571909,
	275423344,
	430227734,
	506948616,
	659060556,
	883997877,
	958139571,
	1322822218,
	1537002063,
	1747873779,
	1955562222,
	2024104815,
	2227730452,
	2361852424,
	2428436474,
	2756734187,
	3204031479,
	3329325298
]), Jo = (e, t) => (e >>> t | e << 32 - t) >>> 0;
function Yo(e) {
	let t = 1779033703, n = 3144134277, r = 1013904242, i = 2773480762, a = 1359893119, o = 2600822924, s = 528734635, c = 1541459225, l = new Uint8Array((e.length + 8 >> 6) + 1 << 6);
	l.set(e), l[e.length] = 128;
	let u = new DataView(l.buffer), d = e.length * 8;
	u.setUint32(l.length - 8, Math.floor(d / 4294967296)), u.setUint32(l.length - 4, d >>> 0);
	let f = /* @__PURE__ */ new Uint32Array(64), p = (e) => f[e], m = (e) => qo[e];
	for (let e = 0; e < l.length; e += 64) {
		for (let t = 0; t < 16; t += 1) f[t] = u.getUint32(e + t * 4);
		for (let e = 16; e < 64; e += 1) {
			let t = p(e - 15), n = p(e - 2), r = Jo(t, 7) ^ Jo(t, 18) ^ t >>> 3, i = Jo(n, 17) ^ Jo(n, 19) ^ n >>> 10;
			f[e] = p(e - 16) + r + p(e - 7) + i >>> 0;
		}
		let l = t, d = n, h = r, g = i, _ = a, v = o, y = s, b = c;
		for (let e = 0; e < 64; e += 1) {
			let t = Jo(_, 6) ^ Jo(_, 11) ^ Jo(_, 25), n = _ & v ^ ~_ & y, r = b + t + n + m(e) + p(e) >>> 0, i = (Jo(l, 2) ^ Jo(l, 13) ^ Jo(l, 22)) + (l & d ^ l & h ^ d & h) >>> 0;
			b = y, y = v, v = _, _ = g + r >>> 0, g = h, h = d, d = l, l = r + i >>> 0;
		}
		t = t + l >>> 0, n = n + d >>> 0, r = r + h >>> 0, i = i + g >>> 0, a = a + _ >>> 0, o = o + v >>> 0, s = s + y >>> 0, c = c + b >>> 0;
	}
	return [
		t,
		n,
		r,
		i,
		a,
		o,
		s,
		c
	].map((e) => e.toString(16).padStart(8, "0")).join("");
}
function Xo(e) {
	let t = "\"";
	for (let n of e) {
		let e = n.codePointAt(0);
		if (n === "\"") t += "\\\"";
		else if (n === "\\") t += "\\\\";
		else if (n === "\n") t += "\\n";
		else if (n === "\r") t += "\\r";
		else if (n === "	") t += "\\t";
		else if (n === "\b") t += "\\b";
		else if (n === "\f") t += "\\f";
		else if (e >= 32 && e <= 126) t += n;
		else if (e < 65536) t += `\\u${e.toString(16).padStart(4, "0")}`;
		else {
			let n = e - 65536, r = 55296 + (n >> 10), i = 56320 + (n & 1023);
			t += `\\u${r.toString(16)}\\u${i.toString(16)}`;
		}
	}
	return `${t}"`;
}
function Zo(e) {
	if (e == null) return "null";
	if (typeof e == "boolean") return e ? "true" : "false";
	if (typeof e == "number") return String(e);
	if (typeof e == "string") return Xo(e);
	if (Array.isArray(e)) return `[${e.map(Zo).join(",")}]`;
	let t = e;
	return `{${Object.keys(t).sort().map((e) => `${Xo(e)}:${Zo(t[e])}`).join(",")}}`;
}
//#endregion
//#region src/wizard/PlanAdvisor.tsx
var Qo = 2e3, $o = 200;
function es(e) {
	return e.trim() || null;
}
var ts = [
	"Minimal",
	"Standard",
	"Comprehensive"
], ns = [
	"none",
	"advisory",
	"adversarial"
];
function rs(e) {
	return typeof e == "string" && e.trim() ? e.trim() : null;
}
function is(e) {
	if (!e) return null;
	let t = rs(e.name);
	if (!t) return null;
	let n = rs(e.depth), r = rs(e.test_strategy), i = rs(e.review_cap), a = Array.isArray(e.keywords) ? e.keywords.filter((e) => typeof e == "string") : [];
	return {
		name: t,
		depth: n && ts.includes(n) ? n : null,
		testStrategy: r && ts.includes(r) ? r : null,
		reviewCap: i && ns.includes(i) ? i : null,
		description: rs(e.description),
		keywords: a,
		projectOwned: e.project_owned === !0
	};
}
function as({ api: e, repoId: t, space: n, locale: r, objective: a, context: s, projectType: c, current: f }) {
	let [p, m] = d(null), [h, g] = d(!1), [_, v] = d(null), y = p && p.repoId === t ? p.draftId : null, b = u(t);
	b.current = t, o(() => {
		m((e) => e && e.repoId !== t ? null : e), v(null), g(!1);
	}, [t]);
	let x = J(y ? `advisor-draft:${y}` : null, i((t) => e.draft(y ?? "", { signal: t }), [e, y]), {
		enabled: !!y,
		busy: (e) => e.draft.status === "queued" || e.draft.status === "running",
		fastInterval: Qo,
		slowInterval: 0,
		revalidateOn: ["advisor.updated", "reset"]
	}), S = x.data?.draft ?? null, C = S && y && S.draft_id === y ? S : null, w = x.error?.code === "draft_not_found", T = C?.status === "expired";
	o(() => {
		y && (w || T) && m(null);
	}, [
		y,
		w,
		T
	]);
	let E = i(() => {
		let i = a.trim();
		t && i && (v(null), g(!0), e.requestPlanDraft(t, {
			space: n,
			objective: i,
			context: es(s),
			project_type: c || null,
			locale: r,
			current: f
		}).then((e) => {
			b.current === t && m({
				repoId: t,
				draftId: e.draft.draft_id,
				objective: i,
				scope: f.scope
			});
		}).catch((e) => {
			b.current === t && v(W(e).code);
		}).finally(() => {
			b.current === t && g(!1);
		}));
	}, [
		e,
		t,
		n,
		a,
		s,
		c,
		r,
		f
	]), D = i(() => {
		m(null), v(null);
	}, []), O = Si(C) && C && C.plan_proposal ? C.plan_proposal : null, [k, A] = d(null), [j, M] = d(!1), [N, P] = d(null), F = l(() => {
		let e = is((k?.repoId === t && k.draftId === y ? k.plan : null)?.scope_meta ?? null);
		return e && O && e.name === O.scope ? e : null;
	}, [
		k,
		t,
		y,
		O
	]), I = l(() => {
		if (!O) return null;
		let e = O.scope ?? f.scope, t = e !== f.scope;
		return {
			scope: e,
			depth: O.depth ?? (t ? F?.depth ?? f.depth : f.depth),
			test_strategy: O.test_strategy ?? (t ? F?.testStrategy ?? null : f.test_strategy),
			review_cap: O.review_cap ?? (t ? F?.reviewCap ?? null : f.review_cap),
			overrides: O.overrides
		};
	}, [
		O,
		F,
		f.scope,
		f.depth,
		f.test_strategy,
		f.review_cap
	]), L = l(() => I ? {
		space: n,
		...I,
		project_type: c || null,
		objective: null,
		label: null,
		context: null
	} : null, [
		I,
		n,
		c
	]), R = L ? Zo({
		repoId: t,
		draftId: y,
		body: L
	}) : "", ee = k?.key === R ? k.plan : null, z = u(L);
	z.current = L;
	let B = u(0);
	o(() => {
		let n = z.current, r = B.current += 1;
		if (A((e) => e ? {
			...e,
			key: ""
		} : null), P(null), !R || !t || !n) {
			A(null), M(!1);
			return;
		}
		M(!0);
		let i = setTimeout(() => {
			e.planPreview(t, n).then((e) => {
				r === B.current && (A({
					key: R,
					repoId: t,
					draftId: y,
					plan: e.plan
				}), P(null));
			}).catch((e) => {
				r === B.current && (A(null), P(W(e).code));
			}).finally(() => {
				r === B.current && M(!1);
			});
		}, $o);
		return () => {
			clearTimeout(i), r === B.current && (B.current += 1);
		};
	}, [
		e,
		t,
		y,
		R
	]);
	let V = O ? O.base_scope ?? O.scope : null, te = p !== null && p.repoId === t ? p : null;
	return {
		draft: C,
		proposal: O,
		effective: I,
		shadow: ee,
		shadowBusy: j,
		shadowError: N,
		pending: h,
		error: _,
		stale: te ? te.objective === a.trim() ? te.scope !== f.scope && V !== f.scope ? "scope" : null : "objective" : null,
		ask: E,
		dismiss: D
	};
}
function os(e) {
	return Object.fromEntries(e.diff.map((e) => [e.slug, "advisor"]));
}
function ss({ advisor: e, available: t, canAsk: n, appliedDraftId: r, dropped: i, onUse: a, onClear: o }) {
	let s = H(), { t: c, has: l } = s, { draft: u, proposal: d, effective: f, shadow: g, shadowBusy: _, shadowError: v, pending: y, error: b, stale: x, ask: S } = e, C = Si(u), w = u && C && d && f ? {
		draft: u,
		usable: C,
		proposal: d,
		effective: f
	} : null, T = y || u?.status === "queued" || u?.status === "running", E = u?.status === "failed" || u?.status === "expired" || u?.status === "ready" && !w, D = c(w ? "advisor.titleDraft" : "advisor.title");
	return /* @__PURE__ */ h("section", {
		className: "studio-block studio-advisor-plan",
		role: "group",
		"aria-label": D,
		"data-state": w ? "ready" : T ? "running" : E ? "failed" : "idle",
		children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
			name: "advisor",
			size: 13
		}), D] }), /* @__PURE__ */ h("div", {
			className: "studio-advisor",
			children: [
				t === !1 ? /* @__PURE__ */ m("p", {
					className: "studio-advisor-copy",
					children: c("advisor.disabled")
				}) : /* @__PURE__ */ m("div", {
					"aria-live": "polite",
					children: w ? /* @__PURE__ */ h(p, { children: [
						/* @__PURE__ */ m("div", {
							className: "studio-advisor-head",
							children: /* @__PURE__ */ m(X, {
								tone: "aim",
								icon: "advisor",
								children: c("advisor.draftNotDecision")
							})
						}),
						w.usable.summary ? /* @__PURE__ */ m("p", {
							className: "studio-advisor-summary",
							children: w.usable.summary
						}) : null,
						/* @__PURE__ */ m("h4", { children: c("wizard.advisor.proposal") }),
						/* @__PURE__ */ h("dl", {
							className: "studio-advisor-dl",
							children: [
								/* @__PURE__ */ m("dt", { children: c("wizard.preset.scope") }),
								/* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m(X, {
									mono: !0,
									children: w.effective.scope
								}) }),
								/* @__PURE__ */ m("dt", { children: c("wizard.preset.depth") }),
								/* @__PURE__ */ m("dd", { children: w.proposal.depth ? /* @__PURE__ */ m(X, {
									mono: !0,
									children: w.proposal.depth
								}) : c("wizard.advisor.keep", { value: w.effective.depth }) }),
								/* @__PURE__ */ m("dt", { children: c("wizard.preset.review") }),
								/* @__PURE__ */ m("dd", { children: w.proposal.review_cap ? /* @__PURE__ */ m(X, {
									mono: !0,
									children: w.proposal.review_cap
								}) : c("wizard.advisor.keep", { value: w.effective.review_cap ?? c("wizard.preset.fromScope") }) }),
								/* @__PURE__ */ m("dt", { children: c("wizard.preset.test") }),
								/* @__PURE__ */ m("dd", { children: w.proposal.test_strategy ? /* @__PURE__ */ m(X, {
									mono: !0,
									children: w.proposal.test_strategy
								}) : c("wizard.advisor.keep", { value: w.effective.test_strategy ?? c("wizard.preset.fromScope") }) })
							]
						}),
						/* @__PURE__ */ m("h4", { children: c("wizard.advisor.stages") }),
						g ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(ja, {
							plan: g,
							hideEmpty: !0,
							origins: os(g)
						}), g.diff.length === 0 && g.issues.length === 0 ? /* @__PURE__ */ m("p", {
							className: "studio-muted",
							children: c("plan.diff.none", { scope: w.effective.scope })
						}) : null] }) : _ ? /* @__PURE__ */ m("p", {
							className: "studio-muted",
							role: "status",
							children: c("plan.busy")
						}) : v ? /* @__PURE__ */ h("p", {
							className: "studio-advisor-copy",
							"data-tone": "warn",
							role: "status",
							children: [
								/* @__PURE__ */ m(Y, {
									name: "warn",
									size: 13
								}),
								c("wizard.error.preview"),
								" ",
								l(`errors.${v}`) ? c(`errors.${v}`) : c("advisor.error.unknown")
							]
						}) : null,
						w.proposal.unresolved.length > 0 ? /* @__PURE__ */ h("p", {
							className: "studio-advisor-copy",
							"data-tone": "warn",
							children: [/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 13
							}), q(s, "wizard.advisor.unresolved", w.proposal.unresolved.length)]
						}) : null,
						/* @__PURE__ */ m(Ti, { result: w.usable }),
						x ? /* @__PURE__ */ h("p", {
							className: "studio-advisor-copy",
							"data-tone": "warn",
							role: "status",
							children: [/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 13
							}), x === "objective" ? c("wizard.advisor.stale.objective") : c("wizard.advisor.stale.scope", { scope: w.effective.scope })]
						}) : null,
						/* @__PURE__ */ h("div", {
							className: "studio-advisor-apply",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								disabled: x !== null || !g || _ || r === w.draft.draft_id,
								onClick: a,
								children: [/* @__PURE__ */ m(Y, {
									name: "check",
									size: 13
								}), c("wizard.advisor.use")]
							}), /* @__PURE__ */ m("span", {
								className: "studio-advisor-note",
								children: c("wizard.advisor.useNote")
							})]
						}),
						/* @__PURE__ */ m("div", {
							className: "studio-advisor-actions",
							children: /* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								disabled: y || !n,
								onClick: S,
								children: [/* @__PURE__ */ m(Y, {
									name: "advisor",
									size: 13
								}), c("wizard.advisor.askAgain")]
							})
						})
					] }) : T ? /* @__PURE__ */ h("p", {
						className: "studio-advisor-copy",
						children: [/* @__PURE__ */ m(Y, {
							name: "clock",
							size: 13
						}), c("advisor.running", { kind: c("advisor.kind.plan_draft") })]
					}) : E ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("p", {
						className: "studio-advisor-copy",
						"data-tone": "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}), u?.status === "expired" ? c("advisor.expired") : l(`advisor.error.${u?.error ?? ""}`) ? c(`advisor.error.${u?.error ?? ""}`) : c("advisor.error.unknown")]
					}), /* @__PURE__ */ m("div", {
						className: "studio-advisor-actions",
						children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							disabled: y || !n,
							onClick: S,
							children: [/* @__PURE__ */ m(Y, {
								name: "advisor",
								size: 13
							}), c("wizard.advisor.askAgain")]
						})
					})] }) : /* @__PURE__ */ h(p, { children: [
						/* @__PURE__ */ m("p", {
							className: "studio-advisor-copy",
							children: c("wizard.advisor.lede")
						}),
						/* @__PURE__ */ m("div", {
							className: "studio-advisor-actions",
							children: /* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								disabled: !n || y,
								onClick: S,
								children: [/* @__PURE__ */ m(Y, {
									name: "advisor",
									size: 13
								}), c("wizard.advisor.ask")]
							})
						}),
						n ? null : /* @__PURE__ */ m("p", {
							className: "studio-help",
							children: c("wizard.advisor.needObjective")
						})
					] })
				}),
				b ? /* @__PURE__ */ h("p", {
					className: "studio-advisor-copy",
					"data-tone": "warn",
					role: "status",
					children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 13
					}), l(`errors.${b}`) ? c(`errors.${b}`) : c("advisor.error.unknown")]
				}) : null,
				r === null ? null : /* @__PURE__ */ h("div", {
					className: "studio-advisor-applied",
					role: "status",
					children: [
						/* @__PURE__ */ h("p", {
							className: "studio-banner",
							"data-tone": "ok",
							children: [/* @__PURE__ */ m(Y, {
								name: "check",
								size: 15
							}), /* @__PURE__ */ m("span", {
								className: "studio-grow",
								children: c("wizard.advisor.applied")
							})]
						}),
						i > 0 ? /* @__PURE__ */ h("p", {
							className: "studio-advisor-copy",
							"data-tone": "warn",
							children: [/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 13
							}), q(s, "wizard.advisor.dropped", i)]
						}) : null,
						/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							onClick: o,
							children: c("wizard.advisor.clear")
						})
					]
				}),
				/* @__PURE__ */ h("p", {
					className: "studio-disclaim",
					children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}), /* @__PURE__ */ m("span", { children: c("advisor.disclaimer") })]
				})
			]
		})]
	});
}
//#endregion
//#region src/wizard/StepPlan.tsx
function cs({ plan: e, busy: t, origins: n, onToggle: r }) {
	let i = H(), { t: a } = i;
	return /* @__PURE__ */ h("div", { children: [/* @__PURE__ */ h("p", {
		className: "studio-consequence",
		children: [/* @__PURE__ */ m(Y, {
			name: "info",
			size: 13
		}), /* @__PURE__ */ m("span", { children: a("wizard.plan.lead") })]
	}), e ? /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ h("div", {
			className: "studio-row studio-wrap studio-plan-summary",
			children: [/* @__PURE__ */ m(X, {
				icon: "check",
				tone: e.valid ? "accent" : "warn",
				children: a("wizard.plan.summary", {
					stages: i.fmt.number(e.exact.stages),
					gates: i.fmt.number(e.exact.gates),
					artifacts: i.fmt.number(e.exact.artifacts)
				})
			}), t ? /* @__PURE__ */ m("span", {
				className: "studio-muted",
				role: "status",
				children: a("plan.busy")
			}) : null]
		}),
		/* @__PURE__ */ m(Na, {
			stages: e.stages,
			onToggle: r,
			busy: t
		}),
		/* @__PURE__ */ m(ja, {
			plan: e,
			origins: n
		})
	] }) : /* @__PURE__ */ m("p", {
		className: "studio-muted",
		children: a(t ? "wizard.preview.busy" : "wizard.preview.none")
	})] });
}
//#endregion
//#region src/wizard/StepPreset.tsx
var ls = [
	"Minimal",
	"Standard",
	"Comprehensive"
], us = [
	"none",
	"advisory",
	"adversarial"
];
function ds({ state: e, scopes: t, meta: n, plan: r, unreadable: i, onPickScope: a, onPatch: o, advisorPanel: s }) {
	let c = H(), { t: l } = c, u = e.scope && !t.includes(e.scope) ? [...t, e.scope] : t, d = r && r.request.scope === e.scope ? l("wizard.preset.scopeSelected", {
		on: c.fmt.number(r.exact.stages),
		total: c.fmt.number(r.graph_stage_count)
	}) : null;
	return /* @__PURE__ */ h("div", {
		className: "studio-wiz-fields",
		children: [
			s,
			/* @__PURE__ */ h(fs, {
				label: l("wizard.preset.scope"),
				help: l("wizard.preset.scopeHelp"),
				children: [
					u.length === 0 ? /* @__PURE__ */ h("p", {
						className: "studio-consequence",
						"data-tone": "warn",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 13
						}), /* @__PURE__ */ m("span", { children: l(i ? "wizard.preset.scopeUnreadable" : "common.loading") })]
					}) : /* @__PURE__ */ m("div", {
						className: "studio-picks",
						children: u.map((t) => {
							let r = t === e.scope;
							return /* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-pick",
								"aria-pressed": r,
								onClick: () => a(t),
								children: [
									/* @__PURE__ */ m("span", {
										className: "studio-pick-l studio-mono",
										children: t
									}),
									/* @__PURE__ */ m("span", {
										className: "studio-pick-d",
										children: r ? d ?? l("common.loading") : l("wizard.preset.scopeUnselected")
									}),
									r && n?.description ? /* @__PURE__ */ m("span", {
										className: "studio-pick-d",
										children: n.description
									}) : null
								]
							}, t);
						})
					}),
					e.scope ? /* @__PURE__ */ m("p", {
						className: "studio-help",
						children: l("wizard.preset.scopeResets")
					}) : null,
					n ? /* @__PURE__ */ h("div", {
						className: "studio-row studio-wrap",
						children: [
							n.depth ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: l("wizard.preset.scopeMetaDepth", { value: n.depth })
							}) : null,
							n.testStrategy ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: l("wizard.preset.scopeMetaTest", { value: n.testStrategy })
							}) : null,
							n.reviewCap ? /* @__PURE__ */ m(X, {
								mono: !0,
								children: l("wizard.preset.scopeMetaReview", { value: n.reviewCap })
							}) : null,
							n.projectOwned ? /* @__PURE__ */ m(X, {
								icon: "repo",
								children: l("wizard.preset.scopeMetaProjectOwned")
							}) : null
						]
					}) : null
				]
			}),
			/* @__PURE__ */ h(fs, {
				label: l("wizard.preset.depth"),
				help: l("wizard.preset.depthHelp"),
				children: [/* @__PURE__ */ m("div", {
					className: "studio-picks",
					children: ls.map((t) => /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-pick",
						"aria-pressed": e.depth === t,
						onClick: () => o({ depth: t }),
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pick-l",
							children: t
						}), /* @__PURE__ */ m("span", {
							className: "studio-pick-d",
							children: l(`wizard.preset.depth.${t}`)
						})]
					}, t))
				}), /* @__PURE__ */ m("p", {
					className: "studio-help",
					children: n?.depth ? l("wizard.preset.fromScopeValue", { value: n.depth }) : n ? l("wizard.preset.fromScopeDepth", { value: e.depth }) : ""
				})]
			}),
			/* @__PURE__ */ m(fs, {
				label: l("wizard.preset.review"),
				help: l("wizard.preset.reviewHelp"),
				children: /* @__PURE__ */ h("div", {
					className: "studio-picks",
					children: [/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-pick",
						"aria-pressed": e.reviewCap === null,
						onClick: () => o({ reviewCap: null }),
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pick-l",
							children: l("wizard.preset.fromScope")
						}), /* @__PURE__ */ m("span", {
							className: "studio-pick-d",
							children: n?.reviewCap ? l("wizard.preset.fromScopeValue", { value: n.reviewCap }) : l("wizard.preset.fromScopeUnset")
						})]
					}), us.map((t) => /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-pick",
						"aria-pressed": e.reviewCap === t,
						onClick: () => o({ reviewCap: t }),
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pick-l studio-mono",
							children: t
						}), /* @__PURE__ */ m("span", {
							className: "studio-pick-d",
							children: l(`wizard.preset.review.${t}`)
						})]
					}, t))]
				})
			}),
			/* @__PURE__ */ m(fs, {
				label: l("wizard.preset.test"),
				help: l("wizard.preset.testHelp"),
				children: /* @__PURE__ */ h("div", {
					className: "studio-picks",
					children: [/* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-pick",
						"aria-pressed": e.testStrategy === null,
						onClick: () => o({ testStrategy: null }),
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pick-l",
							children: l("wizard.preset.fromScope")
						}), /* @__PURE__ */ m("span", {
							className: "studio-pick-d",
							children: n?.testStrategy ? l("wizard.preset.fromScopeValue", { value: n.testStrategy }) : l("wizard.preset.fromScopeUnset")
						})]
					}), ls.map((t) => /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-pick",
						"aria-pressed": e.testStrategy === t,
						onClick: () => o({ testStrategy: t }),
						children: [/* @__PURE__ */ m("span", {
							className: "studio-pick-l",
							children: t
						}), /* @__PURE__ */ m("span", {
							className: "studio-pick-d",
							children: l(`wizard.preset.test.${t}`)
						})]
					}, t))]
				})
			})
		]
	});
}
function fs({ label: e, help: t, children: n }) {
	return /* @__PURE__ */ h("div", {
		className: "studio-field",
		role: "group",
		"aria-label": e,
		children: [
			/* @__PURE__ */ m("span", {
				className: "studio-field-label",
				children: e
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-help",
				children: t
			}),
			n
		]
	});
}
//#endregion
//#region src/wizard/StepReview.tsx
function ps({ plan: e, digest: t, repo: n, origins: r, busy: i, creating: a, workComplete: o, presetComplete: s, error: c }) {
	let l = H(), { t: u } = l, d = n?.counts.blocking_findings ?? 0, f = [];
	return o || f.push(u("wizard.review.blockedFields")), (!s || !e) && f.push(u("wizard.preview.none")), e && !e.valid && f.push(u("wizard.review.blockedInvalid")), d > 0 && f.push(q(l, "wizard.review.blockedFindings", d)), /* @__PURE__ */ h("div", { children: [
		c ? /* @__PURE__ */ h("p", {
			className: "studio-banner",
			"data-tone": "danger",
			role: "alert",
			children: [/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 15
			}), /* @__PURE__ */ h("span", {
				className: "studio-grow",
				children: [
					u("wizard.error.create"),
					" ",
					c.known ? u(`errors.${c.code}`) : c.message
				]
			})]
		}) : null,
		f.length > 0 ? /* @__PURE__ */ m("ul", {
			className: "studio-crit",
			"aria-label": u("wizard.error.create"),
			children: f.map((e) => /* @__PURE__ */ h("li", {
				"data-tone": "warn",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 13
				}), /* @__PURE__ */ m("span", {
					className: "studio-crit-txt",
					children: e
				})]
			}, e))
		}) : null,
		e ? /* @__PURE__ */ h(p, { children: [
			i ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				role: "status",
				children: u("wizard.preview.stale")
			}) : null,
			/* @__PURE__ */ m(Da, { plan: e }),
			/* @__PURE__ */ m(ja, {
				plan: e,
				origins: r
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
					name: "doc",
					size: 13
				}), u("wizard.review.products")] }), e.products.length === 0 ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: u("wizard.review.productsNone")
				}) : /* @__PURE__ */ m("div", {
					className: "studio-row studio-wrap",
					children: e.products.map((e) => /* @__PURE__ */ m(X, {
						mono: !0,
						children: e
					}, e))
				})]
			}),
			/* @__PURE__ */ h("section", {
				className: "studio-block",
				children: [
					/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}), u("wizard.review.plan")] }),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: u("wizard.review.planDigestWhy")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-mono studio-wrap-any studio-digest",
						children: u("wizard.review.planDigest", { digest: t })
					})
				]
			})
		] }) : null,
		/* @__PURE__ */ h("p", {
			className: "studio-consequence",
			children: [/* @__PURE__ */ m(Y, {
				name: "info",
				size: 13
			}), /* @__PURE__ */ m("span", { children: u("wizard.review.consequence") })]
		}),
		a ? /* @__PURE__ */ m("p", {
			className: "studio-muted",
			role: "status",
			children: u("wizard.creating")
		}) : null
	] });
}
//#endregion
//#region src/wizard/StepWork.tsx
function ms({ state: e, repos: t, repo: n, blocked: r, spaces: i, activeSpace: a, derivedLabel: o, labelValid: s, bunMissing: c, bunSearched: l, graphStageCount: u, intentCount: d, onPatch: f, onOpenRepos: p }) {
	let g = H(), { t: _ } = g, v = i.length ? i : a ? [a] : [];
	return /* @__PURE__ */ h("div", {
		className: "studio-wiz-fields",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-repo",
						children: _("wizard.work.repo")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.repoHelp")
					}),
					t.length === 0 ? /* @__PURE__ */ h("p", {
						className: "studio-consequence",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}), /* @__PURE__ */ m("span", { children: _("wizard.work.repoNone") })]
					}) : /* @__PURE__ */ h("select", {
						id: "wizard-repo",
						value: e.repo,
						onChange: (e) => f({
							repo: e.target.value,
							scope: "",
							overrides: {}
						}),
						children: [/* @__PURE__ */ m("option", {
							value: "",
							children: _("wizard.work.repoPick")
						}), t.map((e) => {
							let t = hs(e);
							return /* @__PURE__ */ m("option", {
								value: e.repo_id,
								disabled: t !== null,
								children: t === null ? `${e.label} — ${e.canonical_path}` : _("wizard.work.repoUnusable", {
									label: e.label,
									reason: gs(_, t, e)
								})
							}, e.repo_id);
						})]
					}),
					n && r ? /* @__PURE__ */ h("p", {
						className: "studio-banner",
						"data-tone": "warn",
						role: "status",
						children: [
							/* @__PURE__ */ m(Y, {
								name: "warn",
								size: 15
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-grow",
								children: _("wizard.work.repoBlocked", { reason: gs(_, r, n) })
							}),
							/* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								onClick: p,
								children: _("wizard.work.repoBlockedOpen")
							})
						]
					}) : null
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-space",
						children: _("wizard.work.space")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.spaceHelp")
					}),
					v.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: _("wizard.work.spaceUnknown")
					}) : /* @__PURE__ */ m("select", {
						id: "wizard-space",
						value: a,
						disabled: !0,
						onChange: () => void 0,
						children: v.map((e) => /* @__PURE__ */ m("option", {
							value: e,
							children: _(e === a ? "wizard.work.spaceActive" : "wizard.work.spaceInactive", { space: e })
						}, e))
					})
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-objective",
						children: _("wizard.work.objective")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.objectiveHelp")
					}),
					/* @__PURE__ */ m("input", {
						id: "wizard-objective",
						type: "text",
						value: e.objective,
						placeholder: _("wizard.work.objectivePlaceholder"),
						"aria-describedby": "wizard-objective-help",
						onChange: (e) => f({ objective: e.target.value })
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						id: "wizard-objective-help",
						children: e.objective.trim() ? "" : _("wizard.work.objectiveRequired")
					})
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-context",
						children: _("wizard.work.context")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.contextHelp")
					}),
					/* @__PURE__ */ m("textarea", {
						id: "wizard-context",
						value: e.context,
						placeholder: _("wizard.work.contextPlaceholder"),
						onChange: (e) => f({ context: e.target.value })
					})
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-label",
						children: _("wizard.work.label")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.labelHelp")
					}),
					/* @__PURE__ */ m("input", {
						id: "wizard-label",
						type: "text",
						value: e.label,
						placeholder: _("wizard.work.labelPlaceholder"),
						"aria-invalid": s ? "false" : "true",
						"aria-describedby": "wizard-label-state",
						onChange: (e) => f({ label: e.target.value })
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						id: "wizard-label-state",
						children: e.label.trim() ? s ? "" : _("wizard.work.labelInvalid") : o ? _("wizard.work.labelDerived", { label: o }) : _("wizard.work.labelUndeducible")
					})
				]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("label", {
						htmlFor: "wizard-project-type",
						children: _("wizard.work.projectType")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.projectTypeHelp")
					}),
					/* @__PURE__ */ h("select", {
						id: "wizard-project-type",
						value: e.projectType,
						onChange: (e) => f({ projectType: e.target.value }),
						children: [
							/* @__PURE__ */ m("option", {
								value: "",
								children: _("wizard.work.projectTypeUnset")
							}),
							/* @__PURE__ */ m("option", {
								value: "Greenfield",
								children: "Greenfield"
							}),
							/* @__PURE__ */ m("option", {
								value: "Brownfield",
								children: "Brownfield"
							})
						]
					})
				]
			}),
			n ? /* @__PURE__ */ h("div", {
				className: "studio-field",
				children: [
					/* @__PURE__ */ m("span", {
						className: "studio-field-label",
						children: _("wizard.work.signals")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: _("wizard.work.signalsHelp")
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-row studio-wrap",
						children: [
							n.install.engine_version ? /* @__PURE__ */ m(X, {
								tone: "ok",
								icon: "check",
								children: _("wizard.work.signal.engine", { version: n.install.engine_version })
							}) : /* @__PURE__ */ m(X, {
								icon: "warn",
								tone: "warn",
								children: _("wizard.work.signal.engineUnknown")
							}),
							n.install.drift_count > 0 ? /* @__PURE__ */ m(X, {
								tone: "warn",
								icon: "warn",
								children: `${we(g, n.install.drift_count)} ${_("wizard.work.signal.drift")}`
							}) : null,
							n.git ? n.git.available ? /* @__PURE__ */ m(X, {
								icon: "git",
								children: `${_("wizard.work.signal.gitBranch", { branch: n.git.branch ?? "—" })} · ${n.git.dirty ? q(g, "wizard.work.signal.gitDirty", n.git.dirty_files) : _("wizard.work.signal.gitClean")}`
							}) : /* @__PURE__ */ m(X, {
								icon: "git",
								children: _("wizard.work.signal.gitUnavailable")
							}) : null,
							u === null ? null : /* @__PURE__ */ m(X, {
								icon: "map",
								children: _("wizard.work.signal.stages", { n: g.fmt.number(u) })
							}),
							d === null ? null : /* @__PURE__ */ m(X, {
								icon: "intent",
								children: q(g, "wizard.work.signal.intents", d)
							})
						]
					}),
					c ? /* @__PURE__ */ h("p", {
						className: "studio-banner",
						"data-tone": "danger",
						role: "status",
						children: [/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 15
						}), /* @__PURE__ */ h("span", {
							className: "studio-grow",
							children: [_("errors.bun_missing"), l.length > 0 ? /* @__PURE__ */ m("span", {
								className: "studio-subpath",
								children: _("errors.bun_missing_searched", { locations: l.join(_("shell.format.listJoin")) })
							}) : null]
						})]
					}) : null
				]
			}) : null
		]
	});
}
function hs(e) {
	return e.availability === "available" ? e.install.status === "recovery_required" ? "recovery_required" : e.install.status === "not_installed" ? "not_installed" : null : "availability";
}
function gs(e, t, n) {
	return e(t === "availability" ? `enum.availability.${n.availability}` : t === "recovery_required" ? "wizard.work.repoRecovery" : "wizard.work.repoNotInstalled");
}
//#endregion
//#region src/wizard/WizardView.tsx
var _s = [
	"work",
	"preset",
	"plan",
	"review"
], vs = {
	repo: "",
	space: "",
	objective: "",
	context: "",
	label: "",
	projectType: "",
	scope: "",
	depth: "Standard",
	testStrategy: null,
	reviewCap: null,
	overrides: {}
}, ys = /^[a-z0-9]+(?:-[a-z0-9]+){0,2}$/, bs = 3;
function xs(e) {
	return (e.toLowerCase().match(/[a-z0-9]+/g) ?? []).slice(0, bs).join("-");
}
function Ss(e) {
	return e.trim() || null;
}
function Cs(e, t, n) {
	return {
		space: t || "default",
		scope: e.scope,
		depth: e.depth,
		test_strategy: e.testStrategy,
		review_cap: e.reviewCap,
		project_type: e.projectType || null,
		overrides: e.overrides,
		objective: n ? Ss(e.objective) : null,
		label: n ? Ss(e.label) : null,
		context: n ? Ss(e.context) : null
	};
}
var ws = /* @__PURE__ */ new Set([
	"unknown_stage",
	"required_stage_disabled",
	"frozen_stage",
	"behind_cursor"
]);
function Ts(e) {
	if (e.code === "dependency_missing") {
		let t = e.params.stage;
		return typeof t == "string" ? [t] : [];
	}
	return ws.has(e.code) ? e.slugs : [];
}
function Es(e) {
	let t = (e?.issues.find((e) => e.code === "scope_unknown"))?.params.known;
	return Array.isArray(t) ? t.filter((e) => typeof e == "string") : [];
}
function Ds(e, t) {
	return {
		...e.request,
		objective: Ss(t.objective),
		label: Ss(t.label),
		context: Ss(t.context)
	};
}
function Os(e, t) {
	let n = Zo({
		request: Ds(e, t),
		stages: e.stages.map((e) => [e.slug, e.enabled]),
		graph_stage_count: e.graph_stage_count
	}), r = new Uint8Array(n.length);
	for (let e = 0; e < n.length; e += 1) r[e] = n.charCodeAt(e) & 255;
	return Yo(r);
}
function ks(e) {
	return e.availability === "available" ? e.install.status === "recovery_required" ? "recovery_required" : e.install.status === "not_installed" ? "not_installed" : null : "availability";
}
function As({ route: e, go: t }) {
	let n = H(), { t: r } = n, { api: a, repos: s, health: c, settings: f } = jl(), p = f.data ? f.data.settings.advisor.enabled && f.data.capabilities.advisor.available : null, g = s.data?.repos ?? [], [_, v] = d("work"), [y, b] = d(() => ({
		...vs,
		repo: e.repo
	})), [x, S] = d(null), [C, w] = d(null), [T, E] = d(!1), [D, O] = d(!1), [k, A] = d(null), [j, M] = d(null), [N, P] = d(null), [F, I] = d(!1), [L, R] = d([]), [ee, z] = d(null), [B, V] = d({}), [te, ne] = d(0), re = g.find((e) => e.repo_id === y.repo) ?? null, ie = re ? ks(re) : null, ae = J(re && !ie ? `wizard-spaces:${re.repo_id}` : null, i((e) => a.intents(y.repo, {}, { signal: e }), [a, y.repo]), {
		interval: 0,
		revalidateOn: [
			"intent.updated",
			"repo.updated",
			"reset"
		]
	}), oe = ae.data?.active_space ?? "", se = ae.data?.spaces ?? [], ce = u(0), le = u(""), ue = u({}), de = l(() => re && !ie ? Cs(y, oe, !1) : null, [
		re,
		ie,
		y,
		oe
	]), fe = de ? Zo({
		repo: y.repo,
		body: de
	}) : "", pe = x?.repo === y.repo ? x.plan : null, me = x?.key === fe ? pe : null, he = u(de);
	he.current = de, o(() => {
		let e = ce.current += 1, t = he.current;
		if (S((e) => e ? {
			...e,
			key: ""
		} : null), w(null), !fe || !y.repo || !t) {
			S(null), E(!1);
			return;
		}
		let n = setTimeout(() => {
			E(!0), a.planPreview(y.repo, t).then((n) => {
				if (e !== ce.current) return;
				let r = is(n.plan.scope_meta ?? null);
				if (r?.name === t.scope && r.name !== le.current) {
					le.current = r.name;
					let e = ue.current;
					b((t) => t.repo !== y.repo || t.scope !== r.name ? t : {
						...t,
						depth: e.depth ? t.depth : r.depth ?? t.depth,
						testStrategy: e.testStrategy ? t.testStrategy : r.testStrategy,
						reviewCap: e.reviewCap ? t.reviewCap : r.reviewCap
					});
				}
				S({
					key: fe,
					repo: y.repo,
					plan: n.plan
				}), w(null);
				let i = Es(n.plan);
				i.length && R(i);
			}).catch((t) => {
				e === ce.current && w(t instanceof U ? t : new U("internal_error", String(t), {}, 0));
			}).finally(() => {
				e === ce.current && E(!1);
			});
		}, 200);
		return () => {
			clearTimeout(n), e === ce.current && (ce.current += 1);
		};
	}, [
		a,
		fe,
		y.repo
	]);
	let ge = l(() => {
		let e = is(pe?.scope_meta ?? null);
		return e?.name === y.scope ? e : null;
	}, [pe, y.scope]), _e = l(() => ({
		scope: y.scope,
		depth: y.depth,
		test_strategy: y.testStrategy,
		review_cap: y.reviewCap,
		overrides: y.overrides
	}), [
		y.scope,
		y.depth,
		y.testStrategy,
		y.reviewCap,
		y.overrides
	]), ve = as({
		api: a,
		repoId: y.repo,
		space: oe || "default",
		locale: n.locale,
		objective: y.objective,
		context: y.context,
		projectType: y.projectType,
		current: _e
	}), { dismiss: W } = ve, ye = i((e) => {
		A(null), e.repo !== void 0 && (le.current = "", ue.current = {}, z(null), V({}), ne(0));
		for (let t of [
			"depth",
			"testStrategy",
			"reviewCap"
		]) e[t] !== void 0 && (ue.current = {
			...ue.current,
			[t]: !0
		});
		b((t) => ({
			...t,
			...e
		}));
	}, []), be = i((e) => {
		le.current = "", ue.current = {}, A(null), V({}), ne(0), b((t) => ({
			...t,
			scope: e,
			overrides: {}
		}));
	}, []), xe = i((e, t) => {
		let n = pe?.stages.find((t) => t.slug === e);
		A(null), V((t) => {
			if (!(e in t)) return t;
			let n = { ...t };
			return delete n[e], n;
		}), b((r) => {
			let i = { ...r.overrides };
			return n && n.in_grid === t ? delete i[e] : i[e] = t, {
				...r,
				overrides: i
			};
		});
	}, [pe]), Se = () => {
		let e = ve.effective, t = ve.draft, n = ve.shadow;
		if (!e || !t || !n || ve.shadowBusy || ve.stale !== null) return;
		let r = new Set(n.issues.flatMap((e) => Ts(e))), i = new Set(Object.keys(e.overrides).filter((t) => r.has(t) && e.overrides[t] !== y.overrides[t])), a = Object.fromEntries(Object.entries(e.overrides).filter(([e]) => !i.has(e)));
		ne(i.size), le.current = e.scope, z((e) => ({
			draftId: t.draft_id,
			before: e?.before ?? {
				scope: y.scope,
				depth: y.depth,
				testStrategy: y.testStrategy,
				reviewCap: y.reviewCap,
				overrides: y.overrides
			}
		})), V(Object.fromEntries(Object.keys(a).map((e) => [e, "advisor"]))), A(null), b((t) => ({
			...t,
			scope: e.scope,
			depth: e.depth,
			testStrategy: e.test_strategy,
			reviewCap: e.review_cap,
			overrides: a
		}));
	}, Ce = () => {
		if (!ee) return;
		let { before: e } = ee;
		le.current = e.scope, z(null), V({}), ne(0), A(null), b((t) => ({
			...t,
			...e
		}));
	}, G = pe ? Os(pe, y) : "", we = xs(y.objective), Te = y.label.trim() || we, Ee = ys.test(Te) && Te.length <= 120, De = !!re && !ie && y.objective.trim().length > 0 && Ee && !!oe && !ae.error, K = y.scope.trim().length > 0, Oe = i(async () => {
		if (me?.valid && re && De && K && !T && !D) {
			O(!0), A(null);
			try {
				let e = {
					...Cs(y, oe, !0),
					confirm_plan_digest: G
				}, t = await a.createIntent(re.repo_id, e);
				M(t), W(), z(null), V({}), ne(0);
			} catch (e) {
				let t = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
				A(t), t.details.intent_created === !0 && typeof t.details.intent_key == "string" && typeof t.details.space == "string" && typeof t.details.intent_dir == "string" && (P({
					repoId: re.repo_id,
					intentKey: t.details.intent_key,
					space: t.details.space,
					intentDir: t.details.intent_dir,
					failedPhase: typeof t.details.creation_failed_phase == "string" ? t.details.creation_failed_phase : null
				}), W());
			} finally {
				O(!1);
			}
		}
	}, [
		a,
		me,
		re,
		y,
		oe,
		G,
		De,
		K,
		T,
		D,
		W
	]);
	if (N) {
		let e = N.failedPhase === "plan_composition", n = e ? "wizard.partial.planComposition.title" : "wizard.partial.title", i = () => t({
			view: "intents",
			repo: N.repoId,
			space: N.space,
			intent: N.intentKey
		}), o = async () => {
			if (!(e || F)) {
				I(!0), A(null);
				try {
					await a.compileRuntime(N.repoId, N.intentKey), i();
				} catch (e) {
					A(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
				} finally {
					I(!1);
				}
			}
		};
		return /* @__PURE__ */ h("section", {
			className: "studio-scroll studio-wiz",
			"aria-label": r(n),
			children: [
				/* @__PURE__ */ m("h2", { children: r(n) }),
				/* @__PURE__ */ m("p", { children: r(e ? "wizard.partial.planComposition.body" : "wizard.partial.body", { intent: N.intentDir }) }),
				e ? null : /* @__PURE__ */ m("p", { children: r("wizard.partial.activate") }),
				k ? /* @__PURE__ */ m("p", {
					role: "alert",
					children: k.message
				}) : null,
				/* @__PURE__ */ h("div", {
					className: "studio-row",
					children: [e ? null : /* @__PURE__ */ m("button", {
						className: "studio-btn",
						disabled: F,
						onClick: () => void o(),
						children: r(F ? "wizard.partial.repairing" : "wizard.partial.retry")
					}), /* @__PURE__ */ m("button", {
						className: "studio-btn",
						disabled: F,
						onClick: i,
						children: r(e ? "wizard.partial.planComposition.open" : "wizard.partial.open")
					})]
				})
			]
		});
	}
	if (j) return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ m("div", {
			className: "studio-wiz",
			children: /* @__PURE__ */ m(js, {
				answer: j,
				repoLabel: re?.label ?? y.repo,
				onAgain: () => {
					M(null), S(null), v("work"), le.current = "", ue.current = {}, b({
						...vs,
						repo: y.repo
					});
				},
				onOpen: () => {
					let e = j.intent.intent_key;
					t({
						view: "intents",
						repo: j.intent.repo_id,
						space: j.intent.space,
						intent: e
					});
				}
			})
		})
	});
	let ke = _s.indexOf(_), Ae = c.data ? c.data.tools.bun.found === !1 : !1, je = c.data ? c.data.tools.bun.searched : [];
	return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ h("div", {
			className: "studio-wiz",
			children: [
				/* @__PURE__ */ m("h1", { children: r("wizard.title") }),
				/* @__PURE__ */ m("p", {
					className: "studio-lede",
					children: r("wizard.lede")
				}),
				/* @__PURE__ */ m("ol", {
					className: "studio-wiz-steps",
					"aria-label": r("wizard.a11y.stepper"),
					children: _s.map((e, t) => /* @__PURE__ */ h("li", { children: [t > 0 ? /* @__PURE__ */ m("span", {
						className: "studio-wiz-step-sep",
						"aria-hidden": "true"
					}) : null, /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-wiz-step",
						"data-state": t < ke ? "done" : t === ke ? "now" : "todo",
						...t === ke ? { "aria-current": "step" } : {},
						disabled: D,
						onClick: () => v(e),
						children: [
							/* @__PURE__ */ m("span", {
								className: "studio-wiz-step-n studio-mono",
								"aria-hidden": "true",
								children: t < ke ? /* @__PURE__ */ m(Y, {
									name: "check",
									size: 11,
									strokeWidth: 2
								}) : t + 1
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-sr",
								children: r("wizard.step.a11y", {
									n: t + 1,
									label: r(`wizard.step.${e}`)
								})
							}),
							/* @__PURE__ */ m("span", {
								"aria-hidden": "true",
								children: r(`wizard.step.${e}`)
							})
						]
					})] }, e))
				}),
				ae.error ? /* @__PURE__ */ h("div", {
					className: "studio-banner",
					"data-tone": "danger",
					role: "alert",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 15
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-grow",
							children: ae.error.known ? r(`errors.${ae.error.code}`) : ae.error.message
						}),
						/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: ae.stale || D,
							onClick: () => void ae.refresh(),
							children: r("common.retry")
						})
					]
				}) : null,
				C ? /* @__PURE__ */ h("p", {
					className: "studio-banner",
					"data-tone": "danger",
					role: "alert",
					children: [/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 15
					}), /* @__PURE__ */ h("span", {
						className: "studio-grow",
						children: [
							r("wizard.error.preview"),
							" ",
							C.known ? r(`errors.${C.code}`) : C.message
						]
					})]
				}) : null,
				_ === "work" ? /* @__PURE__ */ m(ms, {
					state: y,
					repos: g,
					repo: re,
					blocked: ie,
					spaces: se,
					activeSpace: oe,
					derivedLabel: we,
					labelValid: Ee,
					bunMissing: Ae,
					bunSearched: je,
					graphStageCount: pe?.graph_stage_count ?? null,
					intentCount: ae.data?.intents.length ?? null,
					onPatch: ye,
					onOpenRepos: () => t({ view: "repos" })
				}) : null,
				_ === "preset" ? /* @__PURE__ */ m(ds, {
					state: y,
					scopes: L,
					meta: ge,
					plan: pe,
					unreadable: !!C,
					onPickScope: be,
					onPatch: ye,
					advisorPanel: /* @__PURE__ */ m(ss, {
						advisor: ve,
						available: p,
						canAsk: !!re && !ie && y.objective.trim().length > 0,
						appliedDraftId: ee?.draftId ?? null,
						dropped: te,
						onUse: Se,
						onClear: Ce
					})
				}) : null,
				_ === "plan" ? /* @__PURE__ */ m(cs, {
					plan: pe,
					busy: T,
					origins: B,
					onToggle: xe
				}) : null,
				_ === "review" ? /* @__PURE__ */ m(ps, {
					plan: pe,
					digest: G,
					repo: re,
					origins: B,
					busy: T,
					creating: D,
					workComplete: De,
					presetComplete: K,
					error: k
				}) : null,
				/* @__PURE__ */ h("div", {
					className: "studio-wiz-bar",
					children: [
						/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: ke === 0 || D,
							onClick: () => v(_s[Math.max(0, ke - 1)]),
							children: r("wizard.nav.back")
						}),
						_ === "review" ? /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-primary",
							disabled: !me?.valid || !De || !K || D || T,
							onClick: () => void Oe(),
							children: [/* @__PURE__ */ m(Y, {
								name: "check",
								size: 13
							}), r("wizard.nav.create")]
						}) : /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn studio-btn-primary",
							disabled: _ === "work" && !De || _ === "preset" && !K,
							onClick: () => v(_s[Math.min(_s.length - 1, ke + 1)]),
							children: r("wizard.nav.continue")
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-muted studio-wiz-count",
							children: r("wizard.step.of", { n: ke + 1 })
						}),
						T ? /* @__PURE__ */ m("span", {
							className: "studio-muted",
							role: "status",
							children: r("wizard.preview.busy")
						}) : null
					]
				})
			]
		})
	});
}
function js({ answer: e, repoLabel: t, onOpen: n, onAgain: r }) {
	let { t: i } = H(), a = e.intent;
	return /* @__PURE__ */ h("section", {
		className: "studio-panel",
		role: "status",
		children: [
			/* @__PURE__ */ h("h1", { children: [/* @__PURE__ */ m(Y, {
				name: "check",
				size: 18
			}), i("wizard.created.title")] }),
			/* @__PURE__ */ m("p", { children: i("wizard.created.body") }),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [
					/* @__PURE__ */ m(X, {
						mono: !0,
						icon: "intent",
						children: i("wizard.created.intent", {
							intent: a.intent_key,
							repo: t
						})
					}),
					a.verified ? /* @__PURE__ */ m(X, {
						tone: "ok",
						icon: "check",
						children: i("wizard.created.verified")
					}) : /* @__PURE__ */ m(X, {
						tone: "warn",
						icon: "warn",
						children: i("wizard.created.unverified")
					}),
					/* @__PURE__ */ m(X, {
						mono: !0,
						children: i("wizard.created.transaction", { id: e.transaction_id })
					})
				]
			}),
			/* @__PURE__ */ h("p", {
				className: "studio-consequence",
				children: [/* @__PURE__ */ m(Y, {
					name: "info",
					size: 13
				}), /* @__PURE__ */ m("span", { children: i("wizard.review.consequence") })]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-row studio-wrap",
				children: [/* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn studio-btn-primary",
					onClick: n,
					children: [/* @__PURE__ */ m(Y, {
						name: "intent",
						size: 13
					}), i("wizard.created.next")]
				}), /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					onClick: r,
					children: [/* @__PURE__ */ m(Y, {
						name: "plus",
						size: 13
					}), i("wizard.created.again")]
				})]
			})
		]
	});
}
//#endregion
//#region src/views/new-intent/index.tsx
var Ms = /* @__PURE__ */ O({ default: () => As });
//#endregion
//#region src/intents/SpaceControls.tsx
function Ns(e) {
	return /* @__PURE__ */ m(Ps, { ...e }, e.repoId);
}
function Ps({ api: e, repoId: t, repoLabel: n, onChanged: r }) {
	let { t: i } = H(), [a, s] = d(null), [c, l] = d(""), [f, p] = d(""), [g, _] = d(!0), [v, y] = d(null), [b, x] = d(!1), [S, C] = d(null), [w, T] = d(""), E = u(!0), D = u(0), O = u(null);
	o(() => {
		v && O.current?.focus();
	}, [v]);
	let k = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(f) && f.length <= 48 && ![
		"help",
		"list",
		"switch",
		"create",
		"archive",
		"rename",
		"show",
		"birth"
	].includes(f) && !a?.spaces.includes(f);
	function A(e) {
		s(e), l(e.active_space);
	}
	async function j(n) {
		let r = ++D.current;
		_(!0), y(null), C(null);
		try {
			let i = await e.spaces(t, { signal: n });
			E.current && r === D.current && A(i);
		} catch (e) {
			!ve(e) && E.current && r === D.current && C(W(e));
		} finally {
			E.current && r === D.current && _(!1);
		}
	}
	o(() => {
		E.current = !0;
		let e = new AbortController();
		return j(e.signal), () => {
			E.current = !1, D.current += 1, e.abort();
		};
	}, [e, t]);
	async function M() {
		if (!v || b) return;
		let n = v;
		x(!0), C(null), T("");
		try {
			let a = n === "create" ? await e.createSpace(t, f) : await e.switchSpace(t, c);
			if (!E.current) return;
			A(a), n === "create" && p(""), T(i(n === "create" ? "workspace.spaces.created" : "workspace.spaces.switched")), r?.();
		} catch (n) {
			if (!E.current) return;
			C(W(n));
			try {
				let n = await e.spaces(t);
				E.current && A(n);
			} catch {}
		} finally {
			E.current && (x(!1), y(null));
		}
	}
	return /* @__PURE__ */ h("section", {
		className: "studio-panel studio-workspace-controls",
		"aria-label": i("workspace.spaces.title"),
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-panel-head",
				children: [/* @__PURE__ */ h("h2", { children: [i("workspace.spaces.title"), n ? ` · ${n}` : ""] }), /* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-btn",
					disabled: b || g,
					onClick: () => void j(),
					children: i("workspace.spaces.refresh")
				})]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: i("workspace.spaces.description")
			}),
			a ? /* @__PURE__ */ m("p", {
				className: "studio-wrap-any",
				children: i("workspace.spaces.active", { name: a.active_space })
			}) : null,
			g ? /* @__PURE__ */ m("p", {
				role: "status",
				children: i("common.loading")
			}) : null,
			S ? /* @__PURE__ */ m("p", {
				className: "studio-failure-detail",
				role: "alert",
				children: S.known ? i(`errors.${S.code}`) : S.message
			}) : null,
			w ? /* @__PURE__ */ m("p", {
				role: "status",
				children: w
			}) : null,
			/* @__PURE__ */ h("fieldset", {
				className: "studio-workspace-group",
				disabled: g || b || !a,
				children: [/* @__PURE__ */ m("legend", {
					className: "studio-field-label",
					children: i("workspace.spaces.switch")
				}), /* @__PURE__ */ h("div", {
					className: "studio-workspace-fields",
					children: [/* @__PURE__ */ h("label", {
						className: "studio-field",
						children: [/* @__PURE__ */ m("span", {
							className: "studio-field-label",
							children: i("workspace.spaces.target")
						}), /* @__PURE__ */ m("select", {
							className: "studio-input",
							value: c,
							onChange: (e) => {
								l(e.target.value), y(null), T("");
							},
							children: a?.spaces.map((e) => /* @__PURE__ */ m("option", {
								value: e,
								children: e
							}, e))
						})]
					}), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: !c || c === a?.active_space,
						onClick: () => y("switch"),
						children: i("workspace.spaces.switch")
					})]
				})]
			}),
			/* @__PURE__ */ h("fieldset", {
				className: "studio-workspace-group",
				disabled: g || b || !a,
				children: [
					/* @__PURE__ */ m("legend", {
						className: "studio-field-label",
						children: i("workspace.spaces.create")
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-workspace-fields",
						children: [/* @__PURE__ */ h("label", {
							className: "studio-field",
							children: [/* @__PURE__ */ m("span", {
								className: "studio-field-label",
								children: i("workspace.spaces.name")
							}), /* @__PURE__ */ m("input", {
								className: "studio-input studio-mono",
								value: f,
								maxLength: 48,
								onChange: (e) => {
									p(e.target.value), y(null), T("");
								}
							})]
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: !k,
							onClick: () => y("create"),
							children: i("workspace.spaces.create")
						})]
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-lede",
						children: i("workspace.spaces.nameHint")
					})
				]
			}),
			v ? /* @__PURE__ */ h("fieldset", {
				className: "studio-confirm",
				disabled: b,
				children: [
					/* @__PURE__ */ m("legend", {
						className: "studio-field-label",
						children: i("workspace.spaces.confirmTitle")
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-wrap-any",
						children: i(v === "create" ? "workspace.spaces.confirmCreate" : "workspace.spaces.confirmSwitch", { name: v === "create" ? f : c })
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-confirm-actions",
						children: [/* @__PURE__ */ m("button", {
							ref: O,
							type: "button",
							className: "studio-btn",
							"data-variant": "primary",
							onClick: () => void M(),
							children: i("workspace.spaces.confirm")
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => y(null),
							children: i("common.cancel")
						})]
					})
				]
			}) : null
		]
	});
}
//#endregion
//#region src/repos/PreflightReport.tsx
function Q({ title: e, icon: t, children: n, id: r }) {
	return /* @__PURE__ */ h("section", {
		className: "studio-block",
		...r ? { id: r } : {},
		children: [/* @__PURE__ */ h("h3", { children: [/* @__PURE__ */ m(Y, {
			name: t,
			size: 13
		}), e] }), n]
	});
}
function Fs({ rows: e }) {
	let t = H();
	return /* @__PURE__ */ m("dl", {
		className: "studio-facts",
		children: e.map((e) => /* @__PURE__ */ h("div", {
			className: "studio-fact",
			children: [/* @__PURE__ */ m("dt", { children: e.label }), /* @__PURE__ */ m("dd", {
				className: e.mono ? "studio-mono studio-wrap-any" : void 0,
				children: e.value === null || e.value === void 0 || e.value === "" ? G(t) : e.value
			})]
		}, e.key))
	});
}
function $({ children: e, tone: t }) {
	return /* @__PURE__ */ h("p", {
		className: "studio-note",
		...t ? { "data-tone": t } : {},
		children: [
			/* @__PURE__ */ m(Y, {
				name: t ? "warn" : "info",
				size: 13
			}),
			" ",
			e
		]
	});
}
function Is(e, t) {
	let n = `errors.${t.code}`;
	return e.has(n) ? e.t(n) : t.message;
}
function Ls({ error: e, reassure: t }) {
	let n = H();
	return /* @__PURE__ */ h("p", {
		className: "studio-failure-detail",
		role: "status",
		children: [
			/* @__PURE__ */ m(Y, {
				name: "warn",
				size: 13
			}),
			" ",
			Is(n, e),
			t ? ` ${t}` : ""
		]
	});
}
function Rs(e, t) {
	return e.has(t.message_key) ? e.t(t.message_key, Me(e, {
		key: t.message_key,
		params: t.params
	})) : e.t("repos.finding.fallback", { code: t.code });
}
var zs = {
	blocking: "danger",
	warn: "warn",
	info: "info"
};
function Bs({ findings: e }) {
	let t = H(), { t: n } = t;
	return /* @__PURE__ */ m("ul", {
		className: "studio-plain-list",
		children: e.map((e, r) => /* @__PURE__ */ h("li", {
			className: "studio-finding",
			"data-severity": e.severity,
			children: [
				/* @__PURE__ */ h("div", {
					className: "studio-row",
					children: [/* @__PURE__ */ m(X, {
						tone: zs[e.severity],
						icon: e.severity === "blocking" ? "recovery" : e.severity === "warn" ? "warn" : "info",
						children: n(`enum.findingSeverity.${e.severity}`)
					}), /* @__PURE__ */ m("span", {
						className: "studio-mono studio-muted",
						children: e.code
					})]
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-finding-text",
					children: Rs(t, e)
				}),
				e.evidence.length > 0 ? /* @__PURE__ */ m("p", {
					className: "studio-subpath",
					children: e.evidence.map((e) => `${e.kind}: ${e.ref}`).join("  ·  ")
				}) : null
			]
		}, `${e.code}-${r}`))
	});
}
function Vs({ dirs: e }) {
	let t = H(), { t: n } = t;
	return /* @__PURE__ */ m("ul", {
		className: "studio-plain-list",
		children: e.map((e) => /* @__PURE__ */ h("li", {
			className: "studio-row studio-chiplist",
			children: [
				/* @__PURE__ */ m("span", {
					className: "studio-mono",
					children: e.dir
				}),
				/* @__PURE__ */ m(X, {
					mono: !0,
					icon: "install",
					children: e.engine_version ?? G(t)
				}),
				e.engine_state_version === null ? null : /* @__PURE__ */ m(X, {
					mono: !0,
					children: n("repos.engine.stateVersion", { version: e.engine_state_version })
				}),
				e.stage_count === null ? null : /* @__PURE__ */ m(X, {
					mono: !0,
					children: q(t, "repos.engine.stages", e.stage_count)
				}),
				/* @__PURE__ */ m(X, {
					tone: e.has_utility ? "ok" : "warn",
					icon: e.has_utility ? "check" : "warn",
					children: n(e.has_utility ? "repos.card.harness.utility" : "repos.card.harness.noUtility")
				})
			]
		}, e.dir))
	});
}
function Hs({ report: e, duplicateLabel: t, children: n }) {
	let r = H(), { t: i } = r, a = [
		{
			key: "input",
			label: i("repos.preflight.input"),
			value: e.path_input,
			mono: !0
		},
		{
			key: "canonical",
			label: i("repos.preflight.canonical"),
			value: e.canonical_path,
			mono: !0
		},
		{
			key: "identity",
			label: i("repos.preflight.identity"),
			value: e.identity ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("span", {
				className: "studio-mono studio-wrap-any",
				children: e.identity.identity_str
			}), /* @__PURE__ */ h("span", {
				className: "studio-chiplist",
				children: [
					/* @__PURE__ */ m(X, {
						tone: e.identity.provable ? "ok" : "warn",
						icon: e.identity.provable ? "check" : "warn",
						children: i(e.identity.provable ? "repos.preflight.identityProvable" : "repos.preflight.identityUnprovable")
					}),
					e.identity.st_dev === null || e.identity.st_ino === null ? null : /* @__PURE__ */ m(X, {
						mono: !0,
						children: i("repos.preflight.inode", {
							dev: e.identity.st_dev,
							ino: e.identity.st_ino
						})
					}),
					e.identity.git_common_dir ? /* @__PURE__ */ m(X, {
						mono: !0,
						icon: "git",
						title: i("repos.card.identity.gitCommonDir"),
						children: e.identity.git_common_dir
					}) : null
				]
			})] }) : null
		},
		{
			key: "platform",
			label: i("repos.card.identity.platform"),
			value: e.platform,
			mono: !0
		},
		{
			key: "git",
			label: i("repos.preflight.git"),
			value: e.git ? /* @__PURE__ */ h("span", {
				className: "studio-chiplist",
				children: [/* @__PURE__ */ m(X, {
					icon: "git",
					children: e.git.is_repo ? i("repos.preflight.gitRepo", { branch: e.git.branch ?? G(r) }) : i("repos.preflight.gitNotRepo")
				}), e.git.is_repo ? /* @__PURE__ */ m(X, {
					tone: e.git.dirty ? "warn" : "ok",
					icon: e.git.dirty ? "warn" : "check",
					children: i(e.git.dirty ? "repos.preflight.gitDirty" : "repos.preflight.gitClean")
				}) : null]
			}) : null
		},
		{
			key: "bun",
			label: i("repos.preflight.bun"),
			value: e.bun ? e.bun.found ? /* @__PURE__ */ m(X, {
				tone: "ok",
				icon: "check",
				mono: !0,
				children: i("repos.preflight.bunFound", {
					version: e.bun.version ?? G(r),
					path: e.bun.path ?? G(r)
				})
			}) : /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m(X, {
					tone: "warn",
					icon: "warn",
					children: i("repos.preflight.bunMissing")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-help",
					children: i("errors.bun_missing")
				}),
				e.bun.searched.length > 0 ? /* @__PURE__ */ m("span", {
					className: "studio-subpath",
					children: i("errors.bun_missing_searched", { locations: e.bun.searched.join(i("shell.format.listJoin")) })
				}) : null
			] }) : null
		},
		{
			key: "harness",
			label: i("repos.preflight.harness"),
			value: e.harness_dirs.length === 0 ? /* @__PURE__ */ m("span", {
				className: "studio-muted",
				children: i("repos.preflight.none")
			}) : /* @__PURE__ */ m(Vs, { dirs: e.harness_dirs })
		},
		{
			key: "aidlc",
			label: i("repos.preflight.aidlc"),
			value: /* @__PURE__ */ h("span", {
				className: "studio-chiplist",
				children: [
					/* @__PURE__ */ m(X, {
						icon: "doc",
						children: i(e.aidlc.layout === "spaces" ? "repos.preflight.layoutSpaces" : e.aidlc.layout === "legacy" ? "repos.preflight.layoutLegacy" : "repos.preflight.layoutNone")
					}),
					e.aidlc.layout === null ? null : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(X, {
						mono: !0,
						children: q(r, "repos.preflight.spaces", e.aidlc.spaces.length)
					}), /* @__PURE__ */ m(X, {
						mono: !0,
						children: q(r, "repos.preflight.intents", e.aidlc.intents)
					})] }),
					e.aidlc.state_versions.length > 0 ? /* @__PURE__ */ m(X, {
						mono: !0,
						icon: "lock",
						children: i("repos.preflight.stateVersions", { versions: e.aidlc.state_versions.join(r.t("shell.format.listJoin")) })
					}) : null
				]
			})
		},
		{
			key: "symlinks",
			label: i("repos.preflight.symlinks"),
			value: e.symlinks_at_managed_paths.length === 0 ? /* @__PURE__ */ m("span", {
				className: "studio-muted",
				children: i("repos.preflight.none")
			}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("ul", {
				className: "studio-plain-list studio-mono",
				children: e.symlinks_at_managed_paths.map((e) => /* @__PURE__ */ m("li", { children: e }, e))
			}), /* @__PURE__ */ m($, {
				tone: "danger",
				children: i("repos.preflight.symlinksBody")
			})] })
		},
		{
			key: "space",
			label: i("repos.preflight.freeSpace"),
			value: je(r, e.free_space_bytes)
		},
		{
			key: "writable",
			label: i("repos.preflight.writable"),
			value: e.writable === null ? null : /* @__PURE__ */ m(X, {
				tone: e.writable ? "ok" : "danger",
				icon: e.writable ? "check" : "warn",
				children: i(e.writable ? "repos.preflight.writable" : "repos.preflight.notWritable")
			})
		},
		{
			key: "receipt",
			label: i("repos.preflight.receipt"),
			value: e.existing_receipt ? /* @__PURE__ */ h("span", {
				className: "studio-chiplist",
				children: [
					/* @__PURE__ */ m(X, {
						mono: !0,
						icon: "doc",
						children: e.existing_receipt.engine_version
					}),
					/* @__PURE__ */ m(X, {
						mono: !0,
						children: e.existing_receipt.receipt_id
					}),
					/* @__PURE__ */ m(X, {
						mono: !0,
						children: q(r, "repos.card.receipt.files", e.existing_receipt.files)
					})
				]
			}) : null
		}
	];
	return /* @__PURE__ */ h(Q, {
		title: i("repos.preflight.title"),
		icon: "search",
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-chiplist",
				children: [/* @__PURE__ */ m(X, {
					tone: e.can_register ? "ok" : "danger",
					icon: e.can_register ? "check" : "warn",
					children: i(e.can_register ? "repos.preflight.canRegister" : "repos.preflight.cannotRegister")
				}), /* @__PURE__ */ m(X, {
					tone: e.can_install ? "ok" : "warn",
					icon: e.can_install ? "check" : "warn",
					children: i(e.can_install ? "repos.preflight.canInstall" : "repos.preflight.cannotInstall")
				})]
			}),
			e.is_directory ? null : /* @__PURE__ */ m($, {
				tone: "danger",
				children: i("repos.preflight.notDirectory")
			}),
			e.sensitive ? /* @__PURE__ */ m($, {
				tone: "danger",
				children: i("repos.preflight.sensitive")
			}) : null,
			e.duplicate_of ? /* @__PURE__ */ h("div", {
				className: "studio-failure-detail",
				children: [
					/* @__PURE__ */ m("strong", { children: i("repos.add.duplicate.title") }),
					" ",
					i("repos.add.duplicate.body", { label: t ?? e.duplicate_of })
				]
			}) : null,
			n,
			/* @__PURE__ */ m(Fs, { rows: a }),
			e.warnings.length > 0 ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("h4", {
				className: "studio-subhead",
				children: i("repos.preflight.warnings")
			}), /* @__PURE__ */ m(Bs, { findings: e.warnings })] }) : null,
			/* @__PURE__ */ m($, { children: i("repos.preflight.readOnly") })
		]
	});
}
//#endregion
//#region src/repos/DirectoryPicker.tsx
function Us({ api: e, initialPath: t, onSelect: n, disabled: r = !1 }) {
	let { t: i } = H(), [a, o] = d(null), [s, c] = d(!1), [l, u] = d(!1), [f, g] = d(null), _ = async (t) => {
		c(!0), u(!0), g(null);
		try {
			o(await e.directories(t));
		} catch (e) {
			g(W(e));
		} finally {
			u(!1);
		}
	};
	return /* @__PURE__ */ h("div", {
		className: "studio-col",
		children: [/* @__PURE__ */ m("button", {
			type: "button",
			className: "studio-btn",
			disabled: r || l,
			onClick: () => void _(t.trim() || null),
			children: i("repos.directory.open")
		}), s ? /* @__PURE__ */ h("div", {
			className: "studio-block",
			"aria-busy": l,
			children: [
				/* @__PURE__ */ m("p", {
					className: "studio-note",
					children: i("repos.directory.help")
				}),
				f ? /* @__PURE__ */ m(Ls, { error: f }) : null,
				a ? /* @__PURE__ */ h(p, { children: [
					/* @__PURE__ */ m("p", {
						className: "studio-mono studio-wrap-any",
						children: a.path
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-row",
						children: [/* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: l || !a.parent,
							onClick: () => void _(a.parent),
							children: i("repos.directory.parent")
						}), /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: l,
							onClick: () => {
								n(a.path), c(!1);
							},
							children: i("repos.directory.select")
						})]
					}),
					/* @__PURE__ */ m("ul", {
						className: "studio-directory-list",
						"aria-label": i("repos.directory.list"),
						children: a.directories.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							disabled: l,
							onClick: () => void _(e.path),
							children: e.name
						}) }, e.path))
					}),
					a.truncated ? /* @__PURE__ */ m("p", {
						className: "studio-note",
						children: i("repos.directory.truncated")
					}) : null
				] }) : null,
				/* @__PURE__ */ m("button", {
					type: "button",
					className: "studio-btn",
					disabled: l,
					onClick: () => c(!1),
					children: i("common.close")
				})
			]
		}) : null]
	});
}
//#endregion
//#region src/repos/AddRepoDialog.tsx
function Ws(e) {
	return [...e.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex=\"-1\"])")];
}
function Gs({ api: e, repos: t, onClose: n, onRegistered: r, onOpenRepo: a }) {
	let { t: s } = H(), [c, l] = d(""), [f, p] = d(""), [g, _] = d(null), [v, y] = d(null), [b, x] = d(null), [S, C] = d("idle"), [w, T] = d(null), E = u(null), D = u(null), O = u(!0);
	o(() => {
		O.current = !0;
		let e = document.activeElement;
		return D.current?.focus(), () => {
			O.current = !1, e instanceof HTMLElement && e.isConnected && e.focus();
		};
	}, []);
	let k = i((e) => {
		if (e.key === "Escape") {
			e.stopPropagation(), n();
			return;
		}
		if (e.key !== "Tab" || !E.current) return;
		let t = Ws(E.current);
		if (t.length === 0) return;
		let r = t[0], i = t[t.length - 1];
		r && i && (e.shiftKey && document.activeElement === r ? (e.preventDefault(), i.focus()) : !e.shiftKey && document.activeElement === i && (e.preventDefault(), r.focus()));
	}, [n]), A = i((e) => {
		l(e), _(null), y(null), x(null), T(null);
	}, []), j = i(async () => {
		let t = c.trim();
		if (!t) {
			x(s("repos.add.pathRequired"));
			return;
		}
		if (!t.startsWith("/") && !t.startsWith("~")) {
			x(s("repos.add.pathNotAbsolute"));
			return;
		}
		x(null), y(null), C("preflight");
		try {
			let n = await e.preflight(t);
			if (!O.current) return;
			_(n.preflight), T(n.preflight.duplicate_of);
		} catch (e) {
			if (!O.current) return;
			_(null), y(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
		} finally {
			O.current && C("idle");
		}
	}, [
		e,
		c,
		s
	]), M = i(async () => {
		if (g && g.can_register) {
			C("registering"), y(null);
			try {
				let t = await e.addRepo(c.trim(), f.trim() || null);
				if (!O.current) return;
				r(t.repo);
			} catch (e) {
				if (!O.current) return;
				let t = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
				if (y(t), t.code === "duplicate_identity") {
					let e = t.details.repo_id;
					T(typeof e == "string" ? e : null);
				}
			} finally {
				O.current && C("idle");
			}
		}
	}, [
		e,
		c,
		f,
		g,
		r
	]), N = w ? t.find((e) => e.repo_id === w) ?? null : null, P = N?.label ?? w, F = g !== null && g.can_register && S === "idle";
	return /* @__PURE__ */ m("div", {
		className: "studio-modal-scrim",
		children: /* @__PURE__ */ h("div", {
			className: "studio-modal",
			role: "dialog",
			"aria-modal": "true",
			"aria-labelledby": "studio-add-repo-title",
			ref: E,
			onKeyDown: k,
			children: [
				/* @__PURE__ */ h("div", {
					className: "studio-spread studio-preview-head",
					children: [/* @__PURE__ */ m("h2", {
						id: "studio-add-repo-title",
						children: s("repos.add.title")
					}), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-icon-btn",
						onClick: n,
						"aria-label": s("common.close"),
						children: /* @__PURE__ */ m(Y, {
							name: "close",
							size: 16
						})
					})]
				}),
				/* @__PURE__ */ h("div", {
					className: "studio-field",
					children: [
						/* @__PURE__ */ m("label", {
							htmlFor: "studio-add-repo-path",
							children: s("repos.add.pathLabel")
						}),
						/* @__PURE__ */ m("input", {
							id: "studio-add-repo-path",
							ref: D,
							className: "studio-input studio-mono",
							type: "text",
							spellCheck: !1,
							autoComplete: "off",
							value: c,
							"aria-describedby": "studio-add-repo-path-help",
							...b ? { "aria-invalid": !0 } : {},
							onChange: (e) => A(e.target.value)
						}),
						/* @__PURE__ */ m("p", {
							className: "studio-help",
							id: "studio-add-repo-path-help",
							children: s("repos.add.pathHelp")
						}),
						/* @__PURE__ */ m(Us, {
							api: e,
							initialPath: c,
							onSelect: A,
							disabled: S !== "idle"
						}),
						b ? /* @__PURE__ */ h("p", {
							className: "studio-note",
							"data-tone": "danger",
							role: "alert",
							children: [
								/* @__PURE__ */ m(Y, {
									name: "warn",
									size: 13
								}),
								" ",
								b
							]
						}) : null
					]
				}),
				/* @__PURE__ */ h("div", {
					className: "studio-field",
					children: [
						/* @__PURE__ */ m("label", {
							htmlFor: "studio-add-repo-label",
							children: s("repos.add.labelLabel")
						}),
						/* @__PURE__ */ m("input", {
							id: "studio-add-repo-label",
							className: "studio-input",
							type: "text",
							value: f,
							"aria-describedby": "studio-add-repo-label-help",
							onChange: (e) => p(e.target.value)
						}),
						/* @__PURE__ */ m("p", {
							className: "studio-help",
							id: "studio-add-repo-label-help",
							children: s("repos.add.labelHelp")
						})
					]
				}),
				/* @__PURE__ */ m("div", {
					className: "studio-repo-actions",
					children: /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => void j(),
						disabled: S !== "idle",
						children: [/* @__PURE__ */ m(Y, {
							name: "search",
							size: 14
						}), s(S === "preflight" ? "repos.add.preflightRunning" : "repos.add.preflight")]
					})
				}),
				v ? /* @__PURE__ */ m(Ls, { error: v }) : null,
				g ? /* @__PURE__ */ m(Hs, {
					report: g,
					duplicateLabel: P,
					children: N ? /* @__PURE__ */ m("div", {
						className: "studio-repo-actions",
						children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => a(N.repo_id),
							children: [/* @__PURE__ */ m(Y, {
								name: "external",
								size: 14
							}), s("repos.add.duplicate.open", { label: N.label })]
						})
					}) : null
				}) : /* @__PURE__ */ m($, { children: s("repos.add.needPreflight") }),
				w && !g?.duplicate_of ? /* @__PURE__ */ h("div", {
					className: "studio-failure-detail",
					role: "alert",
					children: [
						/* @__PURE__ */ m("strong", { children: s("repos.add.duplicate.title") }),
						" ",
						s("repos.add.duplicate.body", { label: P ?? "" })
					]
				}) : null,
				/* @__PURE__ */ h("div", {
					className: "studio-repo-actions studio-modal-actions",
					children: [/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: n,
						children: s("install.cancel")
					}), /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						"data-variant": "primary",
						onClick: () => void M(),
						disabled: !F,
						children: [/* @__PURE__ */ m(Y, {
							name: "check",
							size: 14
						}), s(S === "registering" ? "repos.add.registering" : "repos.add.register")]
					})]
				})
			]
		})
	});
}
//#endregion
//#region src/repos/DoctorResult.tsx
function Ks({ feedback: e }) {
	let t = H(), { t: n } = t, r = u(null);
	return o(() => {
		r.current?.scrollIntoView?.({ block: "nearest" });
	}, [e]), /* @__PURE__ */ h("section", {
		ref: r,
		"aria-label": n("repos.doctor.result"),
		children: [/* @__PURE__ */ m("h4", {
			className: "studio-subhead",
			children: n("repos.doctor.result")
		}), e.status === "running" ? /* @__PURE__ */ h("p", {
			className: "studio-note",
			role: "status",
			children: [
				/* @__PURE__ */ m(Y, {
					name: "clock",
					size: 13
				}),
				" ",
				n("repos.action.doctorRunning")
			]
		}) : e.status === "failed" ? /* @__PURE__ */ m(Ls, { error: e.error }) : /* @__PURE__ */ h(p, { children: [
			/* @__PURE__ */ h("p", {
				className: "studio-note",
				role: "status",
				...e.result.ok ? {} : { "data-tone": "warn" },
				children: [
					/* @__PURE__ */ m(Y, {
						name: e.result.ok ? "check" : "warn",
						size: 13
					}),
					" ",
					n(e.result.ok ? "repos.action.doctorDone" : "repos.action.doctorFailed", { code: e.result.exit_code ?? n("common.unavailable") })
				]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-help",
				children: n("repos.doctor.duration", { ms: t.fmt.number(e.result.duration_ms) })
			}),
			e.result.stdout ? /* @__PURE__ */ h("details", { children: [/* @__PURE__ */ m("summary", { children: n("repos.doctor.output") }), /* @__PURE__ */ m("pre", {
				className: "studio-pre studio-wrap-any",
				children: e.result.stdout
			})] }) : null,
			e.result.stderr ? /* @__PURE__ */ h("details", { children: [/* @__PURE__ */ m("summary", { children: n("repos.doctor.stderr") }), /* @__PURE__ */ m("pre", {
				className: "studio-pre studio-wrap-any",
				children: e.result.stderr
			})] }) : null
		] })]
	});
}
//#endregion
//#region src/repos/GitPanel.tsx
function qs({ git: e }) {
	let t = H(), { t: n } = t;
	return !e || !e.available ? /* @__PURE__ */ m(X, {
		tone: "warn",
		icon: "warn",
		title: e?.reason ?? void 0,
		children: n("repos.git.unavailable")
	}) : /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [
			/* @__PURE__ */ m(X, {
				icon: "git",
				mono: !0,
				children: e.detached ? n("repos.git.detached") : e.branch ?? n("repos.git.detached")
			}),
			/* @__PURE__ */ m(X, {
				tone: e.dirty ? "warn" : "ok",
				icon: e.dirty ? "warn" : "check",
				children: e.dirty ? q(t, "repos.git.dirty", e.dirty_files) : n("repos.git.clean")
			}),
			e.ahead === null ? null : /* @__PURE__ */ m(X, {
				mono: !0,
				children: q(t, "repos.git.ahead", e.ahead)
			}),
			e.behind === null ? null : /* @__PURE__ */ m(X, {
				mono: !0,
				children: q(t, "repos.git.behind", e.behind)
			})
		]
	});
}
function Js({ api: e, repoId: t, enabled: n }) {
	let r = H(), { t: a } = r, o = J(n ? `repo-git:${t}` : null, i((n) => e.repoGit(t, { signal: n }), [e, t]), {
		interval: 0,
		revalidateOn: [
			"repo.updated",
			"transaction.updated",
			"reset"
		]
	});
	return /* @__PURE__ */ h(Q, {
		title: a("repos.git.title"),
		icon: "git",
		children: [
			o.error ? /* @__PURE__ */ m(Ls, { error: o.error }) : null,
			o.data ? /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(qs, { git: o.data.git }), o.data.git.available ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ h("p", {
					className: "studio-subpath",
					children: [o.data.git.head ? `${a("repos.git.head", { sha: o.data.git.head.slice(0, 12) })}  ·  ` : "", o.data.git.head_subject ?? ""]
				}),
				/* @__PURE__ */ h("p", {
					className: "studio-note",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "info",
							size: 13
						}),
						" ",
						o.data.git.upstream ? a("repos.git.upstream", { upstream: o.data.git.upstream }) : a("repos.git.noUpstream"),
						"  ·  ",
						a("repos.git.observedAt", { when: De(r, o.data.git.observed_at) })
					]
				}),
				/* @__PURE__ */ m("h4", {
					className: "studio-subhead",
					children: a("repos.git.ownedDirty.title")
				}),
				o.data.owned_dirty.length === 0 ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: a("repos.git.ownedDirty.none")
				}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("ul", {
					className: "studio-plain-list studio-mono",
					children: o.data.owned_dirty.map((e) => /* @__PURE__ */ m("li", {
						className: "studio-wrap-any",
						children: e
					}, e))
				}), /* @__PURE__ */ m($, {
					tone: "warn",
					children: a("repos.git.ownedDirty.body")
				})] }),
				o.data.unrelated_dirty > 0 ? /* @__PURE__ */ m($, { children: q(r, "repos.git.unrelatedDirty", o.data.unrelated_dirty) }) : null
			] }) : null] }) : o.loading ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: a("common.loading")
			}) : null,
			/* @__PURE__ */ m($, { children: a("repos.git.readOnly") })
		]
	});
}
//#endregion
//#region src/repos/InstallPreview.tsx
var Ys = [
	"create",
	"shell_create",
	"merge_create",
	"merge_update",
	"owned_identical",
	"engine_modified",
	"identical",
	"merge_identical",
	"shell_exists",
	"retire",
	"retire_blocked",
	"conflict",
	"owned_modified",
	"merge_conflict",
	"remove",
	"remove_fragment",
	"preserve",
	"already_absent",
	"uninstall_conflict",
	"restore_version",
	"rollback_conflict",
	"restore_backup",
	"remove_created",
	"recovery_conflict"
], Xs = [
	"conflict",
	"owned_modified",
	"merge_conflict",
	"uninstall_conflict",
	"rollback_conflict",
	"recovery_conflict"
], Zs = ["retire", "retire_blocked"], Qs = {
	create: "accent",
	shell_create: "accent",
	merge_create: "accent",
	merge_update: "info",
	owned_identical: "info",
	engine_modified: "info",
	identical: "neutral",
	merge_identical: "neutral",
	shell_exists: "neutral",
	retire: "warn",
	retire_blocked: "warn",
	conflict: "danger",
	owned_modified: "danger",
	merge_conflict: "danger",
	remove: "warn",
	remove_fragment: "warn",
	preserve: "neutral",
	already_absent: "neutral",
	uninstall_conflict: "danger",
	restore_version: "warn",
	rollback_conflict: "danger",
	restore_backup: "warn",
	remove_created: "warn",
	recovery_conflict: "danger"
}, $s = {
	create: "plus",
	shell_create: "plus",
	merge_create: "plus",
	merge_update: "install",
	owned_identical: "install",
	engine_modified: "install",
	identical: "check",
	merge_identical: "check",
	shell_exists: "check",
	retire: "close",
	retire_blocked: "lock",
	conflict: "warn",
	owned_modified: "warn",
	merge_conflict: "warn",
	remove: "close",
	remove_fragment: "close",
	preserve: "lock",
	already_absent: "check",
	uninstall_conflict: "warn",
	restore_version: "recovery",
	rollback_conflict: "warn",
	restore_backup: "recovery",
	remove_created: "close",
	recovery_conflict: "warn"
}, ec = 200, tc = 40;
function nc(e) {
	return e === null ? null : e.slice(0, 12);
}
function rc({ line: e }) {
	let t = e.startsWith("+++") || e.startsWith("---") || e.startsWith("@@") || e.startsWith("diff ") ? "meta" : e.startsWith("+") ? "add" : e.startsWith("-") ? "del" : "ctx";
	return /* @__PURE__ */ m("span", {
		className: "studio-diffline",
		"data-kind": t,
		children: e === "" ? " " : e
	});
}
function ic({ diff: e }) {
	let { t } = H(), n = e.split("\n"), r = n.slice(0, ec);
	return /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m("pre", {
		className: "studio-diff",
		children: r.map((e, t) => /* @__PURE__ */ m(rc, { line: e }, t))
	}), n.length > r.length ? /* @__PURE__ */ m("p", {
		className: "studio-help",
		children: t("install.blockers.diffTruncated", { n: ec })
	}) : null] });
}
function ac({ entry: e }) {
	let { t } = H();
	return /* @__PURE__ */ m(X, {
		tone: Qs[e.action],
		icon: $s[e.action],
		children: t(`install.action.${e.action}`)
	});
}
function oc({ entry: e }) {
	let { t } = H();
	return /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [/* @__PURE__ */ m(X, {
			icon: "lock",
			children: t(`enum.ownership.${e.ownership}`)
		}), e.fragment_key ? /* @__PURE__ */ m(X, {
			mono: !0,
			children: t("install.entries.fragment", { key: e.fragment_key })
		}) : null]
	});
}
function sc({ entry: e }) {
	return /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [/* @__PURE__ */ m(ac, { entry: e }), /* @__PURE__ */ m(oc, { entry: e })]
	});
}
function cc({ entry: e }) {
	let { t, has: n } = H();
	if (!e.reason) return null;
	let r = `install.reason.${e.reason}`;
	return /* @__PURE__ */ m("p", {
		className: "studio-help",
		children: n(r) ? t(r) : e.reason
	});
}
function lc({ entry: e }) {
	let { t } = H(), n = [], r = nc(e.live_sha256), i = nc(e.payload_sha256), a = nc(e.receipt_sha256);
	return r && n.push(t("install.entries.live", { sha: r })), i && n.push(t("install.entries.payload", { sha: i })), a && n.push(t("install.entries.receipt", { sha: a })), /* @__PURE__ */ m("span", {
		className: "studio-mono studio-wrap-any",
		children: n.join("  ·  ")
	});
}
function uc({ entry: e }) {
	let { t } = H();
	return /* @__PURE__ */ h("li", {
		className: "studio-finding",
		"data-severity": e.blocking ? "blocking" : "warn",
		children: [
			/* @__PURE__ */ m("p", {
				className: "studio-mono studio-strong studio-wrap-any",
				children: e.path
			}),
			/* @__PURE__ */ m(sc, { entry: e }),
			/* @__PURE__ */ m(cc, { entry: e }),
			/* @__PURE__ */ m("p", {
				className: "studio-subpath",
				children: /* @__PURE__ */ m(lc, { entry: e })
			}),
			e.diff ? /* @__PURE__ */ h("details", { children: [/* @__PURE__ */ m("summary", { children: t("install.blockers.diff") }), /* @__PURE__ */ m(ic, { diff: e.diff })] }) : /* @__PURE__ */ m("p", {
				className: "studio-help",
				children: t("install.blockers.noDiff")
			})
		]
	});
}
function dc({ plan: e, digest: t }) {
	let n = H(), { t: r } = n, i = e.entries.filter((e) => Xs.includes(e.action)), a = e.entries.filter((e) => Zs.includes(e.action)), o = Ys.map((t) => [t, e.counts[t] ?? 0]).filter(([, e]) => e > 0);
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ h(Q, {
			title: r("install.counts.title"),
			icon: "install",
			children: [
				/* @__PURE__ */ m(Fs, { rows: [
					{
						key: "from",
						label: r("install.version.from"),
						value: e.engine_from ?? r("install.version.notInstalled"),
						mono: !0
					},
					{
						key: "to",
						label: r("install.version.to"),
						value: e.kind === "uninstall" ? r("install.version.notInstalled") : e.engine_to,
						mono: !0
					},
					{
						key: "studio",
						label: r("install.version.studio"),
						value: e.studio_version,
						mono: !0
					},
					{
						key: "bytes",
						label: r("install.bytes"),
						value: je(n, e.bytes_to_write),
						mono: !0
					},
					{
						key: "payload",
						label: r("install.payloadDigest"),
						value: nc(e.payload_digest),
						mono: !0
					},
					{
						key: "plan",
						label: r("install.planDigest"),
						value: nc(t),
						mono: !0
					}
				] }),
				/* @__PURE__ */ m("div", {
					className: "studio-chiplist studio-counts",
					children: o.length === 0 ? /* @__PURE__ */ m("span", {
						className: "studio-muted",
						children: r("install.entries.none")
					}) : o.map(([e, t]) => /* @__PURE__ */ h(X, {
						tone: Qs[e],
						icon: $s[e],
						children: [
							r(`install.action.${e}`),
							" · ",
							/* @__PURE__ */ m("span", {
								className: "studio-mono",
								children: n.fmt.number(t)
							})
						]
					}, e))
				}),
				e.requires_admin_lease ? /* @__PURE__ */ m($, { children: r("install.lease.required") }) : null
			]
		}),
		e.state_version_blocked ? /* @__PURE__ */ h(Q, {
			title: r("install.stateBlocked.title"),
			icon: "lock",
			children: [/* @__PURE__ */ m("div", {
				className: "studio-failure-detail",
				children: r("install.stateBlocked.body", {
					found: e.state_versions_found.length > 0 ? e.state_versions_found.join(r("shell.format.listJoin")) : G(n),
					supported: e.engine_to
				})
			}), fc(e) > 0 ? /* @__PURE__ */ m($, {
				tone: "warn",
				children: q(n, "install.stateBlocked.unreadable", fc(e))
			}) : null]
		}) : null,
		i.length > 0 ? /* @__PURE__ */ h(Q, {
			title: r("install.blockers.title"),
			icon: "warn",
			children: [/* @__PURE__ */ m("ul", {
				className: "studio-plain-list",
				children: i.map((e) => /* @__PURE__ */ m(uc, { entry: e }, e.path))
			}), /* @__PURE__ */ m($, {
				tone: "danger",
				children: r("install.blockers.body")
			})]
		}) : null,
		a.length > 0 ? /* @__PURE__ */ h(Q, {
			title: r("install.retire.title"),
			icon: "close",
			children: [/* @__PURE__ */ m("ul", {
				className: "studio-plain-list",
				children: a.map((e) => /* @__PURE__ */ m(uc, { entry: e }, e.path))
			}), /* @__PURE__ */ m($, { children: r("install.retire.body") })]
		}) : null,
		e.warnings.length > 0 ? /* @__PURE__ */ m(Q, {
			title: r("install.warnings.title"),
			icon: "warn",
			children: /* @__PURE__ */ m(Bs, { findings: e.warnings })
		}) : null,
		e.entries.some((e) => e.action === "preserve") ? /* @__PURE__ */ h(Q, {
			title: r("install.preserved.title"),
			icon: "lock",
			children: [/* @__PURE__ */ m($, { children: r("install.preserved.body") }), /* @__PURE__ */ m("ul", {
				className: "studio-plain-list",
				children: e.entries.filter((e) => e.action === "preserve").map((e) => /* @__PURE__ */ h("li", { children: [/* @__PURE__ */ m("span", {
					className: "studio-mono",
					children: e.path
				}), /* @__PURE__ */ m(cc, { entry: e })] }, e.path))
			})]
		}) : null,
		/* @__PURE__ */ m(Q, {
			title: r("install.entries.title"),
			icon: "doc",
			children: /* @__PURE__ */ h("details", {
				...e.entries.length <= tc ? { open: !0 } : {},
				children: [/* @__PURE__ */ m("summary", { children: q(n, "install.entries.summary", e.entries.length) }), e.entries.length === 0 ? /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: r("install.entries.none")
				}) : /* @__PURE__ */ h("table", {
					className: "studio-tbl",
					children: [
						/* @__PURE__ */ m("caption", {
							className: "studio-sr",
							children: r("install.entries.caption")
						}),
						/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: r("install.entries.col.path")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: r("install.entries.col.ownership")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: r("install.entries.col.action")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: r("install.entries.col.hash")
							})
						] }) }),
						/* @__PURE__ */ m("tbody", { children: e.entries.map((e) => /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "row",
								className: "studio-mono studio-wrap-any studio-rowhead",
								children: e.path
							}),
							/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m(oc, { entry: e }) }),
							/* @__PURE__ */ h("td", { children: [/* @__PURE__ */ m(ac, { entry: e }), /* @__PURE__ */ m(cc, { entry: e })] }),
							/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m(lc, { entry: e }) })
						] }, e.path)) })
					]
				})]
			})
		}),
		/* @__PURE__ */ m(Q, {
			title: r("install.preflight.title"),
			icon: "search",
			children: /* @__PURE__ */ h("details", { children: [/* @__PURE__ */ m("summary", { children: r("repos.preflight.title") }), /* @__PURE__ */ m(Hs, { report: e.preflight })] })
		})
	] });
}
function fc(e) {
	let t = e.state_versions_unreadable;
	return Array.isArray(t) ? t.length : typeof t == "number" && Number.isFinite(t) ? t : 0;
}
var pc = {
	plan: null,
	digest: "",
	error: null,
	loading: !1,
	stale: !1
};
function mc({ api: e, repoId: t, kind: n, intro: r, targetVersion: a, onStarted: s, onClose: c }) {
	let l = H(), { t: f } = l, [p, g] = d(pc), [_, v] = d(!1), y = u(!0);
	o(() => (y.current = !0, () => {
		y.current = !1;
	}), []);
	let b = i(async (r = null) => {
		let i = r !== null;
		g((e) => ({
			...e,
			loading: !0,
			error: r,
			stale: i
		}));
		try {
			let a = await e.installPreview(t, n);
			if (!y.current) return;
			g({
				plan: a.plan,
				digest: a.plan_digest,
				error: r,
				loading: !1,
				stale: i
			});
		} catch (e) {
			if (!y.current || ve(e)) return;
			g({
				plan: null,
				digest: "",
				error: e instanceof U ? e : new U("internal_error", String(e), {}, 0),
				loading: !1,
				stale: i
			});
		}
	}, [
		e,
		t,
		n
	]);
	o(() => {
		b();
	}, [b]);
	let x = i(async () => {
		if (p.plan && p.digest) {
			v(!0);
			try {
				let r = await e.installApply(t, p.digest, n);
				if (!y.current) return;
				v(!1), s(r.transaction_id);
			} catch (e) {
				if (!y.current) return;
				v(!1);
				let t = e instanceof U ? e : new U("internal_error", String(e), {}, 0);
				if (t.code === "install_conflict") {
					b(t);
					return;
				}
				g((e) => ({
					...e,
					error: t
				}));
			}
		}
	}, [
		e,
		t,
		n,
		p.plan,
		p.digest,
		s,
		b
	]), S = p.plan === null || p.plan.blocking || p.plan.state_version_blocked, C = !!p.plan?.recovery_transaction_id, w = C ? f("install.confirm.restoreTransaction") : n === "rollback" ? f("install.confirm.rollback", { version: p.plan?.engine_to ?? a ?? G(l) }) : n === "uninstall" ? f("install.confirm.uninstall") : n === "recovery" ? f("install.confirm.recovery") : f(n === "install" ? "install.confirm.install" : "install.confirm.upgrade", { version: p.plan?.engine_to ?? a ?? G(l) });
	return /* @__PURE__ */ h("section", {
		className: "studio-preview",
		"aria-label": f(`install.preview.title.${n}`),
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-spread studio-preview-head",
				children: [/* @__PURE__ */ m("h2", { children: f(`install.preview.title.${n}`) }), /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					disabled: _,
					onClick: c,
					children: [/* @__PURE__ */ m(Y, {
						name: "close",
						size: 14
					}), f("install.cancel")]
				})]
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: f(`install.preview.lede.${n}`)
			}),
			r,
			C ? /* @__PURE__ */ m($, {
				tone: "warn",
				children: f("install.recovery.restoreTransaction", {
					id: p.plan.recovery_transaction_id,
					kind: f(`install.tx.kind.${p.plan.recovery_kind}`)
				})
			}) : null,
			p.stale ? /* @__PURE__ */ h("div", {
				className: "studio-failure-detail",
				role: "alert",
				children: [
					/* @__PURE__ */ m("strong", { children: f("install.stale.title") }),
					" ",
					f("install.stale.body")
				]
			}) : null,
			p.error ? /* @__PURE__ */ m(Ls, {
				error: p.error,
				reassure: f("install.error.nothingWritten")
			}) : null,
			/* @__PURE__ */ m("span", {
				className: "studio-sr",
				role: "status",
				"aria-live": "polite",
				children: p.loading ? f("install.preview.running") : ""
			}),
			p.loading && p.plan === null ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: f("install.preview.running")
			}) : null,
			p.plan ? /* @__PURE__ */ m(dc, {
				plan: p.plan,
				digest: p.digest
			}) : null,
			/* @__PURE__ */ h(Q, {
				title: f("install.confirm.title"),
				icon: "check",
				children: [
					/* @__PURE__ */ m($, { children: f(C ? "install.confirm.restoreTransactionNote" : n === "uninstall" ? "install.confirm.uninstallNote" : n === "rollback" ? "install.confirm.rollbackNote" : "install.confirm.note") }),
					p.digest ? /* @__PURE__ */ m("p", {
						className: "studio-help studio-wrap-any",
						children: f("install.confirm.digestNote", { digest: nc(p.digest) ?? "" })
					}) : null,
					S && p.plan ? /* @__PURE__ */ m("p", {
						className: "studio-note",
						"data-tone": "danger",
						children: f("install.blocked")
					}) : null,
					/* @__PURE__ */ h("div", {
						className: "studio-repo-actions",
						children: [/* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => void b(),
							disabled: p.loading || _,
							children: [/* @__PURE__ */ m(Y, {
								name: "refresh",
								size: 14
							}), p.loading ? f("install.preview.running") : f("install.preview.refresh")]
						}), /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							"data-variant": "primary",
							onClick: () => void x(),
							disabled: S || _ || p.loading,
							children: [/* @__PURE__ */ m(Y, {
								name: "check",
								size: 14
							}), w]
						})]
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-help",
						children: f("install.noForce")
					})
				]
			})
		]
	});
}
function hc({ api: e, repoId: t, bundledVersion: n, onStarted: r, onClose: i }) {
	let { t: a } = H();
	return /* @__PURE__ */ m(mc, {
		api: e,
		repoId: t,
		kind: "install",
		targetVersion: n,
		onStarted: r,
		onClose: i,
		intro: /* @__PURE__ */ h("div", {
			className: "studio-chiplist",
			children: [
				/* @__PURE__ */ m(X, {
					icon: "install",
					mono: !0,
					children: a("install.version.notInstalled")
				}),
				/* @__PURE__ */ m(Y, {
					name: "chevron",
					size: 13
				}),
				/* @__PURE__ */ m(X, {
					tone: "accent",
					icon: "install",
					mono: !0,
					children: n
				})
			]
		})
	});
}
//#endregion
//#region src/repos/RecoveryPanel.tsx
function gc(e) {
	let t = e.find((e) => e.status === "recovery_required" && e.failed_dir);
	return t?.failed_dir ? t.failed_dir : e.find((e) => e.failed_dir)?.failed_dir ?? null;
}
function _c({ repoLabel: e, failedDir: t, onStart: n }) {
	let { t: r } = H();
	return /* @__PURE__ */ h("div", {
		className: "studio-failure-detail studio-recovery",
		role: "alert",
		children: [
			/* @__PURE__ */ h("p", {
				className: "studio-row studio-strong",
				children: [/* @__PURE__ */ m(Y, {
					name: "recovery",
					size: 15
				}), r("install.recovery.title")]
			}),
			/* @__PURE__ */ m("p", { children: r("install.recovery.body") }),
			/* @__PURE__ */ m("p", { children: /* @__PURE__ */ m("strong", { children: r("install.recovery.blocked") }) }),
			/* @__PURE__ */ m("p", {
				className: "studio-help",
				children: r("install.recovery.pathLabel")
			}),
			t ? /* @__PURE__ */ m("p", {
				className: "studio-mono studio-wrap-any",
				children: t
			}) : /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: r("install.recovery.noPath")
			}),
			/* @__PURE__ */ m("p", {
				className: "studio-help",
				children: r("install.recovery.pathNote")
			}),
			n ? /* @__PURE__ */ h("button", {
				type: "button",
				className: "studio-btn",
				onClick: n,
				children: [/* @__PURE__ */ m(Y, {
					name: "refresh",
					size: 14
				}), r("install.recovery.start")]
			}) : null,
			/* @__PURE__ */ m("span", {
				className: "studio-sr",
				children: r("repos.a11y.blocked", { repo: e })
			})
		]
	});
}
function vc({ api: e, repoId: t, repoLabel: n, failedDir: r, onStarted: i, onClose: a }) {
	let { t: o } = H();
	return /* @__PURE__ */ m(mc, {
		api: e,
		repoId: t,
		kind: "recovery",
		onStarted: i,
		onClose: a,
		intro: /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(_c, {
			repoLabel: n,
			failedDir: r
		}), /* @__PURE__ */ m($, { children: o("install.confirm.note") })] })
	});
}
//#endregion
//#region src/repos/RepoCard.tsx
var yc = {
	installed: "ok",
	drift: "warn",
	not_installed: "neutral",
	recovery_required: "danger"
}, bc = {
	installed: "check",
	drift: "warn",
	not_installed: "install",
	recovery_required: "recovery"
}, xc = {
	Idle: "neutral",
	Queued: "neutral",
	Running: "ok",
	WaitingForYou: "accent",
	Paused: "neutral",
	Parked: "neutral",
	Interrupted: "warn",
	ReconciliationRequired: "danger",
	RetryEligible: "warn",
	CircuitOpen: "warn",
	Failed: "danger",
	Completed: "ok",
	Archived: "neutral"
}, Sc = {
	available: "ok",
	moved: "warn",
	permission_denied: "danger",
	unavailable: "warn",
	identity_unprovable: "warn"
}, Cc = ".kiro";
function wc(e) {
	if (e.own_engine_version !== null) return null;
	let t = e.engine_dir;
	return t === null || t === ".kiro" ? null : e.harness_dirs.find((e) => e.dir === t) ?? null;
}
function Tc({ status: e }) {
	let { t } = H();
	return /* @__PURE__ */ m(X, {
		tone: yc[e],
		icon: bc[e],
		children: t(`enum.installStatus.${e}`)
	});
}
function Ec({ repo: e }) {
	let t = H(), { t: n } = t, r = e.install, i = r.engine_version !== r.bundled_engine_version, a = wc(r);
	return /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [
			/* @__PURE__ */ m(X, {
				mono: !0,
				icon: "install",
				title: n("install.version.from"),
				children: r.engine_version ?? n("install.version.notInstalled")
			}),
			i ? /* @__PURE__ */ m(X, {
				mono: !0,
				title: n("install.version.to"),
				children: n("repos.engine.bundled", { version: r.bundled_engine_version })
			}) : null,
			a === null ? null : /* @__PURE__ */ m(X, {
				tone: "accent",
				icon: "install",
				children: n("repos.engine.otherHarness", { dir: a.dir })
			}),
			r.upgrade_available ? /* @__PURE__ */ m(X, {
				tone: "accent",
				icon: "install",
				children: n("repos.engine.upgradeAvailable")
			}) : null,
			r.newer_installed ? /* @__PURE__ */ m(X, {
				tone: "warn",
				icon: "warn",
				children: n("repos.engine.newerInstalled")
			}) : null,
			r.state_version_blocked ? /* @__PURE__ */ m(X, {
				tone: "warn",
				icon: "lock",
				children: n("repos.engine.stateBlocked", { version: r.engine_state_version ?? G(t) })
			}) : null,
			r.drift_count > 0 ? /* @__PURE__ */ m(X, {
				tone: "warn",
				icon: "warn",
				children: q(t, "repos.drift", r.drift_count)
			}) : null
		]
	});
}
function Dc({ repo: e }) {
	let t = H(), n = e.counts;
	return /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [
			/* @__PURE__ */ m(X, {
				icon: "intent",
				mono: !0,
				children: q(t, "repos.counts.intents", n.intents)
			}),
			n.in_flight > 0 ? /* @__PURE__ */ m(X, {
				tone: "accent",
				mono: !0,
				children: q(t, "repos.counts.inFlight", n.in_flight)
			}) : null,
			/* @__PURE__ */ m(X, {
				tone: n.open_actions > 0 ? "accent" : "neutral",
				icon: "inbox",
				mono: !0,
				children: q(t, "repos.counts.open", n.open_actions)
			}),
			n.blocking_findings > 0 ? /* @__PURE__ */ m(X, {
				tone: "danger",
				icon: "recovery",
				mono: !0,
				children: q(t, "repos.counts.blocking", n.blocking_findings)
			}) : null
		]
	});
}
function Oc(e) {
	if (e.archived) return [
		"unarchive",
		"rename",
		"cleanup",
		"remove"
	];
	let t = [], n = e.install.status, r = e.availability === "available", i = e.install.own_engine_version !== null;
	return n === "recovery_required" && t.push("recover"), r && !i && n !== "recovery_required" && t.push("install"), r && i && (n === "installed" || n === "drift") && !e.install.newer_installed && (e.install.upgrade_available || e.install.drift_count > 0) && t.push("upgrade"), t.push("rescan"), r && (n === "installed" || n === "drift") && t.push("doctor"), r && e.install.receipt && n !== "recovery_required" && t.push("uninstall"), r && e.install.rollback_target && n !== "recovery_required" && t.push("rollback"), (e.availability === "moved" || e.availability === "unavailable") && t.push("rebind"), t.push("cleanup", "rename", "archive", "remove"), t;
}
var kc = [
	"install",
	"upgrade",
	"recover",
	"rebind",
	"remove",
	"doctor",
	"rename",
	"archive",
	"unarchive",
	"uninstall",
	"cleanup",
	"rollback"
], Ac = {
	open: "repos.action.details",
	queue: "repos.card.queue.open",
	intents: "repos.card.intents.open",
	"new-intent": "intents.newIntent",
	rescan: "repos.action.rescan",
	doctor: "repos.action.doctor",
	rebind: "repos.action.rebind",
	remove: "repos.action.remove",
	install: "repos.action.install",
	upgrade: "repos.action.upgrade",
	recover: "repos.action.recover",
	rename: "repos.action.rename",
	archive: "repos.action.archive",
	unarchive: "repos.action.unarchive",
	uninstall: "repos.action.uninstall",
	cleanup: "maintenance.title",
	rollback: "repos.action.rollback"
};
function jc({ lease: e }) {
	let t = H(), { t: n } = t;
	return e === null ? /* @__PURE__ */ m("span", {
		className: "studio-muted",
		children: n("repos.card.lease.none")
	}) : /* @__PURE__ */ h("span", {
		className: "studio-chiplist",
		children: [
			/* @__PURE__ */ m(X, {
				tone: e.orphaned ? "danger" : "accent",
				icon: "lock",
				mono: !0,
				children: e.operation_type ?? e.kind
			}),
			/* @__PURE__ */ m("span", {
				className: "studio-muted",
				children: n("repos.card.lease.since", { when: De(t, e.acquired_at) })
			}),
			e.orphaned ? /* @__PURE__ */ m(X, {
				tone: "danger",
				icon: "warn",
				children: n("repos.card.lease.orphaned")
			}) : null
		]
	});
}
function Mc({ repo: e, detailed: t = !1, desktop: n, installPreviewOpen: r = !1, busy: i = null, error: a = null, transactions: o = [], intents: s = [], onAction: c, onOpenTransaction: l, children: u, maintenanceFeedback: d }) {
	let f = H(), { t: g } = f, _ = e.install, v = _.status === "recovery_required", y = !e.archived && e.availability === "available" && (_.status === "installed" || _.status === "drift"), b = wc(_), x = v ? "blocked" : e.availability !== "available" || _.status === "drift" ? "true" : "false", S = Oc(e).filter((e) => (n || !kc.includes(e)) && !(r && e === "install")), C = g("repos.a11y.repoRow", {
		label: e.label,
		path: e.canonical_path,
		install: g(`enum.installStatus.${_.status}`),
		availability: g(`enum.availability.${e.availability}`),
		intents: q(f, "repos.counts.intents", e.counts.intents),
		queue: q(f, "repos.counts.open", e.counts.open_actions)
	}), w = [
		{
			key: "path",
			label: g("repos.card.identity.path"),
			value: e.canonical_path,
			mono: !0
		},
		{
			key: "resolved",
			label: g("repos.card.identity.resolved"),
			value: e.resolved_identity,
			mono: !0
		},
		{
			key: "gitdir",
			label: g("repos.card.identity.gitCommonDir"),
			value: e.git_common_dir_identity,
			mono: !0
		},
		{
			key: "platform",
			label: g("repos.card.identity.platform"),
			value: e.platform,
			mono: !0
		},
		{
			key: "added",
			label: g("repos.card.identity.added"),
			value: K(f, e.added_at)
		},
		{
			key: "seen",
			label: g("repos.card.identity.lastSeen"),
			value: K(f, e.last_seen)
		},
		{
			key: "scanned",
			label: g("repos.card.identity.scanned"),
			value: K(f, e.scanned_at)
		}
	];
	return /* @__PURE__ */ h("article", {
		className: "studio-repocard",
		"data-attention": x,
		"aria-label": C,
		children: [
			/* @__PURE__ */ h("header", {
				className: "studio-repocard-head",
				children: [/* @__PURE__ */ h("div", {
					className: "studio-grow",
					children: [/* @__PURE__ */ m("h3", {
						className: "studio-repocard-title",
						children: e.label
					}), /* @__PURE__ */ m("p", {
						className: "studio-subpath",
						children: e.canonical_path
					})]
				}), /* @__PURE__ */ h("div", {
					className: "studio-chiplist",
					children: [/* @__PURE__ */ m(Tc, { status: _.status }), e.availability === "available" ? null : /* @__PURE__ */ m(X, {
						tone: Sc[e.availability],
						icon: "warn",
						title: e.availability_detail ?? void 0,
						children: g(`enum.availability.${e.availability}`)
					})]
				})]
			}),
			/* @__PURE__ */ h("div", {
				className: "studio-repocard-chips",
				children: [
					/* @__PURE__ */ m(Ec, { repo: e }),
					/* @__PURE__ */ m(Dc, { repo: e }),
					/* @__PURE__ */ m(qs, { git: e.git })
				]
			}),
			e.availability === "available" ? null : /* @__PURE__ */ h("div", {
				className: "studio-failure-detail",
				"data-tone": "warn",
				children: [
					/* @__PURE__ */ m("strong", { children: g("repos.availability.title") }),
					" ",
					g(`repos.remedy.${e.availability}`),
					e.availability_detail ? /* @__PURE__ */ m("p", {
						className: "studio-subpath studio-wrap-any",
						children: e.availability_detail
					}) : null
				]
			}),
			v ? /* @__PURE__ */ m(_c, {
				repoLabel: e.label,
				failedDir: gc(o),
				...n ? { onStart: () => c("recover") } : {}
			}) : null,
			a ? /* @__PURE__ */ m(Ls, {
				error: a,
				reassure: g("repos.error.unchanged")
			}) : null,
			t ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m(Q, {
					title: g("repos.card.identity"),
					icon: "repo",
					children: /* @__PURE__ */ m(Fs, { rows: w })
				}),
				/* @__PURE__ */ h(Q, {
					title: g("repos.card.install"),
					icon: "install",
					children: [
						b === null ? null : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m($, { children: g("repos.card.install.otherHarness", {
							dir: b.dir,
							version: b.engine_version ?? G(f),
							own: Cc
						}) }), /* @__PURE__ */ m($, { children: g("repos.card.install.otherHarnessInstall", {
							dir: b.dir,
							own: Cc
						}) })] }),
						/* @__PURE__ */ m(Fs, { rows: [
							{
								key: "dir",
								label: g("repos.card.install.engineDir"),
								value: _.engine_dir,
								mono: !0
							},
							{
								key: "state",
								label: g("repos.card.install.stateVersion"),
								value: _.engine_state_version,
								mono: !0
							},
							{
								key: "stages",
								label: g("repos.card.install.stages"),
								value: _.stage_count,
								mono: !0
							}
						] }),
						/* @__PURE__ */ m("h4", {
							className: "studio-subhead",
							children: g("repos.card.harness")
						}),
						_.harness_dirs.length === 0 ? /* @__PURE__ */ m("p", {
							className: "studio-muted",
							children: g("repos.card.harness.none")
						}) : /* @__PURE__ */ m(Vs, { dirs: _.harness_dirs })
					]
				}),
				/* @__PURE__ */ m(Q, {
					title: g("repos.card.receipt"),
					icon: "doc",
					children: _.receipt === null ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("repos.card.receipt.none")
					}) : /* @__PURE__ */ h("span", {
						className: "studio-chiplist",
						children: [
							/* @__PURE__ */ m(X, {
								mono: !0,
								icon: "lock",
								children: _.receipt.receipt_id
							}),
							/* @__PURE__ */ m(X, {
								mono: !0,
								icon: "install",
								children: _.receipt.engine_version
							}),
							/* @__PURE__ */ m(X, {
								mono: !0,
								children: q(f, "repos.card.receipt.files", _.receipt.files)
							}),
							/* @__PURE__ */ m(X, {
								mono: !0,
								children: g("repos.card.receipt.written", { when: K(f, _.receipt.committed_at) })
							}),
							/* @__PURE__ */ m(X, {
								tone: _.receipt.status === "current" ? "ok" : "neutral",
								children: g(`install.receiptStatus.${_.receipt.status}`)
							})
						]
					})
				}),
				/* @__PURE__ */ m(Q, {
					title: g("repos.card.queue"),
					icon: "inbox",
					children: e.counts.open_actions === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("repos.card.queue.none")
					}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(Dc, { repo: e }), /* @__PURE__ */ m("div", {
						className: "studio-repo-actions",
						children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							onClick: () => c("queue"),
							children: [/* @__PURE__ */ m(Y, {
								name: "inbox",
								size: 13
							}), g("repos.card.queue.open")]
						})
					})] })
				}),
				/* @__PURE__ */ h(Q, {
					title: g("repos.card.intents"),
					icon: "intent",
					children: [s.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("repos.card.intents.none")
					}) : /* @__PURE__ */ m(p, { children: /* @__PURE__ */ m("ul", {
						className: "studio-plain-list",
						children: s.map((e) => /* @__PURE__ */ h("li", {
							className: "studio-row studio-chiplist",
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-strong",
									children: e.slug
								}),
								/* @__PURE__ */ m(X, {
									tone: xc[e.operational_state],
									children: g(`enum.intentState.${e.operational_state}`)
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-muted",
									children: e.disk.current_stage ?? G(f)
								}),
								e.open_actions > 0 ? /* @__PURE__ */ m(X, {
									tone: "accent",
									icon: "inbox",
									mono: !0,
									children: q(f, "repos.counts.open", e.open_actions)
								}) : null
							]
						}, e.intent_key))
					}) }), y || s.length > 0 ? /* @__PURE__ */ h("div", {
						className: "studio-repo-actions",
						children: [y ? /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							"data-variant": "primary",
							onClick: () => c("new-intent"),
							children: [/* @__PURE__ */ m(Y, {
								name: "plus",
								size: 13
							}), g("intents.newIntent")]
						}) : null, s.length > 0 ? /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn studio-btn-sm",
							onClick: () => c("intents"),
							children: [/* @__PURE__ */ m(Y, {
								name: "intent",
								size: 13
							}), g("repos.card.intents.open")]
						}) : null]
					}) : null]
				}),
				/* @__PURE__ */ m(Q, {
					title: g("repos.card.leases"),
					icon: "lock",
					children: /* @__PURE__ */ m(Fs, { rows: [{
						key: "exec",
						label: g("repos.card.lease.execution"),
						value: /* @__PURE__ */ m(jc, { lease: e.leases.execution })
					}, {
						key: "admin",
						label: g("repos.card.lease.admin"),
						value: /* @__PURE__ */ m(jc, { lease: e.leases.admin })
					}] })
				}),
				e.findings.length > 0 ? /* @__PURE__ */ m(Q, {
					title: g("repos.card.findings"),
					icon: "warn",
					children: /* @__PURE__ */ m(Bs, { findings: e.findings })
				}) : null,
				/* @__PURE__ */ m(Q, {
					title: g("repos.card.transactions"),
					icon: "clock",
					children: o.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: g("repos.card.transactions.none")
					}) : /* @__PURE__ */ m("ul", {
						className: "studio-plain-list",
						children: o.map((e) => /* @__PURE__ */ h("li", {
							className: "studio-row studio-chiplist",
							children: [
								/* @__PURE__ */ m(X, {
									mono: !0,
									children: g(`install.tx.kind.${e.kind}`)
								}),
								/* @__PURE__ */ m(X, {
									tone: e.status === "committed" ? "ok" : e.status === "recovery_required" || e.status === "failed" ? "danger" : "warn",
									children: g(`install.txStatus.${e.status}`)
								}),
								/* @__PURE__ */ m(X, {
									mono: !0,
									icon: "install",
									children: e.engine_version
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-muted",
									children: K(f, e.started_at)
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-muted",
									children: e.transaction_id
								}),
								l ? /* @__PURE__ */ m("button", {
									type: "button",
									className: "studio-btn studio-btn-sm",
									onClick: () => l(e.transaction_id),
									children: g("install.tx.open")
								}) : null
							]
						}, e.transaction_id))
					})
				}),
				u
			] }) : null,
			(() => {
				let n = /* @__PURE__ */ h("div", {
					className: "studio-repo-actions",
					children: [
						t ? null : /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => c("open"),
							children: [/* @__PURE__ */ m(Y, {
								name: "chevron",
								size: 14
							}), g("repos.action.details")]
						}),
						!t && e.counts.open_actions > 0 ? /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => c("queue"),
							children: [/* @__PURE__ */ m(Y, {
								name: "inbox",
								size: 14
							}), g("repos.card.queue.open")]
						}) : null,
						S.map((e) => /* @__PURE__ */ m("button", {
							type: "button",
							className: "studio-btn",
							...e === "install" || e === "upgrade" || e === "recover" ? { "data-variant": "primary" } : e === "remove" ? { "data-variant": "danger" } : {},
							disabled: i !== null,
							onClick: () => c(e),
							children: g(i === e && e === "rescan" ? "repos.action.rescanning" : i === e && e === "doctor" ? "repos.action.doctorRunning" : Ac[e])
						}, e))
					]
				});
				return t ? /* @__PURE__ */ h(Q, {
					title: g("repos.maintenance.title"),
					icon: "settings",
					children: [
						n,
						d,
						S.includes("doctor") ? /* @__PURE__ */ m($, { children: g("repos.action.doctorNote") }) : null,
						/* @__PURE__ */ m($, { children: g("repos.maintenance.note") })
					]
				}) : n;
			})()
		]
	});
}
//#endregion
//#region src/repos/RepoList.tsx
var Nc = [
	"recover",
	"install",
	"upgrade"
];
function Pc(e) {
	let t = Oc(e);
	return Nc.find((e) => t.includes(e)) ?? null;
}
var Fc = {
	recover: "repos.action.recover",
	install: "repos.action.install",
	upgrade: "repos.action.upgrade"
};
function Ic({ repos: e, desktop: t, onAction: n }) {
	let r = H(), { t: i } = r;
	return /* @__PURE__ */ h("table", {
		className: "studio-tbl studio-repotbl",
		children: [
			/* @__PURE__ */ m("caption", {
				className: "studio-sr",
				children: i("repos.lede")
			}),
			/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.repository")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.aidlc")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.engine")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.intents")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.queue")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.git")
				}),
				/* @__PURE__ */ m("th", {
					scope: "col",
					children: i("repos.col.actions")
				})
			] }) }),
			/* @__PURE__ */ m("tbody", { children: e.map((e) => {
				let a = t ? Pc(e) : null;
				return /* @__PURE__ */ h("tr", {
					"data-attention": e.availability === "available" ? void 0 : "true",
					children: [
						/* @__PURE__ */ h("th", {
							scope: "row",
							className: "studio-rowhead",
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-strong",
									children: e.label
								}),
								e.archived ? /* @__PURE__ */ m("span", {
									className: "studio-muted",
									children: i("repos.metadata.archived")
								}) : null,
								/* @__PURE__ */ m("span", {
									className: "studio-subpath",
									children: e.canonical_path
								})
							]
						}),
						/* @__PURE__ */ m("td", { children: /* @__PURE__ */ h("div", {
							className: "studio-chiplist",
							children: [/* @__PURE__ */ m(Tc, { status: e.install.status }), e.availability === "available" ? null : /* @__PURE__ */ m("span", {
								className: "studio-muted",
								children: i(`enum.availability.${e.availability}`)
							})]
						}) }),
						/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m(Ec, { repo: e }) }),
						/* @__PURE__ */ m("td", {
							className: "studio-mono",
							children: r.fmt.number(e.counts.intents)
						}),
						/* @__PURE__ */ m("td", {
							className: "studio-mono",
							children: r.fmt.number(e.counts.open_actions)
						}),
						/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m(qs, { git: e.git }) }),
						/* @__PURE__ */ h("td", { children: [/* @__PURE__ */ h("div", {
							className: "studio-repo-actions",
							children: [/* @__PURE__ */ h("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								onClick: () => n(e.repo_id, "open"),
								children: [/* @__PURE__ */ m(Y, {
									name: "chevron",
									size: 13
								}), i("repos.action.details")]
							}), a ? /* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn studio-btn-sm",
								"data-variant": a === "recover" ? "danger" : "primary",
								onClick: () => n(e.repo_id, a),
								children: i(Fc[a])
							}) : null]
						}), /* @__PURE__ */ m("span", {
							className: "studio-sr",
							children: i("repos.a11y.repoRow", {
								label: e.label,
								path: e.canonical_path,
								install: i(`enum.installStatus.${e.install.status}`),
								availability: i(`enum.availability.${e.availability}`),
								intents: q(r, "repos.counts.intents", e.counts.intents),
								queue: q(r, "repos.counts.open", e.counts.open_actions)
							})
						})] })
					]
				}, e.repo_id);
			}) })
		]
	});
}
//#endregion
//#region src/repos/TransactionDrawer.tsx
var Lc = [
	"committed",
	"rolled_back",
	"recovery_required",
	"failed"
], Rc = {
	staged: "accent",
	leased: "accent",
	backed_up: "accent",
	written: "accent",
	merged: "accent",
	validated: "accent",
	committed: "ok",
	rolling_back: "warn",
	rolled_back: "warn",
	recovery_required: "danger",
	failed: "danger"
}, zc = {
	staged: "clock",
	leased: "lock",
	backed_up: "clock",
	written: "clock",
	merged: "clock",
	validated: "clock",
	committed: "check",
	rolling_back: "warn",
	rolled_back: "warn",
	recovery_required: "recovery",
	failed: "recovery"
};
function Bc(e) {
	return e.ok === !0 ? "ok" : e.ok === !1 ? "failed" : "running";
}
function Vc(e, t) {
	let n = `install.step.${t}`;
	return e.has(n) ? e.t(n) : t;
}
function Hc({ api: e, repoId: t, repoLabel: n, transactionId: r, onClose: a, onSettled: s }) {
	let c = H(), { t: l } = c, f = J(`transaction:${t}:${r}`, i((n) => e.transaction(t, r, { signal: n }), [
		e,
		t,
		r
	]), { revalidateOn: [
		"transaction.updated",
		"repo.updated",
		"reset"
	] }), g = f.data?.transaction ?? null, _ = g?.status ?? null, [v, y] = d(!1), [b, x] = d(null);
	o(() => {
		y(!1), x(null);
	}, [r]);
	let S = async () => {
		y(!0), x(null);
		try {
			await e.cancelTransaction(t, r), await f.refresh();
		} catch (e) {
			x(W(e));
		} finally {
			y(!1);
		}
	}, C = u(null);
	return o(() => {
		_ !== null && Lc.includes(_) && C.current !== _ && (C.current = _, s?.(_));
	}, [_, s]), /* @__PURE__ */ h("section", {
		className: "studio-drawer",
		"aria-label": l("install.tx.title", { id: r }),
		children: [
			/* @__PURE__ */ h("div", {
				className: "studio-spread studio-preview-head",
				children: [/* @__PURE__ */ m("h2", {
					className: "studio-mono",
					children: l("install.tx.title", { id: r })
				}), /* @__PURE__ */ h("button", {
					type: "button",
					className: "studio-btn",
					onClick: a,
					children: [/* @__PURE__ */ m(Y, {
						name: "close",
						size: 14
					}), l("install.tx.close")]
				})]
			}),
			f.error ? /* @__PURE__ */ m(Ls, { error: f.error }) : null,
			b ? /* @__PURE__ */ m(Ls, { error: b }) : null,
			g === null ? f.loading ? /* @__PURE__ */ m("p", {
				className: "studio-muted",
				children: l("common.loading")
			}) : null : /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m(Fs, { rows: [
					{
						key: "kind",
						label: l("install.tx.kind"),
						value: l(`install.tx.kind.${g.kind}`)
					},
					{
						key: "status",
						label: l("install.tx.status"),
						value: /* @__PURE__ */ m(X, {
							tone: Rc[g.status],
							icon: zc[g.status],
							children: l(`install.txStatus.${g.status}`)
						})
					},
					{
						key: "engine",
						label: l("install.version.to"),
						value: g.kind === "uninstall" && !g.steps.some((e) => e.name === "confirm_recovery") ? l("install.version.notInstalled") : g.engine_version,
						mono: !0
					},
					{
						key: "started",
						label: l("install.tx.started"),
						value: K(c, g.started_at)
					},
					{
						key: "finished",
						label: l("install.tx.finished"),
						value: g.finished_at ? K(c, g.finished_at) : null
					}
				] }),
				[
					"install",
					"upgrade",
					"recovery"
				].includes(g.kind) && !Lc.includes(g.status) ? /* @__PURE__ */ h("div", {
					className: "studio-col",
					children: [/* @__PURE__ */ m($, { children: l("install.cancel.explanation") }), /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: v || g.error === "cancel_requested" || g.status === "rolling_back",
						onClick: () => void S(),
						children: l("install.cancel.transaction")
					})]
				}) : null,
				/* @__PURE__ */ m(Q, {
					title: l("install.tx.steps"),
					icon: "clock",
					children: /* @__PURE__ */ m("ol", {
						className: "studio-steps",
						children: g.steps.map((e, t) => /* @__PURE__ */ h("li", {
							className: "studio-step",
							"data-state": Bc(e),
							children: [
								/* @__PURE__ */ m("span", {
									className: "studio-step-dot",
									"aria-hidden": !0,
									children: e.ok === !0 ? /* @__PURE__ */ m(Y, {
										name: "check",
										size: 10,
										strokeWidth: 2.4
									}) : e.ok === !1 ? /* @__PURE__ */ m(Y, {
										name: "close",
										size: 10,
										strokeWidth: 2.4
									}) : /* @__PURE__ */ m(Y, {
										name: "clock",
										size: 10,
										strokeWidth: 2
									})
								}),
								/* @__PURE__ */ h("span", {
									className: "studio-grow",
									children: [Vc(c, e.name), /* @__PURE__ */ h("span", {
										className: "studio-step-state",
										children: [" · ", l(`install.stepState.${Bc(e)}`)]
									})]
								}),
								/* @__PURE__ */ m("span", {
									className: "studio-mono studio-muted",
									children: K(c, e.started_at)
								})
							]
						}, `${e.name}-${t}`))
					})
				}),
				g.error === "cancel_requested" ? /* @__PURE__ */ m("p", {
					className: "studio-note",
					role: "status",
					children: l("install.cancel.requested")
				}) : g.error === "cancelled" ? /* @__PURE__ */ m("p", {
					className: "studio-note",
					role: "status",
					children: l("install.cancel.finished")
				}) : g.error ? /* @__PURE__ */ h("div", {
					className: "studio-failure-detail",
					children: [
						/* @__PURE__ */ m("strong", { children: l("install.tx.error") }),
						" ",
						/* @__PURE__ */ m("span", {
							className: "studio-wrap-any",
							children: g.error
						})
					]
				}) : null,
				g.status === "recovery_required" ? /* @__PURE__ */ m(_c, {
					repoLabel: n,
					failedDir: g.failed_dir
				}) : /* @__PURE__ */ h(p, { children: [
					g.status === "rolled_back" ? /* @__PURE__ */ m($, {
						tone: "warn",
						children: l("install.tx.rolledBack")
					}) : null,
					g.status === "committed" ? /* @__PURE__ */ m($, { children: l(g.kind === "uninstall" ? "install.tx.uninstalled" : "install.tx.committed", { version: g.engine_version }) }) : null,
					g.failed_dir ? /* @__PURE__ */ h("p", {
						className: "studio-help studio-wrap-any",
						children: [
							l("install.tx.failedDir"),
							" ",
							/* @__PURE__ */ m("span", {
								className: "studio-mono",
								children: g.failed_dir
							})
						]
					}) : null
				] }),
				/* @__PURE__ */ m("span", {
					className: "studio-sr",
					role: "status",
					"aria-live": "polite",
					children: l("install.tx.progress", {
						kind: l(`install.tx.kind.${g.kind}`),
						id: r,
						status: l(`install.txStatus.${g.status}`)
					})
				})
			] })
		]
	});
}
//#endregion
//#region src/repos/MaintenancePanel.tsx
function Uc({ api: e, repoId: t, onClose: n, onChanged: r }) {
	let i = H(), { t: a, has: s } = i, [c, l] = d(null), [f, p] = d([]), [g, _] = d(!1), [v, y] = d(!1), [b, x] = d(null), [S, C] = d(null), w = u(!0);
	async function T(n = [], r = !1) {
		y(!0), _(!1), x(null);
		try {
			let i = await e.maintenancePreview(t, n);
			if (!w.current) return;
			l(i), p(i.entry_ids), _(r && i.can_cleanup);
		} catch (e) {
			w.current && x(W(e));
		} finally {
			w.current && y(!1);
		}
	}
	o(() => (w.current = !0, T(), () => {
		w.current = !1;
	}), [e, t]);
	async function E() {
		if (g && c?.can_cleanup) {
			y(!0), _(!1), x(null);
			try {
				let n = await e.cleanupMaintenance(t, c.entry_ids, c.plan_digest);
				if (!w.current) return;
				C(n), p([]), r(), await T();
			} catch (e) {
				w.current && x(W(e));
			} finally {
				w.current && y(!1);
			}
		}
	}
	let D = (e) => s(`maintenance.reason.${e}`) ? a(`maintenance.reason.${e}`) : e;
	return /* @__PURE__ */ h(Q, {
		title: a("maintenance.title"),
		icon: "doc",
		children: [
			/* @__PURE__ */ m($, { children: a("maintenance.description") }),
			v ? /* @__PURE__ */ m("p", {
				role: "status",
				children: a("common.loading")
			}) : null,
			b ? /* @__PURE__ */ m(Ls, { error: b }) : null,
			S ? /* @__PURE__ */ h("div", {
				role: "status",
				children: [/* @__PURE__ */ m("p", { children: a(S.ok ? "maintenance.completed" : "maintenance.partial", { count: S.deleted.length }) }), S.failures.map((e) => /* @__PURE__ */ h("p", { children: [
					e.id,
					": ",
					D(e.reason)
				] }, e.id))]
			}) : null,
			c?.entries.length === 0 ? /* @__PURE__ */ m("p", { children: a("maintenance.empty") }) : null,
			c && c.entries.length > 0 ? /* @__PURE__ */ m("div", {
				className: "studio-maintenance-table",
				children: /* @__PURE__ */ h("table", {
					className: "studio-tbl",
					children: [/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
						/* @__PURE__ */ m("th", { children: a("maintenance.select") }),
						/* @__PURE__ */ m("th", { children: a("maintenance.path") }),
						/* @__PURE__ */ m("th", { children: a("maintenance.size") }),
						/* @__PURE__ */ m("th", { children: a("maintenance.state") })
					] }) }), /* @__PURE__ */ m("tbody", { children: c.entries.map((e) => /* @__PURE__ */ h("tr", { children: [
						/* @__PURE__ */ m("td", { children: /* @__PURE__ */ m("input", {
							type: "checkbox",
							"aria-label": e.relative_path,
							disabled: v || e.protected,
							checked: f.includes(e.id),
							onChange: (t) => {
								_(!1), C(null), p((n) => t.target.checked ? [...n, e.id] : n.filter((t) => t !== e.id));
							}
						}) }),
						/* @__PURE__ */ m("th", {
							scope: "row",
							className: "studio-mono studio-wrap-any",
							children: e.relative_path
						}),
						/* @__PURE__ */ m("td", { children: e.size_bytes === null ? a("common.unavailable") : je(i, e.size_bytes) }),
						/* @__PURE__ */ m("td", { children: e.reason ? D(e.reason) : a(`install.txStatus.${e.status}`) })
					] }, e.id)) })]
				})
			}) : null,
			g && c ? /* @__PURE__ */ m($, {
				tone: "warn",
				children: a("maintenance.confirmNote", {
					count: c.totals.selected_entries,
					size: je(i, c.totals.selected_bytes)
				})
			}) : null,
			/* @__PURE__ */ h("div", {
				className: "studio-repo-actions",
				children: [
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: v,
						onClick: n,
						children: a("common.close")
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: v,
						onClick: () => void T(),
						children: a("maintenance.refresh")
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						disabled: v || f.length === 0,
						onClick: () => void T(f, !0),
						children: a("maintenance.preview")
					}),
					g ? /* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						"data-variant": "danger",
						disabled: v,
						onClick: () => void E(),
						children: a("maintenance.confirm")
					}) : null
				]
			})
		]
	});
}
//#endregion
//#region src/repos/UpgradePreview.tsx
function Wc({ api: e, repoId: t, install: n, onStarted: r, onClose: i }) {
	let a = H(), { t: o } = a;
	return /* @__PURE__ */ m(mc, {
		api: e,
		repoId: t,
		kind: "upgrade",
		targetVersion: n.bundled_engine_version,
		onStarted: r,
		onClose: i,
		intro: /* @__PURE__ */ h(p, { children: [
			/* @__PURE__ */ h("div", {
				className: "studio-chiplist",
				children: [
					/* @__PURE__ */ m(X, {
						icon: "install",
						mono: !0,
						title: o("install.version.from"),
						children: n.engine_version ?? o("install.version.notInstalled")
					}),
					/* @__PURE__ */ m(Y, {
						name: "chevron",
						size: 13
					}),
					/* @__PURE__ */ m(X, {
						tone: n.newer_installed ? "warn" : "accent",
						icon: "install",
						mono: !0,
						title: o("install.version.to"),
						children: n.bundled_engine_version
					}),
					n.drift_count > 0 ? /* @__PURE__ */ m(X, {
						tone: "warn",
						icon: "warn",
						children: q(a, "repos.drift", n.drift_count)
					}) : null
				]
			}),
			n.newer_installed ? /* @__PURE__ */ m($, {
				tone: "warn",
				children: o("repos.engine.newerInstalled")
			}) : null,
			/* @__PURE__ */ m($, { children: o("install.retire.body") })
		] })
	});
}
//#endregion
//#region src/repos/ReposView.tsx
var Gc = "(min-width: 900px)";
function Kc() {
	let e = i((e) => {
		if (typeof window.matchMedia != "function") return () => {};
		let t = window.matchMedia(Gc);
		return t.addEventListener("change", e), () => t.removeEventListener("change", e);
	}, []);
	return f(e, () => typeof window.matchMedia != "function" || window.matchMedia(Gc).matches, () => !0);
}
var qc = {
	kind: "none",
	repoId: ""
};
function Jc({ route: e, go: t }) {
	let n = H(), { t: r } = n, { api: a, repos: s } = jl(), c = Kc(), [l, f] = d(qc), [g, _] = d(null), [v, y] = d(null), [b, x] = d(null), [S, C] = d(null), w = u(0), [T, E] = d(""), [D, O] = d(""), [k, A] = d(!1), [j, M] = d(""), N = J(k ? "repos-with-archived" : null, i((e) => a.repos(!0, { signal: e }), [a]), { revalidateOn: [
		"repo.updated",
		"repo.removed",
		"reset"
	] }), P = J(e.repo ? `repo-detail:${e.repo}` : null, i((t) => a.repo(e.repo, { signal: t }), [a, e.repo]), { revalidateOn: [
		"repo.updated",
		"repo.removed",
		"transaction.updated",
		"intent.updated",
		"reset"
	] }), F = k ? N.data : s.data, I = F?.repos ?? [], L = P.data?.repo ?? I.find((t) => t.repo_id === e.repo) ?? null, R = P.data?.transactions ?? [], ee = S?.repoId === L?.repo_id ? S?.feedback : null, z = !c || l.kind === "none" ? "none" : l.kind === "add" || l.repoId === e.repo ? l.kind : "none", B = i(() => f(qc), []);
	o(() => (_(null), y(null), x(null), M(""), C(null), w.current += 1, () => {
		w.current += 1;
	}), [e.repo]);
	let V = i(() => {
		s.refresh(), k && N.refresh(), e.repo && P.refresh();
	}, [
		s,
		N,
		k,
		P,
		e.repo
	]), te = i((e) => {
		y(e instanceof U ? e : new U("internal_error", String(e), {}, 0));
	}, []), ne = i(async (e, n) => {
		switch (y(null), x(null), n) {
			case "open":
				t({
					repo: e,
					tx: ""
				});
				return;
			case "queue":
				t({
					view: "actions",
					repo: e
				});
				return;
			case "intents":
				t({
					view: "intents",
					repo: e
				});
				return;
			case "new-intent":
				t({
					...yt,
					view: "new-intent",
					repo: e
				});
				return;
			case "install":
			case "upgrade":
			case "recover":
			case "uninstall":
			case "cleanup":
			case "rollback":
				t({
					repo: e,
					tx: ""
				}), f({
					kind: n,
					repoId: e
				});
				return;
			case "rebind":
				t({ repo: e }), E(""), f({
					kind: "rebind",
					repoId: e
				});
				return;
			case "remove":
				t({ repo: e }), f({
					kind: "remove",
					repoId: e
				});
				return;
			case "rename":
				t({ repo: e }), O(I.find((t) => t.repo_id === e)?.label ?? L?.label ?? ""), f({
					kind: "rename",
					repoId: e
				});
				return;
			case "archive":
				t({ repo: e }), f({
					kind: "archive",
					repoId: e
				});
				return;
			case "unarchive":
				_("unarchive");
				try {
					await a.updateRepoMetadata(e, { archived: !1 }), V();
				} catch (e) {
					te(e);
				} finally {
					_(null);
				}
				return;
			case "rescan":
				_("rescan");
				try {
					await a.rescanRepo(e), V();
				} catch (e) {
					te(e);
				} finally {
					_(null);
				}
				return;
			case "doctor": {
				let t = ++w.current;
				C({
					repoId: e,
					feedback: { status: "running" }
				});
				try {
					let n = await a.doctorRepo(e);
					if (w.current !== t) return;
					C({
						repoId: e,
						feedback: {
							status: "completed",
							result: n.result
						}
					}), V();
				} catch (n) {
					if (w.current !== t) return;
					let r = n instanceof U ? n : new U("internal_error", String(n), {}, 0);
					C({
						repoId: e,
						feedback: {
							status: "failed",
							error: r
						}
					});
				}
				return;
			}
		}
	}, [
		a,
		t,
		V,
		te,
		r,
		n,
		I,
		L
	]), re = async () => {
		if (!(!L || z !== "rename" && z !== "archive")) {
			_(z);
			try {
				await a.updateRepoMetadata(L.repo_id, z === "rename" ? { label: D.trim() } : { archived: !0 }), B(), V(), z === "archive" && t({
					repo: "",
					tx: ""
				});
			} catch (e) {
				te(e);
			} finally {
				_(null);
			}
		}
	}, ie = i(async () => {
		if (!L) return;
		let e = T.trim();
		if (!e.startsWith("/") && !e.startsWith("~")) {
			y(new U("bad_path", r("repos.add.pathNotAbsolute"), {}, 400));
			return;
		}
		_("rebind");
		try {
			await a.rebindRepo(L.repo_id, e), B(), V();
		} catch (e) {
			te(e);
		} finally {
			_(null);
		}
	}, [
		a,
		L,
		T,
		V,
		te,
		B,
		r
	]), ae = i(async () => {
		if (L) {
			_("remove");
			try {
				await a.removeRepo(L.repo_id), B(), t({
					repo: "",
					tx: ""
				}), s.refresh();
			} catch (e) {
				te(e);
			} finally {
				_(null);
			}
		}
	}, [
		a,
		L,
		t,
		s,
		te,
		B
	]), oe = i((e) => {
		B(), t({ tx: e }), V();
	}, [
		B,
		t,
		V
	]);
	return /* @__PURE__ */ h("div", {
		className: "studio-scroll",
		children: [/* @__PURE__ */ h("div", {
			className: "studio-page",
			children: [
				/* @__PURE__ */ h("div", {
					className: "studio-spread",
					children: [/* @__PURE__ */ m("h1", { children: r("repos.title") }), c ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn",
						"data-variant": "primary",
						onClick: () => f({
							kind: "add",
							repoId: ""
						}),
						children: [/* @__PURE__ */ m(Y, {
							name: "plus",
							size: 14
						}), r("repos.add.open")]
					}) : null]
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-lede",
					children: r("repos.lede")
				}),
				/* @__PURE__ */ h("label", {
					className: "studio-check",
					children: [/* @__PURE__ */ m("input", {
						type: "checkbox",
						checked: k,
						onChange: (e) => A(e.target.checked)
					}), r("repos.metadata.showArchived")]
				}),
				k && N.error ? /* @__PURE__ */ m(Ls, { error: N.error }) : null,
				c ? null : /* @__PURE__ */ h("div", {
					className: "studio-failure-detail studio-desktop-only",
					"data-tone": "info",
					children: [/* @__PURE__ */ h("p", {
						className: "studio-row studio-strong",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 15
						}), r("repos.desktopOnly.title")]
					}), /* @__PURE__ */ m("p", { children: r("repos.desktopOnly.body") })]
				}),
				F ? /* @__PURE__ */ m("p", {
					className: "studio-help studio-mono",
					children: r("repos.totals", {
						repos: n.fmt.number(F.totals.repos),
						unavailable: n.fmt.number(F.totals.unavailable),
						open: n.fmt.number(F.totals.open_actions)
					})
				}) : null,
				s.error ? /* @__PURE__ */ m(Ls, { error: s.error }) : null,
				P.error ? /* @__PURE__ */ m(Ls, { error: P.error }) : null,
				v ? /* @__PURE__ */ m(Ls, {
					error: v,
					reassure: r("repos.error.unchanged")
				}) : null,
				b ? /* @__PURE__ */ h("p", {
					className: "studio-note",
					role: "status",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "check",
							size: 13
						}),
						" ",
						b
					]
				}) : null,
				e.repo && L ? /* @__PURE__ */ h(p, { children: [
					/* @__PURE__ */ m("div", {
						className: "studio-repo-actions",
						children: /* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => t({
								repo: "",
								tx: ""
							}),
							children: [/* @__PURE__ */ m(Y, {
								name: "back",
								size: 14
							}), r("repos.detail.back")]
						})
					}),
					e.tx ? /* @__PURE__ */ m(Hc, {
						api: a,
						repoId: L.repo_id,
						repoLabel: L.label,
						transactionId: e.tx,
						onClose: () => t({ tx: "" }),
						onSettled: V
					}) : null,
					z === "install" ? /* @__PURE__ */ m(hc, {
						api: a,
						repoId: L.repo_id,
						bundledVersion: L.install.bundled_engine_version,
						onStarted: oe,
						onClose: B
					}) : null,
					z === "upgrade" ? /* @__PURE__ */ m(Wc, {
						api: a,
						repoId: L.repo_id,
						install: L.install,
						onStarted: oe,
						onClose: B
					}) : null,
					z === "uninstall" ? /* @__PURE__ */ m(mc, {
						api: a,
						repoId: L.repo_id,
						kind: "uninstall",
						onStarted: oe,
						onClose: B
					}) : null,
					z === "cleanup" ? /* @__PURE__ */ m(Uc, {
						api: a,
						repoId: L.repo_id,
						onClose: B,
						onChanged: V
					}, L.repo_id) : null,
					z === "rollback" ? /* @__PURE__ */ m(mc, {
						api: a,
						repoId: L.repo_id,
						kind: "rollback",
						targetVersion: L.install.rollback_target?.engine_version,
						onStarted: oe,
						onClose: B
					}) : null,
					z === "recover" ? /* @__PURE__ */ m(vc, {
						api: a,
						repoId: L.repo_id,
						repoLabel: L.label,
						failedDir: gc(R),
						onStarted: oe,
						onClose: B
					}) : null,
					z === "rebind" ? /* @__PURE__ */ h(Q, {
						title: r("repos.rebind.title", { label: L.label }),
						icon: "link",
						children: [
							/* @__PURE__ */ m($, { children: r("repos.rebind.body") }),
							/* @__PURE__ */ h("div", {
								className: "studio-field",
								children: [/* @__PURE__ */ m("label", {
									htmlFor: "studio-rebind-path",
									children: r("repos.add.pathLabel")
								}), /* @__PURE__ */ m("input", {
									id: "studio-rebind-path",
									className: "studio-input studio-mono",
									type: "text",
									spellCheck: !1,
									autoComplete: "off",
									value: T,
									onChange: (e) => E(e.target.value)
								})]
							}),
							/* @__PURE__ */ h("div", {
								className: "studio-repo-actions",
								children: [/* @__PURE__ */ m("button", {
									type: "button",
									className: "studio-btn",
									onClick: B,
									children: r("install.cancel")
								}), /* @__PURE__ */ m("button", {
									type: "button",
									className: "studio-btn",
									"data-variant": "primary",
									disabled: g !== null || T.trim() === "",
									onClick: () => void ie(),
									children: r("repos.rebind.confirm")
								})]
							})
						]
					}) : null,
					z === "remove" ? /* @__PURE__ */ h(Q, {
						title: r("repos.remove.title", { label: L.label }),
						icon: "close",
						children: [/* @__PURE__ */ m($, { children: r("repos.remove.body") }), /* @__PURE__ */ h("div", {
							className: "studio-repo-actions",
							children: [/* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								onClick: B,
								children: r("install.cancel")
							}), /* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								"data-variant": "danger",
								disabled: g !== null,
								onClick: () => void ae(),
								children: r("repos.remove.confirm")
							})]
						})]
					}) : null,
					z === "rename" || z === "archive" ? /* @__PURE__ */ h(Q, {
						title: r(z === "rename" ? "repos.action.rename" : "repos.action.archive"),
						icon: "repo",
						children: [z === "rename" ? /* @__PURE__ */ h("div", {
							className: "studio-field",
							children: [/* @__PURE__ */ m("label", {
								htmlFor: "studio-repo-label",
								children: r("repos.metadata.label")
							}), /* @__PURE__ */ m("input", {
								id: "studio-repo-label",
								className: "studio-input",
								value: D,
								onChange: (e) => O(e.target.value)
							})]
						}) : /* @__PURE__ */ m($, { children: r("repos.metadata.archiveBody") }), /* @__PURE__ */ h("div", {
							className: "studio-repo-actions",
							children: [/* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								onClick: B,
								children: r("install.cancel")
							}), /* @__PURE__ */ m("button", {
								type: "button",
								className: "studio-btn",
								"data-variant": "primary",
								disabled: g !== null || z === "rename" && !D.trim(),
								onClick: () => void re(),
								children: r(z === "rename" ? "repos.metadata.save" : "repos.action.archive")
							})]
						})]
					}) : null,
					/* @__PURE__ */ h(Mc, {
						repo: L,
						detailed: !0,
						desktop: c,
						installPreviewOpen: z === "install",
						busy: g ?? (ee?.status === "running" ? "doctor" : null),
						maintenanceFeedback: ee ? /* @__PURE__ */ m(Ks, { feedback: ee }) : null,
						transactions: R,
						intents: P.data?.intents ?? [],
						onAction: (e) => void ne(L.repo_id, e),
						onOpenTransaction: (e) => t({ tx: e }),
						children: [/* @__PURE__ */ m(Js, {
							api: a,
							repoId: L.repo_id,
							enabled: L.availability === "available"
						}), c && !L.archived && L.install.engine_dir && L.availability === "available" ? /* @__PURE__ */ h("details", {
							onToggle: (e) => M(e.currentTarget.open ? L.repo_id : ""),
							children: [/* @__PURE__ */ m("summary", { children: r("workspace.spaces.title") }), j === L.repo_id ? /* @__PURE__ */ m(Ns, {
								api: a,
								repoId: L.repo_id,
								repoLabel: L.label,
								onChanged: V
							}) : null]
						}, L.repo_id) : null]
					})
				] }) : I.length === 0 ? /* @__PURE__ */ h("div", {
					className: "studio-empty",
					children: [/* @__PURE__ */ m("p", {
						className: "studio-strong",
						children: r("repos.empty.title")
					}), /* @__PURE__ */ m("p", {
						className: "studio-muted",
						children: r("repos.empty.body")
					})]
				}) : c ? /* @__PURE__ */ m(Ic, {
					repos: I,
					desktop: c,
					onAction: (e, t) => void ne(e, t)
				}) : /* @__PURE__ */ m("div", {
					className: "studio-cardlist",
					children: I.map((e) => /* @__PURE__ */ m(Mc, {
						repo: e,
						desktop: c,
						onAction: (t) => void ne(e.repo_id, t)
					}, e.repo_id))
				}),
				/* @__PURE__ */ m($, { children: r("repos.footer") })
			]
		}), z === "add" && c ? /* @__PURE__ */ m(Gs, {
			api: a,
			repos: I,
			onClose: B,
			onRegistered: (e) => {
				B(), x(r("repos.registered", { label: e.label })), s.refresh(), t({
					repo: e.repo_id,
					tx: ""
				});
			},
			onOpenRepo: (e) => {
				B(), t({
					repo: e,
					tx: ""
				});
			}
		}) : null]
	});
}
//#endregion
//#region src/views/repos/index.tsx
var Yc = /* @__PURE__ */ O({ default: () => Jc }), Xc = "/apps/detail/aidlc-console";
function Zc(e) {
	return e ? e.console.installed && e.console.enabled && !e.applied : !1;
}
function Qc(e, t) {
	if (t === "migrate") return e("migration.resolution.migrate");
	if (t === "unavailable") return e("migration.resolution.unavailable");
	let [n, r] = t.split(":", 2);
	return n === "duplicate_of" ? e("migration.resolution.duplicate", { of: r ?? "" }) : n === "already_registered" ? e("migration.resolution.already_registered", { of: r ?? "" }) : e("migration.resolution.other", { raw: t });
}
function $c(e, t) {
	let n = W(t);
	return e.has(`errors.${n.code}`) ? e.t(`errors.${n.code}`) : n.message;
}
function el({ onApplied: e }) {
	let t = H(), { t: n } = t, r = xe(), a = y(), o = J("migration-status", i((e) => r.migrationStatus({ signal: e }), [r]), {
		interval: 0,
		revalidateOn: ["migration.updated", "reset"]
	}), [s, c] = d(null), [l, u] = d(null), [f, g] = d(null), [_, v] = d(null), [x, S] = d(!1), C = i(async () => {
		g("preview"), v(null);
		try {
			let e = await r.migrationPreview();
			return c(e.preview), e.preview;
		} catch (e) {
			return v(t.t("migration.previewFailed", { message: $c(t, e) })), null;
		} finally {
			g(null);
		}
	}, [r, t]), w = async () => {
		let i = s?.source_sha256;
		if (i) {
			g("apply"), v(null), S(!1);
			try {
				let t = await r.migrationApply(i);
				u(t.result), c(null), await o.refresh(), e?.();
			} catch (e) {
				W(e).code === "bad_body" ? (S(!0), await C()) : v(n("migration.failed", { message: $c(t, e) }));
			} finally {
				g(null);
			}
		}
	}, T = o.data, E = l ?? T?.result ?? null, D = !!l || !!T?.applied;
	if (!T || !T.console.installed && !T.preview_available && !D) return null;
	let O = Zc(T), k = T.console.installed ? T.console.enabled ? n("migration.consoleState.enabled") : n("migration.consoleState.disabled") : n("migration.consoleState.absent");
	return /* @__PURE__ */ h("section", {
		className: "studio-block studio-migration",
		"aria-label": n("migration.region"),
		children: [
			/* @__PURE__ */ m("h3", { children: n("migration.title") }),
			O ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "status",
				children: [/* @__PURE__ */ m(Y, {
					name: "warn",
					size: 15
				}), /* @__PURE__ */ h("div", {
					className: "studio-grow",
					children: [/* @__PURE__ */ m("strong", { children: n("migration.both.title") }), /* @__PURE__ */ m("p", { children: n("migration.both.body") })]
				})]
			}) : null,
			/* @__PURE__ */ m("p", {
				className: "studio-lede",
				children: n("migration.lede")
			}),
			/* @__PURE__ */ m("div", {
				className: "studio-row studio-wrapchips",
				children: /* @__PURE__ */ m(X, {
					icon: "install",
					children: `${n("migration.consoleState.title")}: ${k}`
				})
			}),
			D ? /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ m("h4", {
					className: "studio-subhead",
					children: n("migration.applied.title")
				}),
				E ? /* @__PURE__ */ h("dl", {
					className: "studio-evgrid",
					children: [
						/* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", { children: n("migration.applied.status") }), /* @__PURE__ */ m("dd", {
								className: "studio-mono",
								children: E.status
							})]
						}),
						/* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", { children: n("migration.applied.at") }), /* @__PURE__ */ m("dd", {
								className: "studio-mono",
								children: K(t, E.applied_at)
							})]
						}),
						/* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", { children: n("migration.applied.summary") }), /* @__PURE__ */ m("dd", { children: /* @__PURE__ */ m("dl", {
								className: "studio-evgrid",
								children: Object.entries(E.summary).map(([e, t]) => /* @__PURE__ */ h("div", {
									className: "studio-evgrid-pair",
									children: [/* @__PURE__ */ m("dt", {
										className: "studio-mono",
										children: e
									}), /* @__PURE__ */ m("dd", {
										className: "studio-mono studio-wrap-any",
										children: typeof t == "object" && t ? JSON.stringify(t) : String(t)
									})]
								}, e))
							}) })]
						})
					]
				}) : null,
				/* @__PURE__ */ h("p", {
					className: "studio-consequence",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "lock",
							size: 13
						}),
						" ",
						E?.backup_path ? n("migration.applied.backup", { path: E.backup_path }) : n("migration.applied.noBackup")
					]
				}),
				/* @__PURE__ */ m("h4", {
					className: "studio-subhead",
					children: n("migration.nextSteps.title")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-consequence",
					children: n("migration.nextSteps.desc")
				}),
				/* @__PURE__ */ m("ul", {
					className: "studio-list",
					children: (E?.next_steps ?? []).map((e) => /* @__PURE__ */ m("li", { children: t.has(`migration.nextSteps.${e}`) ? n(`migration.nextSteps.${e}`) : n("migration.nextSteps.other", { step: e }) }, e))
				}),
				/* @__PURE__ */ m("div", {
					className: "studio-row studio-wrapchips",
					children: /* @__PURE__ */ h(b, {
						type: "button",
						onClick: () => a(Xc),
						children: [/* @__PURE__ */ m(Y, {
							name: "external",
							size: 13
						}), n("migration.openConsole")]
					})
				})
			] }) : /* @__PURE__ */ h(p, { children: [
				/* @__PURE__ */ h("div", {
					className: "studio-migration-explain",
					children: [/* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("h4", {
						className: "studio-subhead",
						children: n("migration.moves.title")
					}), /* @__PURE__ */ h("ul", {
						className: "studio-list",
						children: [
							/* @__PURE__ */ m("li", { children: n("migration.moves.registry") }),
							/* @__PURE__ */ m("li", { children: n("migration.moves.archive") }),
							/* @__PURE__ */ m("li", { children: n("migration.moves.ids") })
						]
					})] }), /* @__PURE__ */ h("div", { children: [/* @__PURE__ */ m("h4", {
						className: "studio-subhead",
						children: n("migration.keeps.title")
					}), /* @__PURE__ */ h("ul", {
						className: "studio-list",
						children: [
							/* @__PURE__ */ m("li", { children: n("migration.keeps.secret") }),
							/* @__PURE__ */ m("li", { children: n("migration.keeps.repoData") }),
							/* @__PURE__ */ m("li", { children: n("migration.keeps.credentials") }),
							/* @__PURE__ */ m("li", { children: n("migration.keeps.actions") })
						]
					})] })]
				}),
				/* @__PURE__ */ h("div", {
					className: "studio-row studio-wrapchips",
					children: [/* @__PURE__ */ h(b, {
						type: "button",
						onClick: () => void C(),
						disabled: f !== null,
						children: [/* @__PURE__ */ m(Y, {
							name: "review",
							size: 13
						}), n(f === "preview" ? "migration.previewing" : s ? "migration.previewAgain" : "migration.preview")]
					}), /* @__PURE__ */ m("span", {
						className: "studio-muted",
						children: n("migration.previewNothing")
					})]
				}),
				/* @__PURE__ */ h("div", {
					role: "status",
					"aria-live": "polite",
					children: [x ? /* @__PURE__ */ m("p", {
						className: "studio-error",
						children: n("migration.stale")
					}) : null, _ ? /* @__PURE__ */ m("p", {
						className: "studio-error",
						children: _
					}) : null]
				}),
				s ? /* @__PURE__ */ h(p, { children: [
					/* @__PURE__ */ h("dl", {
						className: "studio-evgrid",
						children: [/* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", { children: n("migration.source") }), /* @__PURE__ */ m("dd", {
								className: "studio-mono studio-wrap-any",
								children: s.source_path
							})]
						}), /* @__PURE__ */ h("div", {
							className: "studio-evgrid-pair",
							children: [/* @__PURE__ */ m("dt", { children: n("migration.sourceDigest") }), /* @__PURE__ */ m("dd", {
								className: "studio-mono studio-wrap-any",
								children: s.source_sha256 ?? n("common.unavailable")
							})]
						})]
					}),
					s.applicable ? null : /* @__PURE__ */ h("div", {
						className: "studio-banner",
						"data-tone": "warn",
						role: "status",
						children: [/* @__PURE__ */ m(Y, {
							name: "info",
							size: 15
						}), /* @__PURE__ */ h("div", {
							className: "studio-grow",
							children: [/* @__PURE__ */ m("strong", { children: n("migration.notApplicable.title") }), /* @__PURE__ */ m("p", { children: s.reason && t.has(`migration.notApplicable.${s.reason}`) ? n(`migration.notApplicable.${s.reason}`) : n("migration.notApplicable.other", { reason: s.reason ?? n("common.unavailable") }) })]
						})]
					}),
					/* @__PURE__ */ m("h4", {
						className: "studio-subhead",
						children: n("migration.rows.title")
					}),
					s.rows.length === 0 ? /* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: n("migration.rows.none")
					}) : /* @__PURE__ */ h("table", {
						className: "studio-tbl",
						children: [/* @__PURE__ */ m("thead", { children: /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: n("migration.rows.label")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: n("migration.rows.path")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: n("migration.rows.added")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: n("migration.rows.resolution")
							}),
							/* @__PURE__ */ m("th", {
								scope: "col",
								children: n("migration.rows.detail")
							})
						] }) }), /* @__PURE__ */ m("tbody", { children: s.rows.map((e) => /* @__PURE__ */ h("tr", { children: [
							/* @__PURE__ */ h("td", { children: [/* @__PURE__ */ m("span", {
								className: "studio-strong",
								children: e.label
							}), /* @__PURE__ */ m("div", {
								className: "studio-mono studio-muted studio-small",
								children: e.legacy_id
							})] }),
							/* @__PURE__ */ m("td", {
								className: "studio-mono studio-wrap-any",
								children: e.path
							}),
							/* @__PURE__ */ m("td", {
								className: "studio-mono",
								children: e.added_at ? K(t, e.added_at) : n("common.unavailable")
							}),
							/* @__PURE__ */ m("td", { children: Qc(n, e.resolution) }),
							/* @__PURE__ */ m("td", {
								className: "studio-wrap-any",
								children: e.error ?? n("common.none")
							})
						] }, e.legacy_id)) })]
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: n("migration.counts", {
							rowsIn: t.fmt.number(s.rows_in),
							rowsOut: t.fmt.number(s.rows_out)
						})
					}),
					/* @__PURE__ */ m("div", {
						className: "studio-row studio-wrapchips",
						children: /* @__PURE__ */ h(b, {
							primary: !0,
							type: "button",
							onClick: () => void w(),
							disabled: f !== null || !s.applicable || !s.source_sha256,
							children: [/* @__PURE__ */ m(Y, {
								name: "check",
								size: 13
							}), n(f === "apply" ? "migration.applying" : "migration.apply")]
						})
					}),
					/* @__PURE__ */ m("p", {
						className: "studio-consequence",
						children: s.source_sha256 ? n("migration.applyHint") : n("migration.applyBlocked")
					})
				] }) : /* @__PURE__ */ m("p", {
					className: "studio-consequence",
					children: n("migration.applyNeedsPreview")
				})
			] })
		]
	});
}
//#endregion
//#region src/settings/AboutPanel.tsx
function tl({ health: e, versions: t, onOpenActivity: n }) {
	let r = H(), { t: i } = r, a = i("common.unavailable"), o = t?.studio ?? e?.version ?? a, s = t?.bundled_engine ?? e?.bundled_engine_version ?? a, c = t?.min_kirocrew ?? e?.min_kirocrew_version ?? a, l = t?.host ?? e?.host_version ?? null;
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m(bl, {
			id: "aidlc-about-versions",
			title: i("settings.about.title"),
			description: i("settings.about.desc"),
			children: () => /* @__PURE__ */ h("dl", {
				className: "studio-evgrid",
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-evgrid-pair",
						children: [/* @__PURE__ */ m("dt", { children: i("settings.about.studioVersion") }), /* @__PURE__ */ m("dd", {
							className: "studio-mono",
							children: o
						})]
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-evgrid-pair",
						children: [/* @__PURE__ */ m("dt", { children: i("settings.about.engineVersion") }), /* @__PURE__ */ m("dd", {
							className: "studio-mono",
							children: s
						})]
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-evgrid-pair",
						children: [/* @__PURE__ */ m("dt", { children: i("settings.about.minHost") }), /* @__PURE__ */ m("dd", {
							className: "studio-mono",
							children: c
						})]
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-evgrid-pair",
						children: [/* @__PURE__ */ m("dt", { children: i("settings.about.host") }), /* @__PURE__ */ m("dd", {
							className: "studio-mono",
							children: l ?? (e?.host?.attached === !1 ? i("settings.about.hostDetached") : a)
						})]
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-evgrid-pair",
						children: [/* @__PURE__ */ m("dt", { children: i("settings.about.platform") }), /* @__PURE__ */ m("dd", {
							className: "studio-mono",
							children: nl() || a
						})]
					})
				]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-about-update",
			title: i("settings.about.updateState"),
			description: i("settings.about.updateDesc"),
			children: () => /* @__PURE__ */ m(X, {
				icon: "install",
				children: i("settings.about.updateChip")
			})
		}),
		e ? /* @__PURE__ */ m(bl, {
			id: "aidlc-about-health",
			title: i("settings.about.status"),
			description: e.issues.length > 0 ? e.issues.join(" · ") : i(`settings.about.status.${e.status}`),
			children: () => /* @__PURE__ */ h("div", {
				className: "studio-col",
				children: [
					/* @__PURE__ */ h("div", {
						className: "studio-row studio-wrapchips",
						children: [/* @__PURE__ */ m(X, {
							tone: e.status === "healthy" ? "ok" : e.status === "degraded" ? "warn" : "danger",
							icon: e.status === "healthy" ? "check" : "warn",
							children: i(`settings.about.status.${e.status}`)
						}), e.issues.length > 0 ? /* @__PURE__ */ m(X, {
							tone: "warn",
							children: q(r, "settings.about.issues", e.issues.length)
						}) : null]
					}),
					/* @__PURE__ */ h("dl", {
						className: "studio-evgrid",
						children: [
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: i("settings.about.payload") }), /* @__PURE__ */ m("dd", { children: e.payload ? e.payload.mismatches > 0 ? q(r, "settings.about.payloadBad", e.payload.mismatches) : q(r, "settings.about.payloadOk", e.payload.file_count) : a })]
							}),
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: i("settings.about.tools") }), /* @__PURE__ */ m("dd", {
									className: "studio-row studio-wrapchips",
									children: ["bun", "git"].map((t) => {
										let n = e.tools?.[t];
										return n ? /* @__PURE__ */ m(X, {
											tone: n.found ? "neutral" : "warn",
											icon: n.found ? "check" : "warn",
											children: n.found ? i("settings.about.toolFound", {
												name: t,
												version: n.version ?? a
											}) : i("settings.about.toolMissing", { name: t })
										}, t) : /* @__PURE__ */ h(X, { children: [
											t,
											" · ",
											a
										] }, t);
									})
								})]
							}),
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: i("settings.about.storage") }), /* @__PURE__ */ m("dd", { children: e.storage ? e.storage.integrity === "ok" ? i("settings.about.storageOk", { schema: we(r, e.storage.schema_version) }) : i("settings.about.storageBad", { schema: we(r, e.storage.schema_version) }) : a })]
							}),
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: i("settings.about.reconciler") }), /* @__PURE__ */ m("dd", { children: e.reconciler ? e.reconciler.running ? i("settings.about.reconcilerRunning", { when: K(r, e.reconciler.last_tick_at) }) : i("settings.about.reconcilerStopped") : a })]
							}),
							/* @__PURE__ */ h("div", {
								className: "studio-evgrid-pair",
								children: [/* @__PURE__ */ m("dt", { children: i("settings.about.boot") }), /* @__PURE__ */ m("dd", {
									className: "studio-mono studio-wrap-any",
									children: e.boot_id ?? a
								})]
							})
						]
					}),
					n ? /* @__PURE__ */ h("button", {
						type: "button",
						className: "studio-btn studio-btn-sm",
						onClick: n,
						children: [/* @__PURE__ */ m(Y, {
							name: "activity",
							size: 13
						}), i("activity.page.title")]
					}) : null
				]
			})
		}) : null
	] });
}
function nl() {
	return typeof navigator > "u" ? "" : navigator.userAgentData?.platform || navigator.platform || "";
}
//#endregion
//#region src/settings/BunSetting.tsx
function rl({ api: e, tool: t, onChanged: n }) {
	let { t: r, has: i } = H(), [a, s] = d(t?.configured_path ?? ""), [c, l] = d(!1), [u, f] = d(!1), [p, g] = d(""), [_, v] = d(null), [y, b] = d(!1), x = _ ?? t;
	o(() => {
		v(null);
	}, [t]), o(() => {
		c || s(x?.configured_path ?? "");
	}, [x?.configured_path, c]);
	let S = async (t) => {
		f(!0), g(r("settings.bun.checking"));
		try {
			let r = t === "probe" ? await e.probeBun() : await e.configureBun(t === "auto" ? null : a.trim());
			v(r.tool), b(!0), t !== "probe" && (l(!1), s(r.tool.configured_path ?? "")), g(""), n();
		} catch (e) {
			let t = W(e);
			g(i(`errors.${t.code}`) ? r(`errors.${t.code}`) : t.message);
		} finally {
			f(!1);
		}
	};
	return /* @__PURE__ */ m(bl, {
		id: "aidlc-bun",
		title: r("settings.bun.title"),
		description: r("settings.bun.desc"),
		children: (e) => /* @__PURE__ */ h("div", {
			className: "studio-col",
			children: [
				/* @__PURE__ */ m("label", {
					htmlFor: "aidlc-bun-path",
					children: r("settings.bun.path")
				}),
				/* @__PURE__ */ m("input", {
					id: "aidlc-bun-path",
					className: "studio-input",
					value: a,
					disabled: u,
					"aria-describedby": e,
					placeholder: "/absolute/path/to/bun",
					onChange: (e) => {
						s(e.target.value), l(!0);
					}
				}),
				/* @__PURE__ */ h("div", {
					className: "studio-row studio-wrapchips",
					children: [
						/* @__PURE__ */ m("button", {
							className: "studio-btn",
							disabled: u || !a.trim(),
							onClick: () => void S("save"),
							children: r("settings.bun.save")
						}),
						/* @__PURE__ */ m("button", {
							className: "studio-btn",
							disabled: u,
							onClick: () => void S("probe"),
							children: r("settings.bun.probe")
						}),
						/* @__PURE__ */ m("button", {
							className: "studio-btn",
							disabled: u,
							onClick: () => void S("auto"),
							children: r("settings.bun.auto")
						})
					]
				}),
				x?.path ? /* @__PURE__ */ h("code", {
					className: "studio-wrap-any",
					children: [
						x.path,
						" · ",
						x.version
					]
				}) : null,
				x?.searched?.length ? /* @__PURE__ */ m("p", {
					className: "studio-muted studio-wrap-any",
					children: r("settings.bun.searched", { paths: x.searched.join(" · ") })
				}) : null,
				/* @__PURE__ */ m("p", {
					role: "status",
					"aria-live": "polite",
					children: p || (y && x ? r(x.found ? "settings.bun.ready" : "settings.bun.missing") : "")
				})
			]
		})
	});
}
//#endregion
//#region src/settings/AdvisorSetting.tsx
function il({ control: e, repos: t }) {
	let n = H(), { t: r } = n, i = e.capabilities.advisor, a = i?.available !== !1, o = e.values.advisor.auto_draft_repo_ids, s = t ?? [], c = new Set(s.map((e) => e.repo_id)), l = t === null ? [] : o.filter((e) => !c.has(e)), u = e.busy || !a || !e.values.advisor.enabled, d = (t, n) => {
		let r = n ? [...o, t] : o.filter((e) => e !== t);
		e.save({ advisor: { auto_draft_repo_ids: [...new Set(r)] } });
	};
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-advisor",
			title: r("settings.advisor.title"),
			description: r("settings.advisor.desc"),
			children: (t) => /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrapchips",
				children: [a ? null : /* @__PURE__ */ m(Sl, { capability: i }), /* @__PURE__ */ h("label", {
					className: "studio-check",
					children: [/* @__PURE__ */ m("input", {
						type: "checkbox",
						checked: e.values.advisor.enabled,
						disabled: e.busy,
						"aria-describedby": t,
						onChange: (t) => e.save({ advisor: { enabled: t.target.checked } })
					}), /* @__PURE__ */ m("span", { children: r("settings.advisor.label") })]
				})]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-advisor-auto",
			title: r("settings.advisor.autoDraft.title"),
			description: r("settings.advisor.autoDraft.desc"),
			children: (i) => /* @__PURE__ */ m("div", {
				className: "studio-col",
				"aria-describedby": i,
				children: t === null ? /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: r("common.loading")
				}) : s.length === 0 && l.length === 0 ? /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: r("settings.advisor.autoDraft.none")
				}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("ul", {
					className: "studio-mutelist",
					children: [s.map((e) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("label", {
						className: "studio-check",
						children: [
							/* @__PURE__ */ m("input", {
								type: "checkbox",
								checked: o.includes(e.repo_id),
								disabled: u,
								onChange: (t) => d(e.repo_id, t.target.checked),
								"aria-label": r("settings.advisor.autoDraft.label", { repo: e.label })
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-strong",
								children: e.label
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-mono studio-muted studio-small studio-trunc",
								children: e.canonical_path
							})
						]
					}) }, e.repo_id)), l.map((t) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("label", {
						className: "studio-check",
						children: [/* @__PURE__ */ m("input", {
							type: "checkbox",
							checked: !0,
							disabled: e.busy,
							onChange: () => d(t, !1),
							"aria-label": r("settings.advisor.autoDraft.label", { repo: t })
						}), /* @__PURE__ */ m("span", {
							className: "studio-mono",
							children: r("settings.advisor.autoDraft.orphan", { id: t })
						})]
					}) }, t))]
				}), /* @__PURE__ */ m("span", {
					className: "studio-muted studio-small",
					children: q(n, "settings.advisor.autoDraft.count", o.length)
				})] })
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-advisor-model",
			title: r("settings.advisor.model.title"),
			description: r("settings.advisor.model.desc"),
			children: () => /* @__PURE__ */ m(X, {
				icon: "advisor",
				tone: "aim",
				children: r("settings.advisor.model.chip")
			})
		})
	] });
}
//#endregion
//#region src/settings/AutomationSetting.tsx
var al = {
	lo: 1,
	hi: 8
}, ol = {
	lo: 1,
	hi: 1e3
}, sl = /^([01]\d|2[0-3]):[0-5]\d$/;
function cl({ id: e, label: t, value: n, lo: r, hi: i, disabled: a, describedBy: s, onCommit: c }) {
	let [l, u] = d(String(n));
	o(() => u(String(n)), [n]);
	let f = () => {
		let e = Number(l);
		if (!Number.isInteger(e) || e < r || e > i) {
			u(String(n));
			return;
		}
		e !== n && c(e);
	};
	return /* @__PURE__ */ h("label", {
		className: "studio-sfield",
		htmlFor: e,
		children: [/* @__PURE__ */ m("span", {
			className: "studio-sr",
			children: t
		}), /* @__PURE__ */ m("input", {
			id: e,
			type: "number",
			className: "studio-mono studio-num",
			min: r,
			max: i,
			step: 1,
			value: l,
			disabled: a,
			"aria-describedby": s,
			onChange: (e) => u(e.target.value),
			onBlur: f,
			onKeyDown: (e) => {
				e.key === "Enter" && f();
			}
		})]
	});
}
function ll({ id: e, label: t, value: n, disabled: r, describedBy: i, onCommit: a }) {
	let [s, c] = d(n);
	o(() => c(n), [n]);
	let l = () => {
		if (!sl.test(s)) {
			c(n);
			return;
		}
		s !== n && a(s);
	};
	return /* @__PURE__ */ h("label", {
		className: "studio-sfield",
		htmlFor: e,
		children: [/* @__PURE__ */ m("span", {
			className: "studio-sr",
			children: t
		}), /* @__PURE__ */ m("input", {
			id: e,
			type: "time",
			className: "studio-mono",
			value: s,
			disabled: r,
			"aria-describedby": i,
			onChange: (e) => c(e.target.value),
			onBlur: l,
			onKeyDown: (e) => {
				e.key === "Enter" && l();
			}
		})]
	});
}
function ul({ control: e }) {
	let t = H(), { t: n } = t, r = e.values.night_window;
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-night",
			title: n("settings.night.title"),
			description: n("settings.night.desc"),
			children: (t) => /* @__PURE__ */ h("div", {
				className: "studio-col",
				"aria-describedby": t,
				children: [
					/* @__PURE__ */ m(Sl, { capability: e.capabilities.night_window }),
					/* @__PURE__ */ m(X, {
						mono: !0,
						icon: "moon",
						children: n("settings.night.window", {
							start: r.start_local,
							end: r.end_local
						})
					}),
					/* @__PURE__ */ h("div", {
						className: "studio-row",
						children: [/* @__PURE__ */ m(ll, {
							id: "aidlc-set-night-start",
							label: n("settings.night.start"),
							value: r.start_local,
							disabled: e.busy,
							describedBy: t,
							onCommit: (t) => e.save({ night_window: { start_local: t } })
						}), /* @__PURE__ */ m(ll, {
							id: "aidlc-set-night-end",
							label: n("settings.night.end"),
							value: r.end_local,
							disabled: e.busy,
							describedBy: t,
							onCommit: (t) => e.save({ night_window: { end_local: t } })
						})]
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-muted studio-small",
						children: n("settings.night.storedOnly")
					})
				]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-turncap",
			title: n("settings.turnCap.title"),
			description: n("settings.turnCap.desc"),
			children: (t) => /* @__PURE__ */ m(cl, {
				id: "aidlc-set-turncap-input",
				label: n("settings.turnCap.label"),
				value: r.turn_cap,
				lo: ol.lo,
				hi: ol.hi,
				disabled: e.busy,
				describedBy: t,
				onCommit: (t) => e.save({ night_window: { turn_cap: t } })
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-creditcap",
			title: n("settings.creditCap.title"),
			description: n("settings.creditCap.desc"),
			children: () => /* @__PURE__ */ m(Sl, { capability: e.capabilities.credit_cap })
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-concurrency",
			title: n("settings.concurrency.title"),
			description: n("settings.concurrency.desc"),
			children: (r) => /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrapchips",
				children: [/* @__PURE__ */ m(cl, {
					id: "aidlc-set-concurrency-input",
					label: n("settings.concurrency.label"),
					value: e.values.global_concurrency_cap,
					lo: al.lo,
					hi: al.hi,
					disabled: e.busy,
					describedBy: r,
					onCommit: (t) => e.save({ global_concurrency_cap: t })
				}), /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: q(t, "settings.concurrency.value", e.values.global_concurrency_cap)
				})]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-doctor",
			title: n("settings.doctor.title"),
			description: n("settings.doctor.desc"),
			children: (t) => /* @__PURE__ */ h("label", {
				className: "studio-check",
				children: [/* @__PURE__ */ m("input", {
					type: "checkbox",
					checked: e.values.installer.run_doctor_after_install,
					disabled: e.busy,
					"aria-describedby": t,
					onChange: (t) => e.save({ installer: { run_doctor_after_install: t.target.checked } })
				}), /* @__PURE__ */ h("span", { children: [
					/* @__PURE__ */ m(Y, {
						name: "check",
						size: 13
					}),
					" ",
					n("settings.doctor.label")
				] })]
			})
		})
	] });
}
//#endregion
//#region src/settings/DiagnosticsSetting.tsx
var dl = {
	lo: 1,
	hi: 30
};
function fl({ id: e, label: t, value: n, disabled: r, describedBy: i, onCommit: a }) {
	let [s, c] = d(String(n));
	o(() => c(String(n)), [n]);
	let l = () => {
		let e = Number(s);
		if (!Number.isInteger(e) || e < dl.lo || e > dl.hi) {
			c(String(n));
			return;
		}
		e !== n && a(e);
	};
	return /* @__PURE__ */ h("label", {
		className: "studio-sfield",
		htmlFor: e,
		children: [/* @__PURE__ */ m("span", {
			className: "studio-sr",
			children: t
		}), /* @__PURE__ */ m("input", {
			id: e,
			type: "number",
			className: "studio-mono studio-num",
			min: dl.lo,
			max: dl.hi,
			step: 1,
			value: s,
			disabled: r,
			"aria-describedby": i,
			onChange: (e) => c(e.target.value),
			onBlur: l,
			onKeyDown: (e) => {
				e.key === "Enter" && l();
			}
		})]
	});
}
function pl({ control: e }) {
	let t = H(), { t: n } = t, r = e.values.diagnostics.export_include_human_text;
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-retention",
			title: n("settings.diagnostics.retention.title"),
			description: n("settings.diagnostics.retention.desc"),
			children: (r) => /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrapchips",
				children: [/* @__PURE__ */ m(fl, {
					id: "aidlc-set-retention-input",
					label: n("settings.diagnostics.retention.label"),
					value: e.values.diagnostics.retention_days,
					disabled: e.busy,
					describedBy: r,
					onCommit: (t) => e.save({ diagnostics: { retention_days: t } })
				}), /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: q(t, "settings.diagnostics.retention.value", e.values.diagnostics.retention_days)
				})]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-humantext",
			title: n("settings.diagnostics.humanText.title"),
			description: n("settings.diagnostics.humanText.desc"),
			children: (t) => /* @__PURE__ */ h("label", {
				className: "studio-check",
				children: [/* @__PURE__ */ m("input", {
					type: "checkbox",
					checked: r,
					disabled: e.busy,
					"aria-describedby": t,
					onChange: (t) => e.save({ diagnostics: { export_include_human_text: t.target.checked } })
				}), /* @__PURE__ */ h("span", { children: [
					/* @__PURE__ */ m(Y, {
						name: "lock",
						size: 13
					}),
					" ",
					n("settings.diagnostics.humanText.label")
				] })]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-humanretention",
			title: n("settings.diagnostics.humanRetention.title"),
			description: n("settings.diagnostics.humanRetention.desc"),
			children: (r) => /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrapchips",
				children: [/* @__PURE__ */ m(fl, {
					id: "aidlc-set-humanretention-input",
					label: n("settings.diagnostics.humanRetention.label"),
					value: e.values.human_text_retention_days,
					disabled: e.busy,
					describedBy: r,
					onCommit: (t) => e.save({ human_text_retention_days: t })
				}), /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: q(t, "settings.diagnostics.retention.value", e.values.human_text_retention_days)
				})]
			})
		}),
		/* @__PURE__ */ m(pa, { allowHumanText: r })
	] });
}
//#endregion
//#region src/settings/LocaleSetting.tsx
var ml = [
	"auto",
	"en-US",
	"zh-CN"
], hl = ["compact", "comfortable"];
function gl({ control: e }) {
	let { t } = H(), n = te(), r = e.values.locale, i = (t) => {
		ne(t === "auto" ? null : t), e.save({ locale: t });
	};
	return /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ m(bl, {
		id: "aidlc-set-locale",
		title: t("settings.locale.title"),
		description: t("settings.locale.desc"),
		children: (a) => /* @__PURE__ */ h("div", {
			className: "studio-col",
			children: [/* @__PURE__ */ h("label", {
				className: "studio-sfield",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-sr",
					children: t("settings.locale.label")
				}), /* @__PURE__ */ m("select", {
					value: r,
					disabled: e.busy,
					"aria-describedby": a,
					onChange: (e) => i(e.target.value),
					children: ml.map((e) => /* @__PURE__ */ m("option", {
						value: e,
						children: t(e === "auto" ? "settings.locale.auto" : `settings.locale.${e}`)
					}, e))
				})]
			}), /* @__PURE__ */ m("span", {
				className: "studio-muted studio-small",
				children: r === "auto" ? t("settings.locale.following", { locale: t(`settings.locale.${n}`) }) : t("settings.locale.overridden")
			})]
		})
	}), /* @__PURE__ */ m(bl, {
		id: "aidlc-set-density",
		title: t("settings.density.title"),
		description: t("settings.density.desc"),
		children: (n) => /* @__PURE__ */ h("label", {
			className: "studio-sfield",
			children: [/* @__PURE__ */ m("span", {
				className: "studio-sr",
				children: t("settings.density.label")
			}), /* @__PURE__ */ m("select", {
				value: e.values.density,
				disabled: e.busy,
				"aria-describedby": n,
				onChange: (t) => e.save({ density: t.target.value }),
				children: hl.map((e) => /* @__PURE__ */ m("option", {
					value: e,
					children: t(`settings.density.${e}`)
				}, e))
			})]
		})
	})] });
}
//#endregion
//#region src/settings/QueueSetting.tsx
var _l = [
	"priority",
	"repo",
	"type",
	"oldest"
];
function vl({ control: e }) {
	let { t } = H(), n = (t) => {
		try {
			localStorage.setItem(F, t);
		} catch {}
		e.save({ queue_organize: t });
	};
	return /* @__PURE__ */ m(bl, {
		id: "aidlc-set-organize",
		title: t("settings.queue.title"),
		description: t("settings.queue.desc"),
		children: (r) => /* @__PURE__ */ h("div", {
			className: "studio-col",
			children: [/* @__PURE__ */ h("label", {
				className: "studio-sfield",
				children: [/* @__PURE__ */ m("span", {
					className: "studio-sr",
					children: t("settings.queue.label")
				}), /* @__PURE__ */ m("select", {
					value: e.values.queue_organize,
					disabled: e.busy,
					"aria-describedby": r,
					onChange: (e) => n(e.target.value),
					children: _l.map((e) => /* @__PURE__ */ m("option", {
						value: e,
						children: t(`settings.queue.${e}`)
					}, e))
				})]
			}), /* @__PURE__ */ m("span", {
				className: "studio-muted studio-small",
				children: t("settings.queue.localNote")
			})]
		})
	});
}
//#endregion
//#region src/settings/SlackSetting.tsx
function yl({ control: e, repos: t }) {
	let n = H(), { t: r } = n, i = e.capabilities.slack, a = i?.available !== !1, o = e.values.slack.muted_repo_ids, s = new Set(t.map((e) => e.repo_id)), c = o.filter((e) => !s.has(e)), l = (t, n) => {
		let r = n ? [...o, t] : o.filter((e) => e !== t);
		e.save({ slack: { muted_repo_ids: [...new Set(r)] } });
	};
	return /* @__PURE__ */ h(p, { children: [
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-slack",
			title: r("settings.slack.title"),
			description: r("settings.slack.desc"),
			children: (t) => /* @__PURE__ */ h("div", {
				className: "studio-row studio-wrapchips",
				children: [a ? null : /* @__PURE__ */ m(Sl, { capability: i }), /* @__PURE__ */ h("label", {
					className: "studio-check",
					children: [/* @__PURE__ */ m("input", {
						type: "checkbox",
						checked: e.values.slack.enabled,
						disabled: e.busy || !a,
						"aria-describedby": t,
						onChange: (t) => e.save({ slack: { enabled: t.target.checked } })
					}), /* @__PURE__ */ h("span", { children: [
						/* @__PURE__ */ m(Y, {
							name: "slack",
							size: 13
						}),
						" ",
						r("settings.slack.label")
					] })]
				})]
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-slack-quick",
			title: r("settings.slack.quickActions.title"),
			description: r("settings.slack.quickActions.desc"),
			children: () => /* @__PURE__ */ m(Sl, { capability: e.capabilities.slack_quick_actions })
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-slack-mute",
			title: r("settings.slack.mute.title"),
			description: r("settings.slack.mute.desc"),
			children: (i) => /* @__PURE__ */ m("div", {
				className: "studio-col",
				"aria-describedby": i,
				children: t.length === 0 && c.length === 0 ? /* @__PURE__ */ m("span", {
					className: "studio-muted",
					children: r("settings.slack.mute.none")
				}) : /* @__PURE__ */ h(p, { children: [/* @__PURE__ */ h("ul", {
					className: "studio-mutelist",
					children: [t.map((t) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("label", {
						className: "studio-check",
						children: [
							/* @__PURE__ */ m("input", {
								type: "checkbox",
								checked: o.includes(t.repo_id),
								disabled: e.busy || !a,
								onChange: (e) => l(t.repo_id, e.target.checked),
								"aria-label": r("settings.slack.mute.label", { repo: t.label })
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-strong",
								children: t.label
							}),
							/* @__PURE__ */ m("span", {
								className: "studio-mono studio-muted studio-small studio-trunc",
								children: t.canonical_path
							})
						]
					}) }, t.repo_id)), c.map((t) => /* @__PURE__ */ m("li", { children: /* @__PURE__ */ h("label", {
						className: "studio-check",
						children: [/* @__PURE__ */ m("input", {
							type: "checkbox",
							checked: !0,
							disabled: e.busy,
							onChange: () => l(t, !1),
							"aria-label": r("settings.slack.mute.label", { repo: t })
						}), /* @__PURE__ */ m("span", {
							className: "studio-mono",
							children: r("settings.slack.mute.orphan", { id: t })
						})]
					}) }, t))]
				}), /* @__PURE__ */ m("span", {
					className: "studio-muted studio-small",
					children: q(n, "settings.slack.mute.count", o.length)
				})] })
			})
		}),
		/* @__PURE__ */ m(bl, {
			id: "aidlc-set-dashboard",
			title: r("settings.dashboard.title"),
			description: r("settings.dashboard.desc"),
			children: (t) => /* @__PURE__ */ h("label", {
				className: "studio-check",
				children: [/* @__PURE__ */ m("input", {
					type: "checkbox",
					checked: e.values.notifications.dashboard,
					disabled: e.busy,
					"aria-describedby": t,
					onChange: (t) => e.save({ notifications: { dashboard: t.target.checked } })
				}), /* @__PURE__ */ m("span", { children: r("settings.dashboard.label") })]
			})
		})
	] });
}
//#endregion
//#region src/settings/SettingsView.tsx
function bl({ id: e, title: t, description: n, children: r }) {
	let i = `${e}-desc`;
	return /* @__PURE__ */ h("div", {
		className: "studio-mrow",
		children: [/* @__PURE__ */ h("div", {
			className: "studio-grow",
			children: [/* @__PURE__ */ m("div", {
				className: "studio-mrow-title",
				id: `${e}-title`,
				children: t
			}), /* @__PURE__ */ m("p", {
				className: "studio-mrow-desc",
				id: i,
				children: n
			})]
		}), /* @__PURE__ */ m("div", {
			className: "studio-mrow-ctl",
			children: r(i)
		})]
	});
}
function xl({ title: e, children: t }) {
	return /* @__PURE__ */ h("section", {
		className: "studio-block",
		children: [/* @__PURE__ */ m("h3", { children: e }), /* @__PURE__ */ m("div", {
			className: "studio-matrix",
			children: t
		})]
	});
}
function Sl({ capability: e }) {
	let { t, has: n } = H(), r = e?.reason ?? null, i = r && n(`settings.reason.${r}`) ? t(`settings.reason.${r}`) : null;
	return /* @__PURE__ */ m(X, {
		tone: "warn",
		icon: "warn",
		children: i ? t("settings.unavailableWhy", { reason: i }) : r ? t("settings.unavailableWhy", { reason: t("settings.reason.other", { raw: r }) }) : t("settings.unavailable")
	});
}
function Cl(e, t) {
	return e ? t ? (e.updated_at ?? "") >= (t.updated_at ?? "") ? e : t : e : t;
}
function wl({ go: e }) {
	let t = H(), { t: n } = t, r = xe(), a = J("settings", i((e) => r.settings({ signal: e }), [r]), {
		interval: 0,
		revalidateOn: ["settings.updated", "reset"]
	}), o = J("health", i((e) => r.health({ signal: e }), [r]), { interval: 6e4 }), s = J("repos", i((e) => r.repos(!1, { signal: e }), [r]), { interval: 6e4 }), c = a.refresh, [u, f] = d(null), [g, _] = d(!1), [v, y] = d(null), b = Cl(u, a.data), x = i((e) => {
		_(!0), y(n("settings.page.saving")), r.putSettings(e).then((e) => {
			f(e), y(n("settings.page.saved"));
		}).catch((e) => {
			let r = W(e), i = t.has(`errors.${r.code}`) ? n(`errors.${r.code}`) : r.message, a = typeof r.details.key == "string" ? r.details.key : null;
			y(a ? n("settings.page.saveFailedKey", {
				key: a,
				message: i
			}) : n("settings.page.saveFailed", { message: i })), c();
		}).finally(() => _(!1));
	}, [
		r,
		t,
		n,
		c
	]), S = l(() => b ? {
		values: b.settings,
		capabilities: b.capabilities,
		busy: g,
		save: x
	} : null, [
		b,
		g,
		x
	]);
	return /* @__PURE__ */ m("div", {
		className: "studio-scroll",
		children: /* @__PURE__ */ h("div", {
			className: "studio-page studio-settings",
			children: [
				/* @__PURE__ */ h("h1", { children: [
					/* @__PURE__ */ m(Y, {
						name: "settings",
						size: 18
					}),
					" ",
					n("settings.page.title")
				] }),
				/* @__PURE__ */ m("p", {
					className: "studio-lede",
					children: n("settings.page.lede")
				}),
				/* @__PURE__ */ m("p", {
					className: "studio-settings-status",
					role: "status",
					"aria-live": "polite",
					"aria-label": n("settings.page.status"),
					children: v ?? ""
				}),
				/* @__PURE__ */ m(el, { onApplied: () => void s.refresh() }),
				a.error && !b ? /* @__PURE__ */ h("div", {
					className: "studio-banner",
					"data-tone": "warn",
					role: "status",
					children: [
						/* @__PURE__ */ m(Y, {
							name: "warn",
							size: 15
						}),
						/* @__PURE__ */ m("span", {
							className: "studio-grow",
							children: n("settings.page.error", { message: t.has(`errors.${a.error.code}`) ? n(`errors.${a.error.code}`) : a.error.message })
						}),
						/* @__PURE__ */ h("button", {
							type: "button",
							className: "studio-btn",
							onClick: () => void a.refresh(),
							children: [/* @__PURE__ */ m(Y, {
								name: "refresh",
								size: 13
							}), n("settings.page.retry")]
						})
					]
				}) : null,
				S ? /* @__PURE__ */ h(p, { children: [
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.locale"),
						children: /* @__PURE__ */ m(gl, { control: S })
					}),
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.queue"),
						children: /* @__PURE__ */ m(vl, { control: S })
					}),
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.automation"),
						children: /* @__PURE__ */ m(ul, { control: S })
					}),
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.advisor"),
						children: /* @__PURE__ */ m(il, {
							control: S,
							repos: s.data?.repos ?? null
						})
					}),
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.notifications"),
						children: /* @__PURE__ */ m(yl, {
							control: S,
							repos: s.data?.repos ?? []
						})
					}),
					/* @__PURE__ */ m(xl, {
						title: n("settings.section.diagnostics"),
						children: /* @__PURE__ */ m(pl, { control: S })
					})
				] }) : /* @__PURE__ */ m("p", {
					className: "studio-muted",
					children: n("settings.page.reading")
				}),
				/* @__PURE__ */ h(xl, {
					title: n("settings.section.about"),
					children: [/* @__PURE__ */ m(rl, {
						api: r,
						tool: o.data?.tools?.bun ?? null,
						onChanged: () => void o.refresh()
					}), /* @__PURE__ */ m(tl, {
						health: o.data,
						versions: b?.versions ?? null,
						onOpenActivity: () => e({ view: "activity" })
					})]
				})
			]
		})
	});
}
//#endregion
//#region src/views/settings/index.tsx
var Tl = /* @__PURE__ */ O({ default: () => wl }), El = /* @__PURE__ */ new Map();
function Dl() {
	let e = /* @__PURE__ */ new Map(), t = /* #__PURE__ */ Object.assign({
		"../views/actions/index.tsx": Ji,
		"../views/activity/index.tsx": ba,
		"../views/intents/index.tsx": co,
		"../views/map/index.tsx": Ko,
		"../views/new-intent/index.tsx": Ms,
		"../views/repos/index.tsx": Yc,
		"../views/settings/index.tsx": Tl
	});
	for (let [n, r] of Object.entries(t)) {
		let t = /\/views\/([^/]+)\/index\.tsx$/.exec(n)?.[1];
		if (!t || !st.includes(t)) continue;
		let i = r.default;
		typeof i == "function" && e.set(t, i);
	}
	for (let [t, n] of El) e.set(t, n);
	return e;
}
function Ol(e) {
	return e === "new-intent" ? "nav.newIntent" : `nav.${e}`;
}
function kl({ route: e, go: t }) {
	let { t: n } = H(), r = l(() => Dl(), [e.view]).get(e.view);
	if (r) return /* @__PURE__ */ m(r, {
		route: e,
		go: t
	});
	let i = n(Ol(e.view));
	return /* @__PURE__ */ h("div", {
		className: "studio-page studio-placeholder",
		children: [/* @__PURE__ */ h("h1", { children: [
			/* @__PURE__ */ m(Y, {
				name: "clock",
				size: 18
			}),
			" ",
			n("shell.notBuilt.title", { view: i })
		] }), /* @__PURE__ */ m("p", {
			className: "studio-muted",
			children: n("shell.notBuilt.body")
		})]
	});
}
//#endregion
//#region src/shell/StudioApp.tsx
var Al = t(null);
function jl() {
	let e = a(Al);
	if (!e) throw Error("useShellData() must be called inside StudioApp");
	return e;
}
function Ml() {
	let e = xe(), t = i((t) => e.pollEvents(t), [e]);
	return /* @__PURE__ */ m(Be, {
		poll: t,
		children: /* @__PURE__ */ m(Nl, { api: e })
	});
}
function Nl({ api: e }) {
	let t = H(), { t: n } = t, r = ee(), [a, s] = Dt(), c = He(), u = v(), [f, p] = d(null), [g, _] = d(!1), y = J(`actions:${a.repo}:${a.intent}`, i((t) => e.actions({
		repo: a.repo || void 0,
		intent: a.intent || void 0
	}, { signal: t }), [
		e,
		a.repo,
		a.intent
	])), b = J("repos", i((t) => e.repos(!1, { signal: t }), [e]), { interval: 6e4 }), x = J("leases", i((t) => e.leases({ signal: t }), [e])), S = J("settings", i((t) => e.settings({ signal: t }), [e]), { interval: 0 }), C = J("health", i((t) => e.health({ signal: t }), [e]), { interval: 6e4 });
	o(() => {
		let e = () => _(!0);
		return window.addEventListener(he, e), () => window.removeEventListener(he, e);
	}, []);
	let w = g || [
		y.error,
		b.error,
		x.error,
		S.error,
		C.error
	].some((e) => e?.authRequired), T = l(() => y.data ? ot(y.data) : null, [y.data]), E = T?.counts.total ?? null, D = f ?? E;
	o(() => {
		u(D ?? 0);
	}, [D, u]);
	let O = l(() => y.data?.actions.filter((e) => e.failure?.breaker_open).length ?? null, [y.data]), k = S.data ? {
		enabled: S.data.settings.night_window.enabled,
		start: S.data.settings.night_window.start_local,
		end: S.data.settings.night_window.end_local
	} : null, A = l(() => ({
		api: e,
		route: a,
		go: s,
		actions: y,
		repos: b,
		leases: x,
		settings: S,
		health: C,
		setQueueCount: p
	}), [
		e,
		a,
		s,
		y,
		b,
		x,
		S,
		C
	]), j = bt(a), M = n(Ol(a.view)), N = C.data && C.data.status !== "healthy" ? C.data.issues.length : 0;
	return /* @__PURE__ */ h("div", {
		className: "studio",
		"data-mode": r,
		"data-pane": j,
		"data-view": a.view,
		children: [
			/* @__PURE__ */ m("a", {
				className: "studio-skip",
				href: "#studio-view",
				children: n("a11y.skipToDetail")
			}),
			/* @__PURE__ */ m(It, {
				route: a,
				go: s,
				queueCount: D,
				showBack: j === "detail" && a.view === "actions",
				onBack: () => s({
					action: "",
					artifact: ""
				})
			}),
			/* @__PURE__ */ m(kt, {
				route: a,
				go: s,
				repos: b.data?.repos ?? [],
				registered: b.data?.totals.repos ?? null,
				unavailable: b.data?.totals.unavailable ?? null,
				children: /* @__PURE__ */ m(Pt, {
					running: x.data?.live_execution ?? null,
					leases: x.data?.leases.length ?? null,
					circuits: O,
					critical: T?.counts.critical ?? null,
					nightWindow: k,
					streamMode: c.mode
				})
			}),
			w ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "danger",
				role: "alert",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 15
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-grow",
						children: n("shell.banner.sessionExpired")
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => window.location.reload(),
						children: n("shell.banner.reload")
					})
				]
			}) : null,
			N > 0 ? /* @__PURE__ */ h("div", {
				className: "studio-banner",
				"data-tone": "warn",
				role: "status",
				children: [
					/* @__PURE__ */ m(Y, {
						name: "warn",
						size: 15
					}),
					/* @__PURE__ */ m("span", {
						className: "studio-grow",
						children: q(t, "shell.banner.degraded", N)
					}),
					/* @__PURE__ */ m("button", {
						type: "button",
						className: "studio-btn",
						onClick: () => s({ view: "settings" }),
						children: n("shell.banner.openSettings")
					})
				]
			}) : null,
			/* @__PURE__ */ m("main", {
				className: "studio-view",
				id: "studio-view",
				children: /* @__PURE__ */ h(Al.Provider, {
					value: A,
					children: [/* @__PURE__ */ m(Ot, {
						where: M,
						resetKey: a.view,
						children: /* @__PURE__ */ m(kl, {
							route: a,
							go: s
						})
					}), a.draft ? /* @__PURE__ */ h("div", {
						className: "studio-drawer-wrap",
						children: [/* @__PURE__ */ m("div", {
							className: "studio-drawer-scrim",
							onClick: () => s({ draft: "" }),
							"aria-hidden": "true"
						}), /* @__PURE__ */ m(Ot, {
							where: n("advisor.drawer.title"),
							resetKey: a.draft,
							children: /* @__PURE__ */ m(tt, {
								draftId: a.draft,
								onClose: () => s({ draft: "" })
							})
						})]
					}) : null]
				})
			})
		]
	});
}
//#endregion
//#region src/main.tsx
ae();
function Pl() {
	return /* @__PURE__ */ m(de, { children: /* @__PURE__ */ m(Ml, {}) });
}
//#endregion
export { Pl as default };
