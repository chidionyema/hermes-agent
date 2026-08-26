# summary (isopsephy card)

**What it is for.** `/summary <text>` in Telegram (or `summary <text>` in chat) renders a card with three numerological ciphers (Pythagorean, Hebrew gematria, Chaldean), the root-number ladder, a structural profile of the text, anagram permutations, and a qabalah section. Telegram's rich message endpoint renders the `<details>` blocks and tables natively.

**Where it lives.** `gateway/summary_card.py` (`render_summary_card`, `render_for_platform`, `render_summary_json`, `render_compare_card`), `gateway/qabalah.py`, wired in `gateway/slash_commands.py` (`_handle_summary_command`) and `hermes_cli/commands.py`.

**What it costs.** Pure computation, no model call, no network. Anagram output is paginated above 200 results.

**How to stop it.** Remove the `summary` entry from the slash command table in `gateway/slash_commands.py`; there is no runtime flag because the command only runs when a user invokes it.
