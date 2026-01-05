# Steam API Setup Instructions

To use the `sync-steam-playtime` command, you need to obtain Steam API credentials.

## Required Credentials

You need two values:
1. **Steam API Key** (SET_STEAM_API_KEY)
2. **Steam User ID** (SET_STEAM_USER_ID)

## Step 1: Get Your Steam API Key

1. Go to https://steamcommunity.com/dev/apikey
2. Log in with your Steam account
3. Enter a domain name (you can use `localhost` for personal use)
4. Agree to the Steam Web API Terms of Use
5. Click "Register"
6. Copy the generated API key (it will look like a long hexadecimal string)

## Step 2: Get Your Steam User ID

**Option A: From your Steam profile URL**
1. Go to your Steam profile page
2. If your URL is like `https://steamcommunity.com/id/username/`, you need to find your numeric ID
3. If your URL is like `https://steamcommunity.com/profiles/76561198012345678/`, the number is your Steam ID

**Option B: Using a Steam ID Finder**
1. Go to https://steamid.io/ or https://steamidfinder.com/
2. Enter your Steam profile URL or username
3. Copy the "steamID64" value (17-digit number)

## Step 3: Add to Environment Variables

Add these to your environment variables (or `.env` file if you're using one):

```bash
export SET_STEAM_API_KEY="your_api_key_here"
export SET_STEAM_USER_ID="your_17_digit_steam_id_here"
```

Or add them to your shell configuration file (`~/.zshrc`, `~/.bashrc`, etc.):

```bash
echo 'export SET_STEAM_API_KEY="your_api_key_here"' >> ~/.zshrc
echo 'export SET_STEAM_USER_ID="your_17_digit_steam_id_here"' >> ~/.zshrc
source ~/.zshrc
```

## Step 4: Verify Setup

Run the command to verify it works:

```bash
python shelf.py sync-steam-playtime
```

## Notes

- The Steam API key is tied to your Steam account
- Keep your API key private and don't commit it to version control
- The Steam User ID is public information
- Make sure your Steam profile's game details are set to "Public" in your Steam Privacy Settings
  (Settings → Privacy → Game details → Public)

## Troubleshooting

**"No games found"**
- Check that your Steam profile game details are set to Public
- Verify you're using the correct Steam User ID

**"Invalid API key"**
- Double-check you copied the API key correctly
- Make sure there are no extra spaces or quotes in the environment variable

**"Error: No active shelf found"**
- Make sure you have at least one non-finished shelf in your database
