import html


def esc(s):
    return html.escape(str(s if s is not None else ""))
