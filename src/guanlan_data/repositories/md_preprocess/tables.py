"""Table geometry and conservative financial column interpretation (stdlib only)."""
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

SCALES = {'元': 1, '千元': 1000, '万元': 10000, '百万元': 1000000, '亿元': 100000000}
METRICS = ('营业收入', '营业成本', '收入', '成本', '营业利润', '利润总额', '净利润', '毛利率', '毛利',
           '报告分部税前利润', '净利息收入', '利息收入', '利息支出', '净手续费及佣金收入',
           '手续费及佣金收入', '手续费及佣金支出', '其他净收入', '归母净利润', '营业支出', '营业利润率', '税后利润',
           '营业外收入', '营业外支出')

def norm(s):
    return re.sub(r'\s+', '', str(s or '')).replace('（', '(').replace('）', ')').replace('−', '-')

def numeric(raw):
    # MinerU may wrap one number in TeX layout syntax. Strip only complete,
    # balanced formatting; extra prefixes, operators and broken digits fail.
    lexical=str(raw or '').strip()
    match=re.fullmatch(r'\\begin\{array\}\{[lcr]+\}\s*([\d\s{},.%-]+)\s*\\end\{array\}',lexical)
    if match:lexical=match[1]
    if '{' in lexical and lexical.count('{')==lexical.count('}') and re.fullmatch(r'[\d\s{},.%-]+',lexical):lexical=lexical.replace('{','').replace('}','')
    s = norm(lexical).replace('，', ',')
    if s in ('', '-', '—', '–', '－', '不适用') or re.fullmatch(r'[-—–－]{2,}',s):
        return None
    pct = s.endswith('%')
    if pct:
        s = s[:-1]
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    if not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', s):
        raise ValueError('ambiguous numeric cell')
    return str(Decimal(s.replace(',', '')))

def is_number(s):
    try:
        return numeric(s) is not None
    except ValueError:
        return False

class HTMLTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth > 1:
                raise ValueError('nested HTML table requires review')
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            a = dict(attrs)
            self.cell = {'parts': [], 'rowspan': int(a.get('rowspan', 1)), 'colspan': int(a.get('colspan', 1))}
            if not 1 <= self.cell['rowspan'] <= 1000 or not 1 <= self.cell['colspan'] <= 1000:
                raise ValueError('invalid table span')
        if tag == 'br' and self.cell is not None:
            self.cell['parts'].append('\n')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['parts'].append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is None:
                raise ValueError('cell without row')
            self.cell['text'] = ''.join(self.cell.pop('parts')).strip()
            self.row.append(self.cell)
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == 'table':
            self.depth -= 1

def parse_table(raw):
    if raw.lstrip().lower().startswith('<table'):
        p = HTMLTable()
        p.feed(raw)
        p.close()
        if p.depth or p.cell is not None or p.row is not None:
            raise ValueError('unclosed table')
        rows = p.rows
    else:
        rows = []
        for line in raw.splitlines():
            cells = re.split(r'(?<!\\)\|', line.strip().strip('|'))
            if all(re.fullmatch(r'\s*:?-{3,}:?\s*', c) for c in cells):
                continue
            rows.append([{'text': c.strip().replace('\\|', '|'), 'rowspan': 1, 'colspan': 1} for c in cells])
    grid, origins = {}, {}
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            for dr in range(cell['rowspan']):
                for dc in range(cell['colspan']):
                    pos = r + dr, c + dc
                    if pos in grid:
                        raise ValueError('overlapping table spans')
                    grid[pos] = cell['text']
                    origins[pos] = [r, c]
            c += cell['colspan']
    if not grid:
        raise ValueError('empty table structure')
    height = max(r for r, c in grid) + 1
    width = max(c for r, c in grid) + 1
    return {'matrix': [[grid.get((r, c), '') for c in range(width)] for r in range(height)],
            'origins': [[origins.get((r, c)) for c in range(width)] for r in range(height)], 'raw_rows': rows}

