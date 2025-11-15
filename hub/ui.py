import ast
from nicegui import app, ui
import os
import re
from typing import Dict, List, Tuple
import logging
import hub.util as util

Berror = util.Berror
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
### elements that can go in ctags
###


def input(placeholder='', label='', icon=None, icon_position='left'):
    # docs: https://nicegui.io/documentation/input
    # icon names: https://quasar.dev/vue-components/icon/#ionicons
    obj = ui.input(label=label, placeholder=placeholder).classes('w-full')
    if icon:
        with obj.add_slot('prepend' if icon_position == 'left' else 'append'):
            ui.icon(icon).classes('opacity-80')
    return obj


def headline(text: str, align='center'):
    with ui.column().classes(f'w-full items-{align}'):  # left or center (¿right broken?)
        return ui.markdown(f'### {text}')


def image(source='', align='left', alt='', width=None):
    img = ui.image(source if '/' in source else f'ui/img/{source}').props(f'alt={alt}')
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
    return img


def button(text=''):
    return ui.button(text)


def checkbox(label='', value=False, icon=None):
    row = ui.row().classes('items-center gap-2')
    with row:
        if icon:
            ui.icon(icon).classes('opacity-80')
        return ui.checkbox(label, value=value)


elements_available = {
    'input': input,
    'headline': headline,
    'image': image,
    'button': button,
    'checkbox': checkbox,
}

###
### process ctags in Markdown files
###


def test_split_at_ctags():
    input = '{{ zero }}{{ one }}{ foo }{{bar}}{{ two }}'
    assert split_at_ctags(input) == ['{{ zero }}', '{{ one }}', '{ foo }{{bar}}', '{{ two }}']
    input = 'foo {{ three }} bar{{ four }} }}'  # unmatched close are ignored
    assert split_at_ctags(input) == ['foo ', '{{ three }}', ' bar', '{{ four }}', ' }}']
    try:
        input = '{{ thir{{ teen }}{{ fourteen }}'
        split_at_ctags(input)
    except:
        pass
    else:
        assert False, "test_split_at_ctags() did not catch nexted ctag"


def parse_kwargs(param_str: str):
    """Convert a string, e.g. 'length=2, prefix="fo"', into kwargs safely."""
    tree = ast.parse(f"f({param_str})", mode="exec")
    call = tree.body[0].value
    kwargs = dict()
    for kw in call.keywords:
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    return kwargs


def split_at_ctags(text: str) -> List[str]:
    """Split text at '{{ ... }}' ctags.

    Returns a list of strings where each piece either does not contain '{{ ', or
    it starts with '{{ ' and ends with ' }}'. Raises an exception on unmatched or nested ctags.
    """
    ctags: List[str] = list()
    i = 0
    len_text = len(text)
    while i < len_text:
        start = text.find("{{ ", i)
        if start == -1:  # no more ctags
            if i < len_text:
                ctags.append(text[i:])
            break
        if start > i:  # plain text before the ctag
            ctags.append(text[i:start])
        end = text.find(" }}", start)
        if end == -1:
            raise Berror(f"B04365 unmatched ctag: {text[start:start + 10]}...")
        nested = text.find("{{ ", start + 3, end)
        if nested != -1:
            raise Berror(f"B68301 nested '{{ ... }}' ctags are not allowed: {text[start:end+3]}")
        ctags.append(text[start : end + 3])
        i = end + 3
    return ctags


def render_markdown_with_ctags(md: str, scope: Dict[str, object], within=None):
    splits = split_at_ctags(md)
    for split in splits:
        if split.startswith('{{ '):
            # parse ctag, e.g. 'image(source="abc.png", width="50%")'
            m = re.fullmatch(r'\s*([^\d\W]\w*)\(([^\)]*)\)\s*', split[3:-3])
            if not m:
                raise Berror(f"B75468 syntax error in ctag: {split}")
            if m.group(1) not in elements_available:
                raise Berror(f"B50657 unknown element '{m.group(1)}' in: {split}")
            try:
                params = parse_kwargs(m.group(2))
            except (SyntaxError, ValueError, AttributeError, IndexError, TypeError):
                raise Berror(f"B76131 malformed parameters in: {m.group(0)}")
            id = params.get('id', None)
            if id:
                del params['id']  # don't pass to element
            try:
                if within:
                    with within:
                        obj = (elements_available[m.group(1)])(**params)
                else:
                    obj = (elements_available[m.group(1)])(**params)
            except TypeError as e:
                raise Berror(f"B63098 error in '{m.group(0)}': {e}")
            if id:
                scope[id] = obj
        else:
            if within:
                with within:
                    ui.markdown(split)
            else:
                ui.markdown(split)


