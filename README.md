# regimen

Verification files for code agents.

A regimen file is a markdown document that describes how to verify something works. An agent follows the steps, runs the commands, and judges whether the output matches what the prose describes.

## File format

Each file lives in `.regimen/` and starts with a paragraph describing what it covers, then a sequence of steps.

```markdown
# User authentication

Covers the registration and login endpoints in the API server.
Relates to the auth middleware, user model, and session handling.

## Start the server

```bash
cd /path/to/project && npm start &
```

```bash timeout=10
curl -s --retry 5 --retry-connrefused http://localhost:3000/health
```

Expected: HTTP 200, body contains `{"status": "ok"}`.

## Register a new user

POST to the register endpoint. Should return HTTP 201 with a JSON body containing an `id` field.

```bash
curl -s -X POST http://localhost:3000/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "secret"}'
```

## Bad input returns 400

Sending an empty body should return HTTP 400 with an error message.

```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:3000/register \
  -H "Content-Type: application/json" -d '{}'
```

## Cleanup

```bash
kill %1
```
```

`#` is the title. `##` headings are steps. Prose describes what to check and what output to expect. ```` ```bash ```` blocks are commands. Add `timeout=N` on the fence line for commands that take time, based on how long they actually took with 2-3x headroom.

## Skills

Two Claude Code skills for working with regimen files:

- `/regimen-demonstrate` -- Demonstrate a feature by interacting with the running system, then write a regimen file capturing what you did.
- `/regimen-test` -- Step through a regimen file, run each command, check that output matches expectations.

## Druids

`.druids/judge.py` is a druids program that runs regimen files at scale. It spawns a judge agent per file in parallel on cloud VMs and streams pass/fail verdicts back as they finish.
