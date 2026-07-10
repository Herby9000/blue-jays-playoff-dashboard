# Blue Jays Playoff Dashboard

A simple static GitHub Pages site that updates daily with:

- Toronto Blue Jays playoff probability
- Last five completed Blue Jays games
- Next scheduled Blue Jays game
- Five recent Blue Jays news headlines

## Data sources

- Playoff probability: TeamRankings Toronto Blue Jays projections page
- Results and schedule: MLB Stats API
- News: MLB.com Blue Jays RSS feed

## Updating

`python3 scripts/update_data.py` writes `data.json`.

GitHub Actions runs this once a day and commits changes when the data changes.
