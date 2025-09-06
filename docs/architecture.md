# CloudBrew — High-Level Design (HLD)

```mermaid
flowchart LR
  %% Top-level clusters & nodes

  subgraph U["1) User Interfaces"]
    ui[CLI / YAML / Interactive prompts / Web UI]
  end

  subgraph C["2) Core Control Plane"]
    core[Parser / Validator<br/>Command Healing Engine<br/>Execution Planner / Orchestrator]
  end

  subgraph S1["3) Command Healing"]
    heal[Defaults / Disambiguation<br/>Auto-corrections / Fallbacks]
  end

  subgraph S2["4) Translation Contract"]
    tc[Provider Schemas / Validators<br/>HCL & Pulumi artifacts<br/>Contract pre-checks]
  end

  subgraph E["Executors & State"]
    native[5]  ("fuck")
   end
endnote