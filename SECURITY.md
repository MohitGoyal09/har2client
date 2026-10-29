# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Email the maintainers at goel0632@gmail.com and bhattacharya.ananya004@gmail.com, or use GitHub's private vulnerability reporting for this repository. Include what you found, how to reproduce it and which commit you tested.

## What is in scope

- **The grader runs model-written code.** It runs `client.py` in a child process with a timeout, a temporary working copy and an import block. It is **not an OS sandbox**. For untrusted models or RL training, run the grader in a container or VM with no network, no `.env` and no task files except `client.py`. See the limitations in the [README](README.md) and section 12a of the [developer guide](har2client/README.md).
- Leaks of hidden tests, seeds or credentials into visible task files (checked by `release/check_no_leak.py`).
- Secrets committed to the repository. The benchmark generates fake credentials for its mock APIs; none of them is a real secret (see `.gitguardian.yaml`). Real keys live only in a local `har2client/.env`, which is git-ignored.

## Supported versions

Only the latest commit on the default branch is supported.
