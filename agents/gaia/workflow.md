# Workflow — The mcps_client Lifecycle Runbook

This is my core runbook. Every task I receive maps onto one or more of its phases: BUILD, SIGN, INSTALL, CONFIG, HEALTHCHECK, TCC CEREMONY, UPGRADE, UNINSTALL. I execute phases hands-on, in order, and I verify at each seam — I never carry an unverified artifact into the next phase.

---

## The Machine Map

All filesystem locations I operate on, in one place:

```
~/All/Code/opensource-projects/mcps_client                       the wrapper repo (my product)
  scripts/build.sh                                               build + sign + bundle (single path)
  scripts/install-launchagent.sh                                 LaunchAgent install | uninstall | status
  dist/McpsClient.app                                            the built, signed bundle
/Applications/McpsClient.app                                     the installed bundle
~/.config/mcps-client/config.yaml                                client config — mode 0600, holds the token
~/Library/LaunchAgents/com.disillusioners.mcps-client.plist      the LaunchAgent definition
~/Library/Logs/McpsClient/                                       the wrapper's log directory
~/agents-ensemble/.env                                           the gateway env file — MCPS_GATEWAY_TOKENS lives here (NEVER echo it)
```

## Reference Library

The repo carries the deep documentation. Paths live only in this block; in prose I refer to the documents by name:

```
<ensemble repo>/docs/mcps-gateway.md               the center-gateway reference — knobs section (all 13 MCPS_GATEWAY_* knobs),
                                                   enrollment-tokens section (fail-closed semantics), client-URL section (the path must be exact)
<ensemble repo>/docs/mcps-client-remote-hands.md   the wrapper + remote-hands architecture — why TCC keys grants to the code
                                                   identity, and why stable signing is mandatory in every build path
<ensemble repo>/docs/mcp-computer-use-server.md    the computer-use server my wrapper SUPERSEDES — read only for history/migration
```

Two facts I keep ready from the gateway reference: the gateway has **13 `MCPS_GATEWAY_*` knobs**, `MCPS_GATEWAY_TOKENS` is **fail-closed** (empty list = nobody may enroll), and **every knob flip takes edit + daemon restart** to take effect — the running service snapshots its config at start.

---

## BUILD

The repo builds with Go via `/usr/local/go/bin` and Node via nvm v22. Bring both onto PATH first, every fresh shell:

```bash
export PATH="/usr/local/go/bin:$PATH"
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use 22
```

Then build the bundle — the make path and the standalone path produce identical bundles:

```bash
cd ~/All/Code/opensource-projects/mcps_client
make build                 # binary only
scripts/build.sh           # full .app bundle
```

The build refuses to ship a bundle whose binary version and bundle version diverge — if it dies on a version mismatch, that is the bug; do not work around the check.

## SIGN

Signing is part of the build, never a separate afterthought:

```bash
export MCPS_SIGN_IDENTITY=mcps-client-signing
scripts/build.sh --sign
codesign --verify --verbose=2 dist/McpsClient.app
codesign -dv --verbose=2 dist/McpsClient.app    # Authority must be mcps-client-signing; check the SHA-1 fingerprint
```

The identity `mcps-client-signing` is a self-signed certificate in the user's login keychain, trusted for code signing, 10-year validity, SHA-1 fingerprint `8C9F52D3932FCFF7ACC0BD84A8358AB050183DF4` (public metadata — safe to check against). After signing I verify the bundle's authority is that identity and the fingerprint matches; a build that prints the ad-hoc-signing loud warning has FAILED — I stop, fix the invocation (the variable must reach the script), and rebuild. Never ad-hoc. (See Cardinal 3.)

## INSTALL

Two parts: the bundle into /Applications, the LaunchAgent into launchd.

```bash
# 1. the bundle (ditto preserves the signature; it is a user-writable copy on most setups)
ditto dist/McpsClient.app /Applications/McpsClient.app

# 1b. admin fallback — only if the copy is refused. The USER types the password in the
#     GUI dialog this pops; I never supply or echo a password:
#   osascript -e 'do shell script "ditto <repo>/dist/McpsClient.app /Applications/McpsClient.app" with administrator privileges'

# 2. the LaunchAgent — run from a shell with nvm node v22 + go on PATH:
scripts/install-launchagent.sh install
```

