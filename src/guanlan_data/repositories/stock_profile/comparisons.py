"""Explicitly calculated changes, kept separate from provider ratios."""
from decimal import Decimal, localcontext

def financial_comparisons(contexts, target):
    result=[]
    if not target:return result
    for metric in ('营业收入','归母净利润','总资产','归母净资产'):
        prior=str(int(target[:4])-1)+('-12-31' if metric in ('总资产','归母净资产') else target[4:])
        candidates=[c for c in contexts if c['dimension']=='company_metric' and c['label']==metric]
        a=next((c for c in reversed(candidates) if c['period_end']==target),None)
        b=next((c for c in reversed(candidates) if c['period_end']==prior),None)
        ao=a['observations'][0] if a else {};bo=b['observations'][0] if b else {}
        reason=None;value=None
        if ao.get('value') is None or bo.get('value') is None:reason='missing_comparison_value'
        elif not a.get('unit') or not a.get('currency') or (a['unit'],a['currency'],a['scope'])!=(b['unit'],b['currency'],b['scope']):reason='incomparable_basis'
        elif Decimal(bo['value'])==0:reason='zero_denominator'
        else:
            with localcontext() as c:
                c.prec=34;value=format(((Decimal(ao['value'])-Decimal(bo['value']))/abs(Decimal(bo['value']))*100).quantize(Decimal('0.000001')),'f')
        result.append(dict(metric=metric,period=target,prior_period=prior,current_value=ao.get('value'),prior_value=bo.get('value'),provider_yoy=(a or {}).get('basis',{}).get('provider_yoy'),provider_yoy_unit=(a or {}).get('basis',{}).get('provider_yoy_unit','source_unspecified'),calculated_change_pct=value,calculation_status=reason or 'calculated',formula='(current-prior)/abs(prior)*100',formula_version='same_scope_change_v1',input_observation_ids=[o['id'] for o in (ao,bo) if 'id' in o]))
    return result
