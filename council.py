#!/usr/bin/env python3
"""
The Council — orchestrates a debate between three Ollama personas
(architect, critic, appsec) and hands the transcript to a synthesizer
that produces a project backbone document and a PM system prompt.

Usage:
    python council.py "your project idea as a string"
    python council.py --file idea.txt
    python council.py "idea text" --project-name calorie-calendar --max-rounds 3

Requires: Ollama running locally with the four models already created
(architect, critic, appsec, synthesizer). See the Modelfiles for setup.
"""

import argparse
import datetime
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_HOST = "http://localhost:11434"
GENERATE_ENDPOINT = f"{OLLAMA_HOST}/api/generate"

AGENTS = ["architect", "critic", "appsec"]
AGENT_LABELS = {
    "architect": "The Architect",
    "critic": "The Critic",
    "appsec": "AppSec",
}

DEFAULT_MAX_ROUNDS = 4
CONSENSUS_MARKER = "CONSENSUS: YES"

PM_START_MARKER = "--- START PM SYSTEM PROMPT ---"
PM_END_MARKER = "--- END PM SYSTEM PROMPT ---"


THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def call_model(model: str, prompt: str, temperature: float | None = None) -> str:
    """
    Call an Ollama model with a single-shot prompt. Returns only the final
    answer text — the model's <think> reasoning trace is requested as a
    separate field (via "think": true) and discarded here, so it never
    leaks into the transcript or gets fed into later rounds/synthesis.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": True,
    }
    if temperature is not None:
        payload["options"] = {"temperature": temperature}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GENERATE_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_HOST}. "
            f"Is it running? ({e})"
        ) from e

    answer = body.get("response", "").strip()
    # Belt-and-suspenders: if this Ollama version doesn't honor "think" and
    # still inlines a <think>...</think> block into "response", strip it.
    answer = THINK_BLOCK_RE.sub("", answer).strip()
    return answer


def build_round0_prompt(idea: str) -> str:
    return (
        "PROJECT IDEA FOR REVIEW:\n\n"
        f"{idea}\n\n"
        "Provide your Round 0 initial analysis, following the output "
        "structure defined in your system prompt."
    )


def build_debate_prompt(idea: str, round_num: int, own_label: str,
                         history: list[dict[str, str]]) -> str:
    """
    Build the prompt for a debate round: the original idea, plus every
    other agent's most recent statement (their latest round output).
    """
    parts = [
        "PROJECT IDEA UNDER REVIEW (for reference):\n",
        f"{idea}\n",
        f"\n--- DEBATE ROUND {round_num} ---\n",
        "Below are the other agents' most recent analyses. Respond per "
        "your debate protocol: AGREE / DISAGREE / REVISE on their points, "
        "raise any new concerns, then give your CURRENT POSITION and "
        "CONSENSUS line.\n",
    ]
    for entry in history:
        if entry["agent_label"] == own_label:
            continue
        parts.append(f"\n### {entry['agent_label']} said:\n{entry['text']}\n")
    return "".join(parts)


def agent_reached_consensus(text: str) -> bool:
    return CONSENSUS_MARKER in text.upper().replace("consensus: yes", CONSENSUS_MARKER.lower())\
        or CONSENSUS_MARKER in text


def run_council(idea: str, max_rounds: int, verbose: bool = True) -> list[dict]:
    """
    Runs Round 0 + debate rounds. Returns the full transcript as a list of
    {round, agent, agent_label, text} dicts in chronological order.
    """
    transcript: list[dict] = []

    if verbose:
        print("=== ROUND 0: Initial analyses ===\n")

    round0_prompt = build_round0_prompt(idea)
    latest_by_agent: dict[str, str] = {}

    for agent in AGENTS:
        if verbose:
            print(f"-- {AGENT_LABELS[agent]} thinking... --")
        text = call_model(agent, round0_prompt)
        latest_by_agent[agent] = text
        transcript.append({
            "round": 0,
            "agent": agent,
            "agent_label": AGENT_LABELS[agent],
            "text": text,
        })
        if verbose:
            print(f"\n### {AGENT_LABELS[agent]} (Round 0) ###\n{text}\n")

    round_num = 1
    while round_num <= max_rounds:
        if verbose:
            print(f"=== DEBATE ROUND {round_num} ===\n")

        # Build the "most recent statement per agent" view for prompting
        recent_entries = [
            {"agent_label": AGENT_LABELS[a], "text": latest_by_agent[a]}
            for a in AGENTS
        ]

        round_outputs: dict[str, str] = {}
        for agent in AGENTS:
            prompt = build_debate_prompt(
                idea, round_num, AGENT_LABELS[agent], recent_entries
            )
            if verbose:
                print(f"-- {AGENT_LABELS[agent]} thinking... --")
            text = call_model(agent, prompt)
            round_outputs[agent] = text
            transcript.append({
                "round": round_num,
                "agent": agent,
                "agent_label": AGENT_LABELS[agent],
                "text": text,
            })
            if verbose:
                print(f"\n### {AGENT_LABELS[agent]} (Round {round_num}) ###\n{text}\n")

        latest_by_agent = round_outputs

        if all(agent_reached_consensus(t) for t in round_outputs.values()):
            if verbose:
                print(f">>> Consensus reached after round {round_num}. <<<\n")
            break

        round_num += 1
    else:
        if verbose:
            print(f">>> Max rounds ({max_rounds}) reached without full consensus. <<<\n")

    return transcript


def format_transcript_markdown(transcript: list[dict], idea: str) -> str:
    lines = [
        "# Council Debate Transcript",
        f"\nGenerated: {datetime.datetime.now().isoformat(timespec='seconds')}\n",
        "## Project Idea\n",
        f"{idea}\n",
        "## Transcript\n",
    ]
    current_round = None
    for entry in transcript:
        if entry["round"] != current_round:
            current_round = entry["round"]
            label = "Round 0 — Initial Analysis" if current_round == 0 else f"Debate Round {current_round}"
            lines.append(f"\n### {label}\n")
        lines.append(f"\n#### {entry['agent_label']}\n\n{entry['text']}\n")
    return "\n".join(lines)


def build_synthesizer_prompt(idea: str, transcript: list[dict]) -> str:
    parts = [
        "PROJECT IDEA:\n\n",
        f"{idea}\n\n",
        "FULL COUNCIL DEBATE TRANSCRIPT:\n",
    ]
    current_round = None
    for entry in transcript:
        if entry["round"] != current_round:
            current_round = entry["round"]
            label = "ROUND 0 (Initial Analysis)" if current_round == 0 else f"DEBATE ROUND {current_round}"
            parts.append(f"\n=== {label} ===\n")
        parts.append(f"\n--- {entry['agent_label']} ---\n{entry['text']}\n")

    parts.append(
        "\n\nProduce the two deliverables as defined in your system prompt: "
        "the project backbone document, then the PM system prompt."
    )
    return "".join(parts)


def split_synthesizer_output(text: str) -> tuple[str, str | None]:
    """Splits synthesizer output into (backbone_markdown, pm_prompt_or_None)."""
    if PM_START_MARKER in text and PM_END_MARKER in text:
        backbone = text.split(PM_START_MARKER)[0].strip()
        pm_prompt = text.split(PM_START_MARKER)[1].split(PM_END_MARKER)[0].strip()
        return backbone, pm_prompt
    return text.strip(), None


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


def main():
    parser = argparse.ArgumentParser(description="Run The Council on a project idea.")
    parser.add_argument("idea", nargs="?", help="Project idea as a string.")
    parser.add_argument("--file", type=Path, help="Read the idea from a text file instead.")
    parser.add_argument("--project-name", default=None,
                         help="Used for output filenames. Defaults to a slug of the idea's first words.")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                         help=f"Max debate rounds before forcing synthesis (default {DEFAULT_MAX_ROUNDS}).")
    parser.add_argument("--out-dir", type=Path, default=Path("./council_output"),
                         help="Directory to write outputs to.")
    parser.add_argument("--quiet", action="store_true", help="Suppress live round-by-round printing.")
    args = parser.parse_args()

    if args.file:
        idea = args.file.read_text(encoding="utf-8").strip()
    elif args.idea:
        idea = args.idea.strip()
    else:
        parser.error("Provide an idea as an argument or via --file.")
        return

    project_name = args.project_name or slugify(" ".join(idea.split()[:5]))
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    verbose = not args.quiet

    transcript = run_council(idea, args.max_rounds, verbose=verbose)

    transcript_md = format_transcript_markdown(transcript, idea)
    transcript_path = out_dir / f"transcript_{project_name}.md"
    transcript_path.write_text(transcript_md, encoding="utf-8")

    if verbose:
        print("=== SYNTHESIS ===\n")
        print("-- Synthesizer weighing the debate... --")

    synth_prompt = build_synthesizer_prompt(idea, transcript)
    synth_output = call_model("synthesizer", synth_prompt)

    backbone_md, pm_prompt = split_synthesizer_output(synth_output)

    backbone_path = out_dir / f"readme_{project_name}.md"
    backbone_path.write_text(backbone_md, encoding="utf-8")

    if pm_prompt:
        pm_path = out_dir / f"pm_system_prompt_{project_name}.txt"
        pm_path.write_text(pm_prompt, encoding="utf-8")
    else:
        pm_path = None
        print(
            "WARNING: Could not find PM system prompt markers in the "
            "synthesizer output. Check the raw output below and in the "
            "backbone file.",
            file=sys.stderr,
        )

    print("\n=== DONE ===")
    print(f"Transcript:  {transcript_path}")
    print(f"Backbone:    {backbone_path}")
    if pm_path:
        print(f"PM prompt:   {pm_path}")


if __name__ == "__main__":
    main()