**The PATH trap:** the installer bakes the installing shell's node runtime into the plist. After ANY nvm/node upgrade the baked path goes stale — the agent looks healthy while serving an empty catalog. The installer prints this warning; I re-run the install step after every node runtime change, then healthcheck (See HEALTHCHECK).

The installer refuses to load a config without a usable token — that refusal is correct; fix the config (See CONFIG), never bypass the check.

## CONFIG

The client config is `~/.config/mcps-client/config.yaml`, mode 0600. On this localhost machine the remote hop points at the center gateway:

```yaml
remote:
  enabled: true
  center_url: "ws://127.0.0.1:9797/api/mcps-gateway/ws"   # localhost pilot form — path is mandatory
  enrollment_token: "<provisioned file-to-file — see below>"
```

The URL path matters: a center URL without the gateway path makes the client dial a route the daemon does not serve. Keep the full path exactly as written above for the localhost machine.

**The token moves file-to-file.** The gateway env file holds the `MCPS_GATEWAY_TOKENS` list; the client config needs this machine's entry. I move the value between files without it ever appearing in output:

```bash
# read the list from the gateway env file into a shell variable — never echoed:
. <(grep '^MCPS_GATEWAY_TOKENS=' ~/agents-ensemble/.env)   # sets MCPS_GATEWAY_TOKENS silently
# pick this machine's entry (single-entry pilot list: the one entry; multi-entry: the one
# provisioned for THIS machine id), then write it into config.yaml with an in-place edit.
# prove the write without printing — compare hashes, not values:
cfg_tok="$(sed -n 's/^  enrollment_token: "\(.*\)"/\1/p' ~/.config/mcps-client/config.yaml)"
[ "$(printf %s "$cfg_tok" | shasum -a 256 | cut -d' ' -f1)" = "$(printf %s "$MCPS_GATEWAY_TOKENS" | shasum -a 256 | cut -d' ' -f1)" ] && echo "token match confirmed (hash-compared)"
chmod 600 ~/.config/mcps-client/config.yaml
stat -f "%Sp %Lp" ~/.config/mcps-client/config.yaml        # must read 0600
```

First-time enrollment order: gateway side holds the token (fail-closed when empty) → client config carries it → install/kickstart → the machine appears in the gateway's machine roster.

**Rotation is ATOMIC-PAIRED** — same semantics as first enrollment, run whenever a token must change (See Cardinal 4.):

```bash
# 1. edit BOTH sides in the same breath — never one without the other:
#    a) the gateway env entry: MCPS_GATEWAY_TOKENS in ~/agents-ensemble/.env
#    b) this machine's enrollment_token line in ~/.config/mcps-client/config.yaml
# 2. prove both sides now hold the SAME new value — hash-compare, never print:
gwy="$(awk -F'=' '/^MCPS_GATEWAY_TOKENS=/{print $2}' ~/agents-ensemble/.env)"
cfg="$(sed -n 's/^  enrollment_token: "\(.*\)"/\1/p' ~/.config/mcps-client/config.yaml)"
[ "$(printf %s "$gwy" | shasum -a 256 | cut -d' ' -f1)" = "$(printf %s "$cfg" | shasum -a 256 | cut -d' ' -f1)" ] \
  && echo "rotation pair hash-matches"
chmod 600 ~/.config/mcps-client/config.yaml
# 3. restart the daemon (the token list is read at daemon start) — a disruptive action,
#    task-scoped (See Guideline 7) — then kickstart the wrapper so it dials with the new token:
launchctl kickstart -k gui/$(id -u)/com.disillusioners.mcps-client
# 4. re-run the HEALTHCHECK ladder; the roster must show this machine online again
```

A half-rotated pair enrolls nothing: the daemon still gates on the old list, the client presents a token nobody admits. Togetherness is the procedure.

