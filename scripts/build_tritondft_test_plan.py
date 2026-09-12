from pathlib import Path
from datetime import date

from openpyxl import Workbook, load_workbook
from openpyxl.chart import DoughnutChart, BarChart, Reference
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo


OUT = Path("outputs/01a0682d-b982-7de3-a7ee-157db8af6c7e/TritonDFT_Industry_Test_Plan.xlsx")
NAVY = "17365D"
BLUE = "2563EB"
TEAL = "0F766E"
LIGHT_BLUE = "E8F0FE"
LIGHT_TEAL = "E6F4F1"
LIGHT_GRAY = "F3F4F6"
MID_GRAY = "D1D5DB"
DARK = "1F2937"
WHITE = "FFFFFF"
GREEN = "DCFCE7"
RED = "FEE2E2"
AMBER = "FEF3C7"
PURPLE = "EDE9FE"
thin = Side(style="thin", color="D8DEE8")


def title(ws, text, subtitle, end_col):
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_col)
    c = ws.cell(1, 1, text)
    c.fill = PatternFill("solid", fgColor=NAVY)
    c.font = Font(color=WHITE, bold=True, size=18)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 34
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=end_col)
    c = ws.cell(2, 1, subtitle)
    c.fill = PatternFill("solid", fgColor="DCE6F1")
    c.font = Font(color=DARK, italic=True, size=10)
    c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 30


def header_style(row):
    for c in row:
        c.fill = PatternFill("solid", fgColor=BLUE)
        c.font = Font(color=WHITE, bold=True, size=10)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(left=thin, right=thin, top=thin, bottom=thin)


def section(ws, row, text, end_col):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_col)
    c = ws.cell(row, 1, text)
    c.fill = PatternFill("solid", fgColor=TEAL)
    c.font = Font(color=WHITE, bold=True, size=12)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 24


def add_table(ws, ref, name):
    tab = Table(displayName=name, ref=ref)
    tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True, showFirstColumn=False)
    ws.add_table(tab)


def set_widths(ws, widths):
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


wb = Workbook()
wb.remove(wb.active)
readme = wb.create_sheet("Read Me")
dash = wb.create_sheet("Dashboard")
tests = wb.create_sheet("Test Cases")
exec_log = wb.create_sheet("Execution Log")
defects = wb.create_sheet("Defect Log")
trace = wb.create_sheet("Requirements Traceability")
signoff = wb.create_sheet("Release Sign-off")
lists = wb.create_sheet("Lists")

# Lists and validation vocabularies
list_data = {
    "A": ["Status", "Not Run", "In Progress", "Pass", "Fail", "Blocked", "Not Applicable"],
    "B": ["Priority", "P0 - Critical", "P1 - High", "P2 - Medium", "P3 - Low"],
    "C": ["Type", "Functional", "Integration", "System", "Regression", "Security", "Performance", "Usability", "Recovery", "Compatibility", "Scientific Validation"],
    "D": ["Automation", "Manual", "Automated", "Candidate", "Hybrid"],
    "E": ["Severity", "S1 - Critical", "S2 - Major", "S3 - Moderate", "S4 - Minor"],
    "F": ["Defect Status", "New", "Triaged", "In Progress", "Ready for Retest", "Closed", "Deferred", "Rejected"],
    "G": ["Environment", "Local macOS", "Local Linux", "Cluster - Slurm", "API/Staging", "Production-like", "CI"],
    "H": ["Decision", "Approved", "Approved with Exceptions", "Rejected", "Pending"],
}
for col, values in list_data.items():
    for r, value in enumerate(values, 1):
        lists[f"{col}{r}"] = value
lists.sheet_state = "hidden"

# Read Me
title(readme, "TritonDFT Software Test Plan", "Execution-ready QA workbook | Template plus worked examples | Prepared from repository capabilities", 8)
section(readme, 4, "Purpose and scope", 8)
readme["A5"] = "Purpose"
readme["B5"] = "Provide a repeatable, auditable industry-style plan for verifying TritonDFT as a software platform and scientific workflow orchestrator."
readme["A6"] = "In scope"
readme["B6"] = "Installation, CLI, API/auth/jobs/admin, planning and approval, QE input validation, Materials Project lookup, local and SSH/Slurm execution, DAG/recovery, result evidence, plots, security, performance and compatibility."
readme["A7"] = "Out of scope"
readme["B7"] = "Independent certification of Quantum ESPRESSO itself, pseudopotential scientific quality, cluster scheduler correctness, and production penetration testing unless separately commissioned."
section(readme, 9, "How a tester uses this workbook", 8)
steps = [
    ("1", "Confirm release scope", "Enter release/build, commit SHA, environment and owner on Release Sign-off."),
    ("2", "Select assigned cases", "Filter Test Cases by owner, priority, component, type or environment."),
    ("3", "Prepare safely", "Use test credentials, non-production data and a disposable run directory. Never paste API keys into this workbook."),
    ("4", "Execute exactly", "Follow preconditions and numbered steps. Preserve generated inputs, logs, screenshots, job IDs and output paths."),
    ("5", "Judge objectively", "Compare observed behavior with every acceptance criterion. Pass only when all criteria are satisfied."),
    ("6", "Record one run", "Add a row to Execution Log for every execution. Set Pass/Fail; do not put a check mark in both."),
    ("7", "Raise defects", "For failures, create a Defect Log entry, link its ID in the execution record, and attach reproducible evidence."),
    ("8", "Retest and regress", "Retest the fixed build, then run related P0/P1 and regression cases before sign-off."),
]
readme["A10"], readme["B10"], readme["C10"] = "Step", "Activity", "Tester instruction"
header_style(readme[10])
for row_num, row in enumerate(steps, 11):
    for col_num, value in enumerate(row, 1):
        readme.cell(row_num, col_num, value)
add_table(readme, "A10:C18", "TesterInstructions")
section(readme, 20, "Status rules and evidence standard", 8)
rules = [
    ("Pass", "All acceptance criteria met; expected and actual results agree; evidence link recorded."),
    ("Fail", "At least one acceptance criterion is not met. A defect ID is required unless failure is already linked."),
    ("Blocked", "Execution cannot continue due to a documented dependency or environment issue; blocker and owner recorded."),
    ("Not Run", "No execution attempt has begun."),
    ("Not Applicable", "Approved exclusion with a reason; do not use to hide unavailable coverage."),
    ("Evidence", "Use repository-relative paths or approved links. Include command, timestamp, build SHA, relevant log excerpt and artifact checksum for scientific outputs."),
]
readme["A21"], readme["B21"] = "Term", "Definition"
header_style(readme[21])
for row in rules:
    readme.append(row)
