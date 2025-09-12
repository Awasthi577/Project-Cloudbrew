---

# CloudBrew

CloudBrew is a **cross-cloud orchestration and package manager** that makes provisioning infrastructure as simple as installing software.

It abstracts away Terraform and Pulumi into **one-line CLI commands**. CloudBrew dynamically resolves resources across AWS, Azure, and GCP, and falls back gracefully if binaries, providers, or credentials are missing.

---

## Quickstart Guide

Follow these steps to get CloudBrew up and running on your machine.

### 1. Initialize your project repository
Create a new folder for your infrastructure code and initialize git:

```bash
mkdir my-cloudbrew-project
cd my-cloudbrew-project
git init
```
### 2. Clone or install CloudBrew

``` bash
git clone https://github.com/<your-username>/Project-Cloudbrew.git
cd Project-Cloudbrew
python -m venv .venv
.venv\Scripts\activate  # on Windows
# source .venv/bin/activate  # on Linux/macOS

pip install -e ".[cloud-creds,dev]"

```
### 3. Configure CloudBrew for your cloud provider

``` bash
cloudbrew init
```
You’ll be prompted to select a provider:

    1. aws

    2. gcp

    3. azure

Enter your credentials when prompted.

CloudBrew securely stores them in your system keyring (with Fernet fallback if unavailable).

A config file is created at ~/.cloudbrew/config.json.

For non-interactive defaults (no credentials, provider=none):

``` bash
cloudbrew init --yes
```

### 4. Verify Setup

Check that your configuration file was created:

``` bash
type %USERPROFILE%\.cloudbrew\config.json   # on Windows
cat ~/.cloudbrew/config.json                # on Linux/macOS
```

### 5. Run your first command

Plan a VM with a single line:

``` bash
cloudbrew create-vm myvm --image ubuntu --size small --region us-east-1 --provider terraform
```

Or use the dynamic fallback to create a resource by short name:

``` bash
cloudbrew bucket my-bucket --region us-east-1
```
    1. By default this produces a plan only (no apply).

    2. Add --yes to apply immediately.

    3. Add --async to enqueue it into the Offload Manager for background execution.

### 6. Destroy resources when finished

``` bash
cloudbrew destroy-vm myvm --provider terraform
```


---

## Features & Their Implementation

Below is the **complete feature list** with details of *how each is implemented in code*.


### 1. Dynamic Lookup & Resolver

**What it does:**

* Any unknown command (`cloudbrew bucket my-bucket`) is dynamically mapped to the correct provider resource (`aws_s3_bucket`, `azurerm_storage_container`, etc).

**Implementation:**

* **File:** `LCF/resource_resolver.py`
* **Key functions:**

  * `resolve(token, params)` → returns `{_provider, _resolved}`.
  * `_query_terraform_schema()` → runs `terraform providers schema -json`, parses JSON streaming.
* **Cache:**

  * SQLite DB `.cloudbrew_cache/resources.db`.
  * Cached mappings of `logical_type` → `provider resource`.
* **Fallbacks:**

  * Missing provider schema → uses cache.
  * Ambiguous mapping → prints candidate list.
  * Missing everything → falls back to noop adapter.

---

### 2. Terraform Adapter

**What it does:**

* Converts canonical VM spec into HCL, runs Terraform plan/apply/destroy, and streams output.
* Cleans up `.cloudbrew_tf/` workdirs after runs.

**Implementation:**

* **File:** `LCF/cloud_adapters/terraform_adapter.py` (\~348 LOC).
* **Key functions:**

  * `stream_create_instance(logical_id, spec, plan_only=False)`
  * `stream_apply_plan(plan_path)`
  * `stream_destroy_instance(logical_id, spec)`
  * `create_instance` (compat wrapper).
* **HCL translation:** canonical VM spec (dict) → provider HCL blocks.
* **Workdirs:** `.cloudbrew_tf/<logical>` (with `main.tf`, plan files).
* **Cleanup:** `_cleanup()` deletes `.terraform/`, plan, and tf files.
* **Fallbacks:**

  * Terraform missing → error with hint (`CLOUDBREW_TERRAFORM_BIN`).
  * Creds missing → fallback to `null_resource`.
  * Network/provider download fails → cached schema or noop adapter.

---

### 3. Pulumi Adapter

**What it does:**

* Runs Pulumi plans/applies/destroys either via Automation API or subprocess CLI.

**Implementation:**

* **File:** `LCF/cloud_adapters/pulumi_adapter.py`.
* **Key functions:**

  * `plan(spec, stack_name)` → `pulumi preview` or Automation API.
  * `apply(spec, stack_name)` → `pulumi up`.
  * `destroy_instance(stack_name)` → `pulumi destroy`.
* **Project template:** generates `Pulumi.yaml`, `__main__.py`, `spec.json`.
* **Fallbacks:**

  * Automation API not importable → subprocess fallback.
  * Pulumi CLI missing → safe error.
  * Spec translation not implemented → pass through raw spec.json.

---

### 4. CLI Wiring

**What it does:**

* Unified Typer-based CLI entrypoint (`setup.py → cloudbrew`).
* Routes explicit commands or dynamic fallbacks.

**Implementation:**

* **File:** `LCF/cli.py` (Typer app).
* **Commands:**

  * Static: `create-vm`, `destroy-vm`, `status`, `plan`, `apply-plan`.
  * Pulumi helpers: `pulumi-plan`, `pulumi-apply`, `pulumi-destroy`.
  * Offload: `offload enqueue`, `offload run-worker`.
  * Dynamic fallback: unknown verbs → `ResourceResolver`.
