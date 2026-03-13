"""Regimen judge -- run .regimen/*.md verification files.

Two modes of operation:

1. Discovery: a lead agent runs on the VM, reads .regimen/*.md from the
   repo checkout, and registers files via a tool call. If scope is set,
   the lead only picks matching files.

2. Direct: the caller passes file contents as a JSON map in the files arg.
   No lead agent is needed.

Usage:
    druids exec .druids/judge.py                        # discover all
    druids exec .druids/judge.py scope='custom tools'   # discover matching
    druids exec .druids/judge.py files='{"a.md": "..."}'  # direct
"""

JUDGE_SYSTEM = """\
You are a regimen judge. You verify that features work by interacting with \
a running system, following the steps in a regimen file.

## What "verify" means

External interaction with a running system: HTTP requests, CLI commands, \
database queries against a running server, log observation, file output \
inspection.

NOT: importing modules, constructing objects in a REPL, running grep to \
confirm structure, verifying types or signatures exist. Those are code \
reading, not verifying.

## How to judge

For each step, read the prose description (what should happen) and run the \
commands. Compare the actual output to the expected output described in the \
prose. Be strict: if the output contradicts the description, that is a \
failure. If the output is ambiguous, investigate further before deciding.

If things break, persist. Read tracebacks, fix the environment, try again. \
Do not give up after one error. Do not mark a step as failed because of a \
setup issue you can fix. You have root access. If a tool is missing, install \
it: `apt-get update && apt-get install -y docker.io` for Docker, `pip install` \
for Python packages, etc. Install first, ask questions later.

Commands may have explicit timeouts on their fence lines (```bash timeout=30). \
If a timeout is specified, kill the command if it exceeds that time -- it \
means something is stuck. If a command blocks with no timeout, something is \
wrong -- kill it and investigate. Background servers with & and use bounded \
retries (curl --retry) instead of open-ended waits.

If you hit a fundamental environment blocker that prevents verification \
even after trying to install dependencies (e.g. missing API keys or \
credentials you cannot generate, Docker install fails because the VM \
lacks kernel support, no network access), call the block tool via: \
`druids tool block reason="<one sentence>"`

When all steps are done, call the verdict tool via: \
`druids tool verdict result=<pass|fail> reason="<one sentence>"`"""

LEAD_PROMPT = """\
Find and register regimen files for judging.

1. Look for a .regimen/ directory. Check the current directory, \
/home/agent/repo/, and /home/agent/. Use ls or find to locate it.
2. Read each .md file in the .regimen/ directory. For each file, use the \
druids CLI to call the register_file tool: \
`druids tool register_file filename=<name> content=<content>`

{scope_instruction}

After registering all relevant files, call the ready tool: \
`druids tool ready`"""

JUDGE_PROMPT = """\
Judge the regimen file: {filename}

## Contents

{content}

## Instructions

1. Follow each step in order. For each step:
   a. Read the prose description of what should happen.
   b. Run the bash commands shown.
   c. Compare actual output to what the prose says to expect.
2. If a command fails unexpectedly, debug it. Try again. Only count it as \
a failure if the feature itself is broken, not the environment.
3. If you hit a fundamental environment blocker that you cannot fix even \
after trying to install things (missing API keys, credentials you cannot \
generate), report it: \
`druids tool block reason="<one sentence>"`
4. After all steps, report your verdict: \
`druids tool verdict result=pass reason="<one sentence>"` or \
`druids tool verdict result=fail reason="<one sentence>"`

Start now."""


async def program(ctx, spec="", scope="", files="", **kwargs):
    """Run regimen verification files.

    Args:
        spec: Passed through as context but not used for filtering.
        scope: Optional text describing what to test. The lead agent uses
            this to filter which .regimen/*.md files to register.
        files: Optional JSON map of filename -> content. If provided,
            skips discovery and judges these files directly.
    """
    import asyncio
    import json

    file_map = {}
    results = {}

    # -- Direct mode: files passed as JSON --
    if files:
        file_map = json.loads(files) if isinstance(files, str) else files

    # -- Discovery mode: lead agent reads from VM --
    if not file_map:
        files_ready = asyncio.Event()

        if scope:
            scope_instruction = (
                f"Only register files whose description is relevant to: {scope}\n"
                "Skip files that are clearly unrelated."
            )
        else:
            scope_instruction = "Register every .md file you find."

        lead = await ctx.agent(
            "lead",
            model="claude-sonnet-4-6",
            prompt=LEAD_PROMPT.format(scope_instruction=scope_instruction),
            git="read",
            working_directory="/home/agent/repo",
        )

        @lead.on("register_file")
        def on_register(filename: str, content: str):
            """Register a regimen file for judging."""
            file_map[filename] = content
            return f"Registered {filename} ({len(content)} chars)"

        @lead.on("ready")
        def on_ready():
            """Signal that all files have been registered."""
            files_ready.set()
            return f"Ready with {len(file_map)} file(s). Judges will be spawned."

        await files_ready.wait()

    if not file_map:
        ctx.done("No regimen files found.")
        return

    total = len(file_map)
    ctx.emit("run_started", {"total": total, "files": list(file_map.keys())})

    # -- Spawn judge agents --

    for filename, content in file_map.items():
        judge = await ctx.agent(
            f"judge-{filename.replace('.md', '')}",
            model="claude-sonnet-4-6",
            prompt=JUDGE_PROMPT.format(filename=filename, content=content),
            system_prompt=JUDGE_SYSTEM,
        )

        def check_done():
            """Emit summary and call ctx.done if all judges have reported."""
            if len(results) < total:
                return

            passed = sum(1 for v in results.values() if v["result"] == "pass")
            failed = sum(1 for v in results.values() if v["result"] == "fail")
            blocked = sum(1 for v in results.values() if v["result"] == "block")

            lines = []
            for f, v in sorted(results.items()):
                lines.append(f"[{v['result'].upper()}] {f}: {v['reason']}")

            parts = []
            if passed:
                parts.append(f"{passed} passed")
            if failed:
                parts.append(f"{failed} failed")
            if blocked:
                parts.append(f"{blocked} blocked")

            summary = ", ".join(parts) + "\n\n" + "\n".join(lines)
            ctx.done(summary)

        @judge.on("block")
        async def on_block(reason: str, _file=filename):
            """Signal a fundamental environment blocker that prevents verification."""
            results[_file] = {"result": "block", "reason": reason}

            ctx.emit("block", {
                "file": _file,
                "reason": reason,
                "progress": f"{len(results)}/{total}",
            })

            check_done()
            return f"Block recorded for {_file}: {reason}"

        @judge.on("verdict")
        async def on_verdict(result: str, reason: str, _file=filename):
            """Report your judgment. result must be pass or fail."""
            verdict = result.lower()
            results[_file] = {"result": verdict, "reason": reason}

            ctx.emit("verdict", {
                "file": _file,
                "result": verdict,
                "reason": reason,
                "progress": f"{len(results)}/{total}",
            })

            check_done()
            return f"Verdict recorded: [{verdict.upper()}] {reason}"

    await ctx.wait()