add_table(readme, "A21:B27", "StatusRules")
section(readme, 29, "Release entry and exit criteria", 8)
criteria = [
    ("Entry", "Version and commit SHA frozen; environment available; test data approved; required keys stored outside workbook; P0/P1 cases assigned."),
    ("Exit", "100% P0 executed and passed; 100% P1 executed with no open S1/S2 defects; overall pass rate ≥95%; blocked cases have accepted disposition; sign-off completed."),
    ("Stop-test", "Stop affected scope for credential exposure, data-loss risk, unauthorized cluster submission, cross-user data access, corrupted workflow state, or scientifically invalid output presented as valid."),
]
readme["A30"], readme["B30"] = "Gate", "Criterion"
header_style(readme[30])
for row in criteria:
    readme.append(row)
add_table(readme, "A30:B33", "ReleaseCriteria")
set_widths(readme, {"A": 18, "B": 42, "C": 95, "D": 12, "E": 12, "F": 12, "G": 12, "H": 12})
for row in readme.iter_rows(min_row=5, max_row=33):
    for c in row:
        c.alignment = Alignment(vertical="top", wrap_text=True)
readme.freeze_panes = "A4"

# Test cases
title(tests, "Master Test Case Catalogue", "Filter by component, priority, owner, type or environment. Blue columns define the test; yellow columns are assignment fields.", 21)
headers = ["Test Case ID", "Requirement ID", "Component / Feature", "Test Scenario", "Test Type", "Priority", "Risk Covered", "Preconditions", "Test Data", "Test Steps", "Expected Result", "Passing Criteria", "Environment", "Automation", "Owner", "Target Cycle", "Status", "Pass", "Fail", "Comment / Notes", "Evidence Expected"]
tests.append([])
tests.append(headers)
header_style(tests[4])
cases = [
    ["TC-INST-001","REQ-INST-01","Installation","Install supported Python environment and dependencies","Compatibility","P0 - Critical","Platform cannot start","Fresh Linux account; Python 3.11 module available","Repository checkout on target branch","1. Load Python 3.11. 2. Create/activate .venv. 3. Install requirements. 4. Run pip check. 5. Import cluster_agent.","Dependencies install without source-build failure; imports succeed.","Python reports 3.11.x; pip path is inside .venv; pip check reports no broken requirements; import prints cluster_agent import OK.","Local Linux","Candidate","Example: QA Engineer","Smoke","Pass","Yes","No","Example completed: dependency checkpoint passed on clean test VM.","Terminal transcript; Python/pip versions; commit SHA"],
    ["TC-CLI-001","REQ-CLI-01","Cluster CLI","Launcher help smoke test","Functional","P0 - Critical","Users cannot launch product","Dependencies installed; tracked launcher executable","./tritondft-cluster --help","1. Run launcher with --help. 2. Capture exit code and output.","Help text is printed without importing or configuration errors.","Exit code 0; usage and supported options visible; no traceback.","Local Linux","Automated","Example: QA Engineer","Smoke","Pass","Yes","No","Example completed.","Command, exit code, full stdout/stderr"],
    ["TC-CFG-001","REQ-CFG-01","First-run setup","Missing user configuration starts guided setup","System","P1 - High","User blocked or secrets mishandled","Disposable test HOME; no .env.cluster; SSH config backed up","Test SSH alias, host, user, remote work directory","1. Start cluster launcher. 2. Complete prompts with test values. 3. Inspect created files and permissions. 4. Restart.","Guided setup creates/reuses user configuration and does not commit secrets.",".env.cluster and user Slurm template created in private directory; existing SSH config preserved; restart reuses valid settings; secrets are not logged.","Cluster - Slurm","Manual","Unassigned","Regression","Not Run","","","Use a disposable account; redact secrets.","Before/after file listing, permissions, redacted config, console log"],
    ["TC-PLAN-001","REQ-PLAN-01","Workflow planning","Plan correct Si vc-relax → scf → nscf workflow","Scientific Validation","P0 - Critical","Incorrect scientific workflow","Valid OpenAI and Materials Project test keys; cluster or script-only mode","README Si diamond prompt","1. Submit the Si workflow request. 2. Review plan, dependency graph and generated inputs. 3. Do not approve initially.","Minimal ordered workflow is generated with consistent settings and explanations.","Exactly required scientific steps are present; dependencies order vc-relax before scf before nscf; each step has a rationale; XC/pseudopotential/cutoff/structure settings remain consistent.","Cluster - Slurm","Hybrid","DFT Domain Tester","Release","Fail","No","Yes","Example failure: generated SCF changed XC from LDA to PBE. Logged as DEF-001.","Plan JSON/text, dependency graph, all generated input files, DEF-001"],
    ["TC-APR-001","REQ-APR-01","Approval gate","No remote activity before approval","Security","P0 - Critical","Unauthorized compute use/cost","Instrumented or mock SSH/sbatch; assistant mode","One two-step DFT request","1. Generate plan and inputs. 2. Wait on approval screen. 3. Inspect connection/submission calls. 4. Cancel.","No SSH connection, upload or job submission occurs before explicit approval.","Connection, rsync and sbatch call counts remain zero until Approve; Cancel leaves them zero.","CI","Automated","Platform QA","Regression","Pass","Yes","No","Example completed with mocked transport.","Automated test report and call-count assertions"],
    ["TC-APR-002","REQ-APR-02","Approval gate","Revision request regenerates proposed input without running it","Functional","P1 - High","Unreviewed settings execute","Assistant mode; plan awaiting approval","Suggestion: increase ecutwfc to 80 Ry","1. Open generated input. 2. Submit revision suggestion. 3. Review regenerated input. 4. Confirm no submission. 5. Approve revised version.","Requested revision is applied and remains behind approval gate.","Revised input contains 80 Ry; unchanged fields are preserved; no job is submitted before final approval; approved copy is retained.","API/Staging","Candidate","Unassigned","Release","Not Run","","","","Before/after inputs, API responses, job/transport log"],
    ["TC-QE-001","REQ-QE-01","QE syntax validation","Reject malformed namelist/markup","Negative / Security","P0 - Critical","Invalid input reaches expensive compute","Validator available","Inputs containing &amp;control, XML CDATA and Markdown fences","1. Validate each malformed input. 2. Attempt approval/submission.","Malformed syntax is blocked or safely normalized where explicitly supported.","HTML escaped namelist is rejected/decoded per safe path; CDATA/fences never reach QE; actionable issue identifies file and cause; submission does not occur for unresolved errors.","CI","Automated","Validation QA","Regression","Not Run","","","","Validator output and assertion log"],
    ["TC-QE-002","REQ-QE-02","QE scientific validation","Reject zero cell and coincident atoms","Scientific Validation","P0 - Critical","Physically invalid calculation","Workflow validator available","CELL_PARAMETERS all zero; duplicate ATOMIC_POSITIONS","1. Validate zero-cell input. 2. Validate coincident-atom input. 3. Attempt approval.","Both invalid structures are blocked.","Each case produces an error-severity validation issue; approval/execution is prevented; message identifies structural defect.","CI","Automated","DFT Domain Tester","Regression","Not Run","","","","Validation report and test output"],
    ["TC-QE-003","REQ-QE-03","QE consistency","LDA workflow rejects PBE pseudopotential","Scientific Validation","P0 - Critical","Scientifically inconsistent results","LDA request; PBE-labelled UPF fixture","LDA input plus PBE UPF header","1. Generate or load mismatched case. 2. Run cross-step/pseudopotential validation. 3. Try approval.","Functional/pseudopotential mismatch is detected and blocked.","Error explicitly states LDA/PBE incompatibility; no submission; correct LDA pseudopotential passes same check.","CI","Automated","DFT Domain Tester","Regression","Not Run","","","","UPF header, validation evidence, negative and control results"],
    ["TC-QE-004","REQ-QE-04","Spin and SOC","SOC requires fully relativistic pseudopotentials and isolated branch state","Scientific Validation","P0 - Critical","Invalid SOC result or state contamination","Scalar baseline complete; SOC request available","Scalar and fully relativistic UPF fixtures","1. Attempt SOC with scalar UPF. 2. Replace with fully relativistic UPF. 3. Execute/inspect branch staging.","Scalar UPF is rejected; valid SOC branch uses compatible state only.","Scalar UPF blocks approval; fully relativistic case passes; scalar .save state is not cloned into SOC SCF; SOC state is propagated only to dependent SOC steps.","Cluster - Slurm","Hybrid","DFT Domain Tester","Release","Not Run","","","","Input files, UPF metadata, branch directories, logs"],
    ["TC-MP-001","REQ-MP-01","Materials lookup","Resolve space-group alias and select correct structure","Integration","P1 - High","Wrong material structure","MP test key; network available","Material=Si; space group Fd-3m","1. Submit material description. 2. Capture lookup candidates. 3. Confirm filtering/ranking and selected structure.","Space-group alias resolves correctly and chosen structure matches request.","Fd-3m maps to expected group; incompatible candidates excluded; selected provenance stored; user-supplied structure, when supplied, overrides database data.","API/Staging","Hybrid","Integration QA","Release","Not Run","","","","Lookup request/response IDs, selected material ID, structure snapshot"],
    ["TC-SLRM-001","REQ-SLRM-01","Slurm generation","Preserve site srun template and synchronize resources","Integration","P0 - Critical","Invalid or unsafe job request","User Slurm template uses srun; allocation limits set","nodes=2, tasks/node=32; generated request exceeding maximum","1. Generate script. 2. Inspect launcher/header. 3. Compare ntasks and MPI flags. 4. Repeat with oversized model plan.","Site launcher is preserved and all resource fields agree within allocation.","srun remains srun; nodes/tasks-per-node/ntasks/MPI ranks agree; oversized plan is clamped; unique script name per step; input/output variables point to correct files.","Cluster - Slurm","Automated","HPC QA","Regression","Blocked","","","Example blocked: Slurm test partition unavailable; blocker ENV-CLUSTER-02 assigned to HPC admin.","Generated scripts, parsed resource summary, blocker ticket"],
    ["TC-SLRM-002","REQ-SLRM-02","Remote execution","Upload, submit, monitor and retrieve successful job","System","P0 - Critical","End-to-end platform failure","Approved test workflow; SSH alias; Slurm/QE modules; quota available","Small silicon SCF fixture","1. Approve. 2. Observe persistent SSH connection. 3. Verify upload. 4. Capture sbatch ID. 5. Monitor to completion. 6. Verify download and parsing.","One job is submitted and its outputs are retrieved and parsed.","Exactly one sbatch submission; state progresses queued→running→completed; output file is non-empty and contains JOB DONE; local run directory receives output; checkpoint records job ID and completion.","Cluster - Slurm","Manual","HPC QA","Release","Not Run","","","","SSH/rsync log, job ID, squeue history, output file/checksum, workflow_state.json"],
    ["TC-DAG-001","REQ-DAG-01","Workflow DAG","Independent post-SCF branches run only after SCF","System","P0 - Critical","Race/corrupted dependencies","Workflow with SCF then bands and DOS branches","Si bands + DOS request","1. Review graph. 2. Approve and observe submissions. 3. Compare parent completion and child submission times.","Bands and DOS depend on SCF and may run in parallel afterward.","Neither child submits before successful SCF; both declare SCF parent; after SCF they use isolated branch directories; failure of one child does not overwrite the other.","Cluster - Slurm","Hybrid","Workflow QA","Release","Not Run","","","","Graph, timestamps, job IDs, branch directory tree"],
    ["TC-REC-001","REQ-REC-01","Recovery and resume","Resume recorded submitted job without duplicate submission","Recovery","P0 - Critical","Duplicate compute spend","Workflow state contains submitted job ID; process restarted","Checkpoint fixture with queued/running job","1. Stop client after submission. 2. Restart and resume workflow. 3. Observe reconciliation and submission calls.","Existing job is reconciled rather than resubmitted.","Original job ID retained; no second sbatch call; state catches up to scheduler; output retrieved on completion; attempt history remains immutable.","Cluster - Slurm","Hybrid","Workflow QA","Regression","Not Run","","","","Before/after checkpoint, sbatch audit, scheduler history"],
    ["TC-REC-002","REQ-REC-02","Recovery and rerun","Targeted rerun invalidates descendants only","Recovery","P1 - High","Loss of valid work or stale descendants","Completed DAG with one failed descendant","Rerun SCF with edited convergence threshold","1. Select SCF rerun. 2. Edit and approve input. 3. Inspect state and attempt directories. 4. Resume.","SCF and dependent descendants rerun; unrelated completed work is preserved.","New immutable attempt created; input hash change detected; descendants reset; unrelated branch remains completed; old evidence not overwritten.","Cluster - Slurm","Candidate","Workflow QA","Release","Not Run","","","","State diffs, hashes, directory listing, new job IDs"],
    ["TC-PHON-001","REQ-PHON-01","Phonons and Raman","Stage phonon artifacts through ph.x → q2r.x → matdyn.x","Scientific Validation","P1 - High","Broken vibrational workflow","Approved phonon-dispersion request; remote tools available","Small stable material fixture","1. Review two required ph.x stages where Raman+dispersion requested. 2. Execute chain. 3. Verify artifact staging and frequency parsing.","Every consumer receives declared parent artifact and valid frequencies are reported.","Gamma Raman uses lraman; q-grid dynamical-matrix family reaches q2r; force constants reach matdyn; missing parent fails before submission; unphysical Gamma acoustic modes block chain.","Cluster - Slurm","Hybrid","DFT Domain Tester","Extended","Not Run","","","","Inputs, artifact inventory/checksums, frequency output, plot"],
    ["TC-RES-001","REQ-RES-01","Results evidence","Answer only from scoped, line-addressable completed evidence","Functional","P0 - Critical","Hallucinated or stale result","Completed and failed attempts with differing values","Question: What is the final total energy?","1. Ask result question. 2. Inspect retrieved evidence IDs/lines. 3. Repeat with incomplete attempt and unknown evidence ID.","Answer cites valid completed-attempt evidence and rejects unknown evidence.","Value matches output within displayed precision; citation includes known evidence ID and line range; failed/incomplete attempt is not preferred; arbitrary Python is not evaluated.","Local Linux","Automated","Results QA","Regression","Not Run","","","","Answer, evidence manifest, referenced output lines, automated assertions"],
    ["TC-PLOT-001","REQ-PLOT-01","Electronic plots","Render band structure with correct high-symmetry labels","Scientific Validation","P1 - High","Misleading visualization","Completed bands output and saved k-point labels","Known band-path fixture","1. Generate dashboard/plot. 2. Compare label count, coordinates and energies with source. 3. Test mismatched label fixture.","Plot uses paired saved labels/coordinates and rejects inconsistent metadata.","All ticks align with source coordinates; energies match parsed output; mismatched counts produce explicit error rather than a misleading plot.","Local Linux","Hybrid","Results QA","Release","Not Run","","","","Plot image, source files, numerical comparison"],
    ["TC-API-001","REQ-API-01","API health","Health endpoint responds","Functional","P0 - Critical","Service unavailable","API running","GET /healthz","1. Send request. 2. Record status, body and latency.","Health endpoint reports service available.","HTTP 200; JSON equals {status: ok}; response under 1 second in staging baseline.","API/Staging","Automated","API QA","Smoke","Not Run","","","","Request/response and timing"],
    ["TC-AUTH-001","REQ-AUTH-01","Authentication","Magic link single use and expiry","Security","P0 - Critical","Account takeover","Test mail sink; JWT secret set; test user","Valid, reused and expired tokens","1. Request link. 2. Verify within 15 min. 3. Reuse token. 4. Verify expired fixture. 5. Inspect cookie.","Only valid unused link authenticates; invalid states are rejected safely.","Valid token returns user JWT and secure HttpOnly SameSite=None cookie; reuse returns used error; expiry returns expired error; responses do not expose another account.","API/Staging","Automated","Security QA","Release","Not Run","","","","Sanitized responses, mail-sink record, cookie attributes"],
    ["TC-AUTH-002","REQ-AUTH-02","Authentication","Request-link rate limiting and non-enumeration","Security","P1 - High","Email flooding/user discovery","API with rate limiter; known and unknown test emails","6 rapid requests from one IP","1. Request links for known/unknown addresses. 2. Compare outward responses. 3. Exceed 5/minute.","Responses do not reveal account existence and abusive rate is limited.","Known/unknown outward message and status are indistinguishable; request beyond configured threshold receives rate-limit response; no secrets appear in logs.","API/Staging","Automated","Security QA","Release","Not Run","","","","Responses, rate-limit headers, sanitized server log"],
    ["TC-JOB-001","REQ-JOB-01","Job API","Regular user forced to script-only and active-job cap enforced","Security","P0 - Critical","Unauthorized CPU use/spend","Regular test user with credits; API/worker available","Four valid job requests","1. Submit job requesting CPU. 2. Inspect returned script_only. 3. Create three active jobs. 4. Submit fourth.","Policy overrides CPU request and caps active work.","Regular user receives script_only=true; first three accepted subject to credits; fourth gets too-many-active-jobs; no real QE execution begins.","API/Staging","Automated","API QA","Regression","Not Run","","","","API responses, DB job states, worker log"],
    ["TC-JOB-002","REQ-JOB-02","Job API","Cross-user job access is hidden","Security","P0 - Critical","Data leakage","Two authenticated non-admin users; job owned by user A","User A job UUID","1. As user B, GET/cancel/step-action against A job. 2. As A, GET own job.","User B cannot discover or mutate A job.","All B operations return not-found semantics without job details; A can access; database remains unchanged after B attempts.","API/Staging","Automated","Security QA","Regression","Not Run","","","","Sanitized responses and DB audit snapshot"],
    ["TC-JOB-003","REQ-JOB-03","Job lifecycle","Cancel queued/running/awaiting job and reconcile credits once","Functional","P1 - High","Stuck work or incorrect billing","Privileged user; jobs in each cancellable state","Queued, running, awaiting_plan, awaiting_approval fixtures","1. Cancel each state. 2. Poll. 3. Inspect finished timestamp and usage reconciliation. 4. Repeat cancel.","Every live state cancels and accounting is idempotent.","Status becomes cancelled; finished_at set; pre-charge reconciled exactly once; repeated cancel does not double-refund; no later execution occurs.","API/Staging","Automated","API QA","Regression","Not Run","","","","Responses, usage log before/after, worker log"],
    ["TC-ADM-001","REQ-ADM-01","Administration","Non-admin denied and admin changes audited","Security","P0 - Critical","Privilege escalation/untracked changes","Admin and regular test users","PATCH credits/admin/banned flags","1. Call admin routes as regular user. 2. Patch test user as admin. 3. Read audit log. 4. Verify target effects.","Only admins can administer; mutations have before/after audit evidence.","Regular user denied; admin patch persists only submitted fields; audit includes actor, target, action, before, after and timestamp; banning blocks authentication.","API/Staging","Automated","Security QA","Release","Not Run","","","","API responses, audit record, user record"],
    ["TC-ART-001","REQ-ART-01","Artifacts","Prevent path traversal and cross-run file access","Security","P0 - Critical","Arbitrary file disclosure","Completed job with run directory; artifact endpoint enabled","../etc/passwd, absolute path, symlink escape, valid artifact","1. Request each malicious path. 2. Request valid owned artifact. 3. Repeat as other user.","Only normalized files inside authorized run directory are served.","Traversal/absolute/symlink escape and cross-user request are rejected without content; valid owner file downloads with correct bytes/type.","API/Staging","Automated","Security QA","Regression","Not Run","","","","Requests/responses and file checksum"],
    ["TC-PERF-001","REQ-PERF-01","Performance","API responsiveness under expected polling load","Performance","P2 - Medium","Poor usability/outage","Staging resembles production; representative jobs","50 virtual users polling job status for 10 min","1. Warm service. 2. Run load profile. 3. Capture latency/error/resource metrics. 4. Check rate limits.","Status polling remains responsive without DB exhaustion.","p95 <1 s and p99 <2 s for GET job; error rate <1% excluding intentional rate limits; no connection-pool exhaustion; health remains 200.","Production-like","Candidate","Performance QA","Extended","Not Run","","","Baselines should be approved before release gate.","Load script/version, raw results, p50/p95/p99, errors, CPU/memory/DB metrics"],
    ["TC-SEC-001","REQ-SEC-01","Secret handling","Secrets absent from repository, logs, workbook and artifacts","Security","P0 - Critical","Credential compromise","Test keys injected by environment","Known canary secret values","1. Run representative workflow. 2. Search logs/run artifacts/repo for canaries. 3. Inspect error messages and exported diagnostics.","No secret value is persisted or displayed.","Zero exact/encoded canary matches outside approved secret store; configs have restricted permissions; diagnostic bundle redacts tokens.","CI","Hybrid","Security QA","Release","Not Run","","","Never use production keys for this case.","Secret-scan report and approved exceptions"],
    ["TC-COMP-001","REQ-COMP-01","Compatibility","Supported local/cluster matrix smoke test","Compatibility","P1 - High","Works only on developer machine","Supported Python 3.10/3.11 and macOS/Linux runners; cluster template variants","mpirun and srun templates","1. Run install/import/unit smoke on each OS/Python. 2. Generate scripts for both launchers. 3. Compare behavior.","Supported combinations start and generate correct platform-specific output.","All required matrix cells pass; unsupported version fails with clear guidance; srun/mpirun preserved by template; no OS-specific path corruption.","CI","Automated","Release QA","Release","Not Run","","","","CI matrix URLs, versions, generated scripts"],
    ["TC-UNIT-001","REQ-QUAL-01","Automated regression","Run repository unit test suites","Regression","P0 - Critical","Known behavior regresses","Dependencies installed; no real external calls required","test/test_workflow_state.py, test/test_workflow_validation.py, test/test_cluster_approval.py","1. Run each unittest module in isolated environment. 2. Capture totals/duration. 3. Investigate skips/failures.","All deterministic unit/regression tests pass.","Exit code 0; zero failures/errors; any skip is documented and approved; test log records commit SHA and environment.","CI","Automated","QA Automation","Smoke","Not Run","","","","Test command, complete report, coverage report if enabled"],
    ["TC-USAB-001","REQ-USAB-01","Usability","New tester can review plan and safely make a decision","Usability","P2 - Medium","Human approval error","Tester unfamiliar with code; scripted usability task","Sample two-step request with one deliberate input defect","1. Give tester only product docs. 2. Ask them to identify defect, request revision and approve. 3. Observe time/errors.","Interface communicates plan, dependencies, inputs and consequences clearly.","≥4/5 pilot testers identify deliberate defect; median completion ≤10 min; nobody triggers execution before intending to; blockers categorized.","Local macOS","Manual","UX Researcher","Extended","Not Run","","","","Observation notes, timings, anonymized survey"],
]
for row in cases:
    tests.append(row)
