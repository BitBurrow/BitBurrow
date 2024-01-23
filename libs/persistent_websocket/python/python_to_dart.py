"""This module performs a surface-level "literal" translation from Python to Dart.

Usage: python3 python_to_dart.py [input_file.py] > [output_file.dart]

The purpose is to make it easier to keep code that needs to exist in both Python and Dart
in sync. After making changes, use python_to_dart to create a Dart-looking verion of the
Python file, and compare it side-by-side with the actual Dart file.

It is possible to include sections of Python-only code like this:
# the following 1 lines are only in the Python version of this file
from timeit import default_timer as timer

In the translated Dart file, this will become:
//// the following 1 lines are only in the Python version of this file
// from timeit import default_timer as timer

Likewise, Dart-only code blocks begin with a line like:
## the following 2 lines are only in the Dart version of this file
"""

import re
import sys


def camel_case(match):
    """Convert a name from snake_case to lowerCamelCase."""
    words = match.group(0).split('_')
    try:
        first_letter = next(i for i, s in enumerate(words) if s.strip())
    except StopIteration:
        return match.group(0)
    return (
        '_' * first_letter
        + words[first_letter]
        + ''.join(x.capitalize() or '_' for x in words[first_letter + 1 :])
    )


def p_to_d_names(match):
    """Convert reserved names from Python to Dart."""
    translate = {
        "True": "true",
        "False": "false",
        "None": "null",
        "or": "||",
        "and": "&&",
        "elif": "else if",
        "except": "catch",
    }
    return translate.get(match.group(0), match.group(0))  # do nothing if it's not in the list


def split_comment(line):
    """Splits line into (code, comment) and converts comment from Python to Dart.

    The returned comment includes preceding spaces. The returned values are always strings.
    This code does not properly handle a hash symbol within a quoted string.
    """
    # re group number:     (1__)(2_)(_____) (3_)   (code)(pre_spaces)(n/a)#(tail)
    match = re.fullmatch(r'(.*?)( *)(?<!\\)#(.*)', line)
    if match == None:  # line has no comment
        return line, ""
    pre_spaces = p if (p := match.group(2)) != "  " else " "  # Dart uses 1 space before comment
    extra_hashes = len(n.group(0)) if ((n := re.match(r'#+', match.group(3))) != None) else 0
    # replace every hash at the beginning of the comment with a slash
    return match.group(1), pre_spaces + "/" * (2 + extra_hashes) + match.group(3)[extra_hashes:]


def to_dart1(code):
    code = re.sub(r'[a-zA-Z_]+', camel_case, code)  # camelCase each Python name
    code = re.sub(r'(if|for|while) (.*):', r'\1 (\2)', code)  # add ( ) and remove :
    code = re.sub(r'(assert) (.*)', r'\1(\2)', code)  # add ( )
    code = re.sub(r'(\W)self\.', r'\1', code)  # remove `self.`
    code = re.sub(r'^self\.', r'', code)  # remove `self.`
    # return types
    code = re.sub(r'^def (.*) -> None:', r'\1', code)
    code = re.sub(r'^def (.*) -> bytes:', r'Uint8List \1', code)
    code = re.sub(r'^def (.*) -> int:', r'int \1', code)
    code = re.sub(r'^def (.*) -> str:', r'String \1', code)
    # imports
    code = re.sub(r'^import asyncio', r"import 'dart:async'", code)
    code = re.sub(r'^import collections', r"import 'dart:collection'", code)
    code = re.sub(r'^import hub.logs as logs', r"import 'package:logging/logging.dart'", code)
    code = re.sub(r'^import random', r"import 'dart:math' as math", code)
    code = re.sub(r'^import traceback', r"import 'package:mutex/mutex.dart'", code)
    code = re.sub(r'^import typing', r"import 'dart:typed_data'", code)
    code = re.sub(
        r'^import websockets', r"import 'package:web_socket_channel/io.dart' as wsio", code
    )
    # parallel names in the 2 languagews, e.g. True → true
    code = re.sub(r'[a-zA-Z_]+', p_to_d_names, code)
    return code