def header_count(matrix):
    # Year and ratio headers are not data rows. Empty data templates remain data.
    count = 0
    for index, row in enumerate(matrix[:8]):
        n = [norm(x) for x in row]
        if count and not n[0] and n[1:] and all(not x or re.search(r'有限公司$|股份公司$|合伙企业[)）]?$',x) for x in n[1:]):
            count+=1;continue
        if any(is_number(x) for x in row[1:]):
            break
        next_header = index+1 < len(matrix) and any(re.search(r'本期|上期|同期|20\d{2}年|个月期间|营业收入|主营业务收入|毛利率',norm(c)) for c in matrix[index+1][1:]) and not any(is_number(c) for c in matrix[index+1][1:])
        if next_header or any(re.search(r'本期|同期|上期|报告期|发生额|20\d{2}年|收入|成本|金额|毛利率|项目|期间|占比|比重|附注|合同分类|本集团|本行|公司名称|公司类型', x) for x in n):
            if count and n[0] and not re.search(r'项目|单位|期间|20\d{2}|人民币', n[0]):
                if not any(re.search(r'本期|上期|同期|20\d{2}年|金额|成本|毛利率', x) for x in n[1:]):
                    break
            count += 1
        else:
            break
    return count

def metadata_values(text):
    n = norm(text)
    n=re.sub(r'\((人民币|美元|港元|港币)\)',r'\1',n)
    unit_hits = list(re.finditer(r'(?:单位[:：]?|人民币|美元|港元|港币|\()(百万元|千元|万元|亿元|元)(?!/|每)', n))
    units={m[1] for m in unit_hits}
    currencies = {v for k, v in [('人民币', 'CNY'), ('美元', 'USD'), ('港元', 'HKD'), ('港币', 'HKD')] if k in n}
    return {'unit':units,'currency':currencies}

def metadata(text):
    values=metadata_values(text)
    return tuple(next(iter(values[k])) if len(values[k])==1 else None for k in ('unit','currency'))

def column_type(header):
    """Only explicit textual headings exclude a column; malformed amounts still fail."""
    h = norm(header)
    if re.search(r'收入来源地|来源地区|销售量|销量|产量|回款情况',h):return 'non_target_metric'
    if re.search(r'[。；]|[,，].*(?:增加|减少|计提|重分类|影响)|(?:主要系|由于)',h):return 'text'
    if re.search(r'是否|原因|说明|备注|具体情况|主要业务|公司名称|企业名称|取得方式|业务性质|计入.*项目', h):
        return 'text'
    if re.search(r'占|比重|比例|增减|增长|变动|同比|附注|变化幅度|平均余额|收益率|付息率|目标值|触发值', h):
        return 'non_target_metric'
    return 'financial_candidate'

def period_label(text, period):
    n = norm(text)
    # Joint venture headers combine stock and flow labels; use only the explicit
    # flow component for revenue/profit, never translate a bare balance date.
    if re.search(r'期末余额(?:[/／]|或|及)本期发生额|本期发生额(?:[/／]|或|及)期末余额',n):return period
    if re.search(r'期初余额(?:[/／]|或|及)上期发生额|上期发生额(?:[/／]|或|及)期初余额',n):return str(int(period[:4])-1)+period[4:]
    half=re.search(r'(20\d{2})年(?:上半年|1[-—至]6月)',n)
    if half and not re.search(r'增减|增长|同比|变动',n):return half[1]+'0630'
    if re.search(r'增减|增长|变动|同比', n):
        return 'growth'
    if '期末' in n or '期初' in n or (re.search(r'\d{1,2}月\d{1,2}日', n) and not re.search(r'期间|个月|半年', n)):
        return None  # stock / flow not interchangeable
    y = re.search(r'(20\d{2})年', n)
    if y:
        months = re.search(r'年(\d{1,2})[-—–－~～至](\d{1,2})月', n)
        if months:
            if months[1]=='1' and months[2] in ('6','12'):return y[1]+('0630' if months[2]=='6' else '1231')
            return None
        if '年度' in n and '半年度' not in n:return y[1]+'1231'
        return y[1] + period[4:]
    if re.search(r'上年同期|上期|去年同期', n):
        return str(int(period[:4]) - 1) + period[4:]
    if re.search(r'本期|本报告期|报告期', n):
        return period
    return None

