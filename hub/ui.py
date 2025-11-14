import jinja2
from nicegui import app, ui
import os
import re
from typing import Dict, List, Tuple
import shlex
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


def enable_external_links_new_tab():
    """Add JavaScript to the page to make external links open in a new browser tab."""

    ui.run_javascript(
        r'''
            (function() {
              if (window.__ng_ext_link_handler_installed) return;
              window.__ng_ext_link_handler_installed = true;
              const handler = (e) => {
                if (e.defaultPrevented || e.button !== 0 || e.metaKey ||
                    e.ctrlKey || e.shiftKey || e.altKey) return;
                const path = e.composedPath ? e.composedPath() : [];
                const a = path.find(n => n && n.tagName === 'A' && n.href);
                if (!a) return;
                try {
                  const hrefAttr = a.getAttribute('href') || '';
                  const url = new URL(a.href, location.href);
                  const isHttp = /^https?:$/i.test(url.protocol);
                  const isInternal = isHttp && (
                    url.origin === location.origin ||
                    hrefAttr.startsWith('/') ||
                    !/^\w+:/.test(hrefAttr)
                  );
                  if (isHttp && !isInternal) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
                    window.open(url.href, '_blank', 'noopener,noreferrer');
                  }
                } catch (_) {}
              };
              document.removeEventListener('click', window.__ng_ext_link_handler, true);
              window.__ng_ext_link_handler = handler;
              document.addEventListener('click', handler, true);
            })();
        '''
    )


###
### Jinja "shortcode" helpers; emit placeholder markers we can parse later
###


def attrs(**kw) -> str:
    parts = list()
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = 'true' if v else 'false'
        parts.append(f'{k}="{str(v).replace(chr(34), "&quot;")}"')
    return ' '.join(parts)


def headline(text: str, align: str = 'left') -> str:
    return f'<!--NG:headline {attrs(text=text, align=align)}-->'


def image(src: str, align: str = 'left', alt: str = '', width: str = None) -> str:
    return f'<!--NG:image {attrs(src=f'ui/img/{src}', align=align, alt=alt, width=width)}-->'


def input(id: str, icon: str = None, label: str = '', placeholder: str = '') -> str:  # noqa: A001
    return f'<!--NG:input {attrs(id=id, icon=icon, label=label, placeholder=placeholder)}-->'


def checkbox(id: str, label: str, icon: str = None, value: bool = False) -> str:
    return f'<!--NG:checkbox {attrs(id=id, label=label, icon=icon, value=value)}-->'


def button(text: str = 'Submit', id: str = None) -> str:
    return f'<!--NG:button {attrs(text=text, id=id)}-->'


jinja_env = jinja2.Environment(loader=jinja2.BaseLoader(), autoescape=False)
jinja_env.globals.update(
    {
        'headline': headline,
        'image': image,
        'input': input,
        'checkbox': checkbox,
        'button': button,
    }
)

###
### render Markdown
###


def split_h1(md: str) -> Tuple[str, str]:
    h1_re = re.compile(r'^\s*#\s+(?P<title>.+?)\s*$', re.M)
    m = h1_re.search(md)
    if not m:
        return '', md
    page_title = m.group('title').strip()
    start, end = m.span()
    rest = md[:start] + md[end:]
    return page_title, rest.lstrip()