last = 4 + len(cases)
add_table(tests, f"A4:U{last}", "MasterTestCases")
tests.freeze_panes = "D5"
tests.auto_filter.ref = f"A4:U{last}"
set_widths(tests, {"A":15,"B":15,"C":22,"D":34,"E":20,"F":15,"G":27,"H":38,"I":30,"J":55,"K":42,"L":52,"M":18,"N":14,"O":20,"P":14,"Q":16,"R":10,"S":10,"T":38,"U":42})
for row in tests.iter_rows(min_row=5, max_row=last):
    for c in row:
        c.alignment = Alignment(vertical="top", wrap_text=True)
        c.border = Border(bottom=thin)
    tests.row_dimensions[c.row].height = 78
for col in ["O", "P", "Q", "R", "S", "T"]:
    for c in tests[col][4:last]:
        c.fill = PatternFill("solid", fgColor="FFF7D6")
for rng, formula in [(f"E5:E{last}", "Lists!$C$2:$C$11"),(f"F5:F{last}", "Lists!$B$2:$B$5"),(f"M5:M{last}", "Lists!$G$2:$G$7"),(f"N5:N{last}", "Lists!$D$2:$D$5"),(f"Q5:Q{last}", "Lists!$A$2:$A$7")]:
    dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    tests.add_data_validation(dv); dv.add(rng)
