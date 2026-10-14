# The Council

A multi-agent project review pipeline that runs entirely on your own machine. You give it a project idea; three AI personas analyze it independently, debate each other, and a fourth model turns the whole discussion into a project plan and a ready-to-use project-manager prompt.

Everything runs locally through [Ollama](https://ollama.com). No API keys, no cloud, no data leaves your computer.

## What you get

Each run produces three files in `council_output/`:

| File | Contents |
|---|---|
| `transcript_<name>.md` | The full debate, round by round |
| `readme_<name>.md` | The project backbone: overview, load-bearing decisions, architecture, data flow, tech stack, security posture, risks, open decisions, MVP scope |
| `pm_system_prompt_<name>.txt` | A system prompt for a project-manager model that guides you through building the project step by step |

## How it works

```
Your idea
   |
   v
Round 0: Architect, Critic and AppSec each analyze the idea independently
   |
   v
Debate rounds: each agent reads the other two agents' latest statements,
               then AGREEs, DISAGREEs or REVISEs, and ends with
               CONSENSUS: YES or CONSENSUS: NO
   |
   |  stops early when all three say CONSENSUS: YES,
   |  or when --max-rounds is reached
   v
Synthesizer: reads the full transcript, weighs disagreements,
             writes the backbone document and the PM system prompt
```

### The four models

| Model | Role | Temperature |
|---|---|---|
| `architect` | Systems design: components, data flow, scalability, concrete configs and metrics | 0.8 |
| `critic` | Reliability and risk: realistic failure modes. Praises good decisions and is told not to invent problems | 0.5 |
| `appsec` | Offensive security: kill chains and attack paths ranked by likelihood x impact | 0.6 |
| `synthesizer` | Neutral arbiter: adds no new ideas, only weighs and structures what the others said | 0.6 |

All four are built on `qwen3:14b` and differ only in their system prompt and sampling settings. They are configured, not fine-tuned. The persona definitions are in the `Modelfile.*` files.

### Design notes

- **Stateless calls.** Every call to Ollama is a single-shot prompt rebuilt from scratch. There is no hidden chat history, so any round can be reproduced or debugged in isolation.
- **Latest statement only.** In debate rounds, each agent sees the other agents' most recent statement, not the whole backlog. This keeps the context small. The synthesizer does get the full transcript.
- **Thinking traces are discarded.** Qwen3 reasons in a `<think>` block before answering. `council.py` requests that trace separately and drops it, so reasoning never leaks into the transcript or gets fed to other agents.
- **No retries.** If Ollama fails mid-run the script stops, rather than synthesizing from an incomplete debate.

## Requirements

- Linux, macOS or Windows
- [Ollama](https://ollama.com/download), a recent version (older versions may not support the `think` option)
- Python 3.10 or newer (standard library only, nothing to `pip install`)
- Enough memory for a 14B model at a 32k context window. A GPU with 16 GB VRAM works. On less, lower `num_ctx` in the Modelfiles (see Troubleshooting).

## Setup

### 1. Get the code

```bash
git clone https://github.com/Julian-doty/The-Council.git
cd The-Council
```

### 2. Download the base model

```bash
ollama pull qwen3:14b
```

### 3. Create the four models

```bash
ollama create architect -f Modelfile.architect
ollama create critic -f Modelfile.critic
ollama create appsec -f Modelfile.appsec
ollama create synthesizer -f Modelfile.synthesizer
```

This only applies the configuration on top of the base model, so it takes seconds. Check with `ollama list`: you should see all four.

### 3b. Optional smoke test

```bash
ollama run architect "Analyze this project idea: a local-only CLI tool that converts markdown files to PDF using Python."
```

You should get a structured analysis with Overview, Strengths, Weaknesses, Recommendations and Open Questions sections.

## Usage

Make sure Ollama is running, then:

```bash
python3 council.py "your project idea as a string" --project-name my-project
```

Or put a longer description in a text file:

```bash
python3 council.py --file idea.txt --project-name my-project
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `idea` | none | The project idea as a string |
| `--file PATH` | none | Read the idea from a text file instead |
| `--project-name NAME` | slug of the idea's first words | Used in output filenames |
| `--max-rounds N` | 4 | Maximum debate rounds before forcing synthesis |
| `--out-dir PATH` | `./council_output` | Where to write the three output files |
| `--quiet` | off | Don't print each agent's output live |

### Tips for a good idea description

- Say who it is for and what problem it solves.
- Name any technology you have already decided on, or say you have no preference.
- Mention constraints: local-only, solo developer, deadline, budget, privacy needs.
- A few sentences is enough. A full page is fine.

## Using the PM prompt

The synthesizer's PM system prompt is a first draft. Read it and edit it before use, especially the project path (`[PROJECT_PATH]`). To turn it into a project-manager model, put it inside a Modelfile:

```
FROM qwen3:14b

SYSTEM """
(paste the PM system prompt here)
"""

PARAMETER num_ctx 32768
PARAMETER temperature 0.4
```

Then run `ollama create my-project-pm -f Modelfile.pm`.

## Expect it to be slow

A full run makes 3 calls in Round 0, 3 more per debate round, and 1 for synthesis, all with a 14B model that reasons before it answers. With the default 4 rounds that is up to 16 calls. Expect anywhere from several minutes to over half an hour depending on your hardware. Use `--max-rounds 1` or `2` for a faster first run.

## Troubleshooting

**`Could not reach Ollama at http://localhost:11434`**
Ollama isn't running. Start it (`ollama serve`, or launch the desktop app) and retry.

**`ollama create` says `no Modelfile or safetensors files found`**
You're in the wrong directory, or the path contains spaces and isn't quoted. `cd` into the repo folder and run `ls Modelfile.*` to confirm the files are visible.

**Out of memory, or very slow generation**
The Modelfiles set `PARAMETER num_ctx 32768`. The context cache uses VRAM. Lower it (for example to `16384`) in each `Modelfile.*`, then re-run the `ollama create` commands.

**`ollama pull` fails with EOF or connection errors**
Some networks interrupt pulls from the Ollama registry. Retrying often works. A fallback is to download the GGUF from Hugging Face and reference it with `FROM /path/to/model.gguf` in the Modelfiles.

**Reasoning text appears in the transcript**
Your Ollama version may not support the `think` option. Update Ollama. As a fallback, `council.py` also strips literal `<think>...</think>` blocks from responses.

**The PM prompt file wasn't created**
The synthesizer didn't output the `--- START PM SYSTEM PROMPT ---` and `--- END PM SYSTEM PROMPT ---` markers. The full output is still in `readme_<name>.md`, so you can copy the prompt out by hand. Re-running often fixes it.

## Limitations

- The agents are the same 14B model with different instructions. They can share blind spots, and "consensus" reflects the model agreeing with itself, not independent verification.
- Output quality depends on how clear your idea description is.
- Treat the backbone document and the PM prompt as drafts to review, not final decisions.

## Project layout

```
council.py              orchestration script
Modelfile.architect     persona: systems design
Modelfile.critic        persona: reliability and risk
Modelfile.appsec        persona: offensive security
Modelfile.synthesizer   arbiter: backbone doc + PM prompt
council_output/         generated results (git-ignored)
```