def metric_label(text):
    n = norm(text)
    # Asset valuation cost is a stock measure, not business operating cost.
    if re.search(r'初始成本|初始投资成本|摊余成本|重置成本|成本法|资本成本|融资成本|资金成本|合并成本|收入确认|未实现利润|未分配利润|递延收益',n):return None
    if re.search(r'每股|收益率|占|比重|比例|幅度', n):
        return None
    if '净利润' in n and re.search(r'归属于|股东的', n):
        return '扣非归母净利润' if '扣除' in n else '归母净利润'
    # These explicitly named components belong to operating revenue/cost.
    # Keep the original row/header in evidence; never promote a bare 收入/成本.
    operating_components = set(re.findall(r'(?:主营业务|其他业务)(收入|成本)', n))
    if len(operating_components) > 1 or (operating_components and '收入' in n and '成本' in n):
        return None  # One unsplit revenue-and-cost heading has no unique metric.
    if operating_components:
        return '营业' + next(iter(operating_components))
    for metric in sorted(METRICS, key=len, reverse=True):
        if metric in n:
            return metric
    return None

def dimension(text):
    n = norm(text)
    if re.search(r'地区|地域|内销|外销|境内|境外', n): return 'region'
    if re.search(r'分产品|产品类型|业务类型', n): return 'product'
    if re.search(r'分行业|分客户所处行业',n): return 'industry'
    if '分销售模式' in n:return 'sales_channel'
    if re.search(r'经营分部|报告分部|分部业绩|业务分部', n): return 'segment'
    if re.search(r'时间|时点|时段', n): return 'transfer_timing'
    return 'unresolved'