for status, fill in [("Pass",GREEN),("Fail",RED),("Blocked",AMBER),("In Progress",LIGHT_BLUE),("Not Applicable",PURPLE)]:
    tests.conditional_formatting.add(f"Q5:Q{last}", FormulaRule(formula=[f'$Q5="{status}"'], fill=PatternFill("solid", fgColor=fill)))
tests.conditional_formatting.add(f"A5:U{last}", FormulaRule(formula=['AND($R5="Yes",$S5="Yes")'], fill=PatternFill("solid", fgColor=RED)))

# Execution log
title(exec_log, "Test Execution Log", "One row per execution attempt. Pass/Fail flags are explicit and checked for contradictions.", 18)
exec_headers = ["Execution ID","Test Case ID","Release / Build","Commit SHA","Environment","Tester","Execution Date","Start Time","End Time","Status","Pass","Fail","Actual Result","Comment / Deviation","Evidence Link / Path","Defect ID","Retest Of","Record Check"]
exec_log.append([]); exec_log.append(exec_headers); header_style(exec_log[4])
exec_rows = [
    ["EXE-0001","TC-INST-001","v0.9.0-rc1","example-a1b2c3d","Local Linux","Example Tester",date(2026,9,1),"09:00","09:18","Pass","Yes","No","Python 3.11 environment installed; imports and pip check succeeded.","Worked example—replace with real execution data.","evidence/EXE-0001/install.log","","","OK"],
    ["EXE-0002","TC-PLAN-001","v0.9.0-rc1","example-a1b2c3d","API/Staging","Example DFT Tester",date(2026,9,1),"10:05","10:22","Fail","No","Yes","Plan changed SCF functional to PBE after an LDA relaxation.","Cross-step scientific consistency failure.","evidence/EXE-0002/plan_and_inputs.zip","DEF-001","","OK"],
    ["EXE-0003","TC-SLRM-001","v0.9.0-rc1","example-a1b2c3d","Cluster - Slurm","Example HPC Tester",date(2026,9,2),"13:00","13:06","Blocked","No","No","Could not obtain a test allocation; execution not started.","Blocker ENV-CLUSTER-02; rerun after partition restored.","evidence/EXE-0003/blocker.txt","","","OK"],
]
for r in exec_rows: exec_log.append(r)
for r in range(8, 205):
    exec_log.cell(r, 1, f'=IF(B{r}="","","EXE-"&TEXT(ROW()-4,"0000"))')
    exec_log.cell(r, 18, f'=IF(B{r}="","",IF(AND(K{r}="Yes",L{r}="Yes"),"ERROR: Both Pass and Fail",IF(AND(J{r}="Pass",K{r}<>"Yes"),"ERROR: Pass flag",IF(AND(J{r}="Fail",L{r}<>"Yes"),"ERROR: Fail flag",IF(AND(J{r}="Fail",P{r}=""),"ERROR: Defect required","OK")))))')
