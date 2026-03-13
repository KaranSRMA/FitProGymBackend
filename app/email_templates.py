import html

BRAND_NAME = "FitPro GYM"
BRAND_TAGLINE = "Stronger every day"

COLOR_BG = "#0b0b0c"
COLOR_CARD = "#111216"
COLOR_BORDER = "#27272a"
COLOR_TEXT = "#e5e7eb"
COLOR_MUTED = "#a1a1aa"
COLOR_ACCENT = "#ef4444"
COLOR_BUTTON_TEXT = "#ffffff"
COLOR_LINK = "#fca5a5"


def _escape_lines(lines: list[str] | None) -> list[str]:
    if not lines:
        return []
    return [html.escape(line) for line in lines if line is not None]


def _paragraphs(lines: list[str]) -> str:
    if not lines:
        return ""
    return "".join(
        f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{COLOR_TEXT};">{line}</p>'
        for line in lines
    )


def build_basic_email_html(title: str, preheader: str, body_lines: list[str]) -> str:
    safe_title = html.escape(title or "FitPro Update")
    safe_preheader = html.escape(preheader or "")
    safe_lines = _escape_lines(body_lines)

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
  </head>
  <body style="margin:0;padding:0;background:{COLOR_BG};">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      {safe_preheader}
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{COLOR_BG};padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 8px;">
                <div style="font-size:13px;letter-spacing:2px;color:{COLOR_MUTED};text-transform:uppercase;">
                  {BRAND_NAME}
                </div>
                <h1 style="margin:12px 0 16px;font-size:24px;line-height:1.3;color:{COLOR_TEXT};">
                  {safe_title}
                </h1>
                {_paragraphs(safe_lines)}
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px 28px;border-top:1px solid {COLOR_BORDER};">
                <p style="margin:0;font-size:12px;line-height:1.6;color:{COLOR_MUTED};">
                  {BRAND_TAGLINE} · Please do not reply to this email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def build_action_email_html(
    title: str,
    preheader: str,
    intro_lines: list[str],
    action_url: str,
    action_text: str,
    outro_lines: list[str] | None = None,
) -> str:
    safe_title = html.escape(title or "FitPro Action Required")
    safe_preheader = html.escape(preheader or "")
    safe_intro = _escape_lines(intro_lines)
    safe_outro = _escape_lines(outro_lines or [])
    safe_action_text = html.escape(action_text or "Continue")
    safe_action_url = html.escape(action_url or "", quote=True)

    intro_html = _paragraphs(safe_intro)
    outro_html = _paragraphs(safe_outro)

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
  </head>
  <body style="margin:0;padding:0;background:{COLOR_BG};">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      {safe_preheader}
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{COLOR_BG};padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:{COLOR_CARD};border:1px solid {COLOR_BORDER};border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 8px;">
                <div style="font-size:13px;letter-spacing:2px;color:{COLOR_MUTED};text-transform:uppercase;">
                  {BRAND_NAME}
                </div>
                <h1 style="margin:12px 0 16px;font-size:24px;line-height:1.3;color:{COLOR_TEXT};">
                  {safe_title}
                </h1>
                {intro_html}
                <div style="text-align:center;margin:22px 0 18px;">
                  <a href="{safe_action_url}" style="display:inline-block;background:{COLOR_ACCENT};color:{COLOR_BUTTON_TEXT};text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600;font-size:15px;">
                    {safe_action_text}
                  </a>
                </div>
                <p style="margin:0 0 8px;font-size:13px;color:{COLOR_MUTED};">
                  If the button doesn't work, copy and paste this link:
                </p>
                <p style="margin:0 0 14px;font-size:13px;line-height:1.6;word-break:break-all;">
                  <a href="{safe_action_url}" style="color:{COLOR_LINK};text-decoration:underline;">{safe_action_url}</a>
                </p>
                {outro_html}
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px 28px;border-top:1px solid {COLOR_BORDER};">
                <p style="margin:0;font-size:12px;line-height:1.6;color:{COLOR_MUTED};">
                  {BRAND_TAGLINE} · If you didn't request this, please ignore this email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
