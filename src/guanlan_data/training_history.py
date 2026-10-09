from guanlan_data.repositories.training.data import readonly, validate_bar

def next_date(data, current):
    with readonly(data.paths['raw']) as c:
        r=c.execute("SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>? ORDER BY cal_date LIMIT 1",(current,)).fetchone()
    return r[0] if r else None

def history(data, code, start, current, phase):
    if hasattr(data,'history'):return data.history(code,start,current,phase)
    background=data.background(code,start)
    with readonly(data.paths['raw']) as c:
        rows=[dict(r) for r in c.execute('SELECT d.trade_date,d.open,d.high,d.low,d.close,d.vol_lot,a.adj_factor AS factor FROM equity_daily_raw d LEFT JOIN equity_adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date WHERE d.ts_code=? AND d.trade_date>=? AND d.trade_date'+('<=?' if phase=='CLOSE' else '<?')+' ORDER BY d.trade_date',(code,start,current))]
    all_rows=background+rows
    for r in all_rows:validate_bar(r)
    return background,all_rows

