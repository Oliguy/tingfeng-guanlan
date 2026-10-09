"""Recent close-at-limit labels; unknown days remain explicit evidence."""
def sequence(days):
    sealed=[i for i,d in enumerate(days) if d['status']=='confirmed_up']
    unknown=[d['date'] for d in days if d['status'] in ('unknown','conflict')]
    label=''
    if sealed and not unknown:
        n=len(days)-sealed[0] if days[-1]['status']=='confirmed_up' else len(days)
        label=f'{n}天{len(sealed)}板' if days[-1]['status']=='confirmed_up' else f'近{n}天{len(sealed)}板'
    return {'label':label,'known_boards':len(sealed),'unknown_dates':unknown,'days':days,'window':len(days)}
