# Coochie Board — Streamlit Cloud build

Build: v26-streamlit-cloud

This build contains **no Windows drive paths, PowerShell launchers, or durable local SQLite assumptions**.

## Deploy
Upload these files to the GitHub repository used by Streamlit Community Cloud:

- `wnba_player_props_dashboard_fixed.py`
- `requirements.txt`
- `packages.txt`

Set the Streamlit app main file to `wnba_player_props_dashboard_fixed.py`.

## The Odds API key
You may paste it in the sidebar each session. For a persistent secret, add it in Streamlit Cloud app settings as:

```toml
THE_ODDS_API_KEY = "your_key_here"
```

The app also checks the `THE_ODDS_API_KEY` environment variable.

## Calibration / snapshots
Streamlit Community Cloud local files are ephemeral. Durable workflow:

1. Build the player board before games.
2. Download **Pregame snapshot CSV**.
3. Keep your snapshot/master-history CSVs outside the app.
4. Later upload snapshot/master-history CSVs in **Backtest & Calibration**.
5. Grade/calibrate.
6. Download the updated master-history CSV and calibration JSON.

The app may keep history/calibration in the current Streamlit session for convenience, but it does not treat that as permanent storage.
