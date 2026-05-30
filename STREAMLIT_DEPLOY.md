# Deploying to Streamlit Community Cloud

1. Commit and push this repository to GitHub.

2. Go to https://share.streamlit.io and sign in with GitHub.

3. Click **New app** → select the repository and branch. Set the **Main file** to:

   bus_scheduler/app.py

4. Streamlit Community Cloud will read `requirements.txt` from the repository root and install dependencies automatically.

5. Click **Deploy**. The site launches within a minute.

Notes:
- This project is a single-process Streamlit app. The UI, scenario loader, and scheduling engine all live in `bus_scheduler/`.
- Set the Main file explicitly to `bus_scheduler/app.py` if Streamlit does not detect it automatically.
- Do not hardcode a port in `.streamlit/config.toml`; Streamlit Cloud assigns the runtime port automatically.
- To update the live app, push a new commit to the branch and Streamlit will redeploy.