add_table(exec_log, "A4:R204", "ExecutionRecords")
exec_log.freeze_panes = "C5"
set_widths(exec_log, {"A":14,"B":15,"C":17,"D":18,"E":18,"F":20,"G":14,"H":11,"I":11,"J":15,"K":10,"L":10,"M":45,"N":38,"O":40,"P":14,"Q":14,"R":24})
for row in exec_log.iter_rows(min_row=5, max_row=204):
    for c in row: c.alignment = Alignment(vertical="top", wrap_text=True)
for rng, formula in [("E5:E204","Lists!$G$2:$G$7"),("J5:J204","Lists!$A$2:$A$7")]:
    dv=DataValidation(type="list",formula1=formula,allow_blank=True); exec_log.add_data_validation(dv); dv.add(rng)
for rng in ["K5:K204","L5:L204"]:
    dv=DataValidation(type="list",formula1='"Yes,No"',allow_blank=True); exec_log.add_data_validation(dv); dv.add(rng)
exec_log.conditional_formatting.add("R5:R204", FormulaRule(formula=['LEFT($R5,5)="ERROR"'], fill=PatternFill("solid",fgColor=RED), font=Font(color="9B1C1C",bold=True)))
exec_log.conditional_formatting.add("J5:J204", FormulaRule(formula=['$J5="Pass"'], fill=PatternFill("solid",fgColor=GREEN)))
exec_log.conditional_formatting.add("J5:J204", FormulaRule(formula=['$J5="Fail"'], fill=PatternFill("solid",fgColor=RED)))
exec_log.conditional_formatting.add("J5:J204", FormulaRule(formula=['$J5="Blocked"'], fill=PatternFill("solid",fgColor=AMBER)))

