# Contributing

Use Python 3.10+ on Linux. No runtime dependencies are required.

```sh
python3 -m radiant_scope --demo
python3 -m unittest discover -s tests -v
node --check radiant_scope/web/app.js
```

Keep changes focused. Explain the observed problem and validation in your pull request. Include desktop and narrow-screen screenshots for interface changes. Never commit captures, network addresses from your environment, tokens, or session exports. Use RFC 5737 documentation addresses in fixtures.

Real capture validation needs TShark and a correctly configured dumpcap. Run the application as an ordinary user; avoid making Python privileged. Monitor-mode tests should use a spare adapter.