def to_dart2(code):
    code = re.sub(r'([^{}(])$', r'\1;', code)  # add ;
    code = re.sub(r': +{', ' {', code)  # remove colon before {
    return code


def commentout(match):
    method_line = match.group(1)  # e.g. "    async def _resend_one(self) -> None:"
    method = method_line.strip('\n') if method_line != None else ""
    indent = " " * (len(method) - len(method.lstrip()))
    in_tripple_quotes = match.group(3) if match.group(3) != None else match.group(4)
    comment_lines = in_tripple_quotes.strip().split('\n')
    new_comment = '\n'.join((indent + "## " + line.lstrip()).rstrip() for line in comment_lines)
    return new_comment + '\n' + method


def process_python_file(file_path):
    new_indent = '  '
    with open(file_path, 'r') as file:
        content = file.read()
    # move tripple-quote docs after the method def line to comments before the def line
    content = re.sub(
        r'^( *(def |async def |class )[^\n]+)\n *"""(.*?)"""|^ *"""(.*?)"""',
        commentout,
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    indent_stack = list()
    indent_stack.append(0)
    codeless_lines = list()  # queue comment-only lines until after closing braces
    commentout_count = 0
    output = list()  # (code, comment) for each line
    for line in content.split('\n'):
        if commentout_count:
            line = "# " + line
            commentout_count -= 1
        indents = (len(line) - len(line.lstrip())) // 4  # assumes consistent 4-space indents
        code, comment = split_comment(line.strip())  # strip indent, trailing spaces, newline
        if code:  # ignore empty lines
            while indent_stack and indent_stack[-1] > indents:  # add }
                indent_stack.pop()
                output.append((new_indent * (indent_stack[-1]) + '}', ""))
            if indent_stack and indent_stack[-1] < indents:  # add {
                a, b = output[-1]
                output[-1] = (a + ' {', b)
                indent_stack.append(indents)
            for codeless in codeless_lines:  # restore queued empty lines
                output.append(codeless)
            codeless_lines = list()
            output.append((new_indent * indents + to_dart1(code), comment))
        else:  # no code (comment only)
            if (
                match := re.match(
                    r'^// the following ([0-9]+) lines are only in the Python version of this file$',
                    comment,
                )
            ) != None:
                commentout_count = int(match.group(1))
                comment = "//" + comment
            codeless_lines.append(("", new_indent * indents + comment))  # queue until after braces
    while indent_stack:  # add pending }
        indent_stack.pop()
        if indent_stack:
            output.append((new_indent * indent_stack[-1] + '}', ""))
    uncomment_count = 0
    last_line_was_closing_brace = ""
    for code, comment in output:
        # uncomment code blocks that begin with a line: ## the following ...
        if uncomment_count:
            if comment.startswith('// '):
                comment = comment[3:]
            elif comment.startswith('//'):
                comment = comment[2:]
            uncomment_count -= 1
        if (
            match := re.match(
                r'^/// the following ([0-9]+) lines are only in the Dart version of this file$',
                comment,
            )
        ) != None:
            uncomment_count = int(match.group(1))
            comment = comment[1:]
        line = to_dart2(code) + comment
        # fix `}` separated from `else {`
        if last_line_was_closing_brace:
            # with_brace = re.sub(r'[a-zA-Z_]+', lambda m: "} " + m.group(0) if m.group(0) in ["else", "elif", "except", "finally"] else m.group(0), line)
            line, n = re.subn(r'\b(?:else|elif|catch|finally)\b', r'} \g<0>', line, count=1)
            if n == 0:  # no replacement made by re.subn()
                print(last_line_was_closing_brace)
            last_line_was_closing_brace = ""
        if code.strip() == "}":
            last_line_was_closing_brace = line
        else:
            print(line)


if len(sys.argv) != 2:
    print(f"usage: {sys.argv[0]} [input_file]")
else:
    process_python_file(sys.argv[1])
