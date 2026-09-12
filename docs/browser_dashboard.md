# Experimental browser dashboard

TritonDFT's Tk/X11 interface remains the default. The browser dashboard is an
optional transport for terminal-started workflows; it does not replace the
terminal, scientific agent, validators, workflow checkpoints, SSH transport,
or Slurm execution.

## Modes

Existing X11 behavior:

```bash
export TRITONDFT_DASHBOARD_MODE=x11
./tritondft-cluster
```

The same mode can be selected per invocation with
`./tritondft-cluster --dashboard xwindows` (or `--dashboard x11`).

Experimental browser behavior:

```bash
export TRITONDFT_DASHBOARD_MODE=browser
./tritondft-cluster
```

The browser option can also be selected with `--dashboard browser`. For an
HTTPS deployment, configure the public endpoint and select the strict HTTPS
option:

```bash
export TRITONDFT_DASHBOARD_PUBLIC_URL=https://dashboard.tritondft.example
./tritondft-cluster --dashboard https
```

The `https` selection rejects missing or non-HTTPS public URLs so it cannot
silently fall back to a plain HTTP session link.

The terminal prints a secret session URL. Open that URL in a browser. Do not
share it: possession grants access to that one dashboard session.

Both transports:

```bash
export TRITONDFT_DASHBOARD_MODE=both
./tritondft-cluster
```

In the initial experimental `both` implementation, the browser observes the
same workflow and files while the unchanged X11 window remains authoritative
for plan/input approval. This avoids changing Tk's blocking approval semantics
while the two transports are compared.

An unset or unrecognized value resolves to `x11`, preserving existing behavior.

## Local development

To browse completed calculations on a Mac without initializing SSH or the DFT
agent, run:

```bash
bash scripts/browse_workflows.sh
```

The saved workflows are listed immediately. At the `workflow>` prompt, use
`open 1`, `open 2`, `latest`, or just `1`/`2`. Use `workflows` to refresh the
list and `quit` to leave. The printed secure URL opens the selected workflow.

Stop the local dashboard service with one command:

```bash
bash scripts/stop_dashboard.sh
```

This verifies the listener belongs to the current user and is TritonDFT's
`browser_dashboard.py` before stopping it. It does not cancel Slurm jobs.

Use a non-default calculation directory when needed:

```bash
bash scripts/browse_workflows.sh --work-dir /path/to/calculations
```

The CLI starts or reuses one dashboard service on `127.0.0.1:8008`. It does not
allocate a port per session. When TritonDFT runs on a remote server, forward the
shared port:

```bash
ssh -L 8008:127.0.0.1:8008 username@tritondft-server
```

Then use the printed `http://127.0.0.1:8008/s/<token>` URL locally. Configuration:

```bash
export TRITONDFT_DASHBOARD_HOST=127.0.0.1
export TRITONDFT_DASHBOARD_PORT=8008
export TRITONDFT_DASHBOARD_SESSION_TTL=28800
export TRITONDFT_DASHBOARD_DB=tmp/dashboard_sessions.sqlite3
```

For deployment behind HTTPS, set the externally visible origin while keeping
the application bound to an internal address:

```bash
export TRITONDFT_DASHBOARD_PUBLIC_URL=https://dashboard.tritondft.example
```

Nginx or Cloudflare must proxy normal HTTP and WebSocket upgrades. The
application contains no vendor-specific reverse-proxy dependency. Configure the
proxy not to record `/s/`, `/api/s/`, or `/ws/` URL paths because they contain
bearer tokens. Uvicorn access logging is disabled by the bundled launcher.

## Security and lifecycle

- Session URLs contain cryptographically random bearer tokens.
- Only token hashes are persisted.
- Tokens are never included in application logs.
- Every session is bound to one canonical workflow directory.
- Browser file requests use opaque IDs, not paths.
- Symlinks, large QE `.save` trees, pseudopotentials, and unknown extensions are
  excluded.
- Only current `.in` approval drafts can be edited.
- Browser-selected structure paths must resolve inside the registered workflow
  directory; the web service cannot request arbitrary server files.
- Saving is explicit and protected by a content hash against stale overwrites.
- The authoritative terminal process applies existing validation before accepting
  browser approval.
- Browser disconnect does not stop submitted HPC work.
- Sessions expire after the configured TTL and can be explicitly closed by the
  terminal-side session object.

For production, run the dashboard service under a dedicated service manager and
place its SQLite registry in a protected location. A multi-host or multi-worker
deployment should replace the SQLite mailbox/live routing with PostgreSQL plus
a shared event broker such as Redis.

## Browser features in the initial implementation

- Secure session URL and WebSocket updates
- Workflow and step/job status
- Validation report
- Relaxed-structure display with lattice lengths, cell volume, cell vectors,
  atomic positions, and CIF download
- VESTA location/launch and workflow-folder opening when the dashboard service
  runs on the same desktop computer; remote dashboards use local CIF download
- Bounded input/result file viewing
- Explicit editing of current input drafts
- Browser plan/input approve, revise, and cancel in `browser` mode
- Interactive Bands, DOS, PDOS, phonon-dispersion, and Raman plot controls
- VBM/Fermi/Midgap/absolute energy references, axis limits, and Raman FWHM
- PNG and PDF plot download
- Evidence-grounded Ask Results using the same structural-analysis, retrieval,
  citation-verification, and safe-math backend as the X11 monitor
- Simple messages delivered to the terminal approval wait loop
- Read-only sanitized Activity panel for workflow, SSH, and Slurm progress
- Explicit terminal-authentication banner; passwords and TOTP codes remain in
  the terminal and are never accepted by the web application
- WebSocket live updates with a state-preserving 15-second polling fallback

The terminal remains authoritative. A web browser cannot expose a native file
picker's absolute application path or launch software on a different computer.
Consequently, VESTA launch and folder opening act on the computer running the
dashboard service. When that is a remote host, download the CIF and open it in
VESTA on the local computer instead.

## Tests and load probe

Run focused tests:

```bash
PYTHONPATH=src python -m unittest test.test_browser_dashboard
```

The metadata-plane load probe simulates sessions, state reads, file listings,
and messages without executing DFT:

```bash
python scripts/browser_dashboard_load_test.py --sessions 10
python scripts/browser_dashboard_load_test.py --sessions 50
python scripts/browser_dashboard_load_test.py --sessions 100
python scripts/browser_dashboard_load_test.py --sessions 500
python scripts/browser_dashboard_load_test.py --sessions 1000
```

Record CPU, memory, operation latency, active HTTP/WebSocket connections,
session count, and errors in the target deployment. The bundled probe reports
process memory and metadata-operation latency; HTTP/WebSocket connection testing
should be run against the deployed reverse-proxy topology.