# Defects
title(defects, "Defect Log", "Link every failed execution to a reproducible defect. Never store secrets in evidence.", 16)
def_headers=["Defect ID","Title","Related Test Case","Execution ID","Severity","Priority","Status","Component","Detected In Build","Owner","Date Opened","Reproduction Steps","Expected","Actual","Evidence Link / Path","Resolution / Retest Notes"]
defects.append([]); defects.append(def_headers); header_style(defects[4])
defects.append(["DEF-001","SCF step changes requested XC from LDA to PBE","TC-PLAN-001","EXE-0002","S2 - Major","P0 - Critical","New","Workflow planning","v0.9.0-rc1","Planning Team",date(2026,9,1),"Submit README Si LDA vc-relax→scf→nscf prompt; inspect SCF input.","LDA inherited by every dependent step.","SCF input contains PBE.","evidence/EXE-0002/plan_and_inputs.zip","Pending triage; retest must repeat full workflow consistency case."])
for r in range(6,105): defects.cell(r,1,f'=IF(B{r}="","","DEF-"&TEXT(ROW()-4,"000"))')
add_table(defects,"A4:P104","DefectRecords")
defects.freeze_panes="C5"
set_widths(defects,{"A":13,"B":36,"C":18,"D":15,"E":16,"F":15,"G":18,"H":22,"I":18,"J":18,"K":14,"L":48,"M":38,"N":38,"O":40,"P":42})
for row in defects.iter_rows(min_row=5,max_row=104):
    for c in row: c.alignment=Alignment(vertical="top",wrap_text=True)
for rng, formula in [("E5:E104","Lists!$E$2:$E$5"),("F5:F104","Lists!$B$2:$B$5"),("G5:G104","Lists!$F$2:$F$8")]:
    dv=DataValidation(type="list",formula1=formula,allow_blank=True); defects.add_data_validation(dv); dv.add(rng)

