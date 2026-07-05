# DriveProof Enterprise Server

The DriveProof live image remains open source. The Enterprise Server, Management
Portal, and License Server are planned as closed-source commercial components.

This public document describes the integration boundary only. Server source code,
licensing logic, deployment automation, and internal data models should live in
private repositories.

DriveProof Live should remain useful without any Enterprise dependency. The
Enterprise product line is intended for workshops, refurbishers, IT asset
disposition teams, and service providers that need central control, identity
management, fleet visibility, tamper-evident archives, and licensed commercial
support.

## Product Split

- `DriveProof Live`: open-source NixOS live image and local web UI.
- `DriveProof Enterprise Server`: closed-source central management portal.
- `DriveProof License Server`: closed-source licensing, entitlement, activation,
  and subscription enforcement service.

## Enterprise Server Scope

The Enterprise Server should provide:

- discovery of running DriveProof live instances through authenticated heartbeats
- central dashboard with online/offline status, disk inventory, and active jobs
- remote command queue for safe actions
- central report and certificate archive
- server-side verification of signed DriveProof bundles
- append-only server audit log
- OIDC login
- LDAP/Active Directory integration
- role-based access control
- tenant/customer branding
- API access for report export and external asset systems

## License Server Scope

The License Server should provide:

- customer accounts and subscriptions
- offline-capable license files for disconnected environments
- per-feature entitlements
- optional per-drive or per-erasure counters
- activation and revocation
- signed license tokens
- license audit logs

Recommended entitlement flags:

- `enterprise.portal`
- `enterprise.remote-control`
- `enterprise.oidc`
- `enterprise.ldap`
- `enterprise.branding`
- `enterprise.api`
- `erase.remote`
- `erase.nvme-sanitize`
- `reports.cloud-archive`

## Live Client Integration

The open-source live image can integrate with the closed-source portal when
configured with environment variables:

```text
DRIVEPROOF_PORTAL_URL=https://portal.example.com
DRIVEPROOF_PORTAL_TOKEN=<enrollment-or-device-token>
DRIVEPROOF_INSTANCE_NAME=workbench-01
DRIVEPROOF_PORTAL_DISCOVERY_HOSTS=http://driveproof-portal.local:6060,http://driveproof-portal:6060
```

The live client should only initiate outbound connections to the portal. This
avoids requiring inbound firewall access to temporary live-boot machines.

Standalone behavior:

- the live system uses DHCP by default
- if no licensed Enterprise Server is discovered, Enterprise features remain
  disabled automatically
- local testing, erasing, PDF reports, export bundles, and certificates continue
  to work without Enterprise
- network configuration controls are only shown when a licensed Enterprise
  Server is discovered

## Enterprise Discovery Contract

The closed-source Enterprise Server must not advertise itself unless it has a
valid license. The live client probes `/api/v1/discovery` on the configured URL
and on the discovery host list. A server is accepted only when the response
matches this contract:

```json
{
  "product": "DriveProof Enterprise Server",
  "enterprise_enabled": true,
  "advertise": true,
  "license": {
    "valid": true,
    "id": "license-or-subscription-id"
  }
}
```

If the license is missing, expired, invalid, or the server responds with
`"advertise": false`, the live client treats it the same as no Enterprise Server
being present.

Initial safe remote commands:

- `refresh`
- `start_test`
- `safe_remove`
- `export_report`

Remote destructive erase commands should remain disabled until device identity,
operator authorization, approval rules, and audit policies are implemented.

## Secure Connectivity

Minimum viable deployment:

- HTTPS reverse proxy in front of the portal
- long random enrollment/device token
- firewall-restricted portal access
- destructive remote commands disabled

Enterprise target:

- OIDC user login
- LDAP/Active Directory group mapping
- mTLS or device certificates for live instances
- per-instance enrollment tokens
- signed command payloads
- approval workflow for destructive commands
- complete server-side audit trail

## Tamper-Evident Storage

The FAT32 export partition is only a transport medium. It is not trusted.

Trust should come from:

- Ed25519-signed certificates
- SHA-256 bundle manifests
- signed `manifest.sig`
- server-side verification on ingest
- append-only server audit events
- immutable or WORM-style object storage for accepted bundles

## Competitor Context

Blancco offers central management/reporting through Management Portal and
Management Portal On-Premise, including report/certificate management and
enterprise identity integrations such as Active Directory/LDAP or SAML depending
on product variant.

KillDisk Industrial documents configurable report locations, including mapped
network resources, and certificate/report workflows.

DriveProof Enterprise should compete in that category while keeping the live
diagnostic/erasure client open source.
