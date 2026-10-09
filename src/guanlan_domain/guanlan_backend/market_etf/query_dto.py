"""Compact, lossless table encoding at the versioned read interface."""
SCHEMA = 'etf_observer_query_result_v2'

def encode_table(rows):
    columns = list(dict.fromkeys((key for row in rows for key in row)))
    return {'columns': columns, 'rows': [[row.get(key) for key in columns] for row in rows]}

def compact_detail(payload):
    value = dict(payload)
    value['series_table'] = encode_table(value.pop('series'))
    return value

def decode_table(table):
    return [dict(zip(table['columns'], row)) for row in table['rows']]
