# Slack Setup Guide

This guide walks you through setting up Slack as a message source in Ensemble using Socket Mode.

## Prerequisites

- A Slack workspace where you have admin permissions
- The ability to create a Slack App

## Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Select **From an app manifest**
3. Choose your workspace and click **Next**
4. Select **YAML** tab and paste the following manifest:

```yaml
display_information:
  name: Ensemble Bot
  description: AI agent assistant for your workspace
  background_color: "#2C3E50"
features:
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Ensemble
    always_online: true
  slash_commands:
    - command: /new
      description: Start a new conversation
      usage_hint: "[optional message]"
oauth_config:
  scopes:
    bot:
      - chat:write
      - channels:history
      - groups:history
      - im:history
      - mpim:history
      - channels:read
      - groups:read
      - im:read
      - files:read
      - users:read
      - reactions:write
      - commands
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

5. Click **Next**, review permissions, and click **Create**

## Step 2: Configure OAuth Scopes

If you didn't use the manifest, manually add these OAuth scopes to your app:

| Scope | Purpose |
|-------|---------|
| `chat:write` | Send messages as the bot |
| `channels:history` | Read messages in public channels |
| `groups:history` | Read messages in private channels |
| `im:history` | Read direct messages |
| `mpim:history` | Read group direct messages |
| `channels:read` | List and view public channels |
| `groups:read` | List and view private channels |
| `im:read` | View direct message conversations |
| `files:read` | Access files shared in channels |
| `users:read` | View users in workspace |
| `reactions:write` | Add reactions to messages |
| `commands` | Create slash commands |

To add scopes:
1. Go to **OAuth & Permissions** in your app settings
2. Scroll to **Bot Token Scopes**
3. Click **Add an OAuth Scope** and add each scope above

## Step 3: Enable Socket Mode

Socket Mode allows your app to receive events via WebSocket instead of a public HTTPS endpoint.

1. Go to **Socket Mode** in your app settings
2. Toggle **Enable Socket Mode** to ON
3. A message will appear with your **App-Level Token** (starts with `xapp-`)
4. Copy this token - you'll need it for the `app_token` credential

## Step 4: Install the App to Your Workspace

1. Go to **Install App** in your app settings
2. Click **Install to Workspace**
3. Authorize the app with the requested permissions
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`) - this is your `bot_token`

## Step 5: Create the Source in Ensemble

Create a new Slack source configuration using the API:

```bash
curl -X POST http://localhost:8079/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "slack-main",
    "source_type": "slack",
    "name": "Slack Workspace",
    "enabled": true,
    "credentials": {
      "bot_token": "xoxb-your-bot-token-here",
      "app_token": "xapp-your-app-level-token-here"
    },
    "config": {
      "default_agent": "leader"
    }
  }'
```

Or using the Ensemble CLI:

```bash
ensemble sources create slack \
  --source-id slack-main \
  --bot-token xoxb-your-bot-token-here \
  --app-token xapp-your-app-level-token-here \
  --default-agent leader
```

## Step 6: Verify Connection

Test that Ensemble can connect to Slack:

```bash
curl -X POST http://localhost:8079/api/v1/sources/slack-main/test \
  -H "Content-Type: application/json"
```

You should receive a response like:
```json
{
  "success": true,
  "message": "Connected to Your Workspace as @Ensemble"
}
```

Check the adapter status:

```bash
curl http://localhost:8079/api/v1/sources
```

The Slack adapter should show `status: "running"`.

## Optional: Configure Slash Commands

If you want to use the `/new` slash command to start new conversations:

1. Go to **Slash Commands** in your Slack app settings
2. Click **Create New Command**
3. Fill in:
   - **Command**: `/new`
   - **Description**: Start a new conversation with the AI agent
   - **Usage Hint**: `[optional message]`
4. Click **Save**

Note: With Socket Mode enabled, you don't need to specify a Request URL for slash commands.

## Troubleshooting

### "Invalid bot token" error
- Verify your bot token starts with `xoxb-`
- Check that the token hasn't been revoked in the Slack API dashboard

### "Token type not acceptable" error
- Ensure you're using the Bot Token (`xoxb-`), not the User Token (`xoxp-`)

### "App not installed in workspace" error
- Reinstall the app to your workspace from **Install App** settings

### Socket Mode not connecting
- Verify the App-Level Token starts with `xapp-`
- Check that Socket Mode is enabled in app settings

### Events not being received
- With Socket Mode, verify the WebSocket connection is established
- Check that Socket Mode is enabled in app settings
- Verify the bot has been invited to the channel (for channel messages)

## Security Considerations

- Store your tokens securely (environment variables or a secrets manager)
- Never commit tokens to version control
- Rotate tokens periodically
- Use the principle of least privilege when requesting OAuth scopes