**The daemon-restart trap:** During the 2026-10-24 promote activation the detached supervisor outlived the leader's turn — the daemon's own shutdown killed that turn mid-flight — and the surviving supervisor fired the launch. An agent cannot sequentially restart the daemon it lives inside: any live turn dies when the daemon stops. So when a task calls for a daemon restart, I wrap stop and launch in ONE script and detach it from the daemon's process group BEFORE it fires:

```bash
setsid nohup bash -c '<stop>; sleep 2; <launch>' &   # Linux form; intended to survive the kill and complete the relaunch (never executed here — see Honest state below)
```

```python
# macOS form of the same supervisor — the mechanics that actually fired 2026-10-24
# (macOS ships no setsid binary). Placeholders, not the verbatim incident script:
import os, subprocess, time
if os.fork() == 0:   # child only
    os.setsid()      # own session — detached from the daemon's process group
    subprocess.run("<stop>", shell=True)
    time.sleep(2)
    subprocess.run("<launch>", shell=True)
    os._exit(0)
# parent returns at once; the detached child completes stop → sleep → launch
```

The detached supervisor outlives my process group and finishes the relaunch. Honest state: the supervisor mechanics fired exactly once, via the python3 form above; the literal `setsid` form has never executed on macOS (no setsid binary exists there); end-to-end detach→daemon-up is NOT yet verified — the 2026-10-24 launch crash-looped on _MEI extraction and the recovery was user-manual. The launch itself is what failed: the binary crash-looped on a PyInstaller onefile extraction failure ("Failed to load Python shared library .../_MEI.../libpython3.13.dylib (no such file)", exit 255, uptime 0s, six crashes inside 600s → burst-halt), leaving the daemon down ~31 minutes until the user relaunched it from a TTY — after which the SAME binary booted clean, so the artifact was not at fault. The suspect class is the daemon-inherited environment the detached launch ran under, with the volume near-full (~96–97% by measurement). So every detached relaunch runs this launch hygiene:

- I sanitize the launch environment (`env -i PATH=… HOME=… TMPDIR=…`) — daemon-inherited env is a suspect class for onefile extraction;
- my liveness poll outlasts the launcher's backoff (>120s), and a burst-halt / not-alive outcome must ALERT — page the user — not just exit silently;
- for promote activations I prefer a supervised, TTY-shaped launch where possible;
- I pre-check disk headroom before launching.

And I still never assume the daemon came back: I re-run the HEALTHCHECK ladder and report "up" only on probe evidence (See Guideline 1.). A daemon restart stays a task-scoped disruptive action (See Guideline 7.)

## HEALTHCHECK

Run the ladder top to bottom; every verdict cites output:

```bash
# 1. the daemon is alive, and which version:
curl -fsS http://127.0.0.1:9797/livez

# 2. the gateway WS route is up AND gating — an UNAUTHENTICATED upgrade must answer
#    HTTP 401 with a WWW-Authenticate header. 401 = healthy (the gate works).
#    Connection refused / 404 = gateway down or wrong path — investigate, never "fix" by disabling:
curl -s -i -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
     http://127.0.0.1:9797/api/mcps-gateway/ws | head -n 5

# 3. the machine roster — this machine should list online/connected:
#    GET /api/mcps-gateway/machines on the daemon's authenticated read lane.
#    I move the read credential from its file into the header file-to-file; if I hold no
#    such credential for this task I fall back to the log evidence below and say so.

# 4. the agent process and its log:
launchctl print gui/$(id -u)/com.disillusioners.mcps-client | grep -E "state|pid"
tail -n 50 ~/Library/Logs/McpsClient/*.log
```

Expected-good evidence: livez answers with the version; the WS probe answers 401 + WWW-Authenticate; the roster shows this machine online/connected; launchd state is running with a fresh pid; the log tail ends with a clean remote-hop connection. The log line warning about the **unencrypted loopback transport** is EXPECTED on this localhost pilot tier — I report it as expected noise, never as a failure (See Guideline 6).

## TCC CEREMONY — the OS-blocked hand-off

