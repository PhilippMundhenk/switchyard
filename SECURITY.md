# Security

Report vulnerabilities privately through GitHub: *Security* tab, *Report a vulnerability*. Do not
open a public issue. Expect a first answer within a week.

Read [docs/security.md](docs/security.md) for the threat model. Key points for operators:

- Run Switchyard only on a network you trust. The switch protocol itself is weak: passwords
  travel obfuscated, not encrypted, and can be recovered by anyone on the same segment.
- Put the web interface behind TLS (reverse proxy) when you reach it over anything but a trusted LAN.
- `devices.json` can hold switch passwords. Protect the data folder.
