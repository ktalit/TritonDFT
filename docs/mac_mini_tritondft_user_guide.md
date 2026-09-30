# TritonDFT User Guide for the Mac Mini Cluster

This guide explains how users run TritonDFT from the shared Mac mini cluster.

The administrator manages the TritonDFT application and provider settings. Each user manages only their personal configuration, SSH access, and Slurm scripts.

## 1. Log In

Log in to the Mac mini with the account provided by the administrator:

```bash
ssh your_macmini_username@mac-mini-hostname
```

The administrator must create your Mac mini account before you can use TritonDFT.

## 2. Create Your Personal TritonDFT Directory

Create the directory in your home directory:

```bash
mkdir -p ~/.tritondft
chmod 700 ~/.tritondft
```

Your personal TritonDFT files are kept here:

```text
~/.tritondft/
├── config.yaml
├── expanse-qe.sh
└── expanse-vasp.sh
```

The shared application code is managed separately by the administrator under:

```text
/opt/tritondft/TritonDFT/
```

## 3. Create Your Configuration File

Create the configuration file:

```bash
vi ~/.tritondft/config.yaml
```

Use the configuration template supplied by the administrator. A typical configuration looks like this:

```yaml
# This file is stored on the TritonDFT cluster, not on the compute cluster.
# Keep it private because it contains personal cluster settings.
active_cluster: expanse

defaults:
  # Leave empty to choose QE or VASP when TritonDFT starts.
  # Use qe or vasp to select one automatically.
  dft_code: ""

  # How often TritonDFT checks the Slurm job status, in seconds.
  poll_seconds: 30

  # Local directory for generated workflows and logs.
  work_dir: tmp

  # TritonDFT uses SSH public-key authentication.
  # OTP is not configured for this workflow.
  ssh_authentication: public_key

clusters:
  expanse:
    # Your username on the remote compute cluster.
    user_id: your_compute_cluster_username

    # The remote cluster login hostname.
    hostname: login.expanse.sdsc.edu

    # Optional SSH alias. Use this only when the alias already exists
    # in the Mac mini user's ~/.ssh/config file.
    ssh_alias: expanse

    # Remote directory where TritonDFT creates workflow directories.
    remote_working_directory: /scratch/your_compute_cluster_username/tritondft_runs

    # QE Slurm script. Required when dft_code is qe.
    qe_slurm_script: ~/.tritondft/expanse-qe.sh

    # Optional remote QE binary directory. Leave empty when the cluster's
    # module system makes pw.x and other QE programs available on PATH.
    remote_qe_bin_dir: ""

    # VASP Slurm script. Required when dft_code is vasp.
    vasp_slurm_script: ~/.tritondft/expanse-vasp.sh

    # Optional VASP executable override.
    # Examples: vasp_std, vasp_gam, vasp_ncl.
    remote_vasp_command: ""

    # Remote licensed VASP pseudopotential directory.
    # Expected layout:
    #   /home/your_compute_cluster_username/VASP_PP/PBE/Si/POTCAR
    #   /home/your_compute_cluster_username/VASP_PP/LDA/Si/POTCAR
    remote_vasp_potcar_root: /home/your_compute_cluster_username/VASP_PP

    # Normally the functional is selected from the DFT request.
    vasp_functional: ""
```

Protect the configuration file:

```bash
chmod 600 ~/.tritondft/config.yaml
```

### DFT code selection

The current agent supports these values:

```yaml
dft_code: ""
```

The agent asks whether to use Quantum ESPRESSO or VASP.

```yaml
dft_code: qe
```

The agent uses Quantum ESPRESSO automatically.

```yaml
dft_code: vasp
```

The agent uses VASP automatically.

Do not use:

```yaml
dft_code: both
```

The current agent does not support `both` as an automatic setting. Use an empty value to choose QE or VASP when the agent starts.

## 4. Configure SSH Access

The Mac mini must be able to connect to the compute cluster using an SSH key without an OTP prompt.

Test direct access:

```bash
ssh your_compute_cluster_username@login.expanse.sdsc.edu
```

If the administrator configured an SSH alias, test the alias:

```bash
ssh expanse
```

The private key must remain on the Mac mini. Never place private-key contents in `config.yaml` and never send the private key to anyone.

If a nonstandard key is used, the Mac mini account's SSH configuration may contain an entry like this:

```sshconfig
Host expanse
    HostName login.expanse.sdsc.edu
    User your_compute_cluster_username
    IdentityFile ~/.ssh/tritondft_expanse
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
    ControlMaster auto
    ControlPath ~/.ssh/control-%r@%h:%p
    ControlPersist 4h
```