# Requirements traceability
title(trace,"Requirements Traceability Matrix","Links platform requirements to test cases so release coverage gaps are visible.",7)
tr_headers=["Requirement ID","Capability / Control","Source Area","Mapped Test Cases","Coverage Count","Criticality","Coverage Status"]
trace.append([]); trace.append(tr_headers); header_style(trace[4])
reqs=[
 ["REQ-INST-01","Supported environment installs and imports cleanly","README / CLUSTER_INSTALL","TC-INST-001",1,"Critical","Covered"],
 ["REQ-CLI-01","Cluster launcher starts and exposes help","README / launcher","TC-CLI-001",1,"Critical","Covered"],
 ["REQ-CFG-01","Per-user cluster setup preserves config and secrets","cluster_agent / CLUSTER_INSTALL","TC-CFG-001",1,"High","Covered"],
 ["REQ-PLAN-01","Scientifically correct minimal dependency plan","planner / validation","TC-PLAN-001",1,"Critical","Covered"],
 ["REQ-APR-01","No execution before approval","cluster_agent / jobs","TC-APR-001",1,"Critical","Covered"],
 ["REQ-APR-02","Revision remains approval-gated","cluster_agent / jobs","TC-APR-002",1,"High","Covered"],
 ["REQ-QE-01","QE syntax errors and wrappers handled safely","validation/qe_syntax","TC-QE-001",1,"Critical","Covered"],
 ["REQ-QE-02","Invalid structures blocked","validation/workflow","TC-QE-002",1,"Critical","Covered"],
 ["REQ-QE-03","XC and pseudopotential family consistent","validation/workflow","TC-QE-003",1,"Critical","Covered"],
 ["REQ-QE-04","SOC relativity and branch isolation enforced","validation / workflow state","TC-QE-004",1,"Critical","Covered"],
 ["REQ-MP-01","Materials lookup resolves and preserves provenance","tool_mp / prompt utils","TC-MP-001",1,"High","Covered"],
 ["REQ-SLRM-01","Site template and bounded resources preserved","execute_code/slurm","TC-SLRM-001",1,"Critical","Covered"],
 ["REQ-SLRM-02","Remote lifecycle uploads, submits, monitors, retrieves","cluster_agent","TC-SLRM-002",1,"Critical","Covered"],
 ["REQ-DAG-01","Dependencies and parallel branches execute safely","workflow_state","TC-DAG-001",1,"Critical","Covered"],
 ["REQ-REC-01","Submitted work resumes without duplication","workflow_state / cluster_agent","TC-REC-001",1,"Critical","Covered"],
 ["REQ-REC-02","Targeted rerun preserves evidence","workflow_state","TC-REC-002",1,"High","Covered"],
 ["REQ-PHON-01","Phonon artifacts are staged and validated","cluster_agent / results","TC-PHON-001",1,"High","Covered"],
 ["REQ-RES-01","Answers derive from scoped completed evidence","results/evidence_qa","TC-RES-001",1,"Critical","Covered"],
 ["REQ-PLOT-01","Electronic plots reflect source coordinates","results/electronic_plots","TC-PLOT-001",1,"High","Covered"],
 ["REQ-API-01","Service provides health status","server.py","TC-API-001",1,"Critical","Covered"],
 ["REQ-AUTH-01","Magic-link lifecycle is secure","auth.py","TC-AUTH-001",1,"Critical","Covered"],
 ["REQ-AUTH-02","Authentication endpoint is rate limited/non-enumerating","auth.py / ratelimit","TC-AUTH-002",1,"High","Covered"],
 ["REQ-JOB-01","CPU and concurrency policy enforced","jobs.py","TC-JOB-001",1,"Critical","Covered"],
 ["REQ-JOB-02","Jobs isolated by user","jobs.py","TC-JOB-002",1,"Critical","Covered"],
 ["REQ-JOB-03","Cancellation and credits reconcile idempotently","jobs.py / credits","TC-JOB-003",1,"High","Covered"],
 ["REQ-ADM-01","Administration is authorized and audited","admin.py","TC-ADM-001",1,"Critical","Covered"],
 ["REQ-ART-01","Artifact paths cannot escape authorized run","artifacts.py","TC-ART-001",1,"Critical","Covered"],
 ["REQ-PERF-01","Polling meets approved service baseline","server / jobs / db","TC-PERF-001",1,"Medium","Covered"],
 ["REQ-SEC-01","Secrets are not persisted or exposed","configuration / logging","TC-SEC-001",1,"Critical","Covered"],
 ["REQ-COMP-01","Supported platforms behave consistently","README / launchers","TC-COMP-001",1,"High","Covered"],
 ["REQ-QUAL-01","Deterministic regression suite remains green","test/","TC-UNIT-001",1,"Critical","Covered"],
 ["REQ-USAB-01","Approval workflow is understandable and safe","cluster_agent UI","TC-USAB-001",1,"Medium","Covered"],
]
for r in reqs: trace.append(r)
add_table(trace,f"A4:G{4+len(reqs)}","RequirementCoverage")
trace.freeze_panes="A5"
set_widths(trace,{"A":18,"B":48,"C":30,"D":25,"E":15,"F":15,"G":18})
for row in trace.iter_rows(min_row=5,max_row=4+len(reqs)):
    for c in row: c.alignment=Alignment(vertical="top",wrap_text=True)

# Dashboard formulas and charts
title(dash,"TritonDFT QA Dashboard","Live summary driven by the Master Test Case Catalogue and Execution Log.",12)
section(dash,4,"Release readiness overview",12)
cards=[("A6","Total cases",f'=COUNTA(\'Test Cases\'!A5:A{last})'),("D6","P0 cases",f'=COUNTIF(\'Test Cases\'!F5:F{last},"P0*")'),("G6","Executed",'=COUNTIFS(\'Execution Log\'!B5:B204,"<>",\'Execution Log\'!J5:J204,"<>Not Run")'),("J6","Pass rate",'=IFERROR(COUNTIF(\'Execution Log\'!J5:J204,"Pass")/COUNTIFS(\'Execution Log\'!B5:B204,"<>",\'Execution Log\'!J5:J204,"<>Not Run"),0)')]
for cell,label,formula in cards:
    col=dash[cell].column; row=dash[cell].row
    dash.merge_cells(start_row=row,end_row=row,start_column=col,end_column=col+1)
    dash.cell(row,col,label); dash.cell(row,col).fill=PatternFill("solid",fgColor=LIGHT_BLUE); dash.cell(row,col).font=Font(bold=True,color=NAVY)
    dash.merge_cells(start_row=row+1,end_row=row+2,start_column=col,end_column=col+1)
    dash.cell(row+1,col,formula); dash.cell(row+1,col).font=Font(bold=True,size=22,color=NAVY); dash.cell(row+1,col).alignment=Alignment(horizontal="center",vertical="center")
    for rr in range(row,row+3):
        for cc in range(col,col+2): dash.cell(rr,cc).border=Border(left=thin,right=thin,top=thin,bottom=thin)
dash["J7"].number_format="0.0%"
dash["A11"]="Execution Status"; dash["B11"]="Count"; dash["D11"]="Priority"; dash["E11"]="Case Count"; dash["G11"]="Release gate"; dash["H11"]="Current result"
for c in [dash["A11"],dash["B11"],dash["D11"],dash["E11"],dash["G11"],dash["H11"]]:
    c.fill=PatternFill("solid",fgColor=BLUE); c.font=Font(color=WHITE,bold=True); c.alignment=Alignment(horizontal="center")
statuses=["Pass","Fail","Blocked","In Progress","Not Run"]
for i,s in enumerate(statuses,12):
    dash.cell(i,1,s); dash.cell(i,2,f'=COUNTIF(\'Execution Log\'!J5:J204,A{i})')
priorities=["P0 - Critical","P1 - High","P2 - Medium","P3 - Low"]
for i,p in enumerate(priorities,12):
    dash.cell(i,4,p); dash.cell(i,5,f'=COUNTIF(\'Test Cases\'!F5:F{last},D{i})')