def extract_financial(table, period):
    """Return observations, specific issues, header and row dispositions. Never sum dimensions."""
    m = table['matrix']; hc = header_count(m)
    header_matrix=table.get('inherited_header_matrix',m[:hc])
    if 'inherited_headers' in table:hc=table.get('inherited_header_rows',0)
    breakdown_context=bool(re.search(r'收入构成|收入分解|主营业务收入|营业收入.*分解',norm(table['context']+' '+table.get('period_context',''))))
    financial_candidate = any(metric_label(c) for row in header_matrix+m[:3] for c in row if len(c)<60 and column_type(c)=='financial_candidate') or any(metric_label(row[0]) for row in m if len(row[0])<60) or (breakdown_context and any('金额' in c for row in header_matrix for c in row))
    if not any(is_number(c) for row in m for c in row[1:]) and not any(metric_label(row[0]) for row in m) and not any(metric_label(c) for row in header_matrix for c in row) and not (breakdown_context and any('金额' in c for row in header_matrix for c in row)):financial_candidate=False
    if re.search(r'业绩考核|股权激励|激励计划',table['context']):
        financial_candidate=False;table['target_reason']='forecast_or_incentive'
    table['financial_candidate'] = financial_candidate
    if not financial_candidate:
        table['header_rows']=hc if table.get('inherited_headers') else hc or 1
        table['headers']=table.get('inherited_headers') or [' / '.join(dict.fromkeys(row[c] for row in m[:table['header_rows']] if row[c])) for c in range(len(m[0]))]
        return [], [], [{'id':f"{table['id']}R{r+1}",'label':row[0],'disposition':'non_financial_table_row','dimension':'unresolved'} for r,row in enumerate(m[table['header_rows']:],table['header_rows'])]
    table['header_rows'] = hc
    table['headers'] = table.get('inherited_headers') or [' / '.join(dict.fromkeys(row[c] for row in m[:hc] if row[c])) for c in range(len(m[0]))]
    if not hc and not table.get('inherited_headers'):
        if all(not c.strip() for row in m for c in row[1:]):
            return [], [], [{'id':f"{table['id']}R{r+1}",'label':row[0],'disposition':'empty_template','dimension':'unresolved'} for r,row in enumerate(m)]
        return [], [{'type': 'header_unresolved', 'source_id': table['id']}], []
    headers = table['headers']; rows, issues, coverage = [], [], []
    table['column_roles'] = [column_type(h) for h in headers]
    table['cell_dispositions'] = []
    context = norm(table['context'])
    # Customer geography in nearby prose does not make the entire table regional.
    dim = ('entity' if re.search(r'重要.*(?:合营|联营)企业.*财务|主要控股参股公司|主要子公司',context) else
           'region' if re.search(r'地区分部|地域分部|按地区|分地区', context) else
           'segment' if re.search(r'经营分部|报告分部|分部业绩|分部信息|分部报告', context) else
           'product' if re.search(r'按业务类型|按产品类型|按产品类别|分产品', context) else 'unresolved')
    if table.get('inherited_dimension','unresolved')!='unresolved':dim=table['inherited_dimension']
    unit, currency = table['unit'], table['currency']
    is_breakdown = breakdown_context
    # Multicolumn segments identify group labels from the first header row.
    group_row=header_matrix[0] if header_matrix else m[0]
    group_headers = bool((any('业务' in x or '分部' in x or x in ('本集团','本行') for x in group_row[1:]) and not any(metric_label(x) for x in group_row[1:])) or (dim=='segment' and any(metric_label(row[0]) for row in m[hc:]) and not any(metric_label(h) for h in table['headers'][1:])))
    if dim=='entity' and len(header_matrix)>1:group_headers=True
    if group_headers and dim in ('segment','entity'):
        # Bank tables often put the period above the segment names, not vice versa.
        group_row=[next((row[c] for row in reversed(header_matrix) if row[c] and period_label(row[c],period) is None and not re.search(r'截至|\d+个月|\d+月\d+日|期间|单位',norm(row[c])) and not metric_label(row[c])), group_row[c]) for c in range(len(group_row))]
    current_performance_table = (bool(re.search(r'主营业务分析|主营业务经营情况|主要经营情况|营业情况分析|收入构成',context))
        and any(metric_label(h) in ('营业收入','收入') for h in headers)
        and all(any(metric_label(h)==s for h in headers) for s in ('营业成本','毛利率'))
        and any('比上年同期增减' in norm(h) for h in headers)
        and not re.search(r'预计|预测|预算|前景|未来',context)
        and not any(re.search(r'20\d{2}年|季度|期末|期初',h) for h in headers))
    current_nonoperating_table = (bool(re.search(r'非主营业务.*(?:分析|说明)',context)) and any(norm(h)=='金额' for h in headers)
        and any('占利润总额比例' in norm(h) for h in headers) and not re.search(r'预计|预测|预算|未来',context))
    active_row_period=None;active_row_period_source=None
    active_section_metric=None;active_section_source=None
    for r in range(hc, len(m)):
        row = m[r]; label = row[0].strip(); rid = f"{table['id']}R{r+1}"
        distinct = list(dict.fromkeys(x for x in row if x))
        if len(distinct)==1 and (len(distinct[0])>100 or (len(distinct[0])>20 and re.search(r'[。；]',distinct[0]))) and not is_number(distinct[0]):
            coverage.append({'id':rid,'label':label,'disposition':'explanatory_merged_row','dimension':dim});continue
        if len(distinct)==1 and re.fullmatch(r'20\d{2}年(?:1[-—至]6月|半年度|度|1[-—至]12月)',norm(label)):
            active_row_period=period_label(label,period);active_row_period_source=rid
            coverage.append({'id':rid,'label':label,'disposition':'period_header','period':active_row_period});continue
        newdim = dimension(label)
        if len(distinct) == 1 and newdim != 'unresolved':
            dim = newdim
            coverage.append({'id': rid, 'label': label, 'disposition': 'dimension_header', 'dimension': dim})
            continue
        if not label:
            coverage.append({'id': rid, 'label': '', 'disposition': 'unresolved_blank_label'})
            if any(is_number(x) for x in row): issues.append({'type': 'row_label_unresolved', 'source_id': rid})
            continue
        rowmetric = metric_label(label)
        # A year-column table can name its metric in an explicit section row:
        # 主营业务收入 -> product rows -> 其他业务收入 -> product rows.
        # Only literal empty-valued accounting section labels establish this;
        # an unrelated/unknown section clears the inherited metric.
        section_label = re.sub(r'^(?:[一二三四五六七八九十\d]+[、.]|[（(][一二三四五六七八九十\d]+[）)])', '', norm(label))
        empty_section = all(not str(value).strip() for value in row[1:])
        if empty_section and re.fullmatch(r'(?:主营业务|其他业务|营业)(?:收入|成本)', section_label):
            active_section_metric=rowmetric;active_section_source=rid
            coverage.append({'id':rid,'label':label,'disposition':'explicit_metric_section','metric':rowmetric,'dimension':dim})
            continue
        if empty_section or rowmetric is not None:
            active_section_metric=None;active_section_source=None
        selected = []
        for c in range(1, len(row)):
            h = headers[c]; hn = norm(h)
            if table['column_roles'][c] != 'financial_candidate':
                table['cell_dispositions'].append({'source_id':rid,'column':c+1,'disposition':table['column_roles'][c],'raw':row[c]})
                continue
            met = metric_label(h) or rowmetric or active_section_metric
            if met is None and ('金额' in hn) and is_breakdown: met = '营业收入'
            if met is None: continue
            value_period = period_label(h, period)
            period_basis='column_header'
            if active_row_period:
                value_period=active_row_period;period_basis='row_period_header'
            # Income/cost columns without year only inherit an explicit local period caption.
            if value_period is None and not re.search(r'期末|期初|月\d+日|20\d{2}年', hn):
                # Restatement and segment-renaming notes discuss comparative data;
                # they do not date the adjacent current-performance table.
                period_context='\n'.join(line for line in table.get('period_context','').splitlines() if not re.search(r'重述|口径|拆分|变更|更名|分别列示',line))
                value_period = period_label(period_context, period) or period_label(headers[0],period)
                period_basis='local_context'
                if current_performance_table or current_nonoperating_table:
                    caption=table.get('caption_period')
                    value_period=caption or period;period_basis='explicit_table_caption' if caption else 'report_current_performance_rule'
            # A bare annual column beside an explicitly half-year column is ambiguous.
            yr=re.search(r'(20\d{2})年',hn)
            if yr and not re.search(r'个月|期间|年\d|年度',hn) and any(yr[1]+'年1-6月' in norm(x) for x in headers):value_period=None
            if value_period in (None,'growth') and table.get('reconciled_period') and not re.search(r'期末|期初|增长|同比|增减',hn):
                value_period=table['reconciled_period'];period_basis=table['reconciliation']['basis']
            if value_period in (None, 'growth'): continue
            selected.append(c)
            ev = {'source_id': table['id'], 'row': r+1, 'column': c+1,
                  'origin': table['origins'][r][c], 'raw': row[c], 'header': h,
                  'header_rows': hc, 'line_start': table['line_start'], 'line_end': table['line_end']}
            ev['period_basis']=period_basis
            if active_section_metric and metric_label(h) is None and rowmetric is None:
                ev['metric_basis']='explicit_same_table_section'
                ev['metric_context_source_ids']=[active_section_source]
            if period_basis in ('same_note_exact_revenue_and_cost_totals','report_exact_revenue_and_profit_totals'):ev['period_context_source_ids']=table['reconciliation']['source_ids']
            if period_basis=='row_period_header':ev['period_context_source_ids']=[active_row_period_source]
            ev['metadata_basis']=table.get('metadata_basis',{})
            if period_basis=='report_current_performance_rule':
                ev['period_context_source_ids']=table.get('metadata_context_source_ids',[])
            try: val = numeric(row[c])
            except ValueError:
                from guanlan_data.repositories.md_preprocess.cell_recovery import matching_disclosure
                repaired=matching_disclosure(table,r,c)
                if repaired is None:
                    issues.append({'type': 'numeric_cell_unresolved', 'source_id': rid, 'column': c+1, 'raw': row[c]});continue
                val=repaired['value'];ev['numeric_repair']=repaired
            ru, rc = metadata(label)
            hu, hc_currency = metadata(h)
            u = ru or hu or unit; cur = rc or hc_currency or currency
            if ru or rc or hu or hc_currency:
                ev['cell_metadata_override']={'unit_basis':'row_label' if ru else 'column_header' if hu else 'table_context',
                    'currency_basis':'row_label' if rc else 'column_header' if hc_currency else 'table_context'}
            if met == '毛利率' or '%' in row[c]: u = '%'
            if met=='营业利润率':u='%'
            if u=='%':cur=None
            group = group_row[c] if group_headers else None
            hierarchy = ('internal_elimination' if re.search(r'内部|抵销|抵消', label + (group or '')) else
                         'total' if re.search(r'合计|小计|总计', label + (group or '')) else
                         'contained' if label.startswith('其中') else 'unresolved')
            status = 'parsed' if u and (cur or u == '%') else 'metadata_unresolved'
            if status != 'parsed' and val is not None:
                issues.append({'type': status, 'source_id': rid, 'column': c+1, 'missing': [k for k,v in [('unit',u),('currency',cur)] if not v and not (k=='currency' and u=='%')]})
            if val is None:
                table['cell_dispositions'].append({'source_id':rid,'column':c+1,'disposition':'disclosed_missing_value','raw':row[c], 'missing_metadata':[k for k,v in [('unit',u),('currency',cur)] if not v and not (k=='currency' and u=='%')]})
            row_scope = 'consolidated' if group=='本集团' else 'parent' if group=='本行' else table['scope']
            row_dim = 'segment' if group_headers and dim not in ('region','entity') else dim
            if row_dim=='unresolved' and rowmetric and metric_label(h) is None:row_dim='company_metric'
            if table.get('header_source_id'):ev['header_source_id']=table['header_source_id']
            rows.append({'id': f'{rid}C{c+1}', 'table_id': table['id'], 'row_id': rid,
                         'label': label, 'group': group, 'metric': met, 'period': value_period,'period_basis':period_basis,
                         'dimension': row_dim,
                         'scope': row_scope, 'currency': cur, 'unit': u, 'value': val,
                         'value_base': str(Decimal(val)*SCALES[u]) if val is not None and u in SCALES else None,
                         'precision': len(val.split('.')[1]) if val and '.' in val else 0 if val is not None else None,
                         'status': status, 'hierarchy': hierarchy, 'do_not_sum': True, 'evidence': ev})
        if selected:
            state = 'empty_template' if all(not row[c].strip() for c in selected) else 'financial_observations'
        elif re.search(r'资产|负债|资本|现金流|未分配利润|未实现利润|融资成本|资金成本|费用|折旧|摊销|减值损失|长期股权投资|投资收益|其他收益|公允价值变动|税金及附加|应收款|应付款',norm(label)) and not rowmetric:
            state='non_target_stock_or_other_metric'
        elif all(table['column_roles'][c]!='financial_candidate' for c in range(1,len(row))):
            state='non_target_columns'
        elif any(is_number(x) for x in row[1:]) and (rowmetric or dim in ('product', 'industry', 'segment')):
            state = 'unresolved_financial_row'
            issues.append({'type': 'column_period_or_metric_unresolved', 'source_id': rid, 'label': label})
        else:
            state = 'non_target_row'
        coverage.append({'id': rid, 'label': label, 'disposition': state, 'dimension': dim})
    return rows, issues, coverage
