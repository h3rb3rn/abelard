# Debate Lifecycle

A debate progresses through three distinct phases: Preparation, Rotation, and Evaluation. The entire lifecycle is encapsulated in `engine/orchestrator.py::run_debate()`.

## Overall Execution Flow

```mermaid
stateDiagram-v2
    [*] --> Preparation
    Preparation --> Rotation: Agents configured
    
    state Rotation {
        [*] --> LimitCheck
        LimitCheck --> ContextBuilding: All limits OK
        LimitCheck --> Evaluation: Limit reached or stopped
        ContextBuilding --> LLMCall
        LLMCall --> RepetitionCheck
        RepetitionCheck --> RetryAttempt: Repetition detected
        RetryAttempt --> Persistence
        RepetitionCheck --> Persistence: Contribution novel
        Persistence --> ModerationCheck: Every N turns
        Persistence --> LoopCheck: Every 2N turns
        ModerationCheck --> LimitCheck
        LoopCheck --> LimitCheck
        Persistence --> LimitCheck
    }

    Rotation --> Evaluation: Limit, consensus, or stop
    Evaluation --> [*]

    note right of LimitCheck
        Time, Rounds, Kill switch, Cost
    end note
    note right of Evaluation
        Runs in finally block —
        even after termination
    end note
```

## Single Turn in Detail

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant V as Valkey
    participant D as DocumentIndex
    participant W as SearXNG
    participant L as LLM
    participant M as Memory
    participant F as JSONL Log

    O->>V: is_active() + get_total_cost()
    V-->>O: Limits OK
    O->>O: Select agent via Round-Robin
    O->>O: _build_context()<br/>Key points history + last 6 turns
    O->>D: search(project_id, Motion + last turn)
    D-->>O: Top-4 material excerpts
    opt Agent has RESEARCHER role
        O->>W: Web search on topic
        W-->>O: Sources with URLs
    end
    O->>L: chat(System Prompt, Context + Materials)
    L-->>O: Contribution text
    O->>O: _detect_repetition() against last 8 turns
    alt Repetition detected
        O->>L: Retry with higher penalties
        L-->>O: New contribution (re-checked)
    end
    O->>V: Record cost + tokens + turn counter
    O->>M: ChromaDB embedding + Neo4j node
    O->>F: JSONL line (kind = turn)
    O-->>O: yield to WebSocket subscribers
```

## Context Construction — Anti-Loop Mechanism

Earlier versions passed only the last two turns to agents. When more than two agents participated, an agent could not even observe its own previous statement — inevitably leading to repetition.

```mermaid
flowchart LR
    A["All past turns"] --> B{"Position?"}
    B -->|"Older than<br/>last 6 turns"| C["Key points history<br/>One sentence per turn"]
    B -->|"Last 6 turns"| D["Full text<br/>max 2000 chars"]
    C --> E["Prompt"]
    D --> E
    F["Moderator corrections<br/>as separate turns"] --> E
    G["Project materials<br/>Top-4 chunks"] --> E
    H["Current focus<br/>from moderator"] --> E
```

Crucially, **moderator corrections are injected as separate turns** into `state.turns`. Previously, corrections were only streamed over WebSockets without reaching the agents — rendering moderation ineffective.

## Moderation

The moderator runs every `interval_turns` contributions and observes the **entire** debate transcript (older turns condensed, last three in full text).

```mermaid
flowchart TD
    A["Moderator Evaluation"] --> B{Status}
    B -->|CONSENSUS| C{"Sufficient turns?<br/>≥ 2× participant count"}
    C -->|Yes| D["End debate<br/>Status SUCCESS"]
    C -->|No| E["Discard message<br/>Debate continues"]
    B -->|CORRECTION| F["Inject correction as turn<br/>into transcript"]
    B -->|CONTINUE| G["direction → current_focus"]
    F --> H["Included in subsequent prompts"]
    G --> H
```

The consensus lock prevents a single agreeable contribution from ending the debate prematurely.

## Loop Detection

Every `2 × interval_turns` contributions, the orchestrator queries the discourse graph in Neo4j to measure how many **distinct** concepts were mentioned in the session:

```mermaid
flowchart LR
    A["Neo4j Query<br/>MATCH DebateTurn -MENTIONS-> Concept"] --> B{"Turns ≥ 6<br/>and<br/>Concepts < 3?"}
    B -->|No| C["Debate healthy"]
    B -->|Yes| D["Set new focus"]
    D --> E["COURSE CORRECTION turn<br/>injected into transcript"]
```

The minimum threshold of six turns is essential: without it, young debates would trigger loop detection before concepts have had time to accumulate.

## Termination Conditions

| Condition | Verification | Result |
|-----------|--------------|--------|
| Time Limit | `max_duration_minutes` | `TERMINATED` |
| Round Limit | `max_rounds` (1 round = each agent once) | `TERMINATED` |
| Kill Switch | Valkey `debate:{id}:status:active` | `TERMINATED` |
| Cost Guardrail | Valkey total cost ≥ `COST_THRESHOLD_USD` | `TERMINATED` |
| Consensus | Moderator reports `CONSENSUS` | `SUCCESS` |

In **all** cases, the final evaluation runs afterwards as it is placed in the `finally` execution block.

## Final Evaluation

```mermaid
flowchart TD
    A["Debate Concluded"] --> B["Compress transcript<br/>max 12,000 chars"]
    A --> C["Session Neo4j Graph"]
    A --> D["ChromaDB Top-10 Highlights"]
    B --> E["Evaluation Prompt"]
    C --> E
    D --> E
    E --> F["LLM Call, 8192 Tokens"]
    F --> G["Markdown Document"]
    G --> H["JSONL kind = synthesis"]
    G --> I["WebSocket Stream"]
```

The output contains a summary, key arguments, conclusion, three analytical ratings (exhaustion degree, plausibility, source quality — each 1–10 with detailed rationale), and open questions.

## Event Persistence

All events are persisted in `{DEBATE_LOG_DIR}/{session_id}/turns.jsonl`:

| `kind` | Description |
|--------|-------------|
| `turn` | Agent speech contribution |
| `moderator` | Moderator evaluation, correction, or course shift |
| `synthesis` | Final evaluation report |
