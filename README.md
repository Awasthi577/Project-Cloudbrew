# CloudBrew

CloudBrew is a next-generation cross-cloud package manager and orchestration tool that makes provisioning infrastructure as simple as installing software.  

Spin up EC2s, scale clusters, or deploy multi-tier apps across **AWS, GCP, and Azure** — all with a single command.  

---

## Features

**1. Package-Manager Style Infra**  
  Install, update, and remove cloud resources like you do with Homebrew.  

**2. Native Fast Paths**  
  Simple tasks (VMs, storage, autoscaling groups) handled directly via cloud SDKs for **sub-2s execution**.  

**3. Orchestration Offloading**  
  Complex multi-tier infra (DB + VPC + LB) seamlessly offloaded to **Terraform** or **Pulumi**.  

**4. Progressive Learning**  
  CloudBrew “learns” patterns from Terraform/Pulumi runs and can later handle them **natively**, saving time & memory.  

**5. State Management**  
  Tracks resources created, detects drift, and ensures infra consistency.  

**6. Command Healing**  
  Friendly suggestions for ambiguous inputs, with intelligent defaults & provider-specific mappings.  

---

## Quick Start

### Installation

```bash
# Coming soon
brew install cloudbrew   #  Just like you'd expect
cloudbrew create ec2 --autoscale min=1 max=4 cpu=50% type=t3.micro
```

## Provision from YAML

```yaml

infra.yaml

ec2:
type: t3.micro
autoscale:
min: 1
max: 4
cpu: 50%

cloudbrew apply -f infra.yaml
```

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

CloudBrew is designed to gracefully handle the following scenarios:

1. Drift from manual console changes  
2. API rate limits and latency issues  
3. Orchestration partial failures (e.g., half-deployed infra)  
4. Ambiguous inputs in DSL with guided suggestions  
5. State drift and mismatched resource tracking  
6. Provider API feature gaps and abstraction leaks across clouds  
7. Dependency changes in Terraform or Pulumi  
8. Latency under heavy load  
9. Security and credential management failures  
10. Memory inefficiency or runaway resource usage

## Why CloudBrew ?

Infra shouldn’t feel like a chore.
CloudBrew makes the cloud fast, fun, and developer-friendly, while still giving enterprises the control and power they need.
