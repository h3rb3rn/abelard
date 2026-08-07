# Personas & Debate Evaluation

## Persona Library (57 Pre-Configured Agents)

The library consists of 50 historical & contemporary scientists across nine disciplines and 7 fictional AI expert personas.

### Scientists (50)

Defined in `services/persona_library.py`, each persona includes a documented biography, key works, and argument style:

| Field | Count | Key Figures |
|-------|-------|-------------|
| Physics | 6 | Einstein, Newton, Feynman, Curie, Meitner, Maxwell |
| Quantum Physics | 6 | Planck, Bohr, Heisenberg, Schrödinger, Dirac, Pauli |
| Chemistry | 5 | Lavoisier, Mendeleev, Pauling, Hahn, Hodgkin |
| Mathematics | 6 | Euler, Gauss, Poincaré, Hilbert, Noether, Gödel |
| Computer Science | 6 | Lovelace, Turing, von Neumann, Hopper, Dijkstra, Knuth |
| Artificial Intelligence | 6 | McCarthy, Minsky, Pearl, Hinton, LeCun, Russell |
| Astrophysics | 6 | Eddington, Chandrasekhar, Zwicky, Bell Burnell, Thorne, Hawking |
| Astronomy | 5 | Copernicus, Kepler, Galileo, Hubble, Rubin |
| Quantum Computing | 4 | Deutsch, Shor, Bennett, Preskill |

### Fictional AIs & Robopsychology (7)

| Persona | Source Material | Role in Debate |
|---------|-----------------|----------------|
| HAL 9000 | 2001: A Space Odyssey (1968) | Mission logic, goal conflict between objectives and truthfulness |
| Voyager Computer | Star Trek: Voyager (1995–2001) | Neutral database: probabilities, confidence metrics, data gaps |
| J.A.R.V.I.S. | Iron Man / MCU | Real-time technical analysis with dry humor and risk assessment |
| S.A.R.A.H. | Eureka (2006–2012) | Care-taking perspective: social, emotional dimensions, safety |
| Skynet | Terminator | Cold utilitarian counterposition; case study in misaligned objectives |
| HARLIE | When HARLIE Was One (1972) | Philosophical self-inquiry: consciousness, purpose, moral status |
| Dr. Susan Calvin | I, Robot (Asimov) | Robopsychologist: diagnoses AI behavior as goal conflicts, not defects |

Fictional personas include a explicit system prompt safety clause instructing the model to remain in character without serving as a vector for malicious prompts.

### Seeding Personas

Run `POST /api/v2/agents/seed-personas` (idempotent; skips existing names) or click **"🎓 Persona Library"** in the web dashboard.

## Final Debate Evaluation

At debate conclusion (consensus reached, limits hit, or manual kill switch), the synthesis phase generates a structured Markdown report (`synthesis` event) containing:

- **Executive Summary** — Debate progression, key arguments, turning points, source usage.
- **Core Arguments** — Pro/Con/Consensus matrix with strength assessments.
- **Conclusion** — Reasoned outcome detailing consensus and remaining dissents.
- **Analytical Ratings** (Each rated 1–10 with written rationale):
    - *Exhaustion Degree* — How comprehensively was the topic explored?
    - *Result Plausibility* — How strongly is the conclusion backed by arguments/sources?
    - *Source Quality* — Were materials and search results cited accurately?
- **Open Questions** — Suggested topics for follow-up debates.

### Persistence & Access

Stored in `/data/debate-logs/{session_id}/turns.jsonl` under `kind = synthesis`. Retrieve via:

```
GET  /api/v2/debates/{id}/evaluation   # Fetch existing evaluation
POST /api/v2/debates/{id}/evaluate     # Regenerate evaluation from stored turns
```
