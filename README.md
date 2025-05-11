# piazza-cli
A command line tool for Piazza

# Interactive Piazza CLI

![Demo](assets/output.gif)

This project provides an interactive, color-rich command-line interface to Piazza using the unofficial piazza-api.

## Setup

1. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```

2. Run the CLI:
   ```sh
   python piazza_cli.py
   ```

## Features
- Auto-login if credentials are cached (skips login screen)
- List your courses with arrow-key navigation (Windows supported)
- Read course discussions in a Reddit-style, color-formatted thread view
- Scroll through questions and comments with up/down arrow keys
- Post comments directly from the CLI
- Rich text formatting for roles (instructor, student, OP, etc.)
- Cross-platform (tested on Windows)

## Usage
- On first run, you'll be prompted for your Piazza email and password. Credentials are cached for future logins.
- Use the arrow keys to navigate course and question lists.
- When viewing a thread, use up/down arrows to scroll, `c` to comment, and `b`/`esc`/`q` to go back.
- Type `help` at the prompt to see available commands.

## Notes
- This project uses the unofficial piazza-api: https://github.com/hfaran/piazza-api
- Requires Python 3.7+
- For best experience, use a terminal that supports ANSI colors (Windows Terminal, PowerShell, etc.)
