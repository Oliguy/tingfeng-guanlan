"""Recover malformed text only against unique, independently disclosed same-field cells."""
import re
from guanlan_data.repositories.md_preprocess.tables import numeric; from guanlan_data.repositories.md_preprocess.tables import norm

def matching_disclosure(table,row,column):
    matrix=table['matrix'];raw=matrix[row][column];label=norm(matrix[row][0])
    if not label or not re.fullmatch(r'[\d,，.\sBIlOo()+−%-]+',raw):return None
    # Preserve all known digits, signs and scale; punctuation alone is not proof.
    fragment=re.sub(r'[,，.\s]','',norm(raw))
    pattern=''.join(r'\d' if ch in 'BIlOo' else re.escape(ch) for ch in fragment)
    precision=re.search(r'\.(\d+)%?[)）]?$',raw)
    candidates={};refs={}
    for r,values in enumerate(matrix):
        if r==row or norm(values[0])!=label:continue
        candidate=values[column]
        try:value=numeric(candidate)
        except ValueError:continue
        if value is None:continue
        digits=re.sub(r'[,，.\s]','',norm(candidate))
        if not re.fullmatch(pattern,digits):continue
        cp=re.search(r'\.(\d+)%?[)）]?$',candidate)
        if precision and (not cp or len(cp[1])!=len(precision[1])):continue
        candidates[value]=candidate;refs.setdefault(value,[]).append(f'{table["id"]}R{r+1}C{column+1}')
    if len(candidates)!=1:return None
    value=next(iter(candidates))
    return {'value':value,'source_ids':refs[value],'rule':'same_table_same_column_same_label_unique_disclosure','original_raw':raw,'disclosed_raw':candidates[value]}
