# picguessr

## Host yourself

Clone the repository:

```
git clone https://github.com/frostming/picguessr.git
```

Create a file named `.env` in the root directory of the repository, and put the following content in it:

```
AZURE_OPENAI_ENDPOINT=""  # Your Azure OpenAI endpoint
AZURE_OPENAI_API_KEY=""    # Your Azure OpenAI API key
BOT_TOKEN=""  # The bot token got from BotFather
```

Start the bot:

```
./start.sh
```

Or using `docker` directly:

```
docker compose up -d
```
