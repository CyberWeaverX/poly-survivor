# Polymarket Survival Bot

An autonomous AI trading bot for [Polymarket](https://polymarket.com) prediction markets.

## Overview

This bot uses Claude AI to:

- Analyze prediction markets and identify trading opportunities
- Conduct web-based research for market analysis
- Place bets based on Expected Value (EV) calculations
- Manage risk with built-in capital management rules

## Features

- 🔍 **Web Search Research** - Uses Claude's web search to gather real-time information
- 💾 **Research Caching** - SQLite-based caching to avoid duplicate research costs
- 🧠 **Memory Persistence** - Remembers previous cycle summaries for continuity
- ⚖️ **Risk Management** - Position limits, liquidity checks, and reserve requirements
- 🔄 **Autonomous Operation** - Can run unattended on a schedule

## Quick Start

```bash
# Clone the repo
git clone https://github.com/CyberWeaverX/poly-survivor.git
cd poly-survivor

# Install dependencies
pip install -r requirements.txt

# Create secrets directory with your credentials
mkdir secrets
# Add: secrets/config.json, secrets/api_credentials.json, secrets/keys.csv

# Run the bot
python bot.py

# Or dry-run mode (no real trades)
python bot.py --dry-run
```

## Configuration

Create the following files in `secrets/`:

- `config.json` - Anthropic API key
- `api_credentials.json` - Polymarket API credentials
- `keys.csv` - Wallet private key

## Disclaimer

This is an experimental project. Use at your own risk. Prediction market trading involves financial risk.

## License

MIT
