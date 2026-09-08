# Codex Handoff

This file is a sanitized development handoff for continuing this project from another machine or another Codex session.

Do not treat it as a full chat transcript. The raw conversation may contain router details, subscription URLs, tokens, dashboard secrets, and other operational context that should not be committed to Git.

## Repository

- GitHub repository: `git@github.com:gx1617097814/openclash-region-filter.git`
- Main branch: `main`
- Project purpose: local iStoreOS/OpenClash region filter for Clash/OpenClash YAML subscriptions.

## Current Architecture

The recommended flow is:

1. The filter tool stores the original remote Clash/OpenClash YAML subscription URL in its local runtime config.
2. The tool fetches the original YAML itself.
3. It scans all regions present in the subscription and displays them in the web panel.
4. It applies the saved region enable/disable state.
5. It writes the filtered YAML to the OpenClash local config path.
6. It restarts OpenClash and optionally verifies the running proxy list through the OpenClash/Mihomo Dashboard API.

OpenClash should normally use the generated local config file, not the original remote subscription URL and not the filter tool's `/subscription.yaml` URL directly.

The local subscription endpoint still exists for compatibility:

```text
/subscription.yaml
```

But the current operational preference is to generate a final YAML and let OpenClash run that file.

## Current UX Contract

Region changes must be a two-step workflow at most:

1. Manage source URLs and schedules through subscription cards.
2. Only one subscription is active at a time; inactive refreshes never reload OpenClash.
3. Toggle regions in the active subscription; changes are debounced and applied automatically.
4. Use "全部测速" for latency only, then manually choose a node.

An automatic region save must perform the full required chain:

1. Save settings.
2. Re-filter the last successfully downloaded source YAML from the private cache.
3. Filter by current region settings.
4. Write the generated OpenClash config.
5. Reload OpenClash only when the generated config changed.
6. Verify running state when verification is enabled.

The UI intentionally exposes no separate refresh, reload, install, or verification commands. Those operations are automatic.

## Region State Rules

- Hong Kong is expected to remain disabled by default.
- Existing enabled/disabled region state must persist across refreshes and subscription updates.
- Newly discovered regions should appear in the UI instead of being silently hidden.
- Re-enabling a previously filtered-out region must work. This requires regenerating from the original source subscription, not from the already-filtered output YAML.

## Current UI Notes

- Subscription refresh, activation, and latency test are separate commands. Region toggles and node selection are one local draft committed by one apply action.
- Never activate a newly added subscription during deployment or migration.
- Button feedback is shown as a floating notice and must not reserve blank space beside the buttons.
- Only regions with at least one node in the current source subscription are displayed.
- The lower dashboard area uses a 50/50 split between subscription quota and the latest result on desktop widths.

## Router Deployment

When SSH cannot complete its TCP handshake, use the ttyd WebSocket command client
instead of the LuCI terminal:

```sh
node scripts/router-ttyd-exec.mjs --env .router.env -- "command"
```

The local `.router.env` must remain Git-ignored with mode `600`. Its format is
documented in `.router.env.example`; never print or commit its password. The client
suppresses the login transcript and terminal echo, but callers must still redact any
sensitive command output.

Typical deploy command:

```sh
INSTALL_LUCI_MENU=0 ROUTER_HOST=192.168.2.1 scripts/deploy-to-istoreos.sh
```

This redeploys the filter tool container only. It does not itself change OpenClash routing unless a later API action triggers apply/reload.

For OpenClash-affecting changes, create a 10-minute rollback script first. The user's network depends on OpenClash, so a failed restart can interrupt the session.

## Verification Commands

Run local tests:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall app tests
```

Check router service state:

```sh
ssh root@192.168.2.1 'echo status=$(/etc/init.d/openclash status 2>&1 || true); docker ps --filter name=openclash-region-filter --format "{{.Names}} {{.Status}}"'
```

Check panel availability:

```sh
curl -fsS --max-time 5 http://192.168.2.1:8088/api/state
```

## Security Notes

Never commit:

- Real subscription URLs or subscription tokens.
- OpenClash Dashboard secrets.
- Full runtime config copied from the router.
- Generated Clash/OpenClash YAML files.
- Router backups or rollback snapshots.

No separate MD5 file is needed for this handoff document. Git object IDs already provide integrity for tracked content. If future releases include binary artifacts outside Git, use SHA-256 checksums rather than MD5.

## GitHub Setup Notes

If pushing from a new machine:

```sh
git clone git@github.com:gx1617097814/openclash-region-filter.git
cd openclash-region-filter
python3 -m unittest discover -s tests -v
```

If SSH is not configured on the new machine, add that machine's public key to GitHub first.
