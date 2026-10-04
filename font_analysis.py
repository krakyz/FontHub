"""Read usable cmap data without modifying the archived font bytes."""
import struct
from fontTools.ttLib import TTLibError
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable


def recover_format4(data, glyph_order):
    """Keep only mappings whose offsets and glyph IDs are in bounds."""
    if len(data) < 16:
        raise TTLibError('Truncated cmap format 4 header')
    fmt, length, language, count2 = struct.unpack_from('>4H', data)
    if fmt != 4 or length > len(data) or count2 % 2:
        raise TTLibError('Invalid cmap format 4 header')
    count = count2 // 2
    if not count or 16 + count * 8 > length:
        raise TTLibError('Truncated cmap format 4 segment arrays')
    end = struct.unpack_from(f'>{count}H', data, 14)
    start_offset = 16 + count * 2
    start = struct.unpack_from(f'>{count}H', data, start_offset)
    delta = struct.unpack_from(f'>{count}H', data, start_offset + count * 2)
    range_offset = start_offset + count * 4
    offsets = struct.unpack_from(f'>{count}H', data, range_offset)
    mapping, omitted = {}, 0
    # A malformed file must not make overlapping segments perform unbounded work.
    work = 0
    for i in range(count):
        if start[i] > end[i]:
            omitted += 1
            continue
        work += end[i] - start[i] + 1
        if work > 262144:
            raise TTLibError('Excessive overlapping cmap segments')
        for code in range(start[i], min(end[i], 65534) + 1):
            if offsets[i]:
                address = range_offset + 2 * i + offsets[i] + 2 * (code - start[i])
                if offsets[i] % 2 or address < range_offset + count * 2 or address + 2 > length:
                    omitted += 1
                    continue
                glyph = struct.unpack_from('>H', data, address)[0]
                if glyph:
                    glyph = (glyph + delta[i]) & 65535
            else:
                glyph = (code + delta[i]) & 65535
            if glyph >= len(glyph_order):
                omitted += 1
            elif glyph and not 0xD800 <= code <= 0xDFFF:
                mapping[code] = glyph_order[glyph]
    return mapping, omitted


def usable_cmap(font):
    warnings = []
    try:
        order = font.getGlyphOrder()
    except TTLibError:
        if 'glyf' not in font:
            raise
        order = ['.notdef'] + [f'glyph{i:05}' for i in range(1, font['maxp'].numGlyphs)]
        font.setGlyphOrder(order)
        # getGlyphOrder may already have attempted a lazy cmap decompilation.
        if 'cmap' in font.tables:
            del font.tables['cmap']
    raw = font.reader['cmap']
    cmap = font['cmap']
    valid = []
    complete = True
    for table in cmap.tables:
        try:
            table.ensureDecompiled()
            valid.append(table)
        except (TTLibError, IndexError, struct.error) as exc:
            complete = False
            replacement = None
            if table.format == 4:
                _, count = struct.unpack_from('>HH', raw)
                for i in range(count):
                    platform, encoding, offset = struct.unpack_from('>HHI', raw, 4 + i * 8)
                    if (platform, encoding) == (table.platformID, table.platEncID):
                        try:
                            mapping, omitted = recover_format4(raw[offset:], order)
                            replacement = CmapSubtable.newSubtable(4)
                            replacement.platformID, replacement.platEncID = platform, encoding
                            replacement.language, replacement.cmap = table.language, mapping
                            warnings.append(f'cmap {platform}/{encoding}: использованы корректные соответствия; пропущено некорректных записей: {omitted}. Оригинал не изменён.')
                        except (TTLibError, struct.error) as recovery_error:
                            warnings.append(f'cmap {platform}/{encoding}: подтаблица исключена из предпросмотра: {recovery_error}')
                        break
            if replacement:
                valid.append(replacement)
            elif table.format != 4:
                warnings.append(f'cmap {table.platformID}/{table.platEncID}: подтаблица исключена из предпросмотра: {exc}')
    cmap.tables = valid
    best = cmap.getBestCmap() or {}
    return sorted(best), warnings, complete
