"""Regimen judge program -- run verification files with agent judges.

Takes regimen file contents as args, spawns a judge agent per file,
streams results back via events as each finishes.

Usage:
    druids exec .druids/judge.py files='{"api.md": "# API\\n## Step 1\\n..."}'
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
setup issue you can fix.

Commands may have explicit timeouts on their fence lines (```bash timeout=30). \
If a timeout is specified, kill the command if it exceeds that time -- it \
means something is stuck. If a command blocks with no timeout, something is \
wrong -- kill it and investigate. Background servers with & and use bounded \
retries (curl --retry) instead of open-ended waits.

When all steps are done, call the verdict tool with your result."""

JUDGE_PROMPT = """\
Judge the regimen file: {filename}

## Contents

{content}

## Instructions

1. Save the file contents above to a local file.
2. Follow each step in order. For each step:
   a. Read the prose description of what should happen.
   b. Run the bash commands shown.
   c. Compare actual output to what the prose says to expect.
3. If a command fails unexpectedly, debug it. Try again. Only count it as \
a failure if the feature itself is broken, not the environment.
4. After all steps, call the `verdict` tool:
   - result="pass" if all steps produced expected output
   - result="fail" if any step shows the feature is broken
   - result="na" if you cannot determine (environment issues you cannot fix)
   - reason: one sentence explaining your judgment

Start now."""


async def program(ctx, files="{}"):
    """Run regimen files with parallel judge agents.

    Args:
        files: JSON object mapping filename to file content.
    """
    import json
    file_map = json.loads(files)

    if not file_map:
        ctx.done("No regimen files provided.")
        return

    total = len(file_map)
    results = {}

    ctx.emit("run_started", {"total": total, "files": list(file_map.keys())})

    for filename, content in file_map.items():
        judge = await ctx.agent(
            f"judge-{filename.replace('.md', '')}",
            prompt=JUDGE_PROMPT.format(
                filename=filename,
                content=content,
            ),
            system_prompt=JUDGE_SYSTEM,
        )

        @judge.on("verdict")
        async def on_verdict(result: str, reason: str, _file=filename):
            """Report your judgment. result must be pass, fail, or na."""
            verdict = result.lower()
            results[_file] = {"result": verdict, "reason": reason}

            ctx.emit("verdict", {
                "file": _file,
                "result": verdict,
                "reason": reason,
                "progress": f"{len(results)}/{total}",
            })

            if len(results) == total:
                passed = sum(1 for v in results.values() if v["result"] == "pass")
                failed = sum(1 for v in results.values() if v["result"] == "fail")
                na = sum(1 for v in results.values() if v["result"] == "na")

                lines = []
                for f, v in sorted(results.items()):
                    lines.append(f"[{v['result'].upper()}] {f}: {v['reason']}")

                parts = []
                if passed:
                    parts.append(f"{passed} passed")
                if failed:
                    parts.append(f"{failed} failed")
                if na:
                    parts.append(f"{na} na")

                summary = ", ".join(parts) + "\n\n" + "\n".join(lines)
                ctx.done(summary)

            return f"Verdict recorded: [{verdict.upper()}] {reason}"