* **Execution flow:**

  ```
  CLI → Parser → ResourceResolver (if needed) → Adapter (Terraform/Pulumi) 
     → Streaming logs → Cleanup → Cache results
  ```
* **Flags:**

  * Default = plan.
  * `--yes` → apply.
  * `--async` → enqueue for Offload worker.

---

### 5. Offload Manager & Worker

**What it does:**

* Lets you enqueue heavy/long jobs instead of running inline.
* Worker polls jobs and executes them with retries.

**Implementation:**

* **File:** `LCF/offload_manager.py`.
* **Storage:** SQLite DB `cloudbrew_offload.db`.
* **Functions:**

  * `enqueue(command, payload)`
  * `fetch_pending()`
  * `mark_done()`
  * `run_worker(poll_interval)`
* **Fallbacks:**

  * If DB locked or missing → logs error, retries later.

---

### 6. Autoscaler

**What it does:**

* Runs scaling policies (`cpu>80%:scale+2`) against targets.
* Persists cooldowns to avoid flapping.

**Implementation:**

* **File:** `LCF/autoscaler.py`.
* **Functions:**

  * `parse_autoscale_string(policy_str)` → dict of rules.
  * `AutoscalerManager.run_once()` → checks rules, enqueues actions.
  * `AutoscalerManager.run_loop()` → periodic loop.
* **Storage:** `.cloudbrew_autoscaler.db` (SQLite).
* **Integration:** enqueues scale actions to Offload worker.
* **Fallbacks:**

  * Invalid policy → noop with warning.
  * No metrics → skip.

---

### 7. Status & State

**What it does:**

* Lists known resources and instances.
* Tracks backhaul of past runs.

**Implementation:**

* **File:** `LCF/state.py`.
* **DBs:**

  * `.cloudbrew_state.json` → small instance store.
  * `.cloudbrew_backhaul.db` → run logs (adapter, action, status).
* **Functions:**

  * `upsert_instance(logical_id, state)`
  * `list_instances()`

---

### 8. Testing & CI

**What it does:**

* Unit, mocked, integration tests with pytest.
* CI workflow runs pytest on push/PR.

**Implementation:**

* **Tests:** `tests/unit`, `tests/mocked`, `tests/integration`.
* **Scenarios:**

  * Terraform adapter streaming.
  * Dynamic resolver mapping.
  * Offload manager queue/worker.
  * Autoscaler cooldowns.
* **CI file:** `.github/workflows/ci.yml`.
* **Fallbacks tested:** terraform missing, creds missing, ambiguous schema.

---

## CLI Reference (Single-Line Commands)

### VM Lifecycle

```bash
cloudbrew create-vm myvm --image ubuntu-22.04 --size small --region us-east-1
cloudbrew create-vm myvm ... --yes     # apply
cloudbrew destroy-vm myvm --yes        # destroy
```

### Generic Resource (Dynamic Lookup)

```bash
cloudbrew bucket my-bucket --region us-east-1 --storage-class standard
```

### Pulumi

```bash
cloudbrew pulumi-plan --stack dev --spec examples/sample.yml
cloudbrew pulumi-apply --stack dev --yes
cloudbrew pulumi-destroy --stack dev --yes
```

### Offload

```bash
cloudbrew create-vm web01 --image ubuntu --size small --region us-east-1 --async
cloudbrew offload run-worker --db-path cloudbrew_offload.db
```

### Autoscaler

```bash
cloudbrew autoscale --policy "cpu>80%:scale+2" --target mycluster
```

### Status

```bash
cloudbrew status
```

---

## Fallback Matrix

| Component  | Primary Path         | Fallback 1       | Fallback 2     |
| ---------- | -------------------- | ---------------- | -------------- |
| Terraform  | terraform CLI        | noop adapter     | null\_resource |
| Pulumi     | Automation API       | CLI subprocess   | noop adapter   |
| Resolver   | provider schema JSON | cached DB        | candidate list |
| CLI        | explicit subcommand  | dynamic fallback | noop/error     |
| Offload    | sqlite queue         | inline run       | error log      |
| Autoscaler | policy parse         | cooldown skip    | noop           |

---

## Testing

Run all:

```bash
pytest -q
```

Safe smoke test:

```bash
cloudbrew create-vm test --image ubuntu --size small --region us-east-1
```

Dynamic resolver demo:

```bash
cloudbrew bucket logs-bucket --region us-east-1
```

Inspect cache DB:

```bash
sqlite3 .cloudbrew_cache/resources.db "select * from resource_mappings;"
```

---

## Project Structure

```
LCF/
  cloud_adapters/
    terraform_adapter.py
    pulumi_adapter.py
  autoscaler.py
  resource_resolver.py
  offload_manager.py
  state.py
tests/
  unit/
  mocked/
  integration/
.github/workflows/
  ci.yml
```

---

## Example Workflows

Plan → Apply → Destroy:

```bash
cloudbrew create-vm myvm --image ubuntu --size small --region us-east-1
cloudbrew create-vm myvm ... --yes
cloudbrew destroy-vm myvm --yes
```

Dynamic discovery:

```bash
cloudbrew bucket mybucket --region us-east-1 --yes
```

Async workflow:

```bash
cloudbrew create-vm myvm --image ubuntu --size small --region us-east-1 --async
cloudbrew offload run-worker
```

---

With CloudBrew, **everything is one line** — all resources, all providers, dynamic lookups, safe fallbacks, autoscaling, and offload processing are unified into a single CLI.

---
