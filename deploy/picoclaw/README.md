# PicoClaw + token-free WhatsApp alerts (Oracle deploy)

This documents how PikoTrade's WhatsApp alerting works on the Oracle VM so the setup is
reproducible. The Python agent runs on a 5-minute cron/loop and pushes WhatsApp alerts
**without spending any LLM tokens**; DeepSeek is only used when the user messages the bot directly.

## Pieces

1. **`pikotrade` (systemd)** - runs `scripts/run_always_on.py` in a loop. Each cycle scans,
   forward-tests, and (when `--whatsapp-push` / `PICOTRADE_WHATSAPP_PUSH=1`) runs
   `scripts/whatsapp_push.py`.
2. **`scripts/whatsapp_push.py`** - reads `outputs/forward_tests.json`, detects state changes
   (new setup / filled / partial banked / closed), and POSTs a concise, dual-timeframe alert to
   PicoClaw's token-free `/send` endpoint. It dedupes via `outputs/whatsapp_push_state.json`
   (cold-start seeds without flooding) and writes `~/.picoclaw/workspace/memory/OPEN_TRADES.md`.
3. **`picoclaw-gateway` (systemd)** - PicoClaw built from source with `-tags whatsapp_native`,
   paired to the WhatsApp account, allowlisted to the owner's LID, DeepSeek as the brain.
4. **`deploy/picoclaw/SOUL.md`** - the agent persona; copied to `~/.picoclaw/workspace/SOUL.md`
   so the bot understands the system and answers from the live data files.

## The `/send` patch (token-free outbound)

PicoClaw has no built-in way to push an arbitrary message to a channel without invoking the
agent/LLM. We added a `POST /send` endpoint to the gateway health server that delivers text
straight to a channel via the live session.

- `pkg/health/server.go`: add `SetSendFunc(fn func(channel, to, text string) error)`, a
  `sendRequest{channel,to,text}` struct, and a `sendHandler` that validates the (optional)
  bearer token, decodes the body, and calls the registered send func. Route `POST /send`.
- `pkg/gateway/gateway.go`: after services start, wire it up:

  ```go
  runningServices.HealthServer.SetSendFunc(func(channel, to, text string) error {
      return runningServices.ChannelManager.SendToChannel(context.Background(), channel, to, text)
  })
  ```

Rebuild and reinstall:

```bash
cd ~/picoclaw-src
go build -tags "goolm,stdjson,whatsapp_native" -ldflags "-s -w" -o picoclaw ./cmd/picoclaw
sudo install -m 0755 picoclaw /usr/local/bin/picoclaw
sudo systemctl restart picoclaw-gateway
```

The gateway generates an auth token at startup and stores it in `~/.picoclaw/.picoclaw.pid`;
`whatsapp_push.py` reads that token and sends it as a bearer header. The recipient is the
owner's WhatsApp LID; the channel is `whatsapp`.

## Key config (`~/.picoclaw/config.json`)

- `model_list` -> DeepSeek (`deepseek-chat`), key stored in `~/.picoclaw/.security.yml`.
- `channel_list.whatsapp`: `enabled: true`, `type: "whatsapp_native"`,
  `settings.use_native: true`, `settings.bridge_url: ""` (must be empty or it forces bridge mode),
  `settings.session_store_path` = absolute path under `~/.picoclaw/workspace/whatsapp/`,
  and `allow_from: ["<owner-LID>@lid"]`.

## Gotchas (learned the hard way)

- Use `channel_list`, not `channels` (unknown-field error otherwise).
- WhatsApp sender IDs are **LIDs** (`...@lid`), not plain phone numbers - allowlist the LID.
- `IsAllowedSender` drops disallowed messages *silently* before debug logging.
- A non-empty `bridge_url` overrides `use_native` and forces bridge mode (port 3001).
- Strip CRLF from any key file copied from Windows (`tr -d '\r'`).
