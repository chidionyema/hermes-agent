"""Text-mode UI primitives for Telegram bot replies.

Renders the 5-element grammar from the `text-mode-ui-design` skill:
framed header band, boxed chip grid, banner callout, per-entity framed
blocks, and insight callouts. All output is designed to render correctly
in Telegram (box-drawing chars survive, code-block fences preserve
fixed-width alignment, blank lines are avoided in favor of frame changes).

The output is a single string that callers wrap through the platform
adapter's `format_message` so markdown_v2 escaping is applied uniformly.
Place all dynamic content inside `text` fenced blocks to skip escaping
of underscores inside provider slugs / model IDs.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple


# Default width for header bands and chip grids. Telegram renders nicely
# inside its message bubble at ~42-46 chars wide.
WIDTH = 44


def _hr(width: int = WIDTH, char: str = "━") -> str:
    return char * width


def framed_header(
    title: str,
    subtitle: str = "",
    width: int = WIDTH,
) -> str:
    """Framed header band with spaced caps title.

    Returns a code-fenced block so Telegram renders it monospace and
    skips markdown_v2 escaping on the inner content.
    """
    inner = _hr(width) + "\n"
    inner += f"  {title}\n"
    if subtitle:
        inner += f"   {subtitle}\n"
    inner += _hr(width)
    return f"```text\n{inner}\n```"


def chip_grid(
    label: str,
    chips: Iterable[str],
    meta: str = "",
    width: int = WIDTH,
) -> str:
    """Boxed chip grid — heavy corners, vertical bars.

    Example::

        ╔═══════ AT-A-GLANCE ═══════════════╗
        ║  🧮 **6**   ·   ✡️ **2**   ·   ⚡ **11**
        ║  8V/8C  ·  12 unique
        ╚════════════════════════════════════╝
    """
    chip_line = "   ·   ".join(chips)
    body = f"╔═══════ {label} ═{'═' * max(0, width - len(label) - 12)}╗\n"
    body += f"║  {chip_line}\n"
    if meta:
        body += f"║  {meta}\n"
    body += f"╚{'═' * (width - 1)}╝"
    return f"```text\n{body}\n```"


def banner_callout(
    label: str,
    body: str,
    width: int = WIDTH,
) -> str:
    """Banner callout — light corners, distinct frame from chip grid.

    Use for state announcements, persistence flags, alerts.
    """
    body_text = f"┌── {label} {'─' * max(0, width - len(label) - 5)}┐\n"
    body_text += f"│  {body}\n"
    body_text += f"└{'─' * (width - 1)}┘"
    return f"```text\n{body_text}\n```"


def entity_block(
    label: str,
    fields: Iterable[Tuple[str, str]],
    width: int = WIDTH,
) -> str:
    """Per-entity framed block — rounded corners.

    ``label`` is the entity title. ``fields`` is a list of (key, value)
    pairs rendered as bullet lines.
    """
    parts = [f"╭─ {label} {'─' * max(0, width - len(label) - 4)}╮"]
    for key, value in fields:
        parts.append(f"│ {key}  {value}")
    parts.append(f"╰{'─' * (width - 1)}╯")
    return f"```text\n" + "\n".join(parts) + "\n```"


def insight_callout(text: str) -> str:
    """Blockquote insight callout — surfaces a discovery, not data.

    Telegram renders `>` as a real blockquote which creates distinct
    visual hierarchy from the framed blocks.
    """
    return f"> {text}"


def render_model_picker_card(
    current_model: str,
    current_provider_label: str,
    providers: List[dict],
    is_session_only: bool = True,
) -> str:
    """Render the full `/model` header text using the 5-element grammar.

    `providers` is a list of dicts with keys: `slug`, `name`,
    `total_models`, `is_current`. The returned string is fed to
    `adapter.format_message` which handles markdown_v2 escaping.
    """
    sections = []

    # 1. Framed header band
    sections.append(framed_header(
        title="M O D E L   C O N F I G U R A T I O N",
        subtitle=f"current · {current_provider_label}",
    ))

    # 2. Boxed chip grid — current model + provider
    n_models = sum(int(p.get("total_models", 0) or 0) for p in providers)
    sections.append(chip_grid(
        label="ACTIVE",
        chips=[
            f"🤖 `{current_model or 'unknown'}`",
            f"📡 {current_provider_label}",
        ],
        meta=f"{n_models} models across {len(providers)} providers",
    ))

    # 3. Banner callout — provider count
    sections.append(banner_callout(
        label="SELECT PROVIDER",
        body=f"{len(providers)} available · tap to drill in",
    ))

    # 4. Per-entity framed blocks — one per provider
    for p in providers:
        name = p.get("name", p.get("slug", "?"))
        slug = p.get("slug", "")
        count = int(p.get("total_models", 0) or 0)
        is_current = bool(p.get("is_current"))
        marker = "✓ current" if is_current else "tap to switch"
        sections.append(entity_block(
            label=f"{name}  ·  {count} models",
            fields=[
                ("", marker),
                ("slug", slug),
            ],
        ))

    # 5. Insight callout — persistence flag
    if is_session_only:
        sections.append(insight_callout(
            "⚠️ **session-scoped** — changes apply to your next message in this chat. "
            "Use `/model <name> --global` to persist across sessions."
        ))
    else:
        sections.append(insight_callout(
            "✅ **persistent** — saved to `~/.hermes/config.yaml` as the default."
        ))

    return "\n\n".join(sections)


def render_agent_model_panel(
    current_model: str,
    current_provider_label: str,
    switches: List[dict],
) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """Render the 🤖 Agent & Model panel for `/panel` door.

    ``switches`` is a list of dicts with keys: `slug`, `label`, `available`
    (bool). Returns (text, button_rows) ready for `PanelView`.

    The button rows match the existing `agent:<action>` callback pattern
    so the dispatch chain can route them.
    """
    sections = []

    sections.append(framed_header(
        title="A G E N T   &   M O D E L",
        subtitle="behavior switches for this session",
    ))

    sections.append(chip_grid(
        label="NOW",
        chips=[
            f"🤖 `{current_model or 'unknown'}`",
            f"📡 {current_provider_label}",
        ],
        meta="tap a switch below to change",
    ))

    banner = (
        f"{sum(1 for s in switches if s.get('available'))} of {len(switches)} switches available"
    )
    sections.append(banner_callout(label="BEHAVIOR", body=banner))

    for s in switches:
        label = s.get("label", s.get("slug", "?"))
        available = bool(s.get("available"))
        cmd = f"/{s.get('slug', '').replace('agent_', '')}"
        if available:
            sections.append(entity_block(
                label=label,
                fields=[
                    ("", "tap to open"),
                    ("cmd", cmd),
                ],
            ))
        else:
            sections.append(entity_block(
                label=label,
                fields=[
                    ("", "unavailable"),
                    ("cmd", cmd),
                ],
            ))

    sections.append(insight_callout(
        "👉 `/model` is the door — every other switch changes "
        "*how* the model responds, not *which* model is active."
    ))

    text = "\n\n".join(sections)

    # Build button rows: 2 per row, then a back row at the end.
    available_only = [s for s in switches if s.get("available")]
    buttons: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    for s in available_only:
        slug = s.get("slug", "")
        label = s.get("label", slug)
        # Truncate to fit Telegram's 64-char callback-data limit and
        # ~20-char button label.
        short = label[:20]
        # Use the existing `agent:<slug>` callback prefix to keep the
        # dispatch chain consistent.
        callback = f"agent:{slug}"[:64]
        row.append((short, callback))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Always append a back-to-panel row.
    buttons.append([("◀ Panel", "estate:refresh")])

    return text, buttons