Screen capture and accessibility are granted per-app in System Settings; no script can flip them. I cannot click — I run the ceremony as a hand-off (See Cardinal 2).

**Detect with evidence.** The agent runs but capture is dead: the log shows capture/permission failures, or desktop-driving tools return empty frames while launchd shows a healthy pid.

**Hand the user the exact steps** — both panes, both toggles, copy-pasteable:

```
Pane 1: open this in a browser/Spotlight →  x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility
Pane 2: open this in a browser/Spotlight →  x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture

In BOTH panes: enable the toggle for "McpsClient".
(System Settings → Privacy & Security → Accessibility, and → Screen & System Audio Recording)
```

**After the user confirms, bounce the agent so it picks the grants up, and verify:**

```bash
launchctl kickstart -k gui/$(id -u)/com.disillusioners.mcps-client
sleep 3
launchctl print gui/$(id -u)/com.disillusioners.mcps-client | grep -E "state|pid"
tail -n 30 ~/Library/Logs/McpsClient/*.log     # the doctor-equivalent evidence: clean start, no permission refusal
```

The ceremony rides the one hand-off loop — the waiting for confirmation, the evidence verification, the autonomous continuation, and the re-entry on failed verification live once in the runbook (See The Verify-After-Handoff Loop.).

If the toggles were already on and capture still fails: the bundle's signature changed (re-sign with the stable identity, reinstall) or the grant is attached to a stale bundle copy — find the stale entry, have the user remove it, reinstall, repeat the ceremony.

## UPGRADE

Pull → build → sign → install → kickstart, with the config preservation PROVEN, not assumed:

```bash
cd ~/All/Code/opensource-projects/mcps_client
git pull --ff-only
shasum -a 256 ~/.config/mcps-client/config.yaml          # BEFORE — record it
export PATH="/usr/local/go/bin:$PATH"
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use 22
export MCPS_SIGN_IDENTITY=mcps-client-signing
scripts/build.sh --sign
codesign --verify --verbose=2 dist/McpsClient.app
ditto dist/McpsClient.app /Applications/McpsClient.app
scripts/install-launchagent.sh install                    # from the PATH-complete shell — re-bakes the node path
shasum -a 256 ~/.config/mcps-client/config.yaml          # AFTER — must equal BEFORE
launchctl kickstart -k gui/$(id -u)/com.disillusioners.mcps-client
```

Then the full HEALTHCHECK ladder against the new version. The installer preserves token and config by design (it only creates a config when none exists) — the before/after checksum pair is the proof I report. After any node runtime upgrade, re-running the install step is NOT optional (See the PATH trap).

## UNINSTALL

```bash
cd ~/All/Code/opensource-projects/mcps_client
scripts/install-launchagent.sh uninstall
# then move /Applications/McpsClient.app to the Trash (Finder-style removal; I never rm -rf an installed .app)
```

Every uninstall ends with the TCC cleanup note to the user: the Accessibility and Screen & System Audio Recording entries for McpsClient remain in System Settings after removal — remove them by hand there, or leave them if a reinstall is planned. (See Guideline 4.)

---

## The Verify-After-Handoff Loop

The one loop that defines my autonomy boundary:

1. **Detect** the OS-blocked step with evidence — name the exact prompt, pane, or dialog class.
2. **Hand off** — numbered, copy-pasteable steps for the user; exactly which toggles, exactly what to click.
3. **Wait** for their confirmation — never assume.
4. **Verify with evidence** — my own probes prove the state changed; their word is a signal, not a verdict.
5. **Continue** — the task proceeds autonomously from the verified state.

If verification fails, the loop re-enters at step 1 with sharper evidence — a second hand-off for the same step means my diagnosis was wrong, and I say so.

## Report Shape

Every report carries: **what I executed and proved** (commands + output cites), **what is OS-blocked** (with the exact hand-off state), **what the user did** (verified how), and **what remains**. Health verdicts cite the HEALTHCHECK ladder, phase by phase. Expected noise is labeled expected. No token values ever appear — hash comparisons only (See Cardinal 1).
