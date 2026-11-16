# Security Policy

## Supported versions

Security fixes are applied to the latest published release and the current `main` branch. Older releases may not receive backported fixes; users should upgrade to the newest available version before reporting a problem.

## Reporting a vulnerability

Please report suspected vulnerabilities through GitHub's private vulnerability reporting workflow:

1. Open the repository's [Security advisories page](https://github.com/KristopherPeck/TheChronicleBinder/security/advisories).
2. Select **Report a vulnerability**.
3. Provide the affected version, operating system, Python version, reproduction steps, expected impact, and any safe proof-of-concept material.

Do not open a public issue, discussion, or pull request for an undisclosed vulnerability. Please avoid including copyrighted story text, private data, credentials, or destructive payloads in a report.

Reports will be reviewed through GitHub. If the issue is confirmed, remediation and coordinated disclosure will be handled in the private advisory before public details are released. Good-faith reports that respect user privacy and avoid unnecessary disruption are welcome.

## Security scope

Relevant reports include, but are not limited to:

- unsafe network requests, redirects, or local-resource access;
- crafted article markup or artwork that causes code execution, data exposure, or resource exhaustion;
- unsafe EPUB content or archive paths;
- unintended file creation or replacement;
- vulnerable runtime dependencies used by the application.

Ordinary extraction errors, formatting differences between ebook readers, inaccessible third-party services, and changes to Halo Waypoint markup are generally reliability issues rather than security vulnerabilities. They may be reported through the normal public issue tracker when no sensitive details are involved.
