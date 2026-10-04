"""Parse Google's text-format metadata without discarding unknown fields.

All fields are lists because protobuf fields may repeat. Raw documents are also
stored, so future schema changes can be reparsed. Unsupported syntax raises an
error instead of publishing a silently incomplete catalogue.
"""
import ast
import re

TOKEN = re.compile(r'\s+|\#[^\n]*|//[^\n]*|"(?:\\.|[^"\\])*"|[A-Za-z_][\w.]*|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[:{}<>\[\],;]')


def parse(text):
    tokens = []
    position = 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match:
            raise ValueError(f'Unsupported metadata syntax at character {position}')
        token = match.group()
        if not token.isspace() and not token.startswith(('#', '//')):
            tokens.append(token)
        position = match.end()
    position = 0

    def scalar():
        nonlocal position
        token = tokens[position]
        position += 1
        if token.startswith('"'):
            value = ast.literal_eval(token)
            while position < len(tokens) and tokens[position].startswith('"'):
                value += ast.literal_eval(tokens[position])
                position += 1
            return value
        if token in {'true', 'false'}:
            return token == 'true'
        try:
            return float(token) if any(c in token for c in '.eE') else int(token)
        except ValueError:
            return token  # Enum value, retained even when not yet understood.

    def message(closing=None):
        nonlocal position
        result = {}
        while position < len(tokens):
            if tokens[position] == closing:
                position += 1
                return result
            key = tokens[position]
            if not re.fullmatch(r'[A-Za-z_][\w.]*', key):
                raise ValueError('Invalid metadata field: ' + key)
            position += 1
            if position < len(tokens) and tokens[position] == ':':
                position += 1
            if position >= len(tokens):
                raise ValueError('Missing metadata value')
            if tokens[position] in {'{', '<'}:
                end = '}' if tokens[position] == '{' else '>'
                position += 1
                value = message(end)
            elif tokens[position] == '[':
                position += 1
                values = []
                while position < len(tokens) and tokens[position] != ']':
                    values.append(scalar())
                    if position < len(tokens) and tokens[position] == ',':
                        position += 1
                if position >= len(tokens):
                    raise ValueError('Unclosed metadata list')
                position += 1
                result.setdefault(key, []).extend(values)
                continue
            else:
                value = scalar()
            result.setdefault(key, []).append(value)
            if position < len(tokens) and tokens[position] in {',', ';'}:
                position += 1
        if closing:
            raise ValueError('Unclosed metadata message')
        return result

    return message()
