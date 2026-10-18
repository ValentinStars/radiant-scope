# Security

The server binds exclusively to 127.0.0.1, validates the Host header and requires a per-process session token for state changes. There is no shell interpolation of interface names or filters, no remote dependency/CDN, and no persistent packet storage. Treat local users and browser extensions as trusted: loopback binding is not an authentication system for a shared host. Do not expose the port through a reverse proxy or tunnel.

TShark receives a 256-byte snapshot length, a one-hour capture limit and an 8 MiB capture buffer. The UI retains 600 samples, 2000 packets and up to 2048 source aggregates. JSON exposes the newest 500 packets; CSV exports the current packet buffer. Protocol counters summarize the current capture. Capture metadata and exports may contain sensitive local network information. CSV fields starting with spreadsheet formula characters are escaped.

Use the distribution's Wireshark/dumpcap privilege configuration. Do not grant capabilities to Python or run the web server as root.

Report vulnerabilities privately using GitHub private vulnerability reporting if available, or contact the repository owner privately. Do not publish real captures or secrets in issues.
