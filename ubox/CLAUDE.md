# CLAUDE.md — UBOX (NixOS flakes mini PC)

> Authoritative spec for the **UBOX** operating layer. One mini PC, NixOS unstable + flakes, AI-operated rolling research box. Read `HANDOVER.md` next for orientation and current state.

## Communication Style
Caveman mode in this doc: strip filler, keep essential words, technical precision. No explanations unless asked.

---

## North Star

One mini PC. NixOS unstable + flakes. AI-operated from day one. Rolling research box for experimenting with AI agents, the MAKER orchestrator, and autonomous research. The entire system is described by a single git-tracked flake. Reflashable from zero in under 30 minutes. Every privileged change is a commit; `git log` is the audit trail.

**Single host.** Not a cluster. Not a self-hosted LLM rig. Not a production service.

---

## What UBOX is NOT

- **Not for self-hosting LLMs.** Heavy AI inference is the Claude API and other cloud providers. Local LLM hosting was explicitly ruled out by the user. Do not propose it. If local AI ever becomes relevant, it is small models (Haiku-class) for parsing/embeddings only, and only after a discrete GPU is added.
- **Not a production service.** If a workload needs to be reliable, it goes to cloud. UBOX is for breaking, learning, iterating.
- **Not a multi-box cluster.** Earlier conversations explored two boxes; that idea is shelved. Single host.
- **Not the same project as `pa-orchestrator`.** The orchestrator is application code that *runs on* UBOX as one workload among many. UBOX is the OS-layer concern. Keep them distinct in docs, scope, and commits.

---

## What UBOX IS

- A NixOS unstable + flakes machine where the entire system is one git-tracked artifact.
- A box where Claude operates with `--dangerously-skip-permissions` from day one, constrained by a sudo allow-list and by physical filesystem layout (experiments go in `/srv/experiments/<name>/`, the host config is read-only at the operating model).
- A rolling target where `flake.lock` is bumped deliberately: bump → dry-build → snapshot → switch → smoke test.
- A box reflashable from zero in under 30 minutes given the flake repo and a USB stick.

---

## Hardware

| Spec | Value | Implication |
|---|---|---|
| CPU | AMD Ryzen 3 4300U — 4C/4T @ 2.7 GHz, Zen 2, AVX2 | Modest. No heavy CPU AI inference. |
| RAM | 16 GB DDR4 (~25 GB/s bandwidth) | zram swap for headroom. No 70B-class models. |
| Disk | ~477 GB SSD | Plenty for declarative system + experiments + snapshots. |
| GPU | Radeon Vega (integrated) | Not used for AI. ROCm officially unsupported on this APU. |
| Network | Ethernet | Tailscale mesh for remote access. |

---

## Operator Model

Claude operates this box from day one with `--dangerously-skip-permissions`. Every privileged action she takes lands as a git commit in the flake repo. Human review is post-hoc via `git log` + journald + auditd, not pre-approval.

| User | Role | Sudo | SSH |
|---|---|---|---|
| `root` | Console-only; disaster recovery | All | Disabled |
| `you` (human) | Repo owner, secret holder, last-resort admin | Full | Key-only |
| `claude` | AI operator from cloud sessions via Tailscale | Allow-list only | Key-only on tailnet |

Claude's sudo is **not** root-equivalent. Allow-list permits:
- `nixos-rebuild` (dry-build, switch, rollback)
- `systemctl` for project services
- `journalctl`, `auditctl -l`
- `btrfs snapshot create/delete` within experiment subvols
- `podman`, `distrobox`

Denies:
- Filesystem-wide destructive ops (`rm -rf`, `mkfs`, `dd`)
- User management (`useradd`, `userdel`, `passwd` of other users)
- Firewall edits outside the declarative nftables module
- Kernel module ops
- Disk operations outside experiment subvols

Full allow-list lives in `modules/claude-user.nix` (TBD).

---

## Operating Philosophy

Three rules that override defaults:

1. **The flake is the system.** Anything not in the flake repo doesn't exist as far as the operating model is concerned. Imperative changes are throwaway by definition — they don't survive a rebuild and they don't get audited.
2. **Blast radius is bounded.** AI experiments live in `/srv/experiments/<name>/` — own btrfs subvol, own snapshot policy, distrobox containers for anything that wants to `apt install`. The host config is declarative; the experiment area is the playground.
3. **Git is the audit log.** Every change Claude makes lands as a commit in the flake repo. `git log` reads as the system's change history. `git revert` is a real rollback path that complements generation rollback.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Distribution | NixOS unstable | Rolling + declarative + generation-level rollback |
| Config style | Flakes + `flake.lock` | Pinned reproducibility; rolling on the user's schedule |
| Disk layout | `disko` (declarative) | Reinstall = one command |
| Filesystem | btrfs + subvolumes (`@`, `@home`, `@nix`, `@var`, `@experiments`, `@snapshots`) | Subvol-scoped snapshots; cheap rollback |
| Bootloader | systemd-boot | NixOS generations appear as boot menu entries |
| Secrets | `sops-nix` | Age-encrypted; safe to commit; runtime-decrypted |
| Remote access | Tailscale | No public SSH; mesh-authenticated from Claude's cloud sessions |
| Public ingress | cloudflared (only if a workload needs it) | One specific tunnel, declarative |
| Sandboxing | distrobox + podman (rootless), `nix develop` shells | Per-experiment isolation; host stays clean |
| Swap | zram | Effective ~24 GB headroom on 16 GB physical |
| Observability | journald (persistent, 1 GB cap), auditd, netdata bound to tailnet | Post-hoc review of Claude's actions |
| Backup | restic to external target | Daily, encrypted, deduplicated |
| Dotfiles | home-manager (integrated into the flake) | Declarative per-user state |