def render_expansion(title_md: str, within=None):
    if within:
        with within:
            e = ui.expansion(icon='chevron_right').props('dense').classes('rounded-xl shadow-sm')
    else:
        e = ui.expansion(icon='chevron_right').props('dense').classes('rounded-xl shadow-sm')
    with e.add_slot('header'):
        with ui.row().classes('items-center gap-2'):
            ui.icon('circle').classes('text-xs')
            ui.markdown(title_md.lstrip('# \t')).classes('!m-0 !p-0 text-sm font-base')
    return e


###
### load pages: 'ui/*.md'
###


def parse_markdown_sections(md: str):
    lines = md.splitlines(keepends=True)
    root = None
    stack = list()
    current_section = None
    buffer = ''
    heading_re = re.compile(r'^(#+)\s+(.*)\s*$')
    for line in lines:
        m = heading_re.match(line)
        if m:
            hashes, title_md = m.groups()
            level = len(hashes)
            title = title_md.lstrip('# \t')
            if buffer and current_section is not None:
                current_section.append(buffer)
            buffer = ''
            if root is None:
                root = [title]
                stack = [(level, root)]
                current_section = root
                continue
            if title == '..':  # special end-of-section marker
                if len(stack) > 1:
                    stack.pop()
                current_section = stack[-1][1]
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = root if not stack else stack[-1][1]
            new_node = [title]
            parent.append(new_node)
            stack.append((level, new_node))
            current_section = new_node
        else:
            buffer += line
    if buffer and current_section is not None:
        current_section.append(buffer)
    return root


def test_parse_markdown_sections():
    input = (
        "## one\n\n"
        + "Text within 'one'.\n\n"
        + "### two\n\n"
        + "Text within 'two'.\n\n"
        + "## ..\n\n"
        + "Text within 'one' again.\n"
    )
    expected_output = [
        "one",
        "\nText within 'one'.\n\n",
        ["two", "\nText within 'two'.\n\n"],
        "\nText within 'one' again.\n",
    ]
    assert parse_markdown_sections(input) == expected_output


def register_page(path: str) -> None:
    bname, _ = os.path.splitext(os.path.basename(path))
    url_path = (f'/{bname}').replace('//', '/')
    with open(path, 'r', encoding='utf-8') as f:
        sections = parse_markdown_sections(f.read())

        @ui.page(url_path, title=sections[0])
        def page():
            scope: Dict[str, object] = dict()  # collects created widgets by id
            within = list()  # expansion object stack
            within.append(None)  # something for the outer-most level to build on
            enable_external_links_new_tab()
            with ui.header().classes('app-header'):
                ui.label("").classes('text-lg font-medium')
            with ui.column().classes('w-full max-w-screen-sm mx-auto px-3'):
                stack = list()  # the part of the tree that is left to traverse
                stack.append(sections[1:])  # push the list without title
                while len(stack):  # depth-first traversal of sections
                    secs = stack.pop()
                    for i, sec in enumerate(secs):
                        if isinstance(sec, str):
                            w = within[-1]  # current expansion object
                            render_markdown_with_ctags(sec, scope, w)
                        else:  # sec is a list representing a section header and sections
                            assert isinstance(sec[0], str)
                            w = within[-1]  # current expansion object
                            within.append(render_expansion(sec[0], w))
                            stack.append(secs[i + 1 :])  # remainder of current list
                            stack.append(sec[1:])  # sublist without title
                            break
                    else:  # done with secs at this level
                        within.pop()


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