def sections_after_h2(md: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Return (preamble_body, list of (h2_title_md, section_body_md))."""
    h2_re = re.compile(r'^\s*##\s+(?P<h2>.+?)\s*$', re.M)
    last_pos = 0
    for m in h2_re.finditer(md):
        if last_pos == 0:
            preamble = md[: m.start()].strip()
        else:
            prev = list(h2_re.finditer(md[: m.start()]))[-1]
        last_pos = m.start()
    # Collect sections
    sections = list()
    matches = list(h2_re.finditer(md))
    if not matches:
        return md.strip(), list()
    preamble = md[: matches[0].start()].strip()
    for i, m in enumerate(matches):
        title = m.group('h2').strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        sections.append((f'## {title}', body))
    return preamble, sections


def parse_attrs(s: str) -> Dict[str, str]:
    out: Dict[str, str] = dict()
    for token in shlex.split(s or ''):
        if '=' in token:
            k, v = token.split('=', 1)
            out[k] = v.strip('"').strip("'")
        else:
            out[token] = 'true'
    return out


def render_component(kind: str, attrs: Dict[str, str], scope: Dict[str, object]) -> None:
    if kind == 'headline':
        align = attrs.get('align', 'left')
        text = attrs.get('text', '')
        with ui.column().classes(f'w-full items-{align}'):  # left or center (¿right broken?)
            ui.markdown(f'### {text}')
        return
    if kind == 'image':
        src = attrs.get('src', '')
        align = attrs.get('align', 'left')
        alt = attrs.get('alt', '')
        width = attrs.get('width')
        img = ui.image(src).props(f'alt={alt}')
        if width:
            if width.endswith('%'):
                img.style(f'width:{width};')
            elif width.replace('.', '', 1).isdigit():
                img.style(f'width:{width}px;')
            else:
                img.style(f'width:{width};')
        if align == 'center':
            img.classes('mx-auto block')
        elif align == 'right':
            img.classes('ml-auto block')
        elif align == 'left':
            img.classes('mr-auto block')
        return
    if kind == 'input':
        cid = attrs.get('id', '')
        label = attrs.get('label', '')
        placeholder = attrs.get('placeholder', '')
        row = ui.row().classes('items-center gap-2 w-full')
        icon_name = attrs.get('icon')
        with row:
            if icon_name:
                ui.icon(icon_name).classes('opacity-80')
            scope[cid] = ui.input(
                label=label or cid.replace('_', ' ').title(), placeholder=placeholder
            ).classes('w-full')
        return
    if kind == 'checkbox':
        cid = attrs.get('id', '')
        label = attrs.get('label', '')
        icon_name = attrs.get('icon')
        value = attrs.get('value', 'false').lower() == 'true'
        row = ui.row().classes('items-center gap-2')
        with row:
            if icon_name:
                ui.icon(icon_name).classes('opacity-80')
            scope[cid] = ui.checkbox(label or cid.replace('_', ' ').title(), value=value)
        return
    if kind == 'button':
        text = attrs.get('text', 'Submit')
        bid = attrs.get('id')
        b = ui.button(text)
        if bid:
            scope[bid] = b
        return


def render_markdown_with_shortcodes(md_text: str, scope: Dict[str, object]) -> None:
    """Render Markdown interleaved with NG components. 'scope' collects created widgets by id."""
    pos = 0
    MARKER_RE = re.compile(r'<!--NG:(?P<kind>[a-zA-Z0-9_-]+)\s*(?P<attrs>[^>]*)-->', re.S)
    for m in MARKER_RE.finditer(md_text):
        if m.start() > pos:
            ui.markdown(md_text[pos : m.start()])
        kind = m.group('kind')
        attrs = parse_attrs(m.group('attrs'))
        render_component(kind, attrs, scope)
        pos = m.end()
    if pos < len(md_text):
        ui.markdown(md_text[pos:])


def render_expansions(sections: List[Tuple[str, str]], scope: Dict[str, object]) -> None:
    for title_md, body_md in sections:
        e = ui.expansion(icon='chevron_right').props('dense').classes('rounded-xl shadow-sm')
        with e.add_slot('header'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('circle').classes('text-xs')
                ui.markdown(title_md.lstrip('# \t')).classes('!m-0 !p-0 text-sm font-base')
        with e:
            render_markdown_with_shortcodes(body_md, scope)


###
### load pages: 'ui/*.md'
###


def register_page(path: str) -> None:
    bname, _ = os.path.splitext(os.path.basename(path))
    url_path = (f'/{bname}').replace('//', '/')
    with open(path, 'r', encoding='utf-8') as f:
        md = f.read()
        template = jinja_env.from_string(md)  # expand macros into NG markers
        rendered = template.render()
        page_title, rest = split_h1(rendered)  # extract H1 as page title

        @ui.page(url_path, title=page_title)
        def page():
            enable_external_links_new_tab()
            with ui.header().classes('app-header'):
                ui.label("").classes('text-lg font-medium')
            with ui.column().classes('w-full max-w-screen-sm mx-auto px-3'):
                scope: Dict[str, object] = dict()
                preamble, sections = sections_after_h2(rest)
                if preamble:
                    render_markdown_with_shortcodes(preamble, scope)
                if sections:
                    render_expansions(sections, scope)
                ui.separator()


def register_pages():
    this_file_dir = os.path.dirname(os.path.abspath(__file__))
    ui_path = os.path.join(this_file_dir, 'ui')
    app.add_static_files('/ui', ui_path)
    ui.add_css(os.path.join(ui_path, 'theme.css'), shared=True)
    for file in os.listdir(ui_path):
        if file.endswith('.md'):
            md_path = os.path.join(ui_path, file)
            if os.path.isfile(md_path):
                register_page(md_path)
            else:
                logger.error(f"B95652 'ui/*.md' found that is not a file: {md_path}")
