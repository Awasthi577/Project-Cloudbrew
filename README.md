# CloudBrew

CloudBrew is a next-generation cross-cloud package manager and orchestration tool that makes provisioning infrastructure as simple as installing software.  

Spin up EC2s, scale clusters, or deploy multi-tier apps across **AWS, GCP, and Azure** — all with a single command.  

---

## Features

### 1. Package-Manager Style Infra
Install, update, and remove cloud resources like you do with Homebrew.  

### 2. Native Fast Paths
Simple tasks (VMs, storage, autoscaling groups) handled directly via cloud SDKs for **sub-2s execution**.  

### 3. Orchestration Offloading
Complex multi-tier infra (DB + VPC + LB) seamlessly offloaded to **Terraform** or **Pulumi**.  

### 4. Progressive Learning
CloudBrew “learns” patterns from Terraform/Pulumi runs and can later handle them **natively**, saving time & memory.  

### 5. State Management
Tracks resources created, detects drift, and ensures infra consistency.  

### 6. Command Healing
Friendly suggestions for ambiguous inputs, with intelligent defaults & provider-specific mappings.  

---

## Setup & Installation

```bash
# Clone the repo
git clone https://github.com/Awasthi577/Project-Cloudbrew.git
cd Project-Cloudbrew

# (Optional) create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.\.venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Install CloudBrew CLI in editable mode
pip install -e .
```

### Verify installation:
```bash
cloudbrew --help
```

## Running Commands
### Autoscaling

```bash
cloudbrew autoscale create --name web-tier --min 1 --max 4 --cpu 50 --type t3.micro
```

### CloudBrew → Terraform

```bash
cloudbrew terraform plan -f infra.yaml
cloudbrew terraform apply -f infra.yaml
cloudbrew terraform destroy -f infra.yaml
```

## Provision from YAML

```yaml
resources:
  - type: vm
    name: web-tier
    instance_type: t3.micro
    autoscale:
      min: 1
      max: 4
      cpu: 50
```
### Apply it:

``` bash
cloudbrew apply -f infra.yaml
```

## Tested Workflows

1. CLI wiring (Typer)  
2. Autoscaler (create, min/max rules)  
3. DSL parsing (infra.yaml → spec)  
4. Terraform offloading (plan/apply/destroy)  
5. Store & State tracking  
6. No-op adapter for safe testing


## Roadmap

### MVP Phase (Foundations)
1. Core CLI + DSL for human-friendly cloud commands  
2. Native support for simple ops (VMs, storage, autoscale basics)  
3. Offloading complex orchestration to Terraform  
4. Built-in state management to track resources and detect drift  

### Early Adoption (Scaling Up)
1. Add Pulumi support for alternative orchestration workflows  
2. Advanced healing: detect and fix drift or partial failures  
3. Remote state sync for teams and CI/CD pipelines  
4. Pattern recognition engine to start learning common infra recipes  

### Mature Phase (Autonomous CloudBrew)
1. Achieve 90% native execution (minimal Terraform/Pulumi use)  
2. Intelligent resource management with caching & parallel execution  
3. Self-healing infra that can auto-recover from failures or drift  
4. User prompts before switching from offloaded → native execution  
5. Progressive optimization for speed, memory efficiency, and reliability  


## Failure Handling

CloudBrew is designed to gracefully handle:

1. Drift from manual console changes  
2. API rate limits and latency issues  
3. Orchestration partial failures  
4. Ambiguous DSL inputs with guided suggestions  
5. State drift and mismatched tracking  
6. Provider SDK gaps and abstraction leaks  
7. Dependency changes in Terraform/Pulumi  
8. Latency under heavy load  
9. Security/credential issues  
10. Runaway infra usage  
