# Security

## What Switchyard protects

- **Web interface.** One password, hashed with scrypt, sessions in memory (cookie `HttpOnly`,
  `SameSite=Strict`, `Secure` over HTTPS). Five wrong logins lock login for a minute. Every
  `/api` route except login, session and health needs a session; a test enumerates all routes
  to prove it.
- **Cross-site requests.** Write requests with an `Origin` that differs from `Host` are refused.
- **Browser hardening.** Strict content security policy (no inline script), `X-Frame-Options:
  DENY`, `nosniff`, no referrer. All text from switches and users is inserted as text.
- **Destructive actions.** Reboot, factory reset and firmware upgrade need the switch MAC typed as confirmation. A firmware image is parsed and checksummed before anything is sent, is limited to 16 MB, and is never written to disk by Switchyard.
- **Stored secrets.** `devices.json` and `secrets.json` are mode 0600. A switch password is stored
  only when you tick "remember". Backups contain no passwords. The protocol log blanks
  credentials.
- **Outbound connections.** Switchyard talks to the switches and, only if you set `notify.webhook_url`, POSTs event messages (switch name, MAC, port text) to that URL. Nothing else leaves the machine.
- **Metrics.** `/metrics` needs a web session or the bearer token from `metrics.token`; it reveals MAC addresses, names and port counters.

## What it cannot protect

- **The switch protocol.** Passwords are XORed with a public key or combined by a fixed pattern
  (this class of weakness is CVE-2020-35221). Anyone who can see the traffic on the segment can
  recover or replay them, and several firmware versions had no authentication at all. Keep switch
  management on a trusted VLAN.
- **Anyone with the web password** can reconfigure every switch whose password is stored.
- **`auth.disabled`** removes the login. Use it only behind something that authenticates.

## Recommendations

Use a long web password. Put TLS in front. Do not expose the port to the internet. Do not
remember switch passwords on a shared host.