The matching public key must be installed in the user's compute-cluster account. Only the public key may be shared with the cluster administrator.

## 5. Create the Slurm Scripts

Create the QE script:

```bash
vi ~/.tritondft/expanse-qe.sh
```

Create the VASP script if VASP will be used:

```bash
vi ~/.tritondft/expanse-vasp.sh
```

The scripts should contain the settings required by the compute cluster, including:

- Slurm account
- Partition
- Wall time
- Nodes and tasks
- Module commands
- MPI launch command
- QE or VASP executable command

Protect the scripts:

```bash
chmod 700 ~/.tritondft/expanse-qe.sh ~/.tritondft/expanse-vasp.sh
```

Use the actual script paths in `config.yaml`.

## 6. API Keys

For the administrator-managed setup, users should not put API keys in `config.yaml`.

The administrator configuration is stored separately at:

```text
/opt/tritondft/config/.env.cluster_admin
```

The administrator controls the model, backend, and provider keys. Users should not edit or copy that file.

If the administrator does not provide keys for a particular user, that user may be instructed to add personal keys to `config.yaml`:

```yaml
api_keys:
  openai: "your_personal_openai_key"
  materials_project: "your_personal_materials_project_key"
```

Never commit personal API keys to GitHub or share them in a support request.

## 7. Start TritonDFT

Start the agent with:

```bash
tritondft-cluster
```

If `dft_code` is empty, choose one of the displayed options:

```text
Quantum ESPRESSO
VASP
```

Then enter the DFT request at:

```text
DFT request>
```

To stop the agent, enter:

```text
exit
```

or:

```text
quit
```

## 8. Change Settings Later

The agent should not ask setup questions on every run. To change your settings, edit the configuration manually:

```bash
vi ~/.tritondft/config.yaml
```

After saving, restart the agent:

```bash
tritondft-cluster
```

To switch the active cluster, change:

```yaml
active_cluster: expanse
```

Add another profile under `clusters` when needed:

```yaml
clusters:
  expanse:
    user_id: your_expanse_username
    hostname: login.expanse.sdsc.edu
    remote_working_directory: /scratch/your_expanse_username/tritondft_runs
    qe_slurm_script: ~/.tritondft/expanse-qe.sh
    vasp_slurm_script: ~/.tritondft/expanse-vasp.sh

  merced:
    user_id: your_merced_username
    hostname: login.merced.example.edu
    remote_working_directory: /scratch/your_merced_username/tritondft_runs
    qe_slurm_script: ~/.tritondft/merced-qe.sh
    vasp_slurm_script: ~/.tritondft/merced-vasp.sh
```

## 9. Troubleshooting

### Administrator configuration permission denied

If the agent reports:

```text
PermissionError: [Errno 13] Permission denied: /opt/tritondft/config/.env.cluster_admin
```

the process running TritonDFT cannot read the administrator configuration. The administrator must either run the agent under the configured service account or adjust access using a protected group. Do not make the file world-readable just to hide the error.

### SSH connection fails

Test SSH separately:

```bash
ssh your_compute_cluster_username@login.expanse.sdsc.edu
```

Check:

- The hostname is correct.
- The remote username is correct.
- The public key is installed on the compute cluster.
- The private key is available to the Mac mini account running TritonDFT.
- The `ssh_alias` exists if the YAML uses an alias.

### VASP POTCAR files are not found

Verify the remote directory contains functional-specific folders:

```text
VASP_PP/PBE/Si/POTCAR
VASP_PP/LDA/Si/POTCAR
```

Set the root directory in YAML:

```yaml
remote_vasp_potcar_root: /home/your_compute_cluster_username/VASP_PP
```

The functional is selected from the DFT request. For example, a request containing `PBE` uses the `PBE` directory, while a request containing `LDA` uses the `LDA` directory.

### The wrong DFT code starts automatically

Check `config.yaml`:

```bash
grep -n "dft_code" ~/.tritondft/config.yaml
```

Use an empty value to choose interactively:

```yaml
dft_code: ""
```

## 10. Files and Ownership Summary

Shared application code:

```text
/opt/tritondft/TritonDFT/
```

User configuration and Slurm scripts:

```text
~/.tritondft/config.yaml
~/.tritondft/*.sh
```

Administrator provider configuration:

```text
/opt/tritondft/config/.env.cluster_admin
```

Administrator approved-user list, when used:

```text
/opt/tritondft/config/approved_user_ids.txt
```

Users should have access only to their own home-directory configuration and scripts. Administrator provider files must remain protected.
