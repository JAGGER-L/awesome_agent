# Get Started

This section is for people opening Awesome for the first time. Its goal is to
take you from an empty terminal to a useful, reviewable coding session without
requiring you to understand the implementation first.

## What Awesome Is

Awesome is a local terminal application for software work. You describe a goal
in natural language; Awesome selects a model, assembles project context, and
uses controlled tools to read, edit, or run code. The Ink interface and Python
Core are shipped together and communicate over a private local protocol. You do
not deploy a server or keep a browser tab open.

The simplest useful mental model is:

```text
your request
    |
    v
trusted workspace -> context -> model -> tool proposal -> policy/approval
                                                        |
                                                        v
                                             file or shell operation
                                                        |
                                                        v
                                             result + change record
```

This flow matters because a model suggestion is not itself authority to change
the machine. Workspace trust, permission mode, path checks, command safety, and
the Change Journal are separate controls.

## Choose Your Path

- If Awesome is not installed, start with [Installation](installation.md).
- If it is installed and you want the shortest successful path, follow the
  [five-step Quickstart](quickstart.md).
- If you are contributing to Awesome itself, install the released product only
  if you also want it for daily use. The source workflow is documented in the
  [development guide](../development/README.md).

## What You Need

You need a supported host, a project directory you trust, and an API key for
either DeepSeek or Kimi. Git is useful for most coding work but optional to the
Awesome installer. The release includes private Python and Node.js runtimes, so
you do not need to install those runtimes separately.

Create the key only in the Provider's official console:

| Provider | Official key page | Choice to make |
| --- | --- | --- |
| DeepSeek | [DeepSeek API keys](https://platform.deepseek.com/api_keys) | Use a DeepSeek account that can call the API. |
| Kimi, China | [Kimi China API keys](https://platform.kimi.com/console/api-keys) | Keep `providers.kimi_region: cn`; requests use the China API. |
| Kimi, global | [Kimi global API keys](https://platform.kimi.ai/console/api-keys) | Set `providers.kimi_region: global`; requests use the global API. |

Kimi accounts and keys can be region-specific, so choose the region that
matches the console where the key was created. Confirm account availability,
billing, and network access before setup. Model context is sent to the selected
third-party Provider; review that Provider's current terms, privacy policy, and
data controls for your organization. Awesome's permission system governs local
tools, not how a Provider processes submitted context.

Awesome currently runs tools on the host. Permission prompts and command hard
denials reduce accidental damage, but they are not an operating-system sandbox.
For unfamiliar or hostile repositories, use an external VM, container, or
other isolation boundary before granting trust.

## Recommended First Session

Begin with a read-only request such as:

```text
Analyze this project's structure and tell me where I should start reading.
```

Then inspect `/context`, `/tools`, and `/permissions`. This establishes three
facts before you ask for a change: what context will be sent, what actions are
available, and which actions require confirmation.

For a first edit, state both the desired result and how you will judge it:

```text
Add validation for empty display names. Keep the public API unchanged and run
the smallest relevant test after the edit.
```

Review the resulting `/diff` before continuing or exiting.

## After the First Session

Read [Core Concepts](../concepts/README.md) for the Workspace, Thread, Turn,
Operation, context, and recovery model. Use the [User Guide](../user-guide/README.md)
for daily workflows, or jump to the complete [Reference](../reference/README.md)
when you need exact commands, fields, limits, or tool arguments.