---

## Repo Structure (target)

```
ubox/                              ← this directory
├── CLAUDE.md                      ← this file (authoritative spec)
├── HANDOVER.md                    ← orientation for next agent
├── STATUS.md                      ← build sequence tracker [TODO]
├── flake.nix                      ← THE declarative system [TODO]
├── flake.lock                     ← rolling, pinned [TODO]
├── disko.nix                      ← disk layout [TODO]
├── hosts/
│   └── ubox/                      ← the single-host config [TODO]
│       ├── default.nix
│       ├── hardware.nix
│       └── services.nix
├── modules/                       ← reusable NixOS modules [TODO]
│   ├── claude-user.nix            ← user + sudo allow-list
│   ├── experiments.nix            ← /srv/experiments subvol + snapshot policy
│   ├── observability.nix          ← auditd, journald, netdata
│   ├── network.nix                ← tailscale, nftables
│   └── secrets.nix                ← sops-nix wiring
├── secrets/                       ← sops-encrypted, safe to commit [TODO]
│   ├── secrets.yaml
│   └── .sops.yaml
├── runbooks/                      ← how-to procedures [TODO]
├── reference/                     ← specs & contracts [TODO]
├── decisions/                     ← ADRs [TODO]
└── .claude/commands/              ← invokable skills [TODO]
```

The repo may later move to a dedicated `pa-machine` (or `ubox`) repo separate from `pa-orchestrator`. For now it lives here on the `UBOX` branch.

---

## Build Coordination

Same pattern as the orchestrator: read `STATUS.md` (TBD) before any work. Each build step is `todo | in_progress | done`. Claim a step before touching it; close it before ending the session. No two agents touch the same step at the same time.

---

## Change Workflow

Every system change follows this flow. Skipping steps is a Do Not.

1. Edit the flake repo in a working branch.
2. `git commit` — every change is a commit, no exceptions.
3. `nixos-rebuild dry-build --flake .#ubox` — pre-flight; catches eval errors and missing deps.
4. `btrfs subvolume snapshot -r / /.snapshots/<timestamp>-pre-switch` — manual rollback safety net.
5. `nixos-rebuild switch --flake .#ubox` — apply.
6. Smoke test: SSH still works, Tailscale up, critical services running.
7. If fail: `nixos-rebuild --rollback` (last generation) or boot a prior generation from systemd-boot, or restore the btrfs snapshot.
8. `git push` once stable.

---

## Recovery

Three layers, fastest first:

| Layer | When to use | Time |
|---|---|---|
| `nixos-rebuild --rollback` | Last rebuild broke something | <1 min |
| Boot prior generation from systemd-boot menu | System won't boot or rollback isn't enough | <5 min |
| Restore btrfs snapshot from `/.snapshots/` | Filesystem-level corruption | <10 min |
| Reflash from USB + flake repo (disko + nixos-install) | SSD failure or unrecoverable state | <30 min |

The 30-minute reflash target is load-bearing. Any change that breaks it needs explicit user sign-off.

---

## Security Rules

- SSH: key-only; no passwords; public SSH disabled. Tailscale is the only management plane.
- `root`: disabled for SSH; console-only.
- `claude` user: scoped sudo (allow-list above); no shell-as-root.
- Secrets: never in plaintext in the flake repo. Use sops-nix. Age key derived from the host SSH key.
- Firewall: nftables, declarative, deny-by-default inbound except on `tailscale0`.
- Audit: auditd enabled with rules logging every sudo command; journald persistent and capped at 1 GB.
- No public ingress unless explicitly added via cloudflared module for a specific workload.

---

## Do Not

- Do not self-host frontier LLMs on this box. Heavy AI = cloud API. Period.
- Do not edit `/etc/nixos/` directly. Edit the flake repo, commit, `nixos-rebuild switch --flake .#ubox`.
- Do not run imperative package installs on the host (no `nix-env -i`, no `nix profile install --priority` on the system user). Add to the flake.
- Do not commit unencrypted secrets to the flake repo.
- Do not disable auditd, journald-persistent, or fail2ban.
- Do not change firewall rules outside the declarative nftables module.
- Do not grant the `claude` user sudo beyond the allow-list in `modules/claude-user.nix`.
- Do not `nixos-rebuild switch` without: `git commit` first, `dry-build` first, btrfs snapshot first.
- Do not delete NixOS generations newer than 30 days.
- Do not boot a custom or non-NixOS-supplied kernel without keeping an A/B fallback generation.
- Do not run experiments outside `/srv/experiments/<name>/`. If something must touch the host, it's a flake change.
- Do not conflate UBOX with `pa-orchestrator`. The orchestrator is *deployed on* UBOX. The two repos serve different concerns.
- Do not propose a second box, an HA pair, or distributed inference. Single host. If that constraint needs to change, ask the user first.
- Do not skip the change workflow (commit → dry-build → snapshot → switch → smoke → push) to "just try something quickly." That shortcut is what destroys the audit trail.

---

## Cross-References

- Orientation for next agent: `ubox/HANDOVER.md`
- Build sequence: `ubox/STATUS.md` (TBD)
- Flake structure: `ubox/reference/flake-structure.md` (TBD)
- Disk layout: `ubox/reference/disko-layout.md` (TBD)
- User/sudo model: `ubox/reference/user-and-sudo.md` (TBD)
- Bootstrap runbook: `ubox/runbooks/bootstrap.md` (TBD)
- Rebuild runbook: `ubox/runbooks/rebuild.md` (TBD)
- Recovery runbook: `ubox/runbooks/recover-from-zero.md` (TBD)
- Decision records: `ubox/decisions/` (TBD)
