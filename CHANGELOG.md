# Changelog

## 1.0.0 — 2026-10-18

First release of Radiant Scope, a local Linux network observatory.

- Interface selection with IP/MAC details and automatic active Wi-Fi selection.
- RSSI history, estimated signal quality, channel, frequency, channel width and PHY bitrate.
- RX/TX throughput, packet rate, interface drops and errors from Linux counters.
- TShark packet capture with BPF filters, optional 802.11 monitor mode and explicit errors.
- Searchable packet table, protocol filters, keyboard-accessible packet details.
- Protocol distribution and top source addresses.
- NetworkManager cached Wi-Fi environment and channel distribution.
- Bounded history and buffers; JSON session and CSV packet export.
- Responsive dark UI, live charts, reduced-motion support, persisted settings.
- Explicit synthetic demo mode; no cloud, telemetry or runtime Python dependencies.
