# SSH Connector — Adopted Security Standards

This document records the security standards adopted for the SSH Connector project.
All contributors and AI assistants (ChatGPT, Claude Code, Codex, etc.) must follow them.

## 1. SSH key algorithm

| Priority | Algorithm | When to use |
|---|---|---|
| Default | **Ed25519** | All new keys, all systems that support it |
| Fallback | **RSA 4096** | Only for legacy systems without Ed25519 support |
| Not allowed | RSA 1024 / 2048, DSA, ECDSA P-256 for new keys | Considered weak or not recommended |

Ed25519 is supported by OpenSSH (default), GitHub, Debian, Ubuntu, RHEL 8+, Rocky Linux,
Windows OpenSSH and Java 15+.

Generate a key:

```
ssh-keygen -t ed25519 -C "ssh-connector"
```

Fallback:

```
ssh-keygen -t rsa -b 4096 -C "ssh-connector"
```

## 2. Key rotation

- SSH keys and access credentials are rotated every **40–90 days**.
- Old keys are removed from `authorized_keys` after rotation.

## 3. Secrets

- Passwords, private keys, auth keys and tokens are never committed to the repository.
- Secrets are kept only in GitHub Secrets or an approved secret store.
- Secrets are never printed in command output or logs.
- SSH host-key verification always stays enabled.

## 4. Tailscale

- Tailscale (private network to the servers) and the connector tunnel (access from ChatGPT/Claude) are separate components.
- Tailscale commands may show: status, Tailscale version, connected devices, and the account/username the connector connects with.
- Auth keys are never displayed.
- Generating a new auth key and revoking a compromised key are allowed only with explicit user confirmation.
- Tailscale-generated keys use Tailscale's own fixed format; key length is not configurable.