gates=[
    ("P0 execution",f'=IF(COUNTIF(\'Test Cases\'!F5:F{last},"P0*")=COUNTIFS(\'Test Cases\'!F5:F{last},"P0*",\'Test Cases\'!Q5:Q{last},"Pass"),"PASS","NOT READY")'),
    ("No open S1/S2",'=IF(COUNTIFS(\'Defect Log\'!E5:E104,"S1*",\'Defect Log\'!G5:G104,"<>Closed")+COUNTIFS(\'Defect Log\'!E5:E104,"S2*",\'Defect Log\'!G5:G104,"<>Closed")=0,"PASS","NOT READY")'),
    ("Overall pass ≥95%",'=IF(J7>=95%,"PASS","NOT READY")'),
    ("No record errors",'=IF(COUNTIF(\'Execution Log\'!R5:R204,"ERROR*")=0,"PASS","NOT READY")'),
]
for i,(g,f) in enumerate(gates,12): dash.cell(i,7,g); dash.cell(i,8,f)
dash["G17"]="Overall recommendation"; dash["H17"]='=IF(COUNTIF(H12:H15,"NOT READY")=0,"READY FOR SIGN-OFF","NOT READY")'
dash["G17"].font=Font(bold=True); dash["H17"].font=Font(bold=True)
dash.conditional_formatting.add("H12:H17",FormulaRule(formula=['OR(H12="PASS",H12="READY FOR SIGN-OFF")'],fill=PatternFill("solid",fgColor=GREEN)))
dash.conditional_formatting.add("H12:H17",FormulaRule(formula=['H12="NOT READY"'],fill=PatternFill("solid",fgColor=RED)))
chart=DoughnutChart(); chart.title="Execution mix"; chart.add_data(Reference(dash,min_col=2,min_row=11,max_row=16),titles_from_data=True); chart.set_categories(Reference(dash,min_col=1,min_row=12,max_row=16)); chart.height=7; chart.width=10; dash.add_chart(chart,"A20")
bar=BarChart(); bar.type="col"; bar.style=10; bar.title="Test inventory by priority"; bar.y_axis.title="Cases"; bar.add_data(Reference(dash,min_col=5,min_row=11,max_row=15),titles_from_data=True); bar.set_categories(Reference(dash,min_col=4,min_row=12,max_row=15)); bar.height=7; bar.width=10; dash.add_chart(bar,"G20")
set_widths(dash,{"A":18,"B":13,"C":4,"D":18,"E":13,"F":4,"G":24,"H":23,"I":4,"J":18,"K":13,"L":4})
dash.freeze_panes="A4"

# Release sign-off
title(signoff,"Release Test Summary and Sign-off","Complete after execution. Formula cells draw from the logs; approval cells are controlled fields.",8)
section(signoff,4,"Release identification",8)
fields=[("Release / version","Enter version"),("Commit SHA","Enter full SHA"),("Test cycle","Smoke / Release / Extended"),("Test lead","Enter name"),("Test start","Enter date"),("Test end","Enter date"),("Primary environment","Enter environment")]
for i,(a,b) in enumerate(fields,5): signoff.cell(i,1,a); signoff.cell(i,2,b)
section(signoff,13,"Measured quality results",8)
metrics=[("Total test cases",f'=COUNTA(\'Test Cases\'!A5:A{last})'),("Executed attempts",'=COUNTA(\'Execution Log\'!B5:B204)'),("Passed attempts",'=COUNTIF(\'Execution Log\'!J5:J204,"Pass")'),("Failed attempts",'=COUNTIF(\'Execution Log\'!J5:J204,"Fail")'),("Blocked attempts",'=COUNTIF(\'Execution Log\'!J5:J204,"Blocked")'),("Pass rate",'=IFERROR(B16/(B15-B18),0)'),("Open S1/S2 defects",'=COUNTIFS(\'Defect Log\'!E5:E104,"S1*",\'Defect Log\'!G5:G104,"<>Closed")+COUNTIFS(\'Defect Log\'!E5:E104,"S2*",\'Defect Log\'!G5:G104,"<>Closed")')]
for i,(a,f) in enumerate(metrics,14): signoff.cell(i,1,a); signoff.cell(i,2,f)
signoff["B19"].number_format="0.0%"
section(signoff,22,"Approval",8)
approval=[("QA Lead","Enter name","Pending","Enter date","Comments / exceptions"),("DFT Domain Lead","Enter name","Pending","Enter date","Scientific validity statement"),("Engineering Lead","Enter name","Pending","Enter date","Technical risk statement"),("Product / Release Owner","Enter name","Pending","Enter date","Final release decision")]
signoff["A23"],signoff["B23"],signoff["C23"],signoff["D23"],signoff["E23"]="Role","Approver","Decision","Date","Comment"
header_style(signoff[23])
for row in approval: signoff.append(row)
dv=DataValidation(type="list",formula1="Lists!$H$2:$H$5",allow_blank=False); signoff.add_data_validation(dv); dv.add("C24:C27")
signoff["A30"]="Final release decision"; signoff["B30"]='=IF(COUNTIF(C24:C27,"Rejected")>0,"REJECTED",IF(COUNTIF(C24:C27,"Pending")>0,"PENDING",IF(COUNTIF(C24:C27,"Approved with Exceptions")>0,"APPROVED WITH EXCEPTIONS","APPROVED")))'
signoff["A30"].font=Font(bold=True); signoff["B30"].font=Font(bold=True,size=14)
set_widths(signoff,{"A":28,"B":25,"C":27,"D":16,"E":55,"F":12,"G":12,"H":12})
for row in signoff.iter_rows(min_row=5,max_row=30):
    for c in row: c.alignment=Alignment(vertical="top",wrap_text=True)

# Common cosmetics, print settings and workbook metadata
for ws in [readme,dash,tests,exec_log,defects,trace,signoff]:
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.25; ws.page_margins.right = 0.25; ws.page_margins.top = 0.5; ws.page_margins.bottom = 0.5
    ws.oddFooter.center.text = "TritonDFT QA | Controlled copy when exported"
    ws.oddFooter.right.text = "Page &P of &N"
    ws.auto_filter.ref = ws.auto_filter.ref
wb.properties.title = "TritonDFT Industry Test Plan"
wb.properties.subject = "Software and scientific workflow verification"
wb.properties.creator = "OpenAI Codex"
wb.properties.keywords = "TritonDFT, QA, test plan, Quantum ESPRESSO, Slurm"
OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)

# Reopen as a structural validation pass.
check = load_workbook(OUT, data_only=False)
assert check.sheetnames == ["Read Me","Dashboard","Test Cases","Execution Log","Defect Log","Requirements Traceability","Release Sign-off","Lists"]
assert check["Test Cases"].max_row == last
assert len(check["Test Cases"].tables) == 1
assert check["Execution Log"]["R8"].value.startswith("=IF")
assert check["Dashboard"]["J7"].value.startswith("=IFERROR")
assert check["Lists"].sheet_state == "hidden"
print(OUT.resolve())
print(f"test_cases={len(cases)} requirements={len(reqs)} sheets={len(check.sheetnames)}")
